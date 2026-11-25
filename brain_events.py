"""Ticket P2-A: full event-story storage in Brain.

The P0-A schema only kept the event -> memory mapping. This module extends it to hold the story,
stages, sources and provenance for every event, losslessly. It is additive and idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

# P3-2: every remaining event-lifecycle ledger is a virtual JSONL path backed by brain.db.
# The keys keep the old file names so BrainStore can preserve the existing read/append call sites.
EVENT_LEDGER_FILES = {
    'stage-evidence.jsonl': 'stage-evidence',
    'event-versions.jsonl': 'event-version',
    'event-review.jsonl': 'event-review',
    'event-ops.jsonl': 'event-op',
    'event-projections.jsonl': 'event-projection',
    'event-revisions.jsonl': 'event-revision',
    'event-dream-report.jsonl': 'event-dream-report',
}
EVENT_STATE_FILE = 'event-dream.json'
EVENT_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_ledger(
    seq INTEGER PRIMARY KEY, at TEXT, kind TEXT NOT NULL, event_id TEXT, stage_id TEXT,
    memory_id TEXT, payload_json TEXT NOT NULL, row_sha256 TEXT NOT NULL,
    UNIQUE(kind, row_sha256));
CREATE INDEX IF NOT EXISTS event_ledger_event ON event_ledger(event_id);
CREATE INDEX IF NOT EXISTS event_ledger_kind ON event_ledger(kind, seq);
CREATE TABLE IF NOT EXISTS event_state(
    key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at TEXT);
"""

EVENT_COLUMNS = {
    'story': 'TEXT', 'title': 'TEXT', 'status': 'TEXT', 'first_at': 'TEXT',
    'last_at': 'TEXT', 'story_source': 'TEXT', 'confidence': 'REAL', 'approved': 'INTEGER',
    'created_at': 'TEXT', 'updated_at': 'TEXT', 'related_events_json': 'TEXT',
    'previous_events_json': 'TEXT', 'source_parts_json': 'TEXT', 'sources_json': 'TEXT',
    'payload_json': 'TEXT', 'import_baseline': 'INTEGER', 'import_signature': 'TEXT',
}
STAGE_COLUMNS = {'text': 'TEXT', 'facts_json': 'TEXT', 'ord': 'INTEGER'}


def _columns(connection, table):
    return {row[1] for row in connection.execute('PRAGMA table_info(%s)' % table)}


def ensure_schema(connection):
    """Add the P2-A columns once; safe to run on every open."""
    for table, columns in (('events', EVENT_COLUMNS), ('event_stages', STAGE_COLUMNS)):
        existing = _columns(connection, table)
        for name, kind in columns.items():
            if name not in existing:
                connection.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, kind))
    connection.executescript(EVENT_LEDGER_SCHEMA)
    connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                       (str(SCHEMA_VERSION),))
    connection.commit()


def _as_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else None


def ledger_kind(path, role):
    """Map a legacy event JSONL path to its event_ledger kind, or None."""
    name = str(path).replace('\\', '/').rsplit('/', 1)[-1]
    if name not in EVENT_LEDGER_FILES:
        return None
    prefix = '%s/' % role
    if not str(path).replace('\\', '/').startswith(prefix):
        return None
    return EVENT_LEDGER_FILES[name]


def append_ledger(connection, kind, row):
    """Append one lossless event-ledger row; duplicate rows are idempotent by hash."""
    ensure_schema(connection)
    payload = json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    connection.execute(
        'INSERT OR IGNORE INTO event_ledger(at,kind,event_id,stage_id,memory_id,payload_json,row_sha256) '
        'VALUES(?,?,?,?,?,?,?)',
        (row.get('at') or row.get('time'), kind, row.get('event_id'), row.get('stage_id'),
         row.get('memory_id'), payload, digest))
    return digest


def read_ledger(connection, kind, limit=None):
    sql = 'SELECT payload_json FROM event_ledger WHERE kind=? ORDER BY seq'
    params = [kind]
    try:
        if limit is not None:
            sql = ('SELECT payload_json FROM event_ledger WHERE kind=? ORDER BY seq DESC LIMIT ?')
            params.append(int(limit))
            rows = list(reversed([json.loads(item[0]) for item in connection.execute(sql, params)]))
            return rows
        return [json.loads(item[0]) for item in connection.execute(sql, params)]
    except sqlite3.Error:
        return []


def ledger_count(connection, kind):
    try:
        row = connection.execute('SELECT COUNT(*) FROM event_ledger WHERE kind=?', (kind,)).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] if row else 0)


def write_state(connection, key, value):
    ensure_schema(connection)
    connection.execute(
        'INSERT INTO event_state(key,payload_json,updated_at) VALUES(?,?,?) '
        'ON CONFLICT(key) DO UPDATE SET payload_json=excluded.payload_json, '
        'updated_at=excluded.updated_at',
        (str(key), json.dumps(value, ensure_ascii=False, sort_keys=True), value.get('updated_at') if isinstance(value, dict) else None))
    connection.commit()


def read_state(connection, key):
    try:
        row = connection.execute('SELECT payload_json FROM event_state WHERE key=?', (str(key),)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def read_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def upsert_event(connection, row):
    ensure_schema(connection)
    event_id = str(row.get('event_id') or '').strip()
    if not event_id:
        return
    sources = row.get('sources') or []
    connection.execute(
        'INSERT INTO events(event_id, memory_id, source_count) VALUES(?,?,?) '
        'ON CONFLICT(event_id) DO UPDATE SET memory_id=excluded.memory_id, '
        'source_count=excluded.source_count',
        (event_id, row.get('memory_id'), len(sources) if isinstance(sources, list) else 0))
    connection.execute(
        'UPDATE events SET story=?, title=?, status=?, first_at=?, last_at=?, story_source=?, '
        'confidence=?, approved=?, created_at=?, updated_at=?, related_events_json=?, '
        'previous_events_json=?, source_parts_json=?, sources_json=?, payload_json=?, '
        'import_baseline=?, import_signature=? WHERE event_id=?',
        (row.get('story'), row.get('title'), row.get('status'), row.get('first_at'),
         row.get('last_at'), row.get('story_source'), row.get('confidence'),
         int(bool(row.get('approved'))), row.get('created_at'), row.get('updated_at'),
         _as_json(row.get('related_events')), _as_json(row.get('previous_events')),
         _as_json(row.get('source_parts')), _as_json(sources), _as_json(row),
         int(bool(row.get('import_baseline'))), row.get('import_signature'), event_id))
    connection.commit()


def upsert_stage(connection, row, index=None):
    ensure_schema(connection)
    stage_id = str(row.get('stage_id') or '').strip()
    event_id = str(row.get('event_id') or '').strip()
    if not stage_id or not event_id:
        return
    if index is None:
        existing = connection.execute(
            'SELECT ord FROM event_stages WHERE stage_id=?', (stage_id,)).fetchone()
        if existing is not None:
            index = existing[0]
        else:
            top = connection.execute(
                'SELECT COALESCE(MAX(ord), -1) + 1 FROM event_stages').fetchone()
            index = int(top[0] if top else 0)
    connection.execute(
        'INSERT INTO event_stages(stage_id, event_id, at, state, text, facts_json, ord) '
        'VALUES(?,?,?,?,?,?,?) ON CONFLICT(stage_id) DO UPDATE SET event_id=excluded.event_id, '
        'at=excluded.at, state=excluded.state, text=excluded.text, '
        'facts_json=excluded.facts_json, ord=excluded.ord',
        (stage_id, event_id, row.get('at'), row.get('state'), row.get('text'),
         _as_json(row.get('facts')), index))
    connection.commit()


def read_rows(connection, table):
    """JSONL-shaped rows, merging the lossless payload with the extracted columns."""
    rows = []
    if table == 'events':
        for item in connection.execute('SELECT payload_json FROM events ORDER BY event_id'):
            payload = json.loads(item[0]) if item[0] else {}
            rows.append(payload)
    elif table == 'event-stages':
        for item in connection.execute('SELECT text, facts_json, at, state, stage_id, event_id '
                                       'FROM event_stages ORDER BY ord, stage_id'):
            rows.append({'stage_id': item[4], 'event_id': item[5], 'at': item[2],
                         'state': item[3], 'text': item[0],
                         'facts': json.loads(item[1]) if item[1] else None})
    return rows


def import_events(connection, events_path, stages_path):
    """Upsert every event/stage, keeping the unknown fields in payload_json."""
    events = read_jsonl(events_path)
    stages = read_jsonl(stages_path)
    ensure_schema(connection)
    for row in events:
        event_id = str(row.get('event_id') or '').strip()
        if not event_id:
            continue
        sources = row.get('sources') or []
        connection.execute(
            'INSERT INTO events(event_id, memory_id, source_count) VALUES(?,?,?) '
            'ON CONFLICT(event_id) DO UPDATE SET memory_id=excluded.memory_id, '
            'source_count=excluded.source_count',
            (event_id, row.get('memory_id'), len(sources) if isinstance(sources, list) else 0))
        connection.execute(
            'UPDATE events SET story=?, title=?, status=?, first_at=?, last_at=?, story_source=?, '
            'confidence=?, approved=?, created_at=?, updated_at=?, related_events_json=?, '
            'previous_events_json=?, source_parts_json=?, sources_json=?, payload_json=?, '
            'import_baseline=?, import_signature=? WHERE event_id=?',
            (row.get('story'), row.get('title'), row.get('status'), row.get('first_at'),
             row.get('last_at'), row.get('story_source'), row.get('confidence'),
             int(bool(row.get('approved'))), row.get('created_at'), row.get('updated_at'),
             _as_json(row.get('related_events')), _as_json(row.get('previous_events')),
             _as_json(row.get('source_parts')), _as_json(sources), _as_json(row),
             int(bool(row.get('import_baseline'))), row.get('import_signature'), event_id))
    for index, row in enumerate(stages):
        stage_id = str(row.get('stage_id') or '').strip()
        event_id = str(row.get('event_id') or '').strip()
        if not stage_id or not event_id:
            continue
        connection.execute(
            'INSERT INTO event_stages(stage_id, event_id, at, state, text, facts_json, ord) '
            'VALUES(?,?,?,?,?,?,?) ON CONFLICT(stage_id) DO UPDATE SET event_id=excluded.event_id, '
            'at=excluded.at, state=excluded.state, text=excluded.text, '
            'facts_json=excluded.facts_json, ord=excluded.ord',
            (stage_id, event_id, row.get('at'), row.get('state'), row.get('text'),
             _as_json(row.get('facts')), index))
    connection.commit()
    return {'events': len(events), 'stages': len(stages)}


def read_event(connection, event_id):
    """Full story plus stages, used by the export and by P2-B's read adapter."""
    row = connection.execute(
        'SELECT event_id, memory_id, source_count, story, title, status, first_at, last_at, '
        'story_source, confidence, approved, created_at, updated_at, related_events_json, '
        'previous_events_json, source_parts_json, sources_json, payload_json '
        'FROM events WHERE event_id=?', (event_id,)).fetchone()
    if row is None:
        return None
    keys = ('event_id', 'memory_id', 'source_count', 'story', 'title', 'status', 'first_at',
            'last_at', 'story_source', 'confidence', 'approved', 'created_at', 'updated_at',
            'related_events', 'previous_events', 'source_parts', 'sources', 'payload')
    item = dict(zip(keys, row))
    for key in ('related_events', 'previous_events', 'source_parts', 'sources', 'payload'):
        item[key] = json.loads(item[key]) if isinstance(item[key], str) else item[key]
    item['stages'] = [
        {'stage_id': stage[0], 'event_id': stage[1], 'at': stage[2], 'state': stage[3],
         'text': stage[4], 'facts': json.loads(stage[5]) if stage[5] else None}
        for stage in connection.execute(
            'SELECT stage_id, event_id, at, state, text, facts_json FROM event_stages '
            'WHERE event_id=? ORDER BY ord, stage_id', (event_id,))]
    return item


def export_jsonl(connection, out_dir):
    """Write Ombre-compatible events.jsonl / event-stages.jsonl for rollback."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    events, stages = [], []
    rows = connection.execute(
        'SELECT event_id FROM events ORDER BY event_id').fetchall()
    for (event_id,) in rows:
        item = read_event(connection, event_id)
        payload = dict(item.pop('payload') or {})
        payload.update({key: value for key, value in item.items() if key != 'stages'})
        events.append(payload)
        for stage in item['stages']:
            stages.append({'stage_id': stage['stage_id'], 'event_id': stage['event_id'],
                           'at': stage['at'], 'state': stage['state'], 'text': stage['text'],
                           'facts': stage['facts']})
    for name, rows in (('events.jsonl', events), ('event-stages.jsonl', stages)):
        with open(out / name, 'w', encoding='utf-8', newline='\n') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    ledger_counts = {}
    for filename, kind in EVENT_LEDGER_FILES.items():
        rows = read_ledger(connection, kind)
        with open(out / filename, 'w', encoding='utf-8', newline='\n') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        ledger_counts[kind] = len(rows)
    state = read_state(connection, 'event-dream')
    if state is not None:
        (out / EVENT_STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    return {'events': len(events), 'stages': len(stages), 'ledger': ledger_counts,
            'state': state is not None}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('migrate', 'export', 'show'))
    parser.add_argument('--db', required=True)
    parser.add_argument('--events')
    parser.add_argument('--stages')
    parser.add_argument('--out')
    parser.add_argument('--id')
    args = parser.parse_args(argv)
    connection = sqlite3.connect(args.db)
    try:
        if args.action == 'migrate':
            ensure_schema(connection)
            print(json.dumps(import_events(connection, args.events, args.stages), ensure_ascii=False))
        elif args.action == 'export':
            print(json.dumps(export_jsonl(connection, args.out), ensure_ascii=False))
        else:
            print(json.dumps(read_event(connection, args.id), ensure_ascii=False))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
