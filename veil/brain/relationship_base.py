"""Ticket 422 / P6-S3: the small relationship base carried into every normal request.

This module reads the `relationship` items from Learned Context and renders the current ones into
one small block, with its own hard presentation budget (default 480 chars / 8 items).
- `locked` and `confirmed` items are prioritized first.
- `uncertain` (or evidence-degraded) items are labelled `(not confirmed yet)` so a guess or
  stale-evidence item never passes as confirmed consensus.
- `outdated` items are history and never rendered.
- High-priority items that exceed remaining budget use semantic-preserving clause compression at
  presentation time (never mutating disk text or history).
- Notes already carried verbatim by facts/growth are not rendered twice; conflicts with outdated
  or vetoed relationship rules are surfaced in diagnostics.
"""
import re

from veil import prompts

DEFAULTS = {'enabled': True, 'max_chars': 480, 'max_items': 8}
DROP_LIST_LIMIT = 40

_SOURCE_RANK = {'lan': 3, 'evidence': 2, 'peter': 1}
_STATUS_RANK = {'confirmed': 0, 'uncertain': 1}
_NOTE_MARKER = re.compile(r'^(?:[-*+•]\s+|\d{1,3}[.)]\s+)')
_CLAUSE_SPLIT = re.compile(r'(?<=[。；;！!？?，,])\s*')
_BOUNDARY_HINT = re.compile(r'(?:不|别|不要|不能|禁止|勿|除非|除了|但|但是|never|don\'t|not|unless|except|only\s+when)', re.IGNORECASE)


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
    """One note line, normalized, without one markdown list marker."""
    return _normal(_NOTE_MARKER.sub('', str(line).strip(), count=1))


def _effective_status(item):
    eff = item.get('effective_status') or item.get('status')
    if item.get('evidence_degraded'):
        return 'uncertain'
    return eff


def _line(item):
    text = _normal(item.get('text'))
    if _effective_status(item) == 'uncertain':
        return '(not confirmed yet) ' + text
    return text


def _compress_line(line, budget_chars):
    """Compress a high-priority presentation line without cutting mid-clause or dropping negations."""
    if budget_chars < 24 or len(line) <= budget_chars:
        return None
    has_boundary = bool(_BOUNDARY_HINT.search(line))
    target = budget_chars - 1  # reserve 1 char for '…'
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(line) if c.strip()]
    if len(clauses) > 1:
        kept = []
        used = 0
        for clause in clauses:
            sep = 1 if kept and clause[0].isascii() else 0
            if used + sep + len(clause) <= target:
                kept.append(clause)
                used += sep + len(clause)
            else:
                break
        if kept and len(kept) < len(clauses):
            for idx in range(len(kept), len(clauses)):
                if _BOUNDARY_HINT.search(clauses[idx]):
                    candidate = ' '.join([clauses[0], clauses[idx]]) if clauses[0].isascii() else (clauses[0] + clauses[idx])
                    if len(candidate) <= target:
                        return candidate + '…'
                    break
        if kept:
            joined = (' '.join(kept) if kept[0].isascii() else ''.join(kept)).rstrip('，,;； ')
            if len(joined) >= 16 and (not has_boundary or _BOUNDARY_HINT.search(joined)):
                return joined + '…'
    cut = line[:target]
    if ' ' in cut and cut[-1].isascii() and line[target:target + 1].isascii():
        cut = cut.rsplit(' ', 1)[0]
    cut = cut.rstrip('，,;； ')
    if len(cut) < 16:
        return None
    if has_boundary and not _BOUNDARY_HINT.search(cut):
        return None
    return cut + '…'


def _order(items):
    ordered = sorted(items, key=lambda item: str(item.get('id') or ''))
    ordered.sort(key=lambda item: str(item.get('updated_at') or ''), reverse=True)
    ordered.sort(key=lambda item: (
        _STATUS_RANK.get(_effective_status(item), 9),
        0 if item.get('locked') else 1,
        -_SOURCE_RANK.get(item.get('source'), 0),
    ))
    return ordered


def select(items, *, notes='', conflict_texts=(), max_chars=480, max_items=8):
    """Select items that fit in priority order, plus every item left out and why."""
    notes_lines = {_note_line(line) for line in str(notes or '').splitlines() if line.strip()}
    conflict_norms = {_note_line(t).lower() for t in (conflict_texts or ()) if str(t or '').strip()}
    cross_section_conflicts = [
        line for line in notes_lines if line.lower() in conflict_norms
    ]

    kept = []
    seen_text = set()
    dropped = []
    compressed_items = []
    dropped_total = 0
    space_drops = 0
    used = 0
    slots = 0
    valid_rows = [row for row in items if isinstance(row, dict)]
    stored_count = len(valid_rows)
    active_count = 0

    def note(item_id, reason, *, space=False):
        nonlocal dropped_total, space_drops
        dropped_total += 1
        if space:
            space_drops += 1
        if len(dropped) < DROP_LIST_LIMIT:
            dropped.append({'id': item_id, 'reason': reason})

    for item in _order(valid_rows):
        item_id = str(item.get('id') or '')
        status = item.get('status')
        if status == 'outdated':
            note(item_id, 'outdated')
            continue
        active_count += 1
        eff_status = _effective_status(item)
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
        sep = 1 if kept else 0
        cost = len(line) + sep
        if used + cost > max_chars:
            remaining = max_chars - used - sep
            compressed = None
            if eff_status == 'confirmed' or item.get('locked'):
                compressed = _compress_line(line, remaining)
            if compressed is not None:
                kept.append({
                    'id': item_id, 'status': eff_status, 'stored_status': status,
                    'source': item.get('source'), 'locked': bool(item.get('locked')),
                    'stale_ref': bool(item.get('stale_ref')),
                    'evidence_degraded': bool(item.get('evidence_degraded')),
                    'compressed': True, 'original_chars': len(line),
                    'chars': len(compressed), 'line': compressed,
                })
                compressed_items.append(item_id)
                seen_text.add(text)
                used += sep + len(compressed)
                slots += 1
                continue
            note(item_id, 'space', space=True)
            continue
        kept.append({
            'id': item_id, 'status': eff_status, 'stored_status': status,
            'source': item.get('source'), 'locked': bool(item.get('locked')),
            'stale_ref': bool(item.get('stale_ref')),
            'evidence_degraded': bool(item.get('evidence_degraded')),
            'compressed': False,
            'chars': len(line), 'line': line,
        })
        seen_text.add(text)
        used += cost
        slots += 1

    reason = ''
    if space_drops:
        reasons = {row['reason'] for row in dropped if row['reason'] in ('space', 'item_cap')}
        reason = 'space' if reasons == {'space'} else ('item_cap' if reasons == {'item_cap'} else 'space_and_cap')
    elif compressed_items:
        reason = 'compressed'

    return {
        'items': kept,
        'dropped': dropped,
        'dropped_more': max(0, dropped_total - len(dropped)),
        'dropped_total': dropped_total,
        'stored_count': stored_count,
        'active_count': active_count,
        'selected_count': len(kept),
        'compressed_items': compressed_items,
        'cross_section_conflicts': cross_section_conflicts,
        'chars': used,
        'degraded': bool(space_drops or compressed_items),
        'reason': reason,
    }


def build(profile, profile_cfg=None):
    """Render this request's relationship base, or a result saying why there is none."""
    options = settings(profile_cfg)
    result = {
        'text': '', 'header': '', 'items': [], 'dropped': [], 'chars': 0,
        'stored_count': 0, 'active_count': 0, 'selected_count': 0,
        'compressed_items': [], 'cross_section_conflicts': [],
        'degraded': False, 'reason': '',
    }
    if not options['enabled']:
        result['reason'] = 'disabled'
        return result
    items = profile.relationship_items() if hasattr(profile, 'relationship_items') else []
    if not items:
        result['reason'] = 'no_items'
        return result
    notes = profile.notes_text() if hasattr(profile, 'notes_text') else ''
    conflicts = (
        profile.relationship_conflict_texts()
        if hasattr(profile, 'relationship_conflict_texts') else ()
    )
    chosen = select(
        items, notes=notes, conflict_texts=conflicts,
        max_chars=options['max_chars'], max_items=options['max_items'],
    )
    result.update({
        'items': chosen['items'],
        'dropped': chosen['dropped'],
        'dropped_more': chosen.get('dropped_more', 0),
        'dropped_total': chosen.get('dropped_total', len(chosen['dropped'])),
        'stored_count': chosen.get('stored_count', len(items)),
        'active_count': chosen.get('active_count', 0),
        'selected_count': chosen.get('selected_count', len(chosen['items'])),
        'compressed_items': chosen.get('compressed_items', []),
        'cross_section_conflicts': chosen.get('cross_section_conflicts', []),
        'chars': chosen['chars'],
        'degraded': chosen['degraded'],
        'reason': chosen['reason'],
    })
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
