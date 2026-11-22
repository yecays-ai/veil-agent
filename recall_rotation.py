"""Ticket 353: keep automatic recall from circling the same few memories.

Pure helpers used by `memory_agent.Memory.context()`. Nothing here rewrites a memory: it only
decides which of the candidates Ombre already returned are shown this turn, and how much of each.

- Cooldown: a memory shown automatically is not shown automatically again for `cooldown_turns`
  turns. A cooled candidate is dropped, not replaced by a weaker one, so a turn may show none.
- Same event: two candidates count as one event (a repeat) when Ombre linked them as `same_event` or their
  stored content vectors are at least `dedup_similarity` apart; only the first is shown.
- Stages (ticket 369): memories joined by `continuation_of`, `continues` or Peter's own link_to notes are
  different stages of one experience, not repeats. Each may be recalled when it is itself a hit, but one
  experience takes at most `stage_slots` of the recalled memories (the rest stay in the directory line).
- Rotation (opening and autonomous wakes, whose query does not name a topic): candidates are
  ordered by importance, and among equal importance the one shown longest ago comes first.
- Long memories: the whole body is shown by default. ``excerpt_chars`` is 0 (no per-memory cap);
  an old config that still sets a number keeps that limit, but the cut now lands on a paragraph or
  sentence boundary and always says how to read the rest.
- Random recall: Ombre's own "忽然想起来" section, which its search adds when it found fewer than
  three hits, may contribute one memory, labelled as random.
"""
import re
from datetime import datetime, timezone

DEFAULTS = {
    'cooldown_turns': 3,
    'dedup': True,
    'dedup_similarity': 0.88,
    'excerpt_chars': 0,
    'random_chance': 0.1,
    'rotation_candidates': 8,
    'directory': True,
    'directory_members': 6,
    'stage_slots': 2,
}
EVENT_LINKS = frozenset({'same_event', 'continuation_of', 'continues'})   # everything that joins an experience
REPEAT_LINKS = frozenset({'same_event'})                                # the same thing twice: shown once
HISTORY_LIMIT = 400


def blocks(text):
    """Same split as memory_agent.blocks (kept here to avoid a circular import)."""
    matches = list(re.finditer(r'\[bucket_id:([\w-]+)\]', text))
    return [(m.group(1), text[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)])
            for i, m in enumerate(matches)]


def settings(memory_cfg):
    """`[memory.recall]` with defaults; a bad value falls back to its default, never raises."""
    raw = (memory_cfg or {}).get('recall')
    raw = raw if isinstance(raw, dict) else {}
    result = dict(DEFAULTS)
    if raw.get('enabled') is False:
        return {**result, 'cooldown_turns': 0, 'dedup': False, 'excerpt_chars': 0,
                'random_chance': 0.0, 'rotation_candidates': 0, 'directory': False}
    for key in ('cooldown_turns', 'excerpt_chars', 'rotation_candidates'):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 10_000:
            result[key] = value
    for key in ('dedup', 'directory'):
        if type(raw.get(key)) is bool:
            result[key] = raw[key]
    members = raw.get('directory_members')
    if type(members) is int and 2 <= members <= 12:
        result['directory_members'] = members
    slots = raw.get('stage_slots')
    if type(slots) is int and 1 <= slots <= 3:
        result['stage_slots'] = slots
    for key in ('dedup_similarity', 'random_chance'):
        value = raw.get(key)
        if type(value) in (int, float) and 0 <= value <= 1:
            result[key] = float(value)
    if result['excerpt_chars'] and result['excerpt_chars'] < 120:
        result['excerpt_chars'] = 120  # shorter than this cannot carry a recognisable opening
    return result


_PAST = re.compile(
    r'记得|还记不记得|想起|想起来|上次|上回|那次|那天|那晚|那时|当时|去年|前年|以前|之前|刚才说|'
    r'remember|recall|last time|that time|back when|earlier|you said|we talked', re.I)

# Ticket 419: a sentence that continues the thing just discussed. These are not queries on their
# own ("那我呢", "后来呢"), so the previous turn's judged memories stay in play for this turn.
_FOLLOWUP = re.compile(
    r'后来|然后|接着|之后|结果呢|结果|还有呢|那我|她的反应|我的反应|你当时|那时候|那次|'
    r'what happened next|and then|after that|how did (she|you) react|what about', re.I)


def asks_about_past(text):
    """The user is pointing at something that already happened ("还记得…", "上次那次…").

    Cooldown only exists so unrelated old memories do not keep coming back unasked; when she asks
    about the past herself, hiding the memory she means is the worse failure, so it is let through."""
    return bool(isinstance(text, str) and _PAST.search(text))


def is_followup(query, recent=None):
    """A short continuation of the previous turn, or a past question right after one.

    Ticket 419: "同一对话连续问 V50，再问「那我当时是什么反应？」" used to lose V50 because the
    follow-up sentence had no word Ombre could search for and the cooldown dropped the memory the
    previous turn had just shown. Following up on the thing just discussed is the opposite of
    unrelated chat, so it keeps that turn's context available."""
    if asks_about_past(query):
        return True
    if isinstance(query, str) and _FOLLOWUP.search(query):
        return True
    for row in reversed(list(recent or [])[-4:]):
        if row.get('role') == 'user' and asks_about_past(row.get('content')):
            return True
    return False



def drift_hits(text):
    """Memories in Ombre's "忽然想起来（非检索命中）" section, cleaned of separators."""
    if not isinstance(text, str) or '=== 忽然想起来' not in text:
        return []
    section = text.split('=== 忽然想起来', 1)[1].split('=== ombre:result-ids ===')[0]
    found = []
    for key, body in blocks(section):
        body = re.sub(r'(?:\s*\n---\s*)?(?:\s*\[联想浮现·非检索命中\]\s*)?$', '', body.rstrip())
        found.append((key, body.rstrip()))
    return found


def front_matter(raw):
    """Scalars and relation links from one Ombre Markdown file. Unknown shapes are left out."""
    meta = {'links': []}
    if not isinstance(raw, str):
        return meta
    lines = raw.splitlines()
    if not lines or lines[0].strip() != '---':
        return meta
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), len(lines))
    header = lines[1:end]
    meta['body'] = '\n'.join(lines[end + 1:])
    index = 0
    while index < len(header):
        line = header[index]
        key, sep, value = line.partition(':')
        index += 1
        if not sep or line[:1].isspace():
            continue
        key, value = key.strip(), value.strip()
        if key in ('id', 'title', 'name', 'created', 'importance', 'pinned', 'type'):
            meta[key] = value.strip('\'"')
        elif key == 'relation_links' and not value:
            current = None
            while index < len(header) and (header[index][:1].isspace() or header[index].startswith('-')):
                item = header[index].strip()
                index += 1
                if item.startswith('-'):
                    current = {}
                    meta['links'].append(current)
                    item = item[1:].strip()
                name, sep, val = item.partition(':')
                if sep and current is not None:
                    current[name.strip()] = val.strip().strip('\'"')
    try:
        meta['importance'] = int(meta.get('importance', ''))
    except ValueError:
        meta['importance'] = None
    meta['links'] = [(link.get('target_bucket_id'), link.get('type'))
                     for link in meta['links']
                     if link.get('target_bucket_id') and link.get('status', 'active') == 'active']
    return meta


def linked_as_event(a, b, metas, kinds=REPEAT_LINKS):
    for left, right in ((a, b), (b, a)):
        for target, kind in metas.get(left, {}).get('links', []):
            if target == right and kind in kinds:
                return True
    return False


def same_event(a, b, metas, vectors, threshold, cosine):
    if linked_as_event(a, b, metas):
        return True
    if a in vectors and b in vectors:
        try:
            return cosine(vectors[a], vectors[b]) >= threshold
        except ValueError:
            return False
    return False


def experience_chain(key, neighbours, limit=12):
    """Ticket 365: every memory reachable from `key` through same-experience links, `key` first, at most `limit`.
    `neighbours(id)` returns the ids linked to one memory in either direction."""
    order = [key]
    seen = {key}
    for node in order:
        for other in sorted(neighbours(node)):
            if other not in seen and len(seen) < limit:
                seen.add(other)
                order.append(other)
    return order


def created_key(created):
    """Sort key for a memory's creation time. Whole timestamps, so stages written on the same day keep the order
    they happened in; a bare date or something unreadable still sorts (by its text) instead of raising."""
    try:
        moment = datetime.fromisoformat(created)
    except (TypeError, ValueError):
        return (1, 0.0, str(created))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (0, moment.timestamp(), '')


def directory_line(key, entries, size, event_id=''):
    """One line listing the other memories of the experience `key` belongs to: date and title only, oldest first,
    at most `size` of them around `key`. `entries` are (memory_id, created, title). Empty when `key` stands alone.

    Ticket 419: when the memory is part of an event, the line also names the event id and says how to
    read the whole experience, so a story glimpsed through one stage can be recovered on demand."""
    if len(entries) < 2:
        return ''
    year = next((created[:4] for memory_id, created, _ in entries if memory_id == key and created), '')
    ordered = sorted(entries, key=lambda item: (not item[1], created_key(item[1]), item[0]))
    position = next(index for index, item in enumerate(ordered) if item[0] == key)
    start = max(0, min(position - size // 2, len(ordered) - size))
    parts = []
    for memory_id, created, title in ordered[start:start + size]:
        date = created[:10] if created[:4] != year else created[5:10]
        label = ' '.join(part for part in (date, ' '.join(title.split())[:24]) if part) or memory_id
        parts.append(f'{label} (this one)' if memory_id == key else f'{label} [{memory_id}]')
    more = '…' if start else ''
    after = ' → …' if start + size < len(ordered) else ''
    line = ('(This memory is one stage of a longer experience, oldest first: ' + more
            + ' → '.join(parts) + after + '. Titles and dates only; memory_read an ID to read that stage.')
    if event_id:
        line += (f' The whole experience can be read with memory_timeline, event_id "{event_id}";'
                 ' read it before answering about what came later.')
    return line + ')'


def rotate(candidates, metas, history):
    """Higher importance first; among equals, the one shown longest ago (or never) first."""
    def importance(key):
        value = metas.get(key, {}).get('importance')
        return value if type(value) is int else 0
    order = {key: index for index, (key, _) in enumerate(candidates)}
    return sorted(candidates, key=lambda item: (-importance(item[0]), history.get(item[0], -1), order[item[0]]))


def _cut_on_boundary(text, limit):
    """The longest run of whole paragraphs (or, when the first paragraph is too long, whole
    sentences) that is not over ``limit`` characters. Never cuts mid-sentence."""
    if limit <= 0:
        return ''
    kept = []
    used = 0
    for paragraph in re.split(r'\n\s*\n', text):
        if not paragraph.strip():
            continue
        cost = len(paragraph) + (2 if kept else 0)
        if used + cost <= limit:
            kept.append(paragraph)
            used += cost
            continue
        if not kept:
            for sentence in re.split(r'(?<=[.!?。！？])\s+', paragraph):
                if not sentence.strip():
                    continue
                cost = len(sentence) + (1 if kept else 0)
                if used + cost > limit:
                    break
                kept.append(sentence)
                used += cost
        break
    return '\n\n'.join(kept).rstrip()


def excerpt(key, body, meta, limit, event_id=''):
    """The whole memory by default; a legacy character limit still cuts, but on a boundary.

    ``limit`` 0 means no per-memory cap. When a number is set (an old config), the opening is kept
    only up to that many characters, at a paragraph or sentence boundary, followed by a pointer to
    the read tools so the rest is never lost."""
    if not limit or len(body) <= limit:
        return body
    header, _, text = body.partition('\n')
    title = meta.get('title') or meta.get('name') or ''
    created = (meta.get('created') or '')[:10]
    label = ' · '.join(part for part in (title, created) if part)
    cut = _cut_on_boundary(text, max(1, limit - len(header) - len(label) - 1))
    if not cut:
        cut = text[:max(1, limit)].rstrip()
    where = 'memory_read with memory_id "%s"' % key
    if event_id:
        where += ' (or memory_timeline with event_id "%s" for the whole experience)' % event_id
    return (f'{header}{(" " + label) if label else ""}\n{cut}…\n'
            f'(Only the beginning is shown because of the configured character limit. Use {where} '
            'for the rest.)')


class Cooldown:
    """Turn counter and the last turn each memory was shown automatically. IDs only, no text."""

    def __init__(self, state=None):
        state = state if isinstance(state, dict) else {}
        turn = state.get('turn')
        self.turn = turn if type(turn) is int and turn >= 0 else 0
        last = state.get('last')
        self.last = {key: value for key, value in (last.items() if isinstance(last, dict) else [])
                     if isinstance(key, str) and type(value) is int and 0 <= value <= self.turn}

    def cooled(self, key, turns):
        return bool(turns) and key in self.last and self.turn - self.last[key] <= turns

    def mark(self, keys):
        for key in keys:
            self.last.pop(key, None)
            self.last[key] = self.turn
        while len(self.last) > HISTORY_LIMIT:
            self.last.pop(next(iter(self.last)))

    def forget(self, key):
        self.last.pop(key, None)

    def snapshot(self):
        return {'turn': self.turn, 'last': dict(self.last)}
