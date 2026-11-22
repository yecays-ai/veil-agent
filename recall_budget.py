"""Ticket 419: one whole-round budget for the memories sent with one request.

Why this module exists
----------------------
Before this ticket the request builder trimmed the chat history first and then dropped recalled
memories until the request fit. That protected old memories at the cost of the conversation the
current message depends on, and it could throw away a whole memory after the recall code had
already decided it was relevant. It also meant each recalled memory was cut to a fixed 600
characters and at most three were kept.

This module answers one question for the request builder: given the space that is actually left
after the required request blocks, how much of it should go to the conversation, to optional
auxiliary context and to memories, and which memories fit. It never invents a fixed character or
token cap:

- The total is the provider's own ``max_input_tokens`` minus measured required blocks.
- The newest complete exchange is protected as a measured floor. The rest is divided between the
  older history, the memories and optional auxiliary context in proportion to their measured size.
- A memory that does not fit whole is offered as complete paragraphs (or whole sentences) plus an
  explicit pointer to the read tool, never cut mid-sentence. When not even one whole sentence fits,
  the block carries only its ID and the read pointer, saying plainly that the body was not shown.
- Everything is measured with the same token estimate the request builder uses, including JSON
  escaping and the message framing.

The token arithmetic mirrors ``veil_agent.estimate_message_tokens`` (UTF-8 bytes of the message
JSON + per-message framing, times the same safety calibration). A test asserts the two stay in
step so a later change to one estimator cannot silently drift from the other.
"""
import json
import re

from recall_rotation import sentence_parts

# Same framing reserve as veil_agent.message_request_bytes.
MESSAGE_FRAMING_BYTES = 16


def ratio(value):
    """The same clamp veil_agent applies to a calibration value."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.4
    return max(0.15, min(1.0, number))


def message_bytes(text, role='system'):
    """Bytes the provider sees for one single-text message, framed like veil_agent does."""
    return len(json.dumps({'role': role, 'content': text}, ensure_ascii=False).encode('utf-8')) + MESSAGE_FRAMING_BYTES


def tokens_for(text, calibration):
    """Estimated tokens for one single-text message."""
    return int(-(-message_bytes(text) * ratio(calibration) // 1))


def tokens_for_messages(messages, calibration):
    """Estimated tokens for several messages, matching veil_agent.estimate_message_tokens."""
    total = sum(message_bytes(message.get('content') if isinstance(message.get('content'), str) else '',
                              message.get('role') or 'system') for message in messages if isinstance(message, dict))
    return int(-(-total * ratio(calibration) // 1))


def protected_recent_tokens(history_events, calibration):
    """Tokens of the newest complete exchange (her message plus his reply).

    This is the part of the conversation that makes the current message understandable: what she
    just complained about, what he answered, what "she" refers to. It is measured from the actual
    history, so a long or short exchange protects exactly itself instead of a guessed number.
    """
    if not history_events:
        return 0
    picked = []
    seen_assistant = False
    for row in reversed(history_events):
        role = row.get('role')
        content = row.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        picked.append({'role': role, 'content': content})
        if role == 'assistant':
            seen_assistant = True
        elif seen_assistant:
            break   # the user message that prompted that reply closes the protected exchange
    return tokens_for_messages(list(reversed(picked)), calibration)


def split(space, history_need, memory_need, protected, auxiliary_need=0):
    """How many tokens of ``space`` go to history, memories and optional auxiliary context.

    ``space`` is what is left after the required blocks. The newest complete exchange
    (``protected``) is a floor for history: it survives before anything optional is budgeted. When
    all needs fit, each keeps its own. Otherwise the space above the floor is divided in proportion
    to what each actually measured, so neither a flood of memories nor a large optional background
    block can silently erase the recent conversation. The result is non-negative and sums to at
    most ``space``.
    """
    space = max(0, int(space))
    history_need = max(0, int(history_need))
    memory_need = max(0, int(memory_need))
    auxiliary_need = max(0, int(auxiliary_need))
    protected = min(max(0, int(protected)), space)
    if history_need + memory_need + auxiliary_need <= space:
        return {'history': history_need, 'memory': memory_need, 'auxiliary': auxiliary_need,
                'reason': 'fits', 'history_need': history_need, 'memory_need': memory_need,
                'auxiliary_need': auxiliary_need, 'space': space, 'protected': protected}
    rest = space - protected
    older_history = max(0, history_need - protected)
    total = older_history + memory_need + auxiliary_need
    if total <= 0:
        return {'history': protected, 'memory': 0, 'auxiliary': 0,
                'reason': 'protected', 'history_need': history_need, 'memory_need': memory_need,
                'auxiliary_need': auxiliary_need, 'space': space, 'protected': protected}
    history_share = int(rest * older_history / total)
    memory_share = int(rest * memory_need / total)
    if auxiliary_need <= 0:
        history_share = rest - memory_share
        auxiliary_share = 0
    else:
        auxiliary_share = max(0, rest - history_share - memory_share)
    return {'history': protected + history_share, 'memory': memory_share, 'auxiliary': auxiliary_share,
            'reason': 'proportional', 'history_need': history_need, 'memory_need': memory_need,
            'auxiliary_need': auxiliary_need, 'space': space, 'protected': protected}


_PARAGRAPH = re.compile(r'\n\s*\n')


def _paragraphs(text):
    return [part for part in _PARAGRAPH.split(text) if part.strip()]


def _sentences(paragraph):
    return list(sentence_parts(paragraph))


def partial_text(text, allowance_tokens, calibration, pointer=''):
    """The longest run of complete paragraphs/sentences that fits, plus an explicit pointer.

    Returns ``(body, pieces, continues)`` or ``(None, 0, False)`` when not even one whole sentence
    fits. Every candidate is measured with ``tokens_for`` (so JSON escaping, the pointer and the
    message framing are counted), not by raw character count. Nothing is cut mid-sentence: when a
    single sentence is too long, only the caller's pointer (and the block's ID) is sent.
    """
    if allowance_tokens <= 0 or not text:
        return None, 0, False
    header = ''
    if text.startswith('[bucket_id:'):
        header, sep, rest = text.partition('\n')
        text = rest if sep else ''
        if not text:
            return None, 0, False
    prefix = (header + '\n') if header else ''
    pointer_tail = ('\n' + pointer) if pointer else ''

    def fits(candidate):
        return tokens_for(prefix + candidate + pointer_tail, calibration) <= allowance_tokens

    kept = []
    for paragraph in _paragraphs(text):
        candidate = '\n\n'.join(kept + [paragraph])
        if fits(candidate):
            kept.append(paragraph)
            continue
        if not kept:
            for sentence in _sentences(paragraph):
                candidate = ('\n\n'.join(kept + [sentence])) if kept else sentence
                if fits(candidate):
                    kept.append(sentence)
                else:
                    break
        break
    if not kept:
        return None, 0, False
    return prefix + '\n\n'.join(kept).rstrip(), len(kept), True


def _first_line(text):
    return text.split('\n', 1)[0] if isinstance(text, str) else ''


def not_shown_text(block):
    """Header + clear 'body not shown' pointer when not even one full sentence fits."""
    header = _first_line(block.get('text') or '')
    pointer = block.get('pointer') or ''
    return (header + '\n(The full text was not shown in this request: even one complete sentence '
            'does not fit the memory budget. ' + pointer + ')').strip()


def fit(blocks, allowance, calibration):
    """Pick blocks in priority order until ``allowance`` tokens are used.

    ``blocks`` is a list of dicts with at least ``id``, ``text``, ``pointer`` and ``priority``
    (lower first). Whole blocks win over partial ones; a block too big for the remaining room is
    offered as complete paragraphs only when at least one whole sentence fits. Otherwise it is
    still represented by its ID and read pointer, so the fact remains reachable without cutting the
    text. Returns ``(selected, dropped)``; ``selected`` keeps the input order of ``blocks``.
    """
    ordered = sorted(enumerate(blocks), key=lambda item: (item[1].get('priority', 0), item[0]))
    remaining = max(0, int(allowance))
    chosen = {}
    dropped = []
    oversize = []
    for index, block in ordered:
        text = block.get('text') or ''
        cost = tokens_for(text, calibration)
        if cost <= remaining:
            chosen[index] = {**block, 'tokens': cost, 'complete': True}
            remaining -= cost
        else:
            oversize.append((index, block, cost))
    if oversize:
        for index, block, cost in oversize:
            pointer = block.get('pointer') or ''
            body, pieces, _ = partial_text(block.get('text') or '', remaining, calibration, pointer)
            if body:
                text = body + (('\n' + pointer) if pointer else '')
                chosen[index] = {**block, 'text': text, 'tokens': tokens_for(text, calibration),
                                 'complete': False, 'paragraphs': pieces, 'full_tokens': cost}
                remaining = max(0, remaining - chosen[index]['tokens'])
                break
            pointer_only = not_shown_text(block)
            if pointer_only and tokens_for(pointer_only, calibration) <= remaining:
                chosen[index] = {**block, 'text': pointer_only,
                                 'tokens': tokens_for(pointer_only, calibration),
                                 'complete': False, 'not_shown': True, 'full_tokens': cost}
                remaining = max(0, remaining - chosen[index]['tokens'])
                break
        for index, block, cost in oversize:
            if index in chosen:
                continue
            dropped.append({'id': block.get('id'), 'reason': 'budget', 'tokens': cost})
    selected = [chosen[index] for index in sorted(chosen)]
    return selected, dropped
