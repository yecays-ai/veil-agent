"""P2-A: online SQLite backup/restore for brain.db (WAL-safe).

A backup is not trusted just because SQLite can open it: its stored meta.digest is recomputed
from the actual rows. Restore validates a complete snapshot first, checkpoints/refuses a live WAL,
keeps a safety copy of the current database, and replaces the target atomically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

COUNTS = ('memories', 'versions', 'vectors', 'events', 'event_stages', 'event_ledger',
          'event_state', 'delete_requests', 'write_journal', 'tombstones')


def _ro_uri(path):
    return Path(path).resolve().as_uri() + '?mode=ro'


def _tables(connection):
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _actual_digest(connection):
    from veil_brain import canonical_digest
    return canonical_digest(connection)


def snapshot(db_path):
    """Recompute quick_check, canonical digest and lifecycle counts from the database itself."""
    connection = sqlite3.connect(_ro_uri(db_path), uri=True)
    try:
        check = connection.execute('PRAGMA quick_check').fetchone()
        result = {'quick_check': check[0] if check else 'missing', 'counts': {}}
        for table in COUNTS:
            try:
                result['counts'][table] = int(connection.execute(
                    'SELECT COUNT(*) FROM %s' % table).fetchone()[0])
            except sqlite3.Error:
                result['counts'][table] = 0
        try:
            result['digest'] = _actual_digest(connection)
            stored = connection.execute(
                "SELECT value FROM meta WHERE key='digest'").fetchone()
            result['stored_digest'] = stored[0] if stored else ''
            result['digest_ok'] = bool(result['digest']) and result['digest'] == result['stored_digest']
        except Exception:
            result['digest'] = ''
            result['stored_digest'] = ''
            result['digest_ok'] = False
        return result
    finally:
        connection.close()


def _assert_stopped(target):
    """Refuse to restore beside a live Brain writer or an un-checkpointed WAL."""
    target = Path(target)
    lock_path = target.parent / 'brain-write.lock'
    if lock_path.exists():
        try:
            fd = os.open(lock_path, os.O_RDWR)
        except OSError as exc:
            raise ValueError('brain-write.lock 无法检查；请完全关闭 VEIL 后重试') from exc
        try:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise ValueError('Brain 写入锁仍在运行；请完全关闭 VEIL 后重试') from exc
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ValueError('Brain 写入锁仍在运行；请完全关闭 VEIL 后重试') from exc
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    wal = target.with_name(target.name + '-wal')
    shm = target.with_name(target.name + '-shm')
    if wal.exists() or shm.exists():
        raise ValueError('检测到未 checkpoint 的 WAL；请完全关闭 VEIL 后重试，不要直接覆盖数据库')


def backup(db_path, out_path):
    source = sqlite3.connect(_ro_uri(db_path), uri=True)
    target = sqlite3.connect(str(out_path))
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    data = Path(out_path).read_bytes()
    result = {'path': str(out_path), 'bytes': len(data),
              'sha256': hashlib.sha256(data).hexdigest(), **snapshot(out_path)}
    if result['quick_check'] != 'ok' or not result['digest_ok']:
        raise ValueError('backup verification failed: quick_check or digest mismatch')
    return result


def restore(backup_path, db_path):
    backup_path = Path(backup_path)
    target = Path(db_path)
    # Complete preflight on the backup; never touch the live target before this passes.
    validation = snapshot(backup_path)
    if validation['quick_check'] != 'ok':
        raise ValueError('backup database failed quick_check')
    connection = sqlite3.connect(_ro_uri(backup_path), uri=True)
    try:
        if 'memories' not in _tables(connection):
            raise ValueError('backup database has no memories table')
    finally:
        connection.close()
    if not validation['digest_ok']:
        raise ValueError('backup digest does not match its rows')
    _assert_stopped(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    safety = None
    safety_export = None
    if target.exists():
        # Never let an old backup silently erase memories/events created after it. Keep both a
        # byte-identical database copy and a readable export before any replacement.
        safety = target.with_name(target.name + '.pre-restore-' + stamp)
        shutil.copy2(target, safety)
        import brain_export
        safety_export = target.with_name(target.name + '.pre-restore-' + stamp + '-export')
        brain_export.export_vault(target, safety_export)
    temp = target.with_name(target.name + '.restore-' + uuid.uuid4().hex)
    try:
        data = backup_path.read_bytes()
        temp.write_bytes(data)
        local = snapshot(temp)
        if local['quick_check'] != 'ok' or not local['digest_ok']:
            raise ValueError('restored copy failed local verification')
        preserved = {}
        if safety is not None:
            # Merge into the candidate, never into the live target. A failed merge leaves the
            # current database byte-for-byte untouched.
            preserved = _merge_new_rows(safety, temp)
            local = snapshot(temp)
            if local['quick_check'] != 'ok' or not local['digest_ok']:
                raise ValueError('merged candidate failed local verification')
        fd = os.open(temp, os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    result = {'path': str(target), 'bytes': len(data),
              'sha256': hashlib.sha256(data).hexdigest(),
              'pre_restore': str(safety) if safety else '',
              'pre_restore_export': str(safety_export) if safety_export else '',
              'safety_preserved': bool(safety), 'preserved_new': preserved,
              **snapshot(target)}
    return result


def _table_columns(connection, table):
    return [row[1] for row in connection.execute('PRAGMA table_info(%s)' % table)]


def _in(ids):
    ids = list(ids)
    return '(' + ','.join('?' for _ in ids) + ')' if ids else '(NULL)'


def _copy_missing(connection, table, column, ids, *, ignore=False, exclude=()):
    ids = list(ids)
    if not ids:
        return 0
    columns = [name for name in _table_columns(connection, table) if name not in exclude]
    select = ', '.join(columns)
    verb = 'INSERT OR IGNORE' if ignore else 'INSERT'
    connection.execute(
        '%s INTO main.%s(%s) SELECT %s FROM old.%s WHERE %s IN %s'
        % (verb, table, select, select, table, column, _in(ids)), ids)
    return connection.execute('SELECT changes()').fetchone()[0]


def _merge_new_rows(current_db, target_db):
    """Keep memories/events created after the backup in the restored live database.

    All IDs are fixed before any copy runs: inserting the new memories/events must not change the
    predicate that selects their versions, vectors, links and stages. The safety database stays
    untouched; only rows whose primary identity is absent from the backup are copied.
    """
    current = sqlite3.connect(_ro_uri(current_db), uri=True)
    target = sqlite3.connect(str(target_db))
    counts = {}
    try:
        target.execute('ATTACH DATABASE ? AS old', (str(Path(current_db)),))
        new_memory_ids = {row[0] for row in target.execute(
            'SELECT id FROM old.memories WHERE id NOT IN (SELECT id FROM main.memories)')}
        new_event_ids = {row[0] for row in target.execute(
            'SELECT event_id FROM old.events WHERE event_id NOT IN (SELECT event_id FROM main.events)')}
        counts['memories'] = _copy_missing(target, 'memories', 'id', new_memory_ids)
        counts['versions'] = _copy_missing(
            target, 'versions', 'memory_id', new_memory_ids, exclude=('seq',))
        counts['vectors'] = _copy_missing(
            target, 'vectors', 'memory_id', new_memory_ids, ignore=True)
        for table in ('sidecar_state', 'sidecar_events', 'relations', 'sources', 'quotes'):
            try:
                counts[table] = _copy_missing(
                    target, table, 'memory_id', new_memory_ids, ignore=True)
            except sqlite3.Error:
                counts[table] = 0
        counts['events'] = _copy_missing(target, 'events', 'event_id', new_event_ids)
        counts['event_stages'] = _copy_missing(
            target, 'event_stages', 'event_id', new_event_ids, ignore=True)
        try:
            counts['event_sources'] = _copy_missing(
                target, 'event_sources', 'event_id', new_event_ids, ignore=True)
        except sqlite3.Error:
            counts['event_sources'] = 0
        try:
            counts['event_state'] = _copy_missing(
                target, 'event_state', 'key',
                [row[0] for row in target.execute(
                    'SELECT key FROM old.event_state WHERE key NOT IN '
                    '(SELECT key FROM main.event_state)')], ignore=True)
        except sqlite3.Error:
            counts['event_state'] = 0
        # Later event-lifecycle rows that belong to the fixed new IDs.
        try:
            columns = _table_columns(current, 'event_ledger')
        except sqlite3.Error:
            columns = []
        rows = ([row for row in current.execute(
            'SELECT %s FROM event_ledger' % ', '.join(columns))
                 if row[columns.index('event_id')] in new_event_ids
                 or row[columns.index('memory_id')] in new_memory_ids]
                if columns else [])
        max_seq = target.execute(
            'SELECT COALESCE(MAX(ledger_seq), -1) FROM main.event_ledger').fetchone()[0]
        copied = 0
        for row in rows:
            item = dict(zip(columns, row))
            item.pop('id', None)
            item['ledger_seq'] = max_seq + 1 + copied
            values = [item.get(col) for col in columns if col != 'id']
            names = [col for col in columns if col != 'id']
            target.execute('INSERT INTO main.event_ledger(%s) VALUES(%s)' %
                           (', '.join(names), ', '.join('?' for _ in names)), values)
            copied += 1
        counts['event_ledger'] = copied
        target.commit()
        from brain_writer import refresh_digest
        refresh_digest(target)
        return counts
    except Exception:
        target.rollback()
        raise
    finally:
        target.close()
        current.close()
