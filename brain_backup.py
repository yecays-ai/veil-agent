"""P2-A: online SQLite backup/restore for brain.db (WAL-safe)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


COUNTS = ('memories', 'versions', 'vectors', 'events', 'event_stages', 'event_ledger',
          'event_state', 'delete_requests', 'write_journal', 'tombstones')


def snapshot(db_path):
    """Counts and digest used to prove a backup covers the complete memory/event lifecycle."""
    connection = sqlite3.connect('file:%s?mode=ro' % Path(db_path), uri=True)
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
            row = connection.execute("SELECT value FROM meta WHERE key='digest'").fetchone()
            result['digest'] = row[0] if row else ''
        except sqlite3.Error:
            result['digest'] = ''
        return result
    finally:
        connection.close()


def backup(db_path, out_path):
    source = sqlite3.connect('file:%s?mode=ro' % Path(db_path), uri=True)
    target = sqlite3.connect(str(out_path))
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    data = Path(out_path).read_bytes()
    return {'path': str(out_path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            **snapshot(out_path)}


def restore(backup_path, db_path):
    data = Path(backup_path).read_bytes()
    # Validate the backup before touching the live target; a corrupt copy must never replace a
    # healthy brain.db.
    validation = snapshot(backup_path)
    if validation['quick_check'] != 'ok':
        raise ValueError('backup database failed quick_check')
    if 'memories' not in _tables(backup_path):
        raise ValueError('backup database has no memories table')
    Path(db_path).write_bytes(data)
    return {'path': str(db_path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            **snapshot(db_path)}


def _tables(db_path):
    connection = sqlite3.connect('file:%s?mode=ro' % Path(db_path), uri=True)
    try:
        return {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('backup', 'restore'))
    parser.add_argument('--db', required=True)
    parser.add_argument('--file', required=True)
    args = parser.parse_args(argv)
    result = backup(args.db, args.file) if args.action == 'backup' else restore(args.file, args.db)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
