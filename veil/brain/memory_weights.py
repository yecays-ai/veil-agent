"""P6-S6-1: delivered-memory activation, protective decay and versioned importance re-evaluation.

Three separate things live here, deliberately not mixed:

* Current activation ('last_active' plus delivery/reinforcement counters) is derived only from a
  turn that actually delivered a reply. Memory.confirm_delivery() calls apply_delivery(); a
  cancelled, failed, rolled-back or restarted turn never does. Every delivery is recorded once per
  (request_id, memory_id) in memory_delivery_events, so a duplicate confirmation, a concurrent
  retry or a process restart cannot double-count.
* Protective decay only lowers the decayed exposure score of memories whose importance <= 6 and that
  are not pinned. importance >= 7 and pinned memories never decay. Decay never changes importance,
  never deletes, archives or hides a memory, and last_active remains the factual timestamp of the
  last delivered exposure.
* Long-term importance re-evaluation consumes cross-session repetitions, event links, personal
  meaning and relationship evidence, and writes a versioned proposal. Auto-application is bounded to
  safer increases (never on pinned/locked content, never above reeval_max_auto_importance and never
  by more than one step); decreases and top-band changes stay pending for Lan. Every applied
  proposal writes a normal versions row and can be reverted from the memory UI.

The module is the single interface P6-S3 (cognitive consolidation) uses: propose() accepts an
externally evidenced suggestion without bypassing the same lock/bounds/versioning policy.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from veil.brain.storage import brain_writer

POLICY_VERSION = 's6-1.v1'
PROTECTED_IMPORTANCE = 7
MAX_ACTIVATION_CHARS = 64

# The switches and numbers the Brain memory page edits. Values are validated and persisted in
# data/<role>/memory-weights.json; the effective value is what the next turn uses.
DEFAULTS = {
    # Delivery accounting is the base of the ticket and is on by default. Turning it off stops new
    # writes; existing counters stay readable and are never deleted by the switch.
    'activation_enabled': True,
    # Protective decay of the activity score only; importance/pinned memories are exempt in code.
    'decay_enabled': True,
    'decay_half_life_days': 30,
    'activity_cap': 30.0,
    # The optional ordering signal. P6-S4 AB-R5b did not have enough cold-important samples to
    # justify turning it on, so it stays off until the real-run comparison supports it.
    'ranking_enabled': False,
    'ranking_weight': 0.05,
    'reeval_enabled': True,
    'reeval_auto_apply': True,
    'reeval_cooldown_hours': 24,
    'reeval_rejected_cooldown_hours': 168,
    'reeval_min_confidence': 0.45,
    'reeval_max_auto_importance': 7,
    'reeval_max_step': 1,
    'delivery_event_keep': 20000,
}
_BOOL_KEYS = ('activation_enabled', 'decay_enabled', 'ranking_enabled', 'reeval_enabled',
              'reeval_auto_apply')
_INT_RANGES = {
    'decay_half_life_days': (7, 365),
    'reeval_cooldown_hours': (1, 720),
    'reeval_rejected_cooldown_hours': (1, 8760),
    'reeval_max_auto_importance': (5, 9),
    'reeval_max_step': (1, 2),
    'delivery_event_keep': (1000, 200000),
}
_FLOAT_RANGES = {
    'activity_cap': (5.0, 100.0),
    'ranking_weight': (0.0, 0.2),
    'reeval_min_confidence': (0.2, 0.95),
}
REASON_LABELS = {
    'delivered': '实际送达',
    'cross_day': '跨天被想起',
    'cross_session': '跨会话被想起',
    'event': '关联共同经历',
    'relationship': '关系认知引用',
    'meaning': '写有个人意义',
    'why': '写有为什么记住',
    'bucket': '属于长期记忆层',
    'first_of_kind': '同类首次经历',
    'new': '新近共同经历',
    'external': '认知巩固建议',
}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def parse_time(value):
    """Best-effort aware datetime for stored timestamps; None when it cannot be read."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().strip("'\"")
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    for candidate in (text, text[:19], text[:10]):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _day_of(value):
    parsed = parse_time(value)
    return parsed.strftime('%Y-%m-%d') if parsed else ''


def days_between(later, earlier):
    if later is None or earlier is None:
        return 0.0
    return max(0.0, (later - earlier).total_seconds() / 86400.0)


def is_protected(importance, pinned):
    """importance >= 7 and pinned are never decayed and never auto-changed by this module."""
    try:
        value = int(importance)
    except (TypeError, ValueError):
        value = 5
    return bool(pinned) or value >= PROTECTED_IMPORTANCE


def effective_score(score, updated_at, importance, pinned, *, half_life_days, now=None,
                    decay_enabled=True):
    """The decayed exposure score at now; protected memories keep the stored score."""
    try:
        stored = max(0.0, float(score or 0.0))
    except (TypeError, ValueError):
        stored = 0.0
    if not decay_enabled or is_protected(importance, pinned):
        return stored
    half_life = max(1.0, float(half_life_days or 30))
    stamp = parse_time(updated_at)
    current = now or datetime.now(timezone.utc)
    elapsed = days_between(current, stamp)
    if elapsed <= 0:
        return stored
    return stored * (0.5 ** (elapsed / half_life))


def display_activity(score):
    """0-100 display value for the decayed exposure count (saturating, no fake precision)."""
    try:
        value = max(0.0, float(score or 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return int(round(100.0 * value / (value + 2.0))) if value > 0 else 0


def normalized_score(score):
    try:
        value = max(0.0, float(score or 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return value / (value + 2.0) if value > 0 else 0.0


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json(value, default):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def _load_file_dict(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_persisted(store, role):
    """Read the UI-persisted overrides without a Memory instance (API/read-only paths)."""
    if store is None or not role:
        return {}
    root = getattr(store, 'root', None)
    if not root:
        return {}
    data = _load_file_dict(Path(root) / role / 'memory-weights.json')
    out = {}
    for key in DEFAULTS:
        if key not in data:
            continue
        validated = _validate_value(key, data[key])
        if validated is not None:
            out[key] = validated
    return out


def settings_from(overrides):
    values = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in DEFAULTS:
            validated = _validate_value(key, value)
            if validated is not None:
                values[key] = validated
    return values


def _validate_value(key, value):
    if key in _BOOL_KEYS:
        return value if type(value) is bool else None
    if key in _INT_RANGES:
        if type(value) is bool or not isinstance(value, int):
            return None
        low, high = _INT_RANGES[key]
        return value if low <= value <= high else None
    if key in _FLOAT_RANGES:
        if type(value) is bool or not isinstance(value, (int, float)):
            return None
        low, high = _FLOAT_RANGES[key]
        return float(value) if low <= float(value) <= high else None
    return None


def _clip_text(text, limit=MAX_ACTIVATION_CHARS):
    value = str(text or '').strip()
    return value if len(value) <= limit else value[:limit - 1] + '...'


class MemoryWeights:
    """Delivery accounting + protective decay + proposal policy for one role's brain.db."""

    def __init__(self, cfg, store, *, brain_authority=None, now_fn=None):
        self.cfg = cfg
        self.store = store
        self.brain_authority = brain_authority
        self._now = now_fn or _now
        self.mutex = threading.RLock()
        self._settings = None
        self._settings_signature = None
        self._relationship_cache = (None, None)

    # ------------------------------------------------------------------ settings
    def path(self):
        return '%s/memory-weights.json' % self.cfg.id

    def _settings_file(self):
        return Path(self.store.root) / self.path()

    def _signature(self):
        try:
            stat = self._settings_file().stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def settings(self):
        signature = self._signature()
        with self.mutex:
            if self._settings is not None and signature == self._settings_signature:
                return dict(self._settings)
            values = settings_from(load_persisted(self.store, self.cfg.id))
            self._settings, self._settings_signature = values, signature
            return dict(values)

    def update_settings(self, values):
        """Validate and persist only known keys; return the saved snapshot and what changed."""
        updates = {}
        rejected = {}
        for key, value in (values or {}).items():
            if key not in DEFAULTS:
                rejected[key] = 'unknown'
                continue
            validated = _validate_value(key, value)
            if validated is None:
                rejected[key] = 'invalid'
                continue
            updates[key] = validated
        with self.mutex:
            current = load_persisted(self.store, self.cfg.id)
            merged = {**current, **updates}
            self.store.replace(self.path(), merged)
            self._settings = settings_from(merged)
            self._settings_signature = self._signature()
            return {'ok': True, 'settings': dict(self._settings), 'updated': updates,
                    'rejected': rejected, 'persistence_path': self.path()}

    def retrieval_overrides(self):
        """Keys merged into brain_retrieval settings for the optional activation ordering."""
        values = self.settings()
        return {
            'activation_ranking_enabled': bool(values['ranking_enabled']),
            'activation_weight': float(values['ranking_weight']),
            'activation_half_life_days': int(values['decay_half_life_days']),
            'activation_decay_enabled': bool(values['decay_enabled']),
        }

    def status(self):
        values = self.settings()
        summary = self.summary()
        return {
            'available': True,
            'settings': values,
            'defaults': dict(DEFAULTS),
            'policy_version': POLICY_VERSION,
            'persistence_path': self.path(),
            'effective': {
                'next_turn': True,
                'activation_tracking': bool(values['activation_enabled']),
                'protective_decay': bool(values['decay_enabled']),
                'decay_applies_to': 'importance<=6 且未置顶',
                'never_decays': 'importance>=7 或 pinned',
                'ranking_signal': bool(values['ranking_enabled']),
                'ranking_weight': float(values['ranking_weight']),
                'reevaluation': bool(values['reeval_enabled']),
                'auto_apply': bool(values['reeval_auto_apply']),
                'auto_upper_bound': int(values['reeval_max_auto_importance']),
            },
            'guards': {
                'never_delete_or_hide': True,
                'never_change_importance_of_pinned_or_locked': True,
                'decrease_requires_review': True,
                'one_pending_proposal_per_memory': True,
                'idempotent_delivery_key': 'request_id+memory_id',
            },
            'counters': summary,
        }

    # ------------------------------------------------------------------ storage helpers
    def db_path(self):
        authority = self.brain_authority
        path = getattr(authority, 'db_path', None) if authority is not None else None
        if path:
            return Path(path)
        root = getattr(self.store, 'root', None)
        return Path(root) / self.cfg.id / 'brain.db' if root else None

    def _frozen(self):
        root = getattr(self.store, 'root', None)
        return bool(root) and (Path(root) / self.cfg.id / 'brain-write-freeze').exists()

    @contextmanager
    def _write_connection(self):
        path = self.db_path()
        if path is None or not path.is_file():
            raise FileNotFoundError('brain.db')
        if self._frozen():
            raise RuntimeError('memory writes are frozen for cutover')
        connection = sqlite3.connect(str(path), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            brain_writer.ensure_schema(connection)
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self):
        path = self.db_path()
        if path is None or not path.is_file():
            raise FileNotFoundError('brain.db')
        uri = path.resolve().as_uri() + '?mode=ro'
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _has_table(connection, table):
        try:
            return connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------ delivery accounting
    def apply_delivery(self, event):
        """Record one confirmed delivery exactly once; never raises into the chat path.

        event is the dict Memory.confirm_delivery() already publishes for S6 hooks:
        request_id, injected_ids, opening_ids, association_ids, carried_ids and (new) session_key,
        regenerated.
        """
        values = self.settings()
        summary = {'status': 'skipped', 'request_id': '', 'counted_ids': [], 'proposals': [],
                   'updated_ids': [], 'duplicate_ids': [], 'reason': ''}
        if not values['activation_enabled']:
            summary['reason'] = 'activation_disabled'
            return summary
        request_id = str(event.get('request_id') or '').strip()
        summary['request_id'] = request_id
        ids = []
        for key in list(event.get('injected_ids') or []) + list(event.get('opening_ids') or []):
            if isinstance(key, str) and key and key not in ids:
                ids.append(key)
        if not ids:
            summary['reason'] = 'nothing_delivered'
            return summary
        if not request_id:
            # confirm_delivery() itself is at-most-once per pending entry, so a locally generated
            # key cannot hide a real retry; it only lets legacy/tests without request ids stay safe.
            request_id = 'auto-' + uuid.uuid4().hex
            summary['request_id'] = request_id
        session_key = str(event.get('session_key') or '').strip()
        regenerated = bool(event.get('regenerated'))
        association = {str(k) for k in (event.get('association_ids') or [])}
        carried = {str(k) for k in (event.get('carried_ids') or [])}
        opening = {str(k) for k in (event.get('opening_ids') or [])}
        stamp = self._now()
        day = _day_of(stamp) or stamp[:10]
        try:
            with self._write_connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                counters = {'counted_ids': [], 'duplicate_ids': [], 'updated_ids': []}
                for memory_id in ids[:64]:
                    row = connection.execute(
                        'SELECT id, importance, pinned, bucket, type, is_tombstone, is_deleted '
                        'FROM memories WHERE id=?', (memory_id,)).fetchone()
                    if row is None or row['is_tombstone'] or row['is_deleted']:
                        continue
                    if (row['bucket'] or '') == 'archive' or (row['type'] or '') == 'archived':
                        continue
                    event_key = hashlib.sha256(
                        ('%s|%s' % (request_id, memory_id)).encode('utf-8')).hexdigest()[:32]
                    kind = ('association' if memory_id in association else
                            'carried' if memory_id in carried else
                            'opening' if memory_id in opening else 'recall')
                    counted = 0 if regenerated else 1
                    cursor = connection.execute(
                        'INSERT OR IGNORE INTO memory_delivery_events(event_key,memory_id,request_id,'
                        'session_key,delivered_at,delivered_day,kind,counted,regenerated) '
                        'VALUES(?,?,?,?,?,?,?,?,?)',
                        (event_key, memory_id, request_id, session_key, stamp, day, kind,
                         counted, int(regenerated)))
                    if cursor.rowcount == 0:
                        counters['duplicate_ids'].append(memory_id)
                        continue
                    if not counted:
                        # A regenerated reply is a real exposure but not a new reinforcement: keep
                        # the factual recency timestamp, do not grow the counters or the score.
                        connection.execute('UPDATE memories SET last_active=? WHERE id=?',
                                           (stamp, memory_id))
                        continue
                    activation = connection.execute(
                        'SELECT activity_score, score_updated_at, delivery_count, distinct_days '
                        'FROM memory_activation WHERE memory_id=?', (memory_id,)).fetchone()
                    score = (activation['activity_score'] if activation else 0.0)
                    updated_at = (activation['score_updated_at'] if activation else None) or stamp
                    base = effective_score(
                        score, updated_at, row['importance'], row['pinned'],
                        half_life_days=values['decay_half_life_days'],
                        now=parse_time(stamp), decay_enabled=values['decay_enabled'])
                    weight = 0.5 if kind == 'carried' else 1.0
                    new_score = min(float(values['activity_cap']), base + weight)
                    prior_days = _as_int(activation['distinct_days']) if activation else 0
                    same_day = connection.execute(
                        'SELECT 1 FROM memory_delivery_events WHERE memory_id=? AND delivered_day=? '
                        'AND counted=1 AND event_key<>? LIMIT 1',
                        (memory_id, day, event_key)).fetchone() is not None
                    distinct_days = prior_days + (0 if same_day else 1)
                    connection.execute(
                        'INSERT INTO memory_activation(memory_id,last_active,last_delivery_at,'
                        'first_delivery_at,last_request_id,delivery_count,distinct_days,'
                        'distinct_sessions,activity_score,score_updated_at,updated_at) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET '
                        'last_active=excluded.last_active,last_delivery_at=excluded.last_delivery_at,'
                        'last_request_id=excluded.last_request_id,'
                        'delivery_count=memory_activation.delivery_count+1,'
                        'distinct_days=excluded.distinct_days,'
                        'distinct_sessions=(SELECT COUNT(DISTINCT session_key) FROM '
                        'memory_delivery_events WHERE memory_id=excluded.memory_id AND counted=1 '
                        "AND session_key<>''),"
                        'activity_score=excluded.activity_score,'
                        'score_updated_at=excluded.score_updated_at,updated_at=excluded.updated_at',
                        (memory_id, stamp, stamp, stamp, request_id, 1, distinct_days,
                         1 if session_key else 0, new_score, stamp, stamp))
                    connection.execute('UPDATE memories SET last_active=? WHERE id=?',
                                       (stamp, memory_id))
                    counters['counted_ids'].append(memory_id)
                    counters['updated_ids'].append(memory_id)
                self._prune(connection, int(values['delivery_event_keep']))
                connection.commit()
            summary.update(counters)
            summary['status'] = 'applied' if counters['updated_ids'] else 'duplicate'
            if counters['updated_ids'] and values['reeval_enabled']:
                for memory_id in counters['updated_ids']:
                    try:
                        result = self._reevaluate(memory_id, trigger='delivery', force=False)
                        if result.get('proposal'):
                            summary['proposals'].append(result['proposal'])
                    except Exception as exc:
                        summary.setdefault('errors', []).append(
                            {'memory_id': memory_id, 'error': type(exc).__name__})
            return summary
        except Exception as exc:
            summary['status'] = 'error'
            summary['reason'] = type(exc).__name__
            return summary

    @staticmethod
    def _prune(connection, keep):
        try:
            total = connection.execute('SELECT COUNT(*) FROM memory_delivery_events').fetchone()[0]
        except sqlite3.Error:
            return
        if total <= keep + 200:
            return
        extra = total - keep
        connection.execute(
            'DELETE FROM memory_delivery_events WHERE event_key IN ('
            'SELECT event_key FROM memory_delivery_events ORDER BY delivered_at, rowid LIMIT ?)',
            (extra,))

    # ------------------------------------------------------------------ read helpers
    def activation_map(self, memory_ids, settings=None):
        """Per-memory activation view for the UI/API; projection only, never a write."""
        values = settings_from(settings) if settings is not None else self.settings()
        ids = [str(mid) for mid in (memory_ids or []) if mid]
        if not ids:
            return {}
        now = parse_time(self._now())
        try:
            with self._read_connection() as connection:
                if not self._has_table(connection, 'memory_activation'):
                    return {}
                marks = ','.join('?' * len(ids))
                rows = connection.execute(
                    'SELECT a.*, m.importance, m.pinned, m.last_active AS memory_last_active '
                    'FROM memory_activation a JOIN memories m ON m.id=a.memory_id '
                    'WHERE a.memory_id IN (%s)' % marks, ids).fetchall()
                sessions = {}
                if self._has_table(connection, 'memory_delivery_events'):
                    for row in connection.execute(
                            "SELECT memory_id, COUNT(DISTINCT session_key) AS n "
                            "FROM memory_delivery_events WHERE counted=1 AND session_key<>'' "
                            "AND memory_id IN (%s) GROUP BY memory_id" % marks, ids):
                        sessions[row['memory_id']] = row['n']
        except (sqlite3.Error, FileNotFoundError):
            return {}
        result = {}
        for row in rows:
            score = effective_score(
                row['activity_score'], row['score_updated_at'], row['importance'], row['pinned'],
                half_life_days=values['decay_half_life_days'], now=now,
                decay_enabled=values['decay_enabled'])
            result[row['memory_id']] = {
                # Factual recency (a regenerated reply still moves this); the reinforcement
                # counters and decayed score only move on counted deliveries.
                'last_active': row['memory_last_active'] or row['last_active'] or '',
                'last_delivery_at': row['last_delivery_at'] or '',
                'first_delivery_at': row['first_delivery_at'] or '',
                'deliveries': _as_int(row['delivery_count']),
                'distinct_days': _as_int(row['distinct_days']),
                'distinct_sessions': sessions.get(row['memory_id'], _as_int(row['distinct_sessions'])),
                'score': round(score, 4),
                'display': display_activity(score),
                'stored_score': round(float(row['activity_score'] or 0.0), 4),
                'protected': is_protected(row['importance'], row['pinned']),
                'decayed': (not is_protected(row['importance'], row['pinned'])
                            and bool(values['decay_enabled'])
                            and abs(score - float(row['activity_score'] or 0.0)) > 0.0001),
                'last_request_id': row['last_request_id'] or '',
            }
        return result

    def summary(self):
        try:
            with self._read_connection() as connection:
                tracked = pending = applied = rejected = 0
                if self._has_table(connection, 'memory_activation'):
                    tracked = connection.execute(
                        'SELECT COUNT(*) FROM memory_activation').fetchone()[0]
                if self._has_table(connection, 'memory_importance_proposals'):
                    for row in connection.execute(
                            'SELECT status, COUNT(*) AS n FROM memory_importance_proposals '
                            'GROUP BY status'):
                        if row['status'] == 'pending':
                            pending = row['n']
                        elif row['status'] in ('applied', 'reverted'):
                            applied += row['n']
                        elif row['status'] == 'rejected':
                            rejected = row['n']
                return {'tracked_memories': tracked, 'proposals_pending': pending,
                        'proposals_applied': applied, 'proposals_rejected': rejected}
        except (sqlite3.Error, FileNotFoundError):
            return {'tracked_memories': 0, 'proposals_pending': 0,
                    'proposals_applied': 0, 'proposals_rejected': 0}

    # ------------------------------------------------------------------ relationship evidence
    def _relationship_index(self):
        """Read-only index of learned-context relationship items by memory:/event: evidence refs."""
        profile = getattr(self.cfg, 'profile', None) or {}
        filename = profile.get('file') if isinstance(profile, dict) else None
        filename = filename if isinstance(filename, str) and filename else 'learned-context.json'
        path = Path(self.store.root) / self.cfg.id / filename
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return {}
        with self.mutex:
            cached_sig, cached = self._relationship_cache
            if cached is not None and cached_sig == signature:
                return cached
        data = _load_file_dict(path)
        block = data.get('relationship') if isinstance(data.get('relationship'), dict) else {}
        items = (block or {}).get('items') or []
        index = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get('status') != 'confirmed' or item.get('stale_ref') is True:
                continue
            entry = {'id': str(item.get('id') or ''), 'text': _clip_text(item.get('text')),
                     'updated_at': str(item.get('updated_at') or ''),
                     'locked': bool(item.get('locked'))}
            for source in item.get('sources') or []:
                if not isinstance(source, str):
                    continue
                kind, _, ref = source.partition(':')
                if kind in ('memory', 'event') and ref:
                    index.setdefault('%s:%s' % (kind, ref), []).append(entry)
        with self.mutex:
            self._relationship_cache = (signature, index)
        return index

    # ------------------------------------------------------------------ re-evaluation
    def _plan(self, connection, memory_id):
        """Read the memory's evidence and derive a bounded proposal (no writes)."""
        row = connection.execute(
            'SELECT id,title,body,created,importance,pinned,bucket,type,meaning,why_remembered,'
            'first_of_kind,is_tombstone,is_deleted FROM memories WHERE id=?',
            (memory_id,)).fetchone()
        if row is None or row['is_tombstone'] or row['is_deleted']:
            return None
        locked = False
        try:
            state = connection.execute(
                'SELECT locked FROM sidecar_state WHERE memory_id=?', (memory_id,)).fetchone()
            locked = bool(state and state['locked'])
        except sqlite3.Error:
            locked = False
        activation = None
        if self._has_table(connection, 'memory_activation'):
            activation = connection.execute(
                'SELECT * FROM memory_activation WHERE memory_id=?', (memory_id,)).fetchone()
        sessions = 0
        if self._has_table(connection, 'memory_delivery_events'):
            sessions = connection.execute(
                "SELECT COUNT(DISTINCT session_key) FROM memory_delivery_events "
                "WHERE memory_id=? AND counted=1 AND session_key<>''", (memory_id,)).fetchone()[0]
        event = None
        try:
            event = connection.execute(
                'SELECT event_id,title,first_at,last_at FROM events WHERE memory_id=? '
                'ORDER BY rowid DESC LIMIT 1', (memory_id,)).fetchone()
        except sqlite3.Error:
            event = None
        relationship = []
        relationship_index = self._relationship_index()
        if event is not None:
            relationship = list(relationship_index.get('event:%s' % event['event_id']) or [])
        if not relationship:
            relationship = list(relationship_index.get('memory:%s' % memory_id) or [])
        current = _as_int(row['importance'], 5) or 5
        created = parse_time(row['created'])
        now = parse_time(self._now())
        deliveries = _as_int(activation['delivery_count']) if activation else 0
        distinct_days = _as_int(activation['distinct_days']) if activation else 0
        last_delivery = (activation['last_delivery_at'] if activation else '') or ''
        reasons = []
        evidence = []
        strength = 0.0

        def add(code, weight, detail, ref=None, kind='metric'):
            nonlocal strength
            strength += weight
            reasons.append({'code': code, 'label': REASON_LABELS.get(code, code),
                            'detail': detail, 'weight': round(weight, 3)})
            if ref:
                evidence.append({'kind': kind, 'ref': ref, 'detail': detail})

        if deliveries >= 1:
            add('delivered', min(0.20, 0.05 * deliveries),
                '实际送达 %d 次' % deliveries, ref=last_delivery or None, kind='delivery')
        if distinct_days >= 2:
            add('cross_day', 0.10, '跨 %d 天被想起' % distinct_days)
        if sessions >= 2:
            add('cross_session', 0.08, '跨 %d 个会话被想起' % sessions)
        if event is not None:
            add('event', 0.18, '关联共同经历「%s」' % _clip_text(event['title'] or event['event_id']),
                ref=event['event_id'], kind='event')
        for item in relationship[:3]:
            add('relationship', 0.20 / min(3, len(relationship)),
                '关系认知「%s」引用' % (item.get('text') or item.get('id')),
                ref=item.get('id'), kind='relationship')
        if str(row['meaning'] or '').strip():
            add('meaning', 0.12, '写有个人意义', ref=memory_id, kind='memory')
        if str(row['why_remembered'] or '').strip():
            add('why', 0.06, '写有为什么记住', ref=memory_id, kind='memory')
        if (row['bucket'] or '') in ('permanent', 'letters', 'feel'):
            add('bucket', 0.10, '属于 %s 长期记忆层' % row['bucket'])
        if _as_int(row['first_of_kind']) > 0:
            add('first_of_kind', 0.05, '同类首次经历')
        if created is not None and now is not None and days_between(now, created) <= 14:
            add('new', 0.05, '新近的共同经历')
        confidence = min(0.95, strength)
        kinds = {item['code'] for item in reasons}
        has_experience = ('event' in kinds or 'relationship' in kinds
                          or (deliveries >= 2 and distinct_days >= 2))
        proposed = current
        policy_note = ''
        if current >= 10:
            policy_note = '已是最高重要度'
        elif not has_experience:
            policy_note = '缺少可追溯的共同经历证据'
        elif current <= 5 and strength >= 0.45:
            proposed = current + 1
        elif current == 6 and strength >= 0.60:
            proposed = current + 1
        elif 7 <= current <= 9 and strength >= 0.80 and len(kinds) >= 3:
            proposed = current + 1
            policy_note = '重要记忆，只提建议不自动修改'
        else:
            policy_note = '证据强度不足以调整长期重要度'
        metrics = {
            'importance': current, 'pinned': bool(row['pinned']), 'locked': locked,
            'deliveries': deliveries, 'distinct_days': distinct_days, 'sessions': sessions,
            'last_delivery_at': last_delivery, 'event_id': event['event_id'] if event else '',
            'relationship_ids': [item.get('id') for item in relationship[:3]],
            'meaning': bool(str(row['meaning'] or '').strip()),
            'why': bool(str(row['why_remembered'] or '').strip()),
            'bucket': row['bucket'] or '', 'created': row['created'] or '',
            'strength': round(strength, 3), 'kinds': sorted(kinds),
        }
        return {
            'memory_id': memory_id, 'title': row['title'] or memory_id,
            'current': current, 'proposed': min(10, proposed), 'confidence': round(confidence, 3),
            'reasons': reasons, 'evidence': evidence[:12], 'metrics': metrics,
            'policy_note': policy_note, 'strength': round(strength, 3),
            'pinned': bool(row['pinned']), 'locked': locked,
        }

    def _last_proposal(self, connection, memory_id):
        try:
            return connection.execute(
                'SELECT * FROM memory_importance_proposals WHERE memory_id=? '
                'ORDER BY created_at DESC, rowid DESC LIMIT 1', (memory_id,)).fetchone()
        except sqlite3.Error:
            return None

    def _should_create(self, connection, plan, values, now, force=False):
        last = self._last_proposal(connection, plan['memory_id'])
        if last is None:
            return True, ''
        if last['status'] == 'pending':
            return False, 'pending_exists'
        created = parse_time(last['created_at'])
        if created is not None and now is not None and not force:
            hours = (now - created).total_seconds() / 3600.0
            if last['status'] == 'rejected':
                if hours < values['reeval_rejected_cooldown_hours']:
                    return False, 'rejected_cooldown'
            elif hours < values['reeval_cooldown_hours']:
                return False, 'cooldown'
            if last['status'] in ('applied', 'reverted'):
                previous = _json(last['metrics_json'], {})
                if (_as_int(previous.get('deliveries')) >= plan['metrics']['deliveries']
                        and previous.get('event_id') == plan['metrics']['event_id']
                        and previous.get('relationship_ids') == plan['metrics']['relationship_ids']):
                    return False, 'no_new_evidence'
        return True, ''

    def _insert_proposal(self, connection, plan, *, trigger, confidence, status, note,
                         auto_applied=0):
        proposal_id = uuid.uuid4().hex[:16]
        created = self._now()
        connection.execute(
            'INSERT INTO memory_importance_proposals(proposal_id,memory_id,created_at,status,'
            'current_importance,proposed_importance,delta,confidence,trigger,policy_version,'
            'reasons_json,evidence_json,metrics_json,auto_applied,note) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (proposal_id, plan['memory_id'], created, status, plan['current'], plan['proposed'],
             plan['proposed'] - plan['current'], round(float(confidence), 3), trigger,
             POLICY_VERSION, json.dumps(plan['reasons'], ensure_ascii=False),
             json.dumps(plan['evidence'], ensure_ascii=False),
             json.dumps(plan['metrics'], ensure_ascii=False), auto_applied, note))
        return proposal_id

    def _apply_proposal(self, connection, proposal_id, *, by, note=''):
        proposal = connection.execute(
            'SELECT * FROM memory_importance_proposals WHERE proposal_id=?', (proposal_id,)).fetchone()
        if proposal is None:
            return {'ok': False, 'error': 'proposal_not_found'}
        if proposal['status'] != 'pending':
            return {'ok': False, 'error': 'proposal_not_pending'}
        row = connection.execute('SELECT importance FROM memories WHERE id=? AND is_tombstone=0 '
                                 'AND is_deleted=0', (proposal['memory_id'],)).fetchone()
        if row is None:
            connection.execute("UPDATE memory_importance_proposals SET status='superseded',"
                               'decided_at=?, note=? WHERE proposal_id=?',
                               (self._now(), 'memory_missing', proposal_id))
            return {'ok': False, 'error': 'memory_missing'}
        current = _as_int(row['importance'], 5) or 5
        if current != proposal['current_importance']:
            connection.execute("UPDATE memory_importance_proposals SET status='superseded',"
                               'decided_at=?, note=? WHERE proposal_id=?',
                               (self._now(), 'importance_changed', proposal_id))
            return {'ok': False, 'error': 'importance_changed', 'importance': current}
        target = _as_int(proposal['proposed_importance'], current)
        target = max(1, min(10, target))
        if target == current:
            return {'ok': False, 'error': 'no_change'}
        codes = ','.join(item.get('code', '') for item in _json(proposal['reasons_json'], []))
        brain_writer.apply_update_memory(
            connection, proposal['memory_id'], {'importance': target},
            operation_reason=('importance-reevaluation: ' + codes)[:500], why_remembered='')
        seq = connection.execute(
            'SELECT MAX(seq) FROM versions WHERE memory_id=?',
            (proposal['memory_id'],)).fetchone()[0]
        stamp = self._now()
        connection.execute(
            "UPDATE memory_importance_proposals SET status='applied', auto_applied=?, "
            'applied_at=?, decided_at=?, decided_by=?, version_seq=?, note=? WHERE proposal_id=?',
            (1 if by == 'auto' else 0, stamp, stamp, by, seq, note, proposal_id))
        return {'ok': True, 'status': 'applied', 'memory_id': proposal['memory_id'],
                'importance': target, 'previous': current, 'version_seq': seq,
                'proposal_id': proposal_id, 'auto_applied': by == 'auto'}

    def _reevaluate(self, memory_id, *, trigger='delivery', force=False, proposed=None,
                    confidence=None, reasons=None, evidence=None, auto_apply=None, note=''):
        """Evaluate (or accept an external suggestion for) one memory and persist one proposal."""
        values = self.settings()
        if not values['reeval_enabled']:
            return {'ok': False, 'reason': 'reeval_disabled'}
        with self._write_connection() as connection:
            try:
                connection.execute('BEGIN IMMEDIATE')
                plan = self._plan(connection, memory_id)
                if plan is None:
                    connection.rollback()
                    return {'ok': False, 'reason': 'memory_missing'}
                if proposed is not None:
                    target = max(1, min(10, _as_int(proposed, plan['current'])))
                    plan['proposed'] = target
                    plan['policy_note'] = note or '认知巩固建议'
                    if reasons:
                        plan['reasons'] = list(reasons)[:12]
                    if evidence:
                        plan['evidence'] = list(evidence)[:12]
                    plan['confidence'] = float(confidence if confidence is not None else 0.6)
                    plan['metrics']['external'] = True
                if plan['proposed'] == plan['current']:
                    connection.rollback()
                    return {'ok': False, 'reason': 'no_change',
                            'detail': plan['policy_note']}
                now = parse_time(self._now())
                allow, reason_code = self._should_create(connection, plan, values, now, force=force)
                if not allow:
                    connection.rollback()
                    return {'ok': False, 'reason': reason_code}
                step = int(values['reeval_max_step'])
                if plan['proposed'] > plan['current'] and plan['proposed'] - plan['current'] > step:
                    plan['proposed'] = plan['current'] + step
                safe_auto = (
                    bool(values['reeval_auto_apply'] if auto_apply is None else auto_apply)
                    and plan['proposed'] > plan['current']
                    and plan['proposed'] <= int(values['reeval_max_auto_importance'])
                    and plan['confidence'] >= float(values['reeval_min_confidence'])
                    and not plan['pinned'] and not plan['locked']
                )
                proposal_id = self._insert_proposal(
                    connection, plan, trigger=trigger, confidence=plan['confidence'],
                    status='pending', note=note or plan['policy_note'])
                if safe_auto:
                    applied = self._apply_proposal(connection, proposal_id, by='auto',
                                                   note=plan['policy_note'])
                    connection.commit()
                    return {'ok': True, 'proposal': self._proposal_dict(
                        connection, proposal_id), 'applied': bool(applied.get('ok')),
                        'apply': applied}
                connection.commit()
                return {'ok': True, 'proposal': self._proposal_dict(connection, proposal_id),
                        'applied': False}
            except Exception:
                connection.rollback()
                raise

    def reevaluate(self, memory_id, trigger='manual', *, force=True):
        """Local evidence-only re-evaluation entry (UI button and scheduled/local passes)."""
        try:
            return self._reevaluate(memory_id, trigger=trigger, force=force)
        except Exception as exc:
            return {'ok': False, 'reason': type(exc).__name__}

    def propose(self, memory_id, *, trigger='consolidation', proposed_importance=None,
                confidence=None, reasons=None, evidence=None, auto_apply=False, note=''):
        """Unified S6-3 interface: an externally evidenced importance suggestion.

        The same policy applies as for local evidence: pinned/locked memories and decreases stay
        pending, auto-application is bounded by reeval_max_auto_importance and a single step.
        """
        try:
            return self._reevaluate(
                memory_id, trigger=trigger, force=True, proposed=proposed_importance,
                confidence=confidence, reasons=reasons, evidence=evidence,
                auto_apply=auto_apply, note=note)
        except Exception as exc:
            return {'ok': False, 'reason': type(exc).__name__}

    def _proposal_dict(self, connection, proposal_id):
        row = connection.execute(
            'SELECT * FROM memory_importance_proposals WHERE proposal_id=?',
            (proposal_id,)).fetchone()
        if row is None:
            return {}
        return {
            'proposal_id': row['proposal_id'], 'memory_id': row['memory_id'],
            'created_at': row['created_at'], 'status': row['status'],
            'current_importance': row['current_importance'],
            'proposed_importance': row['proposed_importance'], 'delta': row['delta'],
            'confidence': row['confidence'], 'trigger': row['trigger'],
            'policy_version': row['policy_version'],
            'reasons': _json(row['reasons_json'], []),
            'evidence': _json(row['evidence_json'], []),
            'metrics': _json(row['metrics_json'], {}),
            'auto_applied': bool(row['auto_applied']), 'applied_at': row['applied_at'] or '',
            'version_seq': row['version_seq'], 'decided_at': row['decided_at'] or '',
            'decided_by': row['decided_by'] or '', 'note': row['note'] or '',
        }

    # ------------------------------------------------------------------ proposal reads/decisions
    def proposals(self, memory_id='', status='', limit=50):
        try:
            with self._read_connection() as connection:
                if not self._has_table(connection, 'memory_importance_proposals'):
                    return []
                clauses, args = [], []
                if memory_id:
                    clauses.append('p.memory_id=?')
                    args.append(str(memory_id))
                if status:
                    clauses.append('p.status=?')
                    args.append(str(status))
                where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
                args.append(max(1, min(200, int(limit or 50))))
                rows = connection.execute(
                    'SELECT p.*, m.title, m.importance, m.pinned FROM memory_importance_proposals p '
                    'LEFT JOIN memories m ON m.id=p.memory_id %s '
                    'ORDER BY p.created_at DESC, p.rowid DESC LIMIT ?' % where, args).fetchall()
                result = []
                for row in rows:
                    item = self._proposal_dict(connection, row['proposal_id'])
                    item['title'] = row['title'] or row['memory_id']
                    item['memory_importance'] = row['importance']
                    item['pinned'] = bool(row['pinned'])
                    result.append(item)
                return result
        except (sqlite3.Error, FileNotFoundError):
            return []

    def decide(self, proposal_id, action, note=''):
        """Lan's decision: accept | reject | revert. Never applies to a missing/changed memory."""
        action = str(action or '').strip()
        if action not in ('accept', 'reject', 'revert'):
            return {'ok': False, 'error': 'unknown_action', 'status_code': 400}
        try:
            with self._write_connection() as connection:
                try:
                    connection.execute('BEGIN IMMEDIATE')
                    proposal = connection.execute(
                        'SELECT * FROM memory_importance_proposals WHERE proposal_id=?',
                        (proposal_id,)).fetchone()
                    if proposal is None:
                        connection.rollback()
                        return {'ok': False, 'error': 'proposal_not_found', 'status_code': 404}
                    if action == 'reject':
                        if proposal['status'] != 'pending':
                            connection.rollback()
                            return {'ok': False, 'error': 'proposal_not_pending', 'status_code': 409}
                        connection.execute(
                            "UPDATE memory_importance_proposals SET status='rejected', "
                            'decided_at=?, decided_by=?, note=? WHERE proposal_id=?',
                            (self._now(), 'lan', note or 'Lan 拒绝', proposal_id))
                        connection.commit()
                        return {'ok': True, 'proposal': self._proposal_dict(connection, proposal_id)}
                    if action == 'accept':
                        if proposal['status'] != 'pending':
                            connection.rollback()
                            return {'ok': False, 'error': 'proposal_not_pending', 'status_code': 409}
                        applied = self._apply_proposal(connection, proposal_id, by='lan', note=note)
                        if not applied.get('ok'):
                            connection.rollback()
                            return {'ok': False, 'error': applied.get('error', 'apply_failed'),
                                    'status_code': 409}
                        if note:
                            connection.execute(
                                'UPDATE memory_importance_proposals SET note=? WHERE proposal_id=?',
                                (note, proposal_id))
                        connection.commit()
                        return {'ok': True, 'proposal': self._proposal_dict(connection, proposal_id),
                                'apply': applied}
                    # revert: restore the importance recorded before this proposal was applied.
                    if proposal['status'] not in ('applied',):
                        connection.rollback()
                        return {'ok': False, 'error': 'proposal_not_applied', 'status_code': 409}
                    row = connection.execute(
                        'SELECT importance FROM memories WHERE id=? AND is_tombstone=0 AND is_deleted=0',
                        (proposal['memory_id'],)).fetchone()
                    if row is None:
                        connection.rollback()
                        return {'ok': False, 'error': 'memory_missing', 'status_code': 409}
                    current = _as_int(row['importance'], 5) or 5
                    if current != _as_int(proposal['proposed_importance'], current):
                        connection.rollback()
                        return {'ok': False, 'error': 'importance_changed_since_apply',
                                'status_code': 409, 'memory_importance': current}
                    target = max(1, min(10, _as_int(proposal['current_importance'], current)))
                    brain_writer.apply_update_memory(
                        connection, proposal['memory_id'], {'importance': target},
                        operation_reason='importance-reevaluation-revert: %s' % proposal_id,
                        why_remembered='')
                    seq = connection.execute(
                        'SELECT MAX(seq) FROM versions WHERE memory_id=?',
                        (proposal['memory_id'],)).fetchone()[0]
                    connection.execute(
                        "UPDATE memory_importance_proposals SET status='reverted', decided_at=?, "
                        'decided_by=?, note=?, version_seq=? WHERE proposal_id=?',
                        (self._now(), 'lan', note or 'Lan 撤回', seq, proposal_id))
                    connection.commit()
                    return {'ok': True, 'proposal': self._proposal_dict(connection, proposal_id),
                            'importance': target, 'version_seq': seq}
                except Exception:
                    connection.rollback()
                    raise
        except Exception as exc:
            return {'ok': False, 'error': type(exc).__name__, 'status_code': 500}

    def history(self, memory_id, limit=30):
        """Applied/rejected/reverted re-evaluation history for one memory (UI)."""
        return self.proposals(memory_id=memory_id, status='', limit=limit)
