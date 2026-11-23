"""P2-B: isolated cutover/rollback checks. Never starts or stops the live Peter.

Commands:
  backup   copy brain.db online + manifest
  check    digest/counts/pending vectors/events/tags snapshot
  export   same-ID Ombre-readable vault via brain_export
  verify   re-import the export into a fresh brain.db and compare counts
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_backup
import brain_export
import veil_brain


def snapshot(db_path):
    connection = sqlite3.connect('file:%s?mode=ro' % Path(db_path), uri=True)
    try:
        counts = {table: connection.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
                  for table in ('memories', 'versions', 'sidecar_events', 'vectors', 'events',
                                'event_sources', 'event_stages')}
        try:
            pending = connection.execute("SELECT COUNT(*) FROM memories WHERE vector_state='pending'").fetchone()[0]
        except sqlite3.Error:
            pending = None
        try:
            stories = connection.execute(
                "SELECT COUNT(*) FROM events WHERE story IS NOT NULL AND story <> ''").fetchone()[0]
        except sqlite3.Error:
            stories = None
        tagged = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE tags_json NOT IN ('[]','')").fetchone()[0]
    finally:
        connection.close()
    check = veil_brain.check_db(db_path)
    return {'counts': counts, 'pending_vectors': pending, 'event_stories': stories,
            'memories_with_tags': tagged, 'check': check}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('backup', 'check', 'export', 'verify'))
    parser.add_argument('--db', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if args.action == 'backup':
        result = brain_backup.backup(args.db, args.out)
    elif args.action == 'check':
        result = snapshot(args.db)
    elif args.action == 'export':
        result = brain_export.export_vault(args.db, args.out)
    else:
        export = Path(args.out)
        reimported = export / 'verify-brain.db'
        if reimported.exists():
            reimported.unlink()
        imported = veil_brain.run_import(export, reimported)
        result = {'rows': len(imported.rows), 'versions': len(imported.versions),
                  'sidecar': len(imported.sidecar), 'problems': len(imported.problems),
                  'check': veil_brain.check_db(reimported)}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
