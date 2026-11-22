"""Ticket 419: one whole-round budget for the memories sent with one request.

Why this module exists
----------------------
Before this ticket the request builder trimmed the chat history first and then dropped recalled
memories until the request fit. That protected old memories at the cost of the conversation the
current message depends on, and it could throw away a whole memory after the recall code had
already decided it was relevant. It also meant each recalled memory was cut to a fixed 600
characters and at most three were kept.

This module answers one question for the request builder: given the space that is actually left
after the required request blocks, how much of it should go to the conversation and how much to
memories, and which memories fit. It never invents a fixed character or token cap:

- The total is the provider's own ``max_input_tokens`` minus measured required blocks.
- The split between history and memories follows their measured sizes, with the newest complete
  exchange protected as a floor (measured, not a constant).
- A memory that does not fit whole is offered as complete paragraphs (or whole sentences) plus an
  explicit pointer to the read tool, never cut mid-sentence.

The token arithmetic mirrors ``veil_agent.estimate_message_tokens`` (UTF-8 bytes of the message
JSON + per-message framing, times the same safety calibration). A test asserts the two stay in
step so a later change to one estimator cannot silently drift from the other.
"""
import json
import re

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


def split(space, history_need, memory_need, protected):
    """How many tokens of ``space`` go to history and how many to memories.

    ``space`` is what is left after the required blocks. When both needs fit, each keeps its own.
    Otherwise the remaining space is divided in proportion to what each actually measured, so a
    flood of memories cannot silently erase the conversation and a long conversation cannot starve
    memories. ``protected`` (the newest complete exchange) is a floor for history. The result is
    always non-negative and never exceeds ``space``.
    """
    space = max(0, int(space))
    history_need = max(0, int(history_need))
    memory_need = max(0, int(memory_need))
    protected = min(max(0, int(protected)), space)
    if history_need + memory_need <= space:
        return {'history': history_need, 'memory': memory_need,
                'reason': 'fits', 'history_need': history_need, 'memory_need': memory_need,
                'space': space, 'protected': protected}
    total = history_need + memory_need
    proportional = int(space * history_need / total) if total else 0
    history_keep = min(history_need, max(protected, proportional))
    history_keep = min(history_keep, space)
    memory_keep = max(0, space - history_keep)
    return {'history': history_keep, 'memory': memory_keep,
            'reason': 'proportional', 'history_need': history_need, 'memory_need': memory_need,
            'space': space, 'protected': protected}


_PARAGRAPH = re.compile(r'\n\s*\n')
_SENTENCE = re.compile(r'(?<=[.!?。！？])\s+|\n')


def _pieces(text):
    """Paragraphs; a paragraph too long for any partial is further split into whole sentences.

    Returns [(complete_piece, is_sentence_split)] in reading order. The first paragraph is kept as
    one piece when it is reasonably sized, so a single body paragraph is not chopped into
    sentence starts unless it has to be."""
    paragraphs = [part for part in _PARAGRAPH.split(text) if part.strip()]
    if not paragraphs:
        return []
    pieces = []
    for index, paragraph in enumerate(paragraphs):
        pieces.append((paragraph, False))
    return pieces


def _sentence_pieces(paragraph):
    return [part for part in _SENTENCE.split(paragraph) if part.strip()]


def _utf8(text):
    return len(text.encode('utf-8'))


def partial_text(text, allowance_tokens, calibration, pointer):
    """The longest run of complete paragraphs/sentences that fits, plus an explicit pointer.

    Returns ``(body, complete_paragraphs, continuation)`` or ``(None, 0, False)`` when not even one
    whole sentence fits. The pointer is appended by the caller so it can name the exact memory id
    and event id. Nothing is ever cut mid-sentence unless a single sentence alone exceeds the
    allowance, and then it is cut at whitespace with a visible marker.
    """
    room = int(max(0, allowance_tokens) / max(0.15, min(1.0, ratio(calibration))))
    room = max(0, room - MESSAGE_FRAMING_BYTES - _utf8(pointer))
    if room <= 0 or not text:
        return None, 0, False
    used, kept, paragraphs = 0, [], 0
    for paragraph, _ in _pieces(text):
        cost = _utf8(paragraph) + (2 if kept else 0)   # the blank line between kept paragraphs
        if used + cost <= room:
            kept.append(paragraph)
            used += cost
            paragraphs += 1
            continue
        # This paragraph is too long. Try its whole sentences, but only after at least one whole
        # paragraph has been kept (so the story starts at its real beginning).
        if not kept:
            sentence_room = room
            for sentence in _sentence_pieces(paragraph):
                cost = _utf8(sentence) + (1 if kept else 0)
                if used + cost > sentence_room:
                    break
                kept.append(sentence)
                used += cost
            if kept:
                body = '\n'.join(kept).rstrip()
                return body, 0, True
        break
    if not kept:
        return None, 0, False
    return '\n\n'.join(kept).rstrip(), paragraphs, True


def fit(blocks, allowance, calibration):
    """Pick blocks in priority order until ``allowance`` tokens are used.

    ``blocks`` is a list of dicts with at least ``id``, ``text`` and ``priority`` (lower first).
    Whole blocks win over partial ones; a block too big for the remaining room is offered as
    complete paragraphs only when it is the highest-priority thing that fits that way. Returns
    ``(selected, dropped)``; ``selected`` keeps the input order of ``blocks`` so the request can
    show memories in the order they were chosen, not the order they were budgeted.
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
        # Nothing whole fits any more. Give the best remaining memory whole paragraphs instead of
        # dropping the whole story, as long as at least one sentence can be read. If the best one
        # cannot even fit one sentence, try the next before giving up.
        for index, block, cost in oversize:
            pointer = block.get('pointer') or ''
            body, paragraphs, _ = partial_text(block.get('text') or '', remaining, calibration, pointer)
            if not body:
                continue
            text = body + ('\n' + pointer if pointer else '')
            chosen[index] = {**block, 'text': text, 'tokens': tokens_for(text, calibration),
                             'complete': False, 'paragraphs': paragraphs, 'full_tokens': cost}
            remaining = max(0, remaining - chosen[index]['tokens'])
            break
        for index, block, cost in oversize:
            if index in chosen:
                continue
            dropped.append({'id': block.get('id'), 'reason': 'budget', 'tokens': cost})
    selected = [chosen[index] for index in sorted(chosen)]
    return selected, dropped
