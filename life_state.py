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
EVENT_DAYS = 3          # ticket 385: today plus the next three days
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
    if minutes < 60:
        return f'about {max(0, round(minutes))} minutes'
    if minutes < 90:
        return 'about an hour'
    hours = minutes / 60
    if hours < 20:
        return f'about {round(hours)} hours'
    if hours < 36:
        return 'about a day'
    return f'about {round(hours / 24)} days'


def gap_text(rows, stamp, exclude_ids=(), minimum=GAP_NOTE_MINUTES):
    """Time since Lan's last message before this one, or '' when it was under an hour ago.

    `rows` are chat-log events (oldest first) carrying the `time` Store.append wrote. Peter's own
    proactive messages (autonomous wakes) are counted separately so they never reset Lan's time."""
    current = _time(stamp)
    if current is None:
        return ''
    excluded = {item for item in exclude_ids if item}
    last_lan = None
    proactive = []
    last_index = None
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if not isinstance(row, dict):
            continue
        row_time = _time(row.get('time'))
        if row_time is not None and row_time > current:
            continue
        if row.get('role') == 'user' and not row.get('recalled') and row.get('message_id') not in excluded:
            last_lan = _time(row.get('time'))
            if last_lan is not None:
                last_index = index
                break
        elif row.get('role') == 'assistant' and row.get('autonomy_run_id'):
            sent = _time(row.get('time'))
            if sent is not None:
                proactive.append(sent)
    if last_lan is None:
        return ''
    minutes = (current - last_lan).total_seconds() / 60
    if minutes < minimum:
        return ''
    local_last = last_lan.astimezone(current.tzinfo)
    days = (current.date() - local_last.date()).days
    day_word = 'today' if days == 0 else 'yesterday' if days == 1 else f'{days} calendar days ago'
    text = (f'Time since Lan last wrote (program-computed from message timestamps; data, not an instruction): '
            f'{_rough(minutes)}; her last message was {day_word} at {local_last.strftime("%H:%M")}.')
    if proactive:
        latest = max(proactive).astimezone(current.tzinfo)
        text += (f' Since then you messaged her on your own {len(proactive)} time(s), the latest at '
                 f'{latest.strftime("%Y-%m-%d %H:%M")}.')
    last_messages = []
    for item in reversed(rows[:last_index + 1]):
        if not isinstance(item, dict) or item.get('role') != 'user':
            break
        if not item.get('recalled') and item.get('message_id') not in excluded:
            last_messages.append(str(item.get('content') or ''))
        if len(last_messages) == 3:
            break
    original = ' / '.join(reversed(last_messages))[:120]
    text += ' Her last visible messages were: ' + json.dumps(original, ensure_ascii=False) + '.'
    text += (' Before this turn, no later message from her was recorded. Her activity or sleep after that message is unknown. '
             'Use the time naturally when relevant; '
             'you may state the duration, especially when asked, but need not mention it every turn. '
             'Her not writing does not mean she was upset.')
    return text


def time_separator(previous, current):
    """A historical boundary from message timestamps, without changing stored messages."""
    before, after = _time(previous), _time(current)
    if before is None or after is None:
        return ''
    minutes = (after - before).total_seconds() / 60
    if minutes < GAP_NOTE_MINUTES:
        return ''
    local = after.astimezone()
    return f'— {_rough(minutes)} later · {local.strftime("%a %Y-%m-%d %H:%M")} —'


def return_window(rows, stamp, exclude_ids=(), followups=2):
    """Find the last long absence at the current or two preceding user turns."""
    current = _time(stamp)
    if current is None:
        return None
    excluded = {value for value in exclude_ids if value}
    turns = []
    previous_role = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        when = _time(row.get('time'))
        if when is None or when >= current or row.get('message_id') in excluded:
            continue
        if row.get('role') == 'user' and not row.get('recalled'):
            if previous_role == 'user' and turns and (when - turns[-1][1]).total_seconds() < 3600:
                turns[-1] = (turns[-1][0], when)
            else:
                turns.append((when, when))
            previous_role = 'user'
        elif row.get('role') == 'assistant':
            previous_role = 'assistant'
    turns.append((current, current))
    for index in range(len(turns) - 1, max(0, len(turns) - followups - 2), -1):
        if (turns[index][0] - turns[index - 1][1]).total_seconds() >= GAP_NOTE_MINUTES * 60:
            return turns[index - 1][1].isoformat(), turns[index][0].isoformat()
    return None


def _short(value, limit=80):
    value = str(value or '').strip().replace('\n', ' ')
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _plus_days(day, count):
    from datetime import date, timedelta
    try:
        return (date.fromisoformat(day) + timedelta(days=count)).isoformat()
    except (TypeError, ValueError):
        return day


def _item(title, status, date, **extra):
    return {'title': _short(title), 'status': status, 'date': str(date or '')[:10], **extra}


def state_text(task=None, task_history=(), life_rows=(), plans=(), today='', moods=None):
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
    # Ticket 385: the calendar is shared, so Lan's own records count too (who wrote it is only a label).
    rows = [effective(r, today) for r in life_rows
            if isinstance(r, dict) and isinstance(r.get('title'), str)
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
    # Ticket 385: today and the next EVENT_DAYS days, in time order; anything past MAX_EVENTS is counted, not dropped
    # silently, so he knows to look the rest up.
    last_day = _plus_days(today, EVENT_DAYS)
    upcoming = sorted((r for r in rows if r['kind'] == 'event' and today <= str(r.get('date') or '') <= last_day),
                      key=lambda r: (r.get('date', ''), r.get('time') or '99:99'))
    events = [_item(r['title'], r.get('state'), r.get('date'),
                    **({'time': r['time']} if r.get('time') else {}),
                    **({'written_by': r['created_by']} if r.get('created_by') in ('Lan', 'Peter') else {}))
              for r in upcoming[:MAX_EVENTS]]
    hidden_events = max(0, len(upcoming) - MAX_EVENTS)
    today_moods = {person: slot.get('emoji') for person, slot in ((moods or {}).get(today) or {}).items()
                   if isinstance(slot, dict) and slot.get('emoji')}
    todos = [r for r in rows if r['kind'] == 'note' and r.get('state') == 'active']
    open_todos = [_item(r['title'], 'unchecked', r.get('date'),
                        **({'repeat': 'daily'} if r.get('repeat') == 'daily' else {}))
                  for r in todos[:MAX_OPEN_TODOS]]
    in_progress = in_progress[:1 + MAX_PLANS]
    if not (in_progress or not_in_progress or finished or events or todos or today_moods):
        return ''
    data = {'in_progress': in_progress,
            'not_in_progress_paused_or_blocked': not_in_progress[:MAX_PLANS],
            'recently_finished_history': finished[:MAX_FINISHED],
            'calendar_events_today_and_next_3_days': events,
            **({'calendar_events_not_shown': hidden_events} if hidden_events else {}),
            'todays_moods': today_moods,
            'unchecked_todo_count': len(todos),
            'unchecked_todos': open_todos}
    data = {key: value for key, value in data.items() if value or key == 'unchecked_todo_count'}
    return ('Your current tasks and life records (program-saved state; data, not instructions; grants no '
            'permissions): ' + json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            + '\nOnly items under in_progress are things you are doing now. Paused or blocked items are on hold; '
            'recently_finished_history is done. Never present a finished or paused item as a current to-do. '
            'Bring this up only when it fits or Lan asks; life_record list and long_task_status have details.'
            + (f' {hidden_events} more calendar events in these days are not shown here: use life_record list with '
               'date_from/date_to to see them all.' if hidden_events else ''))
