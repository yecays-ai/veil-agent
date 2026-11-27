"""Background-only, metered verification of the current relationship evidence basis."""
import json
import time
from dataclasses import replace
from decimal import Decimal

from openai import APITimeoutError

from veil import prompts
from veil.core.agent_types import BudgetExceeded, UserError

COST_CATEGORY = 'relationship_semantic'
TICK_SECONDS = 30
MAX_INPUT_TOKENS = 16_000
MAX_OUTPUT_TOKENS = 2_048
MAX_RESPONSE_CHARS = 16_000
# Some providers wrap JSON in a single Markdown fence despite response_format=json_object.
_CODE_FENCE = chr(96) * 3


def _contains_secret(*values):
    """Use the shared activity matcher without its truncation or redaction."""
    from veil.brain.experience_memory import SECRET
    from veil.core.veil_agent import (
        NATURAL_API_KEY_VALUE, OPAQUE_ACTIVITY_TOKEN, SENSITIVE_ACTIVITY_VALUE, _sensitive_activity_key,
    )

    def matches(value):
        if isinstance(value, dict):
            return any(_sensitive_activity_key(k) or matches(k) or matches(v) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return any(matches(v) for v in value)
        if not isinstance(value, str):
            return False
        if any(pattern.search(value) for pattern in (SECRET, SENSITIVE_ACTIVITY_VALUE, NATURAL_API_KEY_VALUE,
                                                      OPAQUE_ACTIVITY_TOKEN)):
            return True
        # Chat content can itself be structured JSON with secret-named keys.
        if value.lstrip().startswith(('{', '[')):
            try:
                return matches(json.loads(value))
            except ValueError:
                pass
        return False

    return any(matches(value) for value in values)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate JSON key')
        obj[key] = value
    return obj


def _ids(rows):
    if not isinstance(rows, list):
        raise ValueError('invalid evidence list')
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid evidence row')
        item_id = row.get('id')
        if not isinstance(item_id, str) or not item_id or item_id in ids:
            raise ValueError('invalid evidence ID')
        if not isinstance(row.get('text'), str) or not row['text'].strip():
            raise ValueError('missing evidence text')
        ids.add(item_id)
    return ids


def _verdict(output, evidence_ids, counter_ids):
    if not isinstance(output, str) or len(output) > MAX_RESPONSE_CHARS:
        raise ValueError('invalid response size')
    text = output.strip()
    if text.startswith(_CODE_FENCE) and text.endswith(_CODE_FENCE):
        newline = text.find('\n')
        inner = text[newline + 1:-3].strip() if newline != -1 else ''
        if inner:
            text = inner
    result = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(result, dict) or result.keys() != {
        'verdict', 'supporting_evidence_ids', 'counterevidence',
    }:
        raise ValueError('invalid result fields')
    verdict = result['verdict']
    if verdict not in ('supported', 'insufficient', 'contradicted'):
        raise ValueError('invalid verdict')
    support = result['supporting_evidence_ids']
    if (not isinstance(support, list) or any(not isinstance(i, str) or i not in evidence_ids for i in support)
            or len(set(support)) != len(support)):
        raise ValueError('invalid citations')
    checked = {}
    if not isinstance(result['counterevidence'], list):
        raise ValueError('invalid counterevidence list')
    for row in result['counterevidence']:
        if not isinstance(row, dict) or row.keys() != {'id', 'verdict'}:
            raise ValueError('invalid counterevidence fields')
        item_id, status = row['id'], row['verdict']
        if (not isinstance(item_id, str) or item_id not in counter_ids or item_id in checked
                or status not in ('unrelated', 'conflict', 'uncertain')):
            raise ValueError('invalid counterevidence verdict')
        checked[item_id] = status
    if checked.keys() != counter_ids:
        raise ValueError('missing counterevidence decision')
    active = set(checked.values())
    if verdict == 'supported' and (not support or active - {'unrelated'}):
        raise ValueError('unsupported approval')
    if 'conflict' in active and verdict != 'contradicted':
        raise ValueError('inconsistent conflict decision')
    return verdict


class RelationshipSemanticWorker:
    """Process one still-current candidate per tick, without retrying a dispatched claim."""

    def __init__(self, cfg, budget, model, settings, *, profile=None):
        self.cfg = cfg
        self.budget = budget
        self.model = model
        self.settings = settings
        self.profile = profile if profile is not None else cfg.data_dir

    def tick(self, stopped=lambda: False):
        from veil.brain.learned_context import pending_relationship_semantics
        from veil.core.model_settings import SettingsError

        if stopped() or self.cfg.provider.get('missing'):
            return False
        try:
            settings = self.settings()
            cap = Decimal(settings['daily_cap_cny'])
            timeout = settings['timeout_seconds']
        except (SettingsError, KeyError, TypeError, ArithmeticError):
            return False  # settings unavailable: do not read evidence or reserve money
        if (not cap.is_finite() or cap <= 0 or type(timeout) is not int or not 1 <= timeout <= 60):
            return False
        tasks = pending_relationship_semantics(self.profile)
        if not tasks or stopped():
            return False
        return self._verify(tasks[0], cap, timeout, stopped)

    def _verify(self, task, cap, timeout, stopped):
        from veil.brain.learned_context import claim_relationship_semantic, finish_relationship_semantic
        from veil.core.veil_agent import RequestNotSent, request_byte_size

        claim_id, payload = task['claim_id'], task['payload']

        def finish(verdict, reason=''):
            return finish_relationship_semantic(self.profile, claim_id, verdict=verdict, reason=reason)

        try:
            candidate = payload['candidate']
            if (not isinstance(candidate, dict) or not isinstance(candidate.get('text'), str)
                    or not candidate['text'].strip()):
                raise ValueError('missing candidate text')
            evidence_ids, counter_ids = _ids(payload['evidence']), _ids(payload['counterevidence'])
            system = prompts.text('memory.relationship.semantic_verify')
            if _contains_secret(payload, system):
                return finish('failed', 'sensitive_payload')
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            messages = [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': body},
            ]
            input_bytes = request_byte_size(messages)
        except (ValueError, TypeError, KeyError, RecursionError):
            return finish('failed', 'invalid_payload')

        provider = dict(self.cfg.provider)
        # One token per serialized byte is the conservative text guard used here;
        # never remove evidence to meet it. Lower the request's accounting bound,
        # not the provider settings or the foreground context budget.
        if input_bytes > min(provider['max_input_tokens'], MAX_INPUT_TOKENS):
            return finish('failed', 'payload_too_large')
        provider['max_input_tokens'] = input_bytes
        request_cfg = replace(self.cfg, provider=provider,
                              max_output_tokens=min(self.cfg.max_output_tokens, MAX_OUTPUT_TOKENS))
        if stopped():
            return False
        try:
            reservation = self.budget.reserve(request_cfg, category=COST_CATEGORY,
                                              accounting=COST_CATEGORY, category_daily=cap)
        except BudgetExceeded:
            return finish('budget', 'budget_exhausted')
        except UserError:
            return False  # the ledger is not currently safe to use
        request_id, _ = reservation
        marked = False
        try:
            if stopped() or not claim_relationship_semantic(self.profile, claim_id):
                return False
            if stopped():
                return finish('failed', 'request_not_sent')
            self.budget.mark_dispatched(request_id)
            marked = True
        finally:
            if not marked:
                self.budget.release_not_sent(request_id)

        def retain_upper_bound(reason):
            # No trustworthy usage: consume the reserved upper bound, without carrying
            # an unresolved hold across future budget periods. Recovery does the same.
            try:
                self.budget.settle_estimate(reservation, accounting=COST_CATEGORY + '-unknown-usage')
            finally:
                finish('failed', reason)
            return False

        start = time.monotonic()
        try:
            # The fixed startup background provider uses the standard OpenAI adapter
            # (max_retries=0). Its raw entry avoids holding Chat's model settings locks.
            output, prompt, completion, cached, stop_reason = self.model(
                messages, json_output=True, max_tokens=request_cfg.max_output_tokens,
                thinking='disabled', detail=True, request_timeout=timeout,
            )
        except RequestNotSent:
            try:
                self.budget.release_not_sent(request_id)
            finally:
                finish('failed', 'request_not_sent')
            return False
        except (TimeoutError, APITimeoutError):
            return retain_upper_bound('timeout')
        except Exception:
            return retain_upper_bound('provider_error')
        late = time.monotonic() - start > timeout
        try:
            self.budget.settle(request_id, request_cfg, prompt, completion, cached,
                               input_bytes=input_bytes, category=COST_CATEGORY, accounting=COST_CATEGORY)
        except UserError:
            return retain_upper_bound('invalid_usage')
        if late or stopped():
            return finish('failed', 'timeout' if late else 'provider_error')
        if stop_reason != 'stop':
            return finish('failed', 'invalid_response')
        try:
            verdict = _verdict(output, evidence_ids, counter_ids)
        except (ValueError, RecursionError):
            return finish('failed', 'invalid_response')
        return finish(verdict)
