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
TABLE_NAMES = ('tombstones', 'delete_requests', 'write_journal', 'experience_recoveries')
TABLE_DDLS = (
    'CREATE TABLE IF NOT EXISTS tombstones(memory_id TEXT PRIMARY KEY, deleted_at TEXT, reason_hash TEXT)',
    'CREATE TABLE IF NOT EXISTS delete_requests(request_id TEXT PRIMARY KEY, memory_id TEXT, reason TEXT, status TEXT, created_at TEXT, raw_json TEXT)',
    'CREATE TABLE IF NOT EXISTS write_journal(seq INTEGER PRIMARY KEY, time TEXT, operation TEXT, memory_id TEXT, detail_json TEXT, status TEXT)',
    'CREATE TABLE IF NOT EXISTS experience_recoveries(source_key TEXT PRIMARY KEY, memory_id TEXT NOT NULL, event_id TEXT NOT NULL, stage_id TEXT NOT NULL, created_at TEXT NOT NULL)',
)
TABLES = ';\n'.join(TABLE_DDLS) + ';\n'


def _sha(value):
    data = value.encode('utf-8') if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def ensure_schema(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(memories)')}
    need_vector_state = 'vector_state' not in columns
    present = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing_tables = any(name not in present for name in TABLE_NAMES)
    if not need_vector_state and not missing_tables:
        if connection.in_transaction:
            connection.commit()
        return
    # P6-S2 review: run schema DDL and apply_digest() in ONE transaction (avoid executescript(),
    # which breaks transaction boundaries). Otherwise creating empty experience_recoveries before
    # apply_digest() leaves meta.digest stale across crashes.
    if connection.in_transaction:
        connection.commit()
    connection.execute('BEGIN IMMEDIATE')
    try:
        if need_vector_state:
            connection.execute(SCHEMA_ADDITIONS[0])
        for ddl in TABLE_DDLS:
            connection.execute(ddl)
        try:
            apply_digest(connection)
        except sqlite3.Error:
            # A database without meta/canonical tables must still open for repair; the first real
            # write or import stores the digest.
            pass
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def apply_digest(connection):
    """Update the canonical digest inside the caller's current transaction."""
    from veil_brain import canonical_digest
    connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('digest', ?)",
                       (canonical_digest(connection),))


def refresh_digest(connection):
    """Keep the stored whole-db digest current after an authority write.

    check_db()/the cutover snapshot compare meta.digest with the actual rows; a live authority
    database must not look "corrupt" merely because a memory was saved after the import.
    """
    apply_digest(connection)
    connection.commit()


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
    if record.get('pinned'):
        lines.append('pinned: true')
    if record.get('why_remembered'):
        lines.append('why_remembered: %s' % record['why_remembered'])
    lines.append('---')
    return '\n'.join(lines) + '\n'


def _split_raw_frontmatter(raw):
    """(bom, opening line, header, closing line, rest) of a Markdown memory, or None.

    P6-S1 G-A04: the old code only accepted LF; the 154 CRLF memories silently skipped metadata
    sync. Line endings are preserved exactly, so updating one field never rewrites the whole file."""
    if not isinstance(raw, str):
        return None
    bom, text = ('\ufeff', raw[1:]) if raw.startswith('\ufeff') else ('', raw)
    first, newline, rest = text.partition('\n')
    if not newline or first.strip() != '---':
        return None
    lines = rest.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == '---':
            return bom, first + newline, ''.join(lines[:index]), line, ''.join(lines[index + 1:])
    return None


def _with_metadata(raw, values):
    """Keep raw_markdown's front matter in step with title/importance/pinned.
    Recall and the memory page read these fields; a database-only update would leave them stale.
    LF, CRLF and a UTF-8 BOM are all accepted; every untouched line and the body stay byte-identical."""
    parts = _split_raw_frontmatter(raw)
    if parts is None:
        return raw
    bom, opening, header, closing, rest = parts
    wanted = {key: value for key, value in values.items() if value is not None}
    lines = header.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in (lines[0] if lines else '') else '\n'
    seen = set()
    out = []
    for line in lines:
        content = line.rstrip('\r\n')
        ending = line[len(content):]
        key = content.partition(':')[0].strip()
        if key in wanted:
            value = wanted[key]
            if key == 'pinned':
                value = 'true' if value else 'false'
            out.append('%s: %s%s' % (key, value, ending))
            seen.add(key)
        else:
            out.append(line)
    for key, value in wanted.items():
        if key not in seen:
            if key == 'pinned':
                value = 'true' if value else 'false'
            out.append('%s: %s%s' % (key, value, newline))
    return bom + opening + ''.join(out) + closing + rest


EMBEDDING_MODEL_META = 'embedding_model'


def _valid_vector(values):
    """A list of floats, whatever the embedding model's dimension is (P6-S1 G-A14), or None."""
    if not values:
        return None
    try:
        import array
        packed = array.array('f', list(values))
    except (TypeError, ValueError, OverflowError):
        return None
    return list(packed) if packed else None


def embedding_model_of(embedder):
    """The embedding model behind a provider object, or '' for a plain callable (tests, scripts)."""
    owner = getattr(embedder, '__self__', None)
    model = getattr(owner, 'model', None) if owner is not None else None
    return str(model).strip() if model else ''


def _meta_model(connection):
    try:
        row = connection.execute(
            'SELECT value FROM meta WHERE key=?', (EMBEDDING_MODEL_META,)).fetchone()
    except sqlite3.OperationalError:
        return ''
    return str((row[0] if row else '') or '').strip()


def _remember_embedding_model(connection, model):
    if not model:
        return
    if _meta_model(connection):
        return
    try:
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
                           (EMBEDDING_MODEL_META, model))
    except sqlite3.OperationalError:
        pass


def _vector_compat(connection, vector, state, model):
    """(vector, state, reason): never store a vector from another embedding model.

    The stored model is recorded once in meta; when the configured model changes its vectors are
    incompatible, so the memory stays vector_state='pending' (and a diagnostic is journalled) until
    an operator reindexes instead of silently mixing two models.
    """
    if vector is None or state != 'ready':
        return vector, state, ''
    stored = _meta_model(connection)
    if model and stored and model != stored:
        return None, 'pending', 'embedding_model_mismatch'
    _remember_embedding_model(connection, model)
    return vector, state, ''


def _vector(record, vector, embedder):
    if vector is not None:
        values = _valid_vector(vector)
        return (values, 'ready') if values else (None, 'pending')
    if embedder is None:
        return None, 'pending'
    try:
        values = _valid_vector(embedder(record['body']))
    except Exception:
        values = None
    return (values, 'ready') if values else (None, 'pending')


def _journal(connection, operation, memory_id, detail, status):
    connection.execute('INSERT INTO write_journal(time, operation, memory_id, detail_json, status) '
                       'VALUES(?,?,?,?,?)',
                       (now(), operation, memory_id,
                        json.dumps(detail, ensure_ascii=False, sort_keys=True), status))


def _prepare_memory_vector(body, vector, embedder):
    return _vector({'body': body}, vector, embedder)


def apply_create_memory(connection, record, *, vector=None, state='pending', embedder_model=''):
    """Insert a memory inside an already-open transaction (no commit/digest)."""
    body = record.get('body') or ''
    if not body.strip():
        raise ValueError('empty memory body')
    vector, state, rejected = _vector_compat(connection, vector, state, embedder_model)
    memory_id = record.get('id') or new_id(connection)
    title = record.get('title') or ''
    created = record.get('created') or now()
    frontmatter = _frontmatter({'id': memory_id, 'title': title,
                                'type': record.get('type') or 'dynamic',
                                'created': created, 'importance': record.get('importance'),
                                'pinned': record.get('pinned'),
                                'why_remembered': record.get('why_remembered')})
    raw = frontmatter + body
    frontmatter_json = json.dumps({'id': memory_id, 'title': title,
                                   'type': record.get('type') or 'dynamic',
                                   'created': created}, ensure_ascii=False, sort_keys=True)
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
        blob = _vector_blob(vector)
        connection.execute(
            'INSERT OR REPLACE INTO vectors(memory_id,dim,vector,vector_sha256,updated_at,content_hash) '
            'VALUES(?,?,?,?,?,?)',
            (memory_id, len(vector), blob, _sha(blob), now(), _sha(body)))
    detail = {'body_sha256': _sha(body), 'vector_state': state}
    if rejected:
        detail['vector_rejected'] = rejected
    _journal(connection, 'create', memory_id, detail, state)
    return {'status': 'saved' if state == 'ready' else 'saved_index_pending',
            'memory_id': memory_id, 'vector_state': state, 'vector_rejected': rejected}


def apply_update_memory(connection, memory_id, changes, *, vector=None, state=None,
                        operation_reason='', why_remembered='', archive_raw=False,
                        embedder_model=''):
    """Update a memory + archive single version inside an already-open transaction."""
    row = connection.execute('SELECT body,raw_markdown,file_rel,vector_state FROM memories WHERE id=? AND '
                             'is_deleted=0 AND is_tombstone=0', (memory_id,)).fetchone()
    if row is None:
        raise KeyError(memory_id)
    old_body, old_raw, file_rel, old_state = row
    changes = dict(changes)
    if changes.get('body') == old_body:
        changes.pop('body', None)
    body_changed = 'body' in changes
    body = changes.get('body', old_body) if body_changed else old_body
    if not isinstance(body, str) or not body.strip():
        raise ValueError('empty memory body')
    eff_state = (state or 'pending') if body_changed else old_state
    rejected = ''
    if body_changed:
        vector, eff_state, rejected = _vector_compat(connection, vector, eff_state, embedder_model)
    if not changes:
        return {'status': 'saved' if old_state == 'ready' else 'saved_index_pending',
                'memory_id': memory_id, 'vector_state': old_state, 'changed': False}
    fields = sorted(changes)
    archived_old = old_raw if (archive_raw and isinstance(old_raw, str) and old_raw) else old_body
    connection.execute('INSERT INTO versions(memory_id,time,operation_reason,why_remembered,'
                       'fields_json,old_content,old_sha256) VALUES(?,?,?,?,?,?,?)',
                       (memory_id, now(), operation_reason[:500], why_remembered[:500],
                        json.dumps(fields), archived_old, _sha(archived_old)))
    raw = changes.get('raw_markdown', old_raw.replace(old_body, body, 1))
    raw = _with_metadata(raw, {'title': changes.get('title'),
                               'importance': changes.get('importance'),
                               'pinned': changes.get('pinned')})
    connection.execute('UPDATE memories SET body=?,body_sha256=?,raw_markdown=?,raw_sha256=?,'
                       'title=COALESCE(?,title),importance=COALESCE(?,importance),'
                       'pinned=COALESCE(?,pinned),why_remembered=COALESCE(?,why_remembered),'
                       'vector_state=COALESCE(?,vector_state) WHERE id=?',
                       (body, _sha(body), raw, _sha(raw), changes.get('title'),
                        changes.get('importance'), changes.get('pinned'),
                        why_remembered or None, eff_state if body_changed else None, memory_id))
    if body_changed and eff_state == 'ready' and vector:
        blob = _vector_blob(vector)
        connection.execute('INSERT OR REPLACE INTO vectors(memory_id,dim,vector,vector_sha256,'
                           'updated_at,content_hash) VALUES(?,?,?,?,?,?)',
                           (memory_id, len(vector), blob, _sha(blob), now(), _sha(body)))
    elif body_changed:
        connection.execute('DELETE FROM vectors WHERE memory_id=?', (memory_id,))
    detail = {'body_sha256': _sha(body), 'vector_state': eff_state}
    if rejected:
        detail['vector_rejected'] = rejected
    _journal(connection, 'update', memory_id, detail, eff_state)
    return {'status': 'saved' if eff_state == 'ready' else 'saved_index_pending',
            'memory_id': memory_id, 'vector_state': eff_state, 'changed': True,
            'vector_rejected': rejected}


def create_memory(connection, record, *, vector=None, embedder=None):
    """Insert one memory. Returns saved or saved_index_pending; raises on a DB failure."""
    ensure_schema(connection)
    body = record.get('body') or ''
    if not body.strip():
        raise ValueError('empty memory body')
    vector, state = _vector({**record, 'body': body}, vector, embedder)
    model = embedding_model_of(embedder)
    try:
        connection.execute('BEGIN IMMEDIATE')
        result = apply_create_memory(connection, record, vector=vector, state=state,
                                     embedder_model=model)
        apply_digest(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return result


def _vector_blob(values):
    import array
    return array.array('f', values).tobytes()


def update_memory(connection, memory_id, changes, *, vector=None, embedder=None,
                  operation_reason='', why_remembered='', archive_raw=False):
    """Rewrite a memory, archiving the old version first, in one transaction."""
    ensure_schema(connection)
    body_changed = 'body' in changes
    body = changes.get('body') if body_changed else None
    if body_changed and (not isinstance(body, str) or not body.strip()):
        raise ValueError('empty memory body')
    if body_changed:
        vector, state = _vector({'body': body}, vector, embedder)
    else:
        vector, state = None, None
    model = embedding_model_of(embedder)
    try:
        connection.execute('BEGIN IMMEDIATE')
        result = apply_update_memory(
            connection, memory_id, changes, vector=vector, state=state,
            operation_reason=operation_reason, why_remembered=why_remembered,
            archive_raw=archive_raw, embedder_model=model)
        apply_digest(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return result


def set_state(connection, memory_id, *, locked=None, archived=None, deleted=None,
              reason=''):
    """State changes keep the existing visibility contract; no blanket 'unsearchable'."""
    ensure_schema(connection)
    row = connection.execute('SELECT is_deleted,is_tombstone FROM memories WHERE id=?',
                             (memory_id,)).fetchone()
    if row is None or row[0] or row[1]:
        raise KeyError(memory_id)
    try:
        connection.execute('BEGIN IMMEDIATE')
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
        _journal(connection, 'state', memory_id, {'locked': locked,
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
    old_content = row[0] or ''
    changes = {'body': old_content}
    if isinstance(old_content, str) and old_content.startswith('---\n'):
        from memory_agent import parse_frontmatter
        meta, body = parse_frontmatter(old_content)
        if body.strip():
            changes = {'body': body.strip()}
            title = meta.get('title') or meta.get('name')
            if isinstance(title, str) and title.strip():
                changes['title'] = title.strip()[:100]
    return update_memory(connection, memory_id, changes, vector=vector,
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
    model = embedding_model_of(embedder)
    for memory_id, body in rows:
        try:
            values = _valid_vector(embedder(body))
        except Exception:
            values = None
        if not values:
            continue
        # P6-S2 review: the embedding call runs outside the role write lock, so the body may have
        # been rewritten (or already indexed) while we waited. Only write the vector and mark the
        # row ready when it still holds the body we embedded and is still pending; a newer body
        # must never be replaced by this stale vector.
        try:
            connection.execute('BEGIN IMMEDIATE')
        except sqlite3.Error:
            continue
        try:
            current = connection.execute(
                "SELECT body,vector_state FROM memories WHERE id=? AND is_deleted=0 "
                "AND is_tombstone=0", (memory_id,)).fetchone()
            if not current or current[0] != body or current[1] != 'pending':
                connection.rollback()
                continue
            values, state, rejected = _vector_compat(connection, values, 'ready', model)
            if not values:
                connection.rollback()
                continue
            blob = _vector_blob(values)
            connection.execute('INSERT OR REPLACE INTO vectors(memory_id,dim,vector,vector_sha256,'
                               'updated_at,content_hash) VALUES(?,?,?,?,?,?)',
                               (memory_id, len(values), blob, _sha(blob), now(), _sha(body)))
            connection.execute("UPDATE memories SET vector_state='ready' "
                               "WHERE id=? AND body=? AND vector_state='pending'", (memory_id, body))
            apply_digest(connection)
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            continue
        saved += 1
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
