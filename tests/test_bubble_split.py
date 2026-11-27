"""Ticket 355: blank-line-separated paragraphs in one message go out as separate bubbles, with a switch."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from veil.chat.bubble_split import MAX_BUBBLES, split_bubbles
from veil_agent import Budget, Chat, Store, load_character


class SplitTests(unittest.TestCase):
    def test_paragraphs_become_bubbles(self):
        self.assertEqual(split_bubbles('one\n\ntwo\n\n\nthree'), ['one', 'two', 'three'])
        self.assertEqual(split_bubbles('one\n \t\ntwo'), ['one', 'two'])

    def test_one_paragraph_is_left_alone_even_with_line_breaks(self):
        self.assertEqual(split_bubbles('line one\nline two'), ['line one\nline two'])
        self.assertEqual(split_bubbles('  hi  '), ['  hi  '])

    def test_code_fence_text_is_never_split(self):
        text = 'look\n\n```\na\n\nb\n```'
        self.assertEqual(split_bubbles(text), [text])

    def test_tilde_fence_text_is_never_split(self):
        text = '~~~python\nx = 1\n\ny = 2\n~~~'
        self.assertEqual(split_bubbles(text), [text])

    def test_more_than_four_paragraphs_keep_the_rest_together_in_the_last_bubble(self):
        parts = split_bubbles('a\n\nb\n\nc\n\nd\n\ne\n\nf')
        self.assertEqual(len(parts), MAX_BUBBLES)
        self.assertEqual(parts[:3], ['a', 'b', 'c'])
        self.assertEqual(parts[3], 'd\n\ne\n\nf')

    def test_non_text_is_returned_as_is(self):
        self.assertEqual(split_bubbles(None), [None])


class ChatSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
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
        self.shown = []
        cfg = load_character(root, 'test')
        store = Store(root / 'data')
        self.chat = Chat(cfg, store, Budget(store), lambda *a, **k: ('ok', 10, 2, 0),
                         output=lambda out, *_: self.shown.append(getattr(out, 'text', out)))

    def send(self, args, tool_call_id='call-1'):
        return self.chat.send_message(json.dumps(args), 'req-1', tool_call_id)

    def test_three_paragraphs_are_three_messages_and_done_ends_the_turn_on_the_last(self):
        result = self.send({'text': 'first\n\nsecond\n\nthird', 'done': True})
        self.assertEqual(self.chat.sent_messages, ['first', 'second', 'third'])
        self.assertEqual(result[1], '成功')
        self.assertTrue(self.chat.turn_done)
        self.assertEqual(self.chat.send_message_count, 3)

    def test_done_false_or_missing_does_not_end_the_turn(self):
        self.send({'text': 'first\n\nsecond'})
        self.assertEqual(self.chat.sent_messages, ['first', 'second'])
        self.assertFalse(self.chat.turn_done)

    def test_one_paragraph_is_one_message(self):
        self.send({'text': 'just this\nand this'})
        self.assertEqual(self.chat.sent_messages, ['just this\nand this'])

    def test_switch_off_sends_the_message_whole(self):
        self.chat.cfg = replace(self.chat.cfg, tools={'split_bubbles': False})
        self.send({'text': 'first\n\nsecond'})
        self.assertEqual(self.chat.sent_messages, ['first\n\nsecond'])

    def test_a_message_with_a_file_or_voice_is_not_split(self):
        # Extra keys mean attachments or voice, which belong to one message; the call is refused or sent whole,
        # never cut into pieces.
        self.send({'text': 'first\n\nsecond', 'voice': True})
        self.assertNotEqual(self.chat.sent_messages[:2], ['first', 'second'])

    def test_autonomous_wake_messages_are_not_split(self):
        self.chat.autonomy_mode = True
        self.assertIsNone(self.chat._paragraph_bubbles({'text': 'first\n\nsecond'}))

    def test_six_paragraphs_make_exactly_four_messages(self):
        self.send({'text': 'a\n\nb\n\nc\n\nd\n\ne\n\nf'})
        self.assertEqual(self.chat.sent_messages, ['a', 'b', 'c', 'd\n\ne\n\nf'])

    def test_split_fits_in_the_send_limit_left(self):
        self.chat.send_message_count = 8
        self.send({'text': 'a\n\nb\n\nc\n\nd', 'done': True})
        self.assertEqual(self.chat.sent_messages, ['a', 'b\n\nc\n\nd'])
        self.assertTrue(self.chat.turn_done)

    def test_bad_done_is_refused_before_anything_is_sent(self):
        result = self.send({'text': 'first\n\nsecond', 'done': 'true'})
        self.assertEqual(result[1], '被拒绝')
        self.assertEqual(self.chat.sent_messages, [])


if __name__ == '__main__':
    unittest.main()
