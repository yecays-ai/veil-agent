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
import math
import re
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
from veil.brain.storage.brain_lock import write_lock
from veil.core.agent_types import BudgetExceeded, UserError

COST_CATEGORY = 'cognitive_consolidation'
COST_ACCOUNTING = 'cognitive_consolidation'
P7_GATE_COST_CATEGORY = 'p7_experience_gate'
P7_GATE_COST_ACCOUNTING = 'p7_experience_gate'
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
MAX_COMMIT_ATTEMPTS = 3
MAX_PENDING_COGNITION = 60

_CODE_FENCE = chr(96) * 3
_VALID_SECTIONS = frozenset({'relationship', 'growth', 'facts'})
_VALID_EXP_KINDS = frozenset({
    'behavioral_pattern', 'tool_lesson', 'communication_pattern', 'autonomy_lesson',
})
_VALID_EXP_STATUSES = frozenset({'uncertain', 'confirmed', 'outdated', 'vetoed'})
_VALID_EXP_TYPES = frozenset({
    'success', 'failure', 'unfulfilled_commitment', 'unverified_improvement', 'unknown_legacy',
})
_VALID_EXP_DOMAINS = frozenset({
    'relationship_consensus', 'commitment_accountability', 'work_method', 'autonomous_practice',
})
_VALID_EVIDENCE_TIERS = frozenset({
    'L1_user_feedback', 'L2_objective_action', 'L3_autonomy_reflection', 'L4_self_eval',
})
_VALID_PROMOTERS = frozenset({'lan', 'objective_corroboration', 's3_user_evidence', ''})
_VALID_EVIDENCE_STATES = frozenset({'valid', 'partial_stale', 'invalid', 'unavailable'})
_KIND_DOMAINS = {
    'communication_pattern': 'relationship_consensus',
    'tool_lesson': 'work_method',
    'autonomy_lesson': 'autonomous_practice',
}

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
    if set(parsed.keys()) != allowed:
        raise ValueError('unexpected_or_missing_top_keys')

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
        exp_type = str(row.get('experience_type') or 'unknown_legacy').strip()
        domain = str(row.get('domain') or '').strip()
        exceptions = row.get('exceptions')
        tier = str(row.get('origin_evidence_tier') or 'L4_self_eval').strip()
        experiences.append({
            'kind': kind,
            'trigger_condition': trigger_cond[:240],
            'guidance': guidance[:400],
            'reason': reason[:360],
            'evidence_refs': [r.strip() for r in refs[:12]],
            'supersedes_id': supersedes[:64],
            'experience_type': exp_type if exp_type in _VALID_EXP_TYPES else 'unknown_legacy',
            'domain': domain if domain in _VALID_EXP_DOMAINS else _KIND_DOMAINS.get(kind, ''),
            'exceptions': [str(item).strip()[:120] for item in exceptions[:4]
                           if isinstance(item, str) and item.strip()] if isinstance(exceptions, list) else [],
            'origin_evidence_tier': tier if tier in _VALID_EVIDENCE_TIERS else 'L4_self_eval',
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
        'pending_commit': None,
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
        'pending_cognition_updates': [],
        'vetoed_experiences': [],
        'recent_ui_runs': [],
    }


_INJECTION_RE = re.compile(
    r'(?i)(ignore\s+(all\s+)?(previous|prior|above)\s+instructions?|'
    r'disregard\s+(all\s+)?(previous|prior)\s+instructions?|'
    r'system\s+prompt|you\s+must\s+always|always\s+obey|'
    r'忽略(以上|之前|前面)(所有)?指令|无视(以上|之前)规则|覆盖系统提示|写入\s*(facts|growth))'
)


def _strip_injection_lines(text):
    lines = []
    for line in str(text or '').splitlines():
        if not _INJECTION_RE.search(line):
            lines.append(line)
    return '\n'.join(lines)


def _normalise_experience(row):
    """Project old p7.experience.v1 rows into the additive S1 contract without migrating files."""
    out = dict(row or {})
    exp_type = str(out.get('experience_type') or '')
    out['experience_type'] = exp_type if exp_type in _VALID_EXP_TYPES else 'unknown_legacy'
    domain = str(out.get('domain') or '')
    inferred = _KIND_DOMAINS.get(str(out.get('kind') or ''), '')
    out['domain'] = domain if domain in _VALID_EXP_DOMAINS else inferred
    exceptions = out.get('exceptions')
    out['exceptions'] = [str(item).strip()[:120] for item in exceptions[:4]
                         if isinstance(item, str) and item.strip()] if isinstance(exceptions, list) else []
    tier = str(out.get('origin_evidence_tier') or '')
    out['origin_evidence_tier'] = tier if tier in _VALID_EVIDENCE_TIERS else 'L4_self_eval'
    promoter = str(out.get('promoted_by') or '')
    out['promoted_by'] = promoter if promoter in _VALID_PROMOTERS else ''
    out['locked'] = out.get('locked') is True
    evidence_state = str(out.get('evidence_state') or '')
    out['evidence_state'] = evidence_state if evidence_state in _VALID_EVIDENCE_STATES else 'unavailable'
    fingerprints = out.get('evidence_fingerprints')
    out['evidence_fingerprints'] = {
        str(key): str(value) for key, value in fingerprints.items()
        if isinstance(key, str) and isinstance(value, str) and value
    } if isinstance(fingerprints, dict) else {}
    refs = [str(item) for item in (out.get('evidence_refs') or ()) if isinstance(item, str)]
    stored_core = out.get('core_evidence_refs')
    out['core_evidence_refs'] = [str(item) for item in stored_core
                                 if isinstance(item, str) and item in refs] \
        if isinstance(stored_core, list) else []
    decision = str(out.get('decision') or '')
    out['decision'] = decision if decision in ('confirm', 'veto', 'outdated', 'restore') else ''
    out['trigger_embedding'] = out.get('trigger_embedding') if isinstance(out.get('trigger_embedding'), dict) else None
    verification = out.get('verification') if isinstance(out.get('verification'), dict) else {}
    def count(key):
        value = verification.get(key)
        return value if type(value) is int and value >= 0 else 0

    out['verification'] = {
        'exposed_count': count('exposed_count'),
        'explicit_rebutted_count': count('explicit_rebutted_count'),
        'explicit_suspended_count': count('explicit_suspended_count'),
        'positive_outcomes': count('positive_outcomes'),
        'negative_outcomes': count('negative_outcomes'),
        'consecutive_negatives': count('consecutive_negatives'),
        'last_exposed_at': str(verification.get('last_exposed_at') or ''),
        'last_verified_at': str(verification.get('last_verified_at') or ''),
        'suspension_until': str(verification.get('suspension_until') or ''),
        'review_flag': verification.get('review_flag') is True,
    }
    return out


def _stamp_after(left, right):
    left_dt, right_dt = _parse_dt(left), _parse_dt(right)
    if left_dt is None:
        return False
    if right_dt is None:
        return True
    return left_dt > right_dt


_GOVERNANCE_FIELDS = ('status', 'decision', 'decided_by', 'decided_at', 'updated_at', 'note')


def _adopt_governance(row, source):
    for key in _GOVERNANCE_FIELDS:
        if key in source:
            row[key] = source[key]
        else:
            row.pop(key, None)


def _resolve_governance_winner(row, disk_row):
    """Keep the newer veto/restore decision when two snapshots disagree."""
    row_status = str(row.get('status') or '')
    disk_status = str(disk_row.get('status') or '')
    if row_status == disk_status:
        return
    row_stamp = row.get('decided_at') or row.get('updated_at')
    disk_stamp = disk_row.get('decided_at') or disk_row.get('updated_at')
    if disk_status == 'vetoed' and row_status != 'vetoed':
        if row.get('decision') == 'restore' and _stamp_after(row_stamp, disk_stamp):
            return
        if not row_stamp or _stamp_after(disk_stamp, row_stamp):
            _adopt_governance(row, disk_row)
        return
    if row_status == 'vetoed' and disk_status != 'vetoed':
        if disk_row.get('decision') in ('restore', 'confirm') and _stamp_after(disk_stamp, row_stamp):
            _adopt_governance(row, disk_row)
        return
    if _stamp_after(disk_stamp, row_stamp):
        _adopt_governance(row, disk_row)


def _embedding_generation(cache):
    generation = cache.get('generation') if isinstance(cache, dict) else None
    return generation if type(generation) is int and generation >= 0 else 0


def _merge_candidate_embedding(row, disk_row):
    """Preserve the latest locked cache write, independently of wall-clock time."""
    normalized = _normalise_experience(row)
    text = (f"{normalized.get('trigger_condition', '')} | {normalized.get('domain', '')}"
            f" | {normalized.get('experience_type', '')}")
    fingerprint = hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()
    current = row.get('trigger_embedding') if isinstance(row.get('trigger_embedding'), dict) else {}
    disk = disk_row.get('trigger_embedding') if isinstance(disk_row.get('trigger_embedding'), dict) else {}
    current_valid = (current.get('fingerprint') == fingerprint
                     and type(current.get('dimension')) is int
                     and isinstance(current.get('vector'), list)
                     and len(current['vector']) == current['dimension'])
    disk_valid = (disk.get('fingerprint') == fingerprint
                  and type(disk.get('dimension')) is int
                  and isinstance(disk.get('vector'), list)
                  and len(disk['vector']) == disk['dimension'])
    # Generations are allocated only by cache_experience_embedding under write_lock.
    # Equal generations (including legacy caches at 0) keep the committed disk value.
    disk_is_newer = (disk_valid and (
        not current_valid
        or _embedding_generation(disk) >= _embedding_generation(current)
    ))
    if disk_is_newer:
        row['trigger_embedding'] = dict(disk)
    elif not current_valid:
        row.pop('trigger_embedding', None)


def _merge_governance_state(state, disk):
    """Merge an older in-memory snapshot with the state currently on disk.

    `_save_state()` may run after a long consolidation pass started from an old snapshot.
    Re-reading the on-disk governance fields under the write lock keeps permanent vetoes
    and later explicit restores monotonic even across processes/instances.
    """
    if not isinstance(disk, dict) or not isinstance(state, dict):
        return state
    disk_rows = {row.get('id'): row for row in (disk.get('experience_candidates') or [])
                 if isinstance(row, dict) and row.get('id')}
    rows = [row for row in (state.get('experience_candidates') or []) if isinstance(row, dict)]
    seen = {row.get('id') for row in rows}
    for row_id, disk_row in disk_rows.items():
        if row_id not in seen:
            rows.append(dict(disk_row))
    for row in rows:
        disk_row = disk_rows.get(row.get('id'))
        if disk_row is not None and disk_row is not row:
            _resolve_governance_winner(row, disk_row)
            _merge_candidate_embedding(row, disk_row)
    state['experience_candidates'] = rows
    ledger = []
    for source in (disk.get('vetoed_experiences'), state.get('vetoed_experiences')):
        for value in source if isinstance(source, list) else ():
            if (isinstance(value, str) and value.strip()
                    and not any(_matches_governed_claim(value, old) for old in ledger)):
                ledger.append(value)
    for row in rows:
        if row.get('decision') != 'restore':
            continue
        guidance = str(row.get('guidance') or '')
        if guidance:
            ledger = [value for value in ledger
                      if not _matches_governed_claim(guidance, value)]
    state['vetoed_experiences'] = ledger
    return state


def _redact_run_for_log(record):
    """Strip personal cognitive prose from append-only operational run logs."""
    res = record.get('results') if isinstance(record.get('results'), dict) else {}
    redacted_res = {}
    if res:
        redacted_res = {
            'reverify': dict(res.get('reverify') or {}),
            'rejected_ungrounded': int(res.get('rejected_ungrounded') or 0),
            'skipped_duplicates': int(res.get('skipped_duplicates') or 0),
            'cognition_updates': [
                {
                    'kind': u.get('kind', ''),
                    'section': u.get('section', ''),
                    'item_id': u.get('item_id', ''),
                    'evidence_refs': list(u.get('evidence_refs') or []),
                    'requested_status': u.get('requested_status', ''),
                    'governance_outcome': u.get('governance_outcome', ''),
                    'effective_status': u.get('effective_status', ''),
                }
                for u in (res.get('cognition_updates') or []) if isinstance(u, dict)
            ],
            'importance_proposals': [
                {
                    'memory_id': p.get('memory_id', ''),
                    'current_importance': p.get('current_importance'),
                    'proposed_importance': p.get('proposed_importance'),
                    'evidence_refs': list(p.get('evidence_refs') or []),
                    'outcome': p.get('outcome', ''),
                    'proposal_id': p.get('proposal_id', ''),
                    'applied': bool(p.get('applied')),
                    'version_seq': p.get('version_seq'),
                }
                for p in (res.get('importance_proposals') or []) if isinstance(p, dict)
            ],
            'experience_candidates': [
                {
                    'id': e.get('id', ''),
                    'kind': e.get('kind', ''),
                    'status': e.get('status', ''),
                    'supersedes_id': e.get('supersedes_id', ''),
                    'outcome': e.get('outcome', ''),
                    'evidence_refs': list(e.get('evidence_refs') or []),
                }
                for e in (res.get('experience_candidates') or []) if isinstance(e, dict)
            ],
        }
    return {
        'run_id': record.get('run_id', ''),
        'started_at': record.get('started_at', ''),
        'finished_at': record.get('finished_at', ''),
        'trigger': record.get('trigger', ''),
        'status': record.get('status', ''),
        'reason': record.get('reason', ''),
        'batch_key': record.get('batch_key', ''),
        'policy_version': record.get('policy_version', POLICY_VERSION),
        'inputs': dict(record.get('inputs') or {}),
        'results': redacted_res,
        'cost_cny': record.get('cost_cny', 0.0),
        'prompt_tokens': record.get('prompt_tokens'),
        'completion_tokens': record.get('completion_tokens'),
        'cached_tokens': record.get('cached_tokens'),
        'cache_status': record.get('cache_status'),
        'duration_ms': record.get('duration_ms'),
    }


class CognitiveConsolidator:
    """Role-scoped background cognitive consolidation with S3/S6-1/S6-2 collaboration and P7 contract."""

    def __init__(self, cfg, store, budget, model, *, memory=None, profile=None, semantic_verifier=None, now_fn=None):
        self.cfg = cfg
        self.store = store
        self.budget = budget
        self.model = model
        self.memory = memory
        self.profile = profile
        self.semantic_verifier = semantic_verifier
        self._now = now_fn or _now
        self.mutex = threading.RLock()
        self._run_lock = threading.Lock()
        self._settings = None
        self._settings_sig = None
        self._scheduler = None
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

    def settings_revision(self):
        with self.mutex:
            raw = _load_json_dict(self._settings_file())
            rev = raw.get('_revision')
            return rev if (type(rev) is int and rev >= 1) else 1

    def update_settings(self, values, *, expected_revision=None):
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
            raw = _load_json_dict(self._settings_file())
            cur_rev = raw.get('_revision')
            cur_rev = cur_rev if (type(cur_rev) is int and cur_rev >= 1) else 1
            if expected_revision is not None and int(expected_revision) != cur_rev:
                return {
                    'ok': False,
                    'error': '配置已在其他窗口被更新，请刷新后再保存。',
                    'status_code': 409,
                    'settings_revision': cur_rev,
                    'settings': dict(self.settings()),
                }
            current = load_persisted(self.store, self.cfg.id)
            next_rev = cur_rev + 1 if updates else cur_rev
            merged = {**current, **updates, '_revision': next_rev}
            with write_lock(self.store, self.cfg.id):
                self.store.replace(self.settings_path(), merged)
            self._settings = settings_from(merged)
            self._settings_sig = self._sig()
            return {
                'ok': True,
                'settings': dict(self._settings),
                'settings_revision': next_rev,
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

    def _save_state(self, state, *, active_memory_ids=None, active_autonomy_ids=None, lock=True):
        # Bound auxiliary maps without evicting live active memory/autonomy fingerprints.
        for key, cap in (
            ('processed_batches', MAX_PROCESSED_BATCHES),
            ('importance_proposed', MAX_IMPORTANCE_LEDGER),
        ):
            mapping = state.get(key)
            if isinstance(mapping, dict) and len(mapping) > cap:
                keys = list(mapping.keys())[-cap:]
                state[key] = {k: mapping[k] for k in keys}
        pm = state.get('processed_memories')
        if isinstance(pm, dict) and len(pm) > MAX_PROCESSED_MEMORIES and active_memory_ids is not None:
            active_set = set(active_memory_ids)
            state['processed_memories'] = {k: v for k, v in pm.items() if k in active_set}
        pa = state.get('processed_autonomy')
        if isinstance(pa, dict) and len(pa) > MAX_PROCESSED_AUTONOMY and active_autonomy_ids is not None:
            active_set = set(active_autonomy_ids)
            state['processed_autonomy'] = {k: v for k, v in pa.items() if k in active_set}

        # Never evict confirmed or vetoed experience candidates when bounding list size.
        exps = state.get('experience_candidates')
        if isinstance(exps, list) and len(exps) > MAX_EXPERIENCE_CANDIDATES:
            protected = [e for e in exps if isinstance(e, dict) and e.get('status') in ('confirmed', 'vetoed')]
            uncertain = [e for e in exps if isinstance(e, dict) and e.get('status') == 'uncertain']
            outdated = [e for e in exps if isinstance(e, dict) and e.get('status') == 'outdated']
            room = max(0, MAX_EXPERIENCE_CANDIDATES - len(protected))
            keep_unc = uncertain[-room:] if room > 0 else []
            rem = max(0, room - len(keep_unc))
            keep_out = outdated[-rem:] if rem > 0 else []
            state['experience_candidates'] = protected + keep_out + keep_unc

        ui_runs = state.get('recent_ui_runs')
        if isinstance(ui_runs, list) and len(ui_runs) > 16:
            state['recent_ui_runs'] = ui_runs[-16:]

        pend_cogs = state.get('pending_cognition_updates')
        if isinstance(pend_cogs, list) and len(pend_cogs) > MAX_PENDING_COGNITION:
            open_items = [p for p in pend_cogs if isinstance(p, dict) and p.get('status') == 'pending']
            closed_items = [p for p in pend_cogs if isinstance(p, dict) and p.get('status') != 'pending']
            room = max(0, MAX_PENDING_COGNITION - len(open_items))
            state['pending_cognition_updates'] = open_items + (closed_items[-room:] if room > 0 else [])

        if lock:
            with write_lock(self.store, self.cfg.id):
                self._write_state_locked(state)
        else:
            self._write_state_locked(state)

    def _write_state_locked(self, state):
        """Write state while preserving every newer veto/restore decision on disk."""
        disk = _load_json_dict(self._state_file())
        state = _merge_governance_state(state, disk)
        self.store.replace(self.state_path(), state)

    def _append_run(self, record):
        self.store.append(self.runs_path(), _redact_run_for_log(record))

    def recent_runs(self, limit=20):
        try:
            rows = self.store.read_tail(self.runs_path(), max(1, min(100, int(limit or 20))))
        except Exception:
            return []
        return list(reversed([r for r in rows if isinstance(r, dict)]))

    def _reconcile_role_reservation(self, request_id=''):
        reconcile = getattr(self.budget, 'reconcile_unsettled', None)
        if not callable(reconcile):
            return
        try:
            req_ids = {request_id} if request_id else None
            reconcile({COST_ACCOUNTING}, character=self.cfg.id, request_ids=req_ids)
        except TypeError:
            try:
                reconcile({COST_ACCOUNTING})
            except Exception:
                pass
        except Exception:
            pass

    def recover_interrupted(self):
        """Recover cleanly from an interrupted in-flight run scoped strictly to this role.

        If a durable `pending_commit` checkpoint already captured the completed model output
        before the crash, preserve it so the next run resumes committing at ¥0 without
        re-calling or re-billing the consolidation model.
        """
        with self.mutex:
            state = self._load_state()
            in_flight = state.get('in_flight')
            if not isinstance(in_flight, dict):
                return False
            req_id = str(in_flight.get('request_id') or '').strip()
            phase = str(in_flight.get('phase') or '').strip()
            has_pending = isinstance(state.get('pending_commit'), dict)
            # Only reconcile unsettled reservations when the model call hadn't already settled.
            if not has_pending or phase == 'dispatched':
                self._reconcile_role_reservation(req_id)
            stamp = self._now()
            today = stamp[:10]
            if phase in ('dispatched', 'applying', 'apply_failed') and not in_flight.get('counted_run'):
                if state.get('day') == today:
                    state['runs_today'] = int(state.get('runs_today') or 0) + 1
                else:
                    state['day'] = today
                    state['runs_today'] = 1
            failures = int(state.get('consecutive_failures') or 0) + 1
            state['in_flight'] = None
            state['last_run_at'] = stamp
            state['last_status'] = 'pending_commit' if has_pending else 'interrupted'
            state['last_reason'] = (
                'interrupted_during_apply_resumable' if has_pending
                else f'recovered_after_restart:{phase or "unknown"}'
            )
            state['consecutive_failures'] = failures
            if not has_pending:
                backoff_min = min(120, 15 * (2 ** min(failures - 1, 3)))
                now_dt = _parse_dt(stamp)
                if now_dt is not None:
                    state['cooldown_until'] = (now_dt + timedelta(minutes=backoff_min)).isoformat(timespec='seconds')
            state['totals']['runs'] += 1
            state['totals']['failed'] += 1
            rec = {
                'run_id': str(in_flight.get('run_id') or uuid.uuid4().hex[:12]),
                'started_at': str(in_flight.get('started_at') or stamp),
                'finished_at': stamp,
                'trigger': str(in_flight.get('trigger') or 'scheduled'),
                'status': state['last_status'],
                'reason': state['last_reason'],
                'batch_key': str(in_flight.get('batch_key') or ''),
                'policy_version': POLICY_VERSION,
                'inputs': in_flight.get('inputs') or {},
                'results': {},
                'cost_cny': 0.0,
            }
            state.setdefault('recent_ui_runs', []).append(rec)
            self._save_state(state)
            self._append_run(rec)
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
            pending_deletes = set()
            if 'delete_requests' in tables:
                for dr in conn.execute("SELECT memory_id FROM delete_requests WHERE status='pending'"):
                    if dr[0]:
                        pending_deletes.add(str(dr[0]))

            locked_mems = set()
            if 'sidecar_state' in tables:
                for lr in conn.execute('SELECT memory_id FROM sidecar_state WHERE locked=1'):
                    if lr[0]:
                        locked_mems.add(str(lr[0]))

            rows = conn.execute(
                'SELECT id, title, body, body_sha256, created, last_active, '
                'type, bucket, importance, pinned, meaning, why_remembered '
                'FROM memories WHERE is_tombstone=0 AND is_deleted=0 '
                'ORDER BY created DESC, id DESC'
            ).fetchall()

            events_by_mem = {}
            stage_counts = {}
            if 'events' in tables:
                ev_cols = {r[1] for r in conn.execute('PRAGMA table_info(events)')}
                for er in conn.execute('SELECT * FROM events WHERE memory_id IS NOT NULL'):
                    rec = {'event_id': er['event_id'], 'memory_id': er['memory_id']}
                    for opt in ('title', 'story', 'first_at', 'last_at', 'updated_at'):
                        rec[opt] = er[opt] if opt in ev_cols else ''
                    events_by_mem[er['memory_id']] = rec
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
                if mid in pending_deletes:
                    continue
                if str(row['bucket'] or '') == 'archive' or str(row['type'] or '') == 'archived':
                    continue
                ev = events_by_mem.get(mid)
                ev_id = ev['event_id'] if ev else ''
                sc = stage_counts.get(ev_id, 0) if ev_id else 0
                act = activation_by_mem.get(mid) or {}
                chat_refs = set(user_chat_refs.get(mid) or ())
                if ev_id:
                    chat_refs.update(user_chat_refs.get('ev:' + ev_id) or ())
                fp = _sha([
                    mid,
                    str(row['title'] or ''),
                    row['body_sha256'] or hashlib.sha256((row['body'] or '').encode('utf-8')).hexdigest()[:16],
                    str(row['meaning'] or ''),
                    str(row['why_remembered'] or ''),
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
                    'locked': mid in locked_mems,
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
        """Read full autonomy history and strictly separate audited-clean / unclean / unaudited rows.

        Only explicitly audited-clean rows (`output_audit.clean is True` with no issues) are
        admitted as verified autonomous experiences; both explicitly unclean and unaudited/malformed
        rows are excluded and counted in diagnostics.
        """
        stats = {'audited_clean': 0, 'excluded_unclean': 0, 'excluded_unaudited': 0}
        try:
            rows = self.store.read(f'{self.cfg.id}/autonomy.jsonl')
        except Exception:
            return [], stats
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            started = str(row.get('started_at') or '').strip()
            if not started:
                continue
            audit = row.get('output_audit')
            if isinstance(audit, dict) and audit.get('clean') is False:
                stats['excluded_unclean'] += 1
                continue
            issues = audit.get('issues') if isinstance(audit, dict) else None
            if not (isinstance(audit, dict) and audit.get('clean') is True and (issues is None or (isinstance(issues, list) and not issues))):
                stats['excluded_unaudited'] += 1
                continue
            stats['audited_clean'] += 1
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
                'audit_status': 'audited_clean',
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
        return out, stats

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

        auto_rows, auto_audit_stats = self._read_autonomy_experiences()
        new_auto = [
            row for row in auto_rows
            if processed_auto.get(row['started_at']) != row['fingerprint']
            and not _contains_secret(row['decision'], row['focus'], row['next_step'])
        ]
        # Process oldest unprocessed audited autonomy rows first so earlier records never starve.
        limit_auto = int(values['max_autonomy_items'])
        chosen_auto = new_auto[:limit_auto] if limit_auto > 0 else []

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
        auto_by_id = {}
        for mem in (*chosen_new, *chosen_anchors):
            mem_by_id[mem['id']] = mem
            batch_refs.add('memory:' + mem['id'])
            if mem['event_id']:
                batch_refs.add('event:' + mem['event_id'])
            for cref in mem['chat_refs']:
                batch_refs.add(cref)
        for auto in chosen_auto:
            auto_by_id[auto['id']] = auto
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
            'auto_by_id': auto_by_id,
            'new_count': len(chosen_new) + len(chosen_auto),
            'autonomy_audit_stats': auto_audit_stats,
            'active_memory_ids': [m['id'] for m in memories],
            'active_autonomy_ids': [a['started_at'] for a in auto_rows],
            'trimmed_new_count': 0,
            'trimmed_auto_count': 0,
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

    @staticmethod
    def _build_sent_batch(batch, cur_new, cur_anc, cur_auto):
        """Rebuild exact batch metadata matching the trimmed subset actually sent to the model."""
        sent_new = list(batch['new_memories'][:cur_new])
        sent_anc = list(batch['anchor_memories'][:cur_anc])
        sent_auto = list(batch['autonomy_items'][:cur_auto])
        existing = batch['existing_cognition']
        batch_refs = set()
        mem_by_id = {}
        auto_by_id = {}
        for mem in (*sent_new, *sent_anc):
            mem_by_id[mem['id']] = mem
            batch_refs.add('memory:' + mem['id'])
            if mem['event_id']:
                batch_refs.add('event:' + mem['event_id'])
            for cref in mem['chat_refs']:
                batch_refs.add(cref)
        for auto in sent_auto:
            auto_by_id[auto['id']] = auto
            batch_refs.add(auto['id'])
        batch_key = _sha([
            [(m['id'], m['fingerprint']) for m in sent_new],
            [(a['started_at'], a['fingerprint']) for a in sent_auto],
            _sha([existing['facts'], existing['growth'], [r['text'] for r in existing['relationship_items']]])[:12],
        ])[:24]
        return {
            **batch,
            'batch_key': batch_key,
            'new_memories': sent_new,
            'anchor_memories': sent_anc,
            'autonomy_items': sent_auto,
            'batch_refs': batch_refs,
            'mem_by_id': mem_by_id,
            'auto_by_id': auto_by_id,
            'new_count': len(sent_new) + len(sent_auto),
            'trimmed_new_count': len(batch['new_memories']) - len(sent_new),
            'trimmed_auto_count': len(batch['autonomy_items']) - len(sent_auto),
        }

    def _fit_messages(self, system, batch, max_bytes):
        """Adaptively bound payload size (UTF-8 bytes) and return the exact sent subset."""
        from veil.core.veil_agent import request_byte_size

        new_n = len(batch['new_memories'])
        anc_n = len(batch['anchor_memories'])
        auto_n = len(batch['autonomy_items'])
        for excerpt_chars in (260, 180, 120):
            cur_new, cur_anc, cur_auto = new_n, anc_n, auto_n
            for _ in range(16):
                payload = self._build_payload(
                    batch, excerpt_chars=excerpt_chars,
                    new_limit=cur_new, anchor_limit=cur_anc, auto_limit=cur_auto,
                )
                if _contains_secret(payload, system):
                    return None, 0, 'sensitive_payload', None
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                messages = [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': body},
                ]
                size = request_byte_size(messages)
                if size <= max_bytes:
                    sent_batch = self._build_sent_batch(batch, cur_new, cur_anc, cur_auto)
                    return messages, size, '', sent_batch
                if cur_anc > 0:
                    cur_anc = max(0, cur_anc - 2)
                elif cur_auto > 1:
                    cur_auto = max(1, cur_auto - 2)
                elif cur_new > 1:
                    cur_new = max(1, cur_new - 2)
                elif cur_auto > 0 and cur_new > 0:
                    cur_auto = 0
                else:
                    break
        return None, 0, 'payload_too_large', None

    @staticmethod
    def _normalize_cache_usage(cached_tok):
        if cached_tok is None:
            return None, 'unreported'
        if hasattr(cached_tok, 'tokens'):
            toks = cached_tok.tokens
            status = str(getattr(cached_tok, 'status', '') or ('reported' if toks is not None else 'unreported'))
            return (toks if type(toks) is int and toks >= 0 else None), status
        if type(cached_tok) is int and cached_tok >= 0:
            return cached_tok, 'reported'
        return None, 'unreported'

    def _call_model(self, batch, cap, timeout, stopped, *, state):
        from veil.core.veil_agent import RequestNotSent

        if self.model is None or getattr(self.cfg, 'provider', {}).get('missing'):
            return {'ok': False, 'status': 'failed', 'reason': 'model_unavailable', 'dispatched': False, 'sent_batch': batch}

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
        messages, input_bytes, fit_err, sent_batch = self._fit_messages(system, batch, max_bytes)
        if fit_err or sent_batch is None:
            return {'ok': False, 'status': 'failed', 'reason': fit_err or 'payload_too_large', 'dispatched': False, 'sent_batch': batch}
        provider['max_input_tokens'] = input_bytes
        request_cfg = replace(
            self.cfg,
            provider=provider,
            max_output_tokens=out_tokens,
        )
        if stopped():
            return {'ok': False, 'status': 'skipped', 'reason': 'stopped', 'dispatched': False, 'sent_batch': sent_batch}
        try:
            reservation = self.budget.reserve(
                request_cfg, category=COST_CATEGORY,
                accounting=COST_ACCOUNTING, category_daily=cap,
            )
        except BudgetExceeded:
            return {'ok': False, 'status': 'budget_exhausted', 'reason': 'budget_exhausted', 'dispatched': False, 'sent_batch': sent_batch}
        except UserError:
            return {'ok': False, 'status': 'failed', 'reason': 'ledger_unavailable', 'dispatched': False, 'sent_batch': sent_batch}

        request_id, reserved_amount = reservation
        marked = False
        try:
            if stopped():
                return {'ok': False, 'status': 'skipped', 'reason': 'stopped', 'dispatched': False, 'sent_batch': sent_batch}
            with self.mutex:
                fresh = self._load_state()
                if isinstance(fresh.get('in_flight'), dict):
                    fresh['in_flight']['request_id'] = request_id
                    fresh['in_flight']['phase'] = 'dispatched'
                    fresh['in_flight']['batch_key'] = sent_batch['batch_key']
                    fresh['in_flight']['inputs'] = {
                        'new_memory_ids': [m['id'] for m in sent_batch['new_memories']],
                        'anchor_memory_ids': [m['id'] for m in sent_batch['anchor_memories']],
                        'autonomy_ids': [a['id'] for a in sent_batch['autonomy_items']],
                    }
                    self._save_state(fresh)
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
            return {
                'ok': False, 'status': 'failed', 'reason': reason,
                'dispatched': True, 'sent_batch': sent_batch,
                'cost_cny': float(reserved_amount),
            }

        start = time.monotonic()
        try:
            output, prompt_tok, completion_tok, cached_tok, stop_reason = self.model(
                messages, json_output=True, max_tokens=request_cfg.max_output_tokens,
                thinking='disabled', detail=True, request_timeout=timeout,
            )
        except RequestNotSent:
            self.budget.release_not_sent(request_id)
            return {
                'ok': False, 'status': 'failed', 'reason': 'request_not_sent',
                'dispatched': False, 'sent_batch': sent_batch, 'cost_cny': 0.0,
            }
        except (TimeoutError, APITimeoutError):
            return retain_upper('timeout')
        except Exception:
            return retain_upper('provider_error')

        late = time.monotonic() - start > timeout
        cached_val, cache_status = self._normalize_cache_usage(cached_tok)
        try:
            settled_ok = self.budget.settle(
                request_id, request_cfg, prompt_tok, completion_tok, cached_tok,
                input_bytes=input_bytes, category=COST_CATEGORY, accounting=COST_ACCOUNTING,
            )
            if settled_ok is False:
                return {
                    'ok': False, 'status': 'failed', 'reason': 'settlement_conflict',
                    'dispatched': True, 'sent_batch': sent_batch,
                    'cost_cny': float(reserved_amount),
                    'prompt_tokens': prompt_tok, 'completion_tokens': completion_tok,
                    'cached_tokens': cached_val, 'cache_status': cache_status,
                }
            cached_count = cached_val if isinstance(cached_val, int) else 0
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
                'dispatched': True, 'sent_batch': sent_batch,
                'cost_cny': cost_val,
                'prompt_tokens': prompt_tok, 'completion_tokens': completion_tok,
                'cached_tokens': cached_val, 'cache_status': cache_status,
            }
        if stop_reason != 'stop':
            return {
                'ok': False, 'status': 'failed', 'reason': 'invalid_response',
                'dispatched': True, 'sent_batch': sent_batch,
                'cost_cny': cost_val,
                'prompt_tokens': prompt_tok, 'completion_tokens': completion_tok,
                'cached_tokens': cached_val, 'cache_status': cache_status,
            }
        try:
            parsed = parse_consolidation_output(output)
        except (ValueError, RecursionError):
            return {
                'ok': False, 'status': 'failed', 'reason': 'invalid_response',
                'dispatched': True, 'sent_batch': sent_batch,
                'cost_cny': cost_val,
                'prompt_tokens': prompt_tok, 'completion_tokens': completion_tok,
                'cached_tokens': cached_val, 'cache_status': cache_status,
            }
        return {
            'ok': True,
            'parsed': parsed,
            'dispatched': True,
            'sent_batch': sent_batch,
            'cost_cny': cost_val,
            'prompt_tokens': prompt_tok,
            'completion_tokens': completion_tok,
            'cached_tokens': cached_val,
            'cache_status': cache_status,
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

    @staticmethod
    def _verify_content_evidence(section, text, valid_refs, mem_by_id, auto_by_id):
        """Content-level grounding check for facts/growth against cited batch evidence.

        Guards against indirect prompt injection inside a memory body poisoning facts/growth
        with claims unsupported by the actual non-directive content of cited sources.
        """
        if _INJECTION_RE.search(text or ''):
            return False
        cand_tokens = _lexical_tokens(text)
        if not cand_tokens:
            return False

        cited_mems = []
        seen_mids = set()
        ev_texts = []
        has_user_chat_origin = False

        for ref in valid_refs:
            if ref.startswith('memory:'):
                mid = ref.split(':', 1)[1]
                mem = mem_by_id.get(mid)
                if mem and mid not in seen_mids:
                    seen_mids.add(mid)
                    cited_mems.append(mem)
                    if mem.get('chat_refs'):
                        has_user_chat_origin = True
                    ev_texts.append(_strip_injection_lines(
                        f"{mem.get('title', '')}\n{mem.get('body', '')}\n"
                        f"{mem.get('event_story', '')}\n{mem.get('meaning', '')}\n{mem.get('why_remembered', '')}"
                    ))
            elif ref.startswith('event:'):
                ev_id = ref.split(':', 1)[1]
                for mem in mem_by_id.values():
                    if mem.get('event_id') == ev_id and mem['id'] not in seen_mids:
                        seen_mids.add(mem['id'])
                        cited_mems.append(mem)
                        if mem.get('chat_refs'):
                            has_user_chat_origin = True
                        ev_texts.append(_strip_injection_lines(
                            f"{mem.get('title', '')}\n{mem.get('event_story', '')}\n{mem.get('body', '')}"
                        ))
            elif ref.startswith('chat:'):
                has_user_chat_origin = True
            elif ref.startswith('autonomy:') and section == 'growth':
                auto = auto_by_id.get(ref)
                if auto:
                    ev_texts.append(_strip_injection_lines(
                        f"{auto.get('decision', '')}\n{auto.get('focus', '')}\n{auto.get('next_step', '')}"
                    ))

        if section == 'facts':
            # High-trust facts require non-autonomy memory grounding AND either verified user-chat
            # origin or corroboration across >=2 distinct cited memories.
            if not cited_mems:
                return False
            if not has_user_chat_origin and len(cited_mems) < 2:
                return False

        ev_tokens = _lexical_tokens('\n'.join(ev_texts))
        if not ev_tokens:
            return False
        overlap = len(cand_tokens & ev_tokens)
        min_overlap = 1 if len(cand_tokens) <= 3 else 2
        return overlap >= min_overlap

    @staticmethod
    def _is_related_experience(old_item, new_exp):
        if old_item.get('kind') == new_exp.get('kind'):
            return True
        old_toks = _lexical_tokens(f"{old_item.get('trigger_condition', '')} {old_item.get('guidance', '')}")
        new_toks = _lexical_tokens(f"{new_exp.get('trigger_condition', '')} {new_exp.get('guidance', '')}")
        return bool(old_toks & new_toks)

    @staticmethod
    def _cognition_update_key(upd):
        section = upd.get('section', '')
        kind = upd.get('kind', '')
        text = upd.get('text', '')
        replace_old = upd.get('replace_old_text', '') if kind == 'revision' else ''
        return _sha([section, kind, _norm_text(text), _norm_text(replace_old)])[:16]

    def _build_semantic_verify_payload(self, upd, valid_refs, mem_by_id, auto_by_id, profile_data):
        """Construct an S3-compatible semantic verification payload for facts/growth."""
        section = upd['section']
        evidence = []
        seen_ev = set()
        for ref in valid_refs:
            if ref in seen_ev:
                continue
            txt = ''
            if ref.startswith('memory:'):
                mem = mem_by_id.get(ref.split(':', 1)[1])
                if mem:
                    txt = _strip_injection_lines(
                        f"{mem.get('title', '')}\n{mem.get('event_story') or mem.get('body', '')}"
                    )
            elif ref.startswith('event:'):
                ev_id = ref.split(':', 1)[1]
                for mem in mem_by_id.values():
                    if mem.get('event_id') == ev_id:
                        txt = _strip_injection_lines(
                            f"{mem.get('title', '')}\n{mem.get('event_story') or mem.get('body', '')}"
                        )
                        break
            elif ref.startswith('autonomy:') and section == 'growth':
                auto = auto_by_id.get(ref)
                if auto:
                    txt = _strip_injection_lines(f"{auto.get('decision', '')} {auto.get('focus', '')}")
            txt = _clip(txt, 500)
            if txt:
                seen_ev.add(ref)
                evidence.append({'id': ref, 'text': txt})

        counterevidence = []
        if isinstance(profile_data, dict):
            rel = profile_data.get('relationship') or {}
            for idx, v in enumerate((rel.get('vetoed') or [])[-12:]):
                vtxt = str(v.get('text') if isinstance(v, dict) else v or '').strip()
                if vtxt:
                    counterevidence.append({'id': f'veto:{idx}', 'text': _clip(vtxt, 240)})
            for item in (rel.get('items') or [])[:12]:
                if isinstance(item, dict) and item.get('locked') and item.get('text'):
                    counterevidence.append({'id': f"lock:{item.get('id', '')}", 'text': _clip(item['text'], 240)})
        return {
            'candidate': {'section': section, 'kind': upd['kind'], 'text': upd['text']},
            'evidence': evidence[:8],
            'counterevidence': counterevidence[:24],
        }

    def _preverify_facts_and_growth(self, parsed, batch, cached_verdicts, *, stopped=lambda: False):
        """Run S3 semantic verification for facts/growth OUTSIDE self.mutex.

        Lexical overlap is strictly a cheap pre-filter; only S3 semantic verification returning
        `supported` authorizes automatic admission into effective Learned Context.
        """
        verdicts = dict(cached_verdicts or {})
        batch_refs = batch['batch_refs']
        mem_by_id = batch['mem_by_id']
        auto_by_id = batch.get('auto_by_id') or {}
        profile_data = self._read_profile_raw()

        for upd in parsed.get('cognition_updates') or []:
            if upd.get('section') not in ('facts', 'growth'):
                continue
            key = self._cognition_update_key(upd)
            if key in verdicts:
                continue
            valid_refs = [r for r in upd.get('evidence_refs') or [] if r in batch_refs]
            if not valid_refs:
                continue
            # Cheap lexical pre-filter only (never sufficient for admission).
            if not self._verify_content_evidence(upd['section'], upd['text'], valid_refs, mem_by_id, auto_by_id):
                verdicts[key] = {'verdict': 'prefilter_rejected', 'reason': 'lexical_prefilter_failed'}
                continue
            verifier = self.semantic_verifier
            if verifier is None or not callable(getattr(verifier, 'verify_claim', None)):
                verdicts[key] = {'verdict': 'pending_review', 'reason': 'semantic_verifier_unavailable'}
                continue
            payload = self._build_semantic_verify_payload(upd, valid_refs, mem_by_id, auto_by_id, profile_data)
            if not payload['evidence']:
                verdicts[key] = {'verdict': 'pending_review', 'reason': 'missing_evidence_text'}
                continue
            try:
                res = verifier.verify_claim(payload, stopped=stopped)
            except Exception as exc:
                res = {'ok': False, 'verdict': 'failed', 'reason': type(exc).__name__}
            v = str((res or {}).get('verdict') or 'failed')
            reason = str((res or {}).get('reason') or v)
            if v == 'supported':
                verdicts[key] = {'verdict': 'supported', 'reason': ''}
            elif v == 'contradicted':
                verdicts[key] = {'verdict': 'contradicted', 'reason': reason or 'semantic_contradicted'}
            else:
                verdicts[key] = {'verdict': 'pending_review', 'reason': reason or 'semantic_unverified'}
        return verdicts

    def _apply_outputs(self, parsed, batch, state, values, run_id, *, semantic_verdicts=None):
        batch_refs = batch['batch_refs']
        mem_by_id = batch['mem_by_id']
        auto_by_id = batch.get('auto_by_id') or {}
        existing = self._existing_cognition_snapshot()

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
        retryable_refs = set()

        # 0. Periodically reverify existing relationship evidence (never mutates locked/lan items).
        if self.profile is not None and hasattr(self.profile, 'reverify_relationship_evidence'):
            try:
                rev_res = self.profile.reverify_relationship_evidence()
                summary['reverify'] = {'checked': True, 'changed': bool(rev_res.get('changed'))}
            except Exception:
                summary['reverify'] = {'checked': False, 'changed': False}

        # 1. Cognition updates -> S3 LearnedContext.submit_candidate() (zero parallel store).
        batch_seen_texts = list(existing_corpus)
        for upd in parsed['cognition_updates']:
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

            if section in ('facts', 'growth'):
                upd_key = self._cognition_update_key(upd)
                sem_res = (semantic_verdicts or {}).get(upd_key)
                if sem_res is None:
                    # Fallback when _apply_outputs is invoked directly without preverification:
                    # lexical check can only reject; without S3 semantic confirmation hold for review.
                    if not self._verify_content_evidence(section, text, valid_refs, mem_by_id, auto_by_id):
                        sem_res = {'verdict': 'prefilter_rejected', 'reason': 'lexical_prefilter_failed'}
                    else:
                        sem_res = {'verdict': 'pending_review', 'reason': 'semantic_verifier_unavailable'}
                verdict = sem_res.get('verdict', 'pending_review')
                v_reason = sem_res.get('reason', '')
                if verdict == 'prefilter_rejected':
                    summary['rejected_ungrounded'] += 1
                    summary['cognition_updates'].append({
                        'kind': kind, 'section': section, 'text': text,
                        'replace_old_text': replace_old, 'reason': upd['reason'],
                        'evidence_refs': valid_refs, 's3_sources': [],
                        'requested_status': 'uncertain',
                        'governance_outcome': 'rejected_ungrounded_content',
                        'effective_status': 'rejected_ungrounded_content',
                        'message': v_reason or 'content failed lexical prefilter',
                        'item_id': '',
                    })
                    continue
                if verdict == 'contradicted':
                    summary['rejected_ungrounded'] += 1
                    summary['cognition_updates'].append({
                        'kind': kind, 'section': section, 'text': text,
                        'replace_old_text': replace_old, 'reason': upd['reason'],
                        'evidence_refs': valid_refs, 's3_sources': [],
                        'requested_status': 'uncertain',
                        'governance_outcome': 'rejected_semantic_contradicted',
                        'effective_status': 'rejected_semantic_contradicted',
                        'message': v_reason or 'contradicted by S3 semantic verification',
                        'item_id': '',
                    })
                    continue
                if verdict != 'supported':
                    # Never write unverified facts/growth into effective Learned Context; hold pending review.
                    pend_list = state.setdefault('pending_cognition_updates', [])
                    vetoed_ledger = state.get('vetoed_experiences') or []
                    if any(isinstance(v, str) and _matches_governed_claim(text, v) for v in vetoed_ledger):
                        summary['cognition_updates'].append({
                            'kind': kind, 'section': section, 'text': text,
                            'replace_old_text': replace_old, 'reason': upd['reason'],
                            'evidence_refs': valid_refs, 's3_sources': [],
                            'requested_status': 'uncertain',
                            'governance_outcome': 'blocked_sovereignty',
                            'effective_status': 'blocked_sovereignty',
                            'message': 'previously rejected by Lan',
                            'item_id': '',
                        })
                        state['totals']['cognition_blocked'] += 1
                        continue
                    existing_pend = next(
                        (p for p in pend_list if isinstance(p, dict)
                         and p.get('content_key') == upd_key and p.get('status') == 'pending'),
                        None,
                    )
                    if existing_pend is None:
                        pend_id = 'cog-' + uuid.uuid4().hex[:10]
                        now_stamp = self._now()
                        pend_list.append({
                            'id': pend_id,
                            'content_key': upd_key,
                            'kind': kind,
                            'section': section,
                            'text': text,
                            'replace_old_text': replace_old,
                            'reason': upd['reason'],
                            'evidence_refs': valid_refs,
                            's3_sources': self._expand_s3_sources(valid_refs, mem_by_id, section=section),
                            'status': 'pending',
                            'verify_reason': v_reason or 'semantic_unverified',
                            'run_id': run_id,
                            'created_at': now_stamp,
                            'updated_at': now_stamp,
                        })
                    else:
                        pend_id = existing_pend['id']
                    summary['cognition_updates'].append({
                        'kind': kind, 'section': section, 'text': text,
                        'replace_old_text': replace_old, 'reason': upd['reason'],
                        'evidence_refs': valid_refs, 's3_sources': [],
                        'requested_status': 'uncertain',
                        'governance_outcome': 'pending_review',
                        'effective_status': 'pending_review',
                        'message': v_reason or 'held pending S3 verification or Lan approval',
                        'item_id': pend_id,
                    })
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
                # Content-deterministic idempotency key survives partial crashes & batch shifts.
                idem_key = f"cons:{section}:{kind}:{_sha([_norm_text(text), _norm_text(replace_old)])[:16]}"
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
                        retryable_refs.update(valid_refs)
                    else:
                        entry['governance_outcome'] = 'rejected'
                        entry['effective_status'] = 'rejected'
            else:
                retryable_refs.update(valid_refs)
            summary['cognition_updates'].append(entry)

        # Refresh relationship items after S3 writes so S6-1 can see newly confirmed refs.
        refreshed_rel = self._existing_cognition_snapshot()['relationship_items']

        # 2. Importance proposals -> S6-1 MemoryWeights.propose() (no bypass, crash-idempotent).
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
                recent_hist = weights.history(mid, limit=10)
                if any(
                    h.get('status') == 'rejected'
                    and h.get('proposed_importance') == prop['proposed_importance']
                    for h in recent_hist
                ):
                    item_rec['outcome'] = 'skipped_previously_rejected'
                    summary['skipped_duplicates'] += 1
                    summary['importance_proposals'].append(item_rec)
                    continue
                if any(
                    h.get('trigger') == 'consolidation'
                    and h.get('status') in ('pending', 'applied')
                    and h.get('proposed_importance') == prop['proposed_importance']
                    for h in recent_hist
                ):
                    item_rec['outcome'] = 'skipped_already_proposed'
                    imp_ledger[mid] = {
                        'evidence_fp': ev_fp,
                        'proposed_at': self._now(),
                        'proposal_id': '',
                        'proposed_importance': prop['proposed_importance'],
                    }
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
        vetoed_ledger = state.setdefault('vetoed_experiences', [])
        profile_data = self._read_profile_raw()
        for exp in parsed['experience_candidates']:
            valid_refs = [r for r in exp['evidence_refs'] if r in batch_refs]
            if not valid_refs:
                summary['rejected_ungrounded'] += 1
                continue
            # Honor Lan vetoes in LearnedContext, P7 candidate list, AND permanent veto ledger.
            if self._is_vetoed_experience(exp['guidance'], exp_list, profile_data, vetoed_ledger):
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

            # Dedup against active P7 candidates (including crash-replay identical candidates).
            supersedes_id = exp['supersedes_id']
            content_key = _sha([exp['kind'], _norm_text(exp['trigger_condition']), _norm_text(exp['guidance'])])[:16]
            dup = next(
                (
                    e for e in exp_list
                    if isinstance(e, dict)
                    and e.get('status') in ('confirmed', 'uncertain')
                    and (
                        e.get('content_key') == content_key
                        or (
                            e.get('id') != supersedes_id
                            and (_norm_text(e.get('guidance')) == _norm_text(exp['guidance'])
                                 or _jaccard_similar(e.get('guidance', ''), exp['guidance']))
                        )
                    )
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
                cand_target = next(
                    (
                        e for e in exp_list
                        if isinstance(e, dict) and e.get('id') == supersedes_id
                        and e.get('status') in ('confirmed', 'uncertain')
                    ),
                    None,
                )
                if cand_target is not None and self._is_related_experience(cand_target, exp):
                    superseded_target = cand_target

            record = {
                'id': new_id,
                'schema_version': EXPERIENCE_SCHEMA_VERSION,
                'content_key': content_key,
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
                'experience_type': exp.get('experience_type', 'unknown_legacy'),
                'domain': exp.get('domain', ''),
                'exceptions': list(exp.get('exceptions') or []),
                'origin_evidence_tier': exp.get('origin_evidence_tier', 'L4_self_eval'),
                'promoted_by': '',
                'locked': False,
                'evidence_state': 'unavailable',
                'trigger_embedding': None,
                'verification': {},
            }
            # P7-EVIDENCE: bind the experience to the exact content it was formed from, so a
            # later in-place edit with the same ID invalidates the old guidance.
            try:
                from veil.brain.experience_provider import EvidenceValidator, core_evidence_refs
                evidence_validator = EvidenceValidator(
                    self.cfg, self.store, memory=self.memory, profile=self.profile)
                record['evidence_fingerprints'] = evidence_validator.current_fingerprints(
                    valid_refs, milliseconds=2000)
                record['core_evidence_refs'] = core_evidence_refs(record)
            except Exception:
                record['evidence_fingerprints'] = {}
                record['core_evidence_refs'] = []
            if superseded_target is not None:
                if superseded_target.get('status') == 'confirmed':
                    # Never retire a Lan-confirmed experience until Lan approves the revision!
                    state['totals']['experience_revised'] += 1
                    outcome = 'pending_revision'
                else:
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

        return summary, retryable_refs

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

    def _read_profile_raw_checked(self):
        """Return (data, readable). Unreadable profile data must fail closed for injection."""
        if self.profile is None or not hasattr(self.profile, 'read'):
            return None, True
        try:
            return self.profile.read(), True
        except Exception:
            return None, False

    def _read_profile_raw(self):
        return self._read_profile_raw_checked()[0]

    def _is_vetoed_experience(self, guidance, exp_list, profile_data, vetoed_ledger=None):
        for item in exp_list:
            if isinstance(item, dict) and item.get('status') == 'vetoed':
                if _matches_governed_claim(guidance, item.get('guidance', '')):
                    return True
        for veto_text in (vetoed_ledger or ()):
            if isinstance(veto_text, str) and veto_text and _matches_governed_claim(guidance, veto_text):
                return True
        if profile_data is not None and self.profile is not None and hasattr(self.profile, '_is_rebutted_by_lan'):
            try:
                if self.profile._is_rebutted_by_lan(guidance, profile_data):
                    return True
            except Exception:
                # A profile veto ledger we cannot check must block injection, never allow it.
                return True
        return False

    # ------------------------------------------------------------------ execution engine
    def _due_check(self, state, values, *, trigger, force):
        if not values['enabled']:
            return False, 'disabled'
        cap = Decimal(str(values['daily_budget_cny']))
        if cap <= 0:
            return False, 'budget_cap_zero'
        now_stamp = self._now()
        now_dt = _parse_dt(now_stamp)
        today = now_stamp[:10]
        runs_today = int(state.get('runs_today') or 0) if state.get('day') == today else 0
        # Daily run cap is a hard budget guard and is never bypassed by `force`.
        if runs_today >= int(values['max_runs_per_day']):
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

    @staticmethod
    def _inputs_summary_for(batch):
        audit_stats = batch.get('autonomy_audit_stats') or {}
        return {
            'new_memory_ids': [m['id'] for m in batch['new_memories']],
            'anchor_memory_ids': [m['id'] for m in batch['anchor_memories']],
            'autonomy_ids': [a['id'] for a in batch['autonomy_items']],
            'trimmed_new_count': int(batch.get('trimmed_new_count') or 0),
            'trimmed_auto_count': int(batch.get('trimmed_auto_count') or 0),
            'excluded_autonomy_unclean': int(audit_stats.get('excluded_unclean') or 0),
            'excluded_autonomy_unaudited': int(audit_stats.get('excluded_unaudited') or 0),
            'existing_relationship_count': len(batch['existing_cognition']['relationship_items']),
            'existing_experience_count': len(batch['existing_experiences']),
        }

    @staticmethod
    def _serialize_sent_batch(batch):
        """Serialize the exact sent batch into a durable JSON-safe pending-commit checkpoint."""
        return {
            'batch_key': batch['batch_key'],
            'new_memories': list(batch['new_memories']),
            'anchor_memories': list(batch['anchor_memories']),
            'autonomy_items': list(batch['autonomy_items']),
            'batch_refs': sorted(batch['batch_refs']),
            'inputs_summary': CognitiveConsolidator._inputs_summary_for(batch),
            'active_memory_ids': list(batch.get('active_memory_ids') or []),
            'active_autonomy_ids': list(batch.get('active_autonomy_ids') or []),
        }

    @staticmethod
    def _deserialize_sent_batch(raw):
        new_mems = list(raw.get('new_memories') or [])
        anc_mems = list(raw.get('anchor_memories') or [])
        autos = list(raw.get('autonomy_items') or [])
        mem_by_id = {m['id']: m for m in (*new_mems, *anc_mems) if isinstance(m, dict) and m.get('id')}
        auto_by_id = {a['id']: a for a in autos if isinstance(a, dict) and a.get('id')}
        return {
            'batch_key': str(raw.get('batch_key') or ''),
            'new_memories': new_mems,
            'anchor_memories': anc_mems,
            'autonomy_items': autos,
            'batch_refs': set(raw.get('batch_refs') or []),
            'mem_by_id': mem_by_id,
            'auto_by_id': auto_by_id,
            'inputs_summary': dict(raw.get('inputs_summary') or {}),
            'active_memory_ids': list(raw.get('active_memory_ids') or []),
            'active_autonomy_ids': list(raw.get('active_autonomy_ids') or []),
            'existing_cognition': {'relationship_items': []},
            'existing_experiences': [],
        }

    def _commit_pending(self, values, *, resumed=False, stopped=lambda: False):
        """Apply a persisted `pending_commit` checkpoint without re-calling the consolidation model."""
        start_mono = time.monotonic()
        with self.mutex:
            state = self._load_state()
            pending = state.get('pending_commit')
            if not isinstance(pending, dict):
                return {'ok': False, 'status': 'skipped', 'reason': 'no_pending_commit'}
            attempts = int(pending.get('attempts') or 0)
            if attempts >= MAX_COMMIT_ATTEMPTS:
                stamp = self._now()
                abandoned = {
                    'run_id': str(pending.get('run_id') or ''),
                    'started_at': str(pending.get('started_at') or stamp),
                    'finished_at': stamp,
                    'trigger': str(pending.get('trigger') or 'manual'),
                    'status': 'apply_abandoned',
                    'reason': 'max_commit_attempts_exceeded',
                    'batch_key': str(pending.get('batch_key') or ''),
                    'policy_version': POLICY_VERSION,
                    'inputs': dict((pending.get('sent_batch') or {}).get('inputs_summary') or {}),
                    'results': {},
                    'cost_cny': 0.0,
                    'resumed_from_pending_commit': resumed,
                }
                state['pending_commit'] = None
                state['in_flight'] = None
                state['last_run_at'] = stamp
                state['last_status'] = 'apply_abandoned'
                state['last_reason'] = 'max_commit_attempts_exceeded'
                state.setdefault('recent_ui_runs', []).append(abandoned)
                self._save_state(state)
                self._append_run(abandoned)
                return {'ok': False, **abandoned}
            pending['attempts'] = attempts + 1
            state['pending_commit'] = pending
            self._save_state(state)
            parsed = pending.get('parsed') or {'cognition_updates': [], 'importance_proposals': [], 'experience_candidates': []}
            sent_batch = self._deserialize_sent_batch(pending.get('sent_batch') or {})
            cached_verdicts = dict(pending.get('semantic_verdicts') or {})
            run_id = str(pending.get('run_id') or 'cons-resume')
            started_at = str(pending.get('started_at') or self._now())
            trigger = str(pending.get('trigger') or 'manual')
            inputs_summary = dict(sent_batch.get('inputs_summary') or self._inputs_summary_for(sent_batch))
            orig_cost = round(float(pending.get('cost_cny') or 0.0), 6)
            prompt_tok = pending.get('prompt_tokens')
            completion_tok = pending.get('completion_tokens')
            cached_tok = pending.get('cached_tokens')
            cache_status = pending.get('cache_status')

        # Run S3 semantic verification for facts/growth OUTSIDE self.mutex and persist verdicts
        # into pending_commit so even an interruption after verification never re-bills S3.
        verdicts = self._preverify_facts_and_growth(parsed, sent_batch, cached_verdicts, stopped=stopped)
        with self.mutex:
            state = self._load_state()
            if isinstance(state.get('pending_commit'), dict):
                state['pending_commit']['semantic_verdicts'] = verdicts
                self._save_state(state)

            finished_at = self._now()
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            try:
                applied, retryable_refs = self._apply_outputs(
                    parsed, sent_batch, state, values, run_id, semantic_verdicts=verdicts,
                )
            except Exception as exc:
                # Preserve pending_commit so the next run resumes committing at ¥0!
                if isinstance(state.get('pending_commit'), dict):
                    state['pending_commit']['last_error'] = type(exc).__name__
                state['in_flight'] = None
                state['last_run_at'] = finished_at
                state['last_run_id'] = run_id
                state['last_status'] = 'apply_failed'
                state['last_reason'] = type(exc).__name__
                state['totals']['runs'] += 1
                state['totals']['failed'] += 1
                failures = int(state.get('consecutive_failures') or 0) + 1
                state['consecutive_failures'] = failures
                backoff_min = min(120, 15 * (2 ** min(failures - 1, 3)))
                now_dt = _parse_dt(finished_at)
                if now_dt is not None:
                    state['cooldown_until'] = (
                        now_dt + timedelta(minutes=backoff_min)
                    ).isoformat(timespec='seconds')
                fail_rec = {
                    'run_id': run_id, 'started_at': started_at, 'finished_at': finished_at,
                    'trigger': trigger, 'status': 'apply_failed', 'reason': type(exc).__name__,
                    'batch_key': sent_batch['batch_key'], 'policy_version': POLICY_VERSION,
                    'inputs': inputs_summary, 'results': {},
                    'cost_cny': 0.0 if resumed else orig_cost,
                    'original_cost_cny': orig_cost,
                    'resumed_from_pending_commit': resumed,
                    'prompt_tokens': prompt_tok,
                    'completion_tokens': completion_tok,
                    'cached_tokens': cached_tok,
                    'cache_status': cache_status,
                    'duration_ms': duration_ms,
                }
                state.setdefault('recent_ui_runs', []).append(fail_rec)
                self._save_state(state)
                self._append_run(fail_rec)
                return {'ok': False, **fail_rec}

            for mem in sent_batch['new_memories']:
                if f"memory:{mem['id']}" not in retryable_refs:
                    state['processed_memories'][mem['id']] = mem['fingerprint']
            for auto in sent_batch['autonomy_items']:
                if auto['id'] not in retryable_refs:
                    state['processed_autonomy'][auto['started_at']] = auto['fingerprint']
            if not retryable_refs:
                state['processed_batches'][sent_batch['batch_key']] = finished_at
            state['pending_commit'] = None
            state['in_flight'] = None
            state['last_run_at'] = finished_at
            state['last_success_at'] = finished_at
            state['last_run_id'] = run_id
            state['last_status'] = 'completed'
            state['last_reason'] = ''
            state['consecutive_failures'] = 0
            state['cooldown_until'] = ''
            state['totals']['runs'] += 1
            state['totals']['completed'] += 1
            run_record = {
                'run_id': run_id,
                'started_at': started_at,
                'finished_at': finished_at,
                'trigger': trigger,
                'status': 'completed',
                'reason': '',
                'batch_key': sent_batch['batch_key'],
                'policy_version': POLICY_VERSION,
                'inputs': inputs_summary,
                'results': applied,
                'cost_cny': 0.0 if resumed else orig_cost,
                'original_cost_cny': orig_cost,
                'resumed_from_pending_commit': resumed,
                'prompt_tokens': prompt_tok,
                'completion_tokens': completion_tok,
                'cached_tokens': cached_tok,
                'cache_status': cache_status,
                'duration_ms': duration_ms,
            }
            state.setdefault('recent_ui_runs', []).append(run_record)
            self._save_state(
                state,
                active_memory_ids=sent_batch.get('active_memory_ids'),
                active_autonomy_ids=sent_batch.get('active_autonomy_ids'),
            )
            self._append_run(run_record)
        return {'ok': True, **run_record}

    def run_once(self, *, trigger='manual', force=False, stopped=lambda: False):
        """Execute at most one bounded, idempotent consolidation pass (resuming pending commits first)."""
        if not self._run_lock.acquire(blocking=False):
            return {'ok': False, 'status': 'skipped', 'reason': 'already_running'}
        try:
            values = self.settings()
            has_pending_commit = False
            with self.mutex:
                state = self._load_state()
                if isinstance(state.get('in_flight'), dict):
                    self.recover_interrupted()
                    state = self._load_state()
                if isinstance(state.get('pending_commit'), dict):
                    if not values['enabled']:
                        return {'ok': False, 'status': 'skipped', 'reason': 'disabled'}
                    if not force and state.get('cooldown_until'):
                        cd_dt = _parse_dt(state['cooldown_until'])
                        now_dt = _parse_dt(self._now())
                        if cd_dt is not None and now_dt is not None and now_dt < cd_dt:
                            return {'ok': False, 'status': 'skipped', 'reason': 'failure_backoff'}
                    has_pending_commit = True
                else:
                    due, reason = self._due_check(state, values, trigger=trigger, force=force)
                    if not due:
                        return {'ok': False, 'status': 'skipped', 'reason': reason}

            if has_pending_commit:
                return self._commit_pending(values, resumed=True, stopped=stopped)

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
            initial_inputs = self._inputs_summary_for(batch)

            with self.mutex:
                fresh = self._load_state()
                fresh['in_flight'] = {
                    'run_id': run_id,
                    'batch_key': batch['batch_key'],
                    'started_at': started_at,
                    'trigger': trigger,
                    'phase': 'starting',
                    'counted_run': False,
                    'inputs': initial_inputs,
                }
                self._save_state(fresh)

            cap = Decimal(str(values['daily_budget_cny']))
            model_res = self._call_model(
                batch, cap, int(values['timeout_seconds']), stopped, state=fresh,
            )
            sent_batch = model_res.get('sent_batch') or batch
            inputs_summary = self._inputs_summary_for(sent_batch)
            finished_at = self._now()
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            today = finished_at[:10]

            if not model_res.get('ok'):
                fail_status = model_res.get('status') or 'failed'
                fail_reason = model_res.get('reason') or 'unknown'
                with self.mutex:
                    # Reload latest state so concurrent Lan decisions during HTTP call are preserved.
                    state = self._load_state()
                    state['in_flight'] = None
                    state['last_run_at'] = finished_at
                    state['last_run_id'] = run_id
                    state['last_status'] = fail_status
                    state['last_reason'] = fail_reason
                    state['totals']['runs'] += 1
                    if model_res.get('dispatched'):
                        if state.get('day') == today:
                            state['runs_today'] = int(state.get('runs_today') or 0) + 1
                        else:
                            state['day'] = today
                            state['runs_today'] = 1
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
                    run_record = {
                        'run_id': run_id, 'started_at': started_at, 'finished_at': finished_at,
                        'trigger': trigger, 'status': fail_status, 'reason': fail_reason,
                        'batch_key': sent_batch['batch_key'], 'policy_version': POLICY_VERSION,
                        'inputs': inputs_summary, 'results': {},
                        'cost_cny': round(float(model_res.get('cost_cny') or 0.0), 6),
                        'prompt_tokens': model_res.get('prompt_tokens'),
                        'completion_tokens': model_res.get('completion_tokens'),
                        'cached_tokens': model_res.get('cached_tokens'),
                        'cache_status': model_res.get('cache_status'),
                        'duration_ms': duration_ms,
                    }
                    state.setdefault('recent_ui_runs', []).append(run_record)
                    self._save_state(state)
                    self._append_run(run_record)
                return {'ok': False, **run_record}

            # Durable checkpoint BEFORE applying: persists completed model output so any
            # mid-apply crash or exception resumes applying without re-paying the model!
            with self.mutex:
                state = self._load_state()
                if state.get('day') == today:
                    state['runs_today'] = int(state.get('runs_today') or 0) + 1
                else:
                    state['day'] = today
                    state['runs_today'] = 1
                state['pending_commit'] = {
                    'run_id': run_id,
                    'started_at': started_at,
                    'trigger': trigger,
                    'batch_key': sent_batch['batch_key'],
                    'parsed': model_res['parsed'],
                    'sent_batch': self._serialize_sent_batch(sent_batch),
                    'semantic_verdicts': {},
                    'cost_cny': round(float(model_res.get('cost_cny') or 0.0), 6),
                    'prompt_tokens': model_res.get('prompt_tokens'),
                    'completion_tokens': model_res.get('completion_tokens'),
                    'cached_tokens': model_res.get('cached_tokens'),
                    'cache_status': model_res.get('cache_status'),
                    'counted_run': True,
                    'attempts': 0,
                }
                if isinstance(state.get('in_flight'), dict):
                    state['in_flight']['phase'] = 'applying'
                    state['in_flight']['counted_run'] = True
                    state['in_flight']['batch_key'] = sent_batch['batch_key']
                    state['in_flight']['inputs'] = inputs_summary
                self._save_state(state)

            return self._commit_pending(values, resumed=False, stopped=stopped)
        finally:
            self._run_lock.release()

    # ------------------------------------------------------------------ P7 unified interface
    def experience_interface_contract(self):
        return {
            'schema_version': EXPERIENCE_SCHEMA_VERSION,
            'slot_reserved': 'p7.experience_hints',
            'layer': 'AUXILIARY',
            'order_after': 'memory.short_notes',
            'order_before': 'recall_candidates',
            'budget_tokens': 600,
            'max_items': 4,
            'injectable_statuses': ['confirmed'],
            'permission': 'advice_only; VEIL tool permissions and approvals remain authoritative',
            'diagnostics': ['status', 'selected', 'dropped', 'used_tokens', 'budget_tokens'],
            'statuses': sorted(_VALID_EXP_STATUSES),
            'kinds': sorted(_VALID_EXP_KINDS),
            'experience_types': sorted(_VALID_EXP_TYPES),
            'domains': sorted(_VALID_EXP_DOMAINS),
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
            out.append(_normalise_experience(row))
            if len(out) >= max(1, min(200, int(limit or 50))):
                break
        return out

    def _read_state_shared(self, timeout=0.2):
        """Read state under the same lock boundary that veto commits use.

        A timed-out lock means "unavailable this turn", never "no vetoes".
        """
        try:
            with write_lock(self.store, self.cfg.id, timeout=timeout):
                return self._load_state()
        except TimeoutError:
            return None

    def confirm_experience_candidates(self, candidate_ids):
        """Return the IDs still confirmed and not vetoed, read under the commit boundary."""
        wanted = [str(item) for item in (candidate_ids or ()) if item]
        if not wanted:
            return []
        with self.mutex:
            state = self._read_state_shared(timeout=0.2)
            if state is None:
                return []
            rows = [row for row in (state.get('experience_candidates') or []) if isinstance(row, dict)]
            vetoed_ledger = list(state.get('vetoed_experiences') or [])
            profile_data, profile_ok = self._read_profile_raw_checked()
            if self.profile is not None and not profile_ok:
                return []
            by_id = {row.get('id'): row for row in rows}
            allowed = []
            for candidate_id in wanted:
                row = by_id.get(candidate_id)
                if row is None or row.get('status') != 'confirmed':
                    continue
                if self._is_vetoed_experience(row.get('guidance', ''), rows, profile_data, vetoed_ledger):
                    continue
                allowed.append(candidate_id)
            return allowed

    def query_experience_candidates(self, *, situation='', kinds=None, statuses=('confirmed',), limit=5,
                                    semantic_pool=False, semantic_offset=None,
                                    evidence_validator=None, evidence_deadline=None):
        """Unified P7 read contract: return verified, non-vetoed experience hints matching a situation."""
        allowed_statuses = set(statuses or ('confirmed',))
        allowed_kinds = set(kinds) if kinds else None
        sit_tokens = _lexical_tokens(situation) if situation else set()
        profile_data, profile_readable = self._read_profile_raw_checked()
        if self.profile is not None and not profile_readable:
            # Never inject while the persistent profile veto ledger cannot be checked.
            return []
        with self.mutex:
            state = self._read_state_shared()
            if state is None:
                return []
            rows = list(state.get('experience_candidates') or [])
            vetoed_ledger = list(state.get('vetoed_experiences') or [])
        valid_mems = None if evidence_validator is not None else self._existing_memory_ids()
        # Confirmed rows are protected from eviction and can exceed the nominal cap.
        # Extract veto rows once rather than rescanning all candidates for every row.
        vetoed_rows = [row for row in rows if isinstance(row, dict) and row.get('status') == 'vetoed']
        eligible = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get('status') not in allowed_statuses:
                continue
            if allowed_kinds is not None and row.get('kind') not in allowed_kinds:
                continue
            if self._is_vetoed_experience(row.get('guidance', ''), vetoed_rows, profile_data, vetoed_ledger):
                continue
            projected = _normalise_experience(row)
            eligible.append((row, projected))
        if semantic_pool and semantic_offset is not None and eligible:
            cap = max(1, min(512, int(limit or 512)))
            start = max(0, int(semantic_offset or 0))
            eligible = eligible[start:start + cap]
        if evidence_validator is not None:
            states = evidence_validator.validate_many(
                [projected for _row, projected in eligible], deadline=evidence_deadline)
        else:
            states = [None] * len(eligible)
        scored = []
        for (row, projected), evidence_state in zip(eligible, states):
            if evidence_state is not None:
                projected['evidence_state'] = evidence_state
                if evidence_state not in ('valid', 'partial_stale'):
                    continue
            # Filter out candidates whose memory evidence was all hard-deleted.
            refs = list(row.get('evidence_refs') or [])
            mem_refs = [r.split(':', 1)[1] for r in refs if isinstance(r, str) and r.startswith('memory:')]
            if mem_refs and valid_mems is not None and not any(m in valid_mems for m in mem_refs):
                continue
            score = 1.0 if row.get('status') == 'confirmed' else 0.5
            if sit_tokens and not semantic_pool:
                cand_tokens = _lexical_tokens(
                    f"{row.get('trigger_condition', '')} {row.get('guidance', '')}"
                )
                overlap = len(sit_tokens & cand_tokens)
                if overlap == 0:
                    continue
                score += float(overlap)
            scored.append((score, str(row.get('updated_at') or ''), projected))
        if semantic_pool:
            # The provider rotates this bounded window across the stable candidate order.
            # This is iterative reading, not a newest-first pre-score truncation.
            return [item[2] for item in scored]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:max(1, min(20, int(limit or 5)))]]

    def cache_experience_embedding(self, candidate_id, *, fingerprint, model, vector, vector_space=''):
        """Persist a lazy P7 trigger vector without weakening governance state."""
        candidate_id = str(candidate_id or '')
        try:
            values = [float(item) for item in (vector or ())]
        except (TypeError, ValueError, OverflowError):
            return False
        if not candidate_id or not fingerprint or not model or not values or not all(map(math.isfinite, values)):
            return False
        with self.mutex, write_lock(self.store, self.cfg.id):
            state = self._load_state()
            row = next((item for item in (state.get('experience_candidates') or [])
                        if isinstance(item, dict) and item.get('id') == candidate_id), None)
            if row is None or row.get('status') != 'confirmed':
                return False
            generation = _embedding_generation(row.get('trigger_embedding')) + 1
            row['trigger_embedding'] = {
                'fingerprint': str(fingerprint), 'model': str(model),
                'dimension': len(values), 'vector': values, 'cached_at': self._now(),
                'generation': generation, 'vector_space': str(vector_space or ''),
            }
            self._save_state(state, lock=False)
        return True

    def review_experience_matches(self, envelope, candidates, *, timeout=2.0, daily_cap=0.02):
        """Optional fail-closed gray-zone review under its own hard daily budget."""
        from veil.core.veil_agent import RequestNotSent

        if self.model is None or self.budget is None or not candidates:
            return [], 0.0
        allowed = {str(row.get('id') or '') for row in candidates if row.get('id')}
        payload = {
            'situation': str(getattr(envelope, 'text', '') or '')[:1200],
            'entry_mode': str(getattr(envelope, 'entry_mode', '') or ''),
            'candidates': [{
                'id': row.get('id'), 'trigger': row.get('trigger_condition'),
                'guidance': row.get('guidance'), 'exceptions': row.get('exceptions') or [],
            } for row in candidates[:3]],
        }
        messages = [
            {'role': 'system', 'content': prompts.text('memory.experience.match_review')},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ]
        provider = dict(self.cfg.provider)
        provider['max_input_tokens'] = min(provider.get('max_input_tokens', 4096), 4096)
        request_cfg = replace(self.cfg, provider=provider, max_output_tokens=256)
        cap = max(Decimal('0'), min(Decimal('1.0'), Decimal(str(daily_cap))))
        try:
            reservation = self.budget.reserve(
                request_cfg, category=P7_GATE_COST_CATEGORY,
                accounting=P7_GATE_COST_ACCOUNTING, category_daily=cap)
        except (BudgetExceeded, UserError):
            return [], 0.0
        request_id, reserved_amount = reservation
        try:
            self.budget.mark_dispatched(request_id)
            output, prompt_tok, completion_tok, cached_tok, _stop = self.model(
                messages, json_output=True, max_tokens=256, thinking='disabled',
                detail=True, request_timeout=min(2.0, max(0.1, float(timeout))),
            )
        except RequestNotSent:
            self.budget.release_not_sent(request_id)
            return [], 0.0
        except Exception:
            try:
                self.budget.settle_estimate(reservation, accounting=P7_GATE_COST_ACCOUNTING + '-unknown-usage')
            except Exception:
                pass
            return [], float(reserved_amount)
        try:
            settled = self.budget.settle(
                request_id, request_cfg, prompt_tok, completion_tok, cached_tok,
                input_bytes=len(json.dumps(messages, ensure_ascii=False).encode('utf-8')),
                category=P7_GATE_COST_CATEGORY, accounting=P7_GATE_COST_ACCOUNTING,
            )
            if settled is False:
                return [], float(reserved_amount)
            parsed = json.loads(output) if isinstance(output, str) else output
            ids = parsed.get('applicable_ids') if isinstance(parsed, dict) else None
            accepted = [str(item) for item in ids if str(item) in allowed] if isinstance(ids, list) else []
            cached = cached_tok if type(cached_tok) is int else 0
            rates = request_cfg.provider
            cost = (Decimal(max(0, prompt_tok - cached)) * Decimal(str(rates.get('input_cny_per_million', 0)))
                    + Decimal(cached) * Decimal(str(rates.get('cached_input_cny_per_million', 0)))
                    + Decimal(completion_tok) * Decimal(str(rates.get('output_cny_per_million', 0)))) / Decimal(1_000_000)
            return accepted, float(cost)
        except Exception:
            return [], float(reserved_amount)

    def _existing_memory_ids(self):
        db = self._db_path()
        if db is None or not db.is_file():
            return None
        try:
            conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=3.0)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            pending_deletes = set()
            if 'delete_requests' in tables:
                for dr in conn.execute("SELECT memory_id FROM delete_requests WHERE status='pending'"):
                    if dr[0]:
                        pending_deletes.add(str(dr[0]))
            ids = {
                row[0] for row in conn.execute(
                    'SELECT id FROM memories WHERE is_tombstone=0 AND is_deleted=0'
                )
                if row[0] not in pending_deletes
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
        with self.mutex, write_lock(self.store, self.cfg.id):
            state = self._load_state()
            rows = state.get('experience_candidates') or []
            target = next((r for r in rows if isinstance(r, dict) and r.get('id') == cid), None)
            if target is None:
                return {'ok': False, 'error': 'candidate_not_found', 'status_code': 404}
            if action == 'confirm' and target.get('status') == 'vetoed':
                return {
                    'ok': False,
                    'error': '已否决的经验候选不能直接确认，请使用显式恢复操作。',
                    'status_code': 409,
                }
            mapping = {'confirm': 'confirmed', 'veto': 'vetoed', 'outdated': 'outdated', 'restore': 'confirmed'}
            now_stamp = self._now()
            target['status'] = mapping[action]
            target['decision'] = action
            target['decided_by'] = 'lan'
            target['decided_at'] = now_stamp
            target['updated_at'] = now_stamp
            if note:
                target['note'] = _clip(note, 200)
            vetoed_ledger = state.setdefault('vetoed_experiences', [])
            if action == 'veto':
                g = str(target.get('guidance') or '').strip()
                if g and not any(_matches_governed_claim(g, v) for v in vetoed_ledger if isinstance(v, str)):
                    vetoed_ledger.append(g)
            elif action == 'restore':
                g = str(target.get('guidance') or '').strip()
                if g:
                    state['vetoed_experiences'] = [
                        v for v in vetoed_ledger
                        if not (isinstance(v, str) and _matches_governed_claim(g, v))
                    ]
            if action in ('confirm', 'restore') and target.get('supersedes'):
                old_id = target['supersedes']
                old_row = next(
                    (r for r in rows if isinstance(r, dict) and r.get('id') == old_id
                     and r.get('status') in ('confirmed', 'uncertain')),
                    None,
                )
                if old_row is not None:
                    old_row['status'] = 'outdated'
                    old_row['superseded_by'] = cid
                    old_row['updated_at'] = now_stamp
            self._save_state(state, lock=False)
            result = {'ok': True, 'candidate': dict(target)}
        if action in ('confirm', 'restore'):
            callback = getattr(self, 'experience_confirmed_callback', None)
            if callable(callback):
                try:
                    callback([cid])
                except Exception:
                    pass
        return result

    def list_pending_cognition(self, *, status='pending', limit=60):
        with self.mutex:
            state = self._load_state()
            rows = list(state.get('pending_cognition_updates') or [])
        out = []
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if status and row.get('status') != status:
                continue
            out.append(dict(row))
            if len(out) >= max(1, min(MAX_PENDING_COGNITION, int(limit or 60))):
                break
        return out

    def decide_pending_cognition(self, pending_id, action, note=''):
        """Lan decision for held facts/growth candidates that lacked S3 semantic confirmation."""
        action = str(action or '').strip()
        if action not in ('approve', 'reject'):
            return {'ok': False, 'error': 'unknown_action', 'status_code': 400}
        pid = str(pending_id or '').strip()
        if not pid:
            return {'ok': False, 'error': 'missing_id', 'status_code': 400}
        with self.mutex:
            state = self._load_state()
            rows = state.get('pending_cognition_updates') or []
            target = next((r for r in rows if isinstance(r, dict) and r.get('id') == pid), None)
            if target is None:
                return {'ok': False, 'error': 'pending_cognition_not_found', 'status_code': 404}
            if target.get('status') != 'pending':
                return {'ok': False, 'error': 'already_decided', 'status_code': 409}
            now_stamp = self._now()
            if action == 'reject':
                target['status'] = 'rejected'
                target['decided_by'] = 'lan'
                target['decided_at'] = now_stamp
                target['updated_at'] = now_stamp
                if note:
                    target['note'] = _clip(note, 200)
                vetoed_ledger = state.setdefault('vetoed_experiences', [])
                txt = str(target.get('text') or '').strip()
                if txt and not any(_matches_governed_claim(txt, v) for v in vetoed_ledger if isinstance(v, str)):
                    vetoed_ledger.append(txt)
                self._save_state(state)
                return {'ok': True, 'item': dict(target)}

            # action == 'approve': submit through S3 LearnedContext.submit_candidate()!
            if self.profile is None or not hasattr(self.profile, 'submit_candidate'):
                return {'ok': False, 'error': 'profile_unavailable', 'status_code': 503}
            idem_key = f"cons:approved:{target.get('content_key') or pid}"
            msg, st_label, _ = self.profile.submit_candidate(
                target['section'],
                target['text'],
                status='uncertain',
                sources=list(target.get('s3_sources') or []),
                replace_old_text=target.get('replace_old_text') or None,
                origin='consolidation_lan_approved',
                idempotency_key=idem_key,
            )
            if st_label != '成功':
                return {'ok': False, 'error': str(msg or 'submit_rejected'), 'status_code': 409}
            target['status'] = 'approved'
            target['decided_by'] = 'lan'
            target['decided_at'] = now_stamp
            target['updated_at'] = now_stamp
            if note:
                target['note'] = _clip(note, 200)
            if target.get('kind') == 'revision':
                state['totals']['cognition_revised'] += 1
            else:
                state['totals']['cognition_written'] += 1
            self._save_state(state)
            return {'ok': True, 'item': dict(target), 'message': str(msg or '')}

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
        ui_runs = list(reversed([r for r in (state.get('recent_ui_runs') or []) if isinstance(r, dict)]))
        shown_runs = ui_runs[:max(1, min(16, int(runs_limit or 12)))] if ui_runs else self.recent_runs(limit=runs_limit)
        return {
            'available': True,
            'policy_version': POLICY_VERSION,
            'persistence_path': self.settings_path(),
            'settings': values,
            'settings_revision': self.settings_revision(),
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
                'has_pending_commit': isinstance(state.get('pending_commit'), dict),
            },
            'budget': budget_info,
            'totals': dict(state.get('totals') or {}),
            'p7_interface': {
                **self.experience_interface_contract(),
                'counts': exp_counts,
            },
            'pending_cognition_updates': self.list_pending_cognition(status='pending'),
            'experience_candidates': self.list_experience_candidates(limit=MAX_EXPERIENCE_CANDIDATES),
            'recent_runs': shown_runs,
            'guards': {
                'default_disabled': True,
                'reuses_s3_governance': True,
                'reuses_s3_semantic_verify': True,
                'reuses_s6_1_weights': True,
                'filters_unclean_and_unaudited_autonomy': True,
                'respects_lan_locks_and_vetoes': True,
                'durable_pending_commit_resume': True,
            },
        }
