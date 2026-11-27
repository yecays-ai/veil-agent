"""P3-2: one-way, idempotent, lossless migration of event auxiliary ledgers.

Each legacy file is imported in one transaction. The transaction records the file SHA-256 and
row count in event_ledger_imports; a crash rolls the whole file back, so a retry cannot duplicate
rows. Rows keep their original line sequence and identical legal rows are kept as separate rows.
Only after the database transaction is committed and verified is the file copied to a read-only
archive and removed from the live role directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from veil.brain.storage import brain_events
from veil.brain.storage import brain_writer
from veil.brain.storage.brain_lock import write_lock


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _archive_dir(role_dir):
    return role_dir / 'archive' / ('pre-p3-2-event-ledgers-' + time.strftime('%Y%m%dT%H%M%S'))


def _migrate_file(connection, source, name, kind):
    data = source.read_bytes()
    digest = _sha(data)
    if brain_events.ledger_imported(connection, name, digest):
        return 0, False
    try:
        text = data.decode('utf-8')
        # Old Store.append wrote the JSON text verbatim; a field may legally contain U+2028,
        # which str.splitlines() would misread as a record boundary. Only the file's own
        # newline separates JSONL records.
        rows = [json.loads(line.rstrip('\r')) for line in text.split('\n') if line.strip()]
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError('无法解析事件账本 %s：%s' % (name, exc)) from exc
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeError('事件账本 %s 含有非对象行' % name)
    brain_events.ensure_schema(connection)
    # One transaction per file: interruption before commit leaves neither rows nor the import
    # marker, so the next start imports the same file cleanly.
    connection.execute('BEGIN IMMEDIATE')
    try:
        connection.execute('DELETE FROM event_ledger WHERE source_file=?', (name,))
        connection.execute('DELETE FROM event_ledger_imports WHERE source_file=?', (name,))
        for index, row in enumerate(rows):
            brain_events.append_ledger(connection, kind, row, source_file=name,
                                       source_seq=index, ledger_seq=index)
        connection.execute(
            'INSERT INTO event_ledger_imports(source_file,sha256,row_count,completed_at) '
            'VALUES(?,?,?,?)',
            (name, digest, len(rows), time.strftime('%Y-%m-%dT%H:%M:%S%z')))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    stored = connection.execute(
        'SELECT COUNT(*) FROM event_ledger WHERE source_file=?', (name,)).fetchone()
    if int(stored[0] if stored else 0) != len(rows):
        raise RuntimeError('事件账本 %s 行数校验失败' % name)
    return len(rows), True


def _migrate_state(connection, source):
    data = source.read_bytes()
    digest = _sha(data)
    if brain_events.ledger_imported(connection, brain_events.EVENT_STATE_FILE, digest):
        return False
    try:
        value = json.loads(data.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError('无法解析 %s：%s' % (source.name, exc)) from exc
    if not isinstance(value, dict):
        raise RuntimeError('%s 不是对象' % source.name)
    brain_events.ensure_schema(connection)
    connection.execute('BEGIN IMMEDIATE')
    try:
        brain_events.write_state(connection, 'event-dream', value)
        connection.execute(
            'INSERT OR REPLACE INTO event_ledger_imports(source_file,sha256,row_count,completed_at) '
            'VALUES(?,?,?,?)',
            (brain_events.EVENT_STATE_FILE, digest, 1, time.strftime('%Y-%m-%dT%H:%M:%S%z')))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return True


def _parse_jsonl(data, name):
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise RuntimeError('无法解码事件账本 %s：%s' % (name, exc)) from exc
    rows = []
    for line in text.split('\n'):
        line = line.rstrip('\r')
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise RuntimeError('无法解析事件账本 %s：%s' % (name, exc)) from exc
        if not isinstance(row, dict):
            raise RuntimeError('事件账本 %s 含有非对象行' % name)
        rows.append(row)
    return rows


def _migrate_events(connection, role_dir):
    """Bring legacy events.jsonl/event-stages.jsonl into the Brain tables before reading."""
    events_path = role_dir / 'events.jsonl'
    stages_path = role_dir / 'event-stages.jsonl'
    if not events_path.is_file() and not stages_path.is_file():
        return {}
    events = _parse_jsonl(events_path.read_bytes(), 'events.jsonl') if events_path.is_file() else []
    stages = _parse_jsonl(stages_path.read_bytes(), 'event-stages.jsonl') if stages_path.is_file() else []
    brain_events.ensure_schema(connection)
    connection.execute('BEGIN IMMEDIATE')
    try:
        for row in events:
            brain_events.upsert_event(connection, row, commit=False, ensure=False)
        for index, row in enumerate(stages):
            brain_events.upsert_stage(connection, row, index, commit=False, ensure=False)
        for row in events:
            event_id = str(row.get('event_id') or '')
            if event_id and not connection.execute(
                    'SELECT 1 FROM events WHERE event_id=?', (event_id,)).fetchone():
                raise RuntimeError('事件 %s 未写入 Brain' % event_id)
        for row in stages:
            stage_id = str(row.get('stage_id') or '')
            if stage_id and not connection.execute(
                    'SELECT 1 FROM event_stages WHERE stage_id=?', (stage_id,)).fetchone():
                raise RuntimeError('阶段 %s 未写入 Brain' % stage_id)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {'events': len(events), 'stages': len(stages)}


def unhide_legacy_rows(store, role):
    """P3-3: old hidden/dont_surface rows return to normal visibility.

    Only visibility metadata changes; body/title/why and the P6 cognitive fields are untouched.
    A hidden row that had been folded into archive is moved back to a normal dynamic memory so it
    is visible and recall-eligible. Digested/resolved values are kept as legacy data but ignored.
    """
    role_dir = Path(store.root) / role
    db_path = role_dir / 'brain.db'
    if not db_path.is_file():
        return 0
    with write_lock(store, role):
        connection = sqlite3.connect(str(db_path), timeout=30)
        try:
            brain_events.ensure_schema(connection)
            try:
                pending = {row[0] for row in connection.execute(
                    "SELECT memory_id FROM delete_requests WHERE status='pending'")}
            except sqlite3.Error:
                pending = set()
            rows = [row for row in connection.execute(
                'SELECT id,bucket,type FROM memories WHERE is_tombstone=0 AND is_deleted=0 '
                'AND (is_hidden=1 OR dont_surface=1)').fetchall()
                    if row[0] not in pending]
            for memory_id, bucket, kind in rows:
                normal = bucket in ('dynamic', 'permanent') and kind not in ('archived',)
                connection.execute(
                    'UPDATE memories SET is_hidden=0, dont_surface=0, bucket=?, type=? '
                    'WHERE id=?',
                    (bucket if normal else 'dynamic', kind if normal else 'note', memory_id))
            if rows:
                brain_writer.refresh_digest(connection)
            connection.commit()
            return len(rows)
        finally:
            connection.close()


def migrate_role(store, role, archive_root=None):
    """Migrate this role's remaining event JSONL/state files; safe to call on every start."""
    role_dir = Path(store.root) / role
    db_path = role_dir / 'brain.db'
    if not db_path.is_file():
        return {'migrated_files': 0, 'rows': 0}
    files = [(name, kind) for name, kind in brain_events.EVENT_LEDGER_FILES.items()
             if (role_dir / name).is_file()]
    state_path = role_dir / brain_events.EVENT_STATE_FILE
    event_files = [name for name in ('events.jsonl', 'event-stages.jsonl')
                   if (role_dir / name).is_file()]
    if not files and not event_files and not state_path.is_file():
        return {'migrated_files': 0, 'rows': 0}
    archive = Path(archive_root) if archive_root else _archive_dir(role_dir)
    archive.mkdir(parents=True, exist_ok=True)
    manifest = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'files': {}}
    total = 0
    with write_lock(store, role):
        connection = sqlite3.connect(str(db_path), timeout=30)
        try:
            brain_events.ensure_schema(connection)
            event_counts = _migrate_events(connection, role_dir)
            if event_counts:
                for name in event_files:
                    manifest['files'][name] = {
                        'rows': event_counts.get('events' if name == 'events.jsonl' else 'stages', 0),
                        'sha256': _sha((role_dir / name).read_bytes()),
                        'kind': 'events' if name == 'events.jsonl' else 'event-stages'}
                total += event_counts.get('events', 0) + event_counts.get('stages', 0)
            for name, kind in files:
                source = role_dir / name
                rows, _new = _migrate_file(connection, source, name, kind)
                total += rows
                manifest['files'][name] = {
                    'rows': rows, 'sha256': _sha(source.read_bytes()), 'kind': kind}
            if state_path.is_file():
                _migrate_state(connection, state_path)
                manifest['files'][state_path.name] = {
                    'rows': 1, 'sha256': _sha(state_path.read_bytes()), 'kind': 'event-state'}
            brain_writer.refresh_digest(connection)
        finally:
            connection.close()
        # DB is committed and verified. Write every archive copy and the manifest first; only
        # then remove live files. A disk-full/crash midway leaves every source in place and the
        # retry can finish the same archive.
        to_remove = []
        for name in manifest['files']:
            source = role_dir / name
            if not source.is_file():
                continue
            target = archive / name
            if target.exists():
                os.chmod(target, 0o600)
            target.write_bytes(source.read_bytes())
            os.chmod(target, 0o444)
            to_remove.append(source)
        (archive / 'manifest.json').write_text(
            json.dumps({**manifest, 'character_id': role, 'rows': total}, ensure_ascii=False,
                       indent=1, sort_keys=True), encoding='utf-8')
        for source in to_remove:
            source.unlink()
    return {'migrated_files': len(manifest['files']), 'rows': total, 'archive': str(archive)}
