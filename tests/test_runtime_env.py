"""P2-B: an isolated instance reads only its own config root .env, never the repository one."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import runtime_env
from veil.brain.memory_agent import Memory
from veil.brain.recall.recall_judge import RecallJudge


class RuntimeEnvTests(unittest.TestCase):
    def test_env_file_follows_character_root(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = SimpleNamespace(root=temp)
            self.assertEqual(runtime_env.env_file(cfg), Path(temp).resolve() / '.env')

    def test_legacy_config_without_root_keeps_repository_env(self):
        self.assertEqual(runtime_env.env_file(SimpleNamespace()), runtime_env.ROOT / '.env')

    def test_missing_isolated_env_does_not_fall_back_to_production_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # The repository .env has a real key; the isolated root deliberately has none.
            cfg = SimpleNamespace(id='fake', root=str(root), memory={
                'base_url': 'https://example.invalid/v1', 'model': 'fake-embedding'})
            memory = Memory.__new__(Memory)
            memory.cfg = cfg
            memory._embedder = None
            with self.assertRaises(Exception) as raised:
                memory.embedder()
            self.assertIn('EMBED_API_KEY', str(raised.exception))

    def test_embedder_uses_the_isolated_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.env').write_text('EMBED_API_KEY=isolated-sentinel\n', encoding='utf-8')
            cfg = SimpleNamespace(id='fake', root=str(root), memory={
                'base_url': 'https://example.invalid/v1', 'model': 'fake-embedding'})
            memory = Memory.__new__(Memory)
            memory.cfg = cfg
            memory._embedder = None
            self.assertEqual(memory.embedder().api_key, 'isolated-sentinel')

    def test_recall_judge_uses_the_isolated_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.env').write_text('EMBED_API_KEY=judge-sentinel\n', encoding='utf-8')
            cfg = SimpleNamespace(id='fake', root=str(root), memory={
                'base_url': 'https://example.invalid/v1', 'model': 'judge-embedding'})
            judge = RecallJudge.__new__(RecallJudge)
            judge.memory = SimpleNamespace(cfg=cfg)
            judge.settings = {'timeout_seconds': 5}
            self.assertEqual(judge.make_embedder().api_key, 'judge-sentinel')

if __name__ == '__main__':
    unittest.main()
