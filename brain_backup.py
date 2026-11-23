"""P2-A: online SQLite backup/restore for brain.db (WAL-safe)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


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
    return {'path': str(out_path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def restore(backup_path, db_path):
    data = Path(backup_path).read_bytes()
    Path(db_path).write_bytes(data)
    return {'path': str(db_path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


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
