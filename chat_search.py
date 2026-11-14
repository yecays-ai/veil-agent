"""Ticket 221: Peter looks back through the saved chat log himself when he cannot remember something.

Read-only. The whole chat log is searched (not only the recent rounds sent to the model). Messages that contain
the clue words come back with their time and a few messages around them, in the original words.
"""
import re
from datetime import datetime

CONTEXT_EACH_SIDE = 2
MAX_EXCERPTS = 5
MAX_KEYWORDS = 6
BROWSE_MESSAGES = 30
MESSAGE_CHARS = 200
RESULT_BYTES = 8000  # same ceiling as other tool results (max_result_bytes 8192)
NEIGHBOUR_WINDOW = 3  # keywords found within this many messages of each other count as appearing together

TOOL_NAME = 'search_chat_history'
TOOL_SCHEMA = {'type': 'function', 'function': {
    'name': TOOL_NAME,
    'description': ('Search the saved chat log with lan by clue words when you cannot recall something she '
                    'mentions; returns the matching messages with time and the messages around them. '
                    'For "yesterday" etc. work out the date yourself; since/until alone gives an overview. '
                    'Read-only.'),
    'parameters': {'type': 'object', 'properties': {
        'keywords': {'type': 'array', 'items': {'type': 'string', 'maxLength': 40},
                     'maxItems': MAX_KEYWORDS,
                     'description': 'One to a few clue words; add synonyms or variants as extra words.'},
        'since': {'type': 'string', 'maxLength': 32, 'description': 'Optional, YYYY-MM-DD, local date.'},
        'until': {'type': 'string', 'maxLength': 32, 'description': 'Optional, YYYY-MM-DD, local date.'},
    }, 'additionalProperties': False},
}}


def split_keywords(value):
    """Accept a list or a single string; clue words may be glued with commas or spaces."""
    items = value if isinstance(value, list) else [value]
    words = []
    for item in items:
        if not isinstance(item, str):
            continue
        for word in re.split(r'[\s,，、;；/]+', item.strip()):
            word = word.strip()
            if word and word.casefold() not in [w.casefold() for w in words]:
                words.append(word)
    return words[:MAX_KEYWORDS]


def _parse_day(text):
    if text is None or text == '':
        return None
    if not isinstance(text, str):
        return False
    try:
        return datetime.strptime(text.strip(), '%Y-%m-%d').date()
    except ValueError:
        return False


def _local(stamp):
    try:
        return datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone()
    except (AttributeError, ValueError):
        return None


def _shorten(text, words):
    text = ' '.join(text.split())
    if len(text) <= MESSAGE_CHARS:
        return text
    lowered = text.casefold()
    positions = [lowered.find(w.casefold()) for w in words if lowered.find(w.casefold()) >= 0]
    centre = min(positions) if positions else 0
    start = max(0, min(centre - MESSAGE_CHARS // 3, len(text) - MESSAGE_CHARS))
    return ('…' if start else '') + text[start:start + MESSAGE_CHARS] + ('…' if start + MESSAGE_CHARS < len(text) else '')


def _browse(rows, first, last):
    """Ticket 354: what was said in a period, without clue words: evenly spaced messages, in order."""
    picked = []
    for row in rows:
        content = row.get('content') if isinstance(row, dict) else None
        if row.get('role') not in ('user', 'assistant') or not isinstance(content, str) or row.get('recalled'):
            continue
        when = _local(row.get('time'))
        if not when or (first and when.date() < first) or (last and when.date() > last):
            continue
        picked.append((when, row))
    scope = f'{first or "the start"} to {last or "now"}'
    if not picked:
        return (f'No saved messages from {scope}. Tell lan you found nothing for that time; do not guess '
                'what was said.'), False
    step = max(1, -(-len(picked) // BROWSE_MESSAGES))
    shown = picked[::step]
    lines = [f'Saved chat from {scope}: {len(picked)} messages, showing {len(shown)} evenly spaced, in order. '
             'Search with keywords in the same period for the exact part you need.']
    for when, row in shown:
        who = 'lan' if row['role'] == 'user' else 'you'
        lines.append(f'{when.strftime("%Y-%m-%d %H:%M")} {who}: {_shorten(row["content"], [])}')
    text = '\n'.join(lines)
    if len(text.encode('utf-8')) > RESULT_BYTES:
        text = text.encode('utf-8')[:RESULT_BYTES].decode('utf-8', 'ignore').rstrip() + '\n…(cut; narrow the dates)'
    return text, True


def search_chat(rows, keywords, since=None, until=None):
    """Return (text, found). `rows` is the chat log in order; each row has role, content, time."""
    words = split_keywords(keywords)
    first, last = _parse_day(since), _parse_day(until)
    if first is False or last is False:
        return 'since and until must look like 2026-11-05.', False
    if not words:
        if not (first or last):
            return 'Give at least one keyword, or a date in since/until.', False
        return _browse(rows, first, last)
    folded = [w.casefold() for w in words]
    usable = []
    for index, row in enumerate(rows):
        content = row.get('content') if isinstance(row, dict) else None
        if row.get('role') not in ('user', 'assistant') or not isinstance(content, str) or row.get('recalled'):
            continue
        usable.append(index)
    usable_set = set(usable)
    matches = {}
    for index in usable:
        row = rows[index]
        when = _local(row.get('time'))
        if when and ((first and when.date() < first) or (last and when.date() > last)):
            continue
        if (first or last) and not when:
            continue
        text = row['content'].casefold()
        hit = {i for i, w in enumerate(folded) if w in text}
        if hit:
            matches[index] = hit
    if not matches:
        scope = ' in that period' if first or last else ''
        return (f'No messages containing {", ".join(words)} were found{scope} in the saved chat log. '
                'Tell lan you could not find it; do not guess what was said.'), False
    scored = []
    for index, hit in matches.items():
        together = set(hit)
        for other in range(index - NEIGHBOUR_WINDOW, index + NEIGHBOUR_WINDOW + 1):
            if other != index and other in matches:
                together |= matches[other]
        scored.append((len(together), len(hit), index, together))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2]))
    chosen, covered = [], set()
    for together_count, _own, index, together in scored:
        if len(chosen) >= MAX_EXCERPTS:
            break
        span = range(max(0, index - CONTEXT_EACH_SIDE), min(len(rows), index + CONTEXT_EACH_SIDE + 1))
        if index in covered:
            continue
        chosen.append((index, together_count, together, [i for i in span if i not in covered]))
        covered.update(span)
    pieces = [f'Searched the whole saved chat log for: {", ".join(words)}. Best matches first '
              f'(of {len(matches)} matching messages); [*] marks a message that contains a clue word.']
    for number, (index, count, together, rows_to_show) in enumerate(chosen, 1):
        found = ', '.join(words[i] for i in sorted(together))
        label = 'all clue words near each other' if count == len(words) and len(words) > 1 else (
            f'{count} of {len(words)} clue words ({found})' if len(words) > 1 else f'contains {found}')
        lines = [f'#{number} {label}']
        for i in rows_to_show:
            row = rows[i]
            if i not in usable_set:
                continue
            when = _local(row.get('time'))
            stamp = when.strftime('%Y-%m-%d %H:%M') if when else 'unknown time'
            who = 'lan' if row['role'] == 'user' else 'you'
            mark = '[*] ' if i in matches else ''
            body = _shorten(row['content'], words)
            lines.append(f'{mark}{stamp} {who}: {body}')
        pieces.append('\n'.join(lines))
    text = '\n\n'.join(pieces)
    if len(text.encode('utf-8')) > RESULT_BYTES:
        text = text.encode('utf-8')[:RESULT_BYTES].decode('utf-8', 'ignore').rstrip() + (
            '\n…(cut; narrow with more specific words or since/until)')
    return text, True
