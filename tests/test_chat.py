import json
import signal
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from veil_agent import Chat, Budget, Store, build_self_description, load_character, make_model, run_chat, UserError


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
''', encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store)
        self.model = Mock(return_value=('A fictional reply', 100, 20, 10))
        self.chat = Chat(self.cfg, self.store, self.budget, self.model)

    def test_saved_raw_text_and_reopened_context(self):
        self.chat.send('虚构的中文消息')
        reopened = Chat(self.cfg, self.store, self.budget, self.model)
        self.assertEqual(reopened.rounds, 1)
        reopened.send('Continue')
        messages = self.model.call_args.args[0]
        self.assertEqual([m['content'] for m in messages[2:]], ['虚构的中文消息', 'A fictional reply', 'Continue'])
        rows = self.store.read('test/chat.jsonl')
        self.assertEqual([r['role'] for r in rows], ['user', 'assistant', 'user', 'assistant'])
        self.assertTrue(all(r['time'] for r in rows))

    def test_budget_reserves_before_network_and_settles_actual_usage(self):
        def reply(messages):
            self.assertGreater(self.budget.totals()[2], 0)
            return 'ok', 100, 20, 10
        self.chat.model = reply
        self.chat.send('hello')
        day, month, pending = self.budget.totals()
        self.assertEqual(day, Decimal('0.000350'))
        self.assertEqual(month, day)
        self.assertEqual(pending, 0)

    def test_daily_and_monthly_limits_block_before_request(self):
        for daily, monthly in [('0.001', '300'), ('10', '0.001')]:
            with self.subTest(daily=daily):
                budget = Budget(self.store, Decimal(daily), Decimal(monthly))
                chat = Chat(self.cfg, self.store, budget, self.model)
                with self.assertRaises(UserError):
                    chat.send('hello')
        self.model.assert_not_called()

    def test_missing_persona_never_starts(self):
        (self.root / 'persona' / 'test.md').unlink()
        with self.assertRaises(UserError):
            load_character(self.root, 'test')
        self.model.assert_not_called()

    def test_interrupt_during_request_keeps_input_and_reservation(self):
        self.model.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.chat.send('keep me')
        self.assertEqual(self.store.read('test/chat.jsonl')[0]['content'], 'keep me')
        self.assertGreater(Budget(self.store).totals()[2], 0)

    def test_ctrl_c_at_prompt_keeps_displayed_reply(self):
        output = []
        inputs = Mock(side_effect=['hello', KeyboardInterrupt])
        run_chat(self.chat, inputs, output.append)
        self.assertIn('Test: A fictional reply', output)
        self.assertEqual(self.store.read('test/chat.jsonl')[-1]['content'], 'A fictional reply')

    def test_keys_typed_while_waiting_are_dropped_with_notice(self):
        output = []
        inputs = Mock(side_effect=['hello', '/quit'])
        run_chat(self.chat, inputs, output.append, drop=lambda: True)
        self.assertEqual(output[1:], ['Test: A fictional reply', '（等回复时打的字没有收到，请重新输入。）'])
        output.clear()
        inputs = Mock(side_effect=['hello', '/quit'])
        run_chat(self.chat, inputs, output.append, drop=lambda: False)
        self.assertEqual(output[1:], ['Test: A fictional reply'])

    def test_failed_request_not_retried_and_budget_survives_restart(self):
        self.model.side_effect = RuntimeError('secret must not be displayed')
        with self.assertRaises(UserError):
            self.chat.send('test')
        self.assertEqual(self.model.call_count, 1)
        self.assertGreater(Budget(Store(self.root / 'data')).totals()[2], 0)

    def test_roles_use_separate_history_but_share_budget(self):
        self.chat.send('private test')
        self.assertEqual(self.store.read('other/chat.jsonl'), [])
        self.assertGreater(Budget(Store(self.root / 'data')).totals()[0], 0)

    def test_history_window_preserves_full_ledger(self):
        self.cfg.history_rounds = 2
        for n in range(4):
            self.chat.send(str(n))
        reopened = Chat(self.cfg, self.store, self.budget, self.model)
        self.assertEqual(reopened.rounds, 2)
        self.assertEqual([m['content'] for m in reopened.history if m['role'] == 'user'], ['2', '3'])
        self.assertEqual(len(self.store.read('test/chat.jsonl')), 8)

    def test_too_long_input_never_sends_or_reserves(self):
        with self.assertRaises(UserError):
            self.chat.send('中文' * 2000)
        self.model.assert_not_called()
        self.assertEqual(self.budget.totals(), (0, 0, 0))

    def test_missing_usage_keeps_upper_bound_reserved(self):
        self.model.return_value = ('reply', None, None, None)
        with self.assertRaises(UserError):
            self.chat.send('hello')
        self.assertGreater(self.budget.totals()[2], 0)
        self.assertEqual(self.store.read('test/chat.jsonl')[-1]['content'], 'reply')

    def test_ctrl_c_during_reply_save_finishes_reply_and_cost(self):
        append = self.store.append
        def interrupting_append(path, event):
            if event.get('role') == 'assistant':
                signal.raise_signal(signal.SIGINT)
            append(path, event)
        with patch.object(self.store, 'append', side_effect=interrupting_append):
            with self.assertRaises(KeyboardInterrupt):
                self.chat.send('hello')
        self.assertEqual(self.store.read('test/chat.jsonl')[-1]['role'], 'assistant')
        self.assertEqual(self.budget.totals()[2], 0)

    def test_unresolved_reservation_carries_into_new_day_and_month(self):
        self.budget.clock = lambda: '2026-01-31T23:59:00+08:00'
        self.budget.reserve(self.cfg)
        amount = self.budget.totals()[2]
        self.budget.clock = lambda: '2026-02-01T00:01:00+08:00'
        self.assertEqual(self.budget.totals(), (0, 0, amount))

    def test_settled_usage_month_rollover(self):
        self.budget.clock = lambda: '2026-01-31T23:59:00+08:00'
        self.chat.send('hello')
        self.budget.clock = lambda: '2026-02-01T00:01:00+08:00'
        self.assertEqual(self.budget.totals(), (0, 0, 0))

    def test_process_lock_blocks_second_window_and_releases(self):
        script = 'from pathlib import Path; from veil_agent import Store; import sys; s=Store(Path(sys.argv[1])); lock=s.lock(); lock.__enter__()'
        def another_window():
            return subprocess.run([sys.executable, '-c', script, str(self.store.root)], capture_output=True, text=True)
        with self.store.lock():
            self.assertNotEqual(another_window().returncode, 0)
        self.assertEqual(another_window().returncode, 0)

    def test_corrupt_and_torn_ledger_fail_closed(self):
        path = self.store.root / 'costs.jsonl'
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(UserError):
            self.chat.send('hello')
        self.model.assert_not_called()
        path.write_text('{}', encoding='utf-8')
        with self.assertRaises(UserError):
            self.store.append('costs.jsonl', {'type': 'reserve'})
        self.assertEqual(path.read_text(encoding='utf-8'), '{}')

    def test_real_sdk_serialization_usage_and_no_retry_without_network(self):
        import httpx2
        from openai import OpenAI
        (self.root / '.env').write_text('FAKE_KEY=fake-test-only\n', encoding='utf-8')
        requests = []
        fail = []
        def transport(request):
            requests.append(json.loads(request.content))
            if fail:
                return httpx2.Response(500, json={'error': {'message': 'fake secret'}})
            return httpx2.Response(200, json={
                'id': 'fake', 'object': 'chat.completion', 'created': 0, 'model': 'fake-model',
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '模拟回复'}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
                          'prompt_cache_hit_tokens': 10}})
        def factory(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0)
            return OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
        with patch('openai.OpenAI', side_effect=factory):
            self.cfg.provider['thinking'] = 'disabled'
            client, model = make_model(self.root, self.cfg)
        with client:
            chat = Chat(self.cfg, self.store, self.budget, model)
            self.assertEqual(chat.send('中文输入'), '模拟回复')
            self.assertEqual(requests[0]['messages'][-1]['content'], '中文输入')
            self.assertEqual([m['role'] for m in requests[0]['messages']], ['system', 'system', 'user'])
            self.assertEqual(requests[0]['messages'][0]['content'], self.cfg.persona)
            self.assertEqual(requests[0]['messages'][1]['content'], chat.last_self)
            self.assertEqual(requests[0]['max_tokens'], 50)
            self.assertFalse(requests[0]['stream'])
            self.assertEqual(requests[0]['thinking'], {'type': 'disabled'})
            model([{'role': 'user', 'content': 'Return JSON'}], json_output=True)
            self.assertEqual(requests[1]['response_format'], {'type': 'json_object'})
            self.cfg.provider['thinking'] = 'enabled'
            answer = model([{'role': 'user', 'content': 'Return JSON'}], json_output=True,
                           max_tokens=777, thinking='disabled', detail=True)
            self.assertEqual(requests[2]['max_tokens'], 777)
            self.assertEqual(requests[2]['thinking'], {'type': 'disabled'})
            self.assertEqual((len(answer), answer[4]), (5, 'stop'))
            fail.append(True)
            with self.assertRaises(UserError) as result:
                chat.send('not retried')
            self.assertNotIn('secret', str(result.exception))
        self.assertEqual(len(requests), 4)

    def test_system_environment_key_is_not_used(self):
        with patch.dict('os.environ', {'FAKE_KEY': 'fake-env-only'}):
            with self.assertRaises(UserError):
                make_model(self.root, self.cfg)

    def test_role_paths_cannot_escape_character_directory(self):
        for character in ['../test', 'test/../../test', 'TEST', 'C:test']:
            with self.subTest(character=character), self.assertRaises(UserError):
                load_character(self.root, character)

    def test_budget_includes_prior_spending_before_next_request(self):
        request_id, upper = self.budget.reserve(self.cfg)
        self.budget.settle(request_id, self.cfg, 100, 20, 10)
        spent = self.budget.totals()[0]
        budget = Budget(self.store, daily=upper + spent - Decimal('0.000001'))
        with self.assertRaises(UserError):
            Chat(self.cfg, self.store, budget, self.model).send('hello')
        self.model.assert_not_called()

    def test_self_facts_follow_persona_and_use_selected_role_and_model(self):
        config = self.root / 'characters' / 'test' / 'character.toml'
        config.write_text(config.read_text(encoding='utf-8').replace('name = "Test"', 'name = "Lumen"')
                          .replace('fake', 'alternate'), encoding='utf-8')
        cfg = load_character(self.root, 'test')
        chat = Chat(cfg, self.store, self.budget, self.model)
        self.budget.clock = lambda: '2026-10-19T09:10:00+08:00'
        chat.send('Who are you?')
        messages = self.model.call_args.args[0]
        self.assertEqual(messages[0], {'role': 'system', 'content': cfg.persona})
        facts = messages[1]
        self.assertEqual(facts['role'], 'system')
        for value in ['program-generated', 'not user-authored', 'AI companion', 'Lumen', 'alternate',
                      'alternate-model', '2026-10-19T09:10:00+08:00', 'Monday', '20 recent rounds',
                      'no long-term memory', 'no tools', 'no voice', 'Unlisted abilities are unavailable']:
            self.assertIn(value, facts['content'])

    def test_self_uses_same_cost_source_and_actual_budget_limits(self):
        self.budget = Budget(self.store, daily=Decimal('3'), monthly=Decimal('80'))
        request_id, _ = self.budget.reserve(self.cfg)
        self.budget.settle(request_id, self.cfg, 100, 20, 10)
        self.budget.reserve(self.cfg)  # An unresolved earlier request must also be visible.
        expected_cost = self.budget.display()
        self.assertIn('/ 3', expected_cost)
        self.assertIn('/ 80', expected_cost)
        chat = Chat(self.cfg, self.store, self.budget, self.model)
        chat.send('What have we spent?')
        facts = self.model.call_args.args[0][1]['content']
        self.assertIn(expected_cost, facts)
        self.assertIn('excludes this reply', facts)
        self.assertNotEqual(self.budget.display(), expected_cost)

    def test_self_command_shows_exact_last_sent_facts_without_new_request(self):
        self.budget.clock = lambda: '2026-10-19T09:10:00+08:00'
        self.chat.send('hello')
        sent = self.model.call_args.args[0][1]['content']
        before_rows = self.store.read('test/chat.jsonl')
        before_costs = self.store.read('costs.jsonl')
        self.budget.clock = lambda: '2026-10-20T09:10:00+08:00'
        output = []
        run_chat(self.chat, Mock(side_effect=['/self', '/cost', '/self', '/quit']), output.append)
        self.assertEqual(output[1], sent)
        self.assertEqual(output[2], self.budget.display())
        self.assertEqual(output[3], sent)
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.store.read('test/chat.jsonl'), before_rows)
        self.assertEqual(self.store.read('costs.jsonl'), before_costs)

    def test_self_before_first_request_is_labeled_as_preview_and_costs_nothing(self):
        output = []
        run_chat(self.chat, Mock(side_effect=['/self', '/quit']), output.append)
        self.assertIn('预览', output[1])
        self.assertIn('Test', output[2])
        self.model.assert_not_called()
        self.assertEqual(self.store.read('costs.jsonl'), [])
        self.assertEqual(self.store.read('test/chat.jsonl'), [])

    def test_self_refreshes_each_request_and_never_enters_chat_ledger(self):
        self.budget.clock = lambda: '2026-10-19T09:10:00+08:00'
        self.chat.send('first')
        first = self.model.call_args.args[0][1]['content']
        self.budget.clock = lambda: '2026-10-20T09:10:00+08:00'
        self.cfg.history_rounds = 3
        self.chat.send('second')
        messages = self.model.call_args.args[0]
        second = messages[1]['content']
        self.assertNotEqual(first, second)
        self.assertIn('Tuesday', second)
        self.assertIn('3 recent rounds', second)
        self.assertEqual(sum(m['role'] == 'system' for m in messages), 2)
        self.assertEqual([r['role'] for r in self.store.read('test/chat.jsonl')],
                         ['user', 'assistant', 'user', 'assistant'])

    def test_self_excludes_keys_paths_persona_and_api_config(self):
        self.cfg.persona = 'A private fictional persona that must not be repeated in /self'
        self.cfg.provider['api_key'] = 'FAKE-KEY-NEVER-REAL'
        (self.root / '.env').write_text('FAKE_KEY=another-fake-key\n', encoding='utf-8')
        self.chat.send('hello')
        facts = self.model.call_args.args[0][1]['content']
        for private in [self.cfg.persona, str(self.root), self.cfg.provider['base_url'],
                        self.cfg.provider['api_key_env'], 'FAKE-KEY-NEVER-REAL', 'another-fake-key']:
            self.assertNotIn(private, facts)

    def fill_history(self, rounds, size):
        for i in range(rounds):
            self.store.append('test/chat.jsonl', {'role': 'user', 'content': f'u{i} ' + 'x' * size})
            self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': f'a{i} ' + 'y' * size})

    def test_long_history_drops_oldest_rounds_instead_of_stopping(self):
        self.cfg.history_rounds = 20
        self.fill_history(20, 600)  # About 24 KB of history against a 6000-byte limit.
        chat = Chat(self.cfg, self.store, self.budget, self.model)
        output = []
        run_chat(chat, Mock(side_effect=['continue', 'again', '/quit']), output.append, drop=lambda: False)
        sent = self.model.call_args_list[0].args[0]
        roles = [m['role'] for m in sent]
        self.assertEqual(roles[0], 'system')
        self.assertEqual(roles[-1], 'user')
        self.assertEqual(sent[-1]['content'], 'continue')
        history = [m for m in sent[2:-1]]
        self.assertTrue(history and history[0]['role'] == 'user')  # Whole rounds only.
        self.assertTrue(history[-1]['content'].startswith('a19'))  # Newest kept.
        self.assertLessEqual(sum(len(m['content'].encode()) + 256 for m in sent) + 256,
                             self.cfg.provider['max_input_tokens'])
        self.assertEqual(len([o for o in output if '这次只带了最近' in o]), 1)
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(len(self.store.read('test/chat.jsonl')), 40 + 4)  # Nothing erased.

    def test_oversized_input_is_not_fatal_and_is_not_saved(self):
        output = []
        before = self.store.read('test/chat.jsonl')
        run_chat(self.chat, Mock(side_effect=['z' * 7000, 'short', '/quit']), output.append, drop=lambda: False)
        self.assertTrue(any('太长' in o for o in output))
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.model.call_args.args[0][-1]['content'], 'short')
        rows = self.store.read('test/chat.jsonl')
        self.assertNotIn('z' * 7000, [r['content'] for r in rows])
        self.assertEqual(len(rows), len(before) + 2)

    def test_input_budget_includes_self_facts_before_any_request(self):
        self.chat.send('hello')
        messages = self.model.call_args.args[0]
        self.assertEqual(messages[1]['role'], 'system')
        old_messages = [messages[0], messages[-1]]
        old_bound = sum(len(m['content'].encode('utf-8')) + 256 for m in old_messages) + 256
        self.cfg.provider['max_input_tokens'] = old_bound
        self.model.reset_mock()
        rows, costs = self.store.read('test/chat.jsonl'), self.store.read('costs.jsonl')
        reopened = Chat(self.cfg, self.store, self.budget, self.model)
        reopened.history = []  # Isolate the added facts from prior conversation length.
        with self.assertRaises(UserError):
            reopened.send('hello')
        self.model.assert_not_called()
        self.assertEqual(self.store.read('test/chat.jsonl'), rows)
        self.assertEqual(self.store.read('costs.jsonl'), costs)

    def test_self_clock_and_costs_use_one_local_date_at_midnight(self):
        self.budget.clock = lambda: '2026-10-19T23:59:00+08:00'
        request_id, _ = self.budget.reserve(self.cfg)
        self.budget.settle(request_id, self.cfg, 100, 20, 10)
        expected = self.budget.display()
        clock = Mock(side_effect=['2026-10-19T23:59:59+08:00', '2026-10-20T00:00:00+08:00'])
        self.budget.clock = clock
        facts = build_self_description(self.cfg, self.budget)
        self.assertIn('2026-10-19T23:59:59+08:00', facts)
        self.assertIn(expected, facts)
        self.assertEqual(clock.call_count, 1)

    def test_rejected_request_keeps_last_actually_sent_self(self):
        self.chat.send('first')
        sent = self.model.call_args.args[0][1]['content']
        self.budget.daily = Decimal('0.000001')
        with self.assertRaises(UserError):
            self.chat.send('not sent')
        self.assertEqual(self.chat.last_self, sent)
        self.assertEqual(self.model.call_count, 1)


if __name__ == '__main__':
    unittest.main()
