"""Ticket 227: the isolated Claude Code experiment (experiments/cc-bridge). Fake data only, no network."""
import json
import sys
import tempfile
import unittest
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
    def allowed(self):
        return [n for n in GARDEN_TOOLS if runner.READ_ONLY.match(n) and n not in runner.DENY]

    def test_nothing_that_writes_or_changes_state_is_allowed(self):
        allowed = self.allowed()
        for name in ('join_game', 'start_game', 'submit_action', 'send_game_chat', 'send_chat_message',
                     'withdraw_chat_message', 'nostos_start', 'nostos_act', 'nostos_status', 'leave_waiting_game',
                     'update_profile', 'decorate_avatar', 'create_thread', 'create_reply', 'delete_thread',
                     'delete_reply', 'interact', 'review_drift_bottles', 'list_notifications'):
            self.assertNotIn(name, allowed)
        self.assertIn('list_threads', allowed)
        self.assertIn('get_thread', allowed)
        self.assertEqual(len(allowed), 10)


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
