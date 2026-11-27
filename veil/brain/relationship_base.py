"""Project current relationship items under a separate presentation budget (ticket 422 / P6-S3).

Only complete claims are rendered: an omitted condition or exception may change the rule.
"""
import re

from veil import prompts

DEFAULTS = {'enabled': True, 'max_chars': 480, 'max_items': 8}
DROP_LIST_LIMIT = 40

_SOURCE_RANK = {'lan': 3, 'evidence': 2, 'peter': 1}
_STATUS_RANK = {'confirmed': 0, 'uncertain': 1}
_NOTE_MARKER = re.compile(r'^(?:[-*+•]\s+|\d{1,3}[.)]\s+)')


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


def _order(items):
    ordered = sorted(items, key=lambda item: str(item.get('id') or ''))
    ordered.sort(key=lambda item: str(item.get('updated_at') or ''), reverse=True)
    ordered.sort(key=lambda item: (
        0 if item.get('locked') else 1,
        _STATUS_RANK.get(_effective_status(item), 9),
        -_SOURCE_RANK.get(item.get('source'), 0),
    ))
    return ordered


def select(items, *, notes='', conflict_texts=(), max_chars=480, max_items=8):
    """Select whole, current items in priority order and report omissions by reason."""
    from veil.brain.learned_context import _matches_governed_claim

    notes_lines = {_note_line(line) for line in str(notes or '').splitlines() if line.strip()}
    cross_section_conflicts = [line for line in sorted(notes_lines) if any(
        _matches_governed_claim(line, text) for text in conflict_texts)]
    kept = []
    seen_text = set()
    dropped = []
    dropped_total = 0
    budget_reasons = set()
    used = 0
    valid_rows = [row for row in items if isinstance(row, dict)]
    active_ids = []

    def note(item_id, reason):
        nonlocal dropped_total
        dropped_total += 1
        if reason in ('space', 'item_cap'):
            budget_reasons.add(reason)
        if len(dropped) < DROP_LIST_LIMIT:
            dropped.append({'id': item_id, 'reason': reason})

    for item in _order(valid_rows):
        item_id = str(item.get('id') or '')
        status = item.get('status')
        if status == 'outdated':
            note(item_id, 'outdated')
            continue
        if item.get('prompt_vetoed'):
            note(item_id, 'vetoed')
            continue
        if (item.get('evidence_state') == 'rebutted' and item.get('source') != 'lan'
                and not item.get('locked')):
            note(item_id, 'rebutted')
            continue
        text = _normal(item.get('text'))
        if not text:
            note(item_id, 'empty')
            continue
        active_ids.append(item_id)
        # Approximate matches are safe for withholding vetoed material, not for deduplication:
        # superficially similar rules can contain different subjects, quantities or exceptions.
        if text in notes_lines or text in seen_text:
            note(item_id, 'duplicate')
            continue
        if len(kept) >= max_items:
            note(item_id, 'item_cap')
            continue
        line = _line(item)
        cost = len(line) + bool(kept)
        if used + cost > max_chars:
            note(item_id, 'space')
            continue
        kept.append({
            'id': item_id, 'status': _effective_status(item), 'stored_status': status,
            'source': item.get('source'), 'locked': bool(item.get('locked')),
            'stale_ref': bool(item.get('stale_ref')),
            'evidence_degraded': bool(item.get('evidence_degraded')),
            'compressed': False, 'chars': len(line), 'line': line,
        })
        seen_text.add(text)
        used += cost

    reason = 'space_and_cap' if len(budget_reasons) > 1 else next(iter(budget_reasons), '')
    return {
        'items': kept,
        'dropped': dropped,
        'dropped_more': dropped_total - len(dropped),
        'dropped_total': dropped_total,
        'stored_count': len(valid_rows),
        'active_count': len(active_ids),
        'active_items': active_ids,
        'selected_count': len(kept),
        'compressed_items': [],
        'cross_section_conflicts': cross_section_conflicts,
        'chars': used,
        'degraded': bool(budget_reasons),
        'reason': reason,
    }


def build(profile, profile_cfg=None, *, projection=None):
    """Render this request's relationship base from the same snapshot as the other sections."""
    options = settings(profile_cfg)
    if projection is None and hasattr(profile, 'build_prompt'):
        projection = profile.build_prompt()
    if projection is not None:
        items = projection['relationship_items']
        notes = projection['notes']
        conflicts = projection['cross_section_conflicts']
    else:
        items = profile.relationship_items() if hasattr(profile, 'relationship_items') else []
        notes = profile.notes_text() if hasattr(profile, 'notes_text') else ''
        conflicts = []
    chosen = select(items, notes=notes, max_chars=options['max_chars'] if options['enabled'] else 0,
                    max_items=options['max_items'])
    chosen['cross_section_conflicts'] = conflicts
    chosen.update(text='', header='')
    if not options['enabled']:
        for row in chosen['dropped']:
            if row['reason'] in ('space', 'duplicate'):
                row['reason'] = 'disabled'
        chosen.update(degraded=False, reason='disabled')
    elif chosen['items']:
        chosen['header'] = prompts.text('memory.relationship_base.header')
        chosen['text'] = '\n'.join(item['line'] for item in chosen['items'])
    elif not chosen['reason']:
        chosen['reason'] = 'nothing_current' if items else 'no_items'
    return chosen


def message(result):
    """The request message for a non-empty base, or None when there is nothing current to carry."""
    if not isinstance(result, dict) or not result.get('text'):
        return None
    return {'role': 'system', 'content': result['header'] + '\n' + result['text']}
