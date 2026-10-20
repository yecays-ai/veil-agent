"""Explicit opt-in adapter test: fake chat model, real Ombre/free embeddings."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal

from memory_agent import Memory, open_ombre
from veil_agent import Budget, Character, Chat, ROOT, Store


@unittest.skipUnless(os.environ.get('OMBRE_ADAPTER_GATE') == '1', 'optional real adapter gate')
class LiveAdapter(unittest.TestCase):
    def test_restart_recall_forget_restore_and_child_shutdown(self):
        cfg = Character('fixture', 'Example', 'Fictional test identity', 20, 1024,
            {'model': 'offline-fixture', 'max_input_tokens': 10000,
             'input_cny_per_million': Decimal(0), 'output_cny_per_million': Decimal(0),
             'cached_input_cny_per_million': Decimal(0)}, 'fake',
            {'enabled': True, 'ombre_port': 18912, 'model': 'BAAI/bge-m3', 'dimensions': 1024,
             'base_url': 'https://api.siliconflow.cn/v1', 'input_cny_per_million': '0'})

        def fake_model(messages, *, json_output=False, **extra):
            if json_output:
                reply = json.dumps({'batches': [{'id': 0, 'memories': [{
                    'title': 'Job interview', 'zh': '下周四有一场工作面试。',
                    'en': 'There is a job interview next Thursday.',
                    'keywords': '面试 求职 找工作 interview job application Thursday',
                    'feel': False, 'quote': {'speaker': 'user',
                        'text': 'I have a job interview next Thursday.'}}, {
                    'title': 'Missing home', 'zh': '搬离家后感到孤独和想家。',
                    'en': 'Moving away from home has brought loneliness and homesickness.',
                    'keywords': '搬家 孤独 想家 lonely loneliness homesick moving home',
                    'feel': True, 'quote': {'speaker': 'user',
                        'text': 'Moving away from home has made me feel lonely.'}}]}]})
            else:
                reply = 'Fictional test reply.'
            return reply, 300, 100, 0

        with tempfile.TemporaryDirectory() as directory, Store(ROOT / 'data').lock():
            store = Store(Path(directory) / 'data')
            budget = Budget(store)
            real_budget = Budget(Store(ROOT / 'data'))
            # Only actual embedding estimates enter the shared ledger. Fake chat
            # and extraction token usage stays in this disposable test store.
            budget.estimate = real_budget.estimate
            budget.settle_estimate = real_budget.settle_estimate
            with open_ombre(ROOT, cfg, data_root=store.root) as backend:
                with Memory(cfg, store, budget, fake_model, backend) as memory:
                    chat = Chat(cfg, store, budget, fake_model, memory)
                    for text in ['I have a job interview next Thursday.'] * 2 + [
                            'Moving away from home has made me feel lonely.'] * 2:
                        chat.send(text)
                    memory.jobs.join()
                    notices = memory.notices()
                    self.assertEqual(len(memory.state['entries']), 2, repr(notices))
                    self.assertTrue(any('记下了' in n for n in notices))
                    key = next(iter(memory.state['entries']))
                    feel_key = next(k for k in memory.state['entries'] if k.startswith('feel_'))
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
                    memory.command('/forget ' + key)
                    memory.command('/forget ' + key + ' 确认')
                    self.assertNotIn(key, '\n'.join(m['content'] for m in memory.context('interview')))
                    memory.command('/restore ' + key)
                    self.assertIn(key, memory.context('interview')[-1]['content'])
                    query = '最近搬家后还是很孤独很想家'
                    self.assertIn(feel_key, memory.context(query)[-1]['content'])
                    memory.command('/forget ' + feel_key)
                    memory.command('/forget ' + feel_key + ' 确认')
                    self.assertNotIn(feel_key, '\n'.join(m['content'] for m in memory.context(query)))
                    memory.command('/restore ' + feel_key)
                    self.assertIn(feel_key, memory.context(query)[-1]['content'])
                    self.assertEqual(memory.state['cursor'], 4)
            self.assertFalse(backend.thread.is_alive())

