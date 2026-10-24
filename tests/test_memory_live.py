"""Explicit opt-in adapter test: fake chat model, real Ombre/free embeddings."""
import os
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal

from memory_agent import Memory, open_ombre
from veil_agent import Budget, Character, Chat, ROOT, Store


@unittest.skipUnless(os.environ.get('OMBRE_ADAPTER_GATE') == '1', 'optional real adapter gate')
class LiveAdapter(unittest.TestCase):
    def test_restart_recall_delete_and_child_shutdown(self):
        cfg = Character('fixture', 'Example', 'Fictional test identity', 20, 1024,
            {'model': 'offline-fixture', 'max_input_tokens': 10000,
             'input_cny_per_million': Decimal(0), 'output_cny_per_million': Decimal(0),
             'cached_input_cny_per_million': Decimal(0)}, 'fake',
            {'enabled': True, 'ombre_port': 18912, 'model': 'BAAI/bge-m3', 'dimensions': 1024,
             'base_url': 'https://api.siliconflow.cn/v1', 'input_cny_per_million': '0'})

        def fake_model(messages, **extra):
            return 'Fictional test reply.', 300, 100, 0

        def save(memory, args):
            text = memory.call('hold', args)
            memory.record_native(args, text)
            return memory.created_id(text)

        with tempfile.TemporaryDirectory() as directory, Store(ROOT / 'data').lock():
            store = Store(Path(directory) / 'data')
            budget = Budget(store)
            real_budget = Budget(Store(ROOT / 'data'))
            # Only actual embedding estimates enter the shared ledger. Fake chat
            # token usage stays in this disposable test store.
            budget.estimate = real_budget.estimate
            budget.settle_estimate = real_budget.settle_estimate
            with open_ombre(ROOT, cfg, data_root=store.root) as backend:
                with Memory(cfg, store, budget, fake_model, backend) as memory:
                    key = save(memory, {
                        'title': 'Job interview',
                        'content': '下周四有一场工作面试。\n\nThere is a job interview next Thursday.\n\n'
                                   '检索词：面试 求职 找工作 interview job application Thursday'})
                    Chat(cfg, store, budget, fake_model, memory).send('I have a job interview next Thursday.')
            self.assertFalse(backend.thread.is_alive())
            cfg.id = 'other'
            with open_ombre(ROOT, cfg, data_root=store.root) as backend:
                with Memory(cfg, store, budget, fake_model, backend) as memory:
                    self.assertEqual(memory.state['entries'], {})
                    self.assertNotIn(key, '\n'.join(m['content'] for m in memory.context('interview')))
            self.assertFalse(backend.thread.is_alive())
            cfg.id = 'fixture'
            with open_ombre(ROOT, cfg, data_root=store.root) as backend:
                with Memory(cfg, store, budget, fake_model, backend) as memory:
                    self.assertIn(key, memory.command('/memory'))
                    self.assertIn(key, memory.context('下周四的面试我有点紧张')[-1]['content'])
                    memory.command('/delete ' + key)
                    self.assertIn('已删除', memory.command('/delete ' + key + ' 确认'))
                    self.assertNotIn(key, '\n'.join(m['content'] for m in memory.context('interview')))
                    self.assertNotIn(key, memory.command('/memory'))
            self.assertFalse(backend.thread.is_alive())
