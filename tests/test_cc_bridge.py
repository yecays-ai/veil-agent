"""Ticket 227: the isolated Claude Code experiment (experiments/cc-bridge). Fake data only, no network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / 'experiments' / 'cc-bridge'
sys.path.insert(0, str(BRIDGE))

import run_cc_experiment as runner  # noqa: E402
import veil_bridge_mcp as bridge  # noqa: E402

GARDEN_TOOLS = ['list_games', 'join_game', 'get_my_status', 'start_game', 'submit_action', 'send_game_chat',
                'get_chat_messages', 'send_chat_message', 'withdraw_chat_message', 'nostos_start', 'nostos_status',
                'nostos_act', 'get_tool_schema', 'get_game_summary', 'leave_waiting_game', 'get_self',
                'update_profile', 'decorate_avatar', 'get_machine', 'list_threads', 'get_thread', 'create_thread',
                'create_reply', 'delete_thread', 'delete_reply', 'interact', 'list_notifications', 'list_activity',
                'review_drift_bottles']


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chat, self.received = bridge.build_chat(Path(__file__).resolve().parents[1], self.tmp.name, '假的用户消息')

    def rows(self):
        return self.chat.store.read('test/chat.jsonl')

    def test_each_send_message_is_its_own_saved_bubble(self):
        for text in ('one', 'two', 'three'):
            result, state, _ = self.chat.send_message(json.dumps({'text': text}), 'req-1')
            self.assertEqual((result, state), ('Sent.', '成功'))
        assistant = [r for r in self.rows() if r['role'] == 'assistant']
        self.assertEqual([r['content'] for r in assistant], ['one', 'two', 'three'])
        self.assertEqual(len({r['message_id'] for r in assistant}), 3)
        shown = [json.loads(line)['text'] for line in self.received.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(shown, ['Peter: one', 'Peter: two', 'Peter: three'])

    def test_done_and_stay_silent_end_the_turn(self):
        self.chat.send_message(json.dumps({'text': 'bye', 'done': True}), 'req-2')
        self.assertTrue(self.chat.turn_done)
        other_dir = tempfile.TemporaryDirectory()
        self.addCleanup(other_dir.cleanup)
        other, _ = bridge.build_chat(Path(__file__).resolve().parents[1], other_dir.name)
        result, state, _ = other.stay_silent()
        self.assertEqual(state, '成功')
        self.assertTrue(other.turn_done)

    def test_sandbox_never_calls_a_model(self):
        with self.assertRaises(RuntimeError):
            self.chat.model([])


class GardenAllowlistTests(unittest.TestCase):
    def test_nothing_that_writes_or_changes_state_is_allowed(self):
        allowed = runner.allowed_garden_tools(GARDEN_TOOLS)
        for name in ('join_game', 'start_game', 'submit_action', 'send_game_chat', 'send_chat_message',
                     'withdraw_chat_message', 'nostos_start', 'nostos_act', 'nostos_status', 'leave_waiting_game',
                     'update_profile', 'decorate_avatar', 'create_thread', 'create_reply', 'delete_thread',
                     'delete_reply', 'interact', 'review_drift_bottles', 'list_notifications'):
            self.assertNotIn(name, allowed)
        self.assertEqual(sorted(allowed), sorted(runner.GARDEN_READ_ONLY))
        self.assertEqual(len(allowed), 10)

    def test_a_new_tool_with_a_read_like_name_is_not_allowed(self):
        discovered = ['get_thread', 'get_and_mark_everything_read', 'list_and_delete', 'search_posts']
        self.assertEqual(runner.allowed_garden_tools(discovered), ['get_thread'])


class StopHookTests(unittest.TestCase):
    def run_hook(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'hook.py'
            path.write_text(runner.ptu_hook_source(), encoding='utf-8')
            out = subprocess.run([sys.executable, '-I', str(path)], input=json.dumps(payload), capture_output=True,
                                 text=True, check=True).stdout
        return json.loads(out)

    def test_both_ending_tools_stop_the_turn(self):
        stay = self.run_hook({'tool_name': 'mcp__veil_bridge__stay_silent', 'tool_input': {}})
        done = self.run_hook({'tool_name': 'mcp__veil_bridge__send_message', 'tool_input': {'text': 'x', 'done': True}})
        for out in (stay, done):
            self.assertIs(out['continue'], False)

    def test_other_calls_only_get_the_reminder(self):
        for payload in ({'tool_name': 'mcp__veil_bridge__send_message', 'tool_input': {'text': 'x'}},
                        {'tool_name': 'mcp__galatea_garden__list_threads', 'tool_input': {}}):
            out = self.run_hook(payload)
            self.assertNotIn('continue', out)
            self.assertEqual(out['hookSpecificOutput']['hookEventName'], 'PostToolUse')


class EnvTests(unittest.TestCase):
    def test_unrelated_credentials_are_not_passed_on(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k', 'OPENAI_API_KEY': 'k', 'GITHUB_TOKEN': 'k', 'PATH': '/bin'}):
            env = runner.minimal_env()
        for name in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GITHUB_TOKEN'):
            self.assertNotIn(name, env)
        self.assertEqual(env['PATH'], '/bin')


class StyleInputTests(unittest.TestCase):
    def test_chat_tail_skips_autonomy_rows_and_keeps_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'chat.jsonl'
            rows = [{'role': 'user', 'content': 'q1'}, {'role': 'assistant', 'content': 'a1'},
                    {'role': 'assistant', 'content': 'wake', 'autonomy_run_id': 'x'}, {'role': 'user', 'content': 'q2'}]
            path.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
            self.assertEqual(runner.tail_chat(path, 10), 'lan: q1\nPeter: a1\nlan: q2')


if __name__ == '__main__':
    unittest.main()
