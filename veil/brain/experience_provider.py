"""P7 role-local experience hints, configuration and evidence freshness."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from veil.brain.experience_feedback import ExperienceFeedbackLedger, is_low_risk_trial
from veil.brain.learned_context import _lexical_tokens
from veil.brain.storage.brain_lock import write_lock


DEFAULTS = {
    'enabled': False,
    'allow_in_autonomy': True,
    'allow_tool_step_recheck': True,
    'auto_promote_objective': False,
    'auto_promote_commitment': False,
    'semantic_gate_enabled': False,
    'semantic_gate_daily_budget_cny': 0.02,
    'max_items': 4,
    'max_tokens': 600,
    'session_cooldown_turns': 4,
}

_BOOL_KEYS = frozenset({
    'enabled', 'allow_in_autonomy', 'allow_tool_step_recheck',
    'auto_promote_objective', 'auto_promote_commitment', 'semantic_gate_enabled',
})
_INT_RANGES = {'max_items': (1, 4), 'max_tokens': (120, 600), 'session_cooldown_turns': (0, 20)}
_FLOAT_RANGES = {'semantic_gate_daily_budget_cny': (0.0, 1.0)}

# One synchronous chat turn shares this wall-clock budget for *all* evidence reads.
EVIDENCE_BUDGET_MS = 150
# A long task record is a single JSON document; never read an unbounded file inside a chat turn.
MAX_EVIDENCE_BYTES = 512 * 1024
MAX_CACHE_ENTRIES = 512
MAX_TOPIC_CHARS = 400
EVIDENCE_KINDS = frozenset({'memory', 'event', 'chat', 'autonomy', 'tool', 'long_task', 'ongoing'})
_CORE_KINDS = {
    'L1_user_feedback': frozenset({'chat', 'memory', 'event'}),
    'L2_objective_action': frozenset({'tool', 'long_task', 'autonomy', 'ongoing'}),
    'L3_autonomy_reflection': frozenset({'autonomy'}),
}
_DATA_START = '<<<EXPERIENCE_DATA'
_DATA_END = '<<<END_EXPERIENCE_DATA'
EXPERIENCE_DATA_START = '<<<EXPERIENCE_DATA>>>'
EXPERIENCE_DATA_END = '<<<END_EXPERIENCE_DATA>>>'


def _validate_setting(key, value):
    if key in _BOOL_KEYS:
        return value if type(value) is bool else None
    if key in _INT_RANGES:
        if type(value) is not int:
            return None
        low, high = _INT_RANGES[key]
        return value if low <= value <= high else None
    if key in _FLOAT_RANGES:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        low, high = _FLOAT_RANGES[key]
        return float(number) if number.is_finite() and Decimal(str(low)) <= number <= Decimal(str(high)) else None
    return None


def settings_from(raw):
    values = dict(DEFAULTS)
    for key, value in (raw or {}).items():
        if key in DEFAULTS and (checked := _validate_setting(key, value)) is not None:
            values[key] = checked
    return values


def load_persisted(store, role):
    try:
        raw = json.loads((Path(store.root) / role / 'experience-injection.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def clean_experience_text(value, *, limit=MAX_TOPIC_CHARS):
    """Render untrusted experience prose as one bounded data field.

    Control characters and the data-boundary markers are removed so a stored
    experience can never close its own data block and appear to be instructions.
    """
    text = ''.join(char for char in str(value or '') if char in '\n\t' or ord(char) >= 32)
    text = ' '.join(text.split())
    for marker in (_DATA_START, _DATA_END):
        text = text.replace(marker, '')
    return text[:limit]


def evidence_refs_of(candidate):
    refs = candidate.get('evidence_refs') if isinstance(candidate, dict) else None
    return [ref for ref in (refs or ()) if isinstance(ref, str) and ':' in ref and ref.split(':', 1)[1]]


def core_evidence_refs(candidate):
    """The evidence that must still be provable before a partially stale hint is allowed.

    L1/L2/L3 candidates stay usable only while at least one objective origin-tier
    reference is valid; L4/legacy candidates must keep *every* reference because
    nothing else anchors them.
    """
    refs = evidence_refs_of(candidate)
    stored = candidate.get('core_evidence_refs')
    if isinstance(stored, list) and stored:
        core = [ref for ref in stored if isinstance(ref, str) and ref in refs]
        if core:
            return core
    tier = str(candidate.get('origin_evidence_tier') or '')
    kinds = _CORE_KINDS.get(tier)
    if kinds is None:
        return list(refs)
    core = [ref for ref in refs if ref.split(':', 1)[0] in kinds]
    return core or list(refs)


def _sha(value):
    return hashlib.sha256(str(value).encode('utf-8', 'replace')).hexdigest()


def _digest(value):
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


class _Deadline:
    def __init__(self, milliseconds=EVIDENCE_BUDGET_MS):
        self.end = time.monotonic() + max(1, int(milliseconds)) / 1000.0

    def expired(self):
        return time.monotonic() >= self.end

    def remaining(self):
        return max(0.0, self.end - time.monotonic())


@dataclass(frozen=True, slots=True)
class SituationEnvelope:
    """Signals already computed for this turn; S2 may add matching without changing callers."""

    text: str
    entry_mode: str = 'chat'
    query_vector: tuple[float, ...] = ()
    context_signal: dict = field(default_factory=dict)
    wake_context: dict = field(default_factory=dict)
    tool_context: dict = field(default_factory=dict)
    head_covered: frozenset[str] = frozenset()
    recalled_texts: tuple[str, ...] = ()
    session_key: str = ''
    turn_index: int = 0

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        return cls(text=value if isinstance(value, str) else '')


class EvidenceValidator:
    """Validate role-local evidence under one wall-clock budget and fail closed."""

    def __init__(self, cfg, store, *, memory=None, profile=None):
        self.role = str(cfg.id)
        self.store = store
        self.memory = memory
        self.profile = profile
        self._lock = threading.RLock()
        self._cache = {}
        self._cache_db_key = None
        self._source_cache = {}

    @property
    def role_dir(self):
        return Path(self.store.root) / self.role

    @staticmethod
    def _file_fingerprint(path):
        try:
            with Path(path).open('rb') as handle:
                stat = os.fstat(handle.fileno())
                handle.seek(max(0, stat.st_size - 4096))
                tail = hashlib.sha256(handle.read()).hexdigest()
            return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, tail)
        except OSError:
            return None

    def _db_path(self):
        authority = getattr(self.memory, 'brain_authority', None)
        path = getattr(authority, 'db_path', None)
        return Path(path) if path else self.role_dir / 'brain.db'

    def _db_fingerprint(self, deadline):
        """A fingerprint that also covers pending deletions (cache must not outlive them)."""
        path = self._db_path()
        if not path.is_file() or deadline.expired():
            return None
        try:
            timeout = max(0.01, min(0.15, deadline.remaining()))
            conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=timeout)
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                row = conn.execute("SELECT value FROM meta WHERE key='digest'").fetchone()
                pending = ()
                if 'delete_requests' in tables:
                    pending = tuple(sorted(str(item[0]) for item in conn.execute(
                        "SELECT memory_id FROM delete_requests WHERE status='pending'")))
            finally:
                conn.close()
            return ('digest', row[0] if row and row[0] else '', 'pending', _digest(list(pending)))
        except sqlite3.Error:
            return None

    def _prune_cache(self, db_key):
        with self._lock:
            if db_key != self._cache_db_key:
                self._cache = {}
                self._cache_db_key = db_key
            elif len(self._cache) > MAX_CACHE_ENTRIES:
                for key in list(self._cache)[:len(self._cache) - MAX_CACHE_ENTRIES]:
                    self._cache.pop(key, None)

    def _cache_get(self, key):
        with self._lock:
            return self._cache.get(key)

    def _cache_put(self, key, value):
        with self._lock:
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                for old in list(self._cache)[:max(1, MAX_CACHE_ENTRIES // 4)]:
                    self._cache.pop(old, None)
            self._cache[key] = value

    def _jsonl_rows(self, name, deadline):
        path = self.role_dir / name
        fingerprint = self._file_fingerprint(path)
        if fingerprint is None:
            return None, None
        key = (name, fingerprint)
        with self._lock:
            if key in self._source_cache:
                return self._source_cache[key], fingerprint
        rows = []
        try:
            with path.open('r', encoding='utf-8') as handle:
                for line in handle:
                    if deadline.expired():
                        return None, fingerprint
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
        except (OSError, ValueError, UnicodeError):
            return None, fingerprint
        if deadline.expired():
            return None, fingerprint
        with self._lock:
            self._source_cache = {key: rows}
        return rows, fingerprint

    @staticmethod
    def _row_matches(row, ref_id, keys):
        if row.get('deleted') or row.get('withdrawn') or row.get('retracted'):
            return False
        return any(str(row.get(key) or '') == ref_id for key in keys)

    def _pending_memory_ids(self, conn, tables):
        if 'delete_requests' not in tables:
            return set()
        return {str(item[0]) for item in conn.execute(
            "SELECT memory_id FROM delete_requests WHERE status='pending'") if item[0]}

    def _memory_state(self, conn, memory_id, tables, pending):
        row = conn.execute(
            "SELECT body, title, type, bucket, is_tombstone, is_deleted FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return 'invalid', ''
        body, title, kind, bucket, tombstone, deleted = row
        if tombstone or deleted or str(memory_id) in pending:
            return 'invalid', ''
        if str(bucket or '') == 'archive' or str(kind or '') == 'archived':
            return 'invalid', ''
        return 'valid', _digest({
            'body': body, 'title': title, 'type': kind, 'bucket': bucket,
        })

    def _event_state(self, conn, event_id, tables, pending, deadline):
        cursor = conn.execute('SELECT * FROM events WHERE event_id=?', (event_id,))
        row = cursor.fetchone()
        if row is None:
            return 'invalid', ''
        columns = [item[0] for item in cursor.description]
        data = dict(zip(columns, row))
        linked = str(data.get('memory_id') or '')
        sources = [str(item[0]) for item in conn.execute(
            'SELECT memory_id FROM event_sources WHERE event_id=? ORDER BY ord', (event_id,)) if item[0]]
        source_memory_ids = [value for value in [linked, *sources] if value]
        live = []
        digest_parts = {'event': data}
        for memory_id in source_memory_ids[:8]:
            state, fingerprint = self._memory_state(conn, memory_id, tables, pending)
            if state == 'valid':
                live.append(memory_id)
                digest_parts.setdefault('memories', {})[memory_id] = fingerprint
            if deadline.expired():
                return 'unavailable', ''
        if not source_memory_ids:
            return 'invalid', ''
        if not live:
            return 'invalid', ''
        return 'valid', _digest(digest_parts)

    def _validate_chat(self, ref_id, deadline):
        lookup = getattr(self.profile, '_chat_lookup', None)
        if callable(lookup):
            try:
                row, status = lookup(ref_id, deadline)
            except Exception:
                return 'unavailable', ''
            if status == 'missing':
                return 'invalid', ''
            if status != 'ok' or not isinstance(row, dict):
                return 'unavailable', ''
            return 'valid', _digest({
                'speaker': row.get('role'), 'content': row.get('content') or row.get('text'),
                'time': row.get('time'), 'message_id': row.get('message_id') or row.get('id'),
            })
        rows, _fingerprint = self._jsonl_rows('chat.jsonl', deadline)
        if rows is None:
            return 'unavailable', ''
        matches = [row for row in rows if self._row_matches(row, ref_id, ('message_id', 'id'))]
        if len(matches) != 1:
            return 'invalid', ''
        row = matches[0]
        return 'valid', _digest({
            'speaker': row.get('role'), 'content': row.get('content') or row.get('text'),
            'time': row.get('time'), 'message_id': row.get('message_id') or row.get('id'),
        })

    @staticmethod
    def _clean_audit(row):
        audit = row.get('output_audit') if isinstance(row.get('output_audit'), dict) else {}
        issues = audit.get('issues')
        return audit.get('clean') is True and (issues is None or issues == [])

    def _validate_autonomy(self, ref_id, deadline):
        rows, _fingerprint = self._jsonl_rows('autonomy.jsonl', deadline)
        if rows is None:
            return 'unavailable', ''
        for row in rows:
            if str(row.get('started_at') or '') != ref_id:
                continue
            if row.get('deleted') or row.get('withdrawn') or row.get('retracted'):
                return 'invalid', ''
            if not self._clean_audit(row):
                return 'invalid', ''
            return 'valid', _digest({key: row.get(key) for key in (
                'started_at', 'ended_at', 'summary', 'output', 'output_audit', 'result', 'status')})
        return 'invalid', ''

    def _validate_tool(self, ref_id, deadline):
        rows, _fingerprint = self._jsonl_rows('tools.jsonl', deadline)
        if rows is None:
            return 'unavailable', ''
        for row in rows:
            if self._row_matches(row, ref_id, ('call_id', 'request_id', 'result_id', 'id')):
                return 'valid', _digest({key: row.get(key) for key in (
                    'call_id', 'request_id', 'result_id', 'id', 'tool', 'state', 'summary', 'result',
                    'started_at', 'finished_at')})
        return 'invalid', ''

    def _validate_long_task(self, ref_id, deadline):
        path = self.role_dir / 'long-task.json'
        if deadline.expired():
            return 'unavailable', ''
        try:
            size = path.stat().st_size
            if size > MAX_EVIDENCE_BYTES:
                return 'unavailable', ''
            with path.open('r', encoding='utf-8') as handle:
                raw = handle.read(MAX_EVIDENCE_BYTES + 1)
            if len(raw.encode('utf-8', 'replace')) > MAX_EVIDENCE_BYTES:
                return 'unavailable', ''
            if deadline.expired():
                return 'unavailable', ''
            row = json.loads(raw)
        except FileNotFoundError:
            return 'invalid', ''
        except (OSError, ValueError, UnicodeError):
            return 'unavailable', ''
        if deadline.expired():
            return 'unavailable', ''
        if not isinstance(row, dict) or str(row.get('task_id') or '') != ref_id:
            return 'invalid', ''
        if row.get('deleted') or row.get('withdrawn') or row.get('retracted'):
            return 'invalid', ''
        return 'valid', _digest(row)

    def _validate_ongoing(self, ref_id, deadline):
        if deadline.expired():
            return 'unavailable', ''
        path = self.role_dir / 'learned-context.json'
        try:
            if not path.is_file():
                return 'invalid', ''
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            return 'unavailable', ''
        if not isinstance(raw, dict):
            return 'unavailable', ''
        ongoing = raw.get('ongoing')
        items = ongoing.get('items') if isinstance(ongoing, dict) else ongoing
        if not isinstance(items, list):
            return 'invalid', ''
        for item in items:
            if not isinstance(item, dict) or str(item.get('id') or '') != ref_id:
                continue
            if item.get('deleted') or item.get('withdrawn') or item.get('retracted'):
                return 'invalid', ''
            return 'valid', _digest({
                'id': item.get('id'), 'text': item.get('text'), 'status': item.get('status'),
                'due': item.get('due'), 'due_at': item.get('due_at'), 'overdue': item.get('overdue'),
            })
        return 'invalid', ''

    def _resolve(self, kind, ref_id, deadline):
        if kind == 'chat':
            return self._validate_chat(ref_id, deadline)
        if kind in ('autonomy', 'tool', 'long_task', 'ongoing'):
            if kind == 'autonomy':
                return self._validate_autonomy(ref_id, deadline)
            if kind == 'tool':
                return self._validate_tool(ref_id, deadline)
            if kind == 'ongoing':
                return self._validate_ongoing(ref_id, deadline)
            return self._validate_long_task(ref_id, deadline)
        if kind not in ('memory', 'event'):
            return 'unavailable', ''
        db_key = self._db_fingerprint(deadline)
        if db_key is None:
            return 'unavailable', ''
        self._prune_cache(db_key)
        cache_key = (kind, ref_id, db_key)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        path = self._db_path()
        try:
            timeout = max(0.01, min(0.15, deadline.remaining()))
            conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=timeout)
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                pending = self._pending_memory_ids(conn, tables)
                if kind == 'memory':
                    result = self._memory_state(conn, ref_id, tables, pending)
                else:
                    result = self._event_state(conn, ref_id, tables, pending, deadline)
            finally:
                conn.close()
        except sqlite3.Error:
            result = ('unavailable', '')
        self._cache_put(cache_key, result)
        return result

    def current_fingerprints(self, refs, *, milliseconds=EVIDENCE_BUDGET_MS, deadline=None):
        """Current content fingerprints for evidence that is definitively valid now."""
        deadline = deadline or _Deadline(milliseconds)
        out = {}
        for ref in refs:
            if deadline.expired():
                break
            kind, ref_id = ref.split(':', 1)
            state, fingerprint = self._resolve(kind, ref_id, deadline)
            if state == 'valid' and fingerprint:
                out[ref] = fingerprint
        return out

    def validate_detailed(self, candidate, *, milliseconds=EVIDENCE_BUDGET_MS, deadline=None):
        """Return (state, detail) with both raw and fingerprint-checked evidence states."""
        refs = evidence_refs_of(candidate)
        if not refs:
            return 'invalid', {'raw': {}, 'effective': {}}
        deadline = deadline or _Deadline(milliseconds)
        recorded = candidate.get('evidence_fingerprints')
        recorded = recorded if isinstance(recorded, dict) else {}
        raw, effective = {}, {}
        for ref in refs:
            if deadline.expired():
                raw[ref] = effective[ref] = 'unavailable'
                continue
            kind, ref_id = ref.split(':', 1)
            state, fingerprint = self._resolve(kind, ref_id, deadline)
            raw[ref] = state
            if state == 'valid':
                expected = recorded.get(ref)
                if not isinstance(expected, str) or not expected:
                    # No formation-time baseline: this is an old/unverified row and its
                    # current body cannot be proven to be the body the experience used.
                    state = 'unavailable'
                elif expected != fingerprint:
                    state = 'invalid'
            effective[ref] = state
        values = list(effective.values())
        if all(state == 'valid' for state in values):
            return 'valid', {'raw': raw, 'effective': effective}
        core = [effective[ref] for ref in core_evidence_refs(candidate) if ref in effective]
        if 'valid' in core:
            return 'partial_stale', {'raw': raw, 'effective': effective}
        if 'invalid' in values:
            return 'invalid', {'raw': raw, 'effective': effective}
        return 'unavailable', {'raw': raw, 'effective': effective}

    def validate(self, candidate, *, milliseconds=EVIDENCE_BUDGET_MS, deadline=None):
        """Return valid | partial_stale | invalid | unavailable under the P7-0 rules.

        ``invalid`` means the referenced record (or its content) definitively changed
        or disappeared. ``unavailable`` means freshness could not be proven within the
        shared budget; callers pause injection but must not demote the candidate.
        """
        return self.validate_detailed(candidate, milliseconds=milliseconds, deadline=deadline)[0]

    def validate_many(self, candidates, *, milliseconds=EVIDENCE_BUDGET_MS, deadline=None):
        deadline = deadline or _Deadline(milliseconds)
        return [self.validate(candidate, deadline=deadline) for candidate in candidates]

    def inspect_ref_tier(self, ref, *, deadline=None):
        """Programmatically verify one evidence_ref and determine its objective L1-L4 capability.

        Never trusts model self-classification: checks actual speaker role, DB quotes/sources,
        clean autonomy audit + real tools, tool execution logs, and ongoing commitments.
        """
        if not isinstance(ref, str) or ':' not in ref:
            return {'state': 'invalid', 'tier': 'L4_self_eval', 'kind': '', 'ref': str(ref or ''), 'run_key': '', 'text': ''}
        kind, ref_id = ref.split(':', 1)
        deadline = deadline or _Deadline(max(EVIDENCE_BUDGET_MS, 500))
        state, _fp = self._resolve(kind, ref_id, deadline)
        base = {'state': state, 'tier': 'L4_self_eval', 'kind': kind, 'ref': ref, 'run_key': '', 'text': '', 'objective_kind': ''}
        if state != 'valid':
            return base
        if kind == 'chat':
            row = None
            lookup = getattr(self.profile, '_chat_lookup', None)
            if callable(lookup):
                try:
                    found, status = lookup(ref_id, deadline)
                    if status == 'ok' and isinstance(found, dict):
                        row = found
                except Exception:
                    row = None
            if row is None:
                rows, _ = self._jsonl_rows('chat.jsonl', deadline)
                row = next((r for r in (rows or ()) if self._row_matches(r, ref_id, ('message_id', 'id'))), None)
            if isinstance(row, dict):
                base['text'] = str(row.get('content') or row.get('text') or '')[:400]
                if str(row.get('role') or '').lower() == 'user':
                    base['tier'] = 'L1_user_feedback'
                    base['run_key'] = f'chat:{ref_id}'
            return base
        if kind == 'tool':
            rows, _ = self._jsonl_rows('tools.jsonl', deadline)
            row = next((r for r in (rows or ()) if self._row_matches(r, ref_id, ('call_id', 'request_id', 'result_id', 'id'))), None)
            if isinstance(row, dict):
                base['tier'] = 'L2_objective_action'
                base['objective_kind'] = 'tool'
                base['run_key'] = str(row.get('autonomy_run_id') or row.get('session_key') or row.get('request_id') or ref_id)
                base['tool_state'] = str(row.get('state') or '')
                base['text'] = f"tool={row.get('tool', '')} state={row.get('state', '')} {str(row.get('summary') or row.get('result') or '')[:300]}".strip()
            return base
        if kind == 'long_task':
            path = self.role_dir / 'long-task.json'
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                row = None
            if isinstance(row, dict) and str(row.get('task_id') or '') == ref_id:
                base['tier'] = 'L2_objective_action'
                base['objective_kind'] = 'long_task'
                base['run_key'] = f'long_task:{ref_id}'
                base['text'] = f"long_task={ref_id} status={row.get('status', '')} {str(row.get('summary') or row.get('goal') or '')[:300]}".strip()
            return base
        if kind == 'ongoing':
            path = self.role_dir / 'learned-context.json'
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                data = None
            ongoing = data.get('ongoing') if isinstance(data, dict) else None
            items = ongoing.get('items') if isinstance(ongoing, dict) else ongoing
            row = next((i for i in (items or ()) if isinstance(i, dict) and str(i.get('id') or '') == ref_id), None)
            if isinstance(row, dict):
                base['tier'] = 'L2_objective_action'
                base['objective_kind'] = 'commitment'
                base['run_key'] = f'ongoing:{ref_id}'
                base['text'] = f"ongoing={ref_id} status={row.get('status', '')} overdue={bool(row.get('overdue'))} {str(row.get('text') or '')[:300]}".strip()
            return base
        if kind == 'autonomy':
            rows, _ = self._jsonl_rows('autonomy.jsonl', deadline)
            row = next((r for r in (rows or ()) if str(r.get('started_at') or '') == ref_id), None)
            if isinstance(row, dict) and self._clean_audit(row):
                tools = row.get('tools') if isinstance(row.get('tools'), list) else []
                real_tools = [t for t in tools if isinstance(t, dict) and str(t.get('tool') or '').strip()]
                base['text'] = f"autonomy={ref_id} result={row.get('result', '')} {str(row.get('decision') or row.get('summary') or '')[:260]}".strip()
                if real_tools:
                    base['tier'] = 'L2_objective_action'
                    base['objective_kind'] = 'autonomy_tool'
                    base['run_key'] = f'autonomy:{ref_id}'
                    base['ok_tools'] = [t for t in real_tools if t.get('state') == '成功']
                    base['failed_tools'] = [t for t in real_tools if t.get('state') != '成功']
                    tool_desc = '; '.join(f"{t.get('tool')}:{t.get('state')}" for t in real_tools[:6])
                    base['text'] = f"{base['text']} tools=[{tool_desc}]"[:400]
                else:
                    base['tier'] = 'L3_autonomy_reflection'
                    base['run_key'] = f'autonomy:{ref_id}'
            return base
        if kind in ('memory', 'event'):
            path = self._db_path()
            if path.is_file():
                try:
                    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)
                    try:
                        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        mem_ids = []
                        if kind == 'memory':
                            mem_ids = [ref_id]
                            row = conn.execute('SELECT title, body FROM memories WHERE id=?', (ref_id,)).fetchone()
                            if row:
                                base['text'] = f"{row[0] or ''} {row[1] or ''}".strip()[:400]
                        else:
                            ev = conn.execute('SELECT memory_id, title, story FROM events WHERE event_id=?', (ref_id,)).fetchone()
                            if ev:
                                if ev[0]:
                                    mem_ids.append(str(ev[0]))
                                base['text'] = f"{ev[1] or ''} {ev[2] or ''}".strip()[:400]
                            if 'event_sources' in tables:
                                mem_ids.extend(str(r[0]) for r in conn.execute(
                                    'SELECT memory_id FROM event_sources WHERE event_id=?', (ref_id,)) if r[0])
                        has_user = False
                        for mid in mem_ids[:6]:
                            if 'quotes' in tables:
                                q = conn.execute(
                                    "SELECT 1 FROM quotes WHERE memory_id=? AND lower(COALESCE(speaker,'')) IN ('user','lan') LIMIT 1",
                                    (mid,),
                                ).fetchone()
                                if q:
                                    has_user = True
                                    break
                            if 'sources' in tables:
                                s = conn.execute(
                                    "SELECT 1 FROM sources WHERE memory_id=? AND (kind='chat' OR ref LIKE 'chat:%') LIMIT 1",
                                    (mid,),
                                ).fetchone()
                                if s:
                                    has_user = True
                                    break
                        if has_user:
                            base['tier'] = 'L1_user_feedback'
                            base['run_key'] = ref
                    finally:
                        conn.close()
                except sqlite3.Error:
                    pass
        return base


class ExperienceHintProvider:
    """Role-isolated P7 provider with deterministic local hybrid matching."""

    _WARM_LOCKS = weakref.WeakValueDictionary()
    _WARM_LOCKS_MUTEX = threading.Lock()
    _SHORT_ACKS = frozenset({'嗯', '嗯嗯', '好', '好的', '好呀', '行', '可以', 'ok', 'okay', '收到', '知道了'})
    _KIND_DOMAINS = {
        'tool_lesson': 'work_method', 'communication_pattern': 'relationship_consensus',
        'autonomy_lesson': 'autonomous_practice', 'commitment_lesson': 'commitment_accountability',
    }

    def __init__(self, cfg, store, *, consolidator, memory=None, profile=None,
                 semantic_gate=None, budget=None):
        if str(getattr(consolidator.cfg, 'id', '')) != str(cfg.id):
            raise ValueError('experience provider role mismatch')
        self.cfg = cfg
        self.store = store
        self.consolidator = consolidator
        self.memory = memory
        self.profile = profile
        self.semantic_gate = semantic_gate
        self.budget = budget
        self.validator = EvidenceValidator(cfg, store, memory=memory, profile=profile)
        self.feedback = getattr(consolidator, 'feedback_ledger', None) or ExperienceFeedbackLedger(store, cfg.id)
        self.mutex = threading.RLock()
        warm_key = (os.path.normcase(os.path.abspath(str(store.root))), str(cfg.id))
        with self._WARM_LOCKS_MUTEX:
            self._warm_lock = self._WARM_LOCKS.setdefault(warm_key, threading.RLock())
        self._cooldown = {}
        self._turns = {}
        self._local = threading.local()
        self._gate_day = ''
        self._gate_spent = 0.0
        self._head_vectors = self._read_head_vectors()
        self._candidate_cursor = 0
        self._maintenance_scheduler = None
        self.consolidator.experience_confirmed_callback = self.warm_embeddings

    def settings_path(self):
        return f'{self.cfg.id}/experience-injection.json'

    def _head_cache_path(self):
        return f'{self.cfg.id}/experience-head-vectors.json'

    def _read_head_vectors(self):
        try:
            raw = json.loads((Path(self.store.root) / self._head_cache_path()).read_text(encoding='utf-8'))
            return {key: value for key, value in raw.items()
                    if isinstance(key, str) and isinstance(value, dict)} if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_head_vectors(self):
        try:
            with write_lock(self.store, self.cfg.id):
                self.store.replace(self._head_cache_path(), self._head_vectors)
            return True
        except Exception:
            return False


    def settings(self):
        return settings_from(load_persisted(self.store, self.cfg.id))

    def settings_revision(self):
        raw = load_persisted(self.store, self.cfg.id)
        revision = raw.get('_revision')
        return revision if type(revision) is int and revision >= 1 else 1

    def update_settings(self, values, *, expected_revision=None):
        updates, rejected = {}, {}
        for key, value in (values or {}).items():
            checked = _validate_setting(key, value)
            if key not in DEFAULTS:
                rejected[key] = 'unknown'
            elif checked is None:
                rejected[key] = 'invalid'
            else:
                updates[key] = checked
        became_enabled = False
        with self.mutex, write_lock(self.store, self.cfg.id):
            raw = load_persisted(self.store, self.cfg.id)
            current = raw.get('_revision')
            current = current if type(current) is int and current >= 1 else 1
            if expected_revision is not None and expected_revision != current:
                return {'ok': False, 'status_code': 409, 'settings_revision': current,
                        'error': '配置已在其他窗口被更新，请刷新后再保存。'}
            became_enabled = updates.get('enabled') is True
            revision = current + 1 if updates else current
            self.store.replace(self.settings_path(), {**settings_from(raw), **updates, '_revision': revision})
        if became_enabled:
            self.warm_embeddings()
        return {'ok': True, 'settings': self.settings(), 'settings_revision': revision,
                'updated': updates, 'rejected': rejected, 'persistence_path': self.settings_path()}

    def diagnostics(self):
        return dict(getattr(self._local, 'debug', {}) or {})

    def _set_debug(self, status, **values):
        self._local.debug = {'status': status, **values}

    def confirm(self, candidate_ids):
        ids = [str(item) for item in (candidate_ids or ()) if item]
        if not ids:
            return []
        try:
            return self.consolidator.confirm_experience_candidates(ids)
        except Exception:
            return []

    @staticmethod
    def _valid_vector(vector):
        if not isinstance(vector, (list, tuple)) or not vector:
            return None
        try:
            values = tuple(float(item) for item in vector)
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(item) for item in values) or not any(values):
            return None
        return values

    @classmethod
    def _cosine(cls, left, right):
        left, right = cls._valid_vector(left), cls._valid_vector(right)
        if not left or not right or len(left) != len(right):
            return None
        # Scale before accumulating to avoid overflowing finite large components.
        left_scale, right_scale = max(map(abs, left)), max(map(abs, right))
        left = tuple(item / left_scale for item in left)
        right = tuple(item / right_scale for item in right)
        dot = math.fsum(a * b for a, b in zip(left, right))
        norm = math.sqrt(math.fsum(item * item for item in left) * math.fsum(item * item for item in right))
        return max(-1.0, min(1.0, dot / norm)) if norm else None

    def _domain(self, row):
        return str(row.get('domain') or self._KIND_DOMAINS.get(str(row.get('kind') or ''), ''))

    def _allowed_domain(self, row, envelope):
        domain = self._domain(row)
        if envelope.entry_mode == 'tool_step':
            return domain == 'work_method'
        if envelope.entry_mode == 'autonomy':
            wake = envelope.wake_context or {}
            working = any(wake.get(key) for key in (
                'has_active_long_task', 'has_pending_action', 'has_work_checkpoint',
                'has_investigation_checkpoint', 'previous_unconfirmed_tools'))
            allowed = {'work_method', 'commitment_accountability'} if working else {'autonomous_practice'}
            return domain in allowed
        return envelope.entry_mode in ('chat', 'mobile', 'voice')

    def _signal_score(self, row, envelope):
        domain = self._domain(row)
        signal = envelope.context_signal or {}
        wake = envelope.wake_context or {}
        tool = envelope.tool_context or {}
        work = bool(signal.get('is_work_task') or signal.get('mode') == 'technical_task'
                    or tool.get('error') or tool.get('no_progress') or tool.get('expanded_groups'))
        emotion = bool(signal.get('has_emotion_signal') or signal.get('recent_emotion_signal'))
        commitment = bool(signal.get('has_commitment_signal') or signal.get('active_ongoing'))
        if domain == 'work_method':
            return 1.0 if work else 0.0
        if domain == 'relationship_consensus':
            return 1.0 if emotion and not work else 0.0
        if domain == 'commitment_accountability':
            return 1.0 if commitment or wake.get('has_pending_action') else 0.0
        if domain == 'autonomous_practice':
            return 1.0 if envelope.entry_mode == 'autonomy' and not any(
                wake.get(key) for key in ('has_active_long_task', 'has_work_checkpoint', 'has_investigation_checkpoint')) else 0.0
        return 0.0

    @staticmethod
    def _trigger_text(row):
        return f"{row.get('trigger_condition', '')} | {row.get('domain', '')} | {row.get('experience_type', '')}"

    def vector_space(self):
        memory = getattr(self.cfg, 'memory', None) or {}
        return _sha(json.dumps([str(memory.get('base_url') or '').strip().rstrip('/'),
                               str(memory.get('model') or 'unknown')], separators=(',', ':')))

    def _cached_candidate_vector(self, row, query_vector=None):
        fingerprint = _sha(self._trigger_text(row))
        model = str((getattr(self.cfg, 'memory', None) or {}).get('model') or 'unknown')
        cached = row.get('trigger_embedding') if isinstance(row.get('trigger_embedding'), dict) else {}
        vector = self._valid_vector(cached.get('vector'))
        if (vector and cached.get('fingerprint') == fingerprint and cached.get('model') == model
                and cached.get('vector_space') == self.vector_space()
                and type(cached.get('dimension')) is int and cached['dimension'] == len(vector)
                and (query_vector is None or len(vector) == len(query_vector))):
            return vector
        return None

    def warm_embeddings(self, candidate_ids=None):
        if not self.settings()['enabled']:
            return 0
        # Separate from the settings mutex: never hold the Brain write lock during I/O.
        with self._warm_lock:
            built = self._warm_embeddings(candidate_ids)
            if self._head_claims():
                self.warm_head_embeddings()
            return built

    def start(self):
        from veil.brain.short_term_notes import PeriodicBackgroundTick
        if self._maintenance_scheduler is None:
            self._maintenance_scheduler = PeriodicBackgroundTick(
                f'{self.cfg.id}-p7-vector-maintenance', 900, self.maintain_vectors)
        self._maintenance_scheduler.start()

    def stop(self):
        if self._maintenance_scheduler is not None:
            self._maintenance_scheduler.stop()

    def maintain_vectors(self, stopped=lambda: False):
        """Refresh changed HEAD/candidate vectors off the chat path while P7 is enabled."""
        if stopped() or not self.settings()['enabled']:
            return 0
        with self._warm_lock:
            return self._warm_embeddings() + self.warm_head_embeddings()

    def _warm_embeddings(self, candidate_ids=None):
        """Build missing candidate vectors only at explicit enable/confirm boundaries."""
        if not self.settings()['enabled']:
            return 0
        wanted = {str(item) for item in (candidate_ids or ()) if item}
        if isinstance(candidate_ids, str):
            wanted = {candidate_ids}
        embedder_factory = getattr(self.memory, 'embedder', None)
        if not callable(embedder_factory):
            return 0
        try:
            rows = self.consolidator.query_experience_candidates(
                situation='', statuses=('confirmed',), semantic_pool=True)
            embedder = embedder_factory()
        except Exception:
            return 0
        model = str((getattr(self.cfg, 'memory', None) or {}).get('model') or 'unknown')
        built = 0
        vector_space = self.vector_space()
        for row in rows:
            if wanted and str(row.get('id') or '') not in wanted:
                continue
            text = self._trigger_text(row)
            fingerprint = _sha(text)
            if self._cached_candidate_vector(row) is not None:
                continue
            reservation = None
            try:
                tokens = max(512, len(text.encode('utf-8')))
                rate = Decimal(str((getattr(self.cfg, 'memory', None) or {}).get(
                    'input_cny_per_million', 0) or 0))
                if self.budget is not None:
                    reservation = self.budget.estimate(self.cfg.id, model, tokens, rate)
                method = getattr(embedder, 'embed', None)
                vector = method(text) if callable(method) else embedder(text)
                if reservation is not None:
                    reported = getattr(embedder, 'usage_tokens', None)
                    amount = (Decimal(reported) * rate / Decimal(1_000_000)
                              if type(reported) is int and reported > 0 else reservation[1])
                    if amount > reservation[1]:
                        amount = reservation[1]
                    self.budget.settle_estimate(
                        reservation, accounting='p7-candidate-embedding',
                        settled_amount=amount,
                        usage_tokens=reported if type(reported) is int and reported > 0 else None)
                vector = self._valid_vector(vector)
                if vector is None:
                    continue
                if self.consolidator.cache_experience_embedding(
                        row.get('id'), fingerprint=fingerprint, model=model, vector=vector,
                        vector_space=vector_space):
                    built += 1
            except Exception:
                if reservation is not None:
                    try:
                        self.budget.settle_estimate(
                            reservation, accounting='p7-candidate-embedding-unknown-usage')
                    except Exception:
                        pass
                continue
        return built

    def _head_claims(self):
        if self.profile is None or not hasattr(self.profile, 'build_prompt'):
            return ()
        try:
            projection = self.profile.build_prompt()
            notes = str(projection.get('notes') or '').splitlines()
            claims = tuple(str(item).strip('- ').strip() for item in notes if str(item).strip())
            if not hasattr(self.profile, 'relationship_items'):
                return claims
            from veil.brain.relationship_base import build
            relationship = build(self.profile, getattr(self.cfg, 'profile', None), projection=projection)
            return claims + tuple(
                str(item.get('line') or '').strip() for item in relationship.get('items', ())
                if isinstance(item, dict) and item.get('line'))
        except Exception:
            return ()

    def warm_head_embeddings(self, claims=None):
        """Explicitly cache HEAD and guidance vectors; ordinary turns only read them."""
        if not self.settings()['enabled']:
            return 0
        factory = getattr(self.memory, 'embedder', None)
        if not callable(factory):
            return 0
        try:
            embedder = factory()
        except Exception:
            return 0
        model, space, built = str((getattr(self.cfg, 'memory', None) or {}).get('model') or 'unknown'), self.vector_space(), 0
        texts = [str(item or '').strip() for item in (claims if claims is not None else self._head_claims())
                 if str(item or '').strip()]
        if not texts:
            return 0
        try:
            rows = self.consolidator.query_experience_candidates(
                situation='', statuses=('confirmed',), semantic_pool=True)
            texts.extend(str(row.get('guidance') or '').strip() for row in rows if row.get('guidance'))
        except Exception:
            pass
        with self._warm_lock:
            self._head_vectors = self._read_head_vectors()
            keep = {_sha(text) for text in texts}
            self._head_vectors = {key: value for key, value in self._head_vectors.items() if key in keep}
            for text in texts:
                key = _sha(text)
                cached = self._head_vectors.get(key) or {}
                if (cached.get('space') == space and cached.get('model') == model
                        and self._valid_vector(cached.get('vector')) is not None):
                    continue
                try:
                    reservation = None
                    rate = Decimal(str((getattr(self.cfg, 'memory', None) or {}).get('input_cny_per_million', 0) or 0))
                    if self.budget is not None:
                        reservation = self.budget.estimate(self.cfg.id, model, max(512, len(text.encode('utf-8'))), rate)
                    vector = self._valid_vector(getattr(embedder, 'embed', embedder)(text))
                    if reservation is not None:
                        reported = getattr(embedder, 'usage_tokens', None)
                        amount = (Decimal(reported) * rate / Decimal(1_000_000)
                                  if type(reported) is int and reported > 0 else reservation[1])
                        self.budget.settle_estimate(
                            reservation, accounting='p7-head-embedding',
                            settled_amount=min(amount, reservation[1]),
                            usage_tokens=reported if type(reported) is int and reported > 0 else None)
                    if vector is not None:
                        self._head_vectors[key] = {'model': model, 'space': space, 'vector': list(vector)}
                        built += 1
                except Exception:
                    if reservation is not None:
                        try:
                            self.budget.settle_estimate(reservation, accounting='p7-head-embedding-unknown-usage')
                        except Exception:
                            pass
                    continue
            if not self._save_head_vectors():
                self._head_vectors = self._read_head_vectors()
                return 0
        return built

    def _head_semantically_covers(self, guidance, claims):
        space = self.vector_space()
        model = str((getattr(self.cfg, 'memory', None) or {}).get('model') or 'unknown')
        guidance_vec = self._head_vectors.get(_sha(str(guidance or '').strip())) or {}
        if guidance_vec.get('space') != space or guidance_vec.get('model') != model:
            return False
        for claim in claims or ():
            item = self._head_vectors.get(_sha(str(claim or '').strip())) or {}
            if item.get('space') != space or item.get('model') != model:
                continue
            similarity = self._cosine(guidance_vec.get('vector'), item.get('vector'))
            if similarity is not None and similarity >= 0.85:
                return True
        return False

    @staticmethod
    def _exception_matches(row, text_tokens, text):
        lowered = text.casefold()
        for exception in row.get('exceptions') or ():
            phrase = str(exception or '').strip()
            if not phrase:
                continue
            tokens = _lexical_tokens(phrase)
            overlap = len(text_tokens & tokens)
            if phrase.casefold() in lowered or (overlap >= 2 and overlap / max(1, len(tokens)) >= 0.6):
                return True
        return False

    @staticmethod
    def _covered_by_head(row, claims):
        if not claims:
            return False
        from veil.brain.learned_context import _matches_governed_claim
        guidance = str(row.get('guidance') or '')
        return any(_matches_governed_claim(guidance, str(claim)) for claim in claims if claim)

    @staticmethod
    def _recall_penalty(row, recalled):
        guidance = _lexical_tokens(str(row.get('guidance') or ''))
        return 0.15 if guidance and any(
            len(guidance & _lexical_tokens(text)) / max(1, len(guidance)) >= 0.8 for text in recalled) else 0.0

    @staticmethod
    def _already_recalled(row, recalled):
        return ExperienceHintProvider._recall_penalty(row, recalled) > 0

    @staticmethod
    def _conflicts(left, right):
        left_id, right_id = str(left.get('id') or ''), str(right.get('id') or '')
        links = {str(left.get('supersedes_id') or ''), str(left.get('superseded_by') or '')}
        reverse = {str(right.get('supersedes_id') or ''), str(right.get('superseded_by') or '')}
        explicit = set(left.get('conflicts_with') or ()) | set(right.get('conflicts_with') or ())
        if right_id in links or left_id in reverse or left_id in explicit or right_id in explicit:
            return True
        if str(left.get('domain') or '') != str(right.get('domain') or ''):
            return False
        lt = _lexical_tokens(str(left.get('trigger_condition') or ''))
        rt = _lexical_tokens(str(right.get('trigger_condition') or ''))
        if not lt or len(lt & rt) / max(1, min(len(lt), len(rt))) < 0.6:
            return False
        neg = re.compile(r"\b(?:not|never|avoid|don't|do not)\b|不要|避免|绝不", re.I)
        return bool(neg.search(str(left.get('guidance') or ''))) != bool(neg.search(str(right.get('guidance') or '')))

    @staticmethod
    def _priority(item):
        score, row = item
        lan = int(row.get('locked') is True or row.get('decision') == 'confirm'
                  or row.get('promoted_by') in ('lan', 'lan_confirmed', 'manual'))
        tier = {'L1_user_feedback': 3, 'L2_objective_action': 2,
                'L3_autonomy_reflection': 1, 'L4_self_eval': 0}.get(row.get('origin_evidence_tier'), 0)
        verified = str((row.get('verification') or {}).get('last_verified_at') or row.get('last_verified_at') or '')
        return lan, tier, verified, score, str(row.get('updated_at') or ''), str(row.get('id') or '')

    def _resolve_conflicts(self, scored):
        kept = []
        for item in sorted(scored, key=self._priority, reverse=True):
            if any(self._conflicts(item[1], other[1]) for other in kept):
                continue
            kept.append(item)
            yield item

    def _cooldown_allows(self, row, envelope, settings):
        if not envelope.session_key or settings['session_cooldown_turns'] <= 0:
            return True
        turn = envelope.turn_index or self._turns.get(envelope.session_key, 0) + 1
        previous = self._cooldown.get((envelope.session_key, str(row.get('id') or '')))
        changed = str((envelope.tool_context or {}).get('error_code') or '')
        return previous is None or turn - previous[0] > settings['session_cooldown_turns'] or (changed and changed != previous[1])

    def _record_cooldown(self, rows, envelope):
        if not envelope.session_key:
            return
        turn = envelope.turn_index or self._turns.get(envelope.session_key, 0) + 1
        error = str((envelope.tool_context or {}).get('error_code') or '')
        for row in rows:
            self._cooldown[(envelope.session_key, str(row.get('id') or ''))] = (turn, error)

    def _gray_gate(self, scored, envelope, settings):
        gray = [item for item in scored if 0.34 <= item[0] < 0.48][:3]
        if not gray or not settings['semantic_gate_enabled'] or not callable(self.semantic_gate):
            return []
        day = time.strftime('%Y-%m-%d')
        if day != self._gate_day:
            self._gate_day, self._gate_spent = day, 0.0
        cap = float(settings['semantic_gate_daily_budget_cny'])
        if self._gate_spent >= cap:
            return []
        try:
            result = self.semantic_gate(
                envelope, [row for _score, row in gray], timeout=2.0, daily_cap=cap)
            accepted, cost = result if isinstance(result, tuple) else (result, 0.0)
            cost = max(0.0, float(cost or 0.0))
            if self._gate_spent + cost > cap:
                return []
            self._gate_spent += cost
            accepted = set(accepted or ())
            return [item for item in gray if item[1].get('id') in accepted]
        except Exception:
            return []

    @staticmethod
    def _is_suspended(row):
        verification = row.get('verification') if isinstance(row.get('verification'), dict) else {}
        until = str(verification.get('suspension_until') or '').strip()
        if not until:
            return False
        if until.endswith('Z'):
            until = until[:-1] + '+00:00'
        try:
            until_dt = datetime.fromisoformat(until)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            return until_dt > datetime.now(timezone.utc)
        except ValueError:
            return False

    def select_trial(self, situation, *, injected_ids=()):
        """Select at most one matching, strictly low-risk unverified improvement for a single trial.

        Never promotes the unverified plan to confirmed, never enters normal confirmed slots,
        and never re-offers a candidate that already has an active or completed trial.
        """
        envelope = SituationEnvelope.from_value(situation)
        values = self.settings()
        if not values['enabled']:
            return None
        if envelope.entry_mode in ('glance', 'codex_watch'):
            return None
        if envelope.entry_mode == 'autonomy' and not values['allow_in_autonomy']:
            return None
        if envelope.entry_mode == 'tool_step' and not values['allow_tool_step_recheck']:
            return None
        normalized = re.sub(r'[^\w\s]', '', ' '.join(str(envelope.text or '').casefold().split())).strip()
        if envelope.entry_mode in ('chat', 'mobile', 'voice') and (
                not normalized or normalized in self._SHORT_ACKS or not _lexical_tokens(normalized)):
            return None
        try:
            pool = self.consolidator.query_experience_candidates(
                situation='', statuses=('uncertain',), semantic_pool=True, limit=64, semantic_offset=0,
            )
        except Exception:
            return None
        already_tried = self.feedback.active_or_completed_trial_exp_ids()
        excluded = set(str(item) for item in (injected_ids or ()) if item) | already_tried
        tokens = _lexical_tokens(envelope.text)
        deadline = _Deadline(EVIDENCE_BUDGET_MS)
        best = None
        for row in pool or ():
            exp_id = str(row.get('id') or '')
            if not exp_id or exp_id in excluded:
                continue
            if not is_low_risk_trial(row):
                continue
            if self._is_suspended(row):
                continue
            ver = row.get('verification') if isinstance(row.get('verification'), dict) else {}
            if ver.get('review_flag') or int(ver.get('negative_outcomes') or 0) > 0:
                continue
            if not self._allowed_domain(row, envelope):
                continue
            if self._exception_matches(row, tokens, envelope.text):
                continue
            candidate_tokens = _lexical_tokens(
                f"{row.get('trigger_condition', '')} {row.get('guidance', '')}")
            overlap = len(tokens & candidate_tokens)
            lexical = min(1.0, overlap / 2.0)
            signal = self._signal_score(row, envelope)
            vector = self._cached_candidate_vector(row, envelope.query_vector)
            semantic = self._cosine(envelope.query_vector, vector)
            if semantic is None:
                trigger_tokens = _lexical_tokens(str(row.get('trigger_condition') or ''))
                trigger_overlap = len(tokens & trigger_tokens)
                if trigger_overlap < 2 or trigger_overlap / max(1, len(trigger_tokens)) < 0.6:
                    continue
                total = 0.25 * signal + 0.20 * lexical
            else:
                total = 0.55 * semantic + 0.25 * signal + 0.20 * lexical
                if semantic < 0.36 or total < 0.42:
                    continue
            if deadline.expired():
                break
            evidence_state = self.validator.validate(row, deadline=deadline)
            if evidence_state not in ('valid', 'partial_stale'):
                continue
            if best is None or total > best[0]:
                best = (total, row, evidence_state)
        if best is None:
            return None
        score, row, evidence_state = best
        return {
            'trial_id': 'trial-' + uuid.uuid4().hex[:10],
            'experience_id': str(row.get('id') or ''),
            'trigger_condition': str(row.get('trigger_condition') or ''),
            'guidance': str(row.get('guidance') or ''),
            'reason': str(row.get('reason') or ''),
            'domain': self._domain(row),
            'experience_type': 'unverified_improvement',
            'evidence_state': evidence_state,
            'match_score': round(float(score), 6),
        }

    def __call__(self, situation):
        envelope = SituationEnvelope.from_value(situation)
        values = self.settings()
        if envelope.session_key:
            with self.mutex:
                turn = envelope.turn_index or self._turns.get(envelope.session_key, 0) + 1
                self._turns[envelope.session_key] = max(turn, self._turns.get(envelope.session_key, 0))
                envelope = replace(envelope, turn_index=turn)
        self._set_debug('empty', selected=0, dropped=[])
        if not values['enabled']:
            self._set_debug('disabled', selected=0, dropped=[])
            return []
        if envelope.entry_mode in ('glance', 'codex_watch'):
            self._set_debug('skipped_entry_mode', selected=0, dropped=[])
            return []
        if envelope.entry_mode == 'autonomy' and not values['allow_in_autonomy']:
            self._set_debug('skipped_entry_mode', selected=0, dropped=[])
            return []
        if envelope.entry_mode == 'tool_step' and not values['allow_tool_step_recheck']:
            self._set_debug('skipped_entry_mode', selected=0, dropped=[])
            return []
        normalized = re.sub(r'[^\w\s]', '', ' '.join(str(envelope.text or '').casefold().split())).strip()
        if envelope.entry_mode in ('chat', 'mobile', 'voice') and (
                not normalized or normalized in self._SHORT_ACKS or not _lexical_tokens(normalized)):
            self._set_debug('no_match', selected=0, dropped=['short_ack'])
            return []
        tokens = _lexical_tokens(envelope.text)
        evidence_deadline = _Deadline(EVIDENCE_BUDGET_MS)
        pool, offset, exhausted = [], self._candidate_cursor, False
        while len(pool) < 2048:
            batch = self.consolidator.query_experience_candidates(
                situation='', statuses=('confirmed',), semantic_pool=True,
                limit=256, semantic_offset=offset,
            )
            if not batch:
                exhausted = True
                break
            if len(batch) > 256:  # Compatible with test/legacy consolidators that ignore paging.
                pool = list(batch[:2048])
                exhausted = len(batch) <= 2048
                break
            pool.extend(batch)
            offset += len(batch)
            if len(batch) < 256:
                exhausted = True
                break
        self._candidate_cursor = 0 if exhausted else offset
        scored, gray, dropped = [], [], []
        for row in pool:
            if row.get('experience_type') == 'unverified_improvement':
                dropped.append((row.get('id'), 'unverified_improvement'))
                continue
            if self._is_suspended(row):
                dropped.append((row.get('id'), 'suspended'))
                continue
            if not self._allowed_domain(row, envelope):
                dropped.append((row.get('id'), 'domain'))
                continue
            if self._exception_matches(row, tokens, envelope.text):
                dropped.append((row.get('id'), 'matched_exception'))
                continue
            if (self._covered_by_head(row, envelope.head_covered)
                    or self._head_semantically_covers(row.get('guidance'), envelope.head_covered)):
                dropped.append((row.get('id'), 'covered_by_head'))
                continue
            if self._already_recalled(row, envelope.recalled_texts):
                dropped.append((row.get('id'), 'already_recalled'))
                continue
            if not self._cooldown_allows(row, envelope, values):
                dropped.append((row.get('id'), 'cooldown'))
                continue
            candidate_tokens = _lexical_tokens(
                f"{row.get('trigger_condition', '')} {row.get('guidance', '')}")
            overlap = len(tokens & candidate_tokens)
            lexical = min(1.0, overlap / 2.0)
            signal = self._signal_score(row, envelope)
            vector = self._cached_candidate_vector(row, envelope.query_vector)
            semantic = self._cosine(envelope.query_vector, vector)
            recall_penalty = self._recall_penalty(row, envelope.recalled_texts)
            if semantic is None:
                trigger_tokens = _lexical_tokens(str(row.get('trigger_condition') or ''))
                trigger_overlap = len(tokens & trigger_tokens)
                if (trigger_overlap < 2 or trigger_overlap / max(1, len(trigger_tokens)) < 0.6
                        or recall_penalty):
                    dropped.append((row.get('id'), 'threshold'))
                    continue
                total = 0.25 * signal + 0.20 * lexical - recall_penalty
            else:
                total = 0.55 * semantic + 0.25 * signal + 0.20 * lexical - recall_penalty
                if values['semantic_gate_enabled'] and 0.34 <= total < 0.48:
                    gray.append((total, row))
                    continue
                if semantic < 0.36 or total < 0.42:
                    dropped.append((row.get('id'), 'threshold'))
                    continue
            row = dict(row)
            row['match_score'] = round(total, 6)
            row['match_channels'] = {'semantic': semantic, 'signal': signal, 'lexical': lexical}
            scored.append((total, row))
        scored.extend(self._gray_gate(gray, envelope, values))
        resolved = self._resolve_conflicts(scored)
        remaining = (envelope.tool_context or {}).get('remaining_items')
        item_limit = min(values['max_items'], remaining) if type(remaining) is int and remaining >= 0 else values['max_items']
        selected = []
        for _score, row in resolved:
            if len(selected) >= item_limit or evidence_deadline.expired():
                break
            evidence_state = self.validator.validate(row, deadline=evidence_deadline)
            if evidence_state not in ('valid', 'partial_stale'):
                dropped.append((row.get('id'), 'evidence_' + evidence_state))
                continue
            row['evidence_state'] = evidence_state
            selected.append(row)
        if not selected:
            self._set_debug('no_match', selected=0, dropped=dropped)
            return []
        allowed = set(self.confirm([row.get('id') for row in selected]))
        with self.mutex:
            selected = [row for row in selected if row.get('id') in allowed
                        and self._cooldown_allows(row, envelope, values)]
            self._record_cooldown(selected, envelope)
        self._set_debug('matched' if selected else 'no_match', selected=len(selected), dropped=dropped,
                        ids=[row.get('id') for row in selected])
        return selected
