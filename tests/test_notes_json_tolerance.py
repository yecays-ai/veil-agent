import unittest

from agent_types import UserError
from short_term_notes import ShortTermNotes, _parse_json_object


class NotesJsonToleranceTests(unittest.TestCase):
    def test_strict_fenced_and_surrounded_json_are_parsed(self):
        body = '{"operations": []}'
        for text in (body, '```json\n' + body + '\n```', 'Sure, here it is:\n' + body + '\nDone.'):
            self.assertEqual(_parse_json_object(text), {'operations': []})

    def test_unusable_replies_return_none(self):
        for text in ('', 'no json here', '{"operations": [', None):
            self.assertIsNone(_parse_json_object(text))

    def test_failure_keeps_head_of_reply_and_length(self):
        notes = ShortTermNotes.__new__(ShortTermNotes)
        reply = 'I cannot comply\n' + 'x' * 500
        with self.assertRaises(UserError) as caught:
            notes._validate_operations(reply, [], {})
        self.assertEqual(len(caught.exception.reply_head), 300)
        self.assertTrue(caught.exception.reply_head.startswith('I cannot comply x'))
        self.assertEqual(caught.exception.reply_chars, len(reply))

    def test_wrong_structure_is_still_refused(self):
        notes = ShortTermNotes.__new__(ShortTermNotes)
        with self.assertRaises(UserError):
            notes._validate_operations('{"operations": "none"}', [], {})


if __name__ == '__main__':
    unittest.main()
