"""Ticket 413: load the reviewed final event set into a dedicated data directory.

This is intentionally a file-level importer: it writes the same Markdown vault and event
ledger that the running app already reads.  It never starts or connects to a memory
service, and it refuses to touch a non-import event that happens to share an ID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

IMPORT_VERSION = 1
STORY_SOURCE = 'final-import'
FOLDER = '事件'


def _json_text(value):
    return json.dumps(value, ensure_ascii=False)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _source_dates(sources):
    dates = [str(s.get('recorded_at') or '') for s in sources if isinstance(s, dict)]
    dates = [d for d in dates if d]
    return (min(dates), max(dates)) if dates else ('', '')


def _source_ids(event):
    return [str(s.get('memory_id')) for s in event.get('sources') or []
            if isinstance(s, dict) and s.get('memory_id')]


def _slug(title, event_id):
    slug = re.sub(r'[^0-9A-Za-z]+', '_', title or '').strip('_').lower()[:60]
    return slug or re.sub(r'[^0-9A-Za-z_-]+', '_', event_id)


def _dedupe(values):
    seen, out = set(), []
    for value in values:
        text = ' '.join(str(value or '').split())
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _cues(event):
    cues = [str(c) for c in event.get('recall_cues') or []]
    source_titles = [str(s.get('title') or '') for s in event.get('sources') or []
                     if isinstance(s, dict)]
    return _dedupe(cues + source_titles)


def memory_text(event):
    """The exact Markdown Ombre indexes: title, source clues, then the English story."""
    event_id = event['id']
    title = str(event.get('title') or event_id)
    body = str(event.get('english') or '').strip()
    first, last = _source_dates(event.get('sources') or [])
    cues = _cues(event)
    lines = [
        '---',
        'activation_count: 0',
        'arousal: 0.5',
        f'created: {_json_text(first)}',
        'domain:', '- 事件',
        f'id: {event_id}',
        'importance: 8',
        f'last_active: {_json_text(last)}',
        f'name: {_json_text(title)}',
        f'recall_cues: {_json_text(cues)}',
        'import_marker: final-import',
        'source_tool: hold',
        'tags:', '- event', '- final',
        f'title: {_json_text(title)}',
        'type: dynamic',
        'valence: 0.5',
        f'why_remembered: {_json_text("Final event memory imported for review.")}',
        '---',
    ]
    if cues:
        lines.append('检索词：' + '；'.join(cues))
    lines.extend(['', body, ''])
    return '\n'.join(lines)


def event_signature(event):
    payload = {
        'id': event.get('id'), 'title': event.get('title'), 'english': event.get('english'),
        'sources': event.get('sources'), 'recall_cues': event.get('recall_cues'),
    }
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def event_row(event, signature):
    first, last = _source_dates(event.get('sources') or [])
    return {
        'event_id': event['id'], 'memory_id': event['id'],
        'title': event.get('title') or event['id'],
        'story': str(event.get('english') or '').strip(),
        'first_at': first, 'last_at': last,
        'status': 'open', 'confidence': 1.0, 'story_source': STORY_SOURCE,
        'import_signature': signature,
        'source_memory_ids': _source_ids(event),
        'source_parts': [{'memory_id': s.get('memory_id'), 'part': s.get('part', '')}
                         for s in event.get('sources') or [] if isinstance(s, dict)],
        'sources': event.get('sources') or [],
        'previous_events': event.get('previous_events') or [],
        'related_events': event.get('related_events') or [],
        'approved': event.get('approved'),
        'created_at': first, 'updated_at': last,
    }


def stage_row(event):
    first, _ = _source_dates(event.get('sources') or [])
    return {
        'stage_id': f's-{event["id"]}-1', 'event_id': event['id'],
        'at': first, 'text': str(event.get('english') or '')[:600],
        'state': 'committed', 'facts': {},
    }


def _read_rows(path):
    rows = []
    try:
        with Path(path).open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _latest(rows, key):
    latest = {}
    for row in rows:
        if isinstance(row.get(key), str):
            latest[row[key]] = row
    return latest


def _memory_files(vault, event_id):
    found = []
    for path in vault.rglob('*.md'):
        try:
            head = path.read_text(encoding='utf-8').split('\n---\n', 1)[0]
        except OSError:
            continue
        match = re.search(r'^id:\s*(\S+)\s*$', head, re.M)
        if match and match.group(1).strip("'\"") == event_id:
            found.append(path)
    return found


def _is_import_row(row):
    return row.get('story_source') == STORY_SOURCE or bool(row.get('import_signature'))


def _memory_is_import(path):
    try:
        head = path.read_text(encoding='utf-8').split('\n---\n', 1)[0]
    except OSError:
        return False
    return bool(re.search(r'^import_marker:\s*final-import\s*$', head, re.M))


def build_plan(final, data_dir, role='peter', force=False):
    events = final.get('events')
    if not isinstance(events, list):
        raise ValueError('final file has no events list')
    ids = [str(e.get('id') or '') for e in events if isinstance(e, dict)]
    if len(ids) != len(events) or any(not i for i in ids):
        raise ValueError('every event needs a non-empty id')
    if len(set(ids)) != len(ids):
        raise ValueError('event ids are not unique')
    data_dir = Path(data_dir)
    vault = data_dir / role / 'memory'
    existing_events = _latest(_read_rows(data_dir / role / 'events.jsonl'), 'event_id')
    plan = []
    unknown = []
    for event in events:
        if not isinstance(event, dict) or not str(event.get('english') or '').strip():
            raise ValueError(f'{event.get("id")} has no English body')
        event_id = event['id']
        signature = event_signature(event)
        old = existing_events.get(event_id)
        if old is not None and not _is_import_row(old) and not force:
            raise ValueError(f'target already has a non-import event {event_id}; refusing to overwrite')
        old_files = _memory_files(vault, event_id)
        for path in old_files:
            if not _memory_is_import(path) and not force:
                raise ValueError(f'target already has a non-import memory {path.name}; refusing to overwrite')
        first, _ = _source_dates(event.get('sources') or [])
        if not first:
            unknown.append(event_id)
        action = 'unchanged' if old and old.get('import_signature') == signature else ('update' if old else 'create')
        plan.append({
            'event': event, 'event_id': event_id, 'signature': signature,
            'event_row': event_row(event, signature), 'stage_row': stage_row(event),
            'memory_path': vault / 'dynamic' / FOLDER / f'{event_id} {_slug(event.get("title"), event_id)}.md',
            'memory_text': memory_text(event), 'action': action,
            'old_files': old_files,
        })
    withdrawn = final.get('withdrawn') or []
    preview = {
        'target': str(data_dir),
        'role': role,
        'events': len(plan),
        'source_positions': sum(len(e.get('sources') or []) for e in events),
        'source_unique': len({i for e in events for i in _source_ids(e)}),
        'withdrawn': len(withdrawn),
        'unknown_dates': unknown,
        'actions': {name: sum(1 for item in plan if item['action'] == name)
                    for name in ('create', 'update', 'unchanged')},
        'memory_dir': str(vault / 'dynamic' / FOLDER),
        'event_ids': [item['event_id'] for item in plan],
    }
    try:
        from recall_judge import card_text
        preview['judge_cards'] = {item['event_id']: card_text(item['memory_text'])
                                  for item in plan if item['event_id'] in ('V50', 'V78')}
    except Exception:
        preview['judge_cards'] = {}
    return {'preview': preview, 'items': plan, 'withdrawn': withdrawn,
            'final_sha256': _sha(json.dumps(final, ensure_ascii=False, sort_keys=True))}


def _write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _rows_text(rows):
    return ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)


def apply_plan(plan, data_dir, role='peter'):
    data_dir = Path(data_dir)
    role_dir = data_dir / role
    vault = role_dir / 'memory'
    events_path = role_dir / 'events.jsonl'
    stages_path = role_dir / 'event-stages.jsonl'
    review_path = role_dir / 'event-import-review.jsonl'
    withdrawn_path = role_dir / 'event-import-withdrawn.jsonl'
    manifest_path = role_dir / 'event-import-manifest.json'
    stamp = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%f%z')
    backup = role_dir / f'.event-import-backup-{stamp}'
    touched = [events_path, stages_path, review_path, withdrawn_path, manifest_path]
    touched += [item['memory_path'] for item in plan['items']]
    touched += [path for item in plan['items'] for path in item['old_files'] if path not in touched]
    for path in touched:
        if path.exists():
            backup.mkdir(parents=True, exist_ok=True)
            target = backup / path.relative_to(role_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    old_events = _read_rows(events_path)
    old_stages = _read_rows(stages_path)
    import_ids = {item['event_id'] for item in plan['items']}
    row_by_id = {item['event_id']: item['event_row'] for item in plan['items']}
    merged_events = []
    seen = set()
    for row in old_events:
        event_id = row.get('event_id')
        if event_id in import_ids:
            if event_id not in seen:
                merged_events.append(row_by_id[event_id])
                seen.add(event_id)
        else:
            merged_events.append(row)
    for item in plan['items']:
        if item['event_id'] not in seen:
            merged_events.append(item['event_row'])
    merged_stages = [row for row in old_stages if row.get('event_id') not in import_ids]
    merged_stages += [item['stage_row'] for item in plan['items']]

    review = []
    for item in plan['items']:
        event = item['event']
        review.append({
            'event_id': event['id'], 'title': event.get('title'),
            'approved': event.get('approved'), 'chinese': event.get('chinese'),
            'editorial_evidence': event.get('editorial_evidence'),
            'additional_evidence': event.get('additional_evidence'),
            'recall_cues': event.get('recall_cues') or [],
            'sources': event.get('sources') or [],
            'previous_events': event.get('previous_events') or [],
            'related_events': event.get('related_events') or [],
            'body_sha256': _sha(str(event.get('english') or '')),
        })
    manifest = {
        'version': IMPORT_VERSION, 'story_source': STORY_SOURCE,
        'imported_at': datetime.now().astimezone().isoformat(),
        'final_sha256': plan['final_sha256'], 'role': role,
        'event_count': len(plan['items']),
        'source_positions': plan['preview']['source_positions'],
        'source_unique': plan['preview']['source_unique'],
        'withdrawn_count': len(plan['withdrawn']),
        'unknown_dates': plan['preview']['unknown_dates'],
        'memory_dir': str(vault / 'dynamic' / FOLDER),
    }
    try:
        for item in plan['items']:
            for path in item['old_files']:
                if path != item['memory_path'] and _memory_is_import(path):
                    path.unlink()
            _write_atomic(item['memory_path'], item['memory_text'])
        _write_atomic(events_path, _rows_text(merged_events))
        _write_atomic(stages_path, _rows_text(merged_stages))
        _write_atomic(review_path, _rows_text(review))
        _write_atomic(withdrawn_path, _rows_text(plan['withdrawn']))
        _write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    except Exception:
        for path in touched:
            if path.exists():
                path.unlink()
        if backup.exists():
            for source in backup.rglob('*'):
                if source.is_file():
                    target = role_dir / source.relative_to(backup)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        raise
    return manifest


def load_final(path):
    with Path(path).open(encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('final file must be an object')
    return data


def main():
    parser = argparse.ArgumentParser(description='Load reviewed final event memories into a dedicated data directory.')
    parser.add_argument('--final', required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--role', default='peter')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    final = load_final(args.final)
    plan = build_plan(final, args.data_dir, args.role, force=args.force)
    if not args.apply:
        print(json.dumps(plan['preview'], ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(apply_plan(plan, args.data_dir, args.role), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
