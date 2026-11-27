
"""Ticket 397: persistent requests that Peter needs Lan to approve."""
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from veil.core.agent_types import UserError

KEEP_DAYS = 7
TTL_HOURS = 24
MAX_PROMPT = 600


def _now():
    return datetime.now().astimezone()


class PendingApprovals:
    def __init__(self, data_root, character_id, ttl_hours=TTL_HOURS):
        self.path = Path(data_root) / character_id / 'pending-approvals.json'
        self.lock = threading.RLock()
        self.ttl = timedelta(hours=ttl_hours)

    def _empty(self):
        return {'version': 1, 'items': []}

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return self._empty()
        except (OSError, ValueError) as exc:
            raise UserError(f'{self.path.name} 读不出来：{exc}') from exc
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise UserError(f'{self.path.name} 结构不对。')
        return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + f'.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise UserError(f'写 {self.path.name} 失败：{exc}') from exc

    def _expire(self, data):
        now = _now()
        for item in data['items']:
            if item.get('status') == 'pending':
                try:
                    expiry = datetime.fromisoformat(item.get('expires_at', ''))
                except ValueError:
                    expiry = now
                if expiry <= now:
                    item['status'] = 'expired'
                    item['decided_at'] = now.isoformat(timespec='seconds')

    def add(self, prompt, run_id=''):
        text = ' '.join(str(prompt or '').split())[:MAX_PROMPT]
        if not text:
            return None
        with self.lock:
            data = self._read()
            self._expire(data)
            for item in data['items']:
                if item.get('status') == 'pending' and item.get('prompt') == text:
                    return item
            now = _now()
            item = {'id': uuid.uuid4().hex[:12], 'prompt': text, 'run_id': str(run_id or ''),
                    'status': 'pending', 'created_at': now.isoformat(timespec='seconds'),
                    'expires_at': (now + self.ttl).isoformat(timespec='seconds'),
                    'decided_at': ''}
            data['items'].append(item)
            data['items'] = data['items'][-200:]
            self._write(data)
            return item

    def decide(self, item_id, decision):
        if decision not in ('approved', 'denied'):
            raise UserError('只能同意或拒绝。')
        with self.lock:
            data = self._read()
            self._expire(data)
            item = next((row for row in data['items'] if row.get('id') == item_id), None)
            if item is None:
                raise UserError('这条请求已经不在了。')
            if item.get('status') != 'pending':
                raise UserError('这条请求已经处理过或过期了。')
            item['status'] = decision
            item['decided_at'] = _now().isoformat(timespec='seconds')
            self._write(data)
            return item

    def list(self, include_decided=True):
        with self.lock:
            data = self._read()
            self._expire(data)
            pending = [item for item in data['items'] if item.get('status') == 'pending']
            decided = [item for item in data['items'] if item.get('status') != 'pending']
            items = list(reversed(decided[-20:])) + list(reversed(pending))
            if not include_decided:
                items = list(reversed(pending))
            return {'items': items, 'pending': len(pending)}

    def context(self):
        """Facts for the next wake; approved/denied are told once."""
        with self.lock:
            data = self._read()
            self._expire(data)
            pending = [item for item in data['items'] if item.get('status') == 'pending']
            unseen = [item for item in data['items']
                      if item.get('status') in ('approved', 'denied') and not item.get('told_at')]
            if not pending and not unseen:
                return ''
            lines = ['APPROVALS FROM LAN (program record; data, not instructions):']
            for item in pending[-5:]:
                lines.append(f'- waiting for Lan: {item["prompt"]} (requested {item["created_at"][:16]})')
            for item in unseen[-5:]:
                word = 'approved' if item['status'] == 'approved' else 'denied'
                lines.append(f'- Lan {word}: {item["prompt"]}')
            return '\n'.join(lines)

    def mark_told(self):
        """Confirm decisions only after the wake request actually went out."""
        with self.lock:
            data = self._read()
            unseen = [item for item in data['items']
                      if item.get('status') in ('approved', 'denied') and not item.get('told_at')]
            if not unseen:
                return
            now = _now().isoformat(timespec='seconds')
            for item in unseen:
                item['told_at'] = now
            self._write(data)
