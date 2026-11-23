"""Ticket 429 / P0-B: shadow-sync Ombre's memory changes into VEIL Brain.

Ombre stays the only authority. This module only reads the role's memory files and ledgers and
mirrors them into brain.db; it never writes back, never repairs, never changes a memory.

Design: a tiny outbox plus a periodic hash reconciliation.

- A confirmed write in Memory.call() only enqueues the affected memory ID. Enqueueing is O(1)
  and every failure is swallowed (logged), so a broken shadow database or worker can never fail
  or noticeably slow an Ombre write. Anything missed that way is caught by the next reconcile.
- The worker applies records idempotently (upsert keyed by memory id / source row index), so a
  replay after a crash cannot create duplicates.
- Reconciliation walks the source, compares body/raw hashes, state fields, versions, sidecar
  state, vectors and event links, applies the differences and writes a report. Each sync updates
  the stored digest, so veil_brain.check still validates the shadow database between writes.

Config (role character.toml, optional)::

    [memory.brain]
    shadow = true
    reconcile_seconds = 600

A file named brain-shadow-off in the role directory switches the running shadow off within a
second; removing it switches it back on. Turning it off changes nothing for Peter's memory.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import veil_brain as base

SCHEMA_VERSION = 2
TRUE = base.TRUE
MEMORY_FIELDS = (
    'id', 'bucket', 'file_rel', 'title', 'name', 'body', 'body_sha256', 'raw_markdown',
    'raw_sha256', 'frontmatter_raw', 'frontmatter_json', 'created', 'last_active', 'type',
    'importance', 'valence', 'arousal', 'domain_json', 'tags_json', 'why_remembered', 'meaning',
    'source_tool', 'triggered_by', 'is_hidden', 'is_tombstone', 'is_deleted', 'erasure_mode',
    'deleted_at', 'pinned', 'protected', 'anchor', 'dont_surface', 'digested', 'resolved',
    'first_of_kind', 'extra_json',
)
FLAGS = ('pinned', 'protected', 'anchor', 'dont_surface', 'digested', 'resolved', 'first_of_kind',
         'tombstone')
KNOWN_ACTIONS = ('feel-delete', 'i-delete')
ID_IN_NAME = re.compile(r'(feel_\d{12}_V\d{3}(?:_\d+)?|[0-9a-f]{12})\.md$')
HOLD_ID = re.compile(r'(?:新建|feel|合并|钉选)→([\w-]+)')
MIGRATIONS = (
    'ALTER TABLE memories ADD COLUMN removed_at TEXT',
    'ALTER TABLE memories ADD COLUMN synced_at TEXT',
    'CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY, value TEXT)',
    'CREATE TABLE IF NOT EXISTS sync_issues(seq INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, '
    'stage TEXT, code TEXT, detail TEXT)',
    'CREATE TABLE IF NOT EXISTS known_removed(memory_id TEXT PRIMARY KEY, reason TEXT, at TEXT)',
    'CREATE UNIQUE INDEX IF NOT EXISTS versions_source_unique ON versions(source_index)',
)


def now():
    return datetime.now().astimezone().isoformat()


def role_paths(store, role):
    root = Path(store.root) / role
    return {
        'root': root,
        'memory': root / 'memory',
        'versions': root / 'memory-versions.jsonl',
        'sidecar': root / 'memory-sidecar.jsonl',
        'events': root / 'events.jsonl',
        'simplify_log': root / 'memory-simplify-log.jsonl',
        'db': root / 'brain.db',
        'report': root / 'brain-sync-report.md',
        'issues': root / 'brain-sync-log.jsonl',
        'off': root / 'brain-shadow-off',
    }


def extract_ids(name, args, result):
    """Memory IDs touched by one confirmed Ombre write; unknown shapes yield nothing."""
    if name == 'trace' and isinstance(args, dict):
        memory_id = args.get('bucket_id')
        return [memory_id] if isinstance(memory_id, str) and memory_id else []
    if name == 'hold' and isinstance(result, str):
        match = HOLD_ID.search(result)
        if match:
            return [match.group(1)]
    return []


def known_removed_ids(path):
    """IDs deleted by ticket 426 (feel/I), read from its migration log; never guessed."""
    found = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get('action') not in KNOWN_ACTIONS or row.get('result') != 'deleted':
            continue
        target = Path(str(row.get('target') or '')).name
        match = ID_IN_NAME.search(target)
        if match:
            found[match.group(1)] = 'ticket 426 removed this %s record' % row['action'].split('-')[0]
    return found


def build_row(bucket, file_rel, raw):
    header, body = base.split_frontmatter(raw)
    meta, lists, dicts = base.parse_header(header)
    flags = {key: 1 if str(meta.get(key, '')).lower() in TRUE else 0 for key in FLAGS}
    extra = {key: value for key, value in meta.items()
             if key not in base.SCALARS and key not in FLAGS and key not in ('domain', 'tags')}
    values = {
        'id': str(meta.get('id') or '').strip(),
        'bucket': bucket,
        'file_rel': file_rel,
        'title': meta.get('title'),
        'name': meta.get('name'),
        'body': body,
        'body_sha256': base.sha256_text(body),
        'raw_markdown': raw,
        'raw_sha256': base.sha256_text(raw),
        'frontmatter_raw': ''.join(header),
        'frontmatter_json': base.canonical_json({'meta': meta, 'lists': lists, 'dicts': dicts}),
        'created': meta.get('created'),
        'last_active': meta.get('last_active'),
        'type': meta.get('type'),
        'importance': _int_or_none(meta.get('importance')),
        'valence': _float_or_none(meta.get('valence')),
        'arousal': _float_or_none(meta.get('arousal')),
        'domain_json': base.canonical_json(lists.get('domain', [])),
        'tags_json': base.canonical_json(lists.get('tags', [])),
        'why_remembered': meta.get('why_remembered'),
        'meaning': meta.get('meaning'),
        'source_tool': meta.get('source_tool'),
        'triggered_by': meta.get('triggered_by'),
        'is_hidden': 1 if str(meta.get('dont_surface', '')).lower() in TRUE else 0,
        'is_tombstone': flags.get('tombstone', 0),
        'is_deleted': 1 if str(meta.get('deleted_at', '')).strip() else 0,
        'erasure_mode': meta.get('erasure_mode'),
        'deleted_at': meta.get('deleted_at'),
        'pinned': flags.get('pinned', 0),
        'protected': flags.get('protected', 0),
        'anchor': flags.get('anchor', 0),
        'dont_surface': flags.get('dont_surface', 0),
        'digested': flags.get('digested', 0),
        'resolved': flags.get('resolved', 0),
        'first_of_kind': flags.get('first_of_kind', 0),
        'extra_json': base.canonical_json(extra),
    }
    return values, dicts


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _read_text(path):
    with path.open(encoding='utf-8', newline='') as handle:
        return handle.read()


class ShadowSync:
    """One background worker per role; every public method swallows its own failures."""

    def __init__(self, cfg, store, settings=None, *, db_path=None, force=False, start=True):
        self.cfg = cfg
        self.role = cfg.id
        self.settings = dict(settings or {})
        self.paths = role_paths(store, self.role)
        self.db_path = Path(db_path) if db_path else self.paths['db']
        if not self.db_path.is_file():
            raise FileNotFoundError(str(self.db_path))
        self.interval = max(30, int(self.settings.get('reconcile_seconds', 600)))
        self.queue_limit = max(16, int(self.settings.get('queue_limit', 256)))
        self._queue = queue.Queue(maxsize=self.queue_limit)
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._busy = False
        self._lock = threading.Lock()
        self._enabled = bool(force) or not self.paths['off'].exists()
        self._need_reconcile = False
        self._last_reconcile = 0.0
        self._last_summary = None
        self._file_index = {}
        self._overflow = 0
        self._scan_errors = []
        self._issue_lock = threading.Lock()
        self.migrate()
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._loop, name='brain-shadow', daemon=True)
            self._thread.start()

    # --- lifecycle --------------------------------------------------------
    def close(self):
        self._stop.set()
        thread = getattr(self, '_thread', None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def wait_idle(self, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._idle.is_set() and self._queue.empty():
                return True
            time.sleep(0.01)
        return False

    # --- isolation: enqueue only -----------------------------------------
    def note_write(self, name, args, result):
        """Called after a confirmed Ombre write. Never raises, never touches the disk."""
        try:
            if not self._enabled:
                return
            for memory_id in extract_ids(name, args, result):
                try:
                    self._queue.put_nowait(memory_id)
                except queue.Full:
                    # Counting is in-memory only: a full outbox must not make the chat thread
                    # perform file IO. The worker logs the overflow and reconciles everything.
                    self._overflow = getattr(self, '_overflow', 0) + 1
                    self._need_reconcile = True
        except Exception:
            # Ombre already confirmed the write; bookkeeping trouble must never surface here.
            self._overflow = getattr(self, '_overflow', 0) + 1
    def record_issue(self, stage, code, detail=''):
        row = {'time': now(), 'stage': str(stage), 'code': str(code), 'detail': str(detail)[:300]}
        try:
            with self._issue_lock:
                self.paths['issues'].parent.mkdir(parents=True, exist_ok=True)
                with self.paths['issues'].open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        except Exception:
            pass

    # --- database ---------------------------------------------------------
    def _open(self):
        connection = sqlite3.connect(str(self.db_path), timeout=1.0)
        connection.execute('PRAGMA foreign_keys=OFF')
        return connection

    def migrate(self):
        connection = self._open()
        try:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(memories)')}
            for statement in MIGRATIONS:
                if 'ADD COLUMN' in statement:
                    name = statement.split('ADD COLUMN ')[1].split()[0]
                    if name in columns:
                        continue
                try:
                    connection.execute(statement)
                except sqlite3.OperationalError as exc:
                    lowered = str(exc).lower()
                    if 'duplicate column' not in lowered and 'already exists' not in lowered:
                        raise
            connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)",
                               (str(SCHEMA_VERSION),))
            self._refresh_digest(connection)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _refresh_digest(connection):
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('digest', ?)",
                           (base.canonical_digest(connection),))

    def _state(self, connection, key, default=None):
        row = connection.execute('SELECT value FROM sync_state WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def _set_state(self, connection, key, value):
        connection.execute('INSERT OR REPLACE INTO sync_state(key, value) VALUES(?,?)',
                           (key, str(value)))

    # --- worker -----------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._refresh_enabled()
                if not self._enabled:
                    time.sleep(1)
                    continue
                if getattr(self, '_overflow', 0):
                    dropped = self._overflow
                    self._overflow = 0
                    self.record_issue('queue', 'overflow',
                                      'dropped=%d; next reconcile catches up' % dropped)
                memory_id = None
                try:
                    memory_id = self._queue.get(timeout=1)
                except queue.Empty:
                    pass
                batch = set()
                if memory_id:
                    batch.add(memory_id)
                    while True:
                        try:
                            batch.add(self._queue.get_nowait())
                        except queue.Empty:
                            break
                if batch:
                    self._apply_batch(batch)
                if (self._need_reconcile or
                        time.monotonic() - self._last_reconcile >= self.interval):
                    self.reconcile()
            except Exception as exc:
                self.record_issue('worker', type(exc).__name__, str(exc))
                time.sleep(1)
    def _refresh_enabled(self):
        self._enabled = not self.paths['off'].exists()

    def _apply_batch(self, ids):
        self._busy = True
        self._idle.clear()
        connection = None
        try:
            connection = self._open()
            self._sync_versions(connection)
            self._sync_sidecar_rows(connection)
            failures = set()
            for memory_id in ids:
                try:
                    self.sync_one(connection, memory_id)
                except Exception as exc:
                    failures.add(memory_id)
                    self.record_issue('sync_one', type(exc).__name__, str(memory_id))
            connection.commit()
            self._refresh_digest(connection)
            connection.commit()
            if failures:
                # Do not spin on a broken row; the next reconcile will retry it wholesale.
                self._need_reconcile = True
        except Exception as exc:
            self.record_issue('batch', type(exc).__name__, str(exc))
            for memory_id in ids:
                try:
                    self._queue.put_nowait(memory_id)
                except queue.Full:
                    self._need_reconcile = True
            time.sleep(0.5)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            self._busy = False
            self._idle.set()

    # --- finding and applying one memory ----------------------------------
    def _find_file(self, memory_id):
        if not isinstance(memory_id, str) or not memory_id:
            return None
        cached = self._file_index.get(memory_id)
        if cached is not None and cached[1].is_file():
            return cached
        if cached is not None:
            self._file_index.pop(memory_id, None)
        memory_root = self.paths['memory']
        for bucket in base.BUCKETS:
            folder = memory_root / bucket
            if not folder.is_dir():
                continue
            for path in folder.rglob('*.md'):
                stem = path.stem
                if stem == memory_id or stem.endswith('_' + memory_id):
                    return bucket, path, path.relative_to(memory_root).as_posix()
        return None

    def sync_one(self, connection, memory_id):
        found = self._find_file(memory_id)
        timestamp = now()
        if found is None:
            row = connection.execute('SELECT removed_at FROM memories WHERE id=?',
                                     (memory_id,)).fetchone()
            if row is None:
                self.record_issue('sync_one', 'unknown-id', memory_id)
                return 'unknown'
            if row[0] is None:
                # A confirmed trace/delete that leaves no file is a real erase: mirror the
                # deletion promise by dropping the content instead of keeping a copy.
                self._remove_memory(connection, memory_id, timestamp)
            return 'removed'
        bucket, path, relative = found
        raw = _read_text(path)
        values, children = build_row(bucket, relative, raw)
        if values['id'] != memory_id:
            self.record_issue('sync_one', 'id-mismatch', '%s in %s' % (memory_id, relative))
            return 'mismatch'
        self._upsert_memory(connection, values, timestamp)
        self._replace_children(connection, memory_id, children)
        self._sync_vector(connection, memory_id)
        self._sync_sidecar_state(connection, [memory_id])
        return 'ok'

    def _remove_memory(self, connection, memory_id, timestamp):
        """Mirror a real deletion: keep an empty tombstone, drop every copy of the content.

        Ombre's erase promise covers body text, old versions and vectors, so the shadow must not
        keep them either. The ID and time stay for traceability and for the report's history.
        """
        connection.execute(
            "UPDATE memories SET removed_at=?, synced_at=?, title=NULL, name=NULL, body='', "
            "body_sha256='', raw_markdown='', raw_sha256='', frontmatter_raw='', "
            "frontmatter_json='{}', why_remembered=NULL, meaning=NULL, extra_json='{}', "
            'pinned=0, protected=0, anchor=0, digested=0 WHERE id=?',
            (timestamp, timestamp, memory_id))
        for table in ('versions', 'relations', 'sources', 'quotes', 'sidecar_state', 'vectors'):
            connection.execute('DELETE FROM %s WHERE memory_id=?' % table, (memory_id,))

    def _upsert_memory(self, connection, values, timestamp):
        columns = list(MEMORY_FIELDS) + ['removed_at', 'synced_at']
        placeholders = ','.join(['?'] * len(columns))
        updates = ', '.join('%s=excluded.%s' % (name, name)
                            for name in MEMORY_FIELDS if name != 'id')
        sql = ('INSERT INTO memories(%s) VALUES(%s) ON CONFLICT(id) DO UPDATE SET %s, '
               'removed_at=NULL, synced_at=excluded.synced_at'
               % (','.join(columns), placeholders, updates))
        row = [values[name] for name in MEMORY_FIELDS] + [None, timestamp]
        connection.execute(sql, row)

    def _replace_children(self, connection, memory_id, children):
        connection.execute('DELETE FROM relations WHERE memory_id=?', (memory_id,))
        connection.execute('DELETE FROM sources WHERE memory_id=?', (memory_id,))
        connection.execute('DELETE FROM quotes WHERE memory_id=?', (memory_id,))
        for index, link in enumerate(children.get('relation_links', [])):
            connection.execute(
                'INSERT INTO relations(memory_id, ord, target_id, type, score, status, auto, label) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (memory_id, index, link.get('target_bucket_id'), link.get('type'),
                 _float_or_none(link.get('score')), link.get('status'),
                 1 if str(link.get('auto', '')).lower() in TRUE else 0, link.get('label')))
        rows = []
        for link in children.get('source_links', []):
            rows.append(('source_link', link.get('ref'), link.get('status'), link.get('ranges')))
        for link in children.get('source_refs', []):
            rows.append(('source_ref', link.get('ref'), link.get('status'), link.get('ranges')))
        for index, (kind, ref, status, ranges) in enumerate(rows):
            connection.execute(
                'INSERT INTO sources(memory_id, ord, kind, ref, status, ranges_json) '
                'VALUES(?,?,?,?,?,?)',
                (memory_id, index, kind, ref, status, base.canonical_json(ranges)))
        for index, quote in enumerate(children.get('quotes', [])):
            connection.execute(
                'INSERT INTO quotes(memory_id, ord, speaker, text) VALUES(?,?,?,?)',
                (memory_id, index, quote.get('speaker'), quote.get('text')))

    def _sync_vector(self, connection, memory_id):
        db = self.paths['memory'] / 'embeddings.db'
        if not db.is_file():
            return
        found = None
        try:
            source = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
            try:
                columns = {row[1] for row in source.execute('PRAGMA table_info(embeddings)')}
                meaning = 'meaning_embedding' if 'meaning_embedding' in columns else 'NULL'
                found = source.execute(
                    'SELECT embedding, updated_at, content_hash, %s FROM embeddings WHERE bucket_id=?'
                    % meaning, (memory_id,)).fetchone()
            finally:
                source.close()
        except sqlite3.Error as exc:
            self.record_issue('vector', type(exc).__name__, memory_id)
            return
        if found is None:
            return
        vector, updated_at, content_hash, meaning_vector = found
        self._write_vector(connection, memory_id, vector, updated_at, content_hash, meaning_vector)

    def _write_vector(self, connection, memory_id, vector, updated_at, content_hash, meaning_vector):
        vector = vector.encode('latin-1') if isinstance(vector, str) else (vector or b'')
        meaning_vector = (meaning_vector.encode('latin-1')
                          if isinstance(meaning_vector, str) else meaning_vector)
        connection.execute(
            'INSERT INTO vectors(memory_id, dim, vector, vector_sha256, updated_at, content_hash, '
            'meaning_dim, meaning_vector, meaning_sha256) VALUES(?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(memory_id) DO UPDATE SET dim=excluded.dim, vector=excluded.vector, '
            'vector_sha256=excluded.vector_sha256, updated_at=excluded.updated_at, '
            'content_hash=excluded.content_hash, meaning_dim=excluded.meaning_dim, '
            'meaning_vector=excluded.meaning_vector, meaning_sha256=excluded.meaning_sha256',
            (memory_id, len(vector) // 4, vector, base.sha256_bytes(vector), updated_at,
             content_hash, (len(meaning_vector) // 4) if meaning_vector else None,
             meaning_vector or None, base.sha256_bytes(meaning_vector) if meaning_vector else None))

    def _sync_all_vectors(self, connection):
        db = self.paths['memory'] / 'embeddings.db'
        if not db.is_file():
            return 0
        current = {row[0]: tuple(row[1:]) for row in connection.execute(
            'SELECT memory_id, vector_sha256, meaning_sha256, dim, meaning_dim FROM vectors')}
        changed = 0
        source_ids = set()
        complete = False
        try:
            source = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
            try:
                columns = {row[1] for row in source.execute('PRAGMA table_info(embeddings)')}
                meaning = 'meaning_embedding' if 'meaning_embedding' in columns else 'NULL'
                for row in source.execute(
                        'SELECT bucket_id, embedding, updated_at, content_hash, %s FROM embeddings'
                        % meaning):
                    memory_id, vector, updated_at, content_hash, meaning_vector = row
                    source_ids.add(memory_id)
                    vector = vector.encode('latin-1') if isinstance(vector, str) else (vector or b'')
                    meaning_vector = (meaning_vector.encode('latin-1')
                                      if isinstance(meaning_vector, str) else meaning_vector)
                    expected = (base.sha256_bytes(vector),
                                base.sha256_bytes(meaning_vector) if meaning_vector else None,
                                len(vector) // 4,
                                (len(meaning_vector) // 4) if meaning_vector else None)
                    if current.get(memory_id) == expected:
                        continue
                    self._write_vector(connection, memory_id, vector, updated_at, content_hash,
                                       meaning_vector)
                    changed += 1
                complete = True
            finally:
                source.close()
        except sqlite3.Error as exc:
            self.record_issue('vectors', type(exc).__name__)
        if complete:
            # The source embeddings DB is the authority: a vector it no longer has must not
            # survive in the shadow either.
            for memory_id in set(current) - source_ids:
                connection.execute('DELETE FROM vectors WHERE memory_id=?', (memory_id,))
                changed += 1
        return changed
    def _sidecar_fold(self):
        rows = []
        for line in self._jsonlines(self.paths['sidecar']):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return base.fold_sidecar(rows)

    def _sync_sidecar_state(self, connection, ids=None):
        state = self._sidecar_fold()
        wanted = ids if ids is not None else list(state)
        for memory_id in wanted:
            item = state.get(memory_id)
            if item is None:
                continue
            connection.execute(
                'INSERT INTO sidecar_state(memory_id, locked, lan_edited, peter_after_lan, '
                'links_json, sources_json, merged_into) VALUES(?,?,?,?,?,?,?) '
                'ON CONFLICT(memory_id) DO UPDATE SET locked=excluded.locked, '
                'lan_edited=excluded.lan_edited, peter_after_lan=excluded.peter_after_lan, '
                'links_json=excluded.links_json, sources_json=excluded.sources_json, '
                'merged_into=excluded.merged_into',
                (memory_id, 1 if item['locked'] else 0, item['lan_edited'],
                 item['peter_after_lan'], base.canonical_json(item['links']),
                 base.canonical_json(item['sources']), item['merged_into']))

    # --- append-only ledgers ---------------------------------------------
    def _jsonlines(self, path):
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                rows.append(line)
        return rows

    @staticmethod
    def _ledger_fingerprint(lines):
        head = lines[0][:200] if lines else ''
        tail = lines[-1][-200:] if lines else ''
        return base.sha256_text(head + '\x00' + tail)

    def _sync_versions(self, connection):
        lines = self._jsonlines(self.paths['versions'])
        done = int(self._state(connection, 'versions_rows', '0') or 0)
        fingerprint = self._ledger_fingerprint(lines)
        stored = self._state(connection, 'versions_fingerprint')
        if done and (len(lines) < done or (len(lines) == done and stored != fingerprint)):
            # The ledger was rewritten (Memory.erase removes an erased memory's old text).
            connection.execute('DELETE FROM versions')
            done = 0
            self._set_state(connection, 'versions_rows', 0)
        self._set_state(connection, 'versions_fingerprint', fingerprint)
        if len(lines) <= done:
            return 0
        added = 0
        for index in range(done, len(lines)):
            try:
                row = json.loads(lines[index])
            except ValueError:
                self.record_issue('versions', 'bad-row', str(index))
                continue
            if not isinstance(row, dict):
                continue
            old = row.get('old_content')
            connection.execute(
                'INSERT INTO versions(seq, memory_id, time, operation_reason, why_remembered, '
                'fields_json, old_content, old_sha256, source_index) '
                'VALUES(NULL,?,?,?,?,?,?,?,?) ON CONFLICT(source_index) DO NOTHING',
                (row.get('memory_id'), row.get('time'), row.get('operation_reason'),
                 row.get('why_remembered'), base.canonical_json(row.get('fields')), old,
                 base.sha256_text(old) if isinstance(old, str) else None, index))
            added += 1
        self._set_state(connection, 'versions_rows', len(lines))
        return added

    @staticmethod
    def _ledger_fingerprint(lines):
        head = lines[0][:200] if lines else ''
        tail = lines[-1][-200:] if lines else ''
        return base.sha256_text(head + '\x00' + tail)
    def _sync_sidecar_rows(self, connection):
        lines = self._jsonlines(self.paths['sidecar'])
        done = int(self._state(connection, 'sidecar_rows', '0') or 0)
        rebuilt = False
        if done and len(lines) < done:
            # A shortened log was rewritten; every folded state is now stale.
            connection.execute('DELETE FROM sidecar_events')
            connection.execute('DELETE FROM sidecar_state')
            done = 0
            rebuilt = True
            self._set_state(connection, 'sidecar_rows', 0)
        if len(lines) <= done:
            return 0
        touched = set()
        for index in range(done, len(lines)):
            try:
                row = json.loads(lines[index])
            except ValueError:
                self.record_issue('sidecar', 'bad-row', str(index))
                continue
            if not isinstance(row, dict):
                continue
            connection.execute(
                'INSERT OR IGNORE INTO sidecar_events(seq, memory_id, kind, time, to_id, via, '
                'raw_json) VALUES(?,?,?,?,?,?,?)',
                (index, row.get('memory_id'), row.get('kind'), row.get('time'), row.get('to'),
                 row.get('via'), base.canonical_json(row)))
            if isinstance(row.get('memory_id'), str):
                touched.add(row['memory_id'])
            if row.get('kind') in ('link', 'unlink') and isinstance(row.get('to'), str):
                touched.add(row['to'])
        self._set_state(connection, 'sidecar_rows', len(lines))
        if rebuilt:
            self._sync_sidecar_state(connection)
        elif touched:
            self._sync_sidecar_state(connection, sorted(touched))
        return len(touched)
    def _sync_events(self, connection):
        lines = self._jsonlines(self.paths['events'])
        done = int(self._state(connection, 'events_rows', '0') or 0)
        if len(lines) <= done:
            return 0
        added = 0
        for index in range(done, len(lines)):
            try:
                row = json.loads(lines[index])
            except ValueError:
                self.record_issue('events', 'bad-row', str(index))
                continue
            if not isinstance(row, dict):
                continue
            event_id = str(row.get('event_id') or '').strip()
            if not event_id:
                continue
            sources = row.get('source_memory_ids')
            if isinstance(sources, str):
                try:
                    sources = json.loads(sources.replace("'", '"'))
                except ValueError:
                    self.record_issue('events', 'bad-sources', event_id)
                    sources = []
            sources = [str(item) for item in sources] if isinstance(sources, list) else []
            connection.execute(
                'INSERT INTO events(event_id, memory_id, source_count) VALUES(?,?,?) '
                'ON CONFLICT(event_id) DO UPDATE SET memory_id=excluded.memory_id, '
                'source_count=excluded.source_count',
                (event_id, row.get('memory_id'), len(sources)))
            connection.execute('DELETE FROM event_sources WHERE event_id=?', (event_id,))
            for ord_, source in enumerate(sources):
                connection.execute(
                    'INSERT INTO event_sources(event_id, ord, memory_id, part) VALUES(?,?,?,?)',
                    (event_id, ord_, source, None))
            added += 1
        self._set_state(connection, 'events_rows', len(lines))
        return added

    # --- reconcile --------------------------------------------------------
    def _scan_source(self, expected_buckets=()):
        """id -> parsed source record; an unreadable file makes the scan incomplete, not empty."""
        memory_root = self.paths['memory']
        self._scan_errors = []
        if not memory_root.is_dir():
            # Report the incomplete scan instead of aborting: a missing directory must never be
            # read as "everything was deleted", and the report still has to explain the state.
            self._scan_errors.append('memory directory missing: %s' % memory_root)
            self.record_issue('scan', 'missing-root', str(memory_root))
            self._file_index = {}
            return {}
        found = {}
        index = {}
        for bucket in base.BUCKETS:
            folder = memory_root / bucket
            if not folder.is_dir():
                if bucket in expected_buckets:
                    # A bucket that still holds shadow rows disappearing is an incomplete scan,
                    # never evidence that all of its memories were deleted.
                    self._scan_errors.append('bucket missing: %s' % folder)
                    self.record_issue('scan', 'missing-bucket', str(folder))
                continue
            for path in folder.rglob('*.md'):
                try:
                    raw = _read_text(path)
                except (OSError, UnicodeError) as exc:
                    self._scan_errors.append('%s: %s' % (path.name, type(exc).__name__))
                    self.record_issue('scan', type(exc).__name__, path.name)
                    continue
                values, children = build_row(bucket, path.relative_to(memory_root).as_posix(), raw)
                if not values['id']:
                    self.record_issue('scan', 'missing-id', path.name)
                    continue
                found[values['id']] = (values, children)
                index[values['id']] = (bucket, path, values['file_rel'])
        self._file_index = index
        return found
    def reconcile(self):
        if not self._enabled:
            return None
        connection = None
        try:
            connection = self._open()
            self._sync_versions(connection)
            self._sync_sidecar_rows(connection)
            self._sync_events(connection)
            expected_buckets = {row[0] for row in connection.execute(
                'SELECT DISTINCT bucket FROM memories WHERE removed_at IS NULL')}
            found = self._scan_source(expected_buckets)
            rows = {row[0]: (row[1], row[2], row[3], row[4]) for row in connection.execute(
                'SELECT id, bucket, file_rel, raw_sha256, removed_at FROM memories')}
            timestamp = now()
            changed = 0
            for memory_id, (values, children) in found.items():
                current = rows.get(memory_id)
                if (current is None or current[2] != values['raw_sha256'] or current[3]
                        or current[0] != values['bucket'] or current[1] != values['file_rel']):
                    self._upsert_memory(connection, values, timestamp)
                    self._replace_children(connection, memory_id, children)
                    self._sync_sidecar_state(connection, [memory_id])
                    changed += 1
            # A partial scan (unreadable file, missing bucket) must never be read as a deletion.
            if not self._scan_errors:
                for memory_id, (_, _, _, removed_at) in rows.items():
                    if memory_id not in found and removed_at is None:
                        self._remove_memory(connection, memory_id, timestamp)
                        changed += 1
            self._sync_all_vectors(connection)
            known = known_removed_ids(self.paths['simplify_log'])
            for memory_id, reason in known.items():
                connection.execute(
                    'INSERT OR REPLACE INTO known_removed(memory_id, reason, at) VALUES(?,?,?)',
                    (memory_id, reason, timestamp))
            references = self._reference_check(connection, found, known,
                                               complete=not self._scan_errors)
            comparison = self.compare(connection, found)
            self._refresh_digest(connection)
            connection.commit()
            self._last_reconcile = time.monotonic()
            self._need_reconcile = False
            summary = {'time': timestamp, 'changed': changed, 'source': len(found),
                       'known_removed': sorted(known), 'references': references,
                       'comparison': comparison, 'scan_incomplete': list(self._scan_errors)}
            self.write_report(connection, summary)
            return summary
        except Exception as exc:
            self.record_issue('reconcile', type(exc).__name__, str(exc))
            return None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _reference_check(self, connection, found, known, complete=True):
        live = set(found)
        event_missing = []
        link_missing = []
        for event_id, memory_id in connection.execute('SELECT event_id, memory_id FROM events'):
            if memory_id and memory_id not in live:
                event_missing.append((event_id, memory_id))
        for event_id, source in connection.execute(
                'SELECT event_id, memory_id FROM event_sources'):
            if source and source not in live:
                event_missing.append((event_id, source))
        for memory_id, links in connection.execute(
                'SELECT memory_id, links_json FROM sidecar_state'):
            for target in json.loads(links or '[]'):
                if isinstance(target, str) and target not in live:
                    link_missing.append((memory_id, target))
        removed = {row[0] for row in connection.execute(
            'SELECT id FROM memories WHERE removed_at IS NOT NULL')}
        if not complete:
            # An incomplete scan cannot tell "gone" from "not read"; report it separately.
            return {'known_event_refs': [item for item in event_missing if item[1] in known],
                    'known_link_refs': [item for item in link_missing if item[1] in known],
                    'removed_event_refs': [], 'removed_link_refs': [],
                    'unexpected_event_refs': [], 'unexpected_link_refs': [],
                    'unverified_refs': sorted(set(event_missing) | set(link_missing))}
        known_events = [item for item in event_missing if item[1] in known]
        known_links = [item for item in link_missing if item[1] in known]
        removed_events = [item for item in event_missing
                          if item[1] in removed and item[1] not in known]
        removed_links = [item for item in link_missing
                         if item[1] in removed and item[1] not in known]
        unexpected_events = [item for item in event_missing
                             if item[1] not in known and item[1] not in removed]
        unexpected_links = [item for item in link_missing
                            if item[1] not in known and item[1] not in removed]
        return {'known_event_refs': known_events, 'known_link_refs': known_links,
                'removed_event_refs': removed_events, 'removed_link_refs': removed_links,
                'unexpected_event_refs': unexpected_events,
                'unexpected_link_refs': unexpected_links}

    def compare(self, connection, found=None):
        """Per-item comparison, not just counts: bodies, state, versions, vectors, events."""
        found = self._scan_source() if found is None else found
        memory_mismatch = []
        for memory_id, (values, _) in found.items():
            row = connection.execute(
                'SELECT body_sha256, raw_sha256, bucket, title, created, importance, type, '
                'is_hidden, is_tombstone, is_deleted FROM memories WHERE id=?',
                (memory_id,)).fetchone()
            expected = (values['body_sha256'], values['raw_sha256'], values['bucket'],
                        values['title'], values['created'], values['importance'], values['type'],
                        values['is_hidden'], values['is_tombstone'], values['is_deleted'])
            if row is None or tuple(row) != expected:
                memory_mismatch.append(memory_id)
        version_expected = {}
        for index, line in enumerate(self._jsonlines(self.paths['versions'])):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            old = row.get('old_content')
            version_expected[index] = (row.get('memory_id'),
                                       base.sha256_text(old) if isinstance(old, str) else None)
        version_mismatch = []
        for index, (memory_id, digest) in version_expected.items():
            row = connection.execute(
                'SELECT memory_id, old_sha256 FROM versions WHERE source_index=?',
                (index,)).fetchone()
            if row is None or row[0] != memory_id or row[1] != digest:
                version_mismatch.append(index)
        state = self._sidecar_fold()
        state_mismatch = []
        for memory_id, item in state.items():
            row = connection.execute(
                'SELECT locked, links_json, sources_json, merged_into FROM sidecar_state '
                'WHERE memory_id=?', (memory_id,)).fetchone()
            expected = (1 if item['locked'] else 0, base.canonical_json(item['links']),
                        base.canonical_json(item['sources']), item['merged_into'])
            if row is None or tuple(row) != expected:
                state_mismatch.append(memory_id)
        vector_expected = self._source_vectors()
        vector_mismatch = []
        for memory_id, expected in vector_expected.items():
            row = connection.execute(
                'SELECT dim, vector_sha256, meaning_dim, meaning_sha256 FROM vectors '
                'WHERE memory_id=?', (memory_id,)).fetchone()
            if row is None or tuple(row) != expected:
                vector_mismatch.append(memory_id)
        expected_events = {}
        for line in self._jsonlines(self.paths['events']):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not row.get('event_id'):
                continue
            sources = row.get('source_memory_ids')
            if isinstance(sources, str):
                try:
                    sources = json.loads(sources.replace("'", '"'))
                except ValueError:
                    sources = []
            expected_events[str(row['event_id'])] = (row.get('memory_id'),
                                                     [str(item) for item in sources]
                                                     if isinstance(sources, list) else [])
        event_mismatch = []
        for event_id, (memory_id, sources) in expected_events.items():
            row = connection.execute('SELECT memory_id, source_count FROM events WHERE event_id=?',
                                     (event_id,)).fetchone()
            actual = [item[0] for item in connection.execute(
                'SELECT memory_id FROM event_sources WHERE event_id=? ORDER BY ord', (event_id,))]
            if row is None or row[0] != memory_id or row[1] != len(sources) or actual != sources:
                event_mismatch.append(event_id)
        actual_events = connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        actual_sources = connection.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0]
        return {
            'memories': len(found), 'memory_mismatches': memory_mismatch,
            'versions': len(version_expected), 'version_mismatches': version_mismatch,
            'states': len(state), 'state_mismatches': state_mismatch,
            'vectors': len(vector_expected), 'vector_mismatches': vector_mismatch,
            'events': {'expected': len(expected_events), 'actual': actual_events,
                       'expected_sources': sum(len(item[1]) for item in expected_events.values()),
                       'actual_sources': actual_sources,
                       'event_mismatches': event_mismatch},
        }
    def _source_vectors(self):
        db = self.paths['memory'] / 'embeddings.db'
        expected = {}
        if not db.is_file():
            return expected
        try:
            connection = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
            try:
                columns = {row[1] for row in connection.execute('PRAGMA table_info(embeddings)')}
                meaning = 'meaning_embedding' if 'meaning_embedding' in columns else 'NULL'
                for bucket_id, vector, meaning_vector in connection.execute(
                        'SELECT bucket_id, embedding, %s FROM embeddings' % meaning):
                    vector = vector.encode('latin-1') if isinstance(vector, str) else (vector or b'')
                    meaning_vector = (meaning_vector.encode('latin-1')
                                      if isinstance(meaning_vector, str) else meaning_vector)
                    expected[bucket_id] = (len(vector) // 4, base.sha256_bytes(vector),
                                           (len(meaning_vector) // 4) if meaning_vector else None,
                                           base.sha256_bytes(meaning_vector) if meaning_vector else None)
            finally:
                connection.close()
        except sqlite3.Error as exc:
            self.record_issue('vectors', type(exc).__name__)
        return expected
    def write_report(self, connection, summary):
        comparison = summary['comparison']
        references = summary['references']
        events = comparison['events']
        event_drift = list(events.get('event_mismatches', []))
        if events['expected'] != events['actual']:
            event_drift.append('events:count %d!=%d' % (events['expected'], events['actual']))
        if events['expected_sources'] != events['actual_sources']:
            event_drift.append('event_sources:count %d!=%d'
                               % (events['expected_sources'], events['actual_sources']))
        unexpected = (comparison['memory_mismatches'] + comparison['version_mismatches'] +
                      comparison['state_mismatches'] + comparison['vector_mismatches'] +
                      references['unexpected_event_refs'] + references['unexpected_link_refs'] +
                      event_drift)
        counts = {table: connection.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
                  for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state',
                                'vectors', 'events', 'event_sources')}
        removed = connection.execute(
            'SELECT COUNT(*) FROM memories WHERE removed_at IS NOT NULL').fetchone()[0]
        known = sorted(summary.get('known_removed') or [])
        lines = ['# VEIL Brain 影子同步报告', '',
                 '生成时间：' + summary['time'],
                 '模式：影子同步（只跟随，不反向写入）',
                 '数据库：' + str(self.db_path), '',
                 '## 结果', '',
                 '- 意外差异：' + ('无' if not unexpected else '%d 个' % len(unexpected)),
                 '- 本轮变更：%d 条' % summary.get('changed', 0), '',
                 '## 数量（影子库）', '']
        for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state', 'vectors',
                      'events', 'event_sources'):
            lines.append('- %s: %d' % (table, counts[table]))
        lines += ['- 已移除（removed_at 非空）：%d' % removed, '',
                  '## 逐项比较（影子库 vs 源）', '',
                  '- 记忆正文/状态：比较 %d，差异 %d' % (comparison['memories'],
                                                     len(comparison['memory_mismatches'])),
                  '- 历史版本：比较 %d，差异 %d' % (comparison['versions'],
                                                  len(comparison['version_mismatches'])),
                  '- sidecar 状态：比较 %d，差异 %d' % (comparison['states'],
                                                     len(comparison['state_mismatches'])),
                  '- 向量：比较 %d，差异 %d' % (comparison['vectors'],
                                             len(comparison['vector_mismatches'])),
                  '- 事件：%d 篇 / %d 个来源（源 %d / %d）' % (
                      counts['events'], counts['event_sources'],
                      comparison['events']['expected'], comparison['events']['expected_sources']),
                  '',
                  '## 已知历史引用（#426 删除 feel/I）', '',
                  '- 已知删除的记忆 id：%d 个' % len(known),
                  '- 事件来源引用：%d 条' % len(references['known_event_refs']),
                  '- sidecar 链接目标：%d 条' % len(references['known_link_refs']),
                  '- 说明：它们是 #426 删除 feel 记录后留下的历史引用，不是同步损坏。']
        if known:
            lines += ['- id 列表：' + ', '.join(known[:20])]
        if self._scan_errors:
            lines += ['', '## 扫描不完整', ''] + ['- ' + item for item in self._scan_errors[:20]]
            lines.append('- 本次未核对引用：%d 条' % len(references.get('unverified_refs', [])))
        lines += ['', '## 意外差异', '']
        if not unexpected:
            lines.append('- 无')
        else:
            lines += ['- ' + str(item) for item in unexpected[:50]]
        lines += ['', '## 最近问题（日志尾部）', '']
        tail = []
        try:
            tail = self.paths['issues'].read_text(encoding='utf-8').splitlines()[-10:]
        except OSError:
            pass
        lines += ['- ' + item[:300] for item in tail] or ['- 无']
        lines += ['', '## 边界', '',
                  '- Ombre 是唯一正式权威；影子库不反向修改记忆、不自动修复、不清理历史引用。',
                  '- 关闭方式：删除或创建 role 目录下的 brain-shadow-off（或运行 shadow off/on）。']
        text = '\n'.join(lines) + '\n'
        try:
            self.paths['report'].write_text(text, encoding='utf-8')
            (self.paths['report'].with_suffix('.json')).write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        except OSError as exc:
            self.record_issue('report', type(exc).__name__, str(exc))
        self._last_summary = summary
        return text


def attach(cfg, store):
    """Build the shadow worker when the role enabled it; never raise into Memory."""
    settings = (getattr(cfg, 'memory', {}) or {}).get('brain') or {}
    if not isinstance(settings, dict) or settings.get('shadow') is not True:
        return None
    if not (Path(store.root) / cfg.id / 'brain.db').is_file():
        # The database is seeded explicitly (brain_sync.py init). While it is missing the shadow
        # stays off with no side effect, so a half-set-up role and every test stay safe.
        return None
    try:
        return ShadowSync(cfg, store, settings)
    except Exception as exc:
        try:
            paths = role_paths(store, cfg.id)
            paths['issues'].parent.mkdir(parents=True, exist_ok=True)
            with paths['issues'].open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'time': now(), 'stage': 'attach',
                                         'code': type(exc).__name__, 'detail': str(exc)[:300]},
                                        ensure_ascii=False) + '\n')
        except Exception:
            pass
        return None


def _status(paths):
    db = paths['db']
    result = {'db': str(db), 'exists': db.is_file(),
              'shadow_off': paths['off'].exists(),
              'report': str(paths['report']),
              'report_exists': paths['report'].is_file()}
    if db.is_file():
        try:
            connection = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
            try:
                result['counts'] = {table: connection.execute(
                    'SELECT COUNT(*) FROM %s' % table).fetchone()[0]
                    for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state',
                                  'vectors', 'events', 'event_sources')}
                result['removed'] = connection.execute(
                    'SELECT COUNT(*) FROM memories WHERE removed_at IS NOT NULL').fetchone()[0]
            finally:
                connection.close()
        except sqlite3.Error as exc:
            result['error'] = type(exc).__name__
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='VEIL Brain P0-B shadow sync (Ombre stays the authority).')
    parser.add_argument('--role', default='peter')
    parser.add_argument('--data-root', default='data')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='seed brain.db from an isolated copy, then migrate')
    init.add_argument('--source', required=True)
    init.add_argument('--db')
    sub.add_parser('reconcile', help='full hash reconciliation and report')
    sub.add_parser('status', help='show shadow state without changing anything')
    shadow = sub.add_parser('shadow', help='switch the running shadow on or off')
    shadow.add_argument('action', choices=('on', 'off', 'status'))
    args = parser.parse_args(argv)

    data_root = Path(args.data_root)
    paths = role_paths(type('Store', (), {'root': data_root})(), args.role)
    if not paths['root'].is_dir():
        print('role directory not found: %s' % paths['root'], file=sys.stderr)
        return 2
    cfg = type('Cfg', (), {'id': args.role,
                           'memory': {'brain': {'shadow': True}}})()

    if args.command == 'init':
        db = Path(getattr(args, 'db', None) or paths['db'])
        if db.exists():
            print('database already exists, refusing to overwrite: %s' % db, file=sys.stderr)
            return 2
        importer = base.run_import(args.source, db)
        if importer.problems:
            print('\n'.join(importer.problems[:20]), file=sys.stderr)
            return 1
        shadow_sync = ShadowSync(cfg, type('Store', (), {'root': data_root})(),
                                 {'shadow': True}, db_path=db, force=True, start=False)
        shadow_sync.close()
        print(json.dumps({'db': str(db), 'counts': base.check_db(db)['counts']},
                         ensure_ascii=False, indent=2))
        return 0

    if args.command == 'shadow':
        if args.action == 'on':
            paths['off'].unlink(missing_ok=True)
            print('shadow on')
        elif args.action == 'off':
            paths['off'].touch()
            print('shadow off')
        else:
            print(json.dumps(_status(paths), ensure_ascii=False, indent=2))
        return 0

    if args.command == 'status':
        print(json.dumps(_status(paths), ensure_ascii=False, indent=2))
        return 0

    if args.command == 'reconcile':
        if paths['off'].exists():
            print('shadow is off (%s); run "shadow on" first' % paths['off'], file=sys.stderr)
            return 1
        store = type('Store', (), {'root': data_root})()
        sync = ShadowSync(cfg, store, {'shadow': True}, start=False)
        try:
            summary = sync.reconcile()
        finally:
            sync.close()
        if summary is None:
            print('reconcile failed; see %s' % paths['issues'], file=sys.stderr)
            return 1
        print(json.dumps({'changed': summary['changed'], 'source': summary['source'],
                          'known_removed': len(summary['known_removed']),
                          'unexpected': (len(summary['comparison']['memory_mismatches']) +
                                         len(summary['comparison']['version_mismatches']) +
                                         len(summary['comparison']['state_mismatches']) +
                                         len(summary['comparison']['vector_mismatches']) +
                                         len(summary['references']['unexpected_event_refs']) +
                                         len(summary['references']['unexpected_link_refs'])),
                          'report': str(paths['report'])}, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
