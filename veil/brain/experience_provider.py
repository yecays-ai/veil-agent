"""P7 role-local experience hints, configuration and evidence freshness."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

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
EVIDENCE_KINDS = frozenset({'memory', 'event', 'chat', 'autonomy', 'tool', 'long_task'})
_CORE_KINDS = {
    'L1_user_feedback': frozenset({'chat', 'memory', 'event'}),
    'L2_objective_action': frozenset({'tool', 'long_task', 'autonomy'}),
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
                'role': row.get('role'), 'content': row.get('content') or row.get('text'),
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
            'role': row.get('role'), 'content': row.get('content') or row.get('text'),
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

    def _resolve(self, kind, ref_id, deadline):
        if kind == 'chat':
            return self._validate_chat(ref_id, deadline)
        if kind in ('autonomy', 'tool', 'long_task'):
            if kind == 'autonomy':
                return self._validate_autonomy(ref_id, deadline)
            if kind == 'tool':
                return self._validate_tool(ref_id, deadline)
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


class ExperienceHintProvider:
    """Role-isolated production provider; matching remains deliberately lexical until P7-S2."""

    def __init__(self, cfg, store, *, consolidator, memory=None, profile=None):
        if str(getattr(consolidator.cfg, 'id', '')) != str(cfg.id):
            raise ValueError('experience provider role mismatch')
        self.cfg = cfg
        self.store = store
        self.consolidator = consolidator
        self.validator = EvidenceValidator(cfg, store, memory=memory, profile=profile)
        self.mutex = threading.RLock()

    def settings_path(self):
        return f'{self.cfg.id}/experience-injection.json'

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
        with self.mutex, write_lock(self.store, self.cfg.id):
            raw = load_persisted(self.store, self.cfg.id)
            current = raw.get('_revision')
            current = current if type(current) is int and current >= 1 else 1
            if expected_revision is not None and expected_revision != current:
                return {'ok': False, 'status_code': 409, 'settings_revision': current,
                        'error': '配置已在其他窗口被更新，请刷新后再保存。'}
            revision = current + 1 if updates else current
            self.store.replace(self.settings_path(), {**settings_from(raw), **updates, '_revision': revision})
        return {'ok': True, 'settings': self.settings(), 'settings_revision': revision,
                'updated': updates, 'rejected': rejected, 'persistence_path': self.settings_path()}

    def confirm(self, candidate_ids):
        """Synchronized final veto recheck used immediately before rendering hints.

        Runs in the same mutex/lock boundary as ``decide_experience_candidate()`` so a
        veto is either visible here or is committed after this turn's decision point.
        """
        ids = [str(item) for item in (candidate_ids or ()) if item]
        if not ids:
            return []
        try:
            return self.consolidator.confirm_experience_candidates(ids)
        except Exception:
            return []

    def __call__(self, situation):
        envelope = SituationEnvelope.from_value(situation)
        values = self.settings()
        if not values['enabled'] or envelope.entry_mode in ('glance', 'codex_watch'):
            return []
        if envelope.entry_mode == 'autonomy' and not values['allow_in_autonomy']:
            return []
        tokens = _lexical_tokens(envelope.text)
        if not tokens:
            return []
        evidence_deadline = _Deadline(EVIDENCE_BUDGET_MS)
        pool = self.consolidator.query_experience_candidates(
            situation='', statuses=('confirmed',), limit=200, semantic_pool=True,
            evidence_validator=self.validator, evidence_deadline=evidence_deadline,
        )
        scored = []
        for row in pool:
            candidate_tokens = _lexical_tokens(
                f"{row.get('trigger_condition', '')} {row.get('guidance', '')}"
            )
            overlap = len(tokens & candidate_tokens)
            if overlap:
                scored.append((overlap, str(row.get('updated_at') or ''), row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = [row for _, _, row in scored[:values['max_items']]]
        if not selected:
            return []
        allowed = set(self.confirm([row.get('id') for row in selected]))
        return [row for row in selected if row.get('id') in allowed]
