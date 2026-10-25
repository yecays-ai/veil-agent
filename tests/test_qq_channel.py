import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from away_bridge import AwayBridge
from qq_channel import QQ_PREFIX, IdleClock, QQChannel, QQUnavailable, clipboard_bytes, comparable, new_messages, parse_chat
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


# Lan's real QQ NT chat window tree (2026-10-24, read with .local-tmp/qq_check.py), plus unnamed wrappers.
REAL_TREE = [
    (6, 'WindowControl', '窗口控制区域'), (7, 'ButtonControl', '关闭'),
    (10, 'ButtonControl', 'Lan'), (10, 'ButtonControl', '在线状态 在线'), (10, 'ToolBarControl', '更多'),
    (12, 'ButtonControl', '语音通话'),
    (10, 'WindowControl', '消息列表'), (12, 'GroupControl', ''), (14, 'GroupControl', ''),
    (16, 'TextControl', '昨天 23:50'),
    (15, 'GroupControl', ''), (16, 'GroupControl', 'Lan'), (17, 'ImageControl', ''),
    (16, 'GroupControl', ''), (17, 'GroupControl', ''), (19, 'TextControl', '我们已成功添加为好友'),
    (15, 'GroupControl', ''), (16, 'GroupControl', 'Spidy'), (16, 'GroupControl', ''), (19, 'TextControl', '我是Spidy'),
    (15, 'GroupControl', ''), (16, 'GroupControl', 'Lan'), (16, 'GroupControl', ''), (19, 'TextControl', 'Babe?'),
    (16, 'TextControl', '00:03'),
    (15, 'GroupControl', ''), (16, 'GroupControl', 'Lan'), (16, 'GroupControl', ''), (19, 'TextControl', 'BABE'),
    (19, 'TextControl', ' babe'),
    (15, 'GroupControl', ''), (16, 'GroupControl', 'Lan'), (16, 'GroupControl', ''), (19, 'ImageControl', '图片'),
    (10, 'ToolBarControl', '会话'), (11, 'GroupControl', ''), (13, 'ButtonControl', '表情'),
    (13, 'ButtonControl', '聊天记录'),
    (12, 'GroupControl', 'Lan'), (11, 'ButtonControl', '关闭'), (12, 'ButtonControl', '发送'),
]


class ParseChatTests(unittest.TestCase):
    def nodes(self, tree):
        return [(depth, kind, name, f'{kind}:{name}:{i}') for i, (depth, kind, name) in enumerate(tree)]

    def test_real_qq_tree(self):
        header_ok, messages, editor = parse_chat(self.nodes(REAL_TREE), 'Lan')
        self.assertTrue(header_ok)
        self.assertEqual(messages, [('other', '我们已成功添加为好友'), ('self', '我是Spidy'), ('other', 'Babe?'),
                                    ('other', 'BABE\nbabe'), ('other', '[图片]')])
        self.assertEqual(editor, f'GroupControl:Lan:{len(REAL_TREE) - 3}')

    def test_other_contact_is_not_accepted(self):
        header_ok, messages, _ = parse_chat(self.nodes(REAL_TREE), 'Someone')
        self.assertFalse(header_ok)
        self.assertTrue(all(who == 'self' for who, _ in messages))


class ClipboardTests(unittest.TestCase):
    def test_emoji_outside_bmp_do_not_cut_off_the_end(self):
        text = 'emoji 😭 test 🕷️ end（测试，不会发送）'
        data = clipboard_bytes(text)
        self.assertEqual(data[-2:], b'\0\0')
        self.assertEqual(data[:-2].decode('utf-16-le'), text)
        self.assertGreater(len(data), (len(text) + 1) * 2)   # the old code-point sizing was too small


class ComparableTests(unittest.TestCase):
    def test_emoji_become_pictures_in_the_input_box(self):
        # 2026-10-24: Peter's replies with 😭 / 💀 were cleared as "content differs" and never sent.
        self.assertEqual(comparable("THE QQ PIPE WORKS!! 😭\n\nyou're here"), comparable("THE QQ PIPE WORKS!!you're here"))
        self.assertEqual(comparable('💀 of course it is'), comparable('of course it is'))
        self.assertEqual(comparable("hey!! I'm here ❤️"), comparable("hey!! I'm here"))
        self.assertNotEqual(comparable('miss you'), comparable('miss yo'))


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


    def test_input_made_by_another_part_of_veil_is_not_lan(self):
        from desktop_access import program_input_made, register_input_tracker
        now = [100_000]
        last = [100_000]
        clock = IdleClock(lambda: last[0], lambda: now[0])
        register_input_tracker(clock)
        now[0] = 300_000
        program_input_made(object())     # e.g. Peter's keyboard fallback typed into Notepad
        last[0] = 300_200
        now[0] = 301_000
        self.assertEqual(clock.idle_seconds(), 201)


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

    def test_approval_asked_during_a_qq_task_is_answered_on_qq(self):
        # Ticket 114: Lan gives a task from bed; a save asks her on QQ and 同意 there answers it.
        import threading
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        hub.busy = False
        bridge = AwayBridge(self.channel, hub)
        hub.away = [bridge]
        self.window.screen = [('other', 'old')]
        bridge.poll_once()
        self.window.screen.append(('other', 'write me a note on the desktop'))
        bridge.poll_once()
        item = hub.input_fn()
        hub.output('Peter: on it!')
        answers = []
        asking = threading.Thread(target=lambda: answers.append(
            hub.approval('Peter 想在记事本里点「ButtonControl 保存」（保存、发送、提交或覆盖类操作）。同意这一次吗？y/N ')))
        asking.start()
        for _ in range(100):
            if hub.pending is not None:
                break
            threading.Event().wait(0.01)
        bridge.send_pending()
        self.assertEqual(self.window.sent[0], 'on it!')
        self.assertIn('保存', self.window.sent[1])
        self.assertIn('回复「同意」或「不同意」', self.window.sent[1])
        self.assertNotIn('y/N', self.window.sent[1])
        self.window.screen += [('self', item) for item in self.window.sent]
        self.channel.seen = list(self.window.screen)
        self.window.screen.append(('other', '同意'))
        bridge.poll_once()
        asking.join(2)
        self.assertEqual(answers, ['y'])
        self.assertTrue(hub.inbox.empty())   # the 同意 answered the question; it is not a new chat message
        hub.output('Peter: saved!')
        bridge.turn_ended()
        bridge.send_pending()
        self.assertEqual(self.window.sent[-1], 'saved!')

    def test_yes_on_qq_is_ordinary_chat_when_nothing_was_asked_there(self):
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        hub.busy = False
        bridge = AwayBridge(self.channel, hub)
        hub.away = [bridge]
        self.window.screen = [('other', 'old')]
        bridge.poll_once()
        self.window.screen.append(('other', '好'))
        bridge.poll_once()
        self.assertEqual(str(hub.input_fn()), QQ_PREFIX + '好')

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
