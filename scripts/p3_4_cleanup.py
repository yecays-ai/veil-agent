"""P3-4: maintenance-window cleanup for the retired Ombre data.

The tool is deliberately two-phase:

    plan  -> verifies a cold backup, writes a hash-bound JSON cleanup list
    apply -> re-plans, requires the same list + explicit confirmation, removes data

It never runs from application startup.  It refuses to touch the live role while a Brain writer
lock or the web service is active.  Normal memories and their P6 columns are left untouched;
only already-deleted/tombstoned rows, letter/plan duplicate rows, old file mirrors and Ombre
runtime files are removed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from pathlib import Path

import backup_restore
import brain_writer
import veil_brain

CONFIRM_WORD = 'P3-4-cleanup'
TOMBSTONE_TABLES = ('vectors', 'versions', 'relations', 'sources', 'quotes',
                    'sidecar_state', 'sidecar_events')


def _sha_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _plan_hash(plan):
    return _sha_text(json.dumps(plan, ensure_ascii=False, sort_keys=True))


def _size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())
    return 0


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


def _p6_counts(connection):
    out = {}
    for table in ('memories', 'versions', 'vectors', 'events', 'event_stages', 'event_ledger',
                  'event_state', 'relations', 'sources', 'quotes'):
        try:
            out[table] = connection.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
        except sqlite3.Error:
            out[table] = 0
    return out


def _candidate_paths(root, role):
    root = Path(root)
    role_dir = root / 'data' / role
    memory = role_dir / 'memory'
    paths = []
    # Retired vault content.  _agent holds the live command index and _media holds user media.
    for name in ('dynamic', 'permanent', 'archive', 'feel', 'letters', 'plans', '_ledger',
                 '_sources', '.locks', '.logs', '.them', '.you'):
        paths.append(memory / name)
    for pattern in ('memory.backup-*', '.event-import-backup-*'):
        paths.extend(role_dir.glob(pattern))
    if (role_dir / 'backups').is_dir():
        paths.extend((role_dir / 'backups').glob('*'))
    # Runtime Ombre installation; source entrypoints are removed from the repository separately.
    paths.extend((root / '.deps' / 'ombre',))
    paths.extend((root / '.local-tmp').glob('ombre*'))
    return [path for path in paths if path.exists()]


def _plan(root, role, backup, archive_dir):
    root, backup = Path(root).resolve(), Path(backup).resolve()
    manifest = backup_restore.verify_backup(backup)
    role_dir = root / 'data' / role
    db_path = role_dir / 'brain.db'
    if not db_path.is_file():
        raise RuntimeError('找不到角色 brain.db：%s' % db_path)
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        tombstones = _tombstone_rows(connection)
        letters_plans = _letter_plan_rows(connection)
        p6 = _p6_counts(connection)
    finally:
        connection.close()
    files = []
    for path in _candidate_paths(root, role):
        files.append({'path': str(path.relative_to(root)).replace('\\', '/'),
                      'bytes': _size(path), 'kind': 'dir' if path.is_dir() else 'file'})
    installer = root / '.local-tmp' / 'ombre-115d831.zip'
    if (root / '.deps' / 'ombre').exists() and not installer.is_file():
        raise RuntimeError('Ombre 运行目录存在但安装包缺失；先保留离线安装包再清理')
    if Path(archive_dir).resolve().is_relative_to(root):
        raise RuntimeError('清理 archive 必须在项目目录之外')
    if not backup.is_absolute() or backup.is_relative_to(root):
        raise RuntimeError('备份目录必须在项目目录之外')
    db_sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    wal_path = db_path.with_name(db_path.name + '-wal')
    if wal_path.exists():
        db_sha256 = hashlib.sha256(db_path.read_bytes() + wal_path.read_bytes()).hexdigest()
    plan = {
        'version': 1,
        'root': str(root),
        'role': role,
        'backup': str(backup),
        'backup_manifest_files': len(manifest['files']),
        'archive_dir': str(Path(archive_dir).resolve()),
        'tombstones': tombstones,
        'letters_plans': letters_plans,
        'file_targets': files,
        'p6_before': p6,
        'db_sha256': db_sha256,
        'retained': [
            'data/%s/memory/_agent' % role,
            'data/%s/memory/_media' % role,
            'data/%s/archive' % role,
            'data/%s/brain.db' % role,
            'P6: raw/frontmatter/tag_migration/legacy/relations/sources/quotes',
        ],
        'offline_installer': str(installer) if installer.is_file() else '',
    }
    plan['plan_hash'] = _plan_hash({key: value for key, value in plan.items() if key != 'plan_hash'})
    return plan


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                    encoding='utf-8')


def _archive_letters_plans(connection, rows):
    archive = []
    for row in rows:
        item = dict(row)
        try:
            item['versions'] = [dict(v) for v in connection.execute(
                'SELECT * FROM versions WHERE memory_id=?', (row['id'],))]
        except sqlite3.Error:
            item['versions'] = []
        for table in ('sidecar_state', 'relations', 'sources', 'quotes'):
            try:
                item[table] = [dict(v) for v in connection.execute(
                    'SELECT * FROM %s WHERE memory_id=?' % table, (row['id'],))]
            except sqlite3.Error:
                item[table] = []
        archive.append(item)
    return archive


def _apply_db(root, role, plan, archive_dir):
    role_dir = Path(root) / 'data' / role
    db_path = role_dir / 'brain.db'
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    removed_tombstones = removed_letters = 0
    p6_after = {}
    try:
        tombstones = _tombstone_rows(connection)
        letters_plans = _letter_plan_rows(connection)
        _write_json(archive_dir / 'letters-plans.json', _archive_letters_plans(connection, letters_plans))
        # Deleted rows must leave no recoverable body/raw/versions/vectors.  The normal hard-delete
        # path removes children and leaves a minimal anti-resurrection tombstone.
        for row in tombstones:
            brain_writer.hard_delete(connection, row['id'], 'P3-4 cleanup')
            removed_tombstones += 1
        for row in letters_plans:
            brain_writer.hard_delete(connection, row['id'], 'P3-4 letter/plan duplicate cleanup')
            removed_letters += 1
        connection.commit()
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        connection.execute('VACUUM')
        p6_after = _p6_counts(connection)
        quick = connection.execute('PRAGMA quick_check').fetchone()[0]
        if quick != 'ok':
            raise RuntimeError('cleanup 后 quick_check 失败：%s' % quick)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    verify_connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        brain_writer.refresh_digest(verify_connection)
    finally:
        verify_connection.close()
    return {'tombstones_removed': removed_tombstones,
            'letters_plans_removed': removed_letters,
            'p6_after': p6_after}


def _apply_files(root, plan, archive_dir):
    root = Path(root)
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    removed = []
    installer = root / '.local-tmp' / 'ombre-115d831.zip'
    if installer.is_file():
        target = archive_dir / installer.name
        shutil.copy2(installer, target)
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(installer.read_bytes()).digest():
            raise RuntimeError('Ombre 安装包离线复制校验失败')
    for item in plan['file_targets']:
        path = root / item['path']
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(item['path'])
    # Source entrypoints are removed from the repo by the P3-4 PR, not at runtime.
    return removed


def _verify(root, role):
    role_dir = Path(root) / 'data' / role
    connection = sqlite3.connect(str(role_dir / 'brain.db'), timeout=30)
    try:
        quick = connection.execute('PRAGMA quick_check').fetchone()[0]
        tombstones = connection.execute(
            'SELECT COUNT(*) FROM memories WHERE is_tombstone=1 OR is_deleted=1').fetchone()[0]
        letters = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE type IN ('letter','plan') "
            "OR bucket IN ('letters','plans')").fetchone()[0]
        p6 = _p6_counts(connection)
        digest = connection.execute("SELECT value FROM meta WHERE key='digest'").fetchone()
    finally:
        connection.close()
    return {'quick_check': quick, 'tombstone_rows': tombstones, 'letter_plan_rows': letters,
            'p6': p6, 'digest': digest[0] if digest else ''}


def apply_plan(root, role, plan_path, *, backup, archive_dir, confirm):
    if confirm != CONFIRM_WORD:
        raise RuntimeError('确认词不正确；未执行清理')
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    if plan.get('version') != 1:
        raise RuntimeError('未知清理计划版本')
    if plan.get('plan_hash') != _plan_hash({key: value for key, value in plan.items()
                                            if key != 'plan_hash'}):
        raise RuntimeError('清理计划哈希校验失败')
    _assert_stopped(root, role)
    fresh = _plan(root, role, backup, archive_dir)
    if fresh.get('plan_hash') != plan.get('plan_hash'):
        raise RuntimeError('清理计划与当前数据不一致；重新生成计划')
    archive_dir = Path(archive_dir).resolve()
    db_result = _apply_db(root, role, plan, archive_dir)
    files = _apply_files(root, plan, archive_dir)
    result = {'plan_hash': plan['plan_hash'], 'db': db_result, 'files_removed': files,
              'verify': _verify(root, role)}
    _write_json(archive_dir / 'cleanup-result.json', result)
    return result


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
        _write_json(args.plan, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
