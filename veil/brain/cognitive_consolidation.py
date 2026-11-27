"""P6-S6-3: Background cognitive consolidation and experience distillation.

Periodically synthesizes cognitively valuable new experiences (shared chat memories, events,
and audited autonomous activities) together with relevant past anchors and current Learned
Context, producing:
  1. New or revised cognition submitted strictly through S3 LearnedContext.submit_candidate()
     (enforcing 4-section budgets, evidence provenance, semantic verification queueing,
     version history, and Lan lock/correction/veto sovereignty — never a parallel store).
  2. Evidence-backed importance re-evaluation suggestions submitted through S6-1
     MemoryWeights.propose() (enforcing lock/pin protection, version history, and bounds).
  3. Structured, versioned situational experience candidates reserved for P7's experience-driven
     behavior layer (status machine: uncertain/confirmed/outdated/vetoed, never injected into HEAD).

Default disabled (`enabled=False`) for safe gray-scale rollout, with independent scheduling,
daily cost cap in the shared Budget ledger, crash recovery, idempotency, and redacted run logs.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openai import APITimeoutError

from veil import prompts
from veil.brain.learned_context import (
    SOURCE_REF,
    _jaccard_similar,
    _lexical_tokens,
    _matches_governed_claim,
    _norm_text,
)
from veil.brain.relationship_semantic import _contains_secret
from veil.core.agent_types import BudgetExceeded, UserError

COST_CATEGORY = 'cognitive_consolidation'
COST_ACCOUNTING = 'cognitive_consolidation'
POLICY_VERSION = 's6-3.v1'
EXPERIENCE_SCHEMA_VERSION = 'p7.experience.v1'
PROMPT_ID = 'memory.consolidation.prompt'

TICK_SECONDS = 300
MAX_INPUT_TOKENS = 16_000
MAX_OUTPUT_TOKENS = 1_400
MAX_RESPONSE_CHARS = 32_000
MAX_PROCESSED_MEMORIES = 4000
MAX_PROCESSED_AUTONOMY = 2000
MAX_PROCESSED_BATCHES = 500
MAX_EXPERIENCE_CANDIDATES = 200
MAX_IMPORTANCE_LEDGER = 2000
MAX_COGNITION_UPDATES = 4
MAX_IMPORTANCE_PROPOSALS = 4
MAX_EXPERIENCE_PER_RUN = 4

_CODE_FENCE = chr(96) * 3
_VALID_SECTIONS = frozenset({'relationship', 'growth', 'facts'})
_VALID_EXP_KINDS = frozenset({
    'behavioral_pattern', 'tool_lesson', 'communication_pattern', 'autonomy_lesson',
})
_VALID_EXP_STATUSES = frozenset({'uncertain', 'confirmed', 'outdated', 'vetoed'})

DEFAULTS = {
    # Default off for gray-scale rollout per PR #455 AB-E1 decision table.
    'enabled': False,
    'interval_hours': 24,
    'max_runs_per_day': 2,
    'daily_budget_cny': 0.05,
    'timeout_seconds': 25,
    'max_new_memories': 10,
    'max_anchor_memories': 6,
    'max_autonomy_items': 6,
    'min_new_items': 2,
    'auto_apply_importance': False,
}

_BOOL_KEYS = ('enabled', 'auto_apply_importance')
_INT_RANGES = {
    'interval_hours': (1, 168),
    'max_runs_per_day': (1, 12),
    'timeout_seconds': (5, 120),
    'max_new_memories': (2, 24),
    'max_anchor_memories': (0, 16),
    'max_autonomy_items': (0, 16),
    'min_new_items': (1, 10),
}
_FLOAT_RANGES = {
    'daily_budget_cny': (0.0, 5.0),
}


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    for candidate in (text, text[:19], text[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clip(text, limit=240):
    value = ' '.join(str(text or '').split()).strip()
    return value if len(value) <= limit else value[:max(1, limit - 1)] + '…'


def _sha(payload):
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('ascii')).hexdigest()


def _validate_value(key, value):
    if key in _BOOL_KEYS:
        return value if type(value) is bool else None
    if key in _INT_RANGES:
        if type(value) is bool or not isinstance(value, int):
            return None
        low, high = _INT_RANGES[key]
        return value if low <= value <= high else None
    if key in _FLOAT_RANGES:
        if type(value) is bool:
            return None
        try:
            dec = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, AttributeError):
            return None
        if not dec.is_finite():
            return None
        low, high = _FLOAT_RANGES[key]
        if dec < Decimal(str(low)) or dec > Decimal(str(high)):
            return None
        return float(dec)
    return None


def settings_from(overrides):
    values = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in DEFAULTS:
            validated = _validate_value(key, value)
            if validated is not None:
                values[key] = validated
    return values


def _load_json_dict(path):
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_persisted(store, role):
    if store is None or not role:
        return {}
    root = getattr(store, 'root', None)
    if not root:
        return {}
    data = _load_json_dict(Path(root) / role / 'cognitive-consolidation.json')
    out = {}
    for key in DEFAULTS:
        if key not in data:
            continue
        validated = _validate_value(key, data[key])
        if validated is not None:
            out[key] = validated
    return out


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate JSON key')
        obj[key] = value
    return obj


def parse_consolidation_output(raw_text):
    """Strict parser for the consolidation analyzer's JSON output."""
    if not isinstance(raw_text, str) or not raw_text.strip() or len(raw_text) > MAX_RESPONSE_CHARS:
        raise ValueError('invalid_response_size')
    text = raw_text.strip()
    if text.startswith(_CODE_FENCE) and text.endswith(_CODE_FENCE):
        newline = text.find('\n')
        inner = text[newline + 1:-3].strip() if newline != -1 else ''
        if inner:
            text = inner
    parsed = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(parsed, dict):
        raise ValueError('response_not_object')
    allowed = {'cognition_updates', 'importance_proposals', 'experience_candidates'}
    if not set(parsed.keys()).issubset(allowed) or not parsed.keys():
        raise ValueError('unexpected_top_keys')

    updates = []
    raw_updates = parsed.get('cognition_updates', [])
    if not isinstance(raw_updates, list):
        raise ValueError('invalid_cognition_updates')
    for row in raw_updates[:MAX_COGNITION_UPDATES]:
        if not isinstance(row, dict):
            continue
        kind = str(row.get('kind') or 'new').strip()
        section = str(row.get('section') or '').strip()
        body = str(row.get('text') or '').strip()
        reason = str(row.get('reason') or '').strip()
        refs = row.get('evidence_refs')
        if kind not in ('new', 'revision') or section not in _VALID_SECTIONS or not body or not reason:
            continue
        if not isinstance(refs, list) or not all(isinstance(r, str) and r.strip() for r in refs):
            continue
        claim_status = str(row.get('claim_status') or 'uncertain').strip()
        if claim_status not in ('evidence_backed', 'uncertain'):
            claim_status = 'uncertain'
        replace_old = str(row.get('replace_old_text') or '').strip()
        updates.append({
            'kind': kind,
            'section': section,
            'text': body[:600],
            'replace_old_text': replace_old[:600] if kind == 'revision' else '',
            'reason': reason[:400],
            'evidence_refs': [r.strip() for r in refs[:12]],
            'claim_status': claim_status,
        })

    proposals = []
    raw_props = parsed.get('importance_proposals', [])
    if not isinstance(raw_props, list):
        raise ValueError('invalid_importance_proposals')
    for row in raw_props[:MAX_IMPORTANCE_PROPOSALS]:
        if not isinstance(row, dict):
            continue
        mid = str(row.get('memory_id') or '').strip()
        prop_imp = row.get('proposed_importance')
        reason = str(row.get('reason') or '').strip()
        refs = row.get('evidence_refs')
        if not mid or type(prop_imp) is bool or not isinstance(prop_imp, int) or not (1 <= prop_imp <= 10):
            continue
        if not reason or not isinstance(refs, list) or not all(isinstance(r, str) and r.strip() for r in refs):
            continue
        try:
            conf = float(row.get('confidence', 0.65))
        except (TypeError, ValueError):
            conf = 0.65
        conf = max(0.1, min(0.95, conf))
        proposals.append({
            'memory_id': mid,
            'proposed_importance': prop_imp,
            'confidence': round(conf, 3),
            'reason': reason[:400],
            'evidence_refs': [r.strip() for r in refs[:12]],
        })

    experiences = []
    raw_exps = parsed.get('experience_candidates', [])
    if not isinstance(raw_exps, list):
        raise ValueError('invalid_experience_candidates')
    for row in raw_exps[:MAX_EXPERIENCE_PER_RUN]:
        if not isinstance(row, dict):
            continue
        kind = str(row.get('kind') or '').strip()
        trigger_cond = str(row.get('trigger_condition') or '').strip()
        guidance = str(row.get('guidance') or '').strip()
        reason = str(row.get('reason') or '').strip()
        refs = row.get('evidence_refs')
        if kind not in _VALID_EXP_KINDS or not trigger_cond or not guidance or not reason:
            continue
        if not isinstance(refs, list) or not all(isinstance(r, str) and r.strip() for r in refs):
            continue
        supersedes = str(row.get('supersedes_id') or '').strip()
        experiences.append({
            'kind': kind,
            'trigger_condition': trigger_cond[:240],
            'guidance': guidance[:400],
            'reason': reason[:360],
            'evidence_refs': [r.strip() for r in refs[:12]],
            'supersedes_id': supersedes[:64],
        })

    return {
        'cognition_updates': updates,
        'importance_proposals': proposals,
        'experience_candidates': experiences,
    }


def _empty_state():
    return {
        'version': 1,
        'processed_memories': {},
        'processed_autonomy': {},
        'processed_batches': {},
        'importance_proposed': {},
        'in_flight': None,
        'last_run_at': '',
        'last_success_at': '',
        'last_run_id': '',
        'last_status': 'never',
        'last_reason': '',
        'day': '',
        'runs_today': 0,
        'consecutive_failures': 0,
        'cooldown_until': '',
        'totals': {
            'runs': 0,
            'completed': 0,
            'skipped': 0,
            'failed': 0,
            'budget_exhausted': 0,
            'cognition_written': 0,
            'cognition_revised': 0,
            'cognition_blocked': 0,
            'semantic_queued': 0,
            'importance_proposed': 0,
            'importance_applied': 0,
            'experience_created': 0,
            'experience_revised': 0,
        },
        'experience_candidates': [],
    }


class CognitiveConsolidator:
    """Role-scoped background cognitive consolidation with S3/S6-1/S6-2 collaboration and P7 contract."""

    def __init__(self, cfg, store, budget, model, *, memory=None, profile=None, now_fn=None):
        self.cfg = cfg
        self.store = store
        self.budget = budget
        self.model = model
        self.memory = memory
        self.profile = profile
        self._now = now_fn or _now
        self.mutex = threading.RLock()
        self._run_lock = threading.Lock()
        self._settings = None
        self._settings_sig = None
        self._scheduler = None
        if getattr(self.budget, 'reconcile_unsettled', None):
            try:
                self.budget.reconcile_unsettled({COST_ACCOUNTING})
            except Exception:
                pass
        self.recover_interrupted()

    # ------------------------------------------------------------------ paths & settings
    def settings_path(self):
        return f'{self.cfg.id}/cognitive-consolidation.json'

    def state_path(self):
        return f'{self.cfg.id}/cognitive-consolidation-state.json'

    def runs_path(self):
        return f'{self.cfg.id}/cognitive-consolidation-runs.jsonl'

    def _settings_file(self):
        return Path(self.store.root) / self.settings_path()

    def _state_file(self):
        return Path(self.store.root) / self.state_path()

    def _runs_file(self):
        return Path(self.store.root) / self.runs_path()

    def _db_path(self):
        auth = getattr(self.memory, 'brain_authority', None) if self.memory is not None else None
        path = getattr(auth, 'db_path', None) if auth is not None else None
        if path:
            return Path(path)
        root = getattr(self.store, 'root', None)
        return Path(root) / self.cfg.id / 'brain.db' if root else None

    def _weights(self):
        live = getattr(self.memory, 'weights', None) if self.memory is not None else None
        if live is not None:
            return live
        if self.store is None:
            return None
        try:
            from veil.brain.memory_weights import MemoryWeights
            return MemoryWeights(
                self.cfg, self.store,
                brain_authority=getattr(self.memory, 'brain_authority', None),
                now_fn=self._now,
            )
        except Exception:
            return None

    def _sig(self):
        try:
            st = self._settings_file().stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def settings(self):
        sig = self._sig()
        with self.mutex:
            if self._settings is not None and sig == self._settings_sig:
                return dict(self._settings)
            values = settings_from(load_persisted(self.store, self.cfg.id))
            self._settings, self._settings_sig = values, sig
            return dict(values)

    def update_settings(self, values):
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
            self.store.replace(self.settings_path(), merged)
            self._settings = settings_from(merged)
            self._settings_sig = self._sig()
            return {
                'ok': True,
                'settings': dict(self._settings),
                'updated': updates,
                'rejected': rejected,
                'persistence_path': self.settings_path(),
            }

    # ------------------------------------------------------------------ durable state
    def _load_state(self):
        raw = _load_json_dict(self._state_file())
        state = _empty_state()
        for key in state:
            if key not in raw:
                continue
            if key == 'totals' and isinstance(raw['totals'], dict):
                merged = dict(state['totals'])
                for tk in merged:
                    val = raw['totals'].get(tk)
                    if type(val) is int and val >= 0:
                        merged[tk] = val
                state['totals'] = merged
            elif isinstance(state[key], dict) and isinstance(raw[key], dict):
                state[key] = dict(raw[key])
            elif isinstance(state[key], list) and isinstance(raw[key], list):
                state[key] = list(raw[key])
            elif state[key] is None and (raw[key] is None or isinstance(raw[key], dict)):
                state[key] = dict(raw[key]) if isinstance(raw[key], dict) else None
            elif type(raw[key]) is type(state[key]):
                state[key] = raw[key]
        return state

    def _save_state(self, state):
        # Bound maps so state file never grows without limit across months of operation.
        for key, cap in (
            ('processed_memories', MAX_PROCESSED_MEMORIES),
            ('processed_autonomy', MAX_PROCESSED_AUTONOMY),
            ('processed_batches', MAX_PROCESSED_BATCHES),
            ('importance_proposed', MAX_IMPORTANCE_LEDGER),
        ):
            mapping = state.get(key)
            if isinstance(mapping, dict) and len(mapping) > cap:
                keys = list(mapping.keys())[-cap:]
                state[key] = {k: mapping[k] for k in keys}
        exps = state.get('experience_candidates')
        if isinstance(exps, list) and len(exps) > MAX_EXPERIENCE_CANDIDATES:
            confirmed = [e for e in exps if isinstance(e, dict) and e.get('status') == 'confirmed']
            others = [e for e in exps if isinstance(e, dict) and e.get('status') != 'confirmed']
            keep_other = max(0, MAX_EXPERIENCE_CANDIDATES - len(confirmed))
            state['experience_candidates'] = (confirmed + others[-keep_other:])[-MAX_EXPERIENCE_CANDIDATES:]
        self.store.replace(self.state_path(), state)

    def _append_run(self, record):
        self.store.append(self.runs_path(), record)

    def recent_runs(self, limit=20):
        try:
            rows = self.store.read_tail(self.runs_path(), max(1, min(100, int(limit or 20))))
        except Exception:
            return []
        return list(reversed([r for r in rows if isinstance(r, dict)]))

    def recover_interrupted(self):
        """Recover cleanly from an interrupted in-flight run after crash/restart."""
        with self.mutex:
            state = self._load_state()
            in_flight = state.get('in_flight')
            if not isinstance(in_flight, dict):
                return False
            if getattr(self.budget, 'reconcile_unsettled', None):
                try:
                    self.budget.reconcile_unsettled({COST_ACCOUNTING})
                except Exception:
                    pass
            stamp = self._now()
            failures = int(state.get('consecutive_failures') or 0) + 1
            state['in_flight'] = None
            state['last_run_at'] = stamp
            state['last_status'] = 'interrupted'
            state['last_reason'] = 'recovered_after_restart'
            state['consecutive_failures'] = failures
            backoff_min = min(120, 15 * (2 ** min(failures - 1, 3)))
            now_dt = _parse_dt(stamp)
            if now_dt is not None:
                state['cooldown_until'] = (now_dt + timedelta(minutes=backoff_min)).isoformat(timespec='seconds')
            state['totals']['runs'] += 1
            state['totals']['failed'] += 1
            self._save_state(state)
            self._append_run({
                'run_id': str(in_flight.get('run_id') or uuid.uuid4().hex[:12]),
                'started_at': str(in_flight.get('started_at') or stamp),
                'finished_at': stamp,
                'trigger': str(in_flight.get('trigger') or 'scheduled'),
                'status': 'interrupted',
                'reason': 'recovered_after_restart',
                'batch_key': str(in_flight.get('batch_key') or ''),
                'policy_version': POLICY_VERSION,
                'inputs': in_flight.get('inputs') or {},
                'results': {},
                'cost_cny': 0.0,
            })
            return True

    # ------------------------------------------------------------------ scheduler lifecycle
    def start(self):
        from veil.brain.short_term_notes import PeriodicBackgroundTick
        with self.mutex:
            if self._scheduler is None:
                self._scheduler = PeriodicBackgroundTick(
                    f'{self.cfg.id}-cognitive-consolidation', TICK_SECONDS, self.tick,
                )
            self._scheduler.start()

    def stop(self):
        with self.mutex:
            sched = self._scheduler
        if sched is not None:
            sched.stop()

    def tick(self, stopped=lambda: False):
        """Scheduled background entry; never raises and never blocks chat."""
        if stopped():
            return None
        try:
            return self.run_once(trigger='scheduled', force=False, stopped=stopped)
        except Exception:
            return None

    # ------------------------------------------------------------------ budget helpers
    def _budget_snapshot(self, values=None):
        vals = values if values is not None else self.settings()
        cap = Decimal(str(vals['daily_budget_cny']))
        used = Decimal('0')
        pending = Decimal('0')
        stamp = self._now()
        if self.budget is not None:
            try:
                stamp = self.budget.clock()
                used, pending = self.budget.category_totals(
                    self.cfg.id, stamp=stamp, category=COST_CATEGORY,
                )
            except Exception:
                pass
        remaining = max(Decimal('0'), cap - used - pending)
        return {
            'day': stamp[:10],
            'daily_cap_cny': str(cap),
            'spent_cny': str(used),
            'pending_cny': str(pending),
            'remaining_cny': str(remaining),
        }

    # ------------------------------------------------------------------ step 1: incremental batch selection
    def _read_db_memories(self):
        db = self._db_path()
        if db is None or not db.is_file():
            return [], {}, {}, {}
        uri = db.resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'memories' not in tables:
                return [], {}, {}, {}
            mem_cols = (
                'm.id', 'm.title', 'm.body', 'm.body_sha256', 'm.created', 'm.last_active',
                'm.type', 'm.bucket', 'm.importance', 'm.pinned', 'm.meaning', 'm.why_remembered',
                's.locked', 's.links_json',
            )
            mem_sql = (
                'SELECT ' + ', '.join(mem_cols)
                + ' FROM memories m LEFT JOIN sidecar_state s ON s.memory_id=m.id'
                + ' WHERE m.is_tombstone=0 AND m.is_deleted=0'
                + " AND COALESCE(m.bucket,'')!='archive' AND COALESCE(m.type,'')!='archived'"
                + ' ORDER BY m.created DESC, m.id DESC'
            )
            rows = conn.execute(mem_sql).fetchall()

            events_by_mem = {}
            stage_counts = {}
            if 'events' in tables:
                ev_cols = {r[1] for r in conn.execute('PRAGMA table_info(events)')}
                select_cols = ['event_id', 'memory_id']
                for opt in ('title', 'story', 'first_at', 'last_at', 'updated_at'):
                    select_cols.append(opt if opt in ev_cols else f"'' AS {opt}")
                for er in conn.execute(
                    f"SELECT {', '.join(select_cols)} FROM events WHERE memory_id IS NOT NULL"
                ):
                    events_by_mem[er['memory_id']] = dict(er)
            if 'event_stages' in tables:
                for sr in conn.execute('SELECT event_id, COUNT(*) AS cnt FROM event_stages GROUP BY event_id'):
                    stage_counts[sr['event_id']] = int(sr['cnt'] or 0)

            activation_by_mem = {}
            if 'memory_activation' in tables:
                for ar in conn.execute(
                    'SELECT memory_id, delivery_count, distinct_days, last_delivery_at '
                    'FROM memory_activation'
                ):
                    activation_by_mem[ar['memory_id']] = dict(ar)

            # Collect verified user chat references attached to events/memories so S3 can verify them.
            user_chat_refs = {}
            if 'event_ledger' in tables:
                for lr in conn.execute(
                    "SELECT event_id, memory_id, payload_json FROM event_ledger "
                    "WHERE kind='stage-evidence' ORDER BY ledger_seq DESC LIMIT 1000"
                ):
                    try:
                        payload = json.loads(lr['payload_json'])
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get('kind') == 'chat' and payload.get('role') == 'user':
                        cid = payload.get('message_id') or payload.get('id')
                        if isinstance(cid, str) and SOURCE_REF.fullmatch(f'chat:{cid}'):
                            ref = f'chat:{cid}'
                            if lr['memory_id']:
                                user_chat_refs.setdefault(lr['memory_id'], set()).add(ref)
                            if lr['event_id']:
                                user_chat_refs.setdefault('ev:' + lr['event_id'], set()).add(ref)
            if 'sources' in tables:
                for sr in conn.execute(
                    "SELECT memory_id, kind, ref FROM sources WHERE kind='chat' LIMIT 2000"
                ):
                    raw_ref = str(sr['ref'] or '').strip()
                    ref = raw_ref if raw_ref.startswith('chat:') else (f'chat:{raw_ref}' if raw_ref else '')
                    if ref and SOURCE_REF.fullmatch(ref):
                        user_chat_refs.setdefault(sr['memory_id'], set()).add(ref)

            relations_by_mem = {}
            if 'relations' in tables:
                for rr in conn.execute('SELECT memory_id, target_id FROM relations LIMIT 2000'):
                    relations_by_mem.setdefault(rr['memory_id'], set()).add(rr['target_id'])
                    relations_by_mem.setdefault(rr['target_id'], set()).add(rr['memory_id'])

            memories = []
            for row in rows:
                mid = row['id']
                ev = events_by_mem.get(mid)
                ev_id = ev['event_id'] if ev else ''
                sc = stage_counts.get(ev_id, 0) if ev_id else 0
                act = activation_by_mem.get(mid) or {}
                chat_refs = set(user_chat_refs.get(mid) or ())
                if ev_id:
                    chat_refs.update(user_chat_refs.get('ev:' + ev_id) or ())
                fp = _sha([
                    mid,
                    row['body_sha256'] or hashlib.sha256((row['body'] or '').encode('utf-8')).hexdigest()[:16],
                    int(row['importance'] or 5),
                    int(bool(row['pinned'])),
                    ev_id,
                    sc,
                    (ev.get('updated_at') or '') if ev else '',
                ])[:16]
                memories.append({
                    'id': mid,
                    'title': str(row['title'] or mid),
                    'body': str(row['body'] or ''),
                    'created': str(row['created'] or ''),
                    'last_active': str(row['last_active'] or ''),
                    'type': str(row['type'] or ''),
                    'bucket': str(row['bucket'] or ''),
                    'importance': int(row['importance'] or 5),
                    'pinned': bool(row['pinned']),
                    'locked': bool(row['locked']),
                    'meaning': str(row['meaning'] or ''),
                    'why_remembered': str(row['why_remembered'] or ''),
                    'event_id': ev_id,
                    'event_title': str(ev.get('title') or '') if ev else '',
                    'event_story': str(ev.get('story') or '') if ev else '',
                    'stage_count': sc,
                    'deliveries': int(act.get('delivery_count') or 0),
                    'distinct_days': int(act.get('distinct_days') or 0),
                    'chat_refs': sorted(chat_refs)[:6],
                    'fingerprint': fp,
                })
            return memories, events_by_mem, relations_by_mem, activation_by_mem
        finally:
            conn.close()

    def _read_autonomy_experiences(self):
        """Read clean, audited autonomous wakes (excludes S6-2 flagged narration/roleplay)."""
        try:
            rows = self.store.read_tail(f'{self.cfg.id}/autonomy.jsonl', 200)
        except Exception:
            return []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            started = str(row.get('started_at') or '').strip()
            if not started:
                continue
            audit = row.get('output_audit')
            # S6-2 safety boundary: never ingest roleplay/third-person narration as real experience.
            if isinstance(audit, dict) and audit.get('clean') is False:
                continue
            decision = str(row.get('decision') or '').strip()
            result = str(row.get('result') or '').strip()
            focus = str(row.get('current_focus') or '').strip()
            next_step = str(row.get('next_step') or '').strip()
            tools = [
                {'tool': str(t.get('tool') or ''), 'path': str(t.get('path') or '')[:80], 'state': str(t.get('state') or '')}
                for t in (row.get('tools') or [])[:6] if isinstance(t, dict)
            ]
            ok_tools = [t for t in tools if t['state'] == '成功']
            lab_changed = [str(p)[:100] for p in (row.get('lab_files_changed') or [])[:6]]
            la = row.get('leisure_association') if isinstance(row.get('leisure_association'), dict) else {}
            picked_mems = [str(m) for m in (la.get('picked') or []) if isinstance(m, str) and m]
            if not (ok_tools or lab_changed or picked_mems or decision or focus):
                continue
            aid = 'autonomy:' + started
            fp = _sha([started, result, decision[:120], len(ok_tools), lab_changed, picked_mems])[:16]
            out.append({
                'id': aid,
                'started_at': started,
                'result': result,
                'decision': _clip(decision, 240),
                'focus': _clip(focus, 140),
                'next_step': _clip(next_step, 140),
                'ok_tools': ok_tools,
                'lab_files_changed': lab_changed,
                'leisure_picked': picked_mems,
                'wake_mode': str(la.get('wake_mode') or ''),
                'fingerprint': fp,
            })
        return out

    def _existing_cognition_snapshot(self):
        snap = {
            'facts': '',
            'growth': '',
            'relationship_items': [],
            'ongoing_items': [],
            'vetoed_count': 0,
        }
        if self.profile is None:
            return snap
        try:
            status = self.profile.status(with_text=True)
        except Exception:
            return snap
        if not isinstance(status, dict) or not status.get('available'):
            return snap
        sections = status.get('sections') or {}
        snap['facts'] = str((sections.get('facts') or {}).get('text') or '')
        snap['growth'] = str((sections.get('growth') or {}).get('text') or '')
        rel = (status.get('relationship') or {})
        snap['relationship_items'] = [
            {
                'id': str(it.get('id') or ''),
                'text': str(it.get('text') or ''),
                'status': str(it.get('status') or ''),
                'source': str(it.get('source') or ''),
                'locked': bool(it.get('locked')),
                'sources': list(it.get('sources') or []),
            }
            for it in (rel.get('items') or [])
            if isinstance(it, dict) and it.get('status') != 'outdated'
        ]
        snap['vetoed_count'] = len(rel.get('vetoed') or [])
        ongoing = (status.get('ongoing') or {}).get('items') or []
        snap['ongoing_items'] = [
            {'id': str(it.get('id') or ''), 'text': str(it.get('text') or ''), 'status': str(it.get('status') or '')}
            for it in ongoing
            if isinstance(it, dict) and it.get('status') in ('active', 'uncertain')
        ]
        return snap

    def select_batch(self, state, values):
        """Incrementally select high-value new experiences + relevant historical anchors."""
        memories, _, relations_by_mem, _ = self._read_db_memories()
        processed_mem = state.get('processed_memories') or {}
        processed_auto = state.get('processed_autonomy') or {}

        now_dt = _parse_dt(self._now())

        def value_score(mem):
            score = float(mem['importance']) * 2.0
            if mem['event_id']:
                score += 4.0 + min(6.0, float(mem['stage_count']) * 1.5)
            if mem['meaning'] or mem['why_remembered']:
                score += 2.0
            if mem['chat_refs']:
                score += 2.0
            score += min(5.0, float(mem['deliveries']))
            created_dt = _parse_dt(mem['created'])
            if now_dt and created_dt:
                age_days = max(0.0, (now_dt - created_dt).total_seconds() / 86400.0)
                score += max(0.0, 3.0 - age_days * 0.2)
            return score

        new_mems = []
        old_mems = []
        for mem in memories:
            if _contains_secret(mem['title'], mem['body'], mem['event_story']):
                continue
            if processed_mem.get(mem['id']) == mem['fingerprint']:
                old_mems.append(mem)
            else:
                new_mems.append(mem)

        # Balanced selection: ensure newest unprocessed experiences are always seen promptly
        # (even on a mature 240+ memory database full of high-importance history), while also
        # picking highest cognitive-value unprocessed experiences.
        limit_new = int(values['max_new_memories'])
        recent_quota = max(1, limit_new // 2)
        by_recency = sorted(new_mems, key=lambda m: (m['created'], m['id']), reverse=True)
        chosen_new = list(by_recency[:recent_quota])
        chosen_ids = {m['id'] for m in chosen_new}
        by_value = sorted(new_mems, key=lambda m: (-value_score(m), m['created'], m['id']))
        for mem in by_value:
            if len(chosen_new) >= limit_new:
                break
            if mem['id'] not in chosen_ids:
                chosen_new.append(mem)
                chosen_ids.add(mem['id'])

        auto_rows = self._read_autonomy_experiences()
        new_auto = [
            row for row in auto_rows
            if processed_auto.get(row['started_at']) != row['fingerprint']
            and not _contains_secret(row['decision'], row['focus'], row['next_step'])
        ]
        chosen_auto = new_auto[-int(values['max_autonomy_items']):] if int(values['max_autonomy_items']) > 0 else []

        # Build relevance set to select historical anchors that connect to the new batch.
        new_tokens = set()
        linked_ids = set()
        for mem in chosen_new:
            new_tokens.update(_lexical_tokens(mem['title'] + ' ' + mem['body'][:240] + ' ' + mem['why_remembered']))
            linked_ids.update(relations_by_mem.get(mem['id']) or ())
        for auto in chosen_auto:
            new_tokens.update(_lexical_tokens(auto['decision'] + ' ' + auto['focus']))
            linked_ids.update(auto['leisure_picked'])

        def anchor_score(mem):
            score = 0.0
            if mem['id'] in linked_ids:
                score += 10.0
            toks = _lexical_tokens(mem['title'] + ' ' + mem['body'][:200] + ' ' + mem['why_remembered'])
            overlap = len(toks & new_tokens)
            score += min(8.0, float(overlap) * 1.2)
            if mem['importance'] >= 7 or mem['pinned']:
                score += 2.0
            return score

        old_scored = [(anchor_score(m), m) for m in old_mems]
        old_scored = [pair for pair in old_scored if pair[0] >= 2.0]
        old_scored.sort(key=lambda pair: (-pair[0], -pair[1]['importance'], pair[1]['id']))
        chosen_anchors = [m for _, m in old_scored[:int(values['max_anchor_memories'])]]

        existing = self._existing_cognition_snapshot()
        active_exps = [
            {
                'id': e.get('id'),
                'kind': e.get('kind'),
                'trigger_condition': e.get('trigger_condition'),
                'guidance': e.get('guidance'),
                'status': e.get('status'),
            }
            for e in (state.get('experience_candidates') or [])
            if isinstance(e, dict) and e.get('status') in ('confirmed', 'uncertain')
        ][:20]

        batch_key = _sha([
            [(m['id'], m['fingerprint']) for m in chosen_new],
            [(a['started_at'], a['fingerprint']) for a in chosen_auto],
            _sha([existing['facts'], existing['growth'], [r['text'] for r in existing['relationship_items']]])[:12],
        ])[:24]

        # Build exact verified reference universe for anti-hallucination grounding.
        batch_refs = set()
        mem_by_id = {}
        for mem in (*chosen_new, *chosen_anchors):
            mem_by_id[mem['id']] = mem
            batch_refs.add('memory:' + mem['id'])
            if mem['event_id']:
                batch_refs.add('event:' + mem['event_id'])
            for cref in mem['chat_refs']:
                batch_refs.add(cref)
        for auto in chosen_auto:
            batch_refs.add(auto['id'])

        return {
            'batch_key': batch_key,
            'new_memories': chosen_new,
            'anchor_memories': chosen_anchors,
            'autonomy_items': chosen_auto,
            'existing_cognition': existing,
            'existing_experiences': active_exps,
            'batch_refs': batch_refs,
            'mem_by_id': mem_by_id,
            'new_count': len(chosen_new) + len(chosen_auto),
        }

    # ------------------------------------------------------------------ step 2: model analysis
    def _build_payload(self, batch, *, excerpt_chars=260, anchor_limit=None, new_limit=None, auto_limit=None):
        def pack_mem(m):
            item = {
                'ref': 'memory:' + m['id'],
                'memory_id': m['id'],
                'created': m['created'][:19],
                'importance': m['importance'],
                'pinned': m['pinned'],
                'deliveries': m['deliveries'],
                'distinct_days': m['distinct_days'],
                'title': _clip(m['title'], 100),
                'excerpt': _clip(m['event_story'] or m['body'], excerpt_chars),
            }
            if m['why_remembered']:
                item['why_remembered'] = _clip(m['why_remembered'], 120)
            if m['meaning']:
                item['meaning'] = _clip(m['meaning'], 120)
            if m['event_id']:
                item['event_ref'] = 'event:' + m['event_id']
                item['stage_count'] = m['stage_count']
            if m['chat_refs']:
                item['user_chat_refs'] = m['chat_refs']
            return item

        existing = batch['existing_cognition']
        new_slice = batch['new_memories'][:new_limit] if new_limit is not None else batch['new_memories']
        anchor_slice = batch['anchor_memories'][:anchor_limit] if anchor_limit is not None else batch['anchor_memories']
        auto_slice = batch['autonomy_items'][:auto_limit] if auto_limit is not None else batch['autonomy_items']
        return {
            'as_of': self._now(),
            'existing_cognition': {
                'facts': _clip(existing['facts'], 900),
                'growth': _clip(existing['growth'], 1100),
                'relationship': [
                    {
                        'id': r['id'], 'text': _clip(r['text'], 200), 'status': r['status'],
                        'source': r['source'], 'locked': r['locked'],
                    }
                    for r in existing['relationship_items'][:16]
                ],
                'ongoing': [
                    {'id': o['id'], 'text': _clip(o['text'], 140), 'status': o['status']}
                    for o in existing['ongoing_items'][:10]
                ],
            },
            'existing_experience_candidates': batch['existing_experiences'][:12],
            'new_experiences': [pack_mem(m) for m in new_slice],
            'related_past_anchors': [pack_mem(m) for m in anchor_slice],
            'autonomous_experiences': [
                {
                    'ref': a['id'],
                    'started_at': a['started_at'][:19],
                    'result': a['result'],
                    'decision': _clip(a['decision'], 180),
                    'focus': _clip(a['focus'], 120),
                    'ok_tools': a['ok_tools'][:4],
                    'lab_files_changed': a['lab_files_changed'][:4],
                    'leisure_memories': ['memory:' + m for m in a['leisure_picked'][:4]],
                }
                for a in auto_slice
            ],
        }

    def _fit_messages(self, system, batch, max_bytes):
        """Adaptively bound payload size (UTF-8 bytes) so CJK production batches never overflow."""
        from veil.core.veil_agent import request_byte_size

        new_n = len(batch['new_memories'])
        anc_n = len(batch['anchor_memories'])
        auto_n = len(batch['autonomy_items'])
        for excerpt_chars in (260, 180, 120):
            cur_new, cur_anc, cur_auto = new_n, anc_n, auto_n
            for _ in range(12):
                payload = self._build_payload(
                    batch, excerpt_chars=excerpt_chars,
                    new_limit=cur_new, anchor_limit=cur_anc, auto_limit=cur_auto,
                )
                if _contains_secret(payload, system):
                    return None, 0, 'sensitive_payload'
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                messages = [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': body},
                ]
                size = request_byte_size(messages)
                if size <= max_bytes:
                    return messages, size, ''
                if cur_anc > 2:
                    cur_anc = max(0, cur_anc - 2)
                elif cur_auto > 2:
                    cur_auto = max(1, cur_auto - 2)
                elif cur_new > 2:
                    cur_new = max(2, cur_new - 2)
                else:
                    break
        return None, 0, 'payload_too_large'

    def _call_model(self, batch, cap, timeout, stopped, *, state):
        from veil.core.veil_agent import RequestNotSent

        if self.model is None or getattr(self.cfg, 'provider', {}).get('missing'):
            return {'ok': False, 'status': 'failed', 'reason': 'model_unavailable'}

        system = prompts.text(PROMPT_ID)
        provider = dict(self.cfg.provider)
        out_tokens = min(self.cfg.max_output_tokens, MAX_OUTPUT_TOKENS)
        max_bytes = min(provider['max_input_tokens'], MAX_INPUT_TOKENS)
        in_rate = Decimal(str(provider.get('input_cny_per_million', 0) or 0))
        out_rate = Decimal(str(provider.get('output_cny_per_million', 0) or 0))
        if cap > 0 and in_rate > 0:
            out_reserve = Decimal(out_tokens) * out_rate / Decimal(1_000_000)
            avail_in = cap - out_reserve
            if avail_in > 0:
                affordable_bytes = int((avail_in * Decimal(1_000_000)) / in_rate)
                if affordable_bytes >= 4_000:
                    max_bytes = min(max_bytes, affordable_bytes)
        messages, input_bytes, fit_err = self._fit_messages(system, batch, max_bytes)
        if fit_err:
            return {'ok': False, 'status': 'failed', 'reason': fit_err}
        provider['max_input_tokens'] = input_bytes
        request_cfg = replace(
            self.cfg,
            provider=provider,
            max_output_tokens=out_tokens,
        )
        if stopped():
            return {'ok': False, 'status': 'skipped', 'reason': 'stopped'}
        try:
            reservation = self.budget.reserve(
                request_cfg, category=COST_CATEGORY,
                accounting=COST_ACCOUNTING, category_daily=cap,
            )
        except BudgetExceeded:
            return {'ok': False, 'status': 'budget_exhausted', 'reason': 'budget_exhausted'}
        except UserError:
            return {'ok': False, 'status': 'failed', 'reason': 'ledger_unavailable'}

        request_id, reserved_amount = reservation
        marked = False
        try:
            if stopped():
                return {'ok': False, 'status': 'skipped', 'reason': 'stopped'}
            with self.mutex:
                if isinstance(state.get('in_flight'), dict):
                    state['in_flight']['request_id'] = request_id
                    state['in_flight']['phase'] = 'dispatched'
                    self._save_state(state)
            self.budget.mark_dispatched(request_id)
            marked = True
        finally:
            if not marked:
                self.budget.release_not_sent(request_id)

        def retain_upper(reason):
            try:
                self.budget.settle_estimate(reservation, accounting=COST_ACCOUNTING + '-unknown-usage')
            except Exception:
                pass
            return {'ok': False, 'status': 'failed', 'reason': reason, 'cost_cny': float(reserved_amount)}

        start = time.monotonic()
        try:
            output, prompt_tok, completion_tok, cached_tok, stop_reason = self.model(
                messages, json_output=True, max_tokens=request_cfg.max_output_tokens,
                thinking='disabled', detail=True, request_timeout=timeout,
            )
        except RequestNotSent:
            self.budget.release_not_sent(request_id)
            return {'ok': False, 'status': 'failed', 'reason': 'request_not_sent', 'cost_cny': 0.0}
        except (TimeoutError, APITimeoutError):
            return retain_upper('timeout')
        except Exception:
            return retain_upper('provider_error')

        late = time.monotonic() - start > timeout
        try:
            self.budget.settle(
                request_id, request_cfg, prompt_tok, completion_tok, cached_tok,
                input_bytes=input_bytes, category=COST_CATEGORY, accounting=COST_ACCOUNTING,
            )
            cached_count = cached_tok.tokens if hasattr(cached_tok, 'tokens') else (cached_tok or 0)
            cached_count = int(cached_count) if type(cached_count) is int and cached_count >= 0 else 0
            p = request_cfg.provider
            if p.get('cny_per_call'):
                settled_dec = Decimal(str(p['cny_per_call']))
            else:
                settled_dec = (
                    Decimal(max(0, prompt_tok - cached_count)) * Decimal(str(p['input_cny_per_million']))
                    + Decimal(cached_count) * Decimal(str(p['cached_input_cny_per_million']))
                    + Decimal(completion_tok) * Decimal(str(p['output_cny_per_million']))
                ) / Decimal(1_000_000)
            cost_val = float(settled_dec)
        except UserError:
            return retain_upper('invalid_usage')
        if late or stopped():
            return {
                'ok': False, 'status': 'failed',
                'reason': 'timeout' if late else 'stopped',
                'cost_cny': cost_val,
                'prompt_tokens': prompt_tok, 'completion_tokens': completion_tok, 'cached_tokens': cached_tok,
            }
        if stop_reason != 'stop':
            return {
                'ok': False, 'status': 'failed', 'reason': 'invalid_response',
                'cost_cny': cost_val,
                'prompt_tokens': prompt_tok, 'completion_tokens': completion_tok, 'cached_tokens': cached_tok,
            }
        try:
            parsed = parse_consolidation_output(output)
        except (ValueError, RecursionError):
            return {
                'ok': False, 'status': 'failed', 'reason': 'invalid_response',
                'cost_cny': cost_val,
                'prompt_tokens': prompt_tok, 'completion_tokens': completion_tok, 'cached_tokens': cached_tok,
            }
        return {
            'ok': True,
            'parsed': parsed,
            'cost_cny': cost_val,
            'prompt_tokens': prompt_tok,
            'completion_tokens': completion_tok,
            'cached_tokens': cached_tok,
        }

    # ------------------------------------------------------------------ step 3: governance & collaboration
    @staticmethod
    def _is_novel_text(candidate_text, existing_texts):
        norm = _norm_text(candidate_text)
        if not norm:
            return False
        for old in existing_texts:
            if not old:
                continue
            if _norm_text(old) == norm or _matches_governed_claim(candidate_text, old) or _jaccard_similar(candidate_text, old):
                return False
        return True

    def _expand_s3_sources(self, refs, mem_by_id, *, section='relationship'):
        """Keep only verifiable S3 SOURCE_REF pointers (chat/event/memory), never forge autonomy as user chat.

        For `relationship`, passing a memory/event pointer that carries no user-chat origin causes S3
        `_evaluate_sources` to flag `partial_stale` and stall semantic verification even when another
        cited reference is verified user speech. Keep only user-origin-backed pointers for S3
        relationship admission while preserving the full `evidence_refs` in the consolidation log.
        """
        s3_sources = []
        seen = set()
        ev_has_user = {
            m['event_id']: bool(m['chat_refs'])
            for m in mem_by_id.values() if m.get('event_id')
        }

        def add(ref):
            if SOURCE_REF.fullmatch(ref) and ref not in seen:
                s3_sources.append(ref)
                seen.add(ref)

        for ref in refs:
            if ref.startswith('autonomy:'):
                continue
            if ref.startswith('chat:'):
                add(ref)
                continue
            if ref.startswith('event:'):
                ev_id = ref.split(':', 1)[1]
                if section != 'relationship' or ev_has_user.get(ev_id):
                    add(ref)
                continue
            if ref.startswith('memory:'):
                mid = ref.split(':', 1)[1]
                mem = mem_by_id.get(mid)
                if mem:
                    has_user = bool(mem['chat_refs'])
                    if section != 'relationship' or has_user:
                        add(ref)
                        if mem['event_id']:
                            add('event:' + mem['event_id'])
                    for cref in mem['chat_refs']:
                        add(cref)
                elif section != 'relationship':
                    add(ref)
        return s3_sources[:8]

    def _build_s6_1_evidence(self, memory_id, refs, mem_by_id, existing_rel):
        """Construct strictly verified S6-1 MemoryWeights evidence entries tied to memory_id."""
        mem = mem_by_id.get(memory_id)
        if not mem:
            return []
        ev_list = []
        seen = set()

        def add(kind, ref, detail):
            key = (kind, ref)
            if key in seen or not ref:
                return
            seen.add(key)
            ev_list.append({'kind': kind, 'ref': ref, 'detail': _clip(detail, 120)})

        if mem['event_id']:
            add('event', mem['event_id'], f"关联事件「{mem['event_title'] or mem['event_id']}」")
        add('memory', memory_id, f"巩固经历「{mem['title']}」")
        # Include any confirmed relationship item referencing this memory or its event.
        target_refs = {f'memory:{memory_id}'}
        if mem['event_id']:
            target_refs.add(f"event:{mem['event_id']}")
        for rel in existing_rel:
            if rel.get('status') == 'confirmed' and set(rel.get('sources') or ()) & target_refs:
                add('relationship', rel['id'], f"关系认知「{_clip(rel['text'], 60)}」引用")
        # Check delivery receipts if activation exists.
        if mem['deliveries'] > 0:
            db = self._db_path()
            if db is not None and db.is_file():
                try:
                    conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=3.0)
                    row = conn.execute(
                        'SELECT request_id FROM memory_delivery_receipts WHERE memory_id=? '
                        'ORDER BY rowid DESC LIMIT 1', (memory_id,)
                    ).fetchone()
                    conn.close()
                    if row and row[0]:
                        add('delivery', str(row[0]), f"实际送达 {mem['deliveries']} 次")
                except sqlite3.Error:
                    pass
        return ev_list[:8]

    def _apply_outputs(self, parsed, batch, state, values, run_id):
        batch_refs = batch['batch_refs']
        mem_by_id = batch['mem_by_id']
        existing = batch['existing_cognition']

        existing_corpus = []
        for sec_text in (existing['facts'], existing['growth']):
            for para in sec_text.split('\n\n'):
                if para.strip():
                    existing_corpus.append(para.strip())
        for item in existing['relationship_items']:
            existing_corpus.append(item['text'])

        summary = {
            'cognition_updates': [],
            'importance_proposals': [],
            'experience_candidates': [],
            'reverify': {'checked': False, 'changed': False},
            'rejected_ungrounded': 0,
            'skipped_duplicates': 0,
        }

        # 0. Periodically reverify existing relationship evidence (never mutates locked/lan items).
        if self.profile is not None and hasattr(self.profile, 'reverify_relationship_evidence'):
            try:
                rev_res = self.profile.reverify_relationship_evidence()
                summary['reverify'] = {'checked': True, 'changed': bool(rev_res.get('changed'))}
            except Exception:
                summary['reverify'] = {'checked': False, 'changed': False}

        # 1. Cognition updates -> S3 LearnedContext.submit_candidate() (zero parallel store).
        batch_seen_texts = list(existing_corpus)
        for idx, upd in enumerate(parsed['cognition_updates']):
            valid_refs = [r for r in upd['evidence_refs'] if r in batch_refs]
            if not valid_refs:
                summary['rejected_ungrounded'] += 1
                continue
            section = upd['section']
            kind = upd['kind']
            text = upd['text']
            replace_old = upd['replace_old_text'] if kind == 'revision' else ''
            if kind == 'new' and not self._is_novel_text(text, batch_seen_texts):
                summary['skipped_duplicates'] += 1
                continue
            if kind == 'revision' and not replace_old:
                summary['rejected_ungrounded'] += 1
                continue

            s3_sources = self._expand_s3_sources(valid_refs, mem_by_id, section=section)
            has_non_autonomy = bool(s3_sources)
            req_status = (
                'confirmed'
                if (section == 'relationship' and upd['claim_status'] == 'evidence_backed' and has_non_autonomy)
                else 'uncertain'
            )

            entry = {
                'kind': kind,
                'section': section,
                'text': text,
                'replace_old_text': replace_old,
                'reason': upd['reason'],
                'evidence_refs': valid_refs,
                's3_sources': s3_sources,
                'requested_status': req_status,
                'governance_outcome': 'profile_unavailable',
                'effective_status': 'not_applied',
                'message': '',
                'item_id': '',
            }
            if self.profile is not None and hasattr(self.profile, 'submit_candidate'):
                idem_key = f"cons:{batch['batch_key']}:c{idx}"
                msg, st_label, _ = self.profile.submit_candidate(
                    section,
                    text,
                    status=req_status,
                    sources=s3_sources,
                    replace_old_text=replace_old or None,
                    origin='consolidation',
                    idempotency_key=idem_key,
                )
                entry['message'] = str(msg or '')
                if st_label == '成功':
                    batch_seen_texts.append(text)
                    entry['governance_outcome'] = 'accepted'
                    if kind == 'revision':
                        state['totals']['cognition_revised'] += 1
                    else:
                        state['totals']['cognition_written'] += 1
                    if section == 'relationship':
                        # Inspect actual live status from S3 so UI shows true effective state.
                        rel_state = self._find_relationship_state(text)
                        entry['item_id'] = rel_state.get('id', '')
                        sem = rel_state.get('semantic_state', '')
                        eff = rel_state.get('effective_status', rel_state.get('status', 'uncertain'))
                        if eff == 'confirmed' and not rel_state.get('prompt_vetoed'):
                            entry['effective_status'] = 'effective_confirmed'
                        elif sem in ('pending', 'dispatched', 'budget'):
                            entry['effective_status'] = 'queued_semantic_verify'
                            state['totals']['semantic_queued'] += 1
                        else:
                            entry['effective_status'] = 'effective_uncertain'
                    else:
                        entry['effective_status'] = 'effective_learned'
                else:
                    lower = str(msg or '').lower()
                    if any(tok in lower for tok in ('locked', 'vetoed', 'corrected', 'saved by lan', 'higher-priority', 'removed by lan')):
                        entry['governance_outcome'] = 'blocked_sovereignty'
                        entry['effective_status'] = 'blocked_sovereignty'
                        state['totals']['cognition_blocked'] += 1
                    elif 'limit is' in lower or '超出' in lower:
                        entry['governance_outcome'] = 'rejected_budget'
                        entry['effective_status'] = 'rejected_budget'
                    else:
                        entry['governance_outcome'] = 'rejected'
                        entry['effective_status'] = 'rejected'
            summary['cognition_updates'].append(entry)

        # Refresh relationship items after S3 writes so S6-1 can see newly confirmed refs.
        refreshed_rel = self._existing_cognition_snapshot()['relationship_items']

        # 2. Importance proposals -> S6-1 MemoryWeights.propose() (no bypass).
        weights = self._weights()
        imp_ledger = state.setdefault('importance_proposed', {})
        for prop in parsed['importance_proposals']:
            mid = prop['memory_id']
            mem = mem_by_id.get(mid)
            valid_refs = [r for r in prop['evidence_refs'] if r in batch_refs]
            if mem is None or not valid_refs:
                summary['rejected_ungrounded'] += 1
                continue
            if prop['proposed_importance'] == mem['importance']:
                summary['skipped_duplicates'] += 1
                continue

            evidence = self._build_s6_1_evidence(mid, valid_refs, mem_by_id, refreshed_rel)
            if not evidence:
                summary['rejected_ungrounded'] += 1
                continue

            ev_fp = _sha([mid, prop['proposed_importance'], [(e['kind'], e['ref']) for e in evidence]])[:16]
            prior = imp_ledger.get(mid)
            if isinstance(prior, dict) and prior.get('evidence_fp') == ev_fp:
                summary['skipped_duplicates'] += 1
                continue

            item_rec = {
                'memory_id': mid,
                'memory_title': mem['title'],
                'current_importance': mem['importance'],
                'proposed_importance': prop['proposed_importance'],
                'confidence': prop['confidence'],
                'reason': prop['reason'],
                'evidence_refs': valid_refs,
                'outcome': 'weights_unavailable',
                'proposal_id': '',
                'applied': False,
                'version_seq': None,
            }
            if weights is not None:
                # Respect prior Lan rejection on the same memory/direction without nagging.
                recent_hist = weights.history(mid, limit=5)
                if any(
                    h.get('status') == 'rejected'
                    and h.get('proposed_importance') == prop['proposed_importance']
                    for h in recent_hist
                ):
                    item_rec['outcome'] = 'skipped_previously_rejected'
                    summary['skipped_duplicates'] += 1
                    summary['importance_proposals'].append(item_rec)
                    continue

                reasons = [{
                    'code': 'external',
                    'label': '认知巩固建议',
                    'detail': prop['reason'],
                    'weight': 0.0,
                }]
                res = weights.propose(
                    mid,
                    trigger='consolidation',
                    proposed_importance=prop['proposed_importance'],
                    confidence=prop['confidence'],
                    reasons=reasons,
                    evidence=evidence,
                    auto_apply=bool(values['auto_apply_importance']),
                    note=f"认知巩固({run_id}): {_clip(prop['reason'], 120)}",
                )
                if res.get('ok'):
                    proposal = res.get('proposal') or {}
                    item_rec['proposal_id'] = str(proposal.get('proposal_id') or '')
                    item_rec['applied'] = bool(res.get('applied'))
                    item_rec['version_seq'] = proposal.get('version_seq')
                    item_rec['outcome'] = 'applied' if item_rec['applied'] else 'pending_review'
                    imp_ledger[mid] = {
                        'evidence_fp': ev_fp,
                        'proposed_at': self._now(),
                        'proposal_id': item_rec['proposal_id'],
                        'proposed_importance': prop['proposed_importance'],
                    }
                    state['totals']['importance_proposed'] += 1
                    if item_rec['applied']:
                        state['totals']['importance_applied'] += 1
                else:
                    item_rec['outcome'] = 'skipped:' + str(res.get('reason') or 'declined')
            summary['importance_proposals'].append(item_rec)

        # 3. P7 unified experience candidates (structured, versioned, veto-aware, never in HEAD).
        exp_list = state.setdefault('experience_candidates', [])
        profile_data = self._read_profile_raw()
        for exp in parsed['experience_candidates']:
            valid_refs = [r for r in exp['evidence_refs'] if r in batch_refs]
            if not valid_refs:
                summary['rejected_ungrounded'] += 1
                continue
            # Honor Lan vetoes in LearnedContext AND in P7 candidate list.
            if self._is_vetoed_experience(exp['guidance'], exp_list, profile_data):
                summary['experience_candidates'].append({
                    'id': '',
                    'kind': exp['kind'],
                    'trigger_condition': exp['trigger_condition'],
                    'guidance': exp['guidance'],
                    'reason': exp['reason'],
                    'evidence_refs': valid_refs,
                    'status': 'vetoed',
                    'outcome': 'blocked_sovereignty',
                })
                state['totals']['cognition_blocked'] += 1
                continue

            # Dedup against active P7 candidates unless explicitly superseding.
            supersedes_id = exp['supersedes_id']
            dup = next(
                (
                    e for e in exp_list
                    if isinstance(e, dict)
                    and e.get('status') in ('confirmed', 'uncertain')
                    and e.get('id') != supersedes_id
                    and (_norm_text(e.get('guidance')) == _norm_text(exp['guidance'])
                         or _jaccard_similar(e.get('guidance', ''), exp['guidance']))
                ),
                None,
            )
            if dup is not None:
                summary['skipped_duplicates'] += 1
                continue

            now_stamp = self._now()
            new_id = 'exp-' + uuid.uuid4().hex[:10]
            superseded_target = None
            if supersedes_id:
                superseded_target = next(
                    (
                        e for e in exp_list
                        if isinstance(e, dict) and e.get('id') == supersedes_id
                        and e.get('status') in ('confirmed', 'uncertain')
                    ),
                    None,
                )
            record = {
                'id': new_id,
                'schema_version': EXPERIENCE_SCHEMA_VERSION,
                'kind': exp['kind'],
                'trigger_condition': exp['trigger_condition'],
                'guidance': exp['guidance'],
                'reason': exp['reason'],
                'evidence_refs': valid_refs,
                'status': 'uncertain',
                'origin': 'consolidation',
                'run_id': run_id,
                'batch_key': batch['batch_key'],
                'created_at': now_stamp,
                'updated_at': now_stamp,
                'supersedes': superseded_target['id'] if superseded_target else '',
                'superseded_by': '',
                'effective_in': 'p7_reserved',
            }
            if superseded_target is not None:
                superseded_target['status'] = 'outdated'
                superseded_target['superseded_by'] = new_id
                superseded_target['updated_at'] = now_stamp
                state['totals']['experience_revised'] += 1
                outcome = 'revised'
            else:
                state['totals']['experience_created'] += 1
                outcome = 'created'
            exp_list.append(record)
            summary['experience_candidates'].append({
                'id': new_id,
                'kind': exp['kind'],
                'trigger_condition': exp['trigger_condition'],
                'guidance': exp['guidance'],
                'reason': exp['reason'],
                'evidence_refs': valid_refs,
                'status': 'uncertain',
                'supersedes_id': record['supersedes'],
                'outcome': outcome,
            })

        return summary

    def _find_relationship_state(self, text):
        if self.profile is None:
            return {}
        try:
            items = self.profile.relationship_items()
        except Exception:
            return {}
        norm = _norm_text(text)
        for item in reversed(items):
            if isinstance(item, dict) and _norm_text(item.get('text')) == norm:
                return item
        return {}

    def _read_profile_raw(self):
        if self.profile is None or not hasattr(self.profile, 'read'):
            return None
        try:
            return self.profile.read()
        except Exception:
            return None

    def _is_vetoed_experience(self, guidance, exp_list, profile_data):
        for item in exp_list:
            if isinstance(item, dict) and item.get('status') == 'vetoed':
                if _matches_governed_claim(guidance, item.get('guidance', '')):
                    return True
        if profile_data is not None and self.profile is not None and hasattr(self.profile, '_is_rebutted_by_lan'):
            try:
                if self.profile._is_rebutted_by_lan(guidance, profile_data):
                    return True
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------ execution engine
    def _due_check(self, state, values, *, trigger, force):
        if trigger == 'scheduled' and not values['enabled']:
            return False, 'disabled'
        cap = Decimal(str(values['daily_budget_cny']))
        if cap <= 0:
            return False, 'budget_cap_zero'
        now_stamp = self._now()
        now_dt = _parse_dt(now_stamp)
        today = now_stamp[:10]
        runs_today = int(state.get('runs_today') or 0) if state.get('day') == today else 0
        if not force and runs_today >= int(values['max_runs_per_day']):
            return False, 'daily_run_limit'
        if not force and state.get('cooldown_until'):
            cd_dt = _parse_dt(state['cooldown_until'])
            if cd_dt is not None and now_dt is not None and now_dt < cd_dt:
                return False, 'failure_backoff'
        if trigger == 'scheduled' and not force and state.get('last_success_at'):
            last_dt = _parse_dt(state['last_success_at'])
            if last_dt is not None and now_dt is not None:
                elapsed_hours = (now_dt - last_dt).total_seconds() / 3600.0
                if elapsed_hours < float(values['interval_hours']):
                    return False, 'interval_not_elapsed'
        return True, ''

    def run_once(self, *, trigger='manual', force=False, stopped=lambda: False):
        """Execute at most one bounded, idempotent consolidation pass."""
        if not self._run_lock.acquire(blocking=False):
            return {'ok': False, 'status': 'skipped', 'reason': 'already_running'}
        try:
            values = self.settings()
            with self.mutex:
                state = self._load_state()
                due, reason = self._due_check(state, values, trigger=trigger, force=force)
                if not due:
                    return {'ok': False, 'status': 'skipped', 'reason': reason}

            batch = self.select_batch(state, values)
            min_items = 1 if force else int(values['min_new_items'])
            if batch['new_count'] < min_items:
                return {
                    'ok': True, 'status': 'skipped', 'reason': 'no_new_material',
                    'new_count': batch['new_count'], 'min_required': min_items,
                }
            if batch['batch_key'] in (state.get('processed_batches') or {}) and not force:
                return {
                    'ok': True, 'status': 'skipped', 'reason': 'duplicate_batch',
                    'batch_key': batch['batch_key'],
                }

            run_id = 'cons-' + uuid.uuid4().hex[:10]
            started_at = self._now()
            start_mono = time.monotonic()
            inputs_summary = {
                'new_memory_ids': [m['id'] for m in batch['new_memories']],
                'anchor_memory_ids': [m['id'] for m in batch['anchor_memories']],
                'autonomy_ids': [a['id'] for a in batch['autonomy_items']],
                'existing_relationship_count': len(batch['existing_cognition']['relationship_items']),
                'existing_experience_count': len(batch['existing_experiences']),
            }

            with self.mutex:
                state['in_flight'] = {
                    'run_id': run_id,
                    'batch_key': batch['batch_key'],
                    'started_at': started_at,
                    'trigger': trigger,
                    'phase': 'starting',
                    'inputs': inputs_summary,
                }
                self._save_state(state)

            cap = Decimal(str(values['daily_budget_cny']))
            model_res = self._call_model(
                batch, cap, int(values['timeout_seconds']), stopped, state=state,
            )
            finished_at = self._now()
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            today = finished_at[:10]

            if not model_res.get('ok'):
                fail_status = model_res.get('status') or 'failed'
                fail_reason = model_res.get('reason') or 'unknown'
                with self.mutex:
                    state['in_flight'] = None
                    state['last_run_at'] = finished_at
                    state['last_run_id'] = run_id
                    state['last_status'] = fail_status
                    state['last_reason'] = fail_reason
                    state['totals']['runs'] += 1
                    if fail_status == 'budget_exhausted':
                        state['totals']['budget_exhausted'] += 1
                    elif fail_status == 'skipped':
                        state['totals']['skipped'] += 1
                    else:
                        state['totals']['failed'] += 1
                        failures = int(state.get('consecutive_failures') or 0) + 1
                        state['consecutive_failures'] = failures
                        backoff_min = min(120, 15 * (2 ** min(failures - 1, 3)))
                        now_dt = _parse_dt(finished_at)
                        if now_dt is not None:
                            state['cooldown_until'] = (
                                now_dt + timedelta(minutes=backoff_min)
                            ).isoformat(timespec='seconds')
                    self._save_state(state)
                    run_record = {
                        'run_id': run_id, 'started_at': started_at, 'finished_at': finished_at,
                        'trigger': trigger, 'status': fail_status, 'reason': fail_reason,
                        'batch_key': batch['batch_key'], 'policy_version': POLICY_VERSION,
                        'inputs': inputs_summary, 'results': {},
                        'cost_cny': round(float(model_res.get('cost_cny') or 0.0), 6),
                        'prompt_tokens': model_res.get('prompt_tokens'),
                        'completion_tokens': model_res.get('completion_tokens'),
                        'cached_tokens': model_res.get('cached_tokens'),
                        'duration_ms': duration_ms,
                    }
                    self._append_run(run_record)
                return {'ok': False, **run_record}

            with self.mutex:
                if isinstance(state.get('in_flight'), dict):
                    state['in_flight']['phase'] = 'applying'
                    self._save_state(state)
                applied = self._apply_outputs(model_res['parsed'], batch, state, values, run_id)
                for mem in batch['new_memories']:
                    state['processed_memories'][mem['id']] = mem['fingerprint']
                for auto in batch['autonomy_items']:
                    state['processed_autonomy'][auto['started_at']] = auto['fingerprint']
                state['processed_batches'][batch['batch_key']] = finished_at
                state['in_flight'] = None
                state['last_run_at'] = finished_at
                state['last_success_at'] = finished_at
                state['last_run_id'] = run_id
                state['last_status'] = 'completed'
                state['last_reason'] = ''
                if state.get('day') == today:
                    state['runs_today'] = int(state.get('runs_today') or 0) + 1
                else:
                    state['day'] = today
                    state['runs_today'] = 1
                state['consecutive_failures'] = 0
                state['cooldown_until'] = ''
                state['totals']['runs'] += 1
                state['totals']['completed'] += 1
                self._save_state(state)
                run_record = {
                    'run_id': run_id,
                    'started_at': started_at,
                    'finished_at': finished_at,
                    'trigger': trigger,
                    'status': 'completed',
                    'reason': '',
                    'batch_key': batch['batch_key'],
                    'policy_version': POLICY_VERSION,
                    'inputs': inputs_summary,
                    'results': applied,
                    'cost_cny': round(float(model_res.get('cost_cny') or 0.0), 6),
                    'prompt_tokens': model_res.get('prompt_tokens'),
                    'completion_tokens': model_res.get('completion_tokens'),
                    'cached_tokens': model_res.get('cached_tokens'),
                    'duration_ms': duration_ms,
                }
                self._append_run(run_record)
            return {'ok': True, **run_record}
        finally:
            self._run_lock.release()

    # ------------------------------------------------------------------ P7 unified interface
    def experience_interface_contract(self):
        return {
            'schema_version': EXPERIENCE_SCHEMA_VERSION,
            'slot_reserved': 'p7.experience_hints',
            'statuses': sorted(_VALID_EXP_STATUSES),
            'kinds': sorted(_VALID_EXP_KINDS),
            'injected_in_head': False,
        }

    def list_experience_candidates(self, *, status='', kind='', limit=50):
        with self.mutex:
            state = self._load_state()
            rows = list(state.get('experience_candidates') or [])
        out = []
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if status and row.get('status') != status:
                continue
            if kind and row.get('kind') != kind:
                continue
            out.append(dict(row))
            if len(out) >= max(1, min(200, int(limit or 50))):
                break
        return out

    def query_experience_candidates(self, *, situation='', kinds=None, statuses=('confirmed',), limit=5):
        """Unified P7 read contract: return verified, non-vetoed experience hints matching a situation."""
        allowed_statuses = set(statuses or ('confirmed',))
        allowed_kinds = set(kinds) if kinds else None
        sit_tokens = _lexical_tokens(situation) if situation else set()
        profile_data = self._read_profile_raw()
        with self.mutex:
            state = self._load_state()
            rows = list(state.get('experience_candidates') or [])
        valid_mems = self._existing_memory_ids()
        scored = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get('status') not in allowed_statuses:
                continue
            if allowed_kinds is not None and row.get('kind') not in allowed_kinds:
                continue
            if self._is_vetoed_experience(row.get('guidance', ''), rows, profile_data):
                continue
            # Filter out candidates whose memory evidence was all hard-deleted.
            refs = list(row.get('evidence_refs') or [])
            mem_refs = [r.split(':', 1)[1] for r in refs if isinstance(r, str) and r.startswith('memory:')]
            if mem_refs and valid_mems is not None and not any(m in valid_mems for m in mem_refs):
                continue
            score = 1.0 if row.get('status') == 'confirmed' else 0.5
            if sit_tokens:
                cand_tokens = _lexical_tokens(
                    f"{row.get('trigger_condition', '')} {row.get('guidance', '')}"
                )
                overlap = len(sit_tokens & cand_tokens)
                if overlap == 0:
                    continue
                score += float(overlap)
            scored.append((score, str(row.get('updated_at') or ''), dict(row)))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:max(1, min(20, int(limit or 5)))]]

    def _existing_memory_ids(self):
        db = self._db_path()
        if db is None or not db.is_file():
            return None
        try:
            conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=3.0)
            ids = {
                row[0] for row in conn.execute(
                    'SELECT id FROM memories WHERE is_tombstone=0 AND is_deleted=0'
                )
            }
            conn.close()
            return ids
        except sqlite3.Error:
            return None

    def decide_experience_candidate(self, candidate_id, action, note=''):
        """Lan sovereignty over P7 experience candidates: confirm | veto | outdated | restore."""
        action = str(action or '').strip()
        if action not in ('confirm', 'veto', 'outdated', 'restore'):
            return {'ok': False, 'error': 'unknown_action', 'status_code': 400}
        cid = str(candidate_id or '').strip()
        if not cid:
            return {'ok': False, 'error': 'missing_id', 'status_code': 400}
        with self.mutex:
            state = self._load_state()
            rows = state.get('experience_candidates') or []
            target = next((r for r in rows if isinstance(r, dict) and r.get('id') == cid), None)
            if target is None:
                return {'ok': False, 'error': 'candidate_not_found', 'status_code': 404}
            mapping = {'confirm': 'confirmed', 'veto': 'vetoed', 'outdated': 'outdated', 'restore': 'confirmed'}
            target['status'] = mapping[action]
            target['decided_by'] = 'lan'
            target['decided_at'] = self._now()
            target['updated_at'] = target['decided_at']
            if note:
                target['note'] = _clip(note, 200)
            self._save_state(state)
            return {'ok': True, 'candidate': dict(target)}

    # ------------------------------------------------------------------ status projection
    def status(self, *, runs_limit=12):
        values = self.settings()
        with self.mutex:
            state = self._load_state()
        budget_info = self._budget_snapshot(values)
        cap = Decimal(budget_info['daily_cap_cny'])
        remaining = Decimal(budget_info['remaining_cny'])
        today = budget_info['day']
        runs_today = int(state.get('runs_today') or 0) if state.get('day') == today else 0
        running = self._run_lock.locked()
        if not values['enabled']:
            eff_code = 'disabled'
            eff_text = '已关闭（默认灰度关闭）'
        elif cap <= 0:
            eff_code = 'paused_budget_zero'
            eff_text = '已开启但日额度为 ¥0（暂停运行）'
        elif remaining <= 0:
            eff_code = 'budget_exhausted'
            eff_text = '今日额度已用完（次日自动恢复）'
        elif runs_today >= int(values['max_runs_per_day']):
            eff_code = 'daily_limit_reached'
            eff_text = f"今日已达次数上限（{runs_today}/{values['max_runs_per_day']}）"
        elif running:
            eff_code = 'running'
            eff_text = '后台巩固运行中'
        else:
            eff_code = 'active'
            eff_text = f"已启用 · 每 {values['interval_hours']} 小时检查增量"

        exps = list(state.get('experience_candidates') or [])
        exp_counts = {
            'total': len(exps),
            'confirmed': sum(1 for e in exps if isinstance(e, dict) and e.get('status') == 'confirmed'),
            'uncertain': sum(1 for e in exps if isinstance(e, dict) and e.get('status') == 'uncertain'),
            'outdated': sum(1 for e in exps if isinstance(e, dict) and e.get('status') == 'outdated'),
            'vetoed': sum(1 for e in exps if isinstance(e, dict) and e.get('status') == 'vetoed'),
        }
        return {
            'available': True,
            'policy_version': POLICY_VERSION,
            'persistence_path': self.settings_path(),
            'settings': values,
            'defaults': dict(DEFAULTS),
            'effective': {
                'code': eff_code,
                'status_text': eff_text,
                'running': running,
                'runs_today': runs_today,
                'max_runs_per_day': int(values['max_runs_per_day']),
                'processed_memories': len(state.get('processed_memories') or {}),
                'processed_autonomy': len(state.get('processed_autonomy') or {}),
                'last_run_at': state.get('last_run_at') or '',
                'last_success_at': state.get('last_success_at') or '',
                'last_status': state.get('last_status') or 'never',
                'last_reason': state.get('last_reason') or '',
                'consecutive_failures': int(state.get('consecutive_failures') or 0),
                'cooldown_until': state.get('cooldown_until') or '',
            },
            'budget': budget_info,
            'totals': dict(state.get('totals') or {}),
            'p7_interface': {
                **self.experience_interface_contract(),
                'counts': exp_counts,
            },
            'experience_candidates': self.list_experience_candidates(limit=30),
            'recent_runs': self.recent_runs(limit=runs_limit),
            'guards': {
                'default_disabled': True,
                'reuses_s3_governance': True,
                'reuses_s6_1_weights': True,
                'filters_unclean_autonomy': True,
                'respects_lan_locks_and_vetoes': True,
            },
        }
