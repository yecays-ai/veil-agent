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


class _Deadline:
    def __init__(self, milliseconds=150):
        self.end = time.monotonic() + max(1, int(milliseconds)) / 1000.0

    def expired(self):
        return time.monotonic() >= self.end

    def remaining(self):
        return max(0.0, self.end - time.monotonic())


class EvidenceValidator:
    """Validate role-local evidence under one wall-clock budget and fail closed."""

    def __init__(self, cfg, store, *, memory=None, profile=None):
        self.role = str(cfg.id)
        self.store = store
        self.memory = memory
        self.profile = profile
        self._lock = threading.RLock()
        self._cache = {}
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
        path = self._db_path()
        if not path.is_file() or deadline.expired():
            return None
        try:
            timeout = max(0.01, min(0.15, deadline.remaining()))
            conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=timeout)
            try:
                row = conn.execute("SELECT value FROM meta WHERE key='digest'").fetchone()
            finally:
                conn.close()
            return ('digest', row[0]) if row and row[0] else self._file_fingerprint(path)
        except sqlite3.Error:
            return None

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
        with self._lock:
            self._source_cache = {key: rows}
        return rows, fingerprint

    @staticmethod
    def _row_matches(row, ref_id, keys):
        if row.get('deleted') or row.get('withdrawn') or row.get('retracted'):
            return False
        return any(str(row.get(key) or '') == ref_id for key in keys)

    def _validate_db(self, kind, ref_id, deadline):
        fingerprint = self._db_fingerprint(deadline)
        if fingerprint is None:
            return 'unavailable'
        cache_key = (kind, ref_id, fingerprint)
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        try:
            timeout = max(0.01, min(0.15, deadline.remaining()))
            conn = sqlite3.connect(self._db_path().resolve().as_uri() + '?mode=ro', uri=True, timeout=timeout)
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if kind == 'memory':
                    pending = (" AND NOT EXISTS(SELECT 1 FROM delete_requests WHERE memory_id=? AND status='pending')"
                               if 'delete_requests' in tables else '')
                    params = (ref_id, ref_id) if pending else (ref_id,)
                    row = conn.execute(
                        "SELECT bucket,type FROM memories WHERE id=? AND is_tombstone=0 AND is_deleted=0" + pending,
                        params,
                    ).fetchone()
                    valid = bool(row and str(row[0] or '') != 'archive' and str(row[1] or '') != 'archived')
                elif 'events' not in tables:
                    valid = False
                else:
                    row = conn.execute('SELECT memory_id FROM events WHERE event_id=?', (ref_id,)).fetchone()
                    valid = row is not None
                    if valid and row[0]:
                        pending = (" AND NOT EXISTS(SELECT 1 FROM delete_requests WHERE memory_id=? AND status='pending')"
                                   if 'delete_requests' in tables else '')
                        params = (row[0], row[0]) if pending else (row[0],)
                        mem = conn.execute(
                            "SELECT bucket,type FROM memories WHERE id=? AND is_tombstone=0 AND is_deleted=0" + pending,
                            params,
                        ).fetchone()
                        valid = bool(mem and str(mem[0] or '') != 'archive' and str(mem[1] or '') != 'archived')
            finally:
                conn.close()
            result = 'valid' if valid else 'invalid'
        except sqlite3.Error:
            result = 'unavailable'
        with self._lock:
            self._cache[cache_key] = result
        return result

    def _validate_chat(self, ref_id, deadline):
        lookup = getattr(self.profile, '_chat_lookup', None)
        if callable(lookup):
            _row, status = lookup(ref_id, deadline)
            return {'ok': 'valid', 'missing': 'invalid'}.get(status, 'unavailable')
        rows, _fingerprint = self._jsonl_rows('chat.jsonl', deadline)
        if rows is None:
            return 'unavailable'
        matches = [row for row in rows if self._row_matches(row, ref_id, ('message_id', 'id'))]
        return 'valid' if len(matches) == 1 else 'invalid'

    def _validate_log(self, kind, ref_id, deadline):
        if kind == 'autonomy':
            rows, _fingerprint = self._jsonl_rows('autonomy.jsonl', deadline)
            if rows is None:
                return 'unavailable'
            for row in rows:
                audit = row.get('output_audit') if isinstance(row.get('output_audit'), dict) else {}
                issues = audit.get('issues')
                if (str(row.get('started_at') or '') == ref_id and audit.get('clean') is True
                        and (issues is None or issues == [])):
                    return 'valid'
            return 'invalid'
        if kind == 'tool':
            rows, _fingerprint = self._jsonl_rows('tools.jsonl', deadline)
            if rows is None:
                return 'unavailable'
            return ('valid' if any(self._row_matches(row, ref_id, ('call_id', 'request_id', 'result_id', 'id'))
                                   for row in rows) else 'invalid')
        try:
            row = json.loads((self.role_dir / 'long-task.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return 'invalid'
        except (OSError, ValueError, UnicodeError):
            return 'unavailable'
        return 'valid' if isinstance(row, dict) and str(row.get('task_id') or '') == ref_id else 'invalid'

    def validate(self, candidate, *, milliseconds=150, deadline=None):
        refs = [ref for ref in (candidate.get('evidence_refs') or []) if isinstance(ref, str) and ':' in ref]
        if not refs:
            return 'invalid'
        deadline = deadline or _Deadline(milliseconds)
        results = []
        for ref in refs:
            if deadline.expired():
                results.append('unavailable')
                continue
            kind, ref_id = ref.split(':', 1)
            if not ref_id:
                results.append('invalid')
            elif kind in ('memory', 'event'):
                results.append(self._validate_db(kind, ref_id, deadline))
            elif kind == 'chat':
                results.append(self._validate_chat(ref_id, deadline))
            elif kind in ('autonomy', 'tool', 'long_task'):
                results.append(self._validate_log(kind, ref_id, deadline))
            else:
                results.append('unavailable')
        if all(result == 'valid' for result in results):
            return 'valid'
        if any(result == 'valid' for result in results):
            return 'partial_stale'
        return 'unavailable' if any(result == 'unavailable' for result in results) else 'invalid'

    def validate_many(self, candidates, *, milliseconds=150, deadline=None):
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
        evidence_deadline = _Deadline(150)
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
        selected_ids = [row.get('id') for _, _, row in scored[:values['max_items']]]
        if not selected_ids:
            return []
        # Re-read under the consolidator lock boundary so a just-committed veto cannot leak.
        fresh = self.consolidator.query_experience_candidates(
            situation='', statuses=('confirmed',), limit=200, semantic_pool=True,
            evidence_validator=self.validator, evidence_deadline=evidence_deadline,
        )
        by_id = {row.get('id'): row for row in fresh}
        return [by_id[candidate_id] for candidate_id in selected_ids if candidate_id in by_id]
