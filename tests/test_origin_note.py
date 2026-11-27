"""Ticket 360 fixture: a message Peter sent on his own carries a short program-written p.s. for the model only."""
import json
import tempfile
import unittest
from pathlib import Path

from veil.autonomy.autonomy import origin_note
from veil.core.veil_agent import Budget, Character, Chat, Store

NOW = '2026-11-12T09:00:00+08:00'
OK = '成功'


class OriginNoteTests(unittest.TestCase):
    def test_garden_tools_become_a_place(self):
        tools = [{'tool': 'mcp__galatea_garden__create_reply', 'path': 'x', 'state': OK},
                 {'tool': 'send_message', 'path': 'hi', 'state': OK}]
        self.assertEqual(origin_note(tools), 'the garden forum')

    def test_island_and_forum_both_named_failed_tools_ignored(self):
        tools = [{'tool': 'mcp__galatea_garden__nostos_status', 'path': '', 'state': OK},
                 {'tool': 'mcp__galatea_garden__list_threads', 'path': '', 'state': OK},
                 {'tool': 'web_search', 'path': '', 'state': '出错'}]
        self.assertEqual(origin_note(tools), 'the garden forum, the garden island game')

    def test_nothing_notable_gives_no_note(self):
        self.assertEqual(origin_note([{'tool': 'send_message', 'path': '', 'state': OK}]), '')
        self.assertEqual(origin_note([]), '')

    def test_lab_write_and_unknown_service(self):
        tools = [{'tool': 'write_file', 'path': 'a.md', 'state': OK},
                 {'tool': 'mcp__newthing__do', 'path': '', 'state': OK}]
        self.assertEqual(origin_note(tools), 'another service, your lab')

    def test_alias_is_resolved(self):
        tools = [{'tool': 'alias_reply', 'path': '', 'state': OK}]
        self.assertEqual(origin_note(tools, lambda n: 'mcp__galatea_garden__create_reply'), 'the garden forum')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 60000, 'input_cny_per_million': 2,
                    'output_cny_per_million': 8, 'cached_input_cny_per_million': 1}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake')
        self.sent = []

    def chat(self, origin):
        path = Path(self.store.root) / 'fake' / 'chat.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{'time': '2026-11-12T03:10:00+08:00', 'role': 'assistant', 'content': '你看看那个帖子？',
                 'message_id': 'p0', 'autonomy_run_id': 'run0', 'autonomy_tool_call_id': 'c'},
                {'time': '2026-11-12T03:10:01+08:00', 'role': 'assistant', 'content': '普通回复', 'message_id': 'a1'}]
        if origin:
            rows[0]['autonomy_origin'] = origin
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')

        def model(messages, **kwargs):
            self.sent.append([dict(m) for m in messages])
            return '嗯', 1000, 20, 800
        return Chat(self.cfg, self.store, Budget(self.store, clock=lambda: NOW), model)

    def test_model_sees_the_ps_but_lan_text_is_unchanged(self):
        chat = self.chat('the garden forum')
        chat.send('你怎么想起发这个')
        contents = [m['content'] for m in self.sent[-1]]
        own = next(c for c in contents if c.startswith('你看看那个帖子？'))
        self.assertIn('[p.s. sent on your own at 2026-11-12T03:10:00+08:00 during an autonomous wake, after: the garden forum]', own)
        self.assertIn('普通回复', contents)  # an ordinary reply gets no note
        stored = [json.loads(l) for l in (Path(self.store.root) / 'fake' / 'chat.jsonl').read_text(
            encoding='utf-8').splitlines()]
        self.assertEqual(stored[0]['content'], '你看看那个帖子？')

    def test_message_without_origin_still_shows_wake_time(self):
        chat = self.chat('')
        chat.send('hi')
        self.assertTrue(any('你看看那个帖子？\n[p.s. sent on your own at 2026-11-12T03:10:00+08:00' in m['content']
                            for m in self.sent[-1]))


if __name__ == '__main__':
    unittest.main()
