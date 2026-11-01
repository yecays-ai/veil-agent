"""Ticket 187: context-assisted recall keeps only candidates close to the recent rounds."""
import array
import sqlite3
import tempfile
import unittest
import unittest.mock
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from memory_agent import Memory, vault_path
from recall_relevance import context_text, cosine, decode_vector, load_vectors
from test_memory import Backend
from veil_agent import Budget, Character, Store


class FakeEmbedder:
    def __init__(self, vector=(1.0, 0.0), fail=False):
        self.vector, self.fail, self.texts = list(vector), fail, []

    def embed(self, text):
        self.texts.append(text)
        if self.fail:
            raise RuntimeError('fictional embedding failure')
        return self.vector


RECENT = [{'role': 'user', 'content': 'talk about the garden'}, {'role': 'assistant', 'content': 'roses bloom'}]


class AssistedRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.backend = Backend(self.store.root / 'fake' / 'memory' / 'dynamic')
        self.backend.rows = {'garden': {'content': 'GARDEN_MEMORY'}, 'taxes': {'content': 'TAXES_MEMORY'}}
        for key in self.backend.rows:
            self.backend.write_row(key)
        db = vault_path(self.store.root, 'fake') / 'embeddings.db'
        conn = sqlite3.connect(db)
        conn.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT NOT NULL, '
                     'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT "")')
        conn.execute('INSERT INTO embeddings VALUES (?,?,?,?)',
                     ('garden', array.array('f', [0.9, 0.1]).tobytes(), 'x', ''))
        conn.execute('INSERT INTO embeddings VALUES (?,?,?,?)', ('taxes', '[0.0, 1.0]', 'x', ''))
        conn.commit()
        conn.close()

    def memory(self, recall_context=None):
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        settings = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                    'base_url': 'https://example.invalid/v1'}
        if recall_context is not None:
            settings['recall_context'] = recall_context
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake', settings)
        memory = Memory(cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    def text(self, memory, query, recent=RECENT):
        return '\n'.join(row['content'] for row in memory.context(query, include_opening=False, recent=recent))

    def enabled(self, **extra):
        return {'enabled': True, 'min_similarity': 0.5, **extra}

    def test_off_by_default_and_without_a_calibrated_threshold(self):
        for setting in (None, {'enabled': True}, {'enabled': False, 'min_similarity': 0.5}):
            memory = self.memory(setting)
            memory._embedder = FakeEmbedder()
            text = self.text(memory, 'try again')
            self.assertIn('GARDEN_MEMORY', text)
            self.assertIn('TAXES_MEMORY', text)  # today's recall: everything the search returned
            self.assertEqual(memory._embedder.texts, [])

    def test_short_message_keeps_only_close_memories(self):
        memory = self.memory(self.enabled())
        memory._embedder = FakeEmbedder((1.0, 0.0))
        text = self.text(memory, 'try again')
        self.assertIn('GARDEN_MEMORY', text)
        self.assertNotIn('TAXES_MEMORY', text)
        self.assertIn('talk about the garden', memory._embedder.texts[0])
        self.assertIn('try again', memory._embedder.texts[0])
        self.assertEqual(memory.last_assisted['kept'], ['garden'])

    def test_nothing_close_means_no_long_term_memory_at_all(self):
        memory = self.memory(self.enabled(min_similarity=0.99))
        memory._embedder = FakeEmbedder((0.6, 0.6))
        text = self.text(memory, 'ok')
        self.assertNotIn('GARDEN_MEMORY', text)
        self.assertNotIn('TAXES_MEMORY', text)
        self.assertIn('No relevant memories', text)

    def test_long_message_and_missing_history_use_todays_recall(self):
        memory = self.memory(self.enabled())
        memory._embedder = FakeEmbedder((1.0, 0.0))
        long_text = self.text(memory, 'this message is plainly longer than twelve characters')
        self.assertIn('TAXES_MEMORY', long_text)
        self.assertIn('TAXES_MEMORY', self.text(memory, 'try again', recent=[]))
        self.assertEqual(memory._embedder.texts, [])

    def test_embedding_failure_keeps_todays_recall(self):
        memory = self.memory(self.enabled())
        memory._embedder = FakeEmbedder(fail=True)
        text = self.text(memory, 'try again')
        self.assertIn('GARDEN_MEMORY', text)
        self.assertIn('TAXES_MEMORY', text)

    def test_wrong_vector_size_or_missing_database_keep_todays_recall(self):
        memory = self.memory(self.enabled())
        memory._embedder = FakeEmbedder((1.0, 0.0, 0.0))  # stored vectors have 2 dimensions
        text = self.text(memory, 'try again')
        self.assertIn('GARDEN_MEMORY', text)
        self.assertIn('TAXES_MEMORY', text)
        (vault_path(self.store.root, 'fake') / 'embeddings.db').unlink()
        memory = self.memory(self.enabled())
        memory._embedder = FakeEmbedder((1.0, 0.0))
        text = self.text(memory, 'try again')
        self.assertIn('GARDEN_MEMORY', text)
        self.assertIn('TAXES_MEMORY', text)

    def test_missing_key_fails_before_anything_is_reserved(self):
        memory = self.memory(self.enabled())
        with unittest.mock.patch('dotenv.dotenv_values', return_value={}):
            text = self.text(memory, 'try again')
        self.assertIn('TAXES_MEMORY', text)  # today's recall
        # Only Ombre's own search estimates (large) exist; no small estimate for the query embedding.
        reserves = [row for row in self.store.read('costs.jsonl') if row.get('type') == 'reserve']
        self.assertTrue(reserves)
        self.assertTrue(all(row['estimated_tokens'] >= 8192 for row in reserves))

    def test_candidates_without_a_stored_vector_are_dropped(self):
        self.backend.rows['new'] = {'content': 'NEW_MEMORY'}
        self.backend.write_row('new')
        memory = self.memory(self.enabled())
        memory._embedder = FakeEmbedder((1.0, 0.0))
        self.assertNotIn('NEW_MEMORY', self.text(memory, 'try again'))


class HelperTests(unittest.TestCase):
    def test_vectors_and_cosine(self):
        self.assertEqual(decode_vector(array.array('f', [1.0, 2.0]).tobytes()), [1.0, 2.0])
        self.assertEqual(decode_vector('[1, 2]'), [1.0, 2.0])
        with self.assertRaises(ValueError):
            decode_vector(b'abc')
        self.assertAlmostEqual(cosine([1, 0], [1, 1]), 0.7071, places=3)
        with self.assertRaises(ValueError):
            cosine([1], [1, 2])

    def test_context_text_takes_the_last_rounds_and_clips_assistant_text(self):
        rows = [{'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'},
                {'role': 'user', 'content': 'c'}, {'role': 'assistant', 'content': 'd' * 500},
                {'role': 'user', 'content': 'e'}, {'role': 'assistant', 'content': 'f'}]
        text = context_text(rows, 'now', rounds=2)
        self.assertTrue(text.startswith('c\n'))
        self.assertTrue(text.endswith('f\nnow'))
        self.assertLess(len(text), 300)

    def test_missing_database_is_an_error_not_an_empty_answer(self):
        with self.assertRaises(FileNotFoundError):
            load_vectors('/nonexistent/embeddings.db', ['x'])
        self.assertEqual(load_vectors('/nonexistent/embeddings.db', []), {})


if __name__ == '__main__':
    unittest.main()
