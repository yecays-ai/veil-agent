"""P2-B: an isolated instance reads only its own config root .env, never the repository one."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import runtime_env
from memory_agent import Memory
from recall_judge import RecallJudge


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

    def test_ombre_environment_reads_env_root(self):
        import ombre_resident
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env_root = root / 'config'
            env_root.mkdir()
            (env_root / '.env').write_text('EMBED_API_KEY=ombre-sentinel\n', encoding='utf-8')
            cfg = SimpleNamespace(memory={'ombre_model': False},
                                  provider={'api_key_env': 'DEEPSEEK_API_KEY'})
            (root / 'private').mkdir()
            environment = ombre_resident._environment(
                root, cfg, root / 'vault', root / 'private', root / 'config.yaml', 't' * 48, 12345,
                env_root=env_root)
            self.assertEqual(environment['OMBRE_EMBED_API_KEY'], 'ombre-sentinel')


if __name__ == '__main__':
    unittest.main()
