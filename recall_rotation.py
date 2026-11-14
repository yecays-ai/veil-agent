"""Ticket 353: keep automatic recall from circling the same few memories.

Pure helpers used by `memory_agent.Memory.context()`. Nothing here rewrites a memory: it only
decides which of the candidates Ombre already returned are shown this turn, and how much of each.

- Cooldown: a memory shown automatically is not shown automatically again for `cooldown_turns`
  turns. A cooled candidate is dropped, not replaced by a weaker one, so a turn may show none.
- Same event: two candidates count as one event when Ombre linked them (`same_event`,
  `continuation_of`, `continues`) or their stored content vectors are at least
  `dedup_similarity` apart; only the first is shown.
- Rotation (opening and autonomous wakes, whose query does not name a topic): candidates are
  ordered by importance, and among equal importance the one shown longest ago comes first.
- Long memories: only the title, date and opening of the body are shown, with a pointer to
  `memory_read` for the whole text.
- Random recall: Ombre's own "忽然想起来" section, which its search adds when it found fewer than
  three hits, may contribute one memory, labelled as random.
"""
import re

DEFAULTS = {
    'cooldown_turns': 6,
    'dedup': True,
    'dedup_similarity': 0.88,
    'excerpt_chars': 600,
    'random_chance': 0.1,
    'rotation_candidates': 8,
}
EVENT_LINKS = frozenset({'same_event', 'continuation_of', 'continues'})
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
                'random_chance': 0.0, 'rotation_candidates': 0}
    for key in ('cooldown_turns', 'excerpt_chars', 'rotation_candidates'):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 10_000:
            result[key] = value
    if type(raw.get('dedup')) is bool:
        result['dedup'] = raw['dedup']
    for key in ('dedup_similarity', 'random_chance'):
        value = raw.get(key)
        if type(value) in (int, float) and 0 <= value <= 1:
            result[key] = float(value)
    if result['excerpt_chars'] and result['excerpt_chars'] < 120:
        result['excerpt_chars'] = 120  # shorter than this cannot carry a recognisable opening
    return result


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
        if key in ('title', 'name', 'created', 'importance', 'pinned', 'type'):
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


def linked_as_event(a, b, metas):
    for left, right in ((a, b), (b, a)):
        for target, kind in metas.get(left, {}).get('links', []):
            if target == right and kind in EVENT_LINKS:
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


def rotate(candidates, metas, history):
    """Higher importance first; among equals, the one shown longest ago (or never) first."""
    def importance(key):
        value = metas.get(key, {}).get('importance')
        return value if type(value) is int else 0
    order = {key: index for index, (key, _) in enumerate(candidates)}
    return sorted(candidates, key=lambda item: (-importance(item[0]), history.get(item[0], -1), order[item[0]]))


def excerpt(key, body, meta, limit):
    """Title, date and the opening of a long memory; short ones pass through unchanged."""
    if not limit or len(body) <= limit:
        return body
    header, _, text = body.partition('\n')
    title = meta.get('title') or meta.get('name') or ''
    created = (meta.get('created') or '')[:10]
    label = ' · '.join(part for part in (title, created) if part)
    cut = text[:limit].rstrip()
    return (f'{header}{(" " + label) if label else ""}\n{cut}…\n'
            f'(Long memory: only its beginning is shown. Use memory_read with memory_id "{key}" '
            'if you need the rest.)')


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
