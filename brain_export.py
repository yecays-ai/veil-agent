"""P2-A: same-ID export from brain.db to an Ombre-readable vault.

Brain is the authority; this exporter only writes a rollback/archival copy. It never edits brain.db.
Memory files keep their original IDs and Markdown; versions/sidecar/events keep the ledgers Ombre
reads. Hard-deleted memories are exported only as minimal tombstones, never resurrected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import brain_events


def _json(value, default):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return default
    return value if value is not None else default


def _sha(value):
    data = value.encode('utf-8') if isinstance(value, str) else (value or b'')
    return hashlib.sha256(data).hexdigest()


def export_vault(db_path, out_dir, *, include_events=True):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect('file:%s?mode=ro' % Path(db_path), uri=True)
    try:
        memories = connection.execute(
            'SELECT id, file_rel, raw_markdown, body_sha256, is_hidden, is_tombstone, '
            'is_deleted, deleted_at FROM memories ORDER BY id').fetchall()
        for memory_id, file_rel, raw, body_sha, is_hidden, tombstone, deleted, deleted_at in memories:
            rel = Path(file_rel or ('dynamic/%s.md' % memory_id))
            target = out / 'memory' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('w', encoding='utf-8', newline='') as handle:
                handle.write(raw or '')
        # Ombre-compatible embeddings.db (raw float32 bytes in the TEXT column, as Ombre stores them)
        embeddings = out / 'memory' / 'embeddings.db'
        embeddings.parent.mkdir(parents=True, exist_ok=True)
        if embeddings.exists():
            embeddings.unlink()
        emb = sqlite3.connect(embeddings)
        try:
            emb.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT NOT NULL, '
                        'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT \'\', '
                        'meaning_embedding TEXT)')
            for memory_id, dim, vector, vector_sha, updated_at, content_hash, meaning in connection.execute(
                    'SELECT memory_id, dim, vector, vector_sha256, updated_at, content_hash, meaning_vector '
                    'FROM vectors'):
                emb.execute('INSERT OR REPLACE INTO embeddings(bucket_id, embedding, updated_at, content_hash, '
                            'meaning_embedding) VALUES(?,?,?,?,?)',
                            (memory_id, vector, updated_at or '', content_hash or '', meaning))
            emb.commit()
        finally:
            emb.close()
        # version/sidecar/journal ledgers in the formats Ombre's VEIL integration already reads
        versions = []
        for row in connection.execute('SELECT memory_id, time, operation_reason, why_remembered, '
                                      'fields_json, old_content FROM versions ORDER BY seq'):
            versions.append({'memory_id': row[0], 'time': row[1], 'operation_reason': row[2],
                             'why_remembered': row[3], 'fields': _json(row[4], []),
                             'old_content': row[5]})
        _write_jsonl(out / 'memory-versions.jsonl', versions)
        sidecar = []
        for row in connection.execute('SELECT memory_id, kind, time, to_id, via, raw_json '
                                      'FROM sidecar_events ORDER BY seq'):
            raw = _json(row[5], None)
            if isinstance(raw, dict):
                item = dict(raw)
            else:
                item = {'memory_id': row[0], 'kind': row[1]}
            item.setdefault('memory_id', row[0])
            item.setdefault('kind', row[1])
            sidecar.append(item)
        _write_jsonl(out / 'memory-sidecar.jsonl', sidecar)
        tombstones = []
        try:
            for row in connection.execute('SELECT memory_id, deleted_at, reason_hash FROM tombstones '
                                          'ORDER BY memory_id'):
                tombstones.append({'memory_id': row[0], 'deleted_at': row[1],
                                   'reason_hash': row[2]})
        except sqlite3.Error:
            tombstones = []
        _write_jsonl(out / 'brain-tombstones.jsonl', tombstones)
        events = brain_events.export_jsonl(connection, out) if include_events else {'events': 0, 'stages': 0}
        manifest = {'memories': len(memories), 'versions': len(versions),
                    'sidecar': len(sidecar), 'tombstones': len(tombstones),
                    'events': events['events'], 'stages': events['stages'],
                    'event_ledger': events.get('ledger', {}),
                    'event_state': bool(events.get('state')),
                    'body_sha256': {row[0]: row[3] for row in memories}}
        (out / 'brain-export-manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
        return manifest
    finally:
        connection.close()


def _write_jsonl(path, rows):
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(export_vault(args.db, args.out), ensure_ascii=False))


if __name__ == '__main__':
    main()
