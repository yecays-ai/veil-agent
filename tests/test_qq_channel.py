import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from away_bridge import AwayBridge
from qq_channel import QQ_PREFIX, IdleClock, QQChannel, QQUnavailable, new_messages
from web_agent import Hub


SETTINGS = {'enabled': True, 'poll_seconds': 20, 'idle_seconds': 120, 'daily_max_sent': 3}


class FakeWindow:
    def __init__(self):
        self.screen = []
        self.sent = []
        self.broken = False
        self.full_screen = False

    def other_app_full_screen(self):
        return self.full_screen

    def read(self):
        if self.broken:
            raise QQUnavailable('QQ 窗口打不开')
        return list(self.screen)

    def send(self, text):
        self.sent.append(text)
        self.screen.append(('self', text))


class FakeClock:
    def __init__(self, idle):
        self.idle = idle
        self.own = 0

    def idle_seconds(self):
        return self.idle

    def mark_own_input(self):
        self.own += 1


class NewMessagesTests(unittest.TestCase):
    def test_growth_scroll_and_no_overlap(self):
        old = [('other', 'a'), ('self', 'b')]
        self.assertEqual(new_messages(old, old + [('other', 'c')]), [('other', 'c')])
        self.assertEqual(new_messages(old, [('self', 'b'), ('other', 'c'), ('other', 'd')]),
                         [('other', 'c'), ('other', 'd')])
        self.assertEqual(new_messages(old, old), [])
        self.assertEqual(new_messages(old, [('other', 'x'), ('self', 'y'), ('other', 'z')]), [('other', 'z')])


class IdleClockTests(unittest.TestCase):
    def test_own_input_does_not_count_as_lan_returning(self):
        now = [100_000]
        last = [100_000]
        clock = IdleClock(lambda: last[0], lambda: now[0])
        now[0] = 300_000
        self.assertEqual(clock.idle_seconds(), 200)
        clock.mark_own_input()           # the program clicked and typed at 300 s
        last[0] = 300_500
        now[0] = 310_000
        self.assertEqual(clock.idle_seconds(), 210)
        last[0] = 309_000                # a real key press long after the program's input
        self.assertEqual(clock.idle_seconds(), 1)


class QQChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.window = FakeWindow()
        self.clock = FakeClock(idle=500)
        self.channel = QQChannel(Path(self.tmp.name), 'peter', 'Lan', SETTINGS, self.window, self.clock)

    def test_first_read_is_baseline_then_only_lans_new_lines(self):
        self.window.screen = [('other', 'old'), ('self', 'older reply')]
        self.assertEqual(self.channel.poll(), [])
        self.window.screen += [('other', 'are you there'), ('other', 'I am in bed')]
        found = self.channel.poll()
        self.assertEqual([item.text for item in found], ['are you there\nI am in bed'])
        self.channel.ack(found[0])
        self.assertEqual(self.channel.poll(), [])

    def test_nothing_happens_while_lan_uses_the_computer(self):
        self.clock.idle = 5
        self.window.broken = True        # would raise if touched
        self.assertEqual(self.channel.poll(), [])
        self.assertFalse(self.channel.ready_to_send())

    def test_full_screen_video_is_not_interrupted(self):
        self.window.full_screen = True
        self.window.broken = True
        self.assertEqual(self.channel.poll(), [])
        self.assertFalse(self.channel.ready_to_send())

    def test_reply_splits_bubbles_and_respects_daily_cap(self):
        self.window.screen = [('other', 'hi')]
        self.assertIsNone(self.channel.reply('one\n\ntwo'))
        self.assertEqual(self.window.sent, ['one', 'two'])
        self.assertIn('上限', self.channel.reply('three\n\nfour'))
        self.assertEqual(self.window.sent, ['one', 'two', 'three'])
        self.window.screen.append(('other', 'night'))
        found = self.channel.poll()   # his own messages were not mistaken for new ones
        self.assertEqual([item.text for item in found], ['night'])

    def test_bridge_round_trip_through_hub(self):
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        hub.busy = False
        bridge = AwayBridge(self.channel, hub)
        hub.away = [bridge]
        self.window.screen = [('other', 'old')]
        bridge.poll_once()
        self.window.screen.append(('other', 'miss you'))
        bridge.poll_once()
        item = hub.input_fn()
        self.assertEqual(str(item), QQ_PREFIX + 'miss you')
        hub.output('Peter: miss you too')
        for each in hub.away:   # the turn ends when the loop asks for its next input
            each.turn_ended()
        bridge.send_pending()
        self.assertEqual(self.window.sent, ['miss you too'])

    def test_unavailable_window_is_reported_once(self):
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        bridge = AwayBridge(self.channel, hub)
        self.window.broken = True
        bridge.poll_once()
        bridge.poll_once()
        notices = [event for event in hub.events if event['kind'] == 'notice']
        self.assertEqual(len(notices), 1)

    def test_missing_component_is_reported_not_silent(self):
        import sys
        from unittest.mock import patch
        from qq_channel import QQWindow
        self.channel.ui = QQWindow('Lan')
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        bridge = AwayBridge(self.channel, hub)
        with patch.dict(sys.modules, {'uiautomation': None}):
            self.assertFalse(self.channel.ready_to_send())
            bridge.poll_once()
        notices = [event['text'] for event in hub.events if event['kind'] == 'notice']
        self.assertEqual(len(notices), 1)
        self.assertIn('setup.bat', notices[0])


if __name__ == '__main__':
    unittest.main()
