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
from types import SimpleNamespace

from brain_lock import write_lock

COUNTS = ('memories', 'versions', 'vectors', 'events', 'event_stages', 'event_ledger',
          'event_state', 'delete_requests', 'write_journal', 'tombstones',
          'experience_recoveries')


def _ro_uri(path):
    return Path(path).resolve().as_uri() + '?mode=ro'


def _tables(connection):
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _has_table(connection, table, schema='main'):
    return connection.execute(
        "SELECT 1 FROM %s.sqlite_master WHERE type='table' AND name=?" % schema,
        (table,)).fetchone() is not None


def _copyable(target, table):
    """True when both the candidate and the attached safety database have this child table."""
    return _has_table(target, table) and _has_table(target, table, schema='old')


def _read_jsonl_lines(text):
    """Physical lines of a JSONL document.

    A JSON string may legally contain U+2028; str.splitlines() would split it and corrupt both the
    merge and the reread of the file (P6-S2 review). Only '\n' separates records.
    """
    return [line.rstrip('\r') for line in (text or '').split('\n')]


def _parse_jsonl_lines(text):
    rows = []
    for line in _read_jsonl_lines(text):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            item = None
        rows.append((line, item if isinstance(item, dict) else None))
    return rows


def _jsonl_identity(item):
    """Stable identity of one experience-memory row, or None when the row has none."""
    if not isinstance(item, dict):
        return None
    kind = item.get('kind')
    source_key = item.get('source_key')
    if not isinstance(kind, str) or not kind:
        return None
    if not isinstance(source_key, str) or not source_key:
        return None
    return (kind, source_key, item.get('entry_index'), item.get('memory_id'),
            item.get('deferred_key'))


def _merge_companion_lines(existing_text, bundle_text):
    """Union of a bundle JSONL and the current file, keyed by record identity.

    Rows that describe the same record are not duplicated: the current file's row wins, so a reconcile
    marker such as `stage_unlinked` is neither duplicated nor silently rolled back (P6-S2 review).
    Rows without a stable identity keep the previous exact-line behaviour.
    """
    existing = _parse_jsonl_lines(existing_text)
    bundle = _parse_jsonl_lines(bundle_text)
    current_by_identity = {}
    for line, item in existing:
        key = _jsonl_identity(item)
        if key is not None:
            current_by_identity[key] = line
    merged, seen_lines, seen_keys = [], set(), set()
    for line, item in bundle:
        key = _jsonl_identity(item)
        if key is not None:
            if key in seen_keys:
                continue
            seen_keys.add(key)
            line = current_by_identity.get(key, line)
        if line in seen_lines:
            continue
        merged.append(line)
        seen_lines.add(line)
    for line, item in existing:
        key = _jsonl_identity(item)
        if key is not None:
            if key in seen_keys:
                continue
            seen_keys.add(key)
        if line in seen_lines:
            continue
        merged.append(line)
        seen_lines.add(line)
    return '\n'.join(merged) + ('\n' if merged else '')


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


def restore(backup_path, db_path, *, lock_held=False):
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
    if not lock_held:
        _assert_stopped(target)
    else:
        wal = target.with_name(target.name + '-wal')
        shm = target.with_name(target.name + '-shm')
        if wal.exists() or shm.exists():
            raise ValueError('检测到未 checkpoint 的 WAL；请完全关闭 VEIL 后重试，不要直接覆盖数据库')
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
              'safety_preserved': bool(safety), 'preserved_new': preserved}
    try:
        post = snapshot(target)
    except Exception:
        # P6-S2 review: the database is already replaced here. A failed post-restore check must put
        # the pre-restore copy back instead of leaving a half-verified database in place.
        if safety is not None and safety.exists():
            shutil.copy2(safety, target)
        else:
            target.unlink(missing_ok=True)
        raise
    result.update(post)
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
        from brain_writer import ensure_schema
        ensure_schema(target)
        target.execute('ATTACH DATABASE ? AS old', (str(Path(current_db)),))
        tombstone_ids = [row[0] for row in target.execute('SELECT memory_id FROM old.tombstones')]
        if tombstone_ids:
            columns = _table_columns(target, 'tombstones')
            names = ', '.join(columns)
            target.execute(
                'INSERT OR REPLACE INTO main.tombstones(%s) SELECT %s FROM old.tombstones'
                % (names, names))
        for memory_id in tombstone_ids:
            affected = {row[0] for row in target.execute(
                'SELECT event_id FROM main.events WHERE memory_id=?', (memory_id,))}
            affected.update(row[0] for row in target.execute(
                'SELECT event_id FROM main.event_sources WHERE memory_id=?', (memory_id,)))
            for table in ('vectors', 'versions', 'relations', 'sources', 'quotes', 'sidecar_state',
                          'sidecar_events', 'experience_recoveries'):
                target.execute('DELETE FROM main.%s WHERE memory_id=?' % table, (memory_id,))
            target.execute('DELETE FROM main.event_sources WHERE memory_id=?', (memory_id,))
            target.execute('UPDATE main.events SET memory_id=NULL WHERE memory_id=?', (memory_id,))
            target.execute('DELETE FROM main.memories WHERE id=?', (memory_id,))
            for event_id in affected:
                remaining = target.execute(
                    'SELECT COUNT(*) FROM main.event_sources WHERE event_id=?', (event_id,)).fetchone()[0]
                if remaining:
                    target.execute(
                        'UPDATE main.events SET source_count=? WHERE event_id=?', (remaining, event_id))
                    continue
                target.execute('DELETE FROM main.event_stages WHERE event_id=?', (event_id,))
                target.execute('DELETE FROM main.event_ledger WHERE event_id=?', (event_id,))
                target.execute('DELETE FROM main.events WHERE event_id=?', (event_id,))
        for table, key in (('delete_requests', 'request_id'),):
            columns = _table_columns(target, table)
            names = ', '.join(columns)
            target.execute('INSERT OR REPLACE INTO main.%s(%s) SELECT %s FROM old.%s' %
                           (table, names, names, table))
        new_memory_ids = {row[0] for row in target.execute(
            'SELECT id FROM old.memories WHERE id NOT IN (SELECT id FROM main.memories)')}
        new_event_ids = {row[0] for row in target.execute(
            'SELECT event_id FROM old.events WHERE event_id NOT IN (SELECT event_id FROM main.events)')}
        existing_memory_ids = {row[0] for row in target.execute(
            'SELECT id FROM old.memories WHERE id IN (SELECT id FROM main.memories)')}
        existing_event_ids = {row[0] for row in target.execute(
            'SELECT event_id FROM old.events WHERE event_id IN (SELECT event_id FROM main.events)')}
        counts['memories'] = _copy_missing(target, 'memories', 'id', new_memory_ids)
        counts['versions'] = _copy_missing(
            target, 'versions', 'memory_id', new_memory_ids, exclude=('seq',))
        counts['vectors'] = _copy_missing(
            target, 'vectors', 'memory_id', new_memory_ids, ignore=True)
        for table in ('sidecar_state', 'sidecar_events', 'relations', 'sources', 'quotes'):
            # P6-S2 review: an old database may lack a child table entirely; skip the whole table so
            # the DELETE and the INSERT stay together. Any other SQLite error must propagate and roll
            # the merge back instead of leaving deleted rows behind.
            if not _copyable(target, table):
                continue
            counts[table] = _copy_missing(target, table, 'memory_id', new_memory_ids, ignore=True)
        counts['events'] = _copy_missing(target, 'events', 'event_id', new_event_ids)
        counts['event_stages'] = _copy_missing(
            target, 'event_stages', 'event_id', new_event_ids, ignore=True)

        # The safety database is the T1 truth for objects that existed at T0 but changed later.
        # Replace their current rows and merge every child row using stable primary keys.
        if existing_memory_ids:
            columns = _table_columns(target, 'memories')
            names = ', '.join(columns)
            target.execute(
                'INSERT OR REPLACE INTO main.memories(%s) SELECT %s FROM old.memories WHERE id IN %s'
                % (names, names, _in(existing_memory_ids)), list(existing_memory_ids))
            counts['updated_memories'] = target.execute('SELECT changes()').fetchone()[0]
            for table in ('vectors', 'sidecar_state', 'relations', 'sources', 'quotes'):
                if not _copyable(target, table):
                    continue
                columns = _table_columns(target, table)
                names = ', '.join(columns)
                target.execute('DELETE FROM main.%s WHERE memory_id IN %s' %
                               (table, _in(existing_memory_ids)), list(existing_memory_ids))
                target.execute(
                    'INSERT OR REPLACE INTO main.%s(%s) SELECT %s FROM old.%s WHERE memory_id IN %s'
                    % (table, names, names, table, _in(existing_memory_ids)),
                    list(existing_memory_ids))
                counts['updated_' + table] = target.execute('SELECT changes()').fetchone()[0]
            version_columns = [name for name in _table_columns(target, 'versions') if name != 'seq']
            existing_versions = {tuple(row) for row in target.execute(
                'SELECT %s FROM main.versions' % ', '.join(version_columns))}
            for row in target.execute(
                    'SELECT %s FROM old.versions WHERE memory_id IN %s' %
                    (', '.join(version_columns), _in(existing_memory_ids)), list(existing_memory_ids)):
                values = list(row)
                if tuple(values) not in existing_versions:
                    target.execute(
                        'INSERT INTO main.versions(%s) VALUES(%s)' %
                        (', '.join(version_columns), ', '.join('?' for _ in version_columns)), values)
                    existing_versions.add(tuple(values))
                    counts['updated_versions'] = counts.get('updated_versions', 0) + 1
        if existing_event_ids:
            columns = _table_columns(target, 'events')
            names = ', '.join(columns)
            target.execute(
                'INSERT OR REPLACE INTO main.events(%s) SELECT %s FROM old.events WHERE event_id IN %s'
                % (names, names, _in(existing_event_ids)), list(existing_event_ids))
            counts['updated_events'] = target.execute('SELECT changes()').fetchone()[0]
            for table in ('event_stages', 'event_sources'):
                columns = _table_columns(target, table)
                names = ', '.join(columns)
                target.execute('DELETE FROM main.%s WHERE event_id IN %s' %
                               (table, _in(existing_event_ids)), list(existing_event_ids))
                target.execute(
                    'INSERT OR REPLACE INTO main.%s(%s) SELECT %s FROM old.%s WHERE event_id IN %s'
                    % (table, names, names, table, _in(existing_event_ids)),
                    list(existing_event_ids))
                counts['updated_' + table] = target.execute('SELECT changes()').fetchone()[0]
        try:
            counts['event_sources'] = _copy_missing(
                target, 'event_sources', 'event_id', new_event_ids, ignore=True)
        except sqlite3.Error:
            counts['event_sources'] = 0
        try:
            columns = _table_columns(target, 'event_state')
            names = ', '.join(columns)
            target.execute(
                'INSERT OR REPLACE INTO main.event_state(%s) SELECT %s FROM old.event_state'
                % (names, names))
            counts['event_state'] = target.execute('SELECT changes()').fetchone()[0]
        except sqlite3.Error:
            counts['event_state'] = 0
        try:
            recovery_keys = [row[0] for row in target.execute(
                'SELECT source_key FROM old.experience_recoveries WHERE source_key NOT IN '
                '(SELECT source_key FROM main.experience_recoveries)')]
            counts['experience_recoveries'] = _copy_missing(
                target, 'experience_recoveries', 'source_key', recovery_keys, ignore=True)
        except sqlite3.Error:
            counts['experience_recoveries'] = 0
        # Later event-lifecycle rows that belong to the fixed new IDs.
        try:
            columns = _table_columns(current, 'event_ledger')
        except sqlite3.Error:
            columns = []
        relevant_memory_ids = new_memory_ids | existing_memory_ids
        relevant_event_ids = new_event_ids | existing_event_ids
        rows = ([row for row in current.execute(
            'SELECT %s FROM event_ledger' % ', '.join(columns))
                 if row[columns.index('event_id')] in relevant_event_ids
                 or row[columns.index('memory_id')] in relevant_memory_ids]
                if columns else [])
        max_seq = target.execute(
            'SELECT COALESCE(MAX(ledger_seq), -1) FROM main.event_ledger').fetchone()[0]
        copied = 0
        for row in rows:
            item = dict(zip(columns, row))
            duplicate = target.execute(
                'SELECT 1 FROM main.event_ledger WHERE kind=? AND event_id IS ? AND stage_id IS ? '
                'AND memory_id IS ? AND payload_json=?',
                (item.get('kind'), item.get('event_id'), item.get('stage_id'),
                 item.get('memory_id'), item.get('payload_json'))).fetchone()
            if duplicate:
                continue
            item.pop('id', None)
            item['ledger_seq'] = max_seq + 1 + copied
            values = [item.get(col) for col in columns if col != 'id']
            names = [col for col in columns if col != 'id']
            target.execute('INSERT INTO main.event_ledger(%s) VALUES(%s)' %
                           (', '.join(names), ', '.join('?' for _ in names)), values)
            copied += 1
        counts['event_ledger'] = copied
        if _copyable(target, 'sidecar_events'):
            sidecar_columns = [name for name in _table_columns(target, 'sidecar_events') if name != 'seq']
            existing_sidecar = {tuple(row) for row in target.execute(
                'SELECT %s FROM main.sidecar_events' % ', '.join(sidecar_columns))}
            for row in target.execute('SELECT %s FROM old.sidecar_events' % ', '.join(sidecar_columns)):
                values = tuple(row)
                if values in existing_sidecar:
                    continue
                target.execute(
                    'INSERT INTO main.sidecar_events(%s) VALUES(%s)' %
                    (', '.join(sidecar_columns), ', '.join('?' for _ in sidecar_columns)), values)
                existing_sidecar.add(values)
                counts['sidecar_events'] = counts.get('sidecar_events', 0) + 1
        journal_columns = [name for name in _table_columns(target, 'write_journal') if name != 'seq']
        existing_journal = {tuple(row) for row in target.execute(
            'SELECT %s FROM main.write_journal' % ', '.join(journal_columns))}
        for row in target.execute('SELECT %s FROM old.write_journal' % ', '.join(journal_columns)):
            values = tuple(row)
            if values in existing_journal:
                continue
            target.execute(
                'INSERT INTO main.write_journal(%s) VALUES(%s)' %
                (', '.join(journal_columns), ', '.join('?' for _ in journal_columns)), values)
            existing_journal.add(values)
            counts['write_journal'] = counts.get('write_journal', 0) + 1
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


COMPANION_FILES = (
    'learned-context.json',
    'learned-context.versions.jsonl',
    'experience-memory.jsonl',
    'experience-memory-failures.jsonl',
    'recall-rotation.json',
)

# Files reconcile_companion_state() may rewrite; restore snapshots them before it runs.
RECONCILE_FILES = ('learned-context.json', 'experience-memory.jsonl')


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _chat_watermark(chat_path: Path) -> dict[str, Any]:
    if not chat_path.exists():
        return {'exists': False, 'lines': 0, 'last_message_id': None, 'sha256': None}
    lines = 0
    last_id = None
    with chat_path.open('r', encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            lines += 1
            try:
                item = json.loads(raw)
                if isinstance(item, dict):
                    # Store.append() writes `message_id`; older exports used `id`.
                    message_id = item.get('message_id')
                    if message_id is None:
                        message_id = item.get('id')
                    if message_id is not None:
                        last_id = message_id
            except Exception:
                pass
    return {
        'exists': True,
        'lines': lines,
        'last_message_id': last_id,
        'sha256': _sha256_file(chat_path),
    }


def _extract_db_refs(text: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for prefix, kind in (('event:', 'event'), ('memory:', 'memory')):
        idx = 0
        while True:
            pos = text.find(prefix, idx)
            if pos < 0:
                break
            end = pos + len(prefix)
            while end < len(text) and (text[end].isalnum() or text[end] in '-_'):
                end += 1
            ref_id = text[pos + len(prefix):end]
            if ref_id:
                refs.append((kind, ref_id))
            idx = end
    return refs


def reconcile_companion_state(role_dir: str | Path) -> dict[str, Any]:
    """Reconcile companion JSON/JSONL files against brain.db without deleting text."""
    role = Path(role_dir)
    db_path = role / 'brain.db'
    if not db_path.exists():
        raise FileNotFoundError(f'brain.db not found in {role}')
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        mem_ids = {r[0] for r in conn.execute('SELECT id FROM memories').fetchall()}
        event_ids = {r[0] for r in conn.execute('SELECT event_id FROM events').fetchall()}
        stage_keys: set[str] = set()
        try:
            for r in conn.execute('SELECT payload_json FROM event_stages WHERE payload_json IS NOT NULL'):
                try:
                    p = json.loads(r[0])
                    sk = p.get('source_key') if isinstance(p, dict) else None
                    if sk:
                        stage_keys.add(str(sk))
                except Exception:
                    pass
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()

    stale_refs = 0
    lc_path = role / 'learned-context.json'
    if lc_path.exists():
        try:
            data = json.loads(lc_path.read_text(encoding='utf-8'))
        except Exception:
            data = None
        if isinstance(data, dict):
            changed = False
            for section in data.values():
                if not isinstance(section, list):
                    continue
                for entry in section:
                    if not isinstance(entry, dict):
                        continue
                    # P6-S2 review: read only the entry's own content. Scanning the whole entry also
                    # re-reads the stale_ref/missing_refs written by the previous pass, so a repaired
                    # reference could never clear the marker.
                    content = {key: value for key, value in entry.items()
                               if key not in ('stale_ref', 'missing_refs')}
                    refs = _extract_db_refs(json.dumps(content, ensure_ascii=False))
                    missing = [
                        f'{kind}:{ref_id}'
                        for kind, ref_id in refs
                        if (kind == 'memory' and ref_id not in mem_ids)
                        or (kind == 'event' and ref_id not in event_ids)
                    ]
                    if missing:
                        if not entry.get('stale_ref') or entry.get('missing_refs') != missing:
                            entry['stale_ref'] = True
                            entry['missing_refs'] = missing
                            changed = True
                        stale_refs += 1
                    elif entry.get('stale_ref'):
                        entry.pop('stale_ref', None)
                        entry.pop('missing_refs', None)
                        changed = True
            if changed:
                lc_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8',
                )

    exp_path = role / 'experience-memory.jsonl'
    exp_checked = 0
    exp_missing_stages = 0
    if exp_path.exists():
        updated_lines: list[str] = []
        changed = False
        for raw in _read_jsonl_lines(exp_path.read_text(encoding='utf-8')):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except Exception:
                updated_lines.append(raw)
                continue
            if isinstance(item, dict) and item.get('source_key'):
                exp_checked += 1
                if item['source_key'] not in stage_keys:
                    exp_missing_stages += 1
                    if not item.get('stage_unlinked'):
                        item['stage_unlinked'] = True
                        changed = True
                elif item.get('stage_unlinked'):
                    item.pop('stage_unlinked', None)
                    changed = True
            updated_lines.append(json.dumps(item, ensure_ascii=False))
        if changed:
            exp_path.write_text('\n'.join(updated_lines) + '\n', encoding='utf-8')

    return {
        'stale_learned_context_refs': stale_refs,
        'experience_entries_checked': exp_checked,
        'experience_unlinked_stages': exp_missing_stages,
    }


def backup_bundle(role_dir: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Create a coordinated backup bundle of brain.db, companion files, and chat watermark."""
    role = Path(role_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db_path = role / 'brain.db'
    with write_lock(SimpleNamespace(root=role.parent), role.name):
        db_meta = backup(db_path, out / 'brain.db')
        companions: dict[str, dict[str, Any]] = {}
        for name in COMPANION_FILES:
            src = role / name
            if not src.exists():
                continue
            dst = out / name
            shutil.copy2(src, dst)
            companions[name] = {
                'bytes': dst.stat().st_size,
                'sha256': _sha256_file(dst),
            }
        watermark = _chat_watermark(role / 'chat.jsonl')
    manifest = {
        'version': 1,
        'db': {
            'file': 'brain.db',
            'bytes': db_meta['bytes'],
            'sha256': db_meta['sha256'],
        },
        'companions': companions,
        'chat_watermark': watermark,
    }
    manifest_path = out / 'bundle-manifest.json'
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return {'out_dir': str(out), 'manifest': manifest}


def _valid_sha256(value):
    value = str(value or '')
    return len(value) == 64 and all(char in '0123456789abcdefABCDEF' for char in value)


def _bundle_file(base, name, allowed=None):
    """Resolve one manifest entry to a file inside `base`.

    A manifest may not name an absolute path, a parent directory or any file the format does not
    define; a matching hash authorises content, never a location (P6-S2 review).
    """
    if not isinstance(name, str) or not name or name != Path(name).name:
        raise ValueError('bundle manifest names an invalid path: %r' % (name,))
    if allowed is not None and name not in allowed:
        raise ValueError('bundle manifest names an unexpected file: %r' % (name,))
    root = base.resolve()
    candidate = (base / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError('bundle manifest path escapes the bundle: %r' % (name,))
    return candidate


def restore_bundle(bundle_dir: str | Path, role_dir: str | Path) -> dict[str, Any]:
    """Restore a coordinated backup bundle, preserving newer data and reconciling refs."""
    bundle = Path(bundle_dir)
    role = Path(role_dir)
    manifest_path = bundle / 'bundle-manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f'bundle-manifest.json not found in {bundle}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

    db_info = manifest.get('db') or {}
    if not isinstance(db_info, dict):
        raise ValueError('bundle manifest db entry must be an object')
    db_name = str(db_info.get('file') or 'brain.db')
    if db_name != 'brain.db':
        raise ValueError('bundle manifest must name the database brain.db: %r' % (db_name,))
    bundle_db = _bundle_file(bundle, db_name, allowed={'brain.db'})
    db_sha = str(db_info.get('sha256') or '')
    if not _valid_sha256(db_sha):
        raise ValueError('bundle brain.db entry has no valid sha256; refusing to restore')
    if not bundle_db.is_file():
        raise FileNotFoundError(f'missing brain.db in bundle: {bundle_db}')
    if _sha256_file(bundle_db) != db_sha.lower():
        raise RuntimeError('bundle brain.db sha256 mismatch')

    companions_meta = manifest.get('companions') or {}
    if not isinstance(companions_meta, dict):
        raise ValueError('bundle manifest companions must be an object')
    companion_paths: dict[str, Path] = {}
    for name, meta in companions_meta.items():
        meta = meta if isinstance(meta, dict) else {}
        bfile = _bundle_file(bundle, name, allowed=set(COMPANION_FILES))
        sha = str(meta.get('sha256') or '')
        if not _valid_sha256(sha):
            raise ValueError(f'bundle companion entry has no valid sha256: {name}')
        if not bfile.is_file():
            raise FileNotFoundError(f'missing companion file in bundle: {name}')
        if _sha256_file(bfile) != sha.lower():
            raise RuntimeError(f'bundle companion sha256 mismatch: {name}')
        companion_paths[name] = bfile

    role.mkdir(parents=True, exist_ok=True)
    with write_lock(SimpleNamespace(root=role.parent), role.name):
        db_result = restore(bundle_db, role / 'brain.db', lock_held=True)
        restored_companions: list[str] = []
        preserved_newer: list[str] = []
        companion_safety: dict[str, Path | None] = {}
        stamp = f'{int(time.time())}-{uuid.uuid4().hex[:8]}'
        try:
            for name in companions_meta:
                src = companion_paths[name]
                dst = role / name
                safety = None
                if dst.exists():
                    safety = role / f'{name}.pre-restore.{stamp}'
                    shutil.copy2(dst, safety)
                companion_safety[name] = safety
                if dst.exists() and name == 'learned-context.json' and dst.stat().st_mtime > src.stat().st_mtime:
                    preserved_newer.append(name)
                    continue
                if dst.exists() and name == 'recall-rotation.json':
                    # P6-S2 review: rotation state describes the current memory set. An older bundle
                    # copy must not roll it back; only a missing file is restored from the backup.
                    preserved_newer.append(name)
                    continue
                if dst.exists() and name.endswith('.jsonl'):
                    merged_text = _merge_companion_lines(
                        dst.read_text(encoding='utf-8'), src.read_text(encoding='utf-8'))
                    temporary = role / f'.{name}.restore-{uuid.uuid4().hex}'
                    temporary.write_text(merged_text, encoding='utf-8')
                    os.replace(temporary, dst)
                    restored_companions.append(name)
                    continue
                temporary = role / f'.{name}.restore-{uuid.uuid4().hex}'
                shutil.copy2(src, temporary)
                os.replace(temporary, dst)
                restored_companions.append(name)
            # P6-S2 review: reconcile rewrites these files even when they were created after the
            # backup and are therefore not in the manifest, so snapshot them inside the same rollback
            # boundary before it can touch them.
            for name in RECONCILE_FILES:
                if name in companion_safety:
                    continue
                dst = role / name
                if dst.exists():
                    safety = role / f'{name}.pre-restore.{stamp}'
                    shutil.copy2(dst, safety)
                    companion_safety[name] = safety
            reconcile = reconcile_companion_state(role)
        except Exception:
            pre_restore = Path(db_result['pre_restore']) if db_result.get('pre_restore') else None
            if pre_restore and pre_restore.exists():
                os.replace(pre_restore, role / 'brain.db')
            elif not db_result.get('safety_preserved'):
                (role / 'brain.db').unlink(missing_ok=True)
            for name, safety in companion_safety.items():
                dst = role / name
                if safety is None:
                    dst.unlink(missing_ok=True)
                elif safety.exists():
                    os.replace(safety, dst)
            raise
    return {
        'db': db_result,
        'restored_companions': restored_companions,
        'preserved_newer': preserved_newer,
        'reconcile': reconcile,
    }
