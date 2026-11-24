"""Ticket 430 / P1: Brain retrieval over a fictional brain.db. No model, no network."""
import json
from decimal import Decimal
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import brain_retrieval
import veil_brain
from memory_agent import Memory, ordinary_hits


def frontmatter(**fields):
    lines = ['---']
    for key, value in fields.items():
        lines.append('%s: %s' % (key, value))
    lines.append('---')
    return '\n'.join(lines) + '\n'


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        vector = [0.0] * 1024
        vector[0] = 1.0 if '颜色' in text or 'blue' in text.lower() else 0.2
        vector[1] = 1.0 if '吵架' in text else 0.0
        return vector


class BrainRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        memory = self.source / 'memory'
        (memory / 'dynamic').mkdir(parents=True)
        (memory / 'archive').mkdir(parents=True)
        rows = {
            'blue': frontmatter(id='blue', title='Blue favorite color', importance='8',
                                created="'2026-10-19T10:00:00+08:00'", type='dynamic')
                    + 'Lan 喜欢蓝色，她问我知不知道她最喜欢的颜色。\n',
            'fight': frontmatter(id='fight', title='A fight about memory', importance='6',
                                 created="'2026-10-20T10:00:00+08:00'", type='dynamic')
                     + '我们因为记忆系统吵了一架。\n',
            'dead': frontmatter(id='dead', title='Tombstoned', tombstone='true',
                                deleted_at="'2026-11-01T10:00:00+08:00'", type='dynamic')
                    + '被删除的墓碑记忆。\n',
            'hidden': frontmatter(id='hidden', title='Hidden one', dont_surface='true',
                                  type='dynamic') + '隐藏的记忆。\n',
        }
        for key, text in rows.items():
            (memory / 'dynamic' / (key + '.md')).write_text(text, encoding='utf-8')
        (memory / 'archive' / 'old.md').write_text(
            frontmatter(id='old', title='Archived one', type='archived') + '归档的记忆。\n',
            encoding='utf-8')
        connection = sqlite3.connect(str(memory / 'embeddings.db'))
        try:
            connection.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT NOT NULL, '
                               'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT \'\', '
                               'meaning_embedding TEXT)')
            def vector(first, second=0.0):
                values = [0.0] * 1024
                values[0], values[1] = first, second
                return struct.pack('<1024f', *values)
            for memory_id, blob in (('blue', vector(1.0)), ('fight', vector(0.9, 1.0)),
                                    ('dead', vector(1.0)), ('hidden', vector(1.0)),
                                    ('old', vector(1.0))):
                connection.execute('INSERT INTO embeddings(bucket_id, embedding, updated_at, content_hash) '
                                   'VALUES(?,?,?,?)', (memory_id, blob, '2026-10-19T10:00:00+08:00', 'x'))
            connection.commit()
        finally:
            connection.close()
        self.db = self.root / 'brain.db'
        self.importer = veil_brain.run_import(self.source, self.db)
        self.assertEqual(self.importer.problems, [])
        self.embedder = FakeEmbedder()

    def search(self, query, limit=8):
        text, meta = brain_retrieval.search_text(self.db, query, limit=limit,
                                                 embedder=self.embedder)
        return text, meta

    def test_vector_and_keyword_rank_the_right_memory(self):
        text, meta = self.search('她最喜欢的颜色')
        self.assertEqual(meta['ids'][0], 'blue')
        text, meta = self.search('我们因为记忆吵架')
        self.assertEqual(meta['ids'][0], 'fight')

    def test_plain_embed_function_is_accepted(self):
        # P2-B: BrainAuthority's writer boundary passes embedder.embed, so retrieval must accept
        # either the provider object or the bare function.
        text, meta = brain_retrieval.search_text(self.db, '她最喜欢的颜色',
                                                embedder=self.embedder.embed)
        self.assertEqual(meta['ids'][0], 'blue')
        self.assertIn('[bucket_id:blue]', text)

    def test_hidden_tombstone_and_archived_never_enter_results(self):
        text, _ = self.search('蓝色 吵架 墓碑 隐藏 归档 记忆')
        self.assertIn('[bucket_id:blue]', text)
        self.assertIn('[bucket_id:fight]', text)
        for forbidden in ('dead', 'hidden', 'old'):
            self.assertNotIn('[bucket_id:%s]' % forbidden, text)

    def test_render_keeps_the_ombre_contract(self):
        text, meta = self.search('颜色')
        parsed = ordinary_hits(text)
        self.assertEqual([key for key, _ in parsed], meta['ids'])
        self.assertTrue(text.endswith('\n```'))
        json.loads(text.split('=== ombre:result-ids ===')[1].split('```json')[1].split('```')[0])

    def test_index_refreshes_when_the_database_changes(self):
        first, _ = self.search('颜色')
        self.assertNotIn('[bucket_id:new]', first)
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute(
                "INSERT INTO memories(id,bucket,file_rel,title,name,body,body_sha256,raw_markdown,"
                "raw_sha256,frontmatter_raw,frontmatter_json,created,type,importance,"
                "is_hidden,is_tombstone,is_deleted,dont_surface) "
                "VALUES('new','dynamic','dynamic/new.md','New colour','', '颜色 新的记忆', '', '', '', '', '', "
                "'2026-11-20T10:00:00+08:00','dynamic',9,0,0,0,0)")
            connection.commit()
        finally:
            connection.close()
        second, _ = self.search('颜色')
        self.assertIn('[bucket_id:new]', second)

    def test_drift_section_is_present_when_nothing_matches(self):
        class ZeroEmbedder:
            def embed(self, _text):
                return [0.0] * 1024
        text, meta = brain_retrieval.search_text(self.db, 'zzqq', limit=8,
                                                 embedder=ZeroEmbedder())
        self.assertEqual(meta['ids'], [])
        self.assertIn(brain_retrieval.DRIFT_MARK, text)
        drift = text.split(brain_retrieval.DRIFT_MARK, 1)[1].split('=== ombre:result-ids ===')[0]
        self.assertRegex(drift, r'\[bucket_id:[a-z0-9]+\]')
        self.assertNotIn('[bucket_id:dead]', drift)
        self.assertNotIn('[bucket_id:hidden]', drift)

    def test_brain_embedding_goes_through_the_budget_lifecycle(self):
        import shutil
        target = self.root / 'peter' / 'brain.db'
        target.parent.mkdir(parents=True)
        shutil.copyfile(self.db, target)

        class FakeBudget:
            def __init__(self):
                self.calls = []
            def estimate(self, *args):
                self.calls.append(('estimate', args))
                return ('token', Decimal('1'))
            def settle_estimate(self, reservation, **kwargs):
                self.calls.append(('settle', kwargs))

        fake = SimpleNamespace(
            retrieval_settings={'backend': 'brain', 'weight_vector': 0.65, 'weight_keyword': 0.35},
            store=SimpleNamespace(root=self.root), cfg=SimpleNamespace(
                id='peter', memory={'model': 'm', 'input_cny_per_million': '1'}),
            last_recall_debug={}, embedder=lambda: self.embedder, budget=FakeBudget(),
            call=lambda name, args: 'OMBRE')
        text = Memory.search_candidates(fake, '颜色', 5)
        self.assertIn('[bucket_id:blue]', text)
        kinds = [call[0] for call in fake.budget.calls]
        self.assertEqual(kinds, ['estimate', 'settle'])
        self.assertEqual(fake.last_recall_debug['retrieval']['backend'], 'brain')

    def test_backend_brain_falls_back_to_ombre_and_ombre_is_default(self):
        fake = SimpleNamespace(
            retrieval_settings={'backend': 'brain', 'weight_vector': 0.65, 'weight_keyword': 0.35},
            store=SimpleNamespace(root=self.root),
            cfg=SimpleNamespace(id='peter'),
            last_recall_debug={},
            embedder=lambda: self.embedder,
            calls=[])

        def call(name, args):
            fake.calls.append((name, args))
            return 'FALLBACK'
        fake.call = call
        self.assertEqual(Memory.search_candidates(fake, '颜色', 5), 'FALLBACK')
        self.assertEqual(fake.last_recall_debug['retrieval'], {'backend': 'brain',
                                                               'fallback': 'FileNotFoundError'})
        self.assertEqual(fake.calls[0][0], 'breath_search')

        ombre = SimpleNamespace(
            retrieval_settings={'backend': 'ombre', 'weight_vector': 0.65, 'weight_keyword': 0.35},
            store=SimpleNamespace(root=self.root), cfg=SimpleNamespace(id='peter'),
            last_recall_debug={}, calls=[])
        ombre.call = lambda name, args: ombre.calls.append((name, args)) or 'OMBRE'
        self.assertEqual(Memory.search_candidates(ombre, '颜色', 5), 'OMBRE')
        self.assertEqual(ombre.calls[0][0], 'breath_search')


if __name__ == '__main__':
    unittest.main()
