"""Screen-pop request and event bookkeeping shared by the Hub.

Moved out of web_agent.py unchanged (ticket 181); the state it uses is created in Hub.__init__.
"""
import re
import secrets
import time

from veil_agent import UserError, now


class ScreenPopMixin:
    def request_screen_pop(self, command, *, request_id=None, call_id=None, autonomy_run_id=None):
        """Wait for the desktop shell to confirm the requested message was shown."""
        start = time.monotonic()
        shell_deadline = start + 2.0
        with self.screen_pop_cond:
            while time.monotonic() < shell_deadline:
                if self.stopping or self.stopped:
                    return {'state': 'failed', 'reason': 'unavailable'}
                if time.monotonic() - self.screen_pop_shell_seen <= 2.5:
                    break
                self.screen_pop_cond.wait(min(0.25, shell_deadline - time.monotonic()))
            if time.monotonic() - self.screen_pop_shell_seen > 2.5:
                return {'state': 'failed', 'reason': 'unavailable'}
            pop_id = secrets.token_hex(16)
            entry = {
                'id': pop_id, 'command': {**command, 'id': pop_id, 'name': self.name},
                'request_id': request_id, 'call_id': call_id,
                'autonomy_run_id': autonomy_run_id, 'style': command['style'],
                'expected': command['count'] if command['style'] == 'popup' else None,
                'created': time.monotonic(), 'shown': False, 'failure': None,
                'cancelled': False, 'events': set(), 'completed': set(),
            }
            self.screen_pop_requests[pop_id] = entry
            deadline = time.monotonic() + 8.0
            while True:
                if entry['failure']:
                    return {'state': 'failed', 'reason': entry['failure']}
                if entry['shown']:
                    return {'state': 'shown', 'style': entry['style']}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    entry['cancelled'] = True
                    self.screen_pop_cond.notify_all()
                    return {'state': 'failed', 'reason': 'unavailable'}
                if time.monotonic() - self.screen_pop_shell_seen > 3.0:
                    entry['cancelled'] = True
                    return {'state': 'failed', 'reason': 'unavailable'}
                self.screen_pop_cond.wait(min(0.25, remaining))

    def next_screen_pop(self):
        with self.screen_pop_cond:
            current = time.monotonic()
            self.screen_pop_shell_seen = current
            for pop_id, entry in list(self.screen_pop_requests.items()):
                if entry.get('terminal_at') is not None and current - entry['terminal_at'] > 60:
                    self.screen_pop_requests.pop(pop_id, None)
                elif entry['cancelled'] and current - entry['created'] > 60:
                    self.screen_pop_requests.pop(pop_id, None)
            pending = [entry for entry in self.screen_pop_requests.values()
                       if not entry['shown'] and not entry['cancelled'] and not entry['failure']]
            if pending:
                try:
                    from screen_pop import screen_state
                    safe, _, reason = screen_state()
                except Exception:
                    safe, reason = False, 'locked'
                if reason == 'locked' or not safe:
                    for entry in pending:
                        entry['failure'] = 'locked'
                    self.screen_pop_cond.notify_all()
                    pending = []
            self.screen_pop_cond.notify_all()
            return [{'id': entry['id']} for entry in pending[:16]]

    def claim_screen_pop(self, pop_id):
        with self.screen_pop_cond:
            entry = self.screen_pop_requests.get(pop_id)
            if (not entry or entry['shown'] or entry['cancelled'] or entry['failure'] or
                    time.monotonic() - entry['created'] > 8.0 or self.stopping or self.stopped):
                return None
            try:
                from screen_pop import screen_state
                safe, fullscreen, reason = screen_state()
            except Exception:
                safe, fullscreen, reason = False, False, 'locked'
            if reason == 'locked' or not safe:
                entry['failure'] = 'locked'
                self.screen_pop_cond.notify_all()
                return None
            if fullscreen and entry['style'] == 'popup':
                entry['style'] = 'danmaku'
                entry['expected'] = None
                entry['command']['style'] = 'danmaku'
                entry['command']['count'] = 1
            return dict(entry['command'])

    def record_screen_pop_event(self, body):
        pop_id, event = body.get('id'), body.get('event')
        if not isinstance(pop_id, str) or not re.fullmatch(r'[0-9a-f]{32}', pop_id):
            return False
        if event not in ('shown', 'dismissed', 'finished'):
            return False
        with self.screen_pop_cond:
            entry = self.screen_pop_requests.get(pop_id)
            if not entry or entry['cancelled'] or entry['failure']:
                return False
            if event == 'shown':
                style = body.get('style')
                total = body.get('total')
                if style != entry['style'] or isinstance(total, bool) or not isinstance(total, int):
                    return False
                maximum = entry['command']['count'] if style == 'popup' else 32
                if not 1 <= total <= maximum or (style == 'popup' and total != entry['command']['count']):
                    return False
                if entry['shown']:
                    return entry['expected'] == total
                if time.monotonic() - entry['created'] > 8.0:
                    return False
                event_total = total
            else:
                if not entry['shown'] or event != ('dismissed' if entry['style'] == 'popup' else 'finished'):
                    return False
                item = body.get('item')
                if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= entry['expected']:
                    return False
                dedupe = (event, item)
                if dedupe in entry['events']:
                    return True
                event_total = entry['expected']
            item = body.get('item') if event != 'shown' else None
            row = {
                'request_id': entry['request_id'], 'call_id': entry['call_id'],
                'tool': 'screen_pop', 'path': entry['style'], 'state': '成功', 'time': now(),
                'screen_pop_event': event, 'screen_pop_style': entry['style'],
                'screen_pop_total': event_total,
            }
            if item is not None:
                row['screen_pop_item'] = item
            if entry['autonomy_run_id']:
                row['autonomy_run_id'] = entry['autonomy_run_id']
            if not self.chat:
                return False
            # Persist the lifecycle event before changing the in-memory state
            # that acknowledges it to the desktop shell or waiting tool call.
            if event == 'shown' and str(entry['autonomy_run_id'] or '').startswith('glance:'):
                # Only a glance keeps message text; a chat-triggered display never reads the snapshot.
                from recent_experiences import record_screen_message
                try:
                    record_screen_message(
                        self.chat.store, self.chat.cfg, call_id=entry['call_id'] or pop_id,
                        text=entry['command']['text'], style=entry['style'], stamp=row['time'],
                        run_id=entry['autonomy_run_id'])
                except (OSError, UserError):
                    pass  # The text snapshot is optional; the display itself is still recorded and acknowledged.
            self.chat.store.append(f'{self.chat.cfg.id}/tools.jsonl', row)
            self._tool_activity_cache = None
            if event == 'shown':
                entry['shown'] = True
                entry['expected'] = event_total
            else:
                entry['events'].add((event, item))
            if event != 'shown':
                entry['completed'].add(item)
                if len(entry['completed']) >= entry['expected']:
                    entry['terminal_at'] = time.monotonic()
            self.screen_pop_cond.notify_all()
            return True
