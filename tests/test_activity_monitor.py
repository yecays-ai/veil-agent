import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from veil_agent import Store, nowhere_activity_metadata, safe_tool_activity_detail
from web_agent import Hub


class ActivityMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        self.hub = Hub('Test', SimpleNamespace(enabled=lambda: True))
        self.hub.chat = SimpleNamespace(store=self.store, cfg=SimpleNamespace(id='test'))

    def tearDown(self):
        self.temp.cleanup()

    def test_started_and_finished_rows_become_one_live_record(self):
        path = 'test/tools.jsonl'
        self.store.append(path, {'request_id': 'r1', 'call_id': 'r1:0:c1', 'tool': 'read_text_file',
                                 'path': 'lab/notes.md', 'state': '进行中',
                                 'time': '2026-10-25T12:00:01+00:00'})
        running = self.hub.tool_activity()
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0]['state'], '进行中')
        self.store.append(path, {'request_id': 'r1', 'call_id': 'r1:0:c1', 'tool': 'read_text_file',
                                 'path': 'lab/notes.md', 'state': '成功', 'detail': '读取成功',
                                 'result_state': '已截断',
                                 'time': '2026-10-25T12:00:02+00:00'})
        finished = self.hub.tool_activity()
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]['state'], '成功')
        self.assertEqual(finished[0]['result_state'], '已截断')
        self.assertEqual(finished[0]['started_at'], '2026-10-25T12:00:01+00:00')
        self.assertEqual(finished[0]['finished_at'], '2026-10-25T12:00:02+00:00')

    def test_source_and_category_come_from_real_ledger_fields(self):
        self.store.append('test/tools.jsonl', {'request_id': 'r2', 'call_id': 'r2:0:m1', 'tool': 'hold',
                          'path': '新建记忆', 'state': '成功', 'autonomy_run_id': 'wake-1',
                          'time': '2026-10-25T12:01:00+00:00'})
        item = self.hub.tool_activity()[0]
        self.assertEqual(item['source'], '自主活动')
        self.assertEqual(item['category'], 'memory')

    def test_saved_nowhere_say_text_uses_the_existing_tool_ledger(self):
        metadata = nowhere_activity_metadata(
            'mcp__nowhere__say', 'mcp__nowhere__say', {'text': 'A fictional quote'},
            '{"text":"记下了。","data":{"saved":true}}', '成功')
        self.assertEqual(metadata, {'tool_input': 'A fictional quote'})

        truncated = nowhere_activity_metadata(
            'mcp__nowhere__say', 'mcp_nowhere_say', {'text': 'x' * 501},
            '{"data":{"saved":true,"truncated":true}}', '成功')
        self.assertEqual(len(truncated['tool_input']), 500)
        self.assertTrue(truncated['tool_input_truncated'])
        self.assertEqual(truncated['canonical_tool'], 'mcp__nowhere__say')

    def test_unsaved_or_unrelated_mcp_calls_do_not_persist_tool_input(self):
        args = {'text': 'A fictional quote'}
        self.assertEqual(nowhere_activity_metadata(
            'mcp__nowhere__say', 'mcp__nowhere__say', args,
            '{"data":{"saved":false}}', '成功'), {})
        self.assertEqual(nowhere_activity_metadata(
            'mcp__nowhere__say', 'mcp__nowhere__say', args,
            '{"data":{"saved":true}}', '失败'), {})
        self.assertEqual(nowhere_activity_metadata(
            'mcp__nowhere__quotes', 'mcp__nowhere__quotes', args,
            '{"data":{"saved":true}}', '成功'), {})

    def test_activity_projection_keeps_optional_say_input_role_scoped(self):
        self.store.append('test/tools.jsonl', {'request_id': 'r3', 'call_id': 'r3:0:say',
            'tool': 'mcp__nowhere__say', 'path': 'mcp__nowhere__say', 'state': '成功',
            'detail': '{"data":{"saved":true}}',
            **nowhere_activity_metadata('mcp__nowhere__say', 'quote_alias',
                {'text': 'A fictional quote'}, '{"data":{"saved":true}}', '成功'),
            'autonomy_run_id': 'wake-3', 'time': '2026-10-31T12:03:00+00:00'})
        self.store.append('peter/tools.jsonl', {'request_id': 'other', 'call_id': 'private',
            'tool': 'mcp__nowhere__say', 'state': '成功', 'tool_input': 'other role text'})
        rows = self.hub.tool_activity()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['tool_input'], 'A fictional quote')
        self.assertEqual(rows[0]['canonical_tool'], 'mcp__nowhere__say')
        self.assertEqual(rows[0]['source'], '自主活动')

    def test_common_credentials_are_redacted_before_persistence(self):
        detail = safe_tool_activity_detail('api_key=secret-value password: hunter2 bearer abcdefghijklmnop')
        self.assertNotIn('secret-value', detail)
        self.assertNotIn('hunter2', detail)
        self.assertNotIn('abcdefghijklmnop', detail)
        self.assertIn('[已隐藏]', detail)
        natural = safe_tool_activity_detail('api_key is AABBCCDDEEFF')
        self.assertNotIn('AABBCCDDEEFF', natural)
        self.assertIn('[已隐藏]', natural)
        multiword = safe_tool_activity_detail('api_key is abcd efgh')
        self.assertNotIn('abcd', multiword)
        self.assertNotIn('efgh', multiword)
        authorization = safe_tool_activity_detail('Authorization: Bearer fictional-token-value')
        self.assertNotIn('fictional-token-value', authorization)
        self.assertIn('[已隐藏]', authorization)
        ordinary = 'authorization is required to access the API'
        self.assertEqual(safe_tool_activity_detail(ordinary), ordinary)

    def test_json_credentials_are_redacted_in_objects_strings_and_old_ledger_rows(self):
        for value in ({'password': 'hunter2', 'nested': {'client_secret': 'secret-value'}},
                      '{"password":"hunter2","nested":{"refresh_token":"token-value"}}'):
            detail = safe_tool_activity_detail(value)
            for secret in ('hunter2', 'secret-value', 'token-value'):
                self.assertNotIn(secret, detail)
            self.assertIn('[已隐藏]', detail)

        self.store.append('test/tools.jsonl', {'call_id': 'old', 'tool': 'read_text_file',
            'path': 'lab/notes.md', 'state': '成功', 'detail': '{"password":"hunter2"}'})
        item = self.hub.tool_activity()[0]
        self.assertNotIn('hunter2', item['detail'])

    def test_unchanged_ledger_is_not_reread_on_every_poll(self):
        self.store.append('test/tools.jsonl', {'call_id': 'c1', 'tool': 'read_text_file',
                          'path': 'lab/a.md', 'state': '成功'})
        first = self.hub.tool_activity()
        calls = []
        original = self.store.read_tail
        self.store.read_tail = lambda *args: calls.append(args) or original(*args)
        self.assertIs(self.hub.tool_activity(), first)
        self.assertEqual(calls, [])
        self.store.append('test/tools.jsonl', {'call_id': 'c2', 'tool': 'hold',
                          'path': 'new', 'state': '成功'})
        self.assertEqual(len(self.hub.tool_activity()), 2)
        self.assertEqual(len(calls), 1)

    def test_store_read_tail_parses_only_the_requested_recent_records(self):
        path = self.store.root / 'test' / 'tools.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('not-json\n{"n":1}\n{"n":2}\n', encoding='utf-8')
        self.assertEqual(self.store.read_tail('test/tools.jsonl', 2), [{'n': 1}, {'n': 2}])


if __name__ == '__main__':
    unittest.main()
