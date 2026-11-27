"""P2-A isolated end-to-end proof. Copies data; never touches the production live files.

Usage: python scripts/p2a_isolated_check.py --source-brain <copy> --events ... --stages ... --out DIR
"""
import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veil.brain.storage import brain_events
from veil.brain.storage import brain_export
from veil.brain.storage import brain_read
from veil.brain.storage import brain_writer
from veil.brain.storage import veil_brain


def vector(_value=None):
    return [0.3] * 1024


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-brain', required=True)
    parser.add_argument('--events', required=True)
    parser.add_argument('--stages', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db = out / 'brain.db'
    shutil.copyfile(args.source_brain, db)
    connection = sqlite3.connect(db)
    checks = {}
    try:
        migrated = brain_events.import_events(connection, args.events, args.stages)
        checks['events'] = migrated
        checks['stories'] = connection.execute(
            "SELECT COUNT(*) FROM events WHERE story IS NOT NULL AND story <> ''").fetchone()[0]
        checks['stages'] = connection.execute('SELECT COUNT(*) FROM event_stages').fetchone()[0]
        created = brain_writer.create_memory(connection, {'body': 'first body', 'title': 'First'},
                                             embedder=vector)
        checks['create'] = created
        updated = brain_writer.update_memory(connection, created['memory_id'],
                                             {'body': 'second body'}, embedder=vector,
                                             operation_reason='isolated check')
        checks['update'] = updated
        brain_writer.set_state(connection, created['memory_id'], locked=True)
        second = brain_writer.create_memory(connection, {'body': 'to delete'}, embedder=None)
        checks['pending'] = second
        checks['retry'] = brain_writer.retry_pending_vectors(connection, vector)
        checks['delete'] = brain_writer.hard_delete(connection, second['memory_id'], 'isolated check')
        checks['tombstone'] = connection.execute(
            'SELECT memory_id,deleted_at FROM tombstones').fetchone() is not None
        checks['versions'] = connection.execute(
            'SELECT COUNT(*) FROM versions WHERE memory_id=?', (created['memory_id'],)).fetchone()[0]
        checks['journal'] = connection.execute('SELECT COUNT(*) FROM write_journal').fetchone()[0]
        exported = brain_export.export_vault(db, out / 'export')
        checks['export'] = exported
        reimport_db = out / 'reimport.db'
        result = veil_brain.run_import(out / 'export', reimport_db)
        checks['reimport'] = {'rows': len(result.rows), 'versions': len(result.versions),
                              'sidecar': len(result.sidecar), 'problems': len(result.problems)}
        with brain_read.BrainRead(reimport_db) as reader:
            checks['read'] = {'panel': len(reader.panel_entries()),
                              'story': bool(reader.event('V01') and reader.event('V01')['story'])}
    finally:
        connection.close()
    (out / 'p2a-check.json').write_text(json.dumps(checks, ensure_ascii=False, indent=1),
                                         encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    main()
