import unittest

from chat_search import search_chat, split_keywords


def row(role, content, when, **extra):
    return {'role': role, 'content': content, 'time': when, **extra}


LOG = [
    row('user', 'Lunch was fine today.', '2026-10-01T12:00:00+00:00'),
    row('assistant', 'Glad to hear it.', '2026-10-01T12:01:00+00:00'),
    row('user', 'The factory still has not shipped my badge order.', '2026-10-20T09:00:00+00:00'),
    row('assistant', 'That sounds annoying. Did they give a date?', '2026-10-20T09:01:00+00:00'),
    row('user', 'No. I am packing the other parcels meanwhile.', '2026-10-20T09:02:00+00:00'),
    row('user', 'Totally unrelated: the badge colours are lovely.', '2026-10-25T10:00:00+00:00'),
    row('assistant', 'Withdrawn badge factory remark', '2026-10-25T10:01:00+00:00', recalled=True),
]


class ChatSearchTests(unittest.TestCase):
    def test_keywords_split_from_one_string(self):
        self.assertEqual(split_keywords(['厂家、吧唧']), ['厂家', '吧唧'])
        self.assertEqual(split_keywords('factory, badge'), ['factory', 'badge'])

    def test_finds_co_occurring_words_with_time_and_context(self):
        text, found = search_chat(LOG, ['factory', 'badge'])
        self.assertTrue(found)
        first = text.split('#1', 1)[1].split('#2', 1)[0]
        self.assertIn('all clue words near each other', first)
        self.assertIn('2026-10-20', first)
        self.assertIn('[*]', first)
        self.assertIn('Did they give a date?', first)
        self.assertNotIn('Withdrawn badge factory remark', text)

    def test_single_word_match_is_ranked_after_pairs_and_still_returned(self):
        text, found = search_chat(LOG, ['factory', 'badge'])
        self.assertIn('badge colours are lovely', text)

    def test_nothing_found_says_so(self):
        text, found = search_chat(LOG, ['dragon'])
        self.assertFalse(found)
        self.assertIn('No messages containing dragon', text)
        self.assertIn('do not guess', text)

    def test_date_filter(self):
        text, found = search_chat(LOG, ['badge'], since='2026-10-24')
        self.assertTrue(found)
        self.assertNotIn('factory still has not', text.split('#1', 1)[1].split('\n')[1])
        _, found = search_chat(LOG, ['factory'], since='2026-10-24')
        self.assertFalse(found)
        text, found = search_chat(LOG, ['badge'], since='nonsense')
        self.assertFalse(found)
        self.assertIn('2026-11-05', text)

    def test_long_messages_are_shortened_around_the_clue(self):
        log = [row('user', 'x' * 900 + ' needle ' + 'y' * 900, '2026-10-01T12:00:00+00:00')]
        text, _ = search_chat(log, ['needle'])
        self.assertIn('needle', text)
        self.assertLess(len(text), 900)

    def test_case_insensitive_and_empty_keywords(self):
        _, found = search_chat(LOG, ['FACTORY'])
        self.assertTrue(found)
        text, found = search_chat(LOG, [''])
        self.assertFalse(found)


if __name__ == '__main__':
    unittest.main()
