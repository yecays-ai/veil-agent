"""P2-A: transactional Brain writes. Brain is the authority; no Ombre is called here.

Three outcomes are explicit: saved (memory, version/state, vector and journal committed),
saved_index_pending (memory committed, embedding failed and retried later) and failed (nothing
committed). A hard delete keeps only a minimal tombstone, never the body or quotes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid

SCHEMA_ADDITIONS = (
    "ALTER TABLE memories ADD COLUMN vector_state TEXT NOT NULL DEFAULT 'ready'",
)
TABLES = """
CREATE TABLE IF NOT EXISTS tombstones(memory_id TEXT PRIMARY KEY, deleted_at TEXT, reason_hash TEXT);
CREATE TABLE IF NOT EXISTS delete_requests(request_id TEXT PRIMARY KEY, memory_id TEXT, reason TEXT, status TEXT, created_at TEXT, raw_json TEXT);
CREATE TABLE IF NOT EXISTS write_journal(seq INTEGER PRIMARY KEY, time TEXT, operation TEXT,
    memory_id TEXT, detail_json TEXT, status TEXT);
"""


def _sha(value):
    data = value.encode('utf-8') if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def ensure_schema(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(memories)')}
    if 'vector_state' not in columns:
        connection.execute(SCHEMA_ADDITIONS[0])
    connection.executescript(TABLES)
    connection.commit()


def refresh_digest(connection):
    """Keep the stored whole-db digest current after an authority write.

    check_db()/the cutover snapshot compare meta.digest with the actual rows; a live authority
    database must not look "corrupt" merely because a memory was saved after the import.
    """
    try:
        from veil_brain import canonical_digest
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('digest', ?)",
                           (canonical_digest(connection),))
        connection.commit()
    except Exception:
        pass


def new_id(connection):
    for _ in range(20):
        memory_id = uuid.uuid4().hex[:12]
        if not connection.execute('SELECT 1 FROM memories WHERE id=?', (memory_id,)).fetchone():
            return memory_id
    raise RuntimeError('could not allocate a memory id')


def _frontmatter(record):
    lines = ['---', 'id: %s' % record['id']]
    for key in ('title', 'type', 'created', 'importance'):
        if record.get(key) not in (None, ''):
            lines.append('%s: %s' % (key, record[key]))
    lines.append('---')
    return '\n'.join(lines) + '\n'


def _vector(record, vector, embedder):
    if vector is not None:
        return vector, 'ready'
    if embedder is None:
        return None, 'pending'
    try:
        values = embedder(record['body'])
        if values and len(values) == 1024:
            return list(values), 'ready'
    except Exception:
        pass
    return None, 'pending'


def _journal(connection, operation, memory_id, detail, status):
    connection.execute('INSERT INTO write_journal(time, operation, memory_id, detail_json, status) '
                       'VALUES(?,?,?,?,?)',
                       (now(), operation, memory_id,
                        json.dumps(detail, ensure_ascii=False, sort_keys=True), status))


def create_memory(connection, record, *, vector=None, embedder=None):
    """Insert one memory. Returns saved or saved_index_pending; raises on a DB failure."""
    ensure_schema(connection)
    body = record.get('body') or ''
    if not body.strip():
        raise ValueError('empty memory body')
    vector, state = _vector({**record, 'body': body}, vector, embedder)
    memory_id = record.get('id') or new_id(connection)
    title = record.get('title') or ''
    created = record.get('created') or now()
    frontmatter = _frontmatter({'id': memory_id, 'title': title,
                                'type': record.get('type') or 'dynamic',
                                'created': created, 'importance': record.get('importance')})
    raw = frontmatter + body
    frontmatter_json = json.dumps({'id': memory_id, 'title': title,
                                   'type': record.get('type') or 'dynamic',
                                   'created': created}, ensure_ascii=False, sort_keys=True)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute(
            'INSERT INTO memories(id,bucket,file_rel,title,name,body,body_sha256,raw_markdown,'
            'raw_sha256,frontmatter_raw,frontmatter_json,created,last_active,type,importance,'
            'domain_json,tags_json,why_remembered,meaning,is_hidden,is_tombstone,is_deleted,'
            'pinned,extra_json,vector_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (memory_id, record.get('bucket') or 'dynamic',
             record.get('file_rel') or ('dynamic/%s.md' % memory_id), title, title, body,
             _sha(body), raw, _sha(raw), frontmatter, frontmatter_json, created, created,
             record.get('type') or 'dynamic', record.get('importance') or 5,
             json.dumps(record.get('domain') or [], ensure_ascii=False),
             json.dumps(record.get('tags') or [], ensure_ascii=False),
             record.get('why_remembered') or '', record.get('meaning') or '',
             int(bool(record.get('hidden'))), 0, 0,
             int(bool(record.get('pinned'))), '{}', state))
        if vector:
            connection.execute(
                'INSERT OR REPLACE INTO vectors(memory_id,dim,vector,vector_sha256,updated_at,content_hash) '
                'VALUES(?,?,?,?,?,?)',
                (memory_id, len(vector), _vector_blob(vector), _sha(_vector_blob(vector)), now(), _sha(body)))
        _journal(connection, 'create', memory_id, {'body_sha256': _sha(body), 'vector_state': state}, state)
        connection.commit()
        refresh_digest(connection)
    except Exception:
        connection.rollback()
        raise
    return {'status': 'saved' if state == 'ready' else 'saved_index_pending',
            'memory_id': memory_id, 'vector_state': state}


def _vector_blob(values):
    import array
    return array.array('f', values).tobytes()


def update_memory(connection, memory_id, changes, *, vector=None, embedder=None,
                  operation_reason='', why_remembered=''):
    """Rewrite a memory, archiving the old body first, in one transaction."""
    ensure_schema(connection)
    row = connection.execute('SELECT body,raw_markdown,file_rel FROM memories WHERE id=? AND '
                             'is_deleted=0 AND is_tombstone=0', (memory_id,)).fetchone()
    if row is None:
        raise KeyError(memory_id)
    old_body, old_raw, file_rel = row
    body = changes.get('body', old_body)
    if not isinstance(body, str) or not body.strip():
        raise ValueError('empty memory body')
    vector, state = _vector({'body': body}, vector, embedder)
    fields = sorted(changes)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('INSERT INTO versions(memory_id,time,operation_reason,why_remembered,'
                           'fields_json,old_content,old_sha256) VALUES(?,?,?,?,?,?,?)',
                           (memory_id, now(), operation_reason[:500], why_remembered[:500],
                            json.dumps(fields), old_body, _sha(old_body)))
        raw = changes.get('raw_markdown', old_raw.replace(old_body, body, 1))
        connection.execute('UPDATE memories SET body=?,body_sha256=?,raw_markdown=?,raw_sha256=?,'
                           'title=COALESCE(?,title),importance=COALESCE(?,importance),'
                           'pinned=COALESCE(?,pinned),why_remembered=COALESCE(?,why_remembered),'
                           'vector_state=? WHERE id=?',
                           (body, _sha(body), raw, _sha(raw), changes.get('title'),
                            changes.get('importance'), changes.get('pinned'),
                            why_remembered or None, state, memory_id))
        if state == 'ready':
            connection.execute('INSERT OR REPLACE INTO vectors(memory_id,dim,vector,vector_sha256,'
                               'updated_at,content_hash) VALUES(?,?,?,?,?,?)',
                               (memory_id, len(vector), _vector_blob(vector), _sha(_vector_blob(vector)),
                                now(), _sha(body)))
        else:
            connection.execute('DELETE FROM vectors WHERE memory_id=?', (memory_id,))
        _journal(connection, 'update', memory_id, {'body_sha256': _sha(body), 'vector_state': state}, state)
        connection.commit()
        refresh_digest(connection)
    except Exception:
        connection.rollback()
        raise
    return {'status': 'saved' if state == 'ready' else 'saved_index_pending',
            'memory_id': memory_id, 'vector_state': state}


def set_state(connection, memory_id, *, hidden=None, locked=None, archived=None, deleted=None,
              reason=''):
    """State changes keep the existing visibility contract; no blanket 'unsearchable'."""
    ensure_schema(connection)
    row = connection.execute('SELECT is_deleted,is_tombstone FROM memories WHERE id=?',
                             (memory_id,)).fetchone()
    if row is None or row[0] or row[1]:
        raise KeyError(memory_id)
    try:
        connection.execute('BEGIN IMMEDIATE')
        if hidden is not None:
            connection.execute('UPDATE memories SET is_hidden=? WHERE id=?', (int(bool(hidden)), memory_id))
            _sidecar(connection, memory_id, 'hidden' if hidden else 'shown')
        if deleted is not None:
            connection.execute('UPDATE memories SET is_deleted=?,deleted_at=? WHERE id=?',
                               (int(bool(deleted)), now() if deleted else None, memory_id))
            _sidecar(connection, memory_id, 'deleted' if deleted else 'restored')
        if archived is not None:
            connection.execute("UPDATE memories SET bucket=?,type=? WHERE id=?",
                               ('archive' if archived else 'dynamic',
                                'archived' if archived else 'dynamic', memory_id))
            _sidecar(connection, memory_id, 'archived' if archived else 'unarchived')
        if locked is not None:
            connection.execute(
                "INSERT INTO sidecar_state(memory_id,locked,links_json,sources_json) VALUES(?,?, '[]','[]') "
                'ON CONFLICT(memory_id) DO UPDATE SET locked=excluded.locked',
                (memory_id, int(bool(locked))))
            _sidecar(connection, memory_id, 'locked' if locked else 'unlocked', raw={'locked': bool(locked)})
        _journal(connection, 'state', memory_id, {'hidden': hidden, 'locked': locked,
                                                   'archived': archived, 'deleted': deleted,
                                                   'reason_hash': _sha(reason)[:16]}, 'saved')
        connection.commit()
        refresh_digest(connection)
    except Exception:
        connection.rollback()
        raise
    return {'status': 'saved', 'memory_id': memory_id}


def _sidecar(connection, memory_id, kind, raw=None):
    payload = {'memory_id': memory_id, 'kind': kind}
    if isinstance(raw, dict):
        payload.update(raw)
    connection.execute('INSERT INTO sidecar_events(memory_id,kind,time,raw_json) VALUES(?,?,?,?)',
                       (memory_id, kind, now(),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True)))


def hard_delete(connection, memory_id, reason=''):
    """Erase sensitive content, keep only the minimal marker that prevents resurrection."""
    ensure_schema(connection)
    if not connection.execute('SELECT 1 FROM memories WHERE id=?', (memory_id,)).fetchone():
        return {'status': 'missing', 'memory_id': memory_id}
    try:
        connection.execute('BEGIN IMMEDIATE')
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('vectors', 'versions', 'relations', 'sources', 'quotes', 'sidecar_state',
                      'sidecar_events'):
            if table in tables:
                connection.execute('DELETE FROM %s WHERE memory_id=?' % table, (memory_id,))
        if 'event_sources' in tables:
            affected = [row[0] for row in connection.execute(
                'SELECT DISTINCT event_id FROM event_sources WHERE memory_id=?', (memory_id,))]
            connection.execute('DELETE FROM event_sources WHERE memory_id=?', (memory_id,))
            if 'events' in tables:
                connection.execute('UPDATE events SET memory_id=NULL WHERE memory_id=?', (memory_id,))
                if affected:
                    marks = ','.join('?' * len(affected))
                    connection.execute(
                        'UPDATE events SET source_count=(SELECT COUNT(*) FROM event_sources '
                        'WHERE event_sources.event_id=events.event_id) WHERE event_id IN (%s)' % marks,
                        affected)
            for event_id in affected:
                remaining = connection.execute(
                    'SELECT COUNT(*) FROM event_sources WHERE event_id=?', (event_id,)).fetchone()[0]
                if remaining:
                    continue
                if 'event_stages' in tables:
                    connection.execute('DELETE FROM event_stages WHERE event_id=?', (event_id,))
                if 'events' in tables:
                    connection.execute('DELETE FROM events WHERE event_id=?', (event_id,))
        elif 'events' in tables:
            # Older Brain schemas have the event table but no source table.
            connection.execute('DELETE FROM events WHERE memory_id=?', (memory_id,))
        connection.execute('DELETE FROM memories WHERE id=?', (memory_id,))
        connection.execute('INSERT OR REPLACE INTO tombstones(memory_id,deleted_at,reason_hash) '
                           'VALUES(?,?,?)', (memory_id, now(), _sha(reason)[:16]))
        _journal(connection, 'hard_delete', memory_id, {'reason_hash': _sha(reason)[:16]}, 'saved')
        connection.commit()
        refresh_digest(connection)
    except Exception:
        connection.rollback()
        raise
    return {'status': 'saved', 'memory_id': memory_id, 'tombstone': True}


def restore_version(connection, memory_id, seq, *, vector=None, embedder=None):
    ensure_schema(connection)
    row = connection.execute('SELECT old_content FROM versions WHERE memory_id=? AND seq=?',
                             (memory_id, seq)).fetchone()
    if row is None:
        raise KeyError('version')
    return update_memory(connection, memory_id, {'body': row[0]}, vector=vector,
                         embedder=embedder, operation_reason='restore version %s' % seq)


def merge_into(connection, source_id, target_id, reason=''):
    if source_id == target_id:
        raise ValueError('cannot merge a memory into itself')
    if not connection.execute('SELECT 1 FROM memories WHERE id=?', (target_id,)).fetchone():
        raise KeyError(target_id)
    ensure_schema(connection)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute("UPDATE memories SET bucket='archive',type='archived' WHERE id=?", (source_id,))
        connection.execute(
            "INSERT INTO sidecar_state(memory_id,locked,links_json,sources_json,merged_into) "
            "VALUES(?,0,'[]','[]',?) ON CONFLICT(memory_id) DO UPDATE SET merged_into=excluded.merged_into",
            (source_id, target_id))
        _sidecar(connection, source_id, 'merged', raw={'memory_id': source_id, 'kind': 'merged',
                                                       'merged_into': target_id,
                                                       'reason_hash': _sha(reason)[:16]})
        _journal(connection, 'merge', source_id, {'target': target_id}, 'saved')
        connection.commit()
        refresh_digest(connection)
    except Exception:
        connection.rollback()
        raise
    return {'status': 'saved', 'memory_id': source_id, 'merged_into': target_id}


def retry_pending_vectors(connection, embedder):
    ensure_schema(connection)
    rows = connection.execute("SELECT id,body FROM memories WHERE vector_state='pending' "
                              "AND is_deleted=0 AND is_tombstone=0").fetchall()
    saved = 0
    for memory_id, body in rows:
        try:
            values = embedder(body)
            if not values or len(values) != 1024:
                continue
            with connection:
                connection.execute('INSERT OR REPLACE INTO vectors(memory_id,dim,vector,vector_sha256,'
                                   'updated_at,content_hash) VALUES(?,?,?,?,?,?)',
                                   (memory_id, len(values), _vector_blob(values),
                                    _sha(_vector_blob(values)), now(), _sha(body)))
                connection.execute("UPDATE memories SET vector_state='ready' WHERE id=?", (memory_id,))
            saved += 1
        except Exception:
            continue
    if saved:
        refresh_digest(connection)
    return {'retried': len(rows), 'saved': saved, 'pending': len(rows) - saved}


def get_memory(connection, memory_id):
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
        return dict(row) if row else None
    finally:
        connection.row_factory = None


def link(connection, memory_id, target_id):
    """Record a 'this continues that' link in Brain's sidecar state."""
    ensure_schema(connection)
    row = connection.execute('SELECT links_json FROM sidecar_state WHERE memory_id=?',
                             (memory_id,)).fetchone()
    links = []
    if row and row[0]:
        try:
            links = json.loads(row[0])
        except ValueError:
            links = []
    if target_id not in links:
        links.append(target_id)
    connection.execute(
        "INSERT INTO sidecar_state(memory_id,locked,links_json,sources_json) VALUES(?,0,?,'[]') "
        'ON CONFLICT(memory_id) DO UPDATE SET links_json=excluded.links_json',
        (memory_id, json.dumps(links, ensure_ascii=False)))
    _sidecar(connection, memory_id, 'link', raw={'memory_id': memory_id, 'kind': 'link',
                                                'to': target_id})
    connection.commit()
    refresh_digest(connection)
