import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from veil_agent import Budget, Character, Chat, Store, history_keep


class CacheOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 60000, 'input_cny_per_million': 2,
                    'output_cny_per_million': 8, 'cached_input_cny_per_million': 1}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake')
        self.clock = ['2026-10-19T09:00:00+08:00']
        self.budget = Budget(self.store, clock=lambda: self.clock[0])
        self.sent = []

        def model(messages, **kwargs):
            self.sent.append([dict(m) for m in messages])
            return 'reply', 1000, 20, 800
        self.chat = Chat(self.cfg, self.store, self.budget, model)

    def test_everything_that_changes_comes_after_the_history(self):
        self.chat.send('one')
        self.clock[0] = '2026-10-19T09:05:00+08:00'
        self.chat.send('two')
        first, second = self.sent
        # Persona, stable facts and the whole earlier conversation are byte-identical at the start.
        prefix = first[:-2]  # persona, stable facts (the changing facts and the user message follow)
        self.assertEqual(second[:len(prefix)], prefix)
        self.assertEqual(second[len(prefix):len(prefix) + 2],
                         [{'role': 'user', 'content': 'one'}, {'role': 'assistant', 'content': 'reply'}])
        # Time and ledger sit right before the user's message, not in the prefix.
        for message in second[:-2]:
            self.assertNotIn('Local time', message['content'])
            self.assertNotIn('ledger', message['content'])
        self.assertIn('Local time: 2026-10-19T09:05', second[-2]['content'])
        self.assertEqual(second[-1], {'role': 'user', 'content': 'two'})

    def test_recalled_memory_does_not_break_the_prefix(self):
        memory = Mock()
        memory.context.side_effect = [[], [{'role': 'system', 'content': 'recalled: blue'}]]
        chat = Chat(self.cfg, self.store, self.budget, self.chat.model, memory)
        chat.send('one')
        chat.send('two')
        first, second = self.sent[-2:]
        prefix = first[:-2]
        self.assertEqual(second[:len(prefix)], prefix)
        self.assertNotIn('recalled: blue', [m['content'] for m in second[:len(prefix) + 2]])
        self.assertIn('recalled: blue', [m['content'] for m in second[-3:-1]])

    def test_history_start_moves_in_chunks_not_every_message(self):
        starts = {}
        for total in range(1, 61):
            keep = history_keep(total, 20)
            self.assertLessEqual(keep, 20)
            self.assertGreaterEqual(keep, min(total, 16))
            starts.setdefault(total - keep, []).append(total)
        # Only a handful of different start positions, each reused for several messages in a row.
        self.assertLessEqual(len(starts), 10)
        self.assertTrue(all(len(v) >= 5 or v[0] <= 20 for v in starts.values() if v[-1] < 56))

    def test_small_windows_keep_plain_sliding(self):
        self.assertEqual([history_keep(t, 3) for t in range(1, 8)], [1, 2, 3, 3, 3, 3, 3])
        self.assertEqual(history_keep(5, 2), 2)

    def test_cache_display_reports_todays_hit_rate(self):
        self.assertIn('暂无', self.budget.cache_display())
        self.chat.send('one')
        self.chat.send('two')
        text = self.budget.cache_display()
        self.assertIn('80%', text)
        self.assertIn('1600 / 已报告输入 2000', text)
        self.clock[0] = '2026-10-20T09:00:00+08:00'
        self.assertIn('暂无', self.budget.cache_display())

    def test_missing_cache_usage_is_unknown_but_charged_at_regular_input_rate(self):
        request_id, reserved = self.budget.reserve(self.cfg)
        self.budget.settle(request_id, self.cfg, 1000, 20, None)
        row = self.store.read('costs.jsonl')[-1]
        self.assertEqual(row['cached_tokens'], 0)
        self.assertIs(row['cache_reported'], False)
        self.assertEqual(row['amount'], str((1000 * 2 + 20 * 8) / 1_000_000))
        self.assertIn('未报告缓存用量', self.budget.cache_display())
        self.assertNotIn('缓存命中 0%', self.budget.cache_display())

        request_id, reserved = self.budget.reserve(self.cfg)
        self.budget.settle(request_id, self.cfg, 1000, 20, 0)
        self.assertIs(self.store.read('costs.jsonl')[-1]['cache_reported'], True)
        self.assertIn('已报告输入 1000 tokens', self.budget.cache_display())
        self.assertIn('另有 1 次', self.budget.cache_display())


if __name__ == '__main__':
    unittest.main()
