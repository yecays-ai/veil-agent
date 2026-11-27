#!/usr/bin/env python3
"""Operator tool: explicitly reindex memory vectors after an embedding-model change.

An embedding-model switch makes every stored vector incompatible. P6-S1 never mixes models and
never auto-embeds the whole library, so the deliberate operator step is:

    # 1. see what would change (no write)
    python scripts/embedding_reindex.py --db data/peter/brain.db --model bge-m3
    # 2. actually clear vectors + mark every memory vector_state=pending + forget old model
    python scripts/embedding_reindex.py --db data/peter/brain.db --model bge-m3 --apply

After --apply the normal pending-vector retry (brain_authority._retry_pending) re-embeds memories
with the configured new model. Run it in a maintenance window on a backup; never on the live
database without a fresh bundle.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from veil.brain.storage import brain_writer


def _state(connection):
    live = connection.execute(
        "SELECT COUNT(*) FROM memories WHERE is_deleted=0 AND is_tombstone=0").fetchone()[0]
    vectors = connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    pending = connection.execute(
        "SELECT COUNT(*) FROM memories WHERE is_deleted=0 AND is_tombstone=0 "
        "AND vector_state='pending'").fetchone()[0]
    try:
        row = connection.execute(
            "SELECT value FROM meta WHERE key=?", (brain_writer.EMBEDDING_MODEL_META,)).fetchone()
        stored = str((row[0] if row else '') or '')
    except sqlite3.OperationalError:
        stored = ''
    return {'memories': int(live), 'vectors': int(vectors), 'pending': int(pending),
            'stored_embedding_model': stored}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', required=True, help='path to <role>/brain.db (read-only unless --apply)')
    parser.add_argument('--model', required=True, help='the embedding model that is now configured')
    parser.add_argument('--apply', action='store_true',
                        help='clear vectors, mark memories pending and forget the old model')
    parser.add_argument('--force', action='store_true',
                        help='allow a reindex even when the recorded model already equals --model')
    args = parser.parse_args(argv)
    db = Path(args.db)
    model = str(args.model).strip()
    if not model:
        raise SystemExit('--model must not be empty')
    if not db.is_file():
        raise SystemExit('no such brain.db: %s' % db)
    connection = sqlite3.connect(str(db), timeout=30)
    try:
        brain_writer.ensure_schema(connection)
        before = _state(connection)
    finally:
        connection.close()
    report = {'db': str(db), 'new_embedding_model': model, 'before': before,
              'applied': bool(args.apply)}
    if before['stored_embedding_model'] == model and not args.force:
        report['note'] = ('the database already records this embedding model; nothing to reindex. '
                           'Pass --force only to deliberately rebuild every vector with the same model.')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if not args.apply:
        report['note'] = 'dry run only; add --apply to clear vectors and mark every memory pending'
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    connection = sqlite3.connect(str(db), timeout=30)
    try:
        report['result'] = brain_writer.prepare_embedding_reindex(connection, model)
        report['after'] = _state(connection)
    finally:
        connection.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
