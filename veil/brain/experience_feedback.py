"""P7-S3 role-isolated experience feedback ledger, pending-delivery recovery and reflection tool."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path

from veil.brain.relationship_semantic import _contains_secret
from veil.brain.storage.brain_lock import write_lock


FEEDBACK_FILENAME = 'experience-feedback.jsonl'
FEEDBACK_SCHEMA_VERSION = 'p7.feedback.v1'
VALID_STANCES = frozenset({'adopt', 'adjust', 'rebut', 'suspend', 'propose_revision'})
VALID_EVIDENCE_REF_RE = re.compile(r'^(memory|event|chat|autonomy|tool|long_task|ongoing):[A-Za-z0-9._:@+-]{1,120}$')

# Low-risk trial guard: any mention of permissions, destructive writes, money, or external side effects
# disqualifies an unverified improvement from automatic single-trial offering.
_HIGH_RISK_TRIAL_RE = re.compile(
    r'(?i)(\b(delete|remove|rm|drop|truncate|wipe|format|pay|payment|purchase|buy|charge|billing|'
    r'install|uninstall|push|deploy|publish|post|tweet|email|mail|send_email|shell|exec|sudo|'
    r'chmod|registry|overwrite|bypass|approve|approval|permission|credential|secret|token|password|'
    r'transfer|money|cny|usd|cost|budget)\b|'
    r'删除|清空|卸载|安装|付款|支付|付费|购买|扣费|推送|部署|发布|发邮件|外部写入|'
    r'覆盖文件|修改系统|注册表|绕过批准|跳过审批|放宽权限|自动确认|提权|转账|密钥|密码)'
)

# Tool schema stays strictly under tool_groups.BUDGET['tool'] (1500 bytes).
EXPERIENCE_REFLECT_TOOL = {
    'type': 'function',
    'function': {
        'name': 'experience_reflect',
        'description': (
            'Record your situational judgment on an injected experience hint '
            '(rebut, suspend, propose_revision, adjust, or adopt) with reasoning. '
            'Writes feedback or an uncertain revision draft only; never alters confirmed entries, '
            'Lan vetoes, or tool permissions.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'experience_id': {'type': 'string', 'description': 'Injected experience ID.'},
                'stance': {
                    'type': 'string',
                    'enum': ['adopt', 'adjust', 'rebut', 'suspend', 'propose_revision'],
                },
                'basis': {'type': 'string', 'description': 'Concrete situational reason (max 400 chars).'},
                'suggested_exception': {'type': 'string', 'description': 'Optional exception condition.'},
                'suggested_guidance': {'type': 'string', 'description': 'Optional revised guidance draft.'},
                'evidence_refs': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Optional verifiable refs (chat:/memory:/event:/tool:/autonomy:/ongoing:).',
                },
            },
            'required': ['experience_id', 'stance', 'basis'],
            'additionalProperties': False,
        },
    },
}


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _parse_dt(value):
    text = str(value or '').strip()
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


def _sanitize_text(value, limit=300):
    text = ''.join(ch for ch in str(value or '') if ch in '\n\t' or ord(ch) >= 32).strip()
    if not text:
        return ''
    if _contains_secret(text):
        return '[redacted]'
    return text[:limit]


def _sanitize_refs(refs, limit=8):
    out = []
    seen = set()
    for item in refs or ():
        if not isinstance(item, str):
            continue
        ref = item.strip()
        if not ref or ref in seen or not VALID_EVIDENCE_REF_RE.fullmatch(ref):
            continue
        seen.add(ref)
        out.append(ref)
        if len(out) >= limit:
            break
    return out


def make_idem_key(request_id, exp_id, event_type, result_id):
    return f'{str(request_id or "").strip()}|{str(exp_id or "").strip()}|{str(event_type or "").strip()}|{str(result_id or "").strip()}'


def is_low_risk_trial(candidate):
    """Return True only when an unverified improvement is strictly low-risk and side-effect free."""
    if not isinstance(candidate, dict):
        return False
    if candidate.get('experience_type') != 'unverified_improvement':
        return False
    if candidate.get('status') != 'uncertain':
        return False
    risk = str(candidate.get('risk_level') or 'low').strip().lower()
    if risk and risk != 'low':
        return False
    if candidate.get('requires_permission') or candidate.get('requires_cost') or candidate.get('external_side_effect'):
        return False
    combined = ' '.join(str(candidate.get(k) or '') for k in ('trigger_condition', 'guidance', 'reason'))
    if not combined.strip() or _HIGH_RISK_TRIAL_RE.search(combined):
        return False
    return True


def _read_jsonl_safe(path: Path):
    """Read complete JSON object lines, ignoring any truncated trailing crash fragment."""
    if not path.is_file():
        return []
    rows = []
    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows


class ExperienceFeedbackLedger:
    """Atomic, idempotent, crash-recoverable feedback ledger per role."""

    _ROLE_LOCKS = weakref.WeakValueDictionary()
    _ROLE_LOCKS_MUTEX = threading.Lock()

    def __init__(self, store, role, *, now_fn=None):
        self.store = store
        self.role = str(role)
        self._now = now_fn or _now
        lock_key = (os.path.normcase(os.path.abspath(str(store.root))), self.role)
        with self._ROLE_LOCKS_MUTEX:
            self._mutex = self._ROLE_LOCKS.setdefault(lock_key, threading.RLock())

    @property
    def role_dir(self) -> Path:
        return Path(self.store.root) / self.role

    @property
    def path(self) -> Path:
        return self.role_dir / FEEDBACK_FILENAME

    def _existing_keys_locked(self):
        keys = set()
        for row in _read_jsonl_safe(self.path):
            key = row.get('idem_key')
            if isinstance(key, str) and key:
                keys.add(key)
            else:
                keys.add(make_idem_key(
                    row.get('request_id'), row.get('exp_id'),
                    row.get('event_type'), row.get('result_id'),
                ))
        return keys

    def _append_records(self, records):
        """Atomically append deduplicated records under per-role mutex + cross-process write_lock."""
        if not records:
            return []
        self.role_dir.mkdir(parents=True, exist_ok=True)
        written = []
        with self._mutex, write_lock(self.store, self.role):
            seen = self._existing_keys_locked()
            to_write = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                req_id = str(rec.get('request_id') or '').strip()
                exp_id = str(rec.get('exp_id') or '').strip()
                ev_type = str(rec.get('event_type') or '').strip()
                res_id = str(rec.get('result_id') or '').strip()
                if not ev_type or not res_id:
                    continue
                idem_key = make_idem_key(req_id, exp_id, ev_type, res_id)
                if idem_key in seen:
                    continue
                seen.add(idem_key)
                event_id = str(rec.get('event_id') or '').strip()
                if not event_id:
                    event_id = 'fb-' + hashlib.sha256(idem_key.encode('utf-8')).hexdigest()[:16]
                payload = {
                    'schema_version': FEEDBACK_SCHEMA_VERSION,
                    'event_id': event_id,
                    'idem_key': idem_key,
                    'at': str(rec.get('at') or self._now()),
                    'role': self.role,
                    'request_id': req_id,
                    'exp_id': exp_id,
                    'event_type': ev_type,
                    'result_id': res_id,
                }
                for key, value in rec.items():
                    if key in payload:
                        continue
                    payload[key] = value
                to_write.append(payload)
            if not to_write:
                return []
            # Rewrite/append cleanly: ensure existing file ends with newline before appending
            # and fsync so power/process loss never loses committed records.
            need_prefix_nl = False
            if self.path.is_file() and self.path.stat().st_size > 0:
                try:
                    with self.path.open('rb') as check:
                        check.seek(-1, os.SEEK_END)
                        need_prefix_nl = check.read(1) != b'\n'
                except OSError:
                    need_prefix_nl = False
            chunk = ('\n' if need_prefix_nl else '') + ''.join(
                json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n'
                for item in to_write
            )
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(chunk)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            written = to_write
        return written

    def read_all(self):
        with self._mutex:
            return _read_jsonl_safe(self.path)

    def record_pending_delivery(self, *, request_id, session_key='', entry_mode='chat',
                                injected=(), trial=None, autonomy_run_id=''):
        """Write pending_delivery records BEFORE the model call (idempotent on request_id+exp_id)."""
        req_id = str(request_id or '').strip()
        if not req_id:
            return []
        stamp = self._now()
        records = []
        for item in injected or ():
            if isinstance(item, str):
                exp_id, score, domain, exp_type = item, None, '', ''
            elif isinstance(item, dict):
                exp_id = str(item.get('id') or item.get('exp_id') or '').strip()
                score = item.get('match_score')
                domain = str(item.get('domain') or '')
                exp_type = str(item.get('experience_type') or '')
            else:
                continue
            if not exp_id:
                continue
            records.append({
                'at': stamp,
                'request_id': req_id,
                'exp_id': exp_id,
                'event_type': 'pending_delivery',
                'result_id': 'delivery',
                'session_key': str(session_key or ''),
                'entry_mode': str(entry_mode or 'chat'),
                'autonomy_run_id': str(autonomy_run_id or ''),
                'match_score': round(float(score), 6) if isinstance(score, (int, float)) else None,
                'domain': domain,
                'experience_type': exp_type,
            })
        if isinstance(trial, dict) and trial.get('trial_id') and trial.get('experience_id'):
            trial_id = str(trial['trial_id'])
            exp_id = str(trial['experience_id'])
            records.append({
                'at': stamp,
                'request_id': req_id,
                'exp_id': exp_id,
                'event_type': 'trial_started',
                'result_id': f'trial:{trial_id}',
                'trial_id': trial_id,
                'session_key': str(session_key or ''),
                'entry_mode': str(entry_mode or 'chat'),
                'autonomy_run_id': str(autonomy_run_id or ''),
                'plan': _sanitize_text(trial.get('guidance') or trial.get('plan') or '', 240),
                'risk_level': 'low',
            })
        return self._append_records(records)

    def confirm_delivery(self, *, request_id, session_key='', entry_mode='chat',
                         injected=(), trial=None, tool_summaries=(), autonomy_run_id='',
                         reconciled=False):
        """Mark pending deliveries as sent and attach objective tool/trial outcomes."""
        req_id = str(request_id or '').strip()
        if not req_id:
            return []
        stamp = self._now()
        records = []
        exp_ids = []
        for item in injected or ():
            exp_id = item if isinstance(item, str) else (
                str(item.get('id') or item.get('exp_id') or '').strip() if isinstance(item, dict) else ''
            )
            if exp_id and exp_id not in exp_ids:
                exp_ids.append(exp_id)
                records.append({
                    'at': stamp,
                    'request_id': req_id,
                    'exp_id': exp_id,
                    'event_type': 'delivery_sent',
                    'result_id': 'delivery',
                    'session_key': str(session_key or ''),
                    'entry_mode': str(entry_mode or 'chat'),
                    'autonomy_run_id': str(autonomy_run_id or ''),
                    'reconciled': bool(reconciled),
                })

        # Record objective tool outcomes for every real tool executed in this request.
        # Each tool gets its own stable result_id so multiple results in one request are never collapsed.
        _PLUMBING_TOOLS = {'expand_capability', 'experience_reflect', 'send_message', 'stay_silent', 'reply_later'}
        target_exp_ids = list(exp_ids) if exp_ids else ['*']
        trial_id = str(trial.get('trial_id') or '') if isinstance(trial, dict) else ''
        trial_exp_id = str(trial.get('experience_id') or '') if isinstance(trial, dict) else ''
        tool_positives = 0
        tool_negatives = 0
        tool_refs = []
        for idx, summary in enumerate(tool_summaries or ()):
            if not isinstance(summary, dict):
                continue
            tool_name = str(summary.get('tool') or '').strip()
            if not tool_name or tool_name in _PLUMBING_TOOLS:
                continue
            state_str = str(summary.get('state') or '').strip()
            call_id = str(summary.get('call_id') or summary.get('id') or f'{req_id}:t{idx}')
            is_ok = state_str == '成功'
            outcome = 'positive' if is_ok else 'negative'
            if is_ok:
                tool_positives += 1
            else:
                tool_negatives += 1
            ref = f'tool:{call_id}'
            if VALID_EVIDENCE_REF_RE.fullmatch(ref):
                tool_refs.append(ref)
            run_key = str(autonomy_run_id or session_key or req_id)
            for exp_id in target_exp_ids:
                records.append({
                    'at': stamp,
                    'request_id': req_id,
                    'exp_id': exp_id,
                    'event_type': 'tool_outcome',
                    'result_id': f'tool:{call_id}',
                    'tool': tool_name[:80],
                    'state': state_str[:40],
                    'outcome': outcome,
                    'evidence_tier': 'L2_objective_action',
                    'evidence_ref': ref if VALID_EVIDENCE_REF_RE.fullmatch(ref) else '',
                    'checkpoint_verified': bool(summary.get('checkpoint_verified', False)),
                    'session_key': str(session_key or ''),
                    'autonomy_run_id': str(autonomy_run_id or ''),
                    'run_key': run_key,
                })

        if trial_id and trial_exp_id:
            records.append({
                'at': stamp,
                'request_id': req_id,
                'exp_id': trial_exp_id,
                'event_type': 'trial_delivered',
                'result_id': f'trial:{trial_id}:delivered',
                'trial_id': trial_id,
                'session_key': str(session_key or ''),
                'entry_mode': str(entry_mode or 'chat'),
                'autonomy_run_id': str(autonomy_run_id or ''),
                'reconciled': bool(reconciled),
            })
            if tool_positives > 0 or tool_negatives > 0:
                trial_outcome = 'negative' if tool_negatives > 0 else 'positive'
                records.append({
                    'at': stamp,
                    'request_id': req_id,
                    'exp_id': trial_exp_id,
                    'event_type': 'trial_outcome',
                    'result_id': f'trial:{trial_id}:l2:{req_id}',
                    'trial_id': trial_id,
                    'outcome': trial_outcome,
                    'evidence_tier': 'L2_objective_action',
                    'evidence_refs': tool_refs[:6],
                    'run_key': str(autonomy_run_id or session_key or req_id),
                    'detail': f'tools ok={tool_positives}, failed={tool_negatives}',
                })
        return self._append_records(records)

    def cancel_delivery(self, *, request_id, injected=(), trial=None, reconciled=False):
        """Mark pending deliveries as cancelled so undelivered turns never count as exposures."""
        req_id = str(request_id or '').strip()
        if not req_id:
            return []
        stamp = self._now()
        records = []
        for item in injected or ():
            exp_id = item if isinstance(item, str) else (
                str(item.get('id') or item.get('exp_id') or '').strip() if isinstance(item, dict) else ''
            )
            if not exp_id:
                continue
            records.append({
                'at': stamp,
                'request_id': req_id,
                'exp_id': exp_id,
                'event_type': 'delivery_cancelled',
                'result_id': 'delivery',
                'reconciled': bool(reconciled),
            })
        if isinstance(trial, dict) and trial.get('trial_id') and trial.get('experience_id'):
            trial_id = str(trial['trial_id'])
            records.append({
                'at': stamp,
                'request_id': req_id,
                'exp_id': str(trial['experience_id']),
                'event_type': 'trial_cancelled',
                'result_id': f'trial:{trial_id}:cancelled',
                'trial_id': trial_id,
                'reconciled': bool(reconciled),
            })
        return self._append_records(records)

    def record_reflection(self, *, request_id, exp_id, stance, basis, result_id='',
                          suggested_exception='', suggested_guidance='', evidence_refs=(),
                          session_key='', revision_draft_id=''):
        op_id = str(result_id or uuid.uuid4().hex[:12])
        return self._append_records([{
            'request_id': str(request_id or ''),
            'exp_id': str(exp_id or ''),
            'event_type': 'reflection',
            'result_id': f'reflect:{op_id}',
            'stance': str(stance or ''),
            'basis': _sanitize_text(basis, 400),
            'suggested_exception': _sanitize_text(suggested_exception, 120),
            'suggested_guidance': _sanitize_text(suggested_guidance, 400),
            'evidence_refs': _sanitize_refs(evidence_refs),
            'session_key': str(session_key or ''),
            'revision_draft_id': str(revision_draft_id or ''),
        }])

    def record_commitment_outcome(self, *, request_id='', exp_ids=(), item_id, status,
                                  text='', outcome='positive', evidence_tier='L2_objective_action',
                                  session_key='', result_suffix=''):
        cid = str(item_id or '').strip()
        if not cid:
            return []
        targets = [str(e) for e in (exp_ids or ()) if e] or ['*']
        suffix = f':{result_suffix}' if result_suffix else ''
        res_id = f'ongoing:{cid}:{status}{suffix}'
        ref = f'ongoing:{cid}'
        records = []
        for exp_id in targets:
            records.append({
                'request_id': str(request_id or ''),
                'exp_id': exp_id,
                'event_type': 'commitment_outcome',
                'result_id': res_id,
                'item_id': cid,
                'status': str(status or ''),
                'text': _sanitize_text(text, 200),
                'outcome': 'positive' if outcome == 'positive' else 'negative',
                'evidence_tier': str(evidence_tier or 'L2_objective_action'),
                'evidence_ref': ref if VALID_EVIDENCE_REF_RE.fullmatch(ref) else '',
                'session_key': str(session_key or ''),
                'run_key': str(session_key or request_id or cid),
            })
        return self._append_records(records)

    def record_autonomy_outcome(self, *, request_id='', autonomy_run_id, clean, result,
                                tools=(), injected_exp_ids=()):
        run_id = str(autonomy_run_id or '').strip()
        if not run_id:
            return []
        ok_tools = [t for t in (tools or ()) if isinstance(t, dict) and t.get('state') == '成功']
        failed_tools = [t for t in (tools or ()) if isinstance(t, dict) and t.get('state') != '成功']
        outcome = 'positive' if (clean and result in ('成功', '完成', '') and not failed_tools and ok_tools) else (
            'negative' if (not clean or failed_tools or result == '失败') else 'neutral'
        )
        tier = 'L2_objective_action' if (clean and (ok_tools or failed_tools)) else (
            'L3_autonomy_reflection' if clean else 'L4_self_eval'
        )
        targets = [str(e) for e in (injected_exp_ids or ()) if e] or ['*']
        ref = f'autonomy:{run_id}'
        records = []
        for exp_id in targets:
            records.append({
                'request_id': str(request_id or ''),
                'exp_id': exp_id,
                'event_type': 'autonomy_outcome',
                'result_id': f'autonomy:{run_id}',
                'autonomy_run_id': run_id,
                'clean': bool(clean),
                'result': str(result or ''),
                'ok_tool_count': len(ok_tools),
                'failed_tool_count': len(failed_tools),
                'outcome': outcome,
                'evidence_tier': tier,
                'evidence_ref': ref if VALID_EVIDENCE_REF_RE.fullmatch(ref) else '',
                'run_key': run_id,
            })
        return self._append_records(records)

    def record_trial_outcome(self, *, trial_id, exp_id, outcome, evidence_tier='L2_objective_action',
                             request_id='', evidence_refs=(), run_key='', detail='', result_suffix=''):
        tid = str(trial_id or '').strip()
        eid = str(exp_id or '').strip()
        if not tid or not eid or outcome not in ('positive', 'negative'):
            return []
        suffix = f':{result_suffix}' if result_suffix else (f':{run_key}' if run_key else '')
        return self._append_records([{
            'request_id': str(request_id or ''),
            'exp_id': eid,
            'event_type': 'trial_outcome',
            'result_id': f'trial:{tid}:{outcome}{suffix}',
            'trial_id': tid,
            'outcome': outcome,
            'evidence_tier': str(evidence_tier or 'L2_objective_action'),
            'evidence_refs': _sanitize_refs(evidence_refs),
            'run_key': str(run_key or request_id or tid),
            'detail': _sanitize_text(detail, 240),
        }])

    def record_user_decision(self, *, exp_id, action, decided_at='', note=''):
        eid = str(exp_id or '').strip()
        if not eid or action not in ('confirm', 'veto', 'outdated', 'restore'):
            return []
        stamp = str(decided_at or self._now())
        outcome = 'positive' if action in ('confirm', 'restore') else 'negative'
        return self._append_records([{
            'at': stamp,
            'request_id': '',
            'exp_id': eid,
            'event_type': 'user_decision',
            'result_id': f'decision:{stamp}:{action}',
            'action': action,
            'outcome': outcome,
            'evidence_tier': 'L1_user_feedback',
            'note': _sanitize_text(note, 200),
        }])

    def _delivery_truth(self, request_ids, autonomy_run_ids):
        """Check ground-truth delivery facts from chat.jsonl, autonomy.jsonl and tools.jsonl."""
        delivered_reqs = set()
        failed_reqs = set()
        delivered_auto = set()
        if not request_ids and not autonomy_run_ids:
            return delivered_reqs, failed_reqs, delivered_auto

        if request_ids:
            for row in _read_jsonl_safe(self.role_dir / 'chat.jsonl'):
                req = str(row.get('request_id') or '')
                if req not in request_ids:
                    continue
                if row.get('role') == 'assistant' and not (row.get('deleted') or row.get('withdrawn') or row.get('retracted')):
                    delivered_reqs.add(req)
            for row in _read_jsonl_safe(self.role_dir / 'tools.jsonl'):
                req = str(row.get('request_id') or '')
                if req not in request_ids:
                    continue
                if row.get('tool') == 'send_message' and row.get('state') == '成功':
                    delivered_reqs.add(req)
            for row in _read_jsonl_safe(self.role_dir / 'message-processing.jsonl'):
                req = str(row.get('request_id') or '')
                if req in request_ids and row.get('state') == 'failed':
                    failed_reqs.add(req)

        if autonomy_run_ids:
            for row in _read_jsonl_safe(self.role_dir / 'autonomy.jsonl'):
                started = str(row.get('started_at') or '')
                if started not in autonomy_run_ids:
                    continue
                audit = row.get('output_audit') if isinstance(row.get('output_audit'), dict) else {}
                if row.get('result') in ('成功', '完成') and audit.get('clean') is True:
                    delivered_auto.add(started)
        return delivered_reqs, failed_reqs, delivered_auto

    def reconcile_pending_deliveries(self, *, active_request_ids=(), stale_seconds=600, force_stale=False):
        """Resolve orphaned pending_delivery and trial_started records after crashes or restarts."""
        rows = self.read_all()
        if not rows:
            return {'checked': 0, 'sent': 0, 'cancelled': 0, 'still_pending': 0}
        active = {str(item) for item in (active_request_ids or ()) if item}
        pending_deliveries = {}
        finalized_deliveries = set()
        pending_trials = {}
        finalized_trials = set()
        for row in rows:
            ev = row.get('event_type')
            req = str(row.get('request_id') or '')
            eid = str(row.get('exp_id') or '')
            if ev == 'pending_delivery' and req and eid:
                pending_deliveries[(req, eid)] = row
            elif ev in ('delivery_sent', 'delivery_cancelled') and req and eid:
                finalized_deliveries.add((req, eid))
            elif ev == 'trial_started' and row.get('trial_id'):
                pending_trials[str(row['trial_id'])] = row
            elif ev in ('trial_delivered', 'trial_cancelled') and row.get('trial_id'):
                finalized_trials.add(str(row['trial_id']))

        unresolved = {k: v for k, v in pending_deliveries.items() if k not in finalized_deliveries}
        unresolved_trials = {k: v for k, v in pending_trials.items() if k not in finalized_trials}
        if not unresolved and not unresolved_trials:
            return {'checked': 0, 'sent': 0, 'cancelled': 0, 'still_pending': 0}

        req_ids = {k[0] for k in unresolved} | {str(v.get('request_id') or '') for v in unresolved_trials.values()}
        req_ids.discard('')
        auto_ids = {str(v.get('autonomy_run_id') or '') for v in unresolved.values()} | {
            str(v.get('autonomy_run_id') or '') for v in unresolved_trials.values()
        }
        auto_ids.discard('')
        delivered_reqs, failed_reqs, delivered_auto = self._delivery_truth(req_ids, auto_ids)

        now_dt = _parse_dt(self._now())
        to_write = []
        sent_count = 0
        cancelled_count = 0
        still_pending = 0
        stamp = self._now()

        def is_stale(row):
            if force_stale:
                return True
            row_dt = _parse_dt(row.get('at'))
            if now_dt is None or row_dt is None:
                return True
            return (now_dt - row_dt).total_seconds() >= max(0, int(stale_seconds))

        for (req, eid), row in unresolved.items():
            if req in active:
                still_pending += 1
                continue
            auto_id = str(row.get('autonomy_run_id') or '')
            if req in delivered_reqs or (auto_id and auto_id in delivered_auto):
                to_write.append({
                    'at': stamp, 'request_id': req, 'exp_id': eid,
                    'event_type': 'delivery_sent', 'result_id': 'delivery',
                    'session_key': str(row.get('session_key') or ''),
                    'entry_mode': str(row.get('entry_mode') or 'chat'),
                    'autonomy_run_id': auto_id,
                    'reconciled': True,
                })
                sent_count += 1
            elif req in failed_reqs or is_stale(row):
                to_write.append({
                    'at': stamp, 'request_id': req, 'exp_id': eid,
                    'event_type': 'delivery_cancelled', 'result_id': 'delivery',
                    'reconciled': True,
                })
                cancelled_count += 1
            else:
                still_pending += 1

        for tid, row in unresolved_trials.items():
            req = str(row.get('request_id') or '')
            if req in active:
                continue
            auto_id = str(row.get('autonomy_run_id') or '')
            if req in delivered_reqs or (auto_id and auto_id in delivered_auto):
                to_write.append({
                    'at': stamp, 'request_id': req, 'exp_id': str(row.get('exp_id') or ''),
                    'event_type': 'trial_delivered', 'result_id': f'trial:{tid}:delivered',
                    'trial_id': tid, 'reconciled': True,
                })
            elif req in failed_reqs or is_stale(row):
                to_write.append({
                    'at': stamp, 'request_id': req, 'exp_id': str(row.get('exp_id') or ''),
                    'event_type': 'trial_cancelled', 'result_id': f'trial:{tid}:cancelled',
                    'trial_id': tid, 'reconciled': True,
                })

        self._append_records(to_write)
        return {
            'checked': len(unresolved) + len(unresolved_trials),
            'sent': sent_count,
            'cancelled': cancelled_count,
            'still_pending': still_pending,
        }

    def active_or_completed_trial_exp_ids(self):
        """Return experience IDs that already have a delivered/active/completed trial (prevent spam)."""
        rows = self.read_all()
        started = {}
        cancelled = set()
        for row in rows:
            tid = str(row.get('trial_id') or '')
            eid = str(row.get('exp_id') or '')
            if not tid or not eid:
                continue
            ev = row.get('event_type')
            if ev == 'trial_started':
                started[tid] = eid
            elif ev == 'trial_cancelled':
                cancelled.add(tid)
        return {eid for tid, eid in started.items() if tid not in cancelled}
