"""P3-4: maintenance-window cleanup for retired Ombre data.

Two-phase and resumable:

    plan  -> verifies cold backup coverage, binds user-data hashes + a cleanup list
    apply -> re-verifies the same plan, then commits database cleanup and file removal

The tool never runs automatically.  A plan file is created exclusively (never overwrites a user
file); apply requires the same plan, a confirmation word and a stopped service.  After the DB
transaction commits, an archive state file lets a retry finish VACUUM/file cleanup without
deleting any row twice.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backup_restore
import brain_writer
import veil_brain

CONFIRM_WORD = 'P3-4-cleanup'
CHILD_TABLES = ('vectors', 'versions', 'relations', 'sources', 'quotes',
                'sidecar_state', 'sidecar_events')
RUNTIME_TARGETS = ('.deps/ombre', '.local-tmp/ombre-115d831.zip',
                   '.local-tmp/ombre-upstream', '.local-tmp/ombre312',
                   '.local-tmp/probe_ombre.py', '.local-tmp/ticket-066-ombre.log')


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha_text(text):
    return _sha_bytes(text.encode('utf-8'))


def _plan_hash(plan):
    return _sha_text(json.dumps(plan, ensure_ascii=False, sort_keys=True))


def _inside(root, path):
    try:
        Path(path).resolve().is_relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _json_default(value):
    if isinstance(value, bytes):
        return {'__base64__': base64.b64encode(value).decode('ascii')}
    raise TypeError('not JSON serializable: %s' % type(value).__name__)


def _write_json(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                         default=_json_default) + '\n'
    if exclusive:
        with open(path, 'x', encoding='utf-8') as handle:
            handle.write(payload)
    else:
        path.write_text(payload, encoding='utf-8')


def _tree(path):
    path = Path(path)
    if path.is_file():
        data = path.read_bytes()
        return {'kind': 'file', 'bytes': len(data), 'sha256': _sha_bytes(data)}
    files = []
    dirs = []
    for item in sorted(path.rglob('*')):
        if item.is_symlink():
            raise RuntimeError('清理目标含符号链接，拒绝处理：%s' % item)
        if item.is_file():
            files.append({'path': item.relative_to(path).as_posix(),
                          'bytes': item.stat().st_size,
                          'sha256': _sha_bytes(item.read_bytes())})
        elif item.is_dir():
            dirs.append(item.relative_to(path).as_posix())
    return {'kind': 'dir', 'bytes': sum(item['bytes'] for item in files),
            'members': files, 'dirs': dirs,
            'tree_sha256': _sha_text(json.dumps({'files': files, 'dirs': dirs},
                                                ensure_ascii=False, sort_keys=True))}


def _targets(root, role):
    root = Path(root)
    role_dir = root / 'data' / role
    memory = role_dir / 'memory'
    found = []
    # Old vault content. _agent and _media remain live.
    for name in ('dynamic', 'permanent', 'archive', 'feel', 'letters', 'plans', '_ledger',
                 '_sources', '.locks', '.logs', '.them', '.you'):
        found.append(memory / name)
    for pattern in ('memory.backup-*', '.event-import-backup-*', '*.jsonl'):
        found.extend(role_dir.glob(pattern))
    if (role_dir / 'backups').is_dir():
        found.extend((role_dir / 'backups').glob('*'))
    for pattern in ('*.jsonl', 'embeddings.db'):
        found.extend(memory.glob(pattern))
    found.extend(root / rel for rel in RUNTIME_TARGETS)
    return found


def _safe_targets(root, role):
    root = Path(root)
    seen, safe, external = set(), [], []
    for path in _targets(root, role):
        if not path.exists():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            try:
                label = str(path.relative_to(root)).replace('\\', '/')
            except ValueError:
                label = str(path)
            external.append(label)
            continue
        rel = str(path.relative_to(root)).replace('\\', '/')
        if rel not in seen:
            seen.add(rel)
            safe.append(path)
    return safe, external


def _assert_stopped(root, role):
    role_dir = Path(root) / 'data' / role
    lock_path = role_dir / 'brain-write.lock'
    if lock_path.exists():
        try:
            fd = os.open(lock_path, os.O_RDWR)
        except OSError as exc:
            raise RuntimeError('brain-write.lock 无法检查；先关闭 VEIL') from exc
        try:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise RuntimeError('Brain 写入锁仍在；先关闭 VEIL') from exc
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise RuntimeError('Brain 写入锁仍在；先关闭 VEIL') from exc
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    service = Path(root) / 'data' / 'web_service.json'
    if service.is_file():
        try:
            port = int(json.loads(service.read_text(encoding='utf-8'))['port'])
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                raise RuntimeError('VEIL 网页服务仍在运行；先关闭 VEIL')
        except (OSError, ValueError, KeyError):
            pass


def _tombstone_rows(connection):
    return [dict(row) for row in connection.execute(
        'SELECT id,title,type,deleted_at,created FROM memories '
        'WHERE is_tombstone=1 OR is_deleted=1')]


def _letter_plan_rows(connection):
    return [dict(row) for row in connection.execute(
        "SELECT id,title,bucket,type,file_rel,source_tool,created FROM memories "
        "WHERE type IN ('letter','plan') OR bucket IN ('letters','plans') ORDER BY created")]


def _count(connection, query, params=()):
    try:
        return connection.execute(query, params).fetchone()[0]
    except sqlite3.Error:
        return 0


def _residuals(connection, ids):
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    items = list(ids)
    result = {}
    result['event_ledger'] = _count(
        connection, 'SELECT COUNT(*) FROM event_ledger WHERE memory_id IN (%s)' % marks, items) if _table(connection, 'event_ledger') else 0
    result['delete_requests'] = _count(
        connection, 'SELECT COUNT(*) FROM delete_requests WHERE memory_id IN (%s)' % marks, items) if _table(connection, 'delete_requests') else 0
    result['write_journal'] = _count(
        connection, 'SELECT COUNT(*) FROM write_journal WHERE memory_id IN (%s)' % marks, items) if _table(connection, 'write_journal') else 0
    return result


def _table(connection, name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _backup_coverage(root, backup, targets, role):
    manifest = backup_restore.verify_backup(Path(backup))
    files = {entry['path']: entry for entry in manifest['files']}
    dirs = set(manifest['directories'])
    root = Path(root)
    checked, missing, mismatched = 0, [], []
    for path in targets:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            continue  # external/symlink targets are not user data
        relative = path.relative_to(root).as_posix()
        if relative.startswith(('.deps/', '.local-tmp/')):
            continue  # reinstallable runtime: covered by the offline installer, not cold backup
        if path.is_file():
            rel = 'payload/' + path.relative_to(root).as_posix()
            entry = files.get(rel)
            checked += 1
            if entry is None:
                missing.append(rel)
            elif entry['size'] != path.stat().st_size or entry['sha256'] != _sha_bytes(path.read_bytes()):
                mismatched.append(rel)
            continue
        for item in path.rglob('*'):
            if item.is_symlink():
                raise RuntimeError('清理目标含符号链接/联接点，拒绝处理：%s' % item)
            if not item.is_file():
                continue
            if not item.resolve().is_relative_to(root.resolve()):
                raise RuntimeError('清理目标经联接点越出项目目录：%s' % item)
            rel = 'payload/' + item.relative_to(root).as_posix()
            entry = files.get(rel)
            checked += 1
            if entry is None:
                missing.append(rel)
            elif entry['size'] != item.stat().st_size or entry['sha256'] != _sha_bytes(item.read_bytes()):
                mismatched.append(rel)
        for item in path.rglob('*'):
            if item.is_symlink():
                raise RuntimeError('清理目标含符号链接/联接点，拒绝处理：%s' % item)
            if item.is_dir():
                rel = 'payload/' + item.relative_to(root).as_posix()
                if rel not in dirs:
                    missing.append(rel)
    db = root / 'data' / role / 'brain.db'
    db_rel = 'payload/data/%s/brain.db' % role
    entry = files.get(db_rel)
    checked += 1
    if entry is None:
        missing.append(db_rel)
    elif entry['size'] != db.stat().st_size or entry['sha256'] != _sha_bytes(db.read_bytes()):
        mismatched.append(db_rel)
    if 'payload/data' not in dirs:
        missing.append('payload/data')
    return {'ok': not missing and not mismatched, 'checked': checked,
            'missing': missing[:100], 'mismatched': mismatched[:100]}


def _row_hash(row):
    return _sha_text(json.dumps({key: row[key] for key in row.keys()},
                                ensure_ascii=False, sort_keys=True, default=str))


def _snapshot(connection, exclude_memory_ids, removed_event_ids=()):
    exclude = set(exclude_memory_ids)
    removed_events = set(removed_event_ids)
    snap = {}
    def rows(table, where='', params=()):
        try:
            return [dict(row) for row in connection.execute('SELECT * FROM %s %s' % (table, where), params)]
        except sqlite3.Error:
            return []
    def hashed(items, key=None, skip=()):
        out = {}
        for item in items:
            if key and item.get(key) in skip:
                continue
            value = _row_hash(item)
            out[item.get(key) or len(out)] = value
        return out
    snap['memories'] = hashed(rows('memories'), 'id', exclude)
    for table in ('versions', 'vectors', 'relations', 'sources', 'quotes',
                  'sidecar_state', 'sidecar_events'):
        items = rows(table)
        snap[table] = [(_row_hash(item)) for item in items
                       if item.get('memory_id') not in exclude]
    event_items = []
    for item in rows('events'):
        if item.get('event_id') in removed_events:
            continue
        item = {key: value for key, value in item.items() if key != 'source_count'}
        event_items.append(item)
    snap['events'] = hashed(event_items, 'event_id')
    snap['event_stages'] = [(_row_hash(item)) for item in rows('event_stages')
                            if item.get('event_id') not in removed_events]
    snap['event_sources'] = [(_row_hash(item)) for item in rows('event_sources')
                             if item.get('event_id') not in removed_events
                             and item.get('memory_id') not in exclude]
    if _table(connection, 'event_ledger'):
        snap['event_ledger'] = [(_row_hash(item)) for item in rows('event_ledger')
                                if item.get('memory_id') not in exclude
                                and item.get('event_id') not in removed_events]
    if _table(connection, 'event_state'):
        snap['event_state'] = hashed(rows('event_state'), 'key')
    if _table(connection, 'delete_requests'):
        snap['delete_requests'] = [(_row_hash(item)) for item in rows('delete_requests')
                                   if item.get('memory_id') not in exclude]
    if _table(connection, 'write_journal'):
        snap['write_journal'] = [(_row_hash(item)) for item in rows('write_journal')
                                 if item.get('memory_id') not in exclude]
    return snap


def _plan(root, role, backup, archive_dir):
    root, backup = Path(root).resolve(), Path(backup).resolve()
    archive_dir = Path(archive_dir).resolve()
    if archive_dir == backup or archive_dir.is_relative_to(backup) or backup.is_relative_to(archive_dir):
        raise RuntimeError('清理 archive 与冷备目录不能相同或嵌套')
    if archive_dir.is_relative_to(root):
        raise RuntimeError('清理 archive 必须在项目目录之外')
    if backup.is_relative_to(root):
        raise RuntimeError('备份目录必须在项目目录之外')
    role_dir = root / 'data' / role
    db_path = role_dir / 'brain.db'
    if not db_path.is_file():
        raise RuntimeError('找不到角色 brain.db：%s' % db_path)
    if db_path.with_name(db_path.name + '-wal').exists():
        raise RuntimeError('检测到未 checkpoint 的 brain.db-wal；先正常关闭并 checkpoint')
    targets, external = _safe_targets(root, role)
    coverage = _backup_coverage(root, backup, targets, role)
    if not coverage['ok']:
        raise RuntimeError('冷备未覆盖全部待删用户数据：%s' % (coverage['missing'] + coverage['mismatched']))
    installer = root / '.local-tmp' / 'ombre-115d831.zip'
    if (root / '.deps' / 'ombre').exists() and not installer.is_file():
        raise RuntimeError('Ombre 运行目录存在但安装包缺失；先保留离线安装包')
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        tombstones = _tombstone_rows(connection)
        letters = _letter_plan_rows(connection)
        ids = {row['id'] for row in tombstones} | {row['id'] for row in letters}
        residuals = _residuals(connection, ids)
        db_sha = _sha_bytes(db_path.read_bytes())
        before = _snapshot(connection, ids)
        # Events hard-deleted because all their sources belong to cleaned memories.
        removed_events = set()
        if ids and _table(connection, 'event_sources'):
            marks = ','.join('?' * len(ids))
            for row in connection.execute(
                    'SELECT DISTINCT event_id FROM event_sources WHERE memory_id IN (%s)' % marks,
                    tuple(ids)):
                remaining = connection.execute(
                    'SELECT COUNT(*) FROM event_sources WHERE event_id=? AND memory_id NOT IN (%s)'
                    % marks, (row[0], *ids)).fetchone()[0]
                if remaining == 0:
                    removed_events.add(row[0])
    finally:
        connection.close()
    plan = {
        'version': 2, 'root': str(root), 'role': role, 'backup': str(backup),
        'backup_manifest_files': coverage['checked'],
        'archive_dir': str(archive_dir),
        'tombstones': tombstones, 'letters_plans': letters,
        'file_targets': [{'path': str(path.relative_to(root)).replace('\\', '/'),
                          **_tree(path)} for path in targets],
        'external_skipped': external,
        'backup_coverage': coverage,
        'residuals': residuals,
        'removed_event_ids': sorted(removed_events),
        'p6_before': before,
        'db_sha256': db_sha,
        'offline_installer': str(installer) if installer.is_file() else '',
        'retained': ['data/%s/memory/_agent' % role, 'data/%s/memory/_media' % role,
                     'data/%s/archive' % role, 'data/%s/brain.db' % role,
                     'P6/raw/frontmatter/tag_migration/legacy/relations/sources/quotes'],
    }
    plan['plan_hash'] = _plan_hash({key: value for key, value in plan.items() if key != 'plan_hash'})
    return plan


def _archive_full_rows(connection, ids):
    archived = []
    for memory_id in sorted(ids):
        row = connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
        if row is None:
            continue
        item = dict(row)
        for table in ('versions', 'vectors', 'relations', 'sources', 'quotes',
                      'sidecar_state', 'sidecar_events'):
            if _table(connection, table):
                item[table] = [dict(v) for v in connection.execute(
                    'SELECT * FROM %s WHERE memory_id=?' % table, (memory_id,))]
        archived.append(item)
    return archived


def _delete_locked(connection, memory_id, reason):
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in CHILD_TABLES:
        if table in tables:
            connection.execute('DELETE FROM %s WHERE memory_id=?' % table, (memory_id,))
    affected = []
    if 'event_sources' in tables:
        affected = [row[0] for row in connection.execute(
            'SELECT DISTINCT event_id FROM event_sources WHERE memory_id=?', (memory_id,))]
        connection.execute('DELETE FROM event_sources WHERE memory_id=?', (memory_id,))
        if 'events' in tables:
            for event_id in affected:
                remaining = connection.execute(
                    'SELECT COUNT(*) FROM event_sources WHERE event_id=?', (event_id,)).fetchone()[0]
                connection.execute(
                    'UPDATE events SET source_count=? WHERE event_id=?', (remaining, event_id))
                if remaining == 0:
                    if 'event_stages' in tables:
                        connection.execute('DELETE FROM event_stages WHERE event_id=?', (event_id,))
                    if 'event_ledger' in tables:
                        connection.execute('DELETE FROM event_ledger WHERE event_id=?', (event_id,))
                    if 'events' in tables:
                        connection.execute('DELETE FROM events WHERE event_id=?', (event_id,))
    if 'event_ledger' in tables:
        connection.execute('DELETE FROM event_ledger WHERE memory_id=?', (memory_id,))
    if 'delete_requests' in tables:
        connection.execute("DELETE FROM delete_requests WHERE memory_id=? AND status<>'pending'",
                           (memory_id,))
    if 'write_journal' in tables:
        connection.execute('DELETE FROM write_journal WHERE memory_id=?', (memory_id,))
    connection.execute('DELETE FROM memories WHERE id=?', (memory_id,))
    connection.execute('INSERT OR REPLACE INTO tombstones(memory_id,deleted_at,reason_hash) '
                       'VALUES(?,?,?)', (memory_id, brain_writer.now(),
                                         _sha_text(reason)[:16]))


def _state_path(archive_dir, plan):
    return Path(archive_dir) / ('state-' + plan['plan_hash'][:16] + '.json')


def _load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _db_applied(connection, plan):
    ids = {row['id'] for row in plan['tombstones']} | {row['id'] for row in plan['letters_plans']}
    if not ids:
        return True
    marks = ','.join('?' * len(ids))
    return connection.execute('SELECT COUNT(*) FROM memories WHERE id IN (%s)' % marks,
                              tuple(ids)).fetchone()[0] == 0


def _apply_db(root, role, plan, archive_dir, state_path):
    role_dir = Path(root) / 'data' / role
    db_path = role_dir / 'brain.db'
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    plan_ids = {row['id'] for row in plan['tombstones']} | {row['id'] for row in plan['letters_plans']}
    archive_name = archive_dir / ('letters-plans-' + plan['plan_hash'][:16] + '.json')
    if archive_name.exists():
        archived = json.loads(archive_name.read_text(encoding='utf-8'))
        if not isinstance(archived, list):
            raise RuntimeError('已有归档格式损坏')
    else:
        connection = sqlite3.connect(str(db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            archived = _archive_full_rows(connection, plan_ids)
        finally:
            connection.close()
        _write_json(archive_name, archived, exclusive=True)
    _write_json(state_path, {'plan_hash': plan['plan_hash'], 'phase': 'started'}, exclusive=False)
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN IMMEDIATE')
        current = {row['id'] for row in _tombstone_rows(connection)} |                   {row['id'] for row in _letter_plan_rows(connection)}
        if current != plan_ids:
            connection.rollback()
            raise RuntimeError('清理目标与批准计划不一致；未删除任何行')
        for memory_id in sorted(plan_ids):
            _delete_locked(connection, memory_id, 'P3-4 cleanup')
        connection.commit()
        _write_json(state_path, {'plan_hash': plan['plan_hash'], 'phase': 'db_committed'})
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {'tombstones_removed': len(plan['tombstones']),
            'letters_plans_removed': len(plan['letters_plans'])}


def _finish_db(root, role, plan, state_path):
    db_path = Path(root) / 'data' / role / 'brain.db'
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        connection.execute('VACUUM')
        connection.commit()
        quick = connection.execute('PRAGMA quick_check').fetchone()[0]
        if quick != 'ok':
            raise RuntimeError('cleanup 后 quick_check 失败：%s' % quick)
        excluded = {row['id'] for row in plan['tombstones']} |                    {row['id'] for row in plan['letters_plans']}
        after = _snapshot(connection, excluded, plan['removed_event_ids'])
        if plan['p6_before'] != after:
            raise RuntimeError('清理前后保留数据完整性对账失败')
        digest = connection.execute("SELECT value FROM meta WHERE key='digest'").fetchone()
    finally:
        connection.close()
    verify = sqlite3.connect(str(db_path), timeout=30)
    try:
        brain_writer.refresh_digest(verify)
    finally:
        verify.close()
    _write_json(state_path, {'plan_hash': plan['plan_hash'], 'phase': 'db_finished'})
    return {'quick_check': quick, 'digest': digest[0] if digest else '',
            'p6': plan['p6_before']}


def _apply_files(root, plan, archive_dir):
    root = Path(root)
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    installer = root / '.local-tmp' / 'ombre-115d831.zip'
    if installer.is_file():
        target = archive_dir / installer.name
        data = installer.read_bytes()
        if target.exists():
            if _sha_bytes(target.read_bytes()) != _sha_bytes(data):
                raise RuntimeError('已有 Ombre 安装包未通过离线校验')
        else:
            target.write_bytes(data)
    removed = []
    for item in plan['file_targets']:
        path = root / item['path']
        if not path.exists():
            continue
        if not _inside(root, path):
            raise RuntimeError('清理目标越出项目目录：%s' % item['path'])
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(item['path'])
    return removed


def _verify(root, role, plan, state_path):
    role_dir = Path(root) / 'data' / role
    connection = sqlite3.connect(str(role_dir / 'brain.db'), timeout=30)
    try:
        return {'quick_check': connection.execute('PRAGMA quick_check').fetchone()[0],
                'tombstone_rows': _count(connection, 'SELECT COUNT(*) FROM memories WHERE is_tombstone=1 OR is_deleted=1'),
                'letter_plan_rows': _count(connection, "SELECT COUNT(*) FROM memories WHERE type IN ('letter','plan') OR bucket IN ('letters','plans')"),
                'p6': plan['p6_before']}
    finally:
        connection.close()


def _load_plan(path):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    if plan.get('version') != 2:
        raise RuntimeError('未知清理计划版本')
    if plan.get('plan_hash') != _plan_hash({key: value for key, value in plan.items()
                                            if key != 'plan_hash'}):
        raise RuntimeError('清理计划哈希校验失败')
    return plan


def apply_plan(root, role, plan_path, *, backup, archive_dir, confirm):
    if confirm != CONFIRM_WORD:
        raise RuntimeError('确认词不正确；未执行清理')
    plan = _load_plan(plan_path)
    archive_dir = Path(archive_dir).resolve()
    if archive_dir != Path(plan['archive_dir']).resolve():
        raise RuntimeError('archive 目录与计划不一致')
    if Path(backup).resolve() != Path(plan['backup']).resolve():
        raise RuntimeError('backup 目录与计划不一致')
    if Path(archive_dir) == Path(backup).resolve() or Path(archive_dir).is_relative_to(Path(backup).resolve())             or Path(backup).resolve().is_relative_to(Path(archive_dir)):
        raise RuntimeError('清理 archive 与冷备目录不能重叠')
    _assert_stopped(root, role)
    db_result = {'tombstones_removed': len(plan['tombstones']),
                 'letters_plans_removed': len(plan['letters_plans'])}
    state_path = _state_path(archive_dir, plan)
    state = _load_state(state_path)
    if not (state and state.get('plan_hash') == plan['plan_hash']
            and state.get('phase') in ('db_committed', 'db_finished', 'files_done')):
        fresh = _plan(root, role, backup, archive_dir)
        if fresh['plan_hash'] != plan['plan_hash']:
            connection = sqlite3.connect(str(Path(root) / 'data' / role / 'brain.db'), timeout=30)
            try:
                if not (state and state.get('plan_hash') == plan['plan_hash']
                        and state.get('phase') == 'started' and _db_applied(connection, plan)):
                    raise RuntimeError('清理计划与当前数据不一致；重新生成计划')
            finally:
                connection.close()
        applied_connection = sqlite3.connect(str(Path(root) / 'data' / role / 'brain.db'), timeout=30)
        try:
            already_applied = bool(state and state.get('plan_hash') == plan['plan_hash']
                                   and state.get('phase') == 'started'
                                   and _db_applied(applied_connection, plan))
        finally:
            applied_connection.close()
        if not already_applied:
            db_result = _apply_db(root, role, plan, archive_dir, state_path)
    else:
        db_state = _load_state(state_path)
        if db_state.get('phase') == 'db_committed':
            _finish_db(root, role, plan, state_path)
        if db_state.get('phase') in ('db_committed', 'db_finished', 'files_done'):
            pass
    state = _load_state(state_path) or {}
    if state.get('phase') not in ('db_finished', 'files_done'):
        _finish_db(root, role, plan, state_path)
    if state.get('phase') != 'files_done':
        removed = _apply_files(root, plan, archive_dir)
        _write_json(state_path, {'plan_hash': plan['plan_hash'], 'phase': 'files_done'})
    else:
        removed = []
    state = _load_state(state_path) or {}
    if state.get('phase') != 'complete':
        result = {'plan_hash': plan['plan_hash'], 'db': db_result, 'files_removed': removed,
                  'verify': _verify(root, role, plan, state_path)}
        _write_json(archive_dir / ('cleanup-result-' + plan['plan_hash'][:16] + '.json'), result,
                    exclusive=False)
        _write_json(state_path, {'plan_hash': plan['plan_hash'], 'phase': 'complete'})
        return result
    return json.loads((archive_dir / ('cleanup-result-' + plan['plan_hash'][:16] + '.json')).read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description='P3-4 maintenance cleanup (plan/apply)')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--role', default='peter')
    parser.add_argument('--backup', required=True)
    parser.add_argument('--archive-dir', required=True)
    parser.add_argument('--plan')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm', default='')
    args = parser.parse_args(argv)
    if args.apply:
        if not args.plan:
            raise SystemExit('--apply 需要 --plan')
        result = apply_plan(args.root, args.role, args.plan, backup=args.backup,
                             archive_dir=args.archive_dir, confirm=args.confirm)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    plan = _plan(args.root, args.role, args.backup, args.archive_dir)
    if args.plan:
        _write_json(args.plan, plan, exclusive=True)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
