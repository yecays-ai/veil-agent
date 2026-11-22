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
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

IMPORT_VERSION = 1
STORY_SOURCE = 'final-import'
FOLDER = '事件'


def _json_text(value):
    return json.dumps(value, ensure_ascii=False)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _body_hash_text(text):
    parts = str(text or '').split('\n---\n', 1)
    return _sha(parts[1].strip() if len(parts) == 2 else str(text or '').strip())


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


def event_row(event, signature, memory_text, imported_at):
    first, last = _source_dates(event.get('sources') or [])
    story = str(event.get('english') or '').strip()
    stage = stage_row(event)
    return {
        'event_id': event['id'], 'memory_id': event['id'],
        'title': event.get('title') or event['id'],
        'story': story,
        'first_at': first, 'last_at': last,
        'status': 'open', 'confidence': 1.0, 'story_source': STORY_SOURCE,
        'import_signature': signature,
        'import_baseline': {
            'signature': signature,
            'title': event.get('title') or event['id'],
            'story_sha256': _sha(story),
            'stage_id': stage['stage_id'],
            'stage_text_sha256': _sha(stage['text']),
            'memory_sha256': _sha(memory_text),
            'memory_body_sha256': _body_hash_text(memory_text),
            'imported_at': imported_at,
        },
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
        'at': first, 'text': str(event.get('english') or '').strip(),
        'state': 'committed', 'facts': {},
    }

def _read_rows(path):
    rows = []
    try:
        with Path(path).open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f'cannot parse {path}: {exc}') from exc
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


def _stages_for(stages_path, event_id):
    rows = _latest(_read_rows(stages_path), 'stage_id')
    return [row for row in rows.values() if row.get('event_id') == event_id]


def _memory_content(vault, event_id):
    files = _memory_files(vault, event_id)
    if not files:
        return None
    try:
        return files[0].read_text(encoding='utf-8')
    except OSError:
        return None


def _local_changed(existing, stages, memory, baseline, expected_story, expected_memory):
    if existing is None:
        return False
    if existing.get('status') not in (None, 'open'):
        return True
    if baseline:
        if baseline.get('title') and existing.get('title') != baseline.get('title'):
            return True
        if _sha(str(existing.get('story') or '')) != baseline.get('story_sha256'):
            return True
        if len(stages) != 1:
            return True
        stage = stages[0]
        if stage.get('stage_id') != baseline.get('stage_id'):
            return True
        if _sha(str(stage.get('text') or '')) != baseline.get('stage_text_sha256'):
            return True
        if baseline.get('memory_body_sha256'):
            if memory is None or _body_hash_text(memory) != baseline.get('memory_body_sha256'):
                return True
        # Older baselines only kept the full-file hash; frontmatter activation fields
        # change during normal recall, so that legacy value cannot prove a local edit.
        return False
    # Imports made before the baseline field existed: only a single initial stage that still
    # matches the imported story and memory is safe to treat as untouched.
    if len(stages) != 1:
        return True
    if str(existing.get('story') or '').strip() != expected_story.strip():
        return True
    if stages and _sha(str(stages[0].get('text') or '')) != _sha(expected_story):
        return True
    return False


def build_plan(final, data_dir, role='peter'):
    events = final.get('events')
    if not isinstance(events, list):
        raise ValueError('final file has no events list')
    ids = [str(e.get('id') or '') for e in events if isinstance(e, dict)]
    if len(ids) != len(events) or any(not i for i in ids):
        raise ValueError('every event needs a non-empty id')
    if len(set(ids)) != len(ids):
        raise ValueError('event ids are not unique')
    if any(not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z_-]{0,99}', event_id) for event_id in ids):
        raise ValueError('event id contains unsafe path characters')
    if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z_-]{0,99}', str(role)):
        raise ValueError('role contains unsafe path characters')
    data_dir = Path(data_dir)
    role_dir = data_dir / role
    vault = role_dir / 'memory'
    existing_events = _latest(_read_rows(role_dir / 'events.jsonl'), 'event_id')
    plan = []
    unknown = []
    conflicts = []
    for event in events:
        if not isinstance(event, dict) or not str(event.get('english') or '').strip():
            raise ValueError(f'{event.get("id")} has no English body')
        if not re.search(r'[A-Za-z]{2}', str(event.get('english') or '')):
            raise ValueError(f'{event.get("id")} body is not English')
        event_id = event['id']
        signature = event_signature(event)
        expected_story = str(event.get('english') or '').strip()
        text = memory_text(event)
        stage = stage_row(event)
        old = existing_events.get(event_id)
        if old is not None and not _is_import_row(old):
            raise ValueError(f'target already has a non-import event {event_id}; refusing to overwrite')
        old_files = _memory_files(vault, event_id)
        for path in old_files:
            if not _memory_is_import(path):
                raise ValueError(f'target already has a non-import memory {path.name}; refusing to overwrite')
        first, _ = _source_dates(event.get('sources') or [])
        if not first:
            unknown.append(event_id)
        memory = _memory_content(vault, event_id)
        stages = _stages_for(role_dir / 'event-stages.jsonl', event_id) if old else []
        baseline = (old or {}).get('import_baseline') or {}
        old_signature = (old or {}).get('import_signature')
        if old is None:
            source_changed = False
        elif old_signature:
            source_changed = old_signature != signature
        else:
            source_changed = (str(old.get('story') or '').strip() != expected_story
                              or old.get('title') != (event.get('title') or event_id))
        local = _local_changed(old, stages, memory, baseline, expected_story, text)
        if old is None:
            action = 'create'
        elif source_changed and local:
            action = 'conflict'
            conflicts.append(event_id)
        elif source_changed:
            action = 'update'
        elif local:
            action = 'preserved'
        else:
            action = 'unchanged'
        plan.append({
            'event': event, 'event_id': event_id, 'signature': signature,
            'event_row': event_row(event, signature, text,
                                   datetime.now().astimezone().isoformat()),
            'stage_row': stage,
            'memory_path': vault / 'dynamic' / FOLDER / f'{event_id} {_slug(event.get("title"), event_id)}.md',
            'memory_text': text, 'action': action,
            'old_files': old_files,
        })
    withdrawn = final.get('withdrawn') or []
    preview = {
        'target': str(data_dir),
        'character': role,
        'events': len(plan),
        'source_positions': sum(len(e.get('sources') or []) for e in events),
        'source_unique': len({i for e in events for i in _source_ids(e)}),
        'withdrawn': len(withdrawn),
        'unknown_dates': unknown,
        'conflicts': conflicts,
        'actions': {name: sum(1 for item in plan if item['action'] == name)
                    for name in ('create', 'update', 'unchanged', 'preserved', 'conflict')},
        'memory_dir': str(vault / 'dynamic' / FOLDER),
        'event_ids': [item['event_id'] for item in plan],
    }
    try:
        from recall_judge import card_text
        preview['judge_cards'] = {item['event_id']: card_text(item['memory_text'])
                                  for item in plan if item['event_id'] in ('V50', 'V78')}
    except Exception:
        preview['judge_cards'] = {}
    return {'preview': preview, 'items': plan, 'withdrawn': withdrawn, 'final': final,
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


PENDING_NAME = 'event-import-pending.json'


def _restore_backup(role_dir, backup):
    if not backup.exists():
        return
    for source in backup.rglob('*'):
        if source.is_file():
            target = role_dir / source.relative_to(backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _recover_pending(role_dir):
    marker = role_dir / PENDING_NAME
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
        _restore_backup(role_dir, Path(data.get('backup') or ''))
    finally:
        marker.unlink(missing_ok=True)
    return True


def apply_plan(plan, data_dir, role='peter'):
    """Take the data lock, revalidate the preview, then write the same plan."""
    from veil_agent import Store
    data_dir = Path(data_dir)
    with Store(data_dir).lock():
        _recover_pending(data_dir / role)
        fresh = build_plan(plan['final'], data_dir, role)
        old = [(item['event_id'], item['action']) for item in plan['items']]
        new = [(item['event_id'], item['action']) for item in fresh['items']]
        if old != new:
            raise ValueError('target changed after preview; refusing to use a stale import plan')
        return _apply_plan_unlocked(fresh, data_dir, role)


def _apply_plan_unlocked(plan, data_dir, role='peter'):
    conflicts = plan['preview'].get('conflicts') or []
    if conflicts:
        raise ValueError('source final changed after local edits; refusing to overwrite: ' + ', '.join(conflicts))
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
    write_items = [item for item in plan['items'] if item['action'] in ('create', 'update')]
    keep_items = [item for item in plan['items'] if item['action'] in ('unchanged', 'preserved')]
    touched = [events_path, stages_path, review_path, withdrawn_path, manifest_path]
    touched += [item['memory_path'] for item in write_items]
    touched += [path for item in write_items for path in item['old_files'] if path not in touched]
    for path in touched:
        if path.exists():
            backup.mkdir(parents=True, exist_ok=True)
            target = backup / path.relative_to(role_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    marker = role_dir / PENDING_NAME
    _write_atomic(marker, json.dumps({'backup': str(backup), 'at': datetime.now().astimezone().isoformat()},
                                    ensure_ascii=False))
    old_events = _read_rows(events_path)
    old_stages = _read_rows(stages_path)
    write_ids = {item['event_id'] for item in write_items}
    keep_ids = {item['event_id'] for item in keep_items}
    row_by_id = {item['event_id']: item['event_row'] for item in write_items}
    merged_events = []
    seen = set()
    for row in old_events:
        event_id = row.get('event_id')
        if event_id in write_ids:
            if event_id not in seen:
                merged_events.append(row_by_id[event_id])
                seen.add(event_id)
        elif event_id in keep_ids:
            merged_events.append(row)
            seen.add(event_id)
        else:
            merged_events.append(row)
    for item in write_items:
        if item['event_id'] not in seen:
            merged_events.append(item['event_row'])
    merged_stages = [row for row in old_stages if row.get('event_id') not in write_ids]
    merged_stages += [item['stage_row'] for item in write_items]

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
        'final_sha256': plan['final_sha256'], 'character': role,
        'event_count': len(plan['items']),
        'source_positions': plan['preview']['source_positions'],
        'source_unique': plan['preview']['source_unique'],
        'withdrawn_count': len(plan['withdrawn']),
        'unknown_dates': plan['preview']['unknown_dates'],
        'actions': plan['preview']['actions'],
        'memory_dir': str(vault / 'dynamic' / FOLDER),
    }
    try:
        for item in write_items:
            for path in item['old_files']:
                if path != item['memory_path'] and _memory_is_import(path):
                    path.unlink()
            _write_atomic(item['memory_path'], item['memory_text'])
        if write_items:
            _write_atomic(events_path, _rows_text(merged_events))
            _write_atomic(stages_path, _rows_text(merged_stages))
        _write_atomic(review_path, _rows_text(review))
        _write_atomic(withdrawn_path, _rows_text(plan['withdrawn']))
        _write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        marker.unlink(missing_ok=True)
    except Exception:
        for path in touched:
            if path.exists():
                path.unlink()
        _restore_backup(role_dir, backup)
        marker.unlink(missing_ok=True)
        raise
    return manifest





def _body_hash(path):
    try:
        body = path.read_text(encoding='utf-8').split('\n---\n', 1)[1].strip()
    except (OSError, IndexError):
        return ''
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def expected_hashes(vault, event_ids):
    wanted = set(str(item) for item in event_ids)
    found = {}
    for path in vault.rglob('*.md'):
        try:
            head = path.read_text(encoding='utf-8').split('\n---\n', 1)[0]
        except OSError:
            continue
        match = re.search(r'^id:\s*(\S+)\s*$', head, re.M)
        if match and match.group(1).strip("'\"") in wanted:
            event_id = match.group(1).strip("'\"")
            found[event_id] = _body_hash(path)
    return {event_id: found.get(event_id, '') for event_id in wanted}


def index_status(vault, db_path, event_ids):
    expected = expected_hashes(vault, event_ids)
    rows = {}
    if not Path(db_path).exists():
        missing = sorted(expected)
        return {'ready': False, 'expected': len(expected), 'matched': 0,
                'missing': len(missing), 'stale': 0, 'missing_ids': missing, 'stale_ids': []}
    try:
        connection = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            ids = tuple(expected)
            if ids:
                rows = {row[0]: (row[1] or '') for row in connection.execute(
                    'SELECT bucket_id, content_hash FROM embeddings WHERE bucket_id IN (%s)' %
                    ','.join('?' * len(ids)), ids)}
        finally:
            connection.close()
    except sqlite3.Error as exc:
        if 'no such table' not in str(exc).lower():
            return {'ready': False, 'expected': len(expected), 'matched': 0,
                    'missing': len(expected), 'stale': 0, 'missing_ids': sorted(expected),
                    'stale_ids': [], 'error': str(exc)}
    except OSError as exc:
        return {'ready': False, 'expected': len(expected), 'matched': 0,
                'missing': len(expected), 'stale': 0, 'missing_ids': sorted(expected),
                'stale_ids': [], 'error': str(exc)}
    missing = [event_id for event_id in expected if event_id not in rows]
    stale = [event_id for event_id in expected if event_id in rows and rows[event_id] != expected[event_id]]
    matched = [event_id for event_id in expected if event_id in rows and rows[event_id] == expected[event_id]]
    return {'ready': len(matched) == len(expected), 'expected': len(expected),
            'matched': len(matched), 'missing': len(missing), 'stale': len(stale),
            'missing_ids': sorted(missing), 'stale_ids': sorted(stale)}


def drop_stale_embeddings(db_path, event_ids):
    ids = tuple(str(item) for item in event_ids)
    if not ids:
        return 0
    connection = sqlite3.connect(str(db_path), timeout=5)
    try:
        cursor = connection.execute(
            'DELETE FROM embeddings WHERE bucket_id IN (%s)' % ','.join('?' * len(ids)), ids)
        connection.commit()
        return cursor.rowcount if cursor.rowcount > 0 else 0
    except sqlite3.Error:
        return 0
    finally:
        connection.close()

def wait_ready(app_root, data_dir, role, event_ids, timeout=600, query='第一次让你给我找个你觉得有趣的视频，你找了什么？'):
    """Start Ombre in cycles until every imported memory has the current content hash.

    A vector row alone is not enough: after reimporting a changed memory, the old row
    still exists with the old hash. Ombre's own content hash is sha256(body.strip()).
    Stale rows are deleted here so the next decay cycle (up to 50 per process) rebuilds
    them; readiness requires matching hashes, not just bucket ids.
    """
    from veil_agent import Budget, Store, load_character
    from memory_agent import Memory, open_ombre
    root = Path(app_root).resolve()
    data_root = Path(data_dir).resolve()
    cfg = load_character(root, role)
    store = Store(data_root)
    vault = data_root / role / 'memory'
    db_path = vault / 'embeddings.db'
    ids = [str(item) for item in event_ids]
    started = time.monotonic()
    deadline = started + max(1, int(timeout))
    last = {'ready': False, 'indexed': 0, 'missing': len(ids), 'stale': 0,
            'candidates': [], 'query': query}
    cycle = 0
    while time.monotonic() < deadline:
        cycle += 1
        with store.lock():
            budget = Budget(store)
            with open_ombre(root, cfg, data_root=data_root) as backend:
                with Memory(cfg, store, budget, None, backend) as memory:
                    previous = -1
                    stagnant = 0.0
                    while time.monotonic() < deadline:
                        status = index_status(vault, db_path, ids)
                        last.update({'indexed': status['matched'], 'missing': status['missing'],
                                     'stale': status['stale']})
                        if status['stale_ids']:
                            drop_stale_embeddings(db_path, status['stale_ids'])
                            status = index_status(vault, db_path, ids)
                            last.update({'indexed': status['matched'], 'missing': status['missing'],
                                         'stale': status['stale']})
                        if status['ready']:
                            if 'V50' not in ids:
                                return {'ready': True, 'elapsed_seconds': round(time.monotonic() - started, 1),
                                        'cycles': cycle, 'indexed': status['matched'], **status,
                                        'candidates': [], 'query': ''}
                            turn_before = memory.rotation.turn
                            memory.context(query, include_opening=False)
                            candidates = list(memory.last_recall_debug.get('ombre') or [])
                            last['candidates'] = candidates
                            memory.rollback_recall()
                            with memory.mutex:
                                memory.rotation.turn = turn_before
                            memory._save_rotation()
                            if 'V50' in candidates:
                                return {'ready': True, 'elapsed_seconds': round(time.monotonic() - started, 1),
                                        'cycles': cycle, 'indexed': status['matched'], **status,
                                        'candidates': candidates, 'query': query}
                        if status['matched'] <= previous:
                            stagnant += 5
                        else:
                            stagnant = 0
                            previous = status['matched']
                        if stagnant >= 45 and not status['ready']:
                            break
                        time.sleep(5)
    last['elapsed_seconds'] = round(time.monotonic() - started, 1)
    last['cycles'] = cycle
    return last

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
    parser.add_argument('--wait-ready', action='store_true')
    parser.add_argument('--check-index', action='store_true')
    parser.add_argument('--app-root', default='.')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    final = load_final(args.final)
    plan = build_plan(final, args.data_dir, args.role)
    if args.check_index:
        vault = Path(args.data_dir) / args.role / 'memory'
        status = index_status(vault, vault / 'embeddings.db',
                              [item['event_id'] for item in plan['items']])
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status['ready'] else 2
    if not args.apply:
        print(json.dumps(plan['preview'], ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(apply_plan(plan, args.data_dir, args.role), ensure_ascii=False, indent=2))
    if args.wait_ready:
        ready = wait_ready(args.app_root, args.data_dir, args.role,
                           [item['event_id'] for item in plan['items']], timeout=args.timeout)
        print(json.dumps(ready, ensure_ascii=False, indent=2))
        return 0 if ready['ready'] else 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
