"""P2-B read accessors: the same information the Ombre file/vault readers exposed, from brain.db.

Read-only; no Markdown, embeddings.db or events.jsonl is needed here.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class BrainRead:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.connection = sqlite3.connect('file:%s?mode=ro' % self.db_path, uri=True)
        self.connection.row_factory = sqlite3.Row

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def memory(self, memory_id):
        row = self.connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def content(self, memory_id):
        row = self.memory(memory_id)
        return row['body'] if row else None

    def raw(self, memory_id):
        row = self.memory(memory_id)
        return row['raw_markdown'] if row else None

    def title(self, memory_id):
        row = self.memory(memory_id)
        return (row.get('title') or row.get('name')) if row else None

    def state(self, memory_id):
        row = self.connection.execute('SELECT locked, links_json, sources_json, merged_into '
                                      'FROM sidecar_state WHERE memory_id=?', (memory_id,)).fetchone()
        if row is None:
            return {'locked': False, 'links': [], 'sources': [], 'merged_into': None}
        return {'locked': bool(row['locked']), 'links': _json(row['links_json'], []),
                'sources': _json(row['sources_json'], []), 'merged_into': row['merged_into']}

    def versions(self, memory_id):
        return [{'seq': row['seq'], 'time': row['time'], 'operation_reason': row['operation_reason'],
                 'why_remembered': row['why_remembered'], 'fields': _json(row['fields_json'], []),
                 'old_content': row['old_content'], 'old_sha256': row['old_sha256']}
                for row in self.connection.execute(
                    'SELECT * FROM versions WHERE memory_id=? ORDER BY seq DESC', (memory_id,))]

    def panel_entries(self, limit=100):
        rows = self.connection.execute(
            'SELECT m.id, m.title, m.name, m.created, m.importance, m.bucket, m.type, '
            'm.is_hidden, m.is_tombstone, m.is_deleted, s.locked, s.merged_into, '
            '(SELECT COUNT(*) FROM versions v WHERE v.memory_id=m.id) AS versions '
            'FROM memories m LEFT JOIN sidecar_state s ON s.memory_id=m.id '
            'WHERE m.is_tombstone=0 AND m.is_deleted=0 ORDER BY m.created DESC LIMIT ?',
            (limit,)).fetchall()
        return [{'memory_id': row['id'], 'title': row['title'] or row['name'] or row['id'],
                 'time': row['created'], 'importance': row['importance'], 'bucket': row['bucket'],
                 'type': row['type'], 'hidden': bool(row['is_hidden']),
                 'locked': bool(row['locked']), 'merged_into': row['merged_into'],
                 'versions': row['versions']} for row in rows]

    def archived(self, limit=100):
        return [{'memory_id': row['id'], 'title': row['title'] or row['id'], 'time': row['created']}
                for row in self.connection.execute(
                    "SELECT id,title,created FROM memories WHERE bucket='archive' "
                    'AND is_tombstone=0 AND is_deleted=0 ORDER BY created DESC LIMIT ?', (limit,))]

    def all_vectors(self):
        import array
        vectors = {}
        for memory_id, dim, blob in self.connection.execute(
                'SELECT memory_id,dim,vector FROM vectors'):
            if not blob:
                continue
            values = array.array('f')
            values.frombytes(blob)
            vectors[memory_id] = values.tolist()
        return vectors

    def events(self, limit=100):
        return [{'event_id': row['event_id'], 'title': row['title'], 'status': row['status'],
                 'first_at': row['first_at'], 'last_at': row['last_at'],
                 'story_source': row['story_source']}
                for row in self.connection.execute(
                    'SELECT event_id,title,status,first_at,last_at,story_source FROM events '
                    'ORDER BY first_at DESC LIMIT ?', (limit,))]

    def event(self, event_id):
        import brain_events
        return brain_events.read_event(self.connection, event_id)


def _json(value, default):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return default
    return value if value is not None else default
