"""P3-1: first-run Brain initialization, without hiding a lost existing database.

A brand-new role has no role data yet: create brain.db with the full schema.
An existing role whose brain.db disappeared or is damaged must stop loudly; creating an empty
database would look like "all memories are gone" and hide the real recovery path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from veil.brain.storage import brain_writer
from veil.brain.storage.brain_lock import write_lock


class BrainDatabaseProblem(RuntimeError):
    """The role has data but its Brain database cannot be used safely."""


def _role_has_files(store, role):
    root = Path(store.root) / role
    if not root.exists():
        return False
    try:
        return any(path.is_file() and path.name != 'brain-write.lock' for path in root.rglob('*'))
    except OSError:
        # A listing failure must not be treated as "fresh": fail closed.
        return True


def _validate_existing(path):
    try:
        # Open read-write so SQLite can recover a legitimate WAL left by an interrupted run.
        # Validation itself writes nothing; a damaged file still raises before any schema work.
        connection = sqlite3.connect(str(Path(path)), timeout=10)
    except sqlite3.Error as exc:
        raise BrainDatabaseProblem(
            'brain.db 无法打开（%s）；请从备份恢复，不要创建空库。' % type(exc).__name__) from exc
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'memories' not in tables:
            raise BrainDatabaseProblem(
                'brain.db 存在但缺少 memories 表；请从备份恢复，不要重建空库。')
        check = connection.execute('PRAGMA quick_check').fetchone()
        if not check or check[0] != 'ok':
            raise BrainDatabaseProblem(
                'brain.db 完整性检查失败；请从备份恢复，不要删除或重建这个文件。')
    except sqlite3.DatabaseError as exc:
        raise BrainDatabaseProblem(
            'brain.db 不是可用的 Brain 数据库（%s）；请从备份恢复，不要重建空库。'
            % type(exc).__name__) from exc
    finally:
        connection.close()


def initialize(path):
    """Create a new empty Brain database with every schema migration applied."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from veil.brain.storage import veil_brain
    from veil.brain.storage import brain_events
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        brain_events.ensure_schema(connection)
        check = connection.execute('PRAGMA quick_check').fetchone()
        if not check or check[0] != 'ok':
            raise BrainDatabaseProblem('新建 brain.db 后完整性检查失败；请从备份恢复。')
        connection.commit()
        brain_writer.refresh_digest(connection)
    except Exception:
        connection.rollback()
        connection.close()
        try:
            path.unlink()
        except OSError:
            pass
        raise
    else:
        connection.close()
    return str(path)


def prepare(store, role):
    """Return the usable brain.db path, or raise before any memory object is constructed."""
    db_path = Path(store.root) / role / 'brain.db'
    # Serialize first-run initialization across two app instances. The lock file itself is
    # ignored by _role_has_files, so a fresh empty role is still recognized as fresh.
    with write_lock(store, role):
        if db_path.is_file():
            _validate_existing(db_path)
            return str(db_path)
        if _role_has_files(store, role):
            raise BrainDatabaseProblem(
                '角色数据目录里已有其他文件，但 brain.db 缺失。为避免用空库掩盖数据丢失，已停止；'
                '请从备份恢复 brain.db（不要先创建空文件），确认后再启动。')
        return initialize(db_path)
