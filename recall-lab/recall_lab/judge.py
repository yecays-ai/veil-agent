"""Recall-judge arms for the Tier 1 ablations.

The baseline path reuses the production card builder (recall_judge.card_text) so the ablation is
the experiment variable, not a re-implementation of ticket 419. The relaxed prompt, the 500-char
card and the threshold gate live here and never touch the production modules.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import core

PROMPT_CONSERVATIVE = 'conservative'
PROMPT_RELAXED = 'relaxed'

RELAXED_PROMPT = """You help Peter, an AI companion, decide which of his saved memories bear on what his partner Lan just said to him.
A memory bears on her message when it connects directly to the same situation, person, recurring pattern, enduring feeling or relationship rule she is talking about now, so that Peter would handle this moment differently for knowing it. A shared broad theme such as tiredness, sleep, games or money is NOT enough on its own, but a memory of a specific shared moment that shaped how he reads her mood, their agreements, or a pattern between them does count.
Pick every memory that directly bears on her message, best first; there is no fixed number. Greetings, small talk, generic questions, practical requests and outside-world facts usually have none. When unsure about a merely thematic overlap, pick none.
Answer with JSON only: {"ids": ["A"]} or {"ids": []}.
"""


def _prompts_module():
    import prompts
    return prompts


def production_prompt(wake=False):
    try:
        return _prompts_module().text('memory.judge.wake' if wake else 'memory.judge.prompt')
    except Exception:
        path = Path(__file__).resolve().parent.parent.parent / 'prompts' / 'defaults'
        name = 'memory.judge.wake.md' if wake else 'memory.judge.prompt.md'
        return (path / name).read_text(encoding='utf-8').strip()


def card_300(raw):
    """Production judge card (ticket 419 behaviour)."""
    from recall_judge import card_text
    return card_text(raw)


def card_500(raw, snapshot=None, memory_id=''):
    """AB-R3d treatment card: 500 chars, adding why_remembered, importance and a story摘要."""
    head, body = _split_card(raw)
    plain = ' '.join((body or '').split())
    title_match = re.search(r'^title:\s*(.*)$', head, re.M) or re.search(r'^name:\s*(.*)$', head, re.M)
    title = title_match.group(1).strip().strip('\'"')[:60] if title_match else ''
    opening = plain[:220]
    closing = ''
    if plain and len(plain) > 260:
        tail = plain[-180:]
        if tail not in opening:
            closing = '…ending: ' + tail
    parts = [title, '…opening: ' + opening if opening else '', closing]
    if snapshot is not None and memory_id:
        memory = snapshot.memories.get(memory_id)
        if memory is not None:
            if memory.why_remembered:
                parts.append('why: ' + ' '.join(memory.why_remembered.split())[:110])
            parts.append('importance: %d' % memory.importance)
        event = snapshot.events.get(memory_id)
        if event is not None and event.story:
            summary = ' '.join(event.story.split())[:120]
            parts.append('story: ' + summary)
    text = ' | '.join(part for part in parts if part)
    return text[:500]


def _split_card(raw):
    from recall_judge import split_card
    return split_card(raw)


@dataclass
class JudgeCall:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    latency_ms: float
    error: str = ''
    error_kind: str = ''      # timeout | request_error | bad_json | format
    output: str = ''
    usage_missing: bool = False   # provider answered without a usage block: never settle at 0


class JudgeClient:
    """One deepseek-flash JSON-output call, priced and booked by the caller."""

    def __init__(self, *, base_url, model, api_key, max_tokens=200):
        self.base_url, self.model, self._api_key = base_url, model, api_key
        self.max_tokens = max_tokens
        self._client = None
        self.calls = 0

    def _ensure(self, timeout):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, base_url=self.base_url,
                                  timeout=timeout, max_retries=0)
        return self._client.with_options(timeout=max(0.5, float(timeout)))

    def call(self, messages, *, timeout):
        started = time.perf_counter()
        client = self._ensure(timeout)
        try:
            response = client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=self.max_tokens,
                stream=False, response_format={'type': 'json_object'},
                extra_body={'thinking': {'type': 'disabled'}})
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            kind = 'timeout' if 'timeout' in type(exc).__name__.casefold() else 'request_error'
            return JudgeCall(text='', prompt_tokens=0, completion_tokens=0, cached_tokens=0,
                             latency_ms=round(elapsed, 2),
                             error='%s: %s' % (type(exc).__name__, str(exc)[:200]),
                             error_kind=kind)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.calls += 1
        usage = getattr(response, 'usage', None)
        usage_missing = usage is None
        prompt_tokens = int(getattr(usage, 'prompt_tokens', 0) or 0)
        completion_tokens = int(getattr(usage, 'completion_tokens', 0) or 0)
        cached = 0
        for field_name in ('prompt_cache_hit_tokens', 'cache_read_input_tokens'):
            value = getattr(usage, field_name, None)
            if type(value) is int and 0 <= value <= prompt_tokens:
                cached = value
                break
        content = response.choices[0].message.content or ''
        return JudgeCall(text=content, prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens, cached_tokens=cached,
                         latency_ms=round(elapsed, 2), output=content,
                         usage_missing=usage_missing)

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass


def build_messages(query, ordered_candidates, snapshot, *, card='300', prompt=PROMPT_CONSERVATIVE,
                   wake=False):
    """Production layout: one system prompt, one user message listing the shuffled cards."""
    from recall_judge import labels_for
    letters = labels_for(len(ordered_candidates))
    lines = []
    for letter, (memory_id, _body) in zip(letters, ordered_candidates):
        raw = ''
        if memory_id in snapshot.memories:
            raw = snapshot.memories[memory_id].raw
        if card == '500':
            text = card_500(raw, snapshot, memory_id)
        else:
            text = card_300(raw)
        lines.append('%s) %s' % (letter, text))
    system = RELAXED_PROMPT if prompt == PROMPT_RELAXED else production_prompt(wake=wake)
    context_label = 'Autonomous activity context' if wake else 'Lan just said'
    user = '%s: "%s"\n\nSaved memories:\n%s' % (context_label, query[:600], '\n'.join(lines))
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], letters


def parse_picked(call, letters):
    """(picked ids, error kind). Mirrors recall_judge.parse_choice's contract."""
    from recall_judge import parse_choice, JudgeFailed
    if call.error:
        return [], call.error_kind
    try:
        # production parse_choice maps labels to keys; here labels and keys are the same letter.
        picked = parse_choice(call.text, {letter: letter for letter in letters})
    except JudgeFailed as exc:
        return [], 'bad_json' if exc.reason == 'bad_json' else 'bad_format'
    return picked, ''


def threshold_gate(candidate_vector_scores, ranked_ids, *, threshold=0.45, limit=3):
    """AB-R3b offline arm: keep candidates whose stored-vector cosine clears a threshold."""
    picked = []
    for memory_id in ranked_ids:
        if len(picked) >= limit:
            break
        if candidate_vector_scores.get(memory_id, 0.0) >= threshold:
            picked.append(memory_id)
    return picked