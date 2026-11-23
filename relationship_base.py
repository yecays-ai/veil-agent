"""Ticket 422: the small relationship base carried into every normal request.

Learned Context already reaches the model: facts and growth in the stable prefix, ongoing near the
user message. What it could not express is *which* of those notes is current: a rule Lan confirmed,
a habit that repeated evidence settled, and Peter's own guess all looked the same, and an old rule
could keep acting as if it were still true.

This module reads the `relationship` items from the same Learned Context file and renders the few
that are still in force into one small block, with its own hard budget. It is a view over the
existing store: no second memory engine, no new database.

- `confirmed` items are current and are rendered plainly.
- `uncertain` items are Peter's own reading and are labelled as such, so a guess cannot pass as a
  shared agreement.
- `outdated` items are history. They are kept in the file (and readable there), never rendered.
- An item that does not fit the small budget is left out whole; a sentence is never cut in half.
  When anything is left out for space the result records `degraded` and why.
- Notes already carried verbatim by facts/growth are not rendered twice.
"""
import prompts

DEFAULTS = {'enabled': True, 'max_chars': 480, 'max_items': 8}
# How many dropped items a diagnostic keeps naming; the rest are only counted.
DROP_LIST_LIMIT = 40

# Higher first: Lan's own word, then repeated evidence, then Peter's reading; newest wins inside a rank.
_SOURCE_RANK = {'lan': 3, 'evidence': 2, 'peter': 1}
_STATUS_RANK = {'confirmed': 0, 'uncertain': 1}


def settings(profile_cfg):
    """`[profile] relationship_base` with defaults; a bad value falls back, never raises."""
    raw = (profile_cfg or {}).get('relationship_base')
    raw = raw if isinstance(raw, dict) else {}
    result = dict(DEFAULTS)
    if raw.get('enabled') is False:
        result['enabled'] = False
    for key, low, high in (('max_chars', 80, 4000), ('max_items', 1, 40)):
        value = raw.get(key)
        if type(value) is int and low <= value <= high:
            result[key] = value
    return result


def _normal(text):
    return ' '.join(str(text or '').split())


def _note_line(line):
    """One note line, normalized, without a markdown bullet."""
    return _normal(str(line).lstrip('-*• ').strip())


def _line(item):
    text = _normal(item.get('text'))
    if item.get('status') == 'uncertain':
        return '(not confirmed yet) ' + text
    return text


def _order(items):
    # Priority: confirmed before a guess, Lan's word before evidence before Peter's reading; inside one
    # rank the newest item wins, so fresh understanding is not squeezed out by older rows.
    ordered = sorted(items, key=lambda item: str(item.get('id') or ''))
    ordered.sort(key=lambda item: str(item.get('updated_at') or ''), reverse=True)
    ordered.sort(key=lambda item: (_STATUS_RANK.get(item.get('status'), 9),
                                   -_SOURCE_RANK.get(item.get('source'), 0)))
    return ordered


def select(items, *, notes='', max_chars=480, max_items=8):
    """The items that still fit, in priority order, plus every item left out and why.

    Whole items only. `notes` is the facts + growth text already sent; an item whose text is already
    there verbatim is not repeated."""
    notes_lines = {_note_line(line) for line in str(notes or '').splitlines() if line.strip()}
    kept = []
    seen_text = set()
    dropped = []
    dropped_total = 0
    space_drops = 0
    used = 0
    slots = 0

    def note(item_id, reason, *, space=False):
        nonlocal dropped_total, space_drops
        dropped_total += 1
        if space:
            space_drops += 1
        if len(dropped) < DROP_LIST_LIMIT:
            dropped.append({'id': item_id, 'reason': reason})

    for item in _order([row for row in items if isinstance(row, dict)]):
        item_id = str(item.get('id') or '')
        status = item.get('status')
        if status == 'outdated':
            note(item_id, 'outdated')
            continue
        text = _normal(item.get('text'))
        if not text:
            note(item_id, 'empty')
            continue
        if text in notes_lines:
            note(item_id, 'duplicate')
            continue
        if slots >= max_items:
            note(item_id, 'item_cap', space=True)
            continue
        if text in seen_text:
            note(item_id, 'duplicate')
            continue
        line = _line(item)
        cost = len(line) + (1 if kept else 0)
        if used + cost > max_chars:
            note(item_id, 'space', space=True)
            continue
        kept.append({'id': item_id, 'status': status, 'source': item.get('source'),
                     'chars': len(line), 'line': line})
        seen_text.add(text)
        used += cost
        slots += 1
    reason = ''
    if space_drops:
        reasons = {row['reason'] for row in dropped if row['reason'] in ('space', 'item_cap')}
        reason = 'space' if reasons == {'space'} else ('item_cap' if reasons == {'item_cap'} else 'space_and_cap')
    # Only what a diagnostic page can read: a store big enough to drop thousands of items must not
    # turn every request into a large list. The counts stay exact.
    return {'items': kept, 'dropped': dropped, 'dropped_more': max(0, dropped_total - len(dropped)),
            'dropped_total': dropped_total, 'chars': used,
            'degraded': bool(space_drops), 'reason': reason}


def build(profile, profile_cfg=None):
    """Render this request's relationship base, or a result saying why there is none."""
    options = settings(profile_cfg)
    result = {'text': '', 'header': '', 'items': [], 'dropped': [], 'chars': 0,
              'degraded': False, 'reason': ''}
    if not options['enabled']:
        result['reason'] = 'disabled'
        return result
    items = profile.relationship_items() if hasattr(profile, 'relationship_items') else []
    if not items:
        result['reason'] = 'no_items'
        return result
    notes = profile.notes_text() if hasattr(profile, 'notes_text') else ''
    chosen = select(items, notes=notes, max_chars=options['max_chars'],
                    max_items=options['max_items'])
    result.update({'items': chosen['items'], 'dropped': chosen['dropped'],
                   'dropped_more': chosen.get('dropped_more', 0),
                   'dropped_total': chosen.get('dropped_total', len(chosen['dropped'])),
                   'chars': chosen['chars'], 'degraded': chosen['degraded'],
                   'reason': chosen['reason']})
    if not chosen['items']:
        if not chosen['reason']:
            result['reason'] = 'nothing_current'
        return result
    result['header'] = prompts.text('memory.relationship_base.header')
    result['text'] = '\n'.join(item['line'] for item in chosen['items'])
    return result


def message(result):
    """The request message for a non-empty base, or None when there is nothing current to carry."""
    if not isinstance(result, dict) or not result.get('text'):
        return None
    return {'role': 'system', 'content': result['header'] + '\n' + result['text']}
