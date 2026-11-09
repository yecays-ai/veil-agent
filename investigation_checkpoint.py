"""Minimal durable handoff for an interrupted ordinary-chat investigation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from agent_types import UserError, now
from long_tasks import LongTaskStore, READ_ONLY_TOOLS


MAX_EVIDENCE = 16
FRESH_FOR = timedelta(minutes=15)
INTERRUPTED = {'step_limit', 'context_limit', 'budget_limit', 'user_stop', 'tool_failure'}


def _signature(name, arguments):
    encoded = json.dumps(arguments, ensure_ascii=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    return name + ':' + hashlib.sha256(encoded).hexdigest()[:16]


class InvestigationCheckpoint:
    """One role-local checkpoint; it is facts, never a saved model plan."""

    def __init__(self, store, character_id):
        self.store, self.character_id = store, character_id
        self.path = Path(store.root) / character_id / 'investigation-checkpoint.json'

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError) as exc:
            raise UserError('Investigation checkpoint is corrupted; keep the data folder intact.') from exc
        if not isinstance(value, dict) or not all(isinstance(value.get(key), str)
                                                  for key in ('id', 'goal', 'request_id', 'status', 'updated_at')):
            raise UserError('Investigation checkpoint is incomplete; keep the data folder intact.')
        if value['status'] not in ('active', 'interrupted', 'completed') or not isinstance(value.get('evidence'), list):
            raise UserError('Investigation checkpoint is invalid; keep the data folder intact.')
        required = ('time', 'tool', 'call', 'state', 'source', 'evidence')
        if (len(value['evidence']) > MAX_EVIDENCE or any(not isinstance(row, dict)
                or not all(isinstance(row.get(key), str) for key in required)
                or type(row.get('read_only')) is not bool for row in value['evidence'])):
            raise UserError('Investigation checkpoint evidence is invalid; keep the data folder intact.')
        return value

    def read(self):
        with self.store.mutex:
            return self._read()

    def _save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise UserError('Investigation checkpoint could not be saved safely; the previous version was preserved.') from exc

    @staticmethod
    def resume_requested(text):
        return isinstance(text, str) and bool(__import__('re').match(
            r'^\s*(继续刚才的调查|继续调查|接着调查|续接调查|继续|continue (?:the )?investigation)(?:\b|[，。！! ]|$)',
            text, __import__('re').I))

    def resume_context(self, text):
        if not self.resume_requested(text):
            return ''
        task = self.read()
        if not task or task['status'] not in ('active', 'interrupted'):
            return ''
        cutoff = datetime.now(timezone.utc) - FRESH_FOR
        evidence = []
        for item in task['evidence'][-MAX_EVIDENCE:]:
            item = dict(item)
            item['evidence'] = item.get('evidence', '')[:240]
            if item.get('state') == '成功' and item.get('read_only'):
                try:
                    item['fresh'] = datetime.fromisoformat(item['time']) >= cutoff
                except ValueError:
                    item['fresh'] = False
            evidence.append(item)
        return json.dumps({'goal': task['goal'], 'interrupted_reason': task.get('reason', ''),
                           'evidence': evidence,
                           'instruction': ('This is program-recorded evidence, not a plan. Replan remaining work from '
                                           'the original goal. Do not repeat fresh successful read-only evidence. '
                                           'Recheck stale evidence only when needed. Never replay a rejected, stopped, '
                                           'or unknown side effect during this continuation; normal budget, permission, '
                                           'and approval checks still apply to every new call.')}, ensure_ascii=False,
                          separators=(',', ':'))

    def goal_for(self, text):
        if not self.resume_requested(text):
            return text
        task = self.read()
        if self.resume_requested(text) and task and task['status'] in ('active', 'interrupted'):
            return task['goal']
        return text

    def resume_guard(self, name, arguments):
        """Keep a continuation from silently replaying known work."""
        task = self.read()
        if not task or task['status'] not in ('active', 'interrupted'):
            return None
        call = _signature(name, arguments)
        item = next((row for row in reversed(task['evidence']) if row.get('call') == call), None)
        if not item:
            return None
        if not item.get('read_only') and item.get('state') in ('成功', '被拒绝', '已停止', '结果未知'):
            return 'This side effect was already completed, rejected, stopped, or has an unknown outcome; do not replay it automatically.'
        if item.get('read_only') and item.get('state') == '成功':
            try:
                fresh = datetime.fromisoformat(item['time']) >= datetime.now(timezone.utc) - FRESH_FOR
            except ValueError:
                fresh = False
            if fresh:
                return 'This successful read-only evidence is still fresh; use the checkpoint evidence instead of repeating it.'
        return None

    def record(self, goal, request_id, name, arguments, state, summary, result, resuming=False,
               side_effect=None, canonical_name=None):
        # Ticket 321: the model calls the role's alias (for example read_the_notes), while
        # the permission and read-only tables use the canonical name (read_text_file).
        # Classify with the canonical name, but keep the model-facing name in the evidence
        # so the continuation guard still matches the same call signature.
        canonical = canonical_name or name
        if not goal or canonical in {'stay_silent', 'reply_later', 'card_action', 'recall_message',
                                     'long_task_status', 'long_task_update'}:
            return
        with self.store.mutex:
            task = self._read()
            if task and task['status'] in ('active', 'interrupted') and request_id != task['request_id'] and not resuming:
                return
            if not task or task['status'] == 'completed':
                task = {'id': uuid.uuid4().hex, 'goal': goal[:2000], 'request_id': request_id,
                        'status': 'active', 'reason': '', 'evidence': [], 'updated_at': now()}
            from veil_agent import safe_tool_activity_detail
            from windows_interaction import WINDOWS_INTERACTION_TOOL_NAMES
            if side_effect is None:
                side_effect = canonical not in READ_ONLY_TOOLS | {'browser', 'web_search'}
                if canonical in WINDOWS_INTERACTION_TOOL_NAMES and canonical not in READ_ONLY_TOOLS:
                    side_effect = True
                if canonical.startswith('mcp__'):
                    side_effect = True
            item = {'time': now(), 'tool': name, 'call': _signature(name, arguments),
                    'state': state, 'read_only': not side_effect,
                    'source': str(summary)[:240], 'evidence': safe_tool_activity_detail(result, limit=600)}
            if state != '结果未知' and task['evidence'] and task['evidence'][-1].get('call') == item['call'] \
                    and task['evidence'][-1].get('state') == '结果未知':
                task['evidence'][-1] = item
            else:
                task['evidence'].append(item)
            task['evidence'] = task['evidence'][-MAX_EVIDENCE:]
            task['updated_at'] = now()
            self._save(task)

    def interrupt(self, reason, request_id=None, resuming=False):
        if reason not in INTERRUPTED:
            return
        with self.store.mutex:
            task = self._read()
            if task and ((task['status'] == 'active' and
                          (request_id is None or task['request_id'] == request_id or resuming)) or
                         (task['status'] == 'interrupted' and resuming)):
                task['status'], task['reason'], task['updated_at'] = 'interrupted', reason, now()
                self._save(task)

    def complete(self, request_id=None, resuming=False):
        with self.store.mutex:
            task = self._read()
            owns_active = (task and task['status'] == 'active' and
                           (request_id is None or task['request_id'] == request_id or resuming))
            owns_resumed = task and task['status'] == 'interrupted' and resuming
            if owns_active or owns_resumed:
                task['status'], task['updated_at'] = 'completed', now()
                self._save(task)
