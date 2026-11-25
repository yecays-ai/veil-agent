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

import brain_events
import brain_writer
from brain_lock import write_lock


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
        rows = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError('无法解析事件账本 %s：%s' % (name, exc)) from exc
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeError('事件账本 %s 含有非对象行' % name)
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


def migrate_role(store, role, archive_root=None):
    """Migrate this role's remaining event JSONL/state files; safe to call on every start."""
    role_dir = Path(store.root) / role
    db_path = role_dir / 'brain.db'
    if not db_path.is_file():
        return {'migrated_files': 0, 'rows': 0}
    files = [(name, kind) for name, kind in brain_events.EVENT_LEDGER_FILES.items()
             if (role_dir / name).is_file()]
    state_path = role_dir / brain_events.EVENT_STATE_FILE
    if not files and not state_path.is_file():
        return {'migrated_files': 0, 'rows': 0}
    archive = Path(archive_root) if archive_root else _archive_dir(role_dir)
    archive.mkdir(parents=True, exist_ok=True)
    manifest = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'files': {}}
    total = 0
    with write_lock(store, role):
        connection = sqlite3.connect(str(db_path), timeout=30)
        try:
            brain_events.ensure_schema(connection)
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
        # DB is committed and verified; keep a read-only archive, then remove the live copy.
        for name in manifest['files']:
            source = role_dir / name
            if not source.is_file():
                continue
            target = archive / name
            if target.exists():
                os.chmod(target, 0o600)
            target.write_bytes(source.read_bytes())
            os.chmod(target, 0o444)
            source.unlink()
        (archive / 'manifest.json').write_text(
            json.dumps({**manifest, 'character_id': role, 'rows': total}, ensure_ascii=False,
                       indent=1, sort_keys=True), encoding='utf-8')
    return {'migrated_files': len(manifest['files']), 'rows': total, 'archive': str(archive)}
