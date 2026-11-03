"""Ticket 206: the page's live event list is bounded; ids stay unique and ordered; cursors keep working."""
import tempfile
import unittest
from pathlib import Path

import web_agent
from veil_agent import ActivityState, Store
from web_agent import Hub


class HubEventWindowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'data')
        self.hub = Hub('Test', ActivityState(store.root / 'resident-state.json'))

    def fill(self, count):
        return [self.hub.push('assistant', f'fictional {i}') for i in range(count)]

    def test_window_is_bounded_and_ids_never_repeat(self):
        total = web_agent.MAX_LIVE_EVENTS + web_agent.EVENT_TRIM_SLACK * 3 + 7
        ids = self.fill(total)
        self.assertEqual(ids, list(range(1, total + 1)))
        kept = self.hub.events
        self.assertLessEqual(len(kept), web_agent.MAX_LIVE_EVENTS + web_agent.EVENT_TRIM_SLACK)
        self.assertGreaterEqual(len(kept), web_agent.MAX_LIVE_EVENTS)
        self.assertEqual([e['id'] for e in kept], list(range(total - len(kept) + 1, total + 1)))

    def test_connected_page_keeps_receiving_without_loss_or_duplicates(self):
        cursor, seen = 0, []
        for i in range(web_agent.MAX_LIVE_EVENTS * 2):
            self.hub.push('user', f'fictional {i}')
            if i % 97 == 0:
                fresh = self.hub.events_after(cursor)
                seen += [e['id'] for e in fresh]
                cursor = fresh[-1]['id'] if fresh else cursor
        seen += [e['id'] for e in self.hub.events_after(cursor)]
        self.assertEqual(seen, list(range(1, web_agent.MAX_LIVE_EVENTS * 2 + 1)))

    def test_reload_and_stale_cursor_get_the_kept_window(self):
        self.fill(web_agent.MAX_LIVE_EVENTS + web_agent.EVENT_TRIM_SLACK + 50)
        window = self.hub.events_after(0)
        self.assertEqual(window, self.hub.events)
        self.assertEqual(self.hub.events_after(5), window)  # cursor older than the window
        self.assertEqual(self.hub.events_after(window[-1]['id']), [])
        self.assertEqual([e['id'] for e in self.hub.events_after(window[-3]['id'])],
                         [window[-2]['id'], window[-1]['id']])

    def test_small_history_is_untouched(self):
        self.fill(10)
        self.assertEqual([e['id'] for e in self.hub.events_after(0)], list(range(1, 11)))
        self.assertEqual([e['id'] for e in self.hub.events_after(7)], [8, 9, 10])


if __name__ == '__main__':
    unittest.main()
