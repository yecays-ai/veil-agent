"""Ticket 428 / P0-A: VEIL Brain's own memory store, built from an isolated copy.

This module only reads a copied memory tree and writes a new SQLite file plus a readable
snapshot and a reconciliation report. It never touches the live role data and is not wired
into Peter's write path; P0-B (shadow sync) is out of scope.

Layout expected under --source (an isolated copy):

    <source>/memory/**/*.md        Ombre bucket files
    <source>/memory/embeddings.db  bucket id -> vector (TEXT column holding raw float32 bytes)
    <source>/memory-versions.jsonl version history written by VEIL
    <source>/memory-sidecar.jsonl  VEIL's own lock/link/source/merge notes
    <source>/events.jsonl          event ledger (mapping only; stories are NOT copied)
    <source>/event-stages.jsonl    optional; identity only, reserved for later phases

Commands:

    python veil_brain.py import --source DIR --db FILE [--snapshot DIR] [--report FILE]
    python veil_brain.py check --db FILE
    python veil_brain.py inspect --db FILE [--id ID] [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
BUCKETS = ('dynamic', 'permanent', 'feel', 'letters', 'plans', 'archive')
TOP_KEY = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$')
SCALARS = {
    'id', 'title', 'name', 'created', 'last_active', 'type', 'importance', 'valence', 'arousal',
    'why_remembered', 'meaning', 'source_tool', 'triggered_by', 'erasure_mode', 'deleted_at',
}
FLAGS = ('pinned', 'protected', 'anchor', 'dont_surface', 'digested', 'resolved', 'first_of_kind',
         'tombstone')
TRUE = {'true', 'yes', '1'}


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1].replace("''", "'")
    return value


def split_frontmatter(text):
    """(raw header lines, body). The body is exactly what followed the closing marker."""
    if text.startswith('\ufeff'):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        return [], text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            end = index
            break
    if end is None:
        return [], text
    return lines[1:end], ''.join(lines[end + 1:])


def parse_header(header_lines):
    """Top-level scalars, string lists and the nested list-of-dicts fields we need.

    Everything not understood is still preserved verbatim in frontmatter_raw; this parser only
    feeds the queryable columns. Shapes follow what Ombre writes.
    """
    meta, lists = {}, {}
    dicts = {'relation_links': [], 'source_links': [], 'source_refs': [], 'quotes': []}
    index = 0
    while index < len(header_lines):
        line = header_lines[index].rstrip('\r\n')
        match = TOP_KEY.match(line)
        index += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value:
            meta[key] = _unquote(value)
            continue
        block = []
        while index < len(header_lines):
            nxt = header_lines[index].rstrip('\r\n')
            if nxt and not nxt[:1].isspace() and not nxt.startswith('- '):
                break
            block.append(nxt)
            index += 1
        if any(item.strip().startswith('- ') for item in block):
            items = []
            for item in block:
                stripped = item.strip()
                if stripped.startswith('- '):
                    items.append(stripped[2:].strip())
                elif items and stripped:
                    items[-1] = items[-1] + '\n' + stripped
            if key in dicts:
                dicts[key] = _parse_dict_items(items)
            else:
                lists[key] = [_unquote(item) for item in items
                              if item and '\n' not in item]
    return meta, lists, dicts


def _parse_dict_items(items):
    out = []
    for item in items:
        if '\n' not in item and ':' not in item:
            continue
        first, _, rest = item.partition(':')
        row = {first.strip(): _unquote(rest)} if rest else {first.strip(): ''}
        for line in item.split('\n')[1:]:
            key, sep, value = line.partition(':')
            if sep:
                row[key.strip()] = _unquote(value)
        out.append(row)
    return out


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def manifest(root):
    """path -> sha256 for every regular file under root; proves the source did not change."""
    root = Path(root)
    rows = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            rows[path.relative_to(root).as_posix()] = sha256_bytes(path.read_bytes())
    return rows


def fold_sidecar(rows):
    """Same folding rules as memory_sidecar.Sidecar.all(), kept explicit here."""
    out = {}
    for row in rows:
        memory_id, kind = row.get('memory_id'), row.get('kind')
        if not isinstance(memory_id, str) or not isinstance(kind, str):
            continue
        item = out.setdefault(memory_id, {'locked': False, 'lan_edited': None,
                                          'peter_after_lan': None, 'links': [], 'sources': [],
                                          'merged_into': None})
        when = row.get('time')
        if kind == 'lock':
            item['locked'] = True
        elif kind == 'unlock':
            item['locked'] = False
        elif kind == 'lan_edit':
            item['lan_edited'], item['peter_after_lan'] = when, None
        elif kind == 'peter_after_lan':
            item['peter_after_lan'] = when
        elif kind == 'seen':
            item['peter_after_lan'] = None
        elif kind == 'link' and isinstance(row.get('to'), str) and row['to'] not in item['links']:
            item['links'].append(row['to'])
        elif kind == 'unlink':
            item['links'] = [key for key in item['links'] if key != row.get('to')]
        elif kind == 'source':
            item['sources'] = [*item['sources'],
                               {'time': when, 'via': row.get('via', '')}][-20:]
        elif kind == 'merged_into':
            item['merged_into'] = row.get('to') if isinstance(row.get('to'), str) else None
        elif kind == 'unarchived':
            item['merged_into'] = None
    return out


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE memories(
    id TEXT PRIMARY KEY, bucket TEXT NOT NULL, file_rel TEXT NOT NULL,
    title TEXT, name TEXT, body TEXT NOT NULL, body_sha256 TEXT NOT NULL,
    raw_markdown TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
    frontmatter_raw TEXT NOT NULL, frontmatter_json TEXT NOT NULL,
    created TEXT, last_active TEXT, type TEXT, importance INTEGER, valence REAL, arousal REAL,
    domain_json TEXT, tags_json TEXT,
    why_remembered TEXT, meaning TEXT, source_tool TEXT, triggered_by TEXT,
    is_hidden INTEGER NOT NULL DEFAULT 0, is_tombstone INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    erasure_mode TEXT, deleted_at TEXT, pinned INTEGER, protected INTEGER, anchor INTEGER,
    dont_surface INTEGER, digested INTEGER, resolved INTEGER, first_of_kind INTEGER,
    extra_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE versions(
    seq INTEGER PRIMARY KEY, memory_id TEXT NOT NULL, time TEXT, operation_reason TEXT,
    why_remembered TEXT, fields_json TEXT, old_content TEXT, old_sha256 TEXT, source_index INTEGER);
CREATE INDEX versions_memory ON versions(memory_id);
CREATE TABLE sidecar_events(
    seq INTEGER PRIMARY KEY, memory_id TEXT, kind TEXT, time TEXT, to_id TEXT, via TEXT,
    raw_json TEXT);
CREATE INDEX sidecar_memory ON sidecar_events(memory_id);
CREATE TABLE sidecar_state(
    memory_id TEXT PRIMARY KEY, locked INTEGER NOT NULL DEFAULT 0, lan_edited TEXT,
    peter_after_lan TEXT, links_json TEXT NOT NULL, sources_json TEXT NOT NULL, merged_into TEXT);
CREATE TABLE relations(
    memory_id TEXT NOT NULL, ord INTEGER NOT NULL, target_id TEXT, type TEXT, score REAL,
    status TEXT, auto INTEGER, label TEXT, PRIMARY KEY(memory_id, ord));
CREATE TABLE sources(
    memory_id TEXT NOT NULL, ord INTEGER NOT NULL, kind TEXT, ref TEXT, status TEXT,
    ranges_json TEXT, PRIMARY KEY(memory_id, ord));
CREATE TABLE quotes(
    memory_id TEXT NOT NULL, ord INTEGER NOT NULL, speaker TEXT, text TEXT,
    PRIMARY KEY(memory_id, ord));
CREATE TABLE vectors(
    memory_id TEXT PRIMARY KEY, dim INTEGER NOT NULL, vector BLOB NOT NULL,
    vector_sha256 TEXT NOT NULL, updated_at TEXT, content_hash TEXT, meaning_dim INTEGER,
    meaning_vector BLOB, meaning_sha256 TEXT);
CREATE TABLE events(event_id TEXT PRIMARY KEY, memory_id TEXT, source_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE event_sources(event_id TEXT NOT NULL, ord INTEGER NOT NULL, memory_id TEXT, part TEXT,
    PRIMARY KEY(event_id, ord));
CREATE TABLE event_stages(stage_id TEXT PRIMARY KEY, event_id TEXT, at TEXT, state TEXT);
"""

MEMORY_COLUMNS = 36


class BrainImport:
    def __init__(self, source, db_path, snapshot=None, report=None):
        self.source = Path(source).resolve()
        self.db_path = Path(db_path).resolve()
        self.snapshot = Path(snapshot).resolve() if snapshot else None
        self.report = Path(report).resolve() if report else None
        self.rows = []
        self.versions = []
        self.sidecar = []
        self.sidecar_state = {}
        self.event_rows = []
        self.vector_rows = []
        self.relations = 0
        self.sources = 0
        self.quotes = 0
        self.vectors = 0
        self.events = 0
        self.event_sources = 0
        self.warnings = []
        self.errors = []
        self.problems = []
        self.counts = {}
        self.dangling = {}
        self.digest = ''

    # --- reading the copy -------------------------------------------------
    def read_memories(self):
        memory_root = self.source / 'memory'
        for path in sorted(memory_root.rglob('*.md')):
            rel = path.relative_to(memory_root).as_posix()
            bucket = rel.split('/', 1)[0]
            if bucket not in BUCKETS:
                continue
            with path.open(encoding='utf-8', newline='') as handle:
                raw = handle.read()
            header, body = split_frontmatter(raw)
            meta, lists, dicts = parse_header(header)
            memory_id = str(meta.get('id') or '').strip()
            if not memory_id:
                self.errors.append('missing id: ' + rel)
                continue
            self.rows.append({'bucket': bucket, 'file_rel': rel, 'raw': raw,
                              'header': ''.join(header), 'meta': meta, 'lists': lists,
                              'dicts': dicts, 'body': body, 'memory_id': memory_id})

    def read_versions(self):
        path = self.source / 'memory-versions.jsonl'
        if not path.exists():
            self.warnings.append('memory-versions.jsonl missing')
            return
        for index, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                self.errors.append('bad version row %d' % index)
                continue
            if isinstance(row, dict):
                self.versions.append({'row': row, 'index': index})

    def read_sidecar(self):
        path = self.source / 'memory-sidecar.jsonl'
        if not path.exists():
            self.warnings.append('memory-sidecar.jsonl missing')
            return
        for index, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                self.errors.append('bad sidecar row %d' % index)
                continue
            if isinstance(row, dict):
                self.sidecar.append({'row': row, 'index': index})
        self.sidecar_state = fold_sidecar([item['row'] for item in self.sidecar])

    def read_events(self):
        path = self.source / 'events.jsonl'
        if not path.exists():
            self.warnings.append('events.jsonl missing')
            return
        # events.jsonl is append-only: later rows update an existing event. Fold by event_id
        # (last row wins) before inserting, otherwise the live log's update rows break the
        # legacy events primary key. brain_events.import_events already upserts the same way.
        folded = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
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
                    sources = []
            sources = [str(item) for item in sources] if isinstance(sources, list) else []
            folded[event_id] = {'event_id': event_id, 'memory_id': row.get('memory_id'),
                                'sources': list(dict.fromkeys(sources))}
        self.event_rows = list(folded.values())
        self.events = len(self.event_rows)
        self.event_sources = sum(len(row['sources']) for row in self.event_rows)

    def read_vectors(self):
        path = self.source / 'memory' / 'embeddings.db'
        if not path.exists():
            self.warnings.append('embeddings.db missing')
            return
        connection = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'").fetchone()
            if not table:
                self.warnings.append('embeddings table missing')
                return
            columns = {row[1] for row in connection.execute('PRAGMA table_info(embeddings)')}
            meaning = 'meaning_embedding' if 'meaning_embedding' in columns else 'NULL'
            for row in connection.execute(
                    'SELECT bucket_id, embedding, updated_at, content_hash, %s FROM embeddings' % meaning):
                bucket_id, vector, updated_at, content_hash = row[:4]
                meaning_vector = row[4]
                if isinstance(vector, str):
                    vector = vector.encode('latin-1')
                if isinstance(meaning_vector, str):
                    meaning_vector = meaning_vector.encode('latin-1')
                self.vector_rows.append({'memory_id': bucket_id, 'vector': vector,
                                         'updated_at': updated_at, 'content_hash': content_hash,
                                         'meaning': meaning_vector})
                self.vectors += 1
        finally:
            connection.close()

    # --- writing ----------------------------------------------------------
    def open_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path))
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.executescript(SCHEMA)
        return connection

    def import_all(self):
        self.read_memories()
        self.read_versions()
        self.read_sidecar()
        self.read_events()
        self.read_vectors()
        if self.errors:
            return False
        connection = self.open_db()
        try:
            self._insert(connection)
            self.digest = self._canonical_digest(connection)
            connection.execute('INSERT INTO meta(key, value) VALUES(?,?)', ('digest', self.digest))
            connection.execute('INSERT INTO meta(key, value) VALUES(?,?)', ('schema', str(SCHEMA_VERSION)))
            connection.commit()
        finally:
            connection.close()
        return True

    def _insert(self, connection):
        placeholders = ','.join(['?'] * MEMORY_COLUMNS)
        for row in self.rows:
            meta, lists, dicts = row['meta'], row['lists'], row['dicts']
            memory_id = row['memory_id']
            flags = {key: 1 if str(meta.get(key, '')).lower() in TRUE else 0 for key in FLAGS}
            extra = {key: value for key, value in meta.items()
                     if key not in SCALARS and key not in FLAGS and key not in ('domain', 'tags')}
            values = (memory_id, row['bucket'], row['file_rel'], meta.get('title'),
                      meta.get('name'), row['body'], sha256_text(row['body']),
                      row['raw'], sha256_text(row['raw']), row['header'],
                      canonical_json({'meta': meta, 'lists': lists, 'dicts': dicts}),
                      meta.get('created'), meta.get('last_active'), meta.get('type'),
                      _int_or_none(meta.get('importance')), _float_or_none(meta.get('valence')),
                      _float_or_none(meta.get('arousal')),
                      canonical_json(lists.get('domain', [])), canonical_json(lists.get('tags', [])),
                      meta.get('why_remembered'), meta.get('meaning'), meta.get('source_tool'),
                      meta.get('triggered_by'),
                      1 if str(meta.get('dont_surface', '')).lower() in TRUE else 0,
                      flags.get('tombstone', 0),
                      1 if str(meta.get('deleted_at', '')).strip() else 0,
                      meta.get('erasure_mode'), meta.get('deleted_at'),
                      flags.get('pinned', 0), flags.get('protected', 0), flags.get('anchor', 0),
                      flags.get('dont_surface', 0), flags.get('digested', 0),
                      flags.get('resolved', 0), flags.get('first_of_kind', 0),
                      canonical_json(extra))
            if len(values) != MEMORY_COLUMNS:
                raise ValueError('memory column count drifted: %d' % len(values))
            connection.execute('INSERT INTO memories VALUES(%s)' % placeholders, values)
            for ord_, link in enumerate(dicts.get('relation_links', [])):
                connection.execute(
                    'INSERT INTO relations(memory_id, ord, target_id, type, score, status, auto, label) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (memory_id, ord_, link.get('target_bucket_id'), link.get('type'),
                     _float_or_none(link.get('score')), link.get('status'),
                     1 if str(link.get('auto', '')).lower() in TRUE else 0, link.get('label')))
                self.relations += 1
            source_rows = []
            for link in dicts.get('source_links', []):
                source_rows.append(('source_link', link.get('ref'), link.get('status'),
                                    link.get('ranges')))
            for link in dicts.get('source_refs', []):
                source_rows.append(('source_ref', link.get('ref'), link.get('status'),
                                    link.get('ranges')))
            if meta.get('triggered_by'):
                source_rows.append(('triggered_by', meta.get('triggered_by'), 'active', None))
            for ord_, (kind, ref, status, ranges) in enumerate(source_rows):
                connection.execute(
                    'INSERT INTO sources(memory_id, ord, kind, ref, status, ranges_json) '
                    'VALUES(?,?,?,?,?,?)',
                    (memory_id, ord_, kind, ref, status, canonical_json(ranges)))
                self.sources += 1
            for ord_, quote in enumerate(dicts.get('quotes', [])):
                connection.execute(
                    'INSERT INTO quotes(memory_id, ord, speaker, text) VALUES(?,?,?,?)',
                    (memory_id, ord_, quote.get('speaker'), quote.get('text')))
                self.quotes += 1
        for item in self.versions:
            row = item['row']
            old = row.get('old_content')
            connection.execute(
                'INSERT INTO versions(seq, memory_id, time, operation_reason, why_remembered, '
                'fields_json, old_content, old_sha256, source_index) VALUES(?,?,?,?,?,?,?,?,?)',
                (None, row.get('memory_id'), row.get('time'), row.get('operation_reason'),
                 row.get('why_remembered'), canonical_json(row.get('fields')),
                 old, sha256_text(old) if isinstance(old, str) else None, item['index']))
        for item in self.sidecar:
            row = item['row']
            connection.execute(
                'INSERT INTO sidecar_events(seq, memory_id, kind, time, to_id, via, raw_json) '
                'VALUES(?,?,?,?,?,?,?)',
                (item['index'], row.get('memory_id'), row.get('kind'), row.get('time'),
                 row.get('to'), row.get('via'), canonical_json(row)))
        for memory_id, state in self.sidecar_state.items():
            connection.execute(
                'INSERT INTO sidecar_state(memory_id, locked, lan_edited, peter_after_lan, '
                'links_json, sources_json, merged_into) VALUES(?,?,?,?,?,?,?)',
                (memory_id, 1 if state['locked'] else 0, state['lan_edited'],
                 state['peter_after_lan'], canonical_json(state['links']),
                 canonical_json(state['sources']), state['merged_into']))
        for row in self.vector_rows:
            vector = row['vector'] or b''
            meaning = row['meaning'] or b''
            connection.execute(
                'INSERT INTO vectors(memory_id, dim, vector, vector_sha256, updated_at, '
                'content_hash, meaning_dim, meaning_vector, meaning_sha256) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (row['memory_id'], len(vector) // 4, vector, sha256_bytes(vector),
                 row['updated_at'], row['content_hash'],
                 (len(meaning) // 4) if meaning else None, meaning or None,
                 sha256_bytes(meaning) if meaning else None))
        for row in self.event_rows:
            connection.execute('INSERT INTO events(event_id, memory_id, source_count) VALUES(?,?,?)',
                               (row['event_id'], row['memory_id'], len(row['sources'])))
            for ord_, source in enumerate(row['sources']):
                connection.execute(
                    'INSERT INTO event_sources(event_id, ord, memory_id, part) VALUES(?,?,?,?)',
                    (row['event_id'], ord_, source, None))

    @staticmethod
    def _canonical_digest(connection):
        parts = []
        for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state', 'relations',
                      'sources', 'quotes', 'vectors', 'events', 'event_sources', 'event_stages'):
            columns = [row[1] for row in connection.execute('PRAGMA table_info(%s)' % table)]
            rows = connection.execute('SELECT * FROM %s ORDER BY %s' %
                                      (table, ', '.join(columns))).fetchall()
            parts.append([table, columns, rows])
        blob = json.dumps(parts, ensure_ascii=False, default=_json_default,
                          separators=(',', ':')).encode('utf-8')
        return sha256_bytes(blob)

    # --- verification -----------------------------------------------------
    def verify(self, connection, source_manifest_before):
        problems = []
        counts = {}
        for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state', 'relations',
                      'sources', 'quotes', 'vectors', 'events', 'event_sources'):
            counts[table] = connection.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
        expected = {'memories': len(self.rows), 'versions': len(self.versions),
                    'sidecar_events': len(self.sidecar),
                    'sidecar_state': len(self.sidecar_state),
                    'relations': self.relations, 'sources': self.sources, 'quotes': self.quotes,
                    'vectors': self.vectors, 'events': self.events,
                    'event_sources': self.event_sources}
        for table, wanted in expected.items():
            if counts[table] != wanted:
                problems.append('%s: stored %d, expected %d' % (table, counts[table], wanted))
        for row in self.rows:
            stored = connection.execute(
                'SELECT body_sha256, raw_sha256, body FROM memories WHERE id=?',
                (row['memory_id'],)).fetchone()
            if stored is None:
                problems.append('missing row ' + row['memory_id'])
                continue
            if stored[0] != sha256_text(row['body']) or stored[2] != row['body']:
                problems.append('body mismatch ' + row['memory_id'])
            if stored[1] != sha256_text(row['raw']):
                problems.append('raw mismatch ' + row['memory_id'])
        for item in self.versions:
            row = item['row']
            stored = connection.execute(
                'SELECT old_content, old_sha256 FROM versions WHERE source_index=?',
                (item['index'],)).fetchone()
            if stored is None:
                problems.append('missing version row %d' % item['index'])
            elif stored[0] != row.get('old_content'):
                problems.append('version content mismatch %d' % item['index'])
            elif stored[0] is not None and stored[1] != sha256_text(stored[0]):
                problems.append('version hash mismatch %d' % item['index'])
        for row in self.vector_rows:
            stored = connection.execute(
                'SELECT dim, vector, vector_sha256 FROM vectors WHERE memory_id=?',
                (row['memory_id'],)).fetchone()
            if stored is None:
                problems.append('missing vector ' + row['memory_id'])
            elif stored[1] != row['vector'] or stored[2] != sha256_bytes(row['vector'] or b''):
                problems.append('vector mismatch ' + row['memory_id'])
            elif stored[0] != (len(row['vector'] or b'') // 4):
                problems.append('vector dimension mismatch ' + row['memory_id'])
        after = manifest(self.source)
        if after != source_manifest_before:
            problems.append('source tree changed during import')
        ids = {row['memory_id'] for row in self.rows}
        missing_sources = []
        for row in self.event_rows:
            if row['memory_id'] and row['memory_id'] not in ids:
                problems.append('event %s points at missing memory %s' %
                                (row['event_id'], row['memory_id']))
            for source in row['sources']:
                if source not in ids:
                    missing_sources.append(source)
        self.dangling = {
            'versions': sorted({item['row'].get('memory_id') for item in self.versions} - ids),
            # None ids cannot be compared with str ids; filter them before sorting.
            'sidecar_events': sorted({item['row'].get('memory_id') for item in self.sidecar}
                                     - ids),
            'vectors': sorted({row['memory_id'] for row in self.vector_rows} - ids),
            # Sources deleted from the library on purpose (ticket 426 removed feel). Keep the
            # mapping and name them in the report instead of silently dropping the link.
            'event_sources': sorted(set(missing_sources)),
        }
        self.problems = problems
        self.counts = counts
        return problems

    def write_snapshot(self):
        if not self.snapshot:
            return
        self.snapshot.mkdir(parents=True, exist_ok=True)
        index_lines = ['# VEIL Brain 快照', '',
                       '来源：' + str(self.source), '条数：%d' % len(self.rows), '']
        for row in self.rows:
            rel = row['file_rel']
            target = self.snapshot / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('w', encoding='utf-8', newline='') as handle:
                handle.write(row['raw'])
            meta = row['meta']
            flags = [name for name in ('tombstone', 'pinned', 'protected', 'anchor',
                                       'dont_surface', 'digested', 'resolved')
                     if str(meta.get(name, '')).lower() in TRUE]
            if meta.get('deleted_at'):
                flags.append('deleted')
            versions = sum(1 for item in self.versions
                           if item['row'].get('memory_id') == row['memory_id'])
            state = self.sidecar_state.get(row['memory_id']) or {}
            line = ('- ' + row['memory_id'] + ' · ' + str(meta.get('created', ''))[:10] +
                    ' · ' + str(meta.get('title') or meta.get('name') or '(无标题)') +
                    ' · bucket=' + row['bucket'] +
                    ' · 重要度=' + str(meta.get('importance', '')) +
                    ' · 版本=' + str(versions) +
                    ' · 状态=' + (','.join(flags) or '正常'))
            if state.get('locked'):
                line += ' · 锁定'
            if state.get('merged_into'):
                line += ' · 已合并到 ' + str(state.get('merged_into'))
            index_lines.append(line)
        (self.snapshot / '_INDEX.md').write_text('\n'.join(index_lines) + '\n', encoding='utf-8')

    def write_report(self):
        if not self.report:
            return
        lines = ['# VEIL Brain P0-A 导入对账报告', '',
                 '生成时间：' + datetime.now().astimezone().isoformat(), '',
                 '## 数量', '']
        for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state', 'relations',
                      'sources', 'quotes', 'vectors', 'events', 'event_sources'):
            lines.append('- %s: %d' % (table, self.counts.get(table, 0)))
        dims, shared = {}, 0
        if self.db_path.exists():
            connection = sqlite3.connect('file:%s?mode=ro' % self.db_path, uri=True)
            try:
                dims = dict(connection.execute('SELECT dim, COUNT(*) FROM vectors GROUP BY dim'))
                shared = connection.execute(
                    'SELECT COUNT(*) FROM (SELECT memory_id FROM event_sources GROUP BY memory_id '
                    'HAVING COUNT(DISTINCT event_id) > 1)').fetchone()[0]
            finally:
                connection.close()
        lines += ['', '## 向量与事件映射', '',
                  '- 维度分布：' + json.dumps(dims, ensure_ascii=False),
                  '- 事件映射：%d 篇；被多篇事件共享的来源：%d 条' % (self.events, shared), '',
                  '## 悬空引用（保留但不属于当前记忆）', '']
        for name in ('versions', 'sidecar_events', 'vectors', 'event_sources'):
            values = self.dangling.get(name, [])
            lines.append('- %s: %d 个' % (name, len(values)) +
                         ('（' + ', '.join(str(item) for item in values[:5]) + '）' if values else ''))
        lines += ['', '## 问题', '']
        lines += ['- ' + item for item in self.problems] or ['- 无']
        lines += ['', '## 摘要', '',
                  '- 结果：' + ('通过' if not self.problems else '有 %d 个问题' % len(self.problems)),
                  '- 内容摘要（去重指纹）：' + self.digest]
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def canonical_digest(connection):
    """Deterministic fingerprint of every stored row; used by import and by check."""
    parts = []
    for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state', 'relations',
                  'sources', 'quotes', 'vectors', 'events', 'event_sources', 'event_stages'):
        columns = [row[1] for row in connection.execute('PRAGMA table_info(%s)' % table)]
        rows = connection.execute('SELECT * FROM %s ORDER BY %s' %
                                  (table, ', '.join(columns))).fetchall()
        parts.append([table, columns, rows])
    blob = json.dumps(parts, ensure_ascii=False, default=_json_default,
                      separators=(',', ':')).encode('utf-8')
    return sha256_bytes(blob)


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


def _json_default(value):
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    return str(value)


def run_import(source, db_path, snapshot=None, report=None):
    source = Path(source).resolve()
    if not (source / 'memory').is_dir():
        raise ValueError('source has no memory/ directory: %s' % source)
    for label, value in (('db', db_path), ('snapshot', snapshot), ('report', report)):
        if value is None:
            continue
        target = Path(value).resolve()
        if target == source or source in target.parents:
            raise ValueError('%s path must stay outside --source: %s' % (label, target))
    importer = BrainImport(source, db_path, snapshot, report)
    before = manifest(importer.source)
    if not importer.import_all():
        importer.problems = list(importer.errors)
        importer.write_report()
        return importer
    connection = sqlite3.connect(str(importer.db_path))
    try:
        events_path = importer.source / 'events.jsonl'
        stages_path = importer.source / 'event-stages.jsonl'
        if events_path.is_file() and stages_path.is_file():
            import brain_events
            brain_events.import_events(connection, events_path, stages_path)
        importer.verify(connection, before)
        # P2-A: the digest must cover the full event payload/stages imported above, otherwise
        # check_db reports digest_ok=false on a freshly migrated database and backups look corrupt.
        importer.digest = canonical_digest(connection)
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('digest', ?)",
                           (importer.digest,))
        connection.commit()
    finally:
        connection.close()
    importer.write_snapshot()
    importer.write_report()
    return importer


def check_db(db_path):
    """Open a Brain db anywhere and re-verify internal integrity and the stored digest."""
    db_path = Path(db_path)
    connection = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    try:
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        schema = connection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        digest = connection.execute("SELECT value FROM meta WHERE key='digest'").fetchone()
        counts = {table: connection.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
                  for table in ('memories', 'versions', 'sidecar_events', 'sidecar_state',
                                'vectors', 'events', 'event_sources')}
        dims = dict(connection.execute('SELECT dim, COUNT(*) FROM vectors GROUP BY dim'))
        actual = canonical_digest(connection)
    finally:
        connection.close()
    stored = digest[0] if digest else None
    return {'integrity': integrity, 'schema': schema[0] if schema else None,
            'digest': stored, 'digest_ok': stored == actual, 'counts': counts, 'dims': dims}


def inspect(db_path, memory_id=None, limit=10):
    connection = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if memory_id:
            row = connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            if row is None:
                return '没有 ' + memory_id
            lines = ['# ' + str(row['title'] or row['name'] or row['id']),
                     '- id: %s  bucket: %s  type: %s' % (row['id'], row['bucket'], row['type']),
                     '- created: %s  importance: %s' % (row['created'], row['importance']),
                     '- flags: tombstone=%s deleted=%s hidden=%s pinned=%s protected=%s' % (
                         row['is_tombstone'], row['is_deleted'], row['is_hidden'],
                         row['pinned'], row['protected']),
                     '- body sha256: ' + str(row['body_sha256']), '']
            versions = connection.execute(
                'SELECT seq, time, operation_reason, old_sha256 FROM versions WHERE memory_id=? '
                'ORDER BY source_index', (memory_id,)).fetchall()
            lines.append('## 版本 (%d)' % len(versions))
            for item in versions:
                lines.append('- %s · %s · %s' % (item['time'], item['operation_reason'],
                                                 str(item['old_sha256'])[:12]))
            state = connection.execute('SELECT * FROM sidecar_state WHERE memory_id=?',
                                       (memory_id,)).fetchone()
            if state:
                lines += ['', '## Sidecar',
                          '- locked: %s  merged_into: %s' % (state['locked'], state['merged_into']),
                          '- links: ' + state['links_json'],
                          '- sources: ' + state['sources_json']]
            links = connection.execute('SELECT * FROM relations WHERE memory_id=? ORDER BY ord',
                                       (memory_id,)).fetchall()
            if links:
                lines += ['', '## 关系 (%d)' % len(links)]
                lines += ['- %s -> %s (%s)' % (link['type'], link['target_id'], link['score'])
                          for link in links]
            vector = connection.execute('SELECT dim, vector_sha256 FROM vectors WHERE memory_id=?',
                                        (memory_id,)).fetchone()
            lines += ['', '## 向量：%s 维 %s' % (
                vector['dim'] if vector else '无',
                str(vector['vector_sha256'])[:12] if vector else '')]
            return '\n'.join(lines)
        rows = connection.execute(
            'SELECT id, title, created, importance, is_tombstone FROM memories '
            'ORDER BY created DESC, id LIMIT ?', (limit,)).fetchall()
        return '\n'.join('%s · %s · %s%s' % (row['id'], row['created'] or '',
                                              row['title'] or '',
                                              ' · 墓碑' if row['is_tombstone'] else '')
                         for row in rows)
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='VEIL Brain P0-A: isolated import and checks.')
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='read an isolated copy and build brain.db')
    imp.add_argument('--source', required=True)
    imp.add_argument('--db', required=True)
    imp.add_argument('--snapshot')
    imp.add_argument('--report')
    chk = sub.add_parser('check', help='open a brain.db anywhere and verify it')
    chk.add_argument('--db', required=True)
    ins = sub.add_parser('inspect', help='read memories from a brain.db')
    ins.add_argument('--db', required=True)
    ins.add_argument('--id')
    ins.add_argument('--limit', type=int, default=10)
    args = parser.parse_args(argv)
    if args.command == 'import':
        importer = run_import(args.source, args.db, args.snapshot, args.report)
        if importer.problems:
            print('\n'.join(importer.problems[:20]), file=sys.stderr)
            return 1
        print(json.dumps(check_db(args.db), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'check':
        result = check_db(args.db)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['integrity'] == 'ok' and result['digest_ok'] else 1
    print(inspect(args.db, args.id, args.limit))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
