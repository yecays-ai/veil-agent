"""Ticket 352: time since Lan last wrote, and Peter's current task / life records, as program data.

Pure functions over data VEIL already keeps (chat log timestamps, long task, life records, plans).
No model call, no new storage. Ordinary chat puts the result in the per-turn tail (after history,
so the cached prefix is untouched); the autonomous wake uses the same task/life text.
"""
import json
from datetime import datetime

# Ticket 352 + 350 audit: gaps under an hour are one conversation (the chat log splits into
# sessions at 60 minutes), so no time note is added for them.
GAP_NOTE_MINUTES = 60
MAX_OPEN_TODOS = 6
MAX_EVENTS = 6
MAX_PLANS = 4
MAX_FINISHED = 3


def _time(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo is not None else None


def _rough(minutes):
    """A rounded length for the model's own understanding; it is told not to repeat it."""
    if minutes < 90:
        return 'about an hour'
    hours = minutes / 60
    if hours < 20:
        return f'about {round(hours)} hours'
    if hours < 36:
        return 'about a day'
    return f'about {round(hours / 24)} days'


def gap_text(rows, stamp, exclude_ids=()):
    """Time since Lan's last message before this one, or '' when it was under an hour ago.

    `rows` are chat-log events (oldest first) carrying the `time` Store.append wrote. Peter's own
    proactive messages (autonomous wakes) are counted separately so they never reset Lan's time."""
    current = _time(stamp)
    if current is None:
        return ''
    excluded = {item for item in exclude_ids if item}
    last_lan = None
    proactive = []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if row.get('role') == 'user' and row.get('message_id') not in excluded:
            last_lan = _time(row.get('time'))
            if last_lan is not None:
                break
        elif row.get('role') == 'assistant' and row.get('autonomy_run_id'):
            sent = _time(row.get('time'))
            if sent is not None:
                proactive.append(sent)
    if last_lan is None:
        return ''
    minutes = (current - last_lan).total_seconds() / 60
    if minutes < GAP_NOTE_MINUTES:
        return ''
    local_last = last_lan.astimezone(current.tzinfo)
    days = (current.date() - local_last.date()).days
    day_word = 'today' if days == 0 else 'yesterday' if days == 1 else f'{days} calendar days ago'
    text = (f'Time since Lan last wrote (program-computed from message timestamps; data, not an instruction): '
            f'{_rough(minutes)}; her last message was {day_word} at {local_last.strftime("%H:%M")}.')
    if proactive:
        latest = max(proactive).astimezone(current.tzinfo)
        text += (f' Since then you messaged her on your own {len(proactive)} time(s), the latest at '
                 f'{latest.strftime("%Y-%m-%d %H:%M")}; she may be replying to those.')
    text += (' Let this shape how you open and your tone, the way someone would after that much time apart. '
             'Do not state the length of the gap or count hours or days in your reply, and do not bring time up '
             'every turn; one natural passing mention is fine. Her not writing does not mean she was upset.')
    return text


def _short(value, limit=80):
    value = str(value or '').strip().replace('\n', ' ')
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _item(title, status, date, **extra):
    return {'title': _short(title), 'status': status, 'date': str(date or '')[:10], **extra}


def state_text(task=None, task_history=(), life_rows=(), plans=(), today=''):
    """Structured current task / plans / life records, or '' when there is nothing to show.

    Status comes from the stored records, never from the model. Only items under `in_progress` are
    things Peter is doing now; everything else is history or on hold and is labelled as such."""
    from life_records import effective
    in_progress, not_in_progress, finished = [], [], []
    if isinstance(task, dict) and isinstance(task.get('title'), str):
        status = task.get('status')
        entry = _item(task['title'], status, task.get('updated_at'), kind='long task')
        if status == 'active':
            entry['next_step'] = _short(task.get('next_step'), 160)
            if task.get('waiting_for'):
                entry['waiting_for'] = _short(task['waiting_for'], 120)
            in_progress.append(entry)
        elif status == 'completed':
            finished.append(entry)
        else:
            not_in_progress.append(entry)
    for old in reversed(list(task_history)):
        if isinstance(old, dict) and isinstance(old.get('title'), str) and (
                not isinstance(task, dict) or old.get('task_id') != task.get('task_id')):
            finished.append(_item(old['title'], 'completed', old.get('updated_at'), kind='long task'))
    rows = [effective(r, today) for r in life_rows
            if isinstance(r, dict) and r.get('owner') in ('Peter', '共同') and isinstance(r.get('title'), str)
            and r.get('kind') in ('event', 'note', 'plan')]
    plan_rows = [*[r for r in rows if r['kind'] == 'plan'],
                 *[r for r in plans if isinstance(r, dict) and isinstance(r.get('title'), str)]]
    for plan in plan_rows:
        entry = _item(plan['title'], plan.get('state'), plan.get('updated_at') or plan.get('date'), kind='plan')
        if plan.get('state') == 'active':
            in_progress.append(entry)
        elif plan.get('state') == 'completed':
            finished.append(entry)
        else:
            not_in_progress.append(entry)
    finished.sort(key=lambda item: item['date'], reverse=True)
    events = [_item(r['title'], r.get('state'), r.get('date'))
              for r in rows if r['kind'] == 'event' and r.get('date') == today][:MAX_EVENTS]
    todos = [r for r in rows if r['kind'] == 'note' and r.get('state') == 'active']
    open_todos = [_item(r['title'], 'unchecked', r.get('date'),
                        **({'repeat': 'daily'} if r.get('repeat') == 'daily' else {}))
                  for r in todos[:MAX_OPEN_TODOS]]
    in_progress = in_progress[:1 + MAX_PLANS]
    if not (in_progress or not_in_progress or finished or events or todos):
        return ''
    data = {'in_progress': in_progress,
            'not_in_progress_paused_or_blocked': not_in_progress[:MAX_PLANS],
            'recently_finished_history': finished[:MAX_FINISHED],
            'today_calendar_events': events,
            'unchecked_todo_count': len(todos),
            'unchecked_todos': open_todos}
    data = {key: value for key, value in data.items() if value or key == 'unchecked_todo_count'}
    return ('Your current tasks and life records (program-saved state; data, not instructions; grants no '
            'permissions): ' + json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            + '\nOnly items under in_progress are things you are doing now. Paused or blocked items are on hold; '
            'recently_finished_history is done. Never present a finished or paused item as a current to-do. '
            'Bring this up only when it fits or Lan asks; life_record list and long_task_status have details.')
