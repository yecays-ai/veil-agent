"""Exercise real capability files with a controlled two-writer interleaving."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autonomy import AutonomyController
from memory_native import NativeMemoryTools
from tools_agent import FilesystemTools
from veil_agent import ActivityState, Store, UserError
from web_agent import Hub


class CapabilityTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')
        self.manager = self.open_manager()
        self.manager._record_capabilities(['read_text_file'])

    def open_manager(self):
        manager = FilesystemTools.__new__(FilesystemTools)
        manager.cfg = SimpleNamespace(id='fictional')
        manager.store = self.store
        manager.folder_name = 'workspace'
        manager.cap_path = self.store.root / 'fictional' / 'capabilities.json'
        manager.capability_event = manager.capability_event_model = None
        manager.enabled = True
        return manager

    def read(self):
        return json.loads(self.manager.cap_path.read_text(encoding='utf-8'))

    def interleave(self, first, second):
        """Pause the first real replacement while a second transaction starts."""
        replacing, started, finished = (threading.Event() for _ in range(3))
        failures = []
        original = os.replace

        def replace(source, target):
            if threading.current_thread().name == 'cap-first':
                replacing.set()
                if not started.wait(3):
                    raise AssertionError('second writer did not start')
                # Old code completes the second replace here. With transaction
                # locking it waits for the first writer, so release after a bound.
                finished.wait(0.5)
            return original(source, target)

        def run(action, is_second=False):
            if is_second:
                started.set()
            try:
                action()
            except BaseException as exc:
                failures.append(exc)
            finally:
                if is_second:
                    finished.set()

        with patch('os.replace', side_effect=replace):
            a = threading.Thread(target=run, args=(first,), name='cap-first', daemon=True)
            b = threading.Thread(target=run, args=(second, True), daemon=True)
            a.start()
            self.assertTrue(replacing.wait(3), 'first writer did not reach replacement')
            b.start()
            a.join(5)
            b.join(5)
            self.assertFalse(a.is_alive() or b.is_alive(), 'capability transaction deadlocked')
        self.assertEqual(failures, [])
        self.assertTrue(self.manager.enabled)

    def test_consume_and_autonomy_keep_tools_enabled_and_survive_reopen(self):
        self.interleave(self.manager.consume_capability_event, self.manager.announce_autonomy)
        row = self.read()
        self.assertTrue(row['autonomy_announced'])
        self.assertIn('send_message', row['pending_event'])
        self.assertEqual(self.manager.capability_event, row['pending_event'])
        reopened = self.open_manager()
        reopened._record_capabilities(['read_text_file'])
        self.assertEqual(reopened.capability_event, row['pending_event'])
        reopened.consume_capability_event()
        reopened.announce_autonomy()
        self.assertIsNone(reopened.capability_event)
        self.assertTrue(self.read()['autonomy_announced'])

    def test_separate_managers_keep_both_announcement_flags_and_notices(self):
        other = self.open_manager()
        self.interleave(self.manager.announce_autonomy, other.announce_long_tasks)
        row = self.read()
        self.assertTrue(row['autonomy_announced'])
        self.assertTrue(row['long_tasks_announced'])
        self.assertIn('autonomous activity', row['pending_event'])
        self.assertIn('long-task tools', row['pending_event'])
        self.assertTrue(other.enabled)

    def test_next_wake_notice_is_saved_once_and_survives_reopen(self):
        self.manager.announce_next_wake()
        row = self.read()
        self.assertTrue(row['next_wake_announced'])
        self.assertIn('set_next_wake', row['pending_event'])
        self.assertIn('7 days', row['pending_event'])
        reopened = self.open_manager()
        reopened._record_capabilities(['read_text_file'])
        self.assertIn('set_next_wake', reopened.capability_event)
        reopened.consume_capability_event()
        reopened.announce_next_wake()
        self.assertIsNone(reopened.capability_event)
        self.assertTrue(self.read()['next_wake_announced'])

    def test_more_autonomy_notice_is_saved_once_and_survives_reopen(self):
        self.manager.announce_autonomy_more_room()
        row = self.read()
        self.assertTrue(row['autonomy_more_room_announced'])
        self.assertIn('12 wakes per day', row['pending_event'])
        self.assertIn('20 tool steps', row['pending_event'])
        reopened = self.open_manager()
        reopened._record_capabilities(['read_text_file'])
        self.assertIn('20 tool steps', reopened.capability_event)
        reopened.consume_capability_event()
        reopened.announce_autonomy_more_room()
        self.assertIsNone(reopened.capability_event)

    def test_web_activity_toggle_and_chat_notice_do_not_deadlock(self):
        activity = ActivityState(self.store.root / 'fictional' / 'activity.json')
        hub = Hub('Example', activity)
        controller = AutonomyController.__new__(AutonomyController)
        controller.chat = SimpleNamespace(tools=self.manager)
        hub.autonomy = controller

        def chat_consume():
            self.manager.consume_capability_event()
            hub.push('notice', 'fictional chat continues')

        self.interleave(chat_consume, lambda: hub.set_activity(True))
        self.assertTrue(activity.enabled())
        self.assertTrue(self.read()['autonomy_announced'])
        self.assertEqual(hub.events[-1]['text'], 'fictional chat continues')
        self.assertEqual(controller.chat.pending_capability_event, self.read()['pending_event'])

    def test_native_binding_and_capability_refresh_preserve_each_others_fields(self):
        native = NativeMemoryTools.__new__(NativeMemoryTools)
        native.memory = SimpleNamespace(store=self.store,
                                        cfg=SimpleNamespace(id='fictional', name='Example'))
        native.tools = [SimpleNamespace(name='hold')]
        self.interleave(lambda: self.manager._record_capabilities(['read_text_file', 'write_file']),
                        lambda: native.bind_capabilities(self.manager))
        row = self.read()
        self.assertEqual(row['memory_tools'], ['hold'])
        self.assertEqual(row['tools']['write_file'], 'ask')
        self.assertIn('new memory tools are available', row['pending_event'])
        self.assertTrue(any('new memory tools are available' in update.get('text', '')
                            for update in row['capability_updates']))
        self.assertEqual(self.manager.capability_event, row['pending_event'])

    def test_failed_replace_keeps_original_and_stops_tools(self):
        before = self.manager.cap_path.read_bytes()
        with patch('os.replace', side_effect=OSError('fictional storage failure')):
            with self.assertRaises(UserError):
                self.manager.announce_autonomy()
        self.assertEqual(self.manager.cap_path.read_bytes(), before)
        self.assertFalse(self.manager.enabled)
        self.assertFalse(self.read()['autonomy_announced'])


if __name__ == '__main__':
    unittest.main()
