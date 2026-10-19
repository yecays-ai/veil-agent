import json
from decimal import Decimal
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from memory_agent import Memory, ordinary_hits, vault_path
from veil_agent import Budget, Character, Chat, Store, UserError, build_self_description


def entry(quote='fact 0', feel=False):
    return {'title': 'Fictional plan', 'zh': '下周有安排。', 'en': 'There is a plan next week.',
            'keywords': '安排 plan next week', 'feel': feel,
            'quote': {'speaker': 'user', 'text': quote}}


class Backend:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail = False

    def call(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            raise RuntimeError('fictional failure')
        if name == 'breath':
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}' for k, v in self.rows.items())
        if name == 'hold':
            key = 'feel_' + str(len(self.rows)) if args.get('feel') else f'{len(self.rows):012x}'
            self.rows[key] = dict(args)
            return ('feel→' if args.get('feel') else '新建→') + key
        if name == 'trace':
            if 'content' in args:
                self.rows[args['bucket_id']]['content'] = args['content']
            else:
                self.rows[args['bucket_id']]['hidden'] = bool(args['dont_surface'])
            return 'updated'
        if name == 'feel':
            # Simulate the upstream feel route ignoring dont_surface.
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}'
                for k, v in self.rows.items() if v.get('feel'))
        if name == 'breath_search':
            rows = [(k, v) for k, v in self.rows.items() if not v.get('hidden') and not v.get('feel')]
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}' for k, v in rows) + (
                '\n=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': [k for k, _ in rows]}) + '\n```')
        raise AssertionError(name)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024,
            {'model': 'fake', 'max_input_tokens': 10000, 'input_cny_per_million': Decimal(2),
             'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07'})
        self.budget = Budget(self.store)
        self.backend = Backend()
        self.model = Mock(side_effect=self.extract)
        self.memory = self.open()

    def open(self):
        memory = Memory(self.cfg, self.store, self.budget, self.model, self.backend)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    def extract(self, messages, *, json_output, **extra):
        self.assertTrue(json_output)
        self.assertGreater(self.budget.totals()[2], 0)
        raw = json.loads(messages[1]['content'])['batches']
        batches = [{'id': b['id'], 'memories': [entry(b['turns'][0][0]['content'])]} for b in raw]
        return json.dumps({'batches': batches}), 200, 100, 0

    def turns(self, count, start=0):
        for i in range(start, start + count):
            for role, text in [('user', f'fact {i}'), ('assistant', f'reply {i}')]:
                self.store.append('fake/chat.jsonl', {'role': role, 'content': text})

    def test_four_rounds_background_budget_and_notice(self):
        self.turns(3)
        self.memory.supplement()
        self.model.assert_not_called()
        self.turns(1, 3)
        self.memory.completed()
        self.memory.jobs.join()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.backend.rows), 1)
        saved = next(iter(self.backend.rows.values()))
        self.assertIsInstance(saved['source_content'], str)
        self.assertTrue(saved['source_content'].startswith('user: fact 0'))
        self.assertIn('📝 Example 记下了：Fictional plan', self.memory.notices())
        costs = self.store.read('costs.jsonl')
        self.assertEqual(len([c for c in costs if c['type'] == 'reserve']), 2)
        self.assertTrue(any(c.get('accounting') == 'embedding-estimate' for c in costs))
        self.assertEqual(self.budget.totals()[2], 0)

    def test_extractor_sees_existing_memories_but_not_hidden_ones(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        self.turns(4, 4)
        self.memory.supplement()
        sent = json.loads(self.model.call_args.args[0][1]['content'])
        self.assertEqual([e['id'] for e in sent['existing']], [key])
        self.assertEqual(sent['existing'][0]['text'], '下周有安排。')
        self.memory.command(f'/forget {key}')
        self.turns(4, 8)
        self.memory.supplement()
        sent = json.loads(self.model.call_args.args[0][1]['content'])
        self.assertNotIn(key, [e['id'] for e in sent['existing']])

    def test_update_rewrites_existing_memory_instead_of_adding_another(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        self.turns(4, 4)

        def update(messages, *, json_output, **extra):
            item = dict(entry('fact 4'), op='update', target=key, zh='喜欢蓝色，因为名字的读音。',
                        en='Likes blue because of how the name sounds.')
            return json.dumps({'batches': [{'id': 4, 'memories': [item]}]}), 200, 100, 0
        self.model.side_effect = update
        self.memory.supplement()
        self.assertEqual(len(self.backend.rows), 1)
        self.assertIn('因为名字的读音', self.backend.rows[key]['content'])
        self.assertEqual(self.memory.state['entries'][key]['text'], '喜欢蓝色，因为名字的读音。')
        self.assertTrue(any('补充了记忆' in n for n in self.memory.notices()))

    def test_update_of_unknown_or_hidden_target_is_dropped_and_listed_locally(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        self.memory.command(f'/forget {key}')
        self.turns(4, 4)

        def update(messages, *, json_output, **extra):
            item = dict(entry('fact 4'), op='update', target=key, title='Hidden target')
            return json.dumps({'batches': [{'id': 4, 'memories': [item]}]}), 200, 100, 0
        self.model.side_effect = update
        self.memory.supplement()
        self.assertEqual(len(self.backend.rows), 1)
        self.assertIn('被丢弃的候选', self.memory.command('/memory'))
        self.assertIn('Hidden target', self.memory.command('/memory'))

    def test_false_quote_discards_whole_memory(self):
        self.turns(4)
        self.model.side_effect = None
        self.model.return_value = (json.dumps({'batches': [{'id': 0, 'memories': [entry('invented quote')]}]}), 200, 100, 0)
        self.memory.supplement()
        self.assertEqual(self.backend.rows, {})

    def test_quote_speaker_must_match(self):
        self.turns(4)
        item = entry('reply 0')
        self.memory.save_item(item, self.memory.turns()[:4], 0)
        self.assertEqual(self.backend.rows, {})

    def test_failure_waits_for_next_four_and_retries_at_most_twice(self):
        self.turns(4)
        self.model.side_effect = RuntimeError('fake provider failure')
        self.memory.supplement()
        self.memory.supplement()
        self.assertEqual(self.model.call_count, 1)
        reopened = self.open()
        self.turns(4, 4)
        reopened.supplement()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([b['id'] for b in reopened.state['pending']], [4])
        self.turns(4, 8)
        reopened.supplement()
        request = json.loads(self.model.call_args.args[0][1]['content'])['batches']
        self.assertEqual([b['id'] for b in request], [4, 8])

    def test_restart_keeps_partial_group_and_does_not_backfill_old_chats(self):
        self.turns(3)
        reopened = self.open()
        self.turns(1, 3)
        reopened.supplement()
        self.assertEqual(self.model.call_count, 1)
        self.cfg.id = 'new'
        for i in range(6):
            for role in ('user', 'assistant'):
                self.store.append('new/chat.jsonl', {'role': role, 'content': 'old'})
        other = self.open()
        other.supplement()
        self.assertEqual(self.model.call_count, 1)

    def test_top_three_provenance_and_nohit(self):
        empty = self.memory.context('something new')
        self.assertIn('不要假装记得', empty[-1]['content'])
        self.assertIn('不是 lan 刚说的', empty[-1]['content'])
        for i in range(5):
            self.backend.rows[str(i)] = {'content': f'body {i}'}
        text = self.memory.context('plan')[-1]['content']
        self.assertEqual(text.count('[bucket_id:'), 3)
        self.assertNotIn('body 4', text)

    def test_random_surfacing_and_exact_id_are_not_hits(self):
        raw = '[bucket_id:bad] unrelated\n=== ombre:result-ids ===\n```json\n{"bucket_ids": []}\n```'
        self.assertEqual(ordinary_hits(raw), [])
        self.assertEqual(ordinary_hits('[bucket_id:bad] exact'), [])

    def test_forget_filters_emotions_and_opening_restore_works(self):
        self.turns(4)
        self.memory.save_item(entry(feel=True), self.memory.turns(), 0)
        key = next(k for k, v in self.backend.rows.items() if v.get('feel'))
        reopened = self.open()
        reopened.command('/forget ' + key)
        self.assertNotIn(key, '\n'.join(m['content'] for m in reopened.context('plan')))
        self.assertIn('已隐藏', reopened.command('/memory'))
        reopened.command('/restore ' + key)
        self.assertIn(key, reopened.context('plan')[-1]['content'])
        self.assertEqual(len(self.backend.rows), 2)  # No file deletion.

    def test_same_quote_is_not_saved_again_after_restart(self):
        self.turns(4)
        self.memory.save_item(entry(), self.memory.turns(), 0)
        reopened = self.open()
        reopened.save_item(entry(), reopened.turns(), 0)
        self.assertEqual(len(self.backend.rows), 1)

    def test_zero_items_is_valid_and_has_no_hold_calls(self):
        self.turns(4)
        self.model.side_effect = None
        self.model.return_value = ('{"batches":[{"id":0,"memories":[]}]}', 200, 20, 0)
        self.memory.supplement()
        self.assertEqual(self.memory.state['pending'], [])
        self.assertFalse(any(name == 'hold' for name, _ in self.backend.calls))

    def test_uncertain_forget_stays_hidden_across_restart(self):
        self.turns(4)
        self.memory.save_item(entry(), self.memory.turns(), 0)
        key = next(iter(self.memory.state['entries']))
        self.backend.fail = True
        self.memory.command('/forget ' + key)
        self.backend.fail = False
        reopened = self.open()
        self.assertNotIn(key, '\n'.join(m['content'] for m in reopened.context('plan')))

    def test_merge_into_hidden_memory_keeps_it_hidden(self):
        self.turns(4)
        self.memory.save_item(entry(), self.memory.turns(), 0)
        key = next(iter(self.memory.state['entries']))
        self.memory.command('/forget ' + key)
        original = self.backend.call
        self.backend.call = lambda name, args: '合并→' + key if name == 'hold' else original(name, args)
        self.memory.save_item(entry('fact 1'), self.memory.turns(), 0)
        self.assertTrue(self.memory.state['entries'][key]['hidden'])
        self.assertNotIn(key, '\n'.join(m['content'] for m in self.memory.context('plan')))

    def diagnostics_text(self):
        return (Path(self.temp.name) / 'data/fake/memory/_agent/diagnostics.jsonl').read_text(encoding='utf-8')

    def test_extraction_uses_its_own_cap_without_reasoning(self):
        self.turns(4)
        self.memory.supplement()
        kwargs = self.model.call_args.kwargs
        self.assertEqual(kwargs['max_tokens'], 4096)
        self.assertEqual(kwargs['thinking'], 'disabled')
        self.assertTrue(kwargs['detail'])
        reserved = [c for c in self.store.read('costs.jsonl') if c['type'] == 'reserve'
                    and 'accounting' not in c]
        self.assertEqual(Decimal(reserved[0]['amount']), (10000 * Decimal(2) + 4096 * Decimal(8)) / 1_000_000)

    def test_truncated_reply_is_diagnosed_without_chat_text(self):
        self.turns(4)
        self.model.side_effect = None
        self.model.return_value = ('{"batches":[{"id":0,"memories":[{"title":"fact 0 secret', 200, 4096, 0, 'length')
        self.memory.supplement()
        notice = ' '.join(self.memory.notices())
        self.assertIn('截断', notice)
        self.assertIn('4096/4096', notice)
        row = json.loads(self.diagnostics_text().splitlines()[-1])
        self.assertEqual((row['code'], row['finish'], row['completion'], row['cap']),
                         ('truncated', 'length', 4096, 4096))
        self.assertNotIn('fact 0', self.diagnostics_text())
        self.assertNotIn('secret', self.diagnostics_text())
        listing = self.memory.command('/memory')
        self.assertIn('还没有保存任何记忆', listing)
        self.assertIn('截断', listing)

    def test_invalid_json_empty_and_api_errors_have_specific_codes(self):
        class ProviderError(Exception):
            status_code = 429
        cases = [(('not json at all', 200, 20, 0, 'stop'), 'not_json'),
                 ((None, 200, 0, 0, 'stop'), 'empty'),
                 (('{"nope":1}', 200, 5, 0, 'stop'), 'shape'),
                 (('{"batches":[{"id":7,"memories":[]}]}', 200, 5, 0, 'stop'), 'batch_ids')]
        self.turns(4)
        for number, (reply, code) in enumerate(cases):
            self.model.side_effect = None
            self.model.return_value = reply
            self.state_reset()
            self.memory.supplement()
            self.assertEqual(json.loads(self.diagnostics_text().splitlines()[-1])['code'], code)
        self.state_reset()
        self.model.side_effect = ProviderError('contains fact 0')
        self.memory.supplement()
        row = json.loads(self.diagnostics_text().splitlines()[-1])
        self.assertEqual((row['code'], row['error'], row['status']), ('api', 'ProviderError', 429))
        self.assertNotIn('fact 0', self.diagnostics_text())

    def state_reset(self):
        self.memory.state['cursor'] = 0
        self.memory.state['pending'] = []

    def test_mismatched_quotes_are_reported_not_silent(self):
        self.turns(4)
        self.model.side_effect = None
        self.model.return_value = (json.dumps({'batches': [{'id': 0, 'memories': [entry('invented quote')]}]}), 200, 100, 0, 'stop')
        self.memory.supplement()
        self.assertIn('原话对不上', ' '.join(self.memory.notices()))
        self.assertEqual(self.backend.rows, {})

    def test_partial_save_retries_only_unsaved_quotes(self):
        self.turns(4)
        self.memory.save_item(entry(), self.memory.turns(), 0)
        self.memory.supplement()
        self.assertEqual(len(self.backend.rows), 1)

    def test_roles_and_redirected_directories(self):
        self.assertNotEqual(vault_path(self.store.root, 'a'), vault_path(self.store.root, 'b'))
        for key in ('../b', 'a/b', '..'):
            with self.assertRaises(ValueError):
                vault_path(self.store.root, key)
        self.cfg.id = 'second'
        other = self.open()
        self.assertEqual(other.state['entries'], {})
        self.assertEqual(other.state['cursor'], 0)

    def test_embedding_budget_blocks_before_backend(self):
        self.budget.daily = Decimal('0.00001')
        before = len(self.backend.calls)
        with self.assertRaises(UserError):
            self.memory.call('hold', {'content': 'fake'})
        self.assertEqual(len(self.backend.calls), before)

    def test_chat_injects_memory_and_updates_truth(self):
        model = Mock(return_value=('reply', 200, 50, 0))
        chat = Chat(self.cfg, self.store, self.budget, model, self.memory)
        chat.send('hello')
        self.memory.jobs.join()
        sent = model.call_args.args[0]
        self.assertIn('Long-term memory:', sent[1]['content'])
        self.assertIn('不是 lan 刚说的', sent[3]['content'])
        self.assertEqual([r['role'] for r in self.store.read('fake/chat.jsonl')], ['user', 'assistant'])

    def test_concurrent_reservations_share_one_atomic_limit(self):
        self.budget.daily = Decimal('0.15')
        results = []
        def reserve():
            try:
                self.budget.estimate('fake', 'embed', 100000, '1')
                results.append(True)
            except UserError:
                results.append(False)
        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [False, True])
