import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from background import (LIMITS, NOTE, append_context_log, background_context, chunk_paragraph,
                        select_background, split_background)
from veil_agent import Budget, Character, Chat, Store

LIBRARY = '''Queens is home. Small apartment, broken radiator, rent that's always a little overdue.
Keywords: Queens, 皇后区, apartment

May raised me. Her wheatcakes are the best food in New York.
关键词：May、梅姨、wheatcakes

I make games nobody asked for. Find the Bug reshuffles every round.
Keywords: game, 小游戏

[常驻] Lately I'm obsessed with a desktop pet.

Tardigrades are tiny vacuum-proof bears and I love them.
'''


class SelectionTests(unittest.TestCase):
    def pick(self, current, recent=''):
        _, retrievable = split_background(LIBRARY)
        chosen, _ = select_background(retrievable, current, recent)
        return [(item['chunk']['paragraph'], item['via']) for item in chosen]

    def test_keyword_lines_are_removed_and_core_is_separate(self):
        core, retrievable = split_background(LIBRARY)
        self.assertEqual([item['text'] for item in core], ["Lately I'm obsessed with a desktop pet."])
        self.assertTrue(all('Keywords' not in item['text'] and '关键词' not in item['text'] for item in retrievable))
        self.assertEqual(retrievable[1]['keywords'], ['May', '梅姨', 'wheatcakes'])

    def test_english_keyword_needs_a_whole_word(self):
        self.assertEqual(self.pick("how's May doing?"), [(2, 'keyword+text')])
        self.assertEqual(self.pick('this game looks fun'), [(3, 'keyword')])
        self.assertEqual(self.pick('the gamer tag is weird'), [])
        self.assertEqual(self.pick('Maybe later'), [])

    def test_chinese_keyword_matches_as_substring(self):
        self.assertEqual(self.pick('你想梅姨吗'), [(2, 'keyword')])

    def test_body_text_matches_without_a_keyword(self):
        self.assertEqual(self.pick('is your radiator still broken?'), [(1, 'text')])
        self.assertEqual(self.pick('I read about tardigrades today'), [(5, 'text')])

    def test_recent_rounds_need_two_shared_words(self):
        self.assertEqual(self.pick('ok', recent='the radiator'), [])
        self.assertEqual(self.pick('ok', recent='broken radiator again'), [(1, 'text')])

    def test_unrelated_message_picks_nothing(self):
        self.assertEqual(self.pick('what should we name the cat'), [])

    def test_at_most_three_items_and_the_character_limit(self):
        library = '\n\n'.join(f'Paragraph {i} about pizza.' for i in range(6))
        _, retrievable = split_background(library)
        chosen, _ = select_background(retrievable, 'pizza', '')
        self.assertEqual(len(chosen), LIMITS['items'])
        long = {'index': 1, 'text': ('A sentence about pizza. ' * 100).strip(), 'keywords': ['pizza']}
        chunks, skipped = chunk_paragraph(long)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk['text']) <= LIMITS['chunk'] for chunk in chunks))
        self.assertEqual(skipped, 0)
        chosen, _ = select_background([long], 'pizza', '')
        self.assertLessEqual(len(chosen), LIMITS['chunks_per_paragraph'])
        self.assertLessEqual(sum(len(item['chunk']['text']) for item in chosen), LIMITS['characters'])


class InjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = Store(root / 'data')
        self.library = root / 'peter.background.md'
        self.library.write_text(LIBRARY, encoding='utf-8')
        provider = {'model': 'fake', 'max_input_tokens': 60000, 'input_cny_per_million': 2,
                    'output_cny_per_million': 8, 'cached_input_cny_per_million': 1}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
                             background=str(self.library))
        self.budget = Budget(self.store, clock=lambda: '2026-10-25T09:00:00+08:00')
        self.sent = []

        def model(messages, **kwargs):
            self.sent.append([dict(m) for m in messages])
            return 'reply', 1000, 20, 800
        self.model = model

    def contents(self, request):
        return [m['content'] for m in request]

    def test_background_sits_after_history_and_before_recalled_memory(self):
        memory = Mock()
        memory.context.return_value = [{'role': 'system', 'content': 'recalled: blue'}]
        memory.last_context_refs = ['m1']
        chat = Chat(self.cfg, self.store, self.budget, self.model, memory)
        chat.send('hello')
        chat.send("how's May doing?")
        request = self.contents(self.sent[-1])
        history_end = request.index('reply')
        background = next(i for i, c in enumerate(request) if c.startswith('[background]'))
        self.assertGreater(background, history_end)
        self.assertLess(background, request.index('recalled: blue'))
        self.assertIn(NOTE, request[background])
        self.assertIn('wheatcakes', request[background])
        self.assertIn('desktop pet', request[background])
        self.assertNotIn('radiator', request[background])
        self.assertNotIn('关键词', request[background])

    def test_core_paragraph_is_sent_every_request(self):
        chat = Chat(self.cfg, self.store, self.budget, self.model)
        chat.send('what should we name the cat')
        self.assertTrue(any('desktop pet' in c for c in self.contents(self.sent[-1])))

    def test_file_is_read_again_and_missing_file_is_fine(self):
        chat = Chat(self.cfg, self.store, self.budget, self.model)
        chat.send("how's May doing?")
        self.library.write_text('May taught me to bake.\nKeywords: May', encoding='utf-8')
        chat.send("how's May doing?")
        self.assertTrue(any('taught me to bake' in c for c in self.contents(self.sent[-1])))
        self.library.unlink()
        self.assertEqual(chat.send("how's May doing?"), 'reply')
        self.assertFalse(any(c.startswith('[background]') for c in self.contents(self.sent[-1])))

    def test_context_log_has_sizes_and_ids_but_no_text(self):
        memory = Mock()
        memory.context.return_value = []
        memory.last_context_refs = ['m1']
        chat = Chat(self.cfg, self.store, self.budget, self.model, memory)
        chat.send('is your radiator still broken?')
        row = self.store.read('fake/context-log.jsonl')[-1]
        self.assertEqual(row['kind'], 'chat')
        self.assertEqual(row['memory_ids'], ['m1'])
        self.assertEqual(row['background']['paragraphs'], [{'n': 1, 'via': 'text', 'keywords': []}])
        self.assertEqual(row['background']['core'], [4])
        self.assertGreater(row['estimated_input_tokens'], 0)
        raw = (self.store.root / 'fake/context-log.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('radiator', raw)
        self.assertNotIn('fictional persona', raw)

    def test_autonomy_requests_are_logged_as_autonomy(self):
        chat = Chat(self.cfg, self.store, self.budget, self.model)
        chat.send('You woke up on your own.', autonomy=True)
        self.assertEqual(self.store.read('fake/context-log.jsonl')[-1]['kind'], 'autonomy')

    def test_qq_markers_do_not_match_name_but_real_message_and_qq_can(self):
        self.library.write_text('Lan made a drawing.\nKeywords: Lan\n\nQQ is where we chat.\nKeywords: QQ', encoding='utf-8')
        old = '[Lan sent this on QQ; she is away from the computer.]\nhello'
        history = [{'role': 'user', 'content': old}, {'role': 'assistant', 'content': 'hi'}]
        message, details = background_context(self.library, '[QQ 发送]\nhello', history)
        self.assertEqual([p['n'] for p in details['paragraphs']], [2])
        self.assertNotIn('Lan made a drawing', message['content'])
        message, details = background_context(self.library, '[QQ 发送]\nLan made a drawing', [])
        self.assertEqual([p['n'] for p in details['paragraphs']], [1, 2])

    def test_qq_marker_still_logs_the_channel(self):
        chat = Chat(self.cfg, self.store, self.budget, self.model)
        chat.send('[QQ 发送]\nhello')
        self.assertEqual(self.store.read('fake/context-log.jsonl')[-1]['kind'], 'qq')

    def test_context_log_keeps_only_the_newest_rows(self):
        for n in range(30):
            append_context_log(self.store, 'fake', {'n': n}, keep=10)
        rows = self.store.read('fake/context-log.jsonl')
        self.assertLessEqual(len(rows), 12)
        self.assertEqual(rows[-1]['n'], 29)

    def test_no_path_means_no_background(self):
        self.assertEqual(background_context(None, 'May', []), (None, {'paragraphs': [], 'core': [], 'characters': 0}))
        self.assertIsNone(background_context(Path(self.temp.name) / 'missing.md', 'May', [])[0])


if __name__ == '__main__':
    unittest.main()
