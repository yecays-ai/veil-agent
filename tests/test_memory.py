import json
from decimal import Decimal
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from memory_agent import Memory, ordinary_hits, vault_path
from ombre_resident import resident_config
from veil_agent import Budget, Character, Chat, Store, UserError, build_self_description


def entry(quote='fact 0', feel=False):
    return {'title': 'Fictional plan', 'zh': '下周有安排。', 'en': 'There is a plan next week.',
            'keywords': '安排 plan next week', 'feel': feel,
            'quote': {'speaker': 'user', 'text': quote}}


class Backend:
    def __init__(self, vault=None):
        self.vault = vault
        self.rows = {}
        self.calls = []
        self.fail = False

    def write_row(self, key):
        if self.vault is None:
            return
        row = self.rows[key]
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / f'{key}.md').write_text(
            f'---\nid: {key}\ntitle: Fictional memory\n'
            f'dont_surface: {str(bool(row.get("hidden"))).lower()}\n'
            f'type: {"archived" if row.get("deleted") else "dynamic"}\n---\n{row["content"]}',
            encoding='utf-8')

    def call(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            raise RuntimeError('fictional failure')
        if name == 'breath':
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}' for k, v in self.rows.items())
        if name == 'hold':
            key = 'feel_' + str(len(self.rows)) if args.get('feel') else f'{len(self.rows):012x}'
            self.rows[key] = dict(args)
            self.write_row(key)
            return ('feel→' if args.get('feel') else '新建→') + key
        if name == 'trace':
            if args.get('delete'):
                self.rows[args['bucket_id']]['deleted'] = True
            elif args.get('restore'):
                self.rows[args['bucket_id']]['deleted'] = False
            elif 'content' in args:
                self.rows[args['bucket_id']]['content'] = args['content']
            else:
                self.rows[args['bucket_id']]['hidden'] = bool(args['dont_surface'])
            self.write_row(args['bucket_id'])
            return 'updated'
        if name == 'feel':
            # Simulate the upstream feel route ignoring dont_surface.
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}'
                for k, v in self.rows.items() if v.get('feel'))
        if name == 'breath_search':
            rows = [(k, v) for k, v in self.rows.items()
                    if not v.get('hidden') and not v.get('deleted') and not v.get('feel')]
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}' for k, v in rows) + (
                '\n=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': [k for k, _ in rows]}) + '\n```')
        raise AssertionError(name)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024,
            {'model': 'fake', 'base_url': 'https://example.invalid/v1',
             'max_input_tokens': 10000, 'input_cny_per_million': Decimal(2),
             'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}, 'fake',
            {'model': 'fake-embedding', 'base_url': 'https://example.invalid/v1',
             'dimensions': 3, 'input_cny_per_million': '0.07'})
        self.budget = Budget(self.store)
        self.backend = Backend(self.store.root / 'fake' / 'memory' / 'dynamic')
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


    def test_restart_preserves_memory_cursor_after_multiple_and_proactive_replies(self):
        for mode in ('extract', 'native'):
            with self.subTest(mode=mode):
                root = Path(self.temp.name) / mode
                store = Store(root)
                cfg = Character(mode, 'Example', 'fictional persona', 20, 1024,
                    {'model': 'fake', 'base_url': 'https://example.invalid/v1',
                     'max_input_tokens': 10000, 'input_cny_per_million': Decimal(2),
                     'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)},
                    'fake', {'model': 'fake-embedding', 'base_url': 'https://example.invalid/v1',
                     'dimensions': 3, 'input_cny_per_million': '0.07', 'mode': mode})
                for i in range(2):
                    store.append(f'{mode}/chat.jsonl', {'role': 'user', 'content': f'fictional user {i}'})
                    store.append(f'{mode}/chat.jsonl', {'role': 'assistant', 'content': f'fictional reply {i}'})
                first = Memory(cfg, store, Budget(store), Mock(), Backend())
                self.assertEqual(first.state['cursor'], 2)
                first.__exit__(None, None, None)

                # A second independent reply and a proactive message follow the last saved turn.
                store.append(f'{mode}/chat.jsonl', {'role': 'assistant', 'content': 'fictional second reply'})
                store.append(f'{mode}/chat.jsonl', {'role': 'assistant', 'content': 'fictional proactive message',
                                                    'origin': 'proactive'})
                history_path = root / mode / 'chat.jsonl'
                history_before_restart = history_path.read_bytes()
                persisted_before_restart = store.read(f'{mode}/memory/_agent/state.jsonl')[-1]['state']

                reopened = Memory(cfg, store, Budget(store), Mock(), Backend())
                try:
                    self.assertEqual(reopened.state['cursor'], 2)
                    self.assertEqual(reopened.state['entries'], persisted_before_restart['entries'])
                    self.assertEqual(reopened.state['pending'], persisted_before_restart['pending'])
                    self.assertEqual(history_path.read_bytes(), history_before_restart)
                    self.assertEqual(store.read(f'{mode}/chat.jsonl')[-1]['content'], 'fictional proactive message')
                    self.assertEqual(store.read('costs.jsonl'), [])
                finally:
                    reopened.__exit__(None, None, None)

    def test_assistant_only_proactive_chat_keeps_legacy_zero_cursor_on_restart(self):
        self.store.append('fake/chat.jsonl', {'role': 'assistant', 'content': 'fictional proactive opening',
                                              'origin': 'proactive'})
        history_before_restart = (self.store.root / 'fake/chat.jsonl').read_bytes()
        reopened = self.open()
        self.assertEqual(reopened.state['cursor'], 0)
        self.assertEqual((self.store.root / 'fake/chat.jsonl').read_bytes(), history_before_restart)

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
        self.assertEqual(len([c for c in costs if c['type'] == 'reserve']), 3)
        self.assertTrue(any(c.get('accounting') == 'embedding-estimate' for c in costs))
        self.assertTrue(any(c.get('accounting') == 'ombre-model-estimate' for c in costs))
        self.assertEqual(self.budget.totals()[2], 0)

    def test_tool_summaries_reach_memory_without_file_contents(self):
        for i in range(4):
            request_id = f'request-{i}'
            self.store.append('fake/chat.jsonl', {'role': 'user', 'content': f'fact {i}',
                                                  'request_id': request_id})
            self.store.append('fake/chat.jsonl', {'role': 'assistant',
                                                  'content': 'file body secret' if i == 0 else f'reply {i}',
                                                  'request_id': request_id})
        self.store.append('fake/tools.jsonl', {'request_id': 'request-0', 'kind': 'capability',
            'text': '你的能力有变化：可读 workspace。'})
        self.store.append('fake/tools.jsonl', {'request_id': 'request-0', 'tool': 'read_text_file',
            'path': 'notes.txt', 'state': '成功'})
        self.memory.supplement()
        raw = json.loads(self.model.call_args.args[0][1]['content'])['batches'][0]
        self.assertEqual(raw['tool_events'], ['你的能力有变化：可读 workspace。', 'read_text_file：notes.txt（成功）'])
        self.assertIn('reply text is omitted from memory extraction', raw['turns'][0][1]['content'])
        self.assertNotEqual(raw['turns'][0][1]['content'], 'file body secret')
        self.assertNotIn('file body', json.dumps(raw, ensure_ascii=False))
        self.assertTrue(self.backend.rows)
        self.assertTrue(all('file body secret' not in row.get('source_content', '')
                            for row in self.backend.rows.values()))

    def test_legacy_rounds_do_not_shift_file_read_redaction(self):
        for i in range(4):
            request_id = f'request-{i}' if i >= 2 else None
            for role, content in [('user', f'fact {i}'),
                                  ('assistant', 'file body secret' if i == 2 else f'reply {i}')]:
                row = {'role': role, 'content': content}
                if request_id:
                    row['request_id'] = request_id
                self.store.append('fake/chat.jsonl', row)
        self.store.append('fake/tools.jsonl', {'request_id': 'request-2',
            'tool': 'read_text_file', 'path': 'notes.txt', 'state': '成功'})
        self.memory.supplement()
        raw = json.loads(self.model.call_args.args[0][1]['content'])['batches'][0]
        self.assertEqual(raw['turns'][0][1]['content'], 'reply 0')
        self.assertIn('reply text is omitted from memory extraction', raw['turns'][2][1]['content'])
        self.assertNotIn('file body secret', json.dumps(raw, ensure_ascii=False))
        self.assertTrue(all('file body secret' not in row.get('source_content', '')
                            for row in self.backend.rows.values()))

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
        self.memory.command(f'/forget {key} 确认')
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
            item = dict(entry('fact 4'), op='update', target=key, zh='喜欢示例颜色，因为一个示例原因。',
                        en='Likes the example color for an example reason.')
            return json.dumps({'batches': [{'id': 4, 'memories': [item]}]}), 200, 100, 0
        self.model.side_effect = update
        self.memory.supplement()
        self.assertEqual(len(self.backend.rows), 1)
        self.assertIn('因为一个示例原因', self.backend.rows[key]['content'])
        self.assertEqual(self.memory.state['entries'][key]['text'], '喜欢示例颜色，因为一个示例原因。')
        self.assertTrue(any('补充了记忆' in n for n in self.memory.notices()))

    def test_update_of_unknown_or_hidden_target_is_dropped_and_listed_locally(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        self.memory.command(f'/forget {key}')
        self.memory.command(f'/forget {key} 确认')
        self.turns(4, 4)

        def update(messages, *, json_output, **extra):
            item = dict(entry('fact 4'), op='update', target=key, title='Hidden target')
            return json.dumps({'batches': [{'id': 4, 'memories': [item]}]}), 200, 100, 0
        self.model.side_effect = update
        self.memory.supplement()
        self.assertEqual(len(self.backend.rows), 1)
        self.assertIn('被丢弃的候选', self.memory.command('/memory'))
        self.assertIn('Hidden target', self.memory.command('/memory'))

    def test_extraction_request_carries_local_persona_text(self):
        self.turns(4)
        self.memory.supplement()
        system = self.model.call_args.args[0][0]['content']
        self.assertIn('fictional persona', system)
        self.assertIn('One event is one memory', system)

    def test_delete_needs_confirmation_then_removes_the_memory_from_list_recall_and_extractor(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        asked = self.memory.command(f'/delete {key}')
        self.assertIn(f'/delete {key} 确认', asked)
        self.assertNotIn('deleted', self.backend.rows[key])
        self.assertIn('已删除', self.memory.command(f'/delete {key} 确认'))
        self.assertTrue(self.backend.rows[key]['deleted'])
        self.assertNotIn(key, self.memory.command('/memory'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [])
        self.turns(4, 4)
        self.memory.supplement()
        sent = json.loads(self.model.call_args.args[0][1]['content'])
        self.assertNotIn(key, [e['id'] for e in sent['existing']])
        self.assertIn('没有这个记忆编号', self.memory.command(f'/delete {key} 确认'))
        refused = self.memory.command(f'/restore {key}')
        self.assertIn('不能用 /restore 恢复', refused)
        self.assertIn('软删除', refused)
        self.assertIn('没有被物理擦除', refused)
        self.assertTrue(self.backend.rows[key]['deleted'])
        self.assertNotIn(key, self.memory.command('/memory'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [])
        self.assertNotIn(('trace', {'bucket_id': key, 'restore': True}), self.backend.calls)

    def test_delete_prompt_and_result_explain_soft_delete_and_point_to_forget(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        asked = self.memory.command(f'/delete {key}')
        for text in ('不能用 /restore 恢复', '软删除', '没有被物理擦除', f'/forget {key}'):
            self.assertIn(text, asked)
        done = self.memory.command(f'/delete {key} 确认')
        for text in ('已删除', '不能用 /restore 恢复', '软删除'):
            self.assertIn(text, done)

    def test_forget_is_visible_in_memory_list_and_restore_brings_it_back(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        self.memory.command(f'/forget {key}')
        self.assertIn('可用 /restore', self.memory.command(f'/forget {key} 确认'))
        self.assertIn('（已隐藏）', self.memory.command('/memory'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [])
        self.assertIn('已恢复', self.memory.command(f'/restore {key}'))
        self.assertNotIn('（已隐藏）', self.memory.command('/memory'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [(key, 'x')])

    def test_failed_forget_or_delete_stays_blocked_after_restart_and_other_memories_survive(self):
        self.turns(4)
        self.memory.supplement()
        self.turns(4, 4)
        self.memory.supplement()
        keys = list(self.memory.state['entries'])
        self.assertEqual(len(keys), 2)
        hidden, deleted = keys[0], keys[1]
        self.backend.fail = True
        self.memory.command(f'/forget {hidden}')
        self.assertIn('没能确认', self.memory.command(f'/forget {hidden} 确认'))
        self.memory.command(f'/delete {deleted}')
        self.assertIn('没能确认', self.memory.command(f'/delete {deleted} 确认'))
        self.backend.fail = False
        reopened = self.open()
        calls_before = len(self.backend.calls)
        self.assertEqual(reopened.visible([(hidden, 'x'), (deleted, 'y')]), [])
        self.assertIn('不能用 /restore 恢复', reopened.command(f'/restore {deleted}'))
        self.assertNotIn(deleted, reopened.command('/memory'))
        self.assertEqual(len(self.backend.calls), calls_before)  # Restart touches nothing.
        self.assertEqual(set(self.backend.rows), set(keys))  # Nothing was physically removed.
        # The forgotten one is still recoverable; the deleted one can be retried to completion.
        self.assertIn('已恢复', reopened.command(f'/restore {hidden}'))
        self.assertIn('已删除', reopened.command(f'/delete {deleted}') + reopened.command(f'/delete {deleted} 确认'))
        self.assertTrue(self.backend.rows[deleted]['deleted'])

    def test_delete_stays_out_of_recall_even_if_ombre_does_not_confirm(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        self.backend.fail = True
        self.memory.command(f'/delete {key}')
        self.assertIn('没能确认', self.memory.command(f'/delete {key} 确认'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [])
        self.assertNotIn(key, self.memory.command('/memory'))
        self.backend.fail = False
        self.assertIn('已删除', self.memory.command(f'/delete {key}'))
        self.assertIn('没有这个记忆编号', self.memory.command(f'/delete {key} 确认'))

    def test_delete_confirm_without_asking_first_only_shows_the_memory(self):
        self.turns(4)
        self.memory.supplement()
        key = next(iter(self.memory.state['entries']))
        self.assertIn(f'/delete {key} 确认', self.memory.command(f'/delete {key} 确认'))
        self.assertNotIn('deleted', self.memory.state['entries'][key])
        self.assertIn('已删除', self.memory.command(f'/delete {key} 确认'))

    def test_delete_unknown_number_changes_nothing(self):
        self.assertIn('没有这个记忆编号', self.memory.command('/delete nope 确认'))
        self.assertEqual(self.backend.calls[-1][0], 'breath')

    def test_ombre_model_calls_are_estimated_into_the_shared_ledger(self):
        self.memory.call('hold', {'content': '下周有安排。' * 40})
        costs = self.store.read('costs.jsonl')
        modelled = [c for c in costs if c.get('accounting') == 'ombre-model-estimate']
        self.assertEqual(len(modelled), 2)  # reserve + settle
        self.assertGreater(Decimal(modelled[0]['amount']), 0)
        self.assertEqual(self.budget.totals()[2], 0)

    def test_ombre_model_estimate_covers_pinned_output_caps_and_retries(self):
        from memory_agent import ombre_model_tokens
        tokens_in, tokens_out = ombre_model_tokens('fictional memory')
        self.assertGreaterEqual(tokens_in, 3 * ((3200 + 16 + 1000 + 16 + 5400) // 2))
        self.assertEqual(tokens_out, 3 * (4096 + 1024))

    def test_three_fake_child_retries_are_inside_the_shared_reservation(self):
        from memory_agent import ombre_model_tokens
        tokens_in, tokens_out = ombre_model_tokens('fictional memory')
        expected = (Decimal(tokens_in) * Decimal('2') + Decimal(tokens_out) * Decimal('8')) / 1_000_000
        attempts = []

        def fake_child_call(name, args):
            model_reserve = next(row for row in self.store.read('costs.jsonl')
                                 if row.get('type') == 'reserve'
                                 and row.get('accounting') == 'ombre-model-estimate')
            self.assertGreaterEqual(Decimal(model_reserve['amount']), expected)
            attempts.extend([1, 2, 3])
            return 'fictional success after retries'

        self.backend.call = Mock(side_effect=fake_child_call)
        self.memory.call('hold', {'content': 'fictional memory'})
        self.assertEqual(attempts, [1, 2, 3])
        self.assertEqual(self.budget.totals()[2], 0)

    def test_failed_ombre_call_settles_conservative_reservations(self):
        original = self.backend.call
        self.backend.call = Mock(side_effect=RuntimeError('fictional timeout'))
        with self.assertRaises(RuntimeError):
            self.memory.call('hold', {'content': 'fictional memory'})
        self.assertEqual(self.budget.totals()[2], 0)
        rows = self.store.read('costs.jsonl')
        reservations = [row for row in rows if row['type'] == 'reserve']
        settlements = [row for row in rows if row['type'] == 'settle']
        self.assertEqual(len(reservations), 2)
        self.assertEqual({row['id'] for row in reservations}, {row['id'] for row in settlements})
        self.backend.call = original

    def test_later_preflight_rejection_releases_only_unsent_reservation(self):
        original = self.backend.call
        self.budget.estimate_model = Mock(side_effect=UserError('fictional budget limit'))
        with self.assertRaises(UserError):
            self.memory.call('hold', {'content': 'fictional memory'})
        self.backend.call = original
        self.assertEqual(self.backend.calls, [('breath', {})])
        rows = self.store.read('costs.jsonl')
        reserves = [row for row in rows if row['type'] == 'reserve']
        settlements = [row for row in rows if row['type'] == 'settle']
        self.assertEqual(len(reserves), 1)
        self.assertEqual(len(settlements), 1)
        self.assertEqual(settlements[0]['amount'], '0')
        self.assertEqual(self.budget.totals()[2], 0)

    def test_recovered_ombre_reservation_is_settled_once_after_restart(self):
        pending = self.budget.estimate_model('fake', 'fake-chat', 100, 50, '1', '2')
        self.memory.__exit__(None, None, None)
        reopened = self.open()
        self.assertEqual(self.budget.totals()[2], 0)
        rows = self.store.read('costs.jsonl')
        settles = [row for row in rows if row['type'] == 'settle' and row['id'] == pending[0]]
        self.assertEqual(len(settles), 1)
        self.assertEqual(settles[0]['amount'], str(pending[1]))
        self.assertEqual(self.budget.settle_estimate(pending, 'duplicate-recovery'), False)
        self.assertEqual(len([row for row in self.store.read('costs.jsonl')
                              if row['type'] == 'settle' and row['id'] == pending[0]]), 1)
        reopened.__exit__(None, None, None)

    def test_ombre_model_can_be_switched_off(self):
        self.cfg.memory.update(ombre_model=False, base_url='https://example.invalid/v1')
        self.memory.call('hold', {'content': 'x'})
        costs = self.store.read('costs.jsonl')
        self.assertFalse(any(c.get('accounting') == 'ombre-model-estimate' for c in costs))
        self.assertEqual(resident_config(self.cfg, Path('v'), 'x' * 64)['dehydration'], {'api_key': ''})

    def test_ombre_config_uses_chat_model_without_a_key_in_the_file(self):
        self.cfg.memory['base_url'] = 'https://example.invalid/v1'
        self.cfg.provider['base_url'] = 'https://api.deepseek.com'
        dehydration = resident_config(self.cfg, Path('v'), 'x' * 64)['dehydration']
        self.assertEqual(dehydration['model'], 'fake')
        self.assertEqual(dehydration['api_key'], '')
        self.assertEqual(dehydration['extra_body'], {'thinking': {'type': 'disabled'}})
        self.cfg.provider['base_url'] = 'https://api.openai.com/v1'
        self.assertNotIn('extra_body', resident_config(self.cfg, Path('v'), 'x' * 64)['dehydration'])

    def test_ombre_model_budget_blocks_before_the_backend(self):
        self.budget.daily = Decimal('0.0000001')
        before = len(self.backend.calls)
        with self.assertRaises(UserError):
            self.memory.call('hold', {'content': 'x'})
        self.assertEqual(len(self.backend.calls), before)

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
        self.assertIn('do not claim to remember', empty[-1]['content'])
        self.assertIn('not newly said by the user', empty[-1]['content'])
        self.assertNotRegex('\n'.join(item['content'] for item in empty), r'[\u3400-\u9fff]')
        for i in range(5):
            self.backend.rows[str(i)] = {'content': f'body {i}'}
            self.backend.write_row(str(i))
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
        reopened.command('/forget ' + key + ' 确认')
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
        self.memory.command('/forget ' + key + ' 确认')
        self.backend.fail = False
        reopened = self.open()
        self.assertNotIn(key, '\n'.join(m['content'] for m in reopened.context('plan')))

    def test_merge_into_hidden_memory_keeps_it_hidden(self):
        self.turns(4)
        self.memory.save_item(entry(), self.memory.turns(), 0)
        key = next(iter(self.memory.state['entries']))
        self.memory.command('/forget ' + key)
        self.memory.command('/forget ' + key + ' 确认')
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

    def test_failed_background_extraction_settles_reservation(self):
        self.turns(4)
        self.model.side_effect = RuntimeError('fictional provider timeout')
        self.memory.supplement()
        rows = self.store.read('costs.jsonl')
        reserves = [row for row in rows if row.get('accounting') == 'memory-extraction']
        settled_ids = {row['id'] for row in rows if row['type'] == 'settle'}
        self.assertEqual(len(reserves), 1)
        self.assertIn(reserves[0]['id'], settled_ids)
        self.assertEqual(self.budget.totals()[2], 0)

    def test_background_extraction_keeps_autonomy_budget_category(self):
        self.turns(4)
        self.budget.autonomy_daily = Decimal('0.000001')
        with self.budget.autonomous('fake'):
            self.memory.completed()
        self.memory.jobs.join()
        self.model.assert_not_called()
        self.assertIn('共享预算上限', ' '.join(self.memory.notices()))
        self.assertEqual(self.budget.category_totals('fake')[0], 0)

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
        self.assertIn('not newly said by the user', sent[3]['content'])
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



