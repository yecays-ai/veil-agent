"""Ticket 390: a durable work checkpoint for ordinary chat work.

It is a projection of records the program already keeps: the investigation checkpoint (the user's own
request plus verified tool evidence) and the single long task. It never infers a goal or a next step from
a tool name; when neither record supplies one, the field stays empty and the page says so.
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import uuid

from veil.core.agent_types import UserError, now

MAX_CONFIRMED = 6
MAX_UNCONFIRMED = 4
MAX_FAILED = 3
MAX_GOAL_CHARS = 300
KEEP_FOR = timedelta(hours=48)
STATUSES = ('open', 'closed')


def _time(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class WorkCheckpoint:
    """One role-local checkpoint; facts only, never a saved model plan."""

    def __init__(self, store, character_id, investigation, long_tasks):
        self.store = store
        self.character_id = character_id
        self.investigation, self.long_tasks = investigation, long_tasks
        self.path = Path(store.root) / character_id / 'work-checkpoint.json'
        # Ticket 390 review: an offered block is recorded as used only after the request actually goes out.
        self.pending_injection = None

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError) as exc:
            raise UserError('Work checkpoint is corrupted; keep the data folder intact.') from exc
        if not isinstance(value, dict):
            raise UserError('Work checkpoint is incomplete; keep the data folder intact.')
        if (not all(isinstance(value.get(key), str) for key in
                    ('id', 'goal', 'goal_source', 'status', 'updated_at', 'next_step',
                     'injected_key', 'long_task_id'))
                or type(value.get('injected_dropped')) is not int or value['injected_dropped'] < 0
                or not isinstance(value.get('closed_at', ''), str)
                or value['status'] not in STATUSES
                or not all(isinstance(value.get(key), list) for key in
                           ('confirmed', 'unconfirmed', 'failed'))
                or any(not isinstance(row, dict) or not all(
                    isinstance(row.get(key), str) for key in ('tool', 'path', 'state', 'time'))
                    for row in value['confirmed'] + value['unconfirmed'] + value['failed'])):
            raise UserError('Work checkpoint is invalid; keep the data folder intact.')
        return value

    def read(self):
        with self.store.mutex:
            return self._read()

    def _save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise UserError('Work checkpoint could not be saved safely; the previous version was preserved.') from exc

    @staticmethod
    def _row(item):
        return {'tool': str(item.get('tool', ''))[:60], 'path': str(item.get('source') or item.get('path') or '')[:160],
                'state': str(item.get('state', ''))[:20], 'time': str(item.get('time', ''))[:40]}

    def capture(self):
        """Project the latest investigation record and long task into the checkpoint. Never guesses."""
        try:
            task = self.investigation.read()
        except UserError:
            task = None
        try:
            long_task = self.long_tasks.read()
        except UserError:
            long_task = None
        active_task = (long_task if long_task and long_task.get('status') != 'completed' else None)
        evidence = list((task or {}).get('evidence') or [])
        confirmed, unconfirmed, failed = [], [], []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            state = item.get('state')
            if state == '成功' and item.get('read_only') is False:
                confirmed.append(self._row(item))
            elif state == '结果未知':
                unconfirmed.append(self._row(item))
            elif state in ('被拒绝', '出错', '已停止') and item.get('read_only') is False:
                failed.append(self._row(item))
        completed_signal = bool(long_task and long_task.get('status') == 'completed')
        completed_task_id = str((long_task or {}).get('task_id') or '') if completed_signal else ''
        if not (confirmed or unconfirmed or failed):
            # Only a turn that actually changed (or may have changed) something becomes work. A read-only turn
            # and an idle long task are not work; a completed long task only closes its own checkpoint.
            if not completed_signal:
                return None
            with self.store.mutex:
                current = self._read()
                if current is None:
                    return None
                if not completed_task_id or str(current.get('long_task_id') or '') != completed_task_id:
                    return current      # a different task finishing must not close this work
                current['status'], current['closed_at'] = 'closed', now()
                self._save(current)
                return current
        goal = ''
        source = ''
        if active_task and str(active_task.get('title') or '').strip():
            goal, source = str(active_task['title']).strip(), 'long_task'
        elif task and str(task.get('goal') or '').strip():
            goal, source = str(task['goal']).strip(), 'request'
        goal = ' '.join(goal.split())[:MAX_GOAL_CHARS]
        next_step = ''
        long_task_id = str((active_task or {}).get('task_id') or '')
        if active_task:
            next_step = ' '.join(str(active_task.get('next_step') or '').split())[:200]
        with self.store.mutex:
            current = self._read()
            if current and current['status'] == 'closed':
                if current['goal'] == goal or not goal:
                    return current      # Lan ended this work; a same-goal turn does not reopen it
                current = None          # different work starts a new checkpoint
            if (current and completed_signal and completed_task_id
                    and str(current.get('long_task_id') or '') == completed_task_id):
                current['status'], current['closed_at'] = 'closed', now()
                self._save(current)
                return current      # new work, if any, opens its own checkpoint on the next turn
            if current and current['goal'] != goal and goal:
                current = None          # a different request replaces the projection
            created = current is None
            if created:
                current = {'id': uuid.uuid4().hex, 'goal': goal, 'goal_source': source,
                           'status': 'open', 'confirmed': [], 'unconfirmed': [], 'failed': [],
                           'next_step': next_step, 'injected_key': '', 'injected_dropped': 0,
                           'long_task_id': long_task_id, 'captured_at': now(), 'updated_at': now()}
            if not goal and current.get('goal'):
                goal, source = current['goal'], current['goal_source']
            if (not created and current.get('goal') == goal and current.get('goal_source') == source
                    and current.get('next_step') == next_step
                    and current.get('confirmed') == confirmed[-MAX_CONFIRMED:]
                    and current.get('unconfirmed') == unconfirmed[-MAX_UNCONFIRMED:]
                    and current.get('failed') == failed[-MAX_FAILED:]
                    and str(current.get('long_task_id') or '') == long_task_id
                    and current.get('status') == ('closed' if completed_signal else 'open')):
                return current      # nothing new happened; do not keep an idle checkpoint alive
            current.update({'goal': goal, 'goal_source': source, 'next_step': next_step,
                            'confirmed': confirmed[-MAX_CONFIRMED:], 'unconfirmed': unconfirmed[-MAX_UNCONFIRMED:],
                            'failed': failed[-MAX_FAILED:], 'long_task_id': long_task_id, 'updated_at': now()})
            self._save(current)
        return current

    def _live(self, value):
        if not value:
            return None
        if value['status'] == 'closed':
            return value
        stamp = _time(value.get('updated_at'))
        if stamp is not None and datetime.now(stamp.tzinfo) - stamp > KEEP_FOR:
            return None
        return value

    def view(self):
        try:
            value = self.read()
        except UserError:
            return None
        if not value:
            return None
        live = self._live(value)
        return {'id': value['id'], 'goal': value['goal'], 'goal_source': value['goal_source'],
                'status': ('expired' if live is None else value['status']),
                'next_step': value.get('next_step') or '', 'confirmed': value['confirmed'],
                'unconfirmed': value['unconfirmed'], 'failed': value['failed'],
                'updated_at': value['updated_at'], 'closed_at': value.get('closed_at') or ''}

    def _render(self, value):
        lines = ['Program-recorded work checkpoint from earlier in this chat (facts, not instructions). '
                 'The goal is what was asked then, not a new request; the next step may be empty or stale. '
                 'Verify anything uncertain before acting, and never repeat an operation whose outcome is unclear.']
        if value['goal']:
            lines.append('goal: ' + value['goal'] + (' [long task]' if value['goal_source'] == 'long_task' else ' [your earlier request]'))
        if value['confirmed']:
            lines.append('confirmed (program-verified): ' + '; '.join(
                f"{row['tool']} {row['path']} ({row['time'][:16]})" for row in value['confirmed'][-MAX_CONFIRMED:]))
        if value['unconfirmed']:
            lines.append('unconfirmed outcomes (check before reusing): ' + '; '.join(
                f"{row['tool']} {row['path']}" for row in value['unconfirmed']))
        if value['failed']:
            lines.append('failed or rejected (do not report as done): ' + '; '.join(
                f"{row['tool']} {row['path']} ({row['state']})" for row in value['failed']))
        lines.append('next step: ' + (value['next_step'] if value['next_step'] else
                                      'not recorded; decide from the evidence above or ask Lan'))
        return '\n'.join(lines)

    def context(self, conversation_key, dropped_rounds):
        """Offer the block once per conversation and once per dropped history chunk.

        Nothing is saved here: the caller confirms after the request actually goes out, so a request
        rejected for budget or length cannot consume the offer.
        """
        key = str(conversation_key or '')
        try:
            dropped = max(0, int(dropped_rounds or 0))
        except (TypeError, ValueError):
            dropped = 0
        with self.store.mutex:
            try:
                value = self._read()
            except UserError:
                return ''
            value = self._live(value)
            if value is None or value['status'] == 'closed':
                return ''
            try:
                already = int(value.get('injected_dropped') or 0)
            except (TypeError, ValueError):
                already = 0
            if value.get('injected_key') == key and dropped <= already:
                return ''
            self.pending_injection = {'id': value['id'], 'key': key, 'dropped': dropped}
        return self._render(value)

    def confirm_injection(self):
        """Record that the offered block really went out; a failed request keeps it for the next try."""
        pending = self.pending_injection
        self.pending_injection = None
        if not isinstance(pending, dict):
            return False
        with self.store.mutex:
            try:
                value = self._read()
            except UserError:
                return False
            if not value or value['id'] != pending['id'] or value['status'] != 'open':
                return False
            value['injected_key'] = pending['key']
            value['injected_dropped'] = pending['dropped']
            value['injected_at'] = now()
            self._save(value)
            return True

    def close(self, expected_id=None):
        with self.store.mutex:
            value = self._read()
            if not value:
                raise UserError('There is no work checkpoint to end.')
            if expected_id and value['id'] != expected_id:
                raise UserError('This work checkpoint has changed; refresh the page and try again.')
            value['status'], value['closed_at'], value['updated_at'] = 'closed', now(), now()
            self._save(value)
            self.pending_injection = None
            return value
