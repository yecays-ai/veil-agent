"""P2-A: same-ID export stays Ombre-readable and keeps deletion tombstones."""
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from veil.brain.storage import brain_export
from veil.brain.storage import veil_brain


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        memory = source / 'memory' / 'dynamic'
        memory.mkdir(parents=True)
        (memory / 'm1.md').write_text('---\nid: m1\ntitle: One\n---\nbody one\n', encoding='utf-8')
        connection = sqlite3.connect(str(source / 'memory' / 'embeddings.db'))
        connection.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT NOT NULL, '
                           'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT "", meaning_embedding TEXT)')
        connection.execute('INSERT INTO embeddings VALUES (?,?,?,?,?)',
                           ('m1', struct.pack('<1024f', *([0.1] * 1024)), 't', '', None))
        connection.commit(); connection.close()
        (source / 'memory-versions.jsonl').write_text(json.dumps(
            {'memory_id': 'm1', 'time': 't', 'operation_reason': 'edit', 'why_remembered': '',
             'fields': ['content'], 'old_content': 'old'}) + '\n', encoding='utf-8')
        (source / 'memory-sidecar.jsonl').write_text(json.dumps(
            {'memory_id': 'm1', 'kind': 'link', 'to': 'm0'}) + '\n', encoding='utf-8')
        self.db = self.root / 'brain.db'
        veil_brain.run_import(source, self.db)
        c = sqlite3.connect(self.db)
        c.execute('CREATE TABLE tombstones(memory_id TEXT PRIMARY KEY, deleted_at TEXT, reason_hash TEXT)')
        c.execute('INSERT INTO tombstones VALUES (?,?,?)', ('gone', '2026-11-24', 'h'))
        c.commit(); c.close()

    def test_export_has_same_ids_and_minimal_tombstone(self):
        out = self.root / 'export'
        manifest = brain_export.export_vault(self.db, out)
        self.assertEqual(manifest['memories'], 1)
        self.assertEqual(manifest['versions'], 1)
        self.assertEqual(manifest['sidecar'], 1)
        self.assertEqual(manifest['tombstones'], 1)
        self.assertTrue((out / 'memory' / 'dynamic' / 'm1.md').is_file())
        gone = json.loads((out / 'brain-tombstones.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(gone['memory_id'], 'gone')
        self.assertNotIn('body', gone)
        emb = sqlite3.connect(str(out / 'memory' / 'embeddings.db'))
        self.assertEqual(emb.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0], 1)
        emb.close()
        reimported = self.root / 'reimport.db'
        result = veil_brain.run_import(out, reimported)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(result.versions), 1)
        source = sqlite3.connect(str(self.db))
        copy = sqlite3.connect(str(reimported))
        try:
            left = source.execute('SELECT id,title,body,importance,type,body_sha256 FROM memories').fetchall()
            right = copy.execute('SELECT id,title,body,importance,type,body_sha256 FROM memories').fetchall()
        finally:
            source.close()
            copy.close()
        self.assertEqual(left, right)


if __name__ == '__main__':
    unittest.main()
