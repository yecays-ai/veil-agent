"""Ticket P2-A: full event-story storage in Brain.

The P0-A schema only kept the event -> memory mapping. This module extends it to hold the story,
stages, sources and provenance for every event, losslessly. It is additive and idempotent.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

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
    connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                       (str(SCHEMA_VERSION),))
    connection.commit()


def _as_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else None


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


def upsert_stage(connection, row, index=0):
    ensure_schema(connection)
    stage_id = str(row.get('stage_id') or '').strip()
    event_id = str(row.get('event_id') or '').strip()
    if not stage_id or not event_id:
        return
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
    return {'events': len(events), 'stages': len(stages)}


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
