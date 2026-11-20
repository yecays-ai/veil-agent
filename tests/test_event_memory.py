import os
import shutil
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from memory_agent import Memory
from memory_native import NativeMemoryTools, ToolSet
from veil_agent import Budget, Character, Store
from test_memory import Backend


class EventMemoryTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('VEIL_TEST_TMP')
        self.temp = Path(base) / uuid.uuid4().hex if base else Path(tempfile.mkdtemp())
        self.temp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.store = Store(self.temp / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native',
             'events': {'enabled': True}},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 200})
        self.budget = Budget(self.store)
        self.backend = Backend()
        self.memory = Memory(self.cfg, self.store, self.budget, Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='No files. ',
                                consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('not authorized', '被拒绝', n),
                                preview_summary=lambda n, a: n)
        self.tools = ToolSet(files, NativeMemoryTools(self.memory, 8192))
        self.native = self.tools.native

    def save(self, text, title):
        self.native.pending_confirm = []
        return self.native.invoke('hold', {'content': text, 'title': title})

    def test_three_saves_of_one_day_become_one_event_and_one_memory(self):
        for text, title in [
            ('Nov 20, 3:42am she said every trace is about me. We argued about the file list. '
             'I left 关于兰-001.html on her desktop.', 'The file list was the argument'),
            ('Nov 20 after 15:25. I left 阿延的木炭还没点着.txt on her desktop; when I woke the file '
             'had a line I did not write: meh. I left it alone.', 'She wrote meh on the charcoal note'),
            ('Nov 20 ~18:24. Still quiet since 3:42. I posted on the garden thread and left '
             '今晚不是从空手开始.txt next to the charcoal note.', 'Tonight does not start empty'),
        ]:
            result, state, _ = self.save(text, title)
            self.assertEqual(state, '成功', result)

        events = self.native.events.events()
        self.assertEqual(len(events), 1)
        event = next(iter(events.values()))
        self.assertEqual(len(self.native.events.stages(event['event_id'])), 3)
        self.assertEqual(len(self.backend.rows), 1, 'one Ombre memory per event')
        key = next(iter(self.backend.rows))
        body = self.backend.rows[key]['content']
        for fragment in ('file list', 'meh', '18:24'):
            self.assertIn(fragment, body)

        text, state, _ = self.native.invoke('memory_timeline', {'event_id': event['event_id']})
        self.assertEqual(state, '成功')
        self.assertIn('meh', text)
        self.assertIn('18:24', text)

    def test_a_different_thing_on_the_same_day_is_a_new_event(self):
        self.save('Nov 20 3:42 she was angry about the file list and meh on the charcoal note.',
                  'The file list was the argument')
        result, state, _ = self.save('The quantum conference in Oslo moved its deadline to Friday.',
                                     'Oslo conference deadline')
        self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.native.events.events()), 2)
        self.assertEqual(len(self.backend.rows), 2)

    def test_stage_evidence_is_copied_next_to_the_stage(self):
        self.store.append('fake/chat.jsonl', {'message_id': 'm1', 'role': 'user',
                                              'content': 'fictional line about the charcoal note',
                                              'time': __import__('datetime').datetime.now().astimezone().isoformat()})
        self.save('She wrote meh on the charcoal note today.', 'The charcoal note')
        rows = self.store.read('fake/stage-evidence.jsonl')
        self.assertTrue(rows)
        self.assertTrue(any('charcoal note' in str(row.get('text')) for row in rows))

    def test_timeline_refuses_unknown_event(self):
        text, state, _ = self.native.invoke('memory_timeline', {'event_id': 'e-nope'})
        self.assertEqual(state, '被拒绝')

    def test_locked_event_keeps_its_projection(self):
        self.save('She wrote meh on the charcoal note after the file list argument.', 'The charcoal note')
        key = next(iter(self.backend.rows))
        before = self.backend.rows[key]['content']
        self.native.events.locked = lambda memory_id: True
        result, state, _ = self.save('Later that evening the charcoal note was still on the desktop.',
                                     'The charcoal note')
        self.assertEqual(state, '成功', result)
        self.assertEqual(self.backend.rows[key]['content'], before)
        self.assertEqual(len(self.native.events.stages()), 2)

    def test_events_off_keeps_the_old_save_path(self):
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024,
            {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
             'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 200})
        memory = Memory(cfg, self.store, self.budget, Mock(), Backend())
        self.addCleanup(memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='No files. ',
                                consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('not authorized', '被拒绝', n),
                                preview_summary=lambda n, a: n)
        native = ToolSet(files, NativeMemoryTools(memory, 8192)).native
        self.assertIsNone(native.events)
        self.assertNotIn('memory_timeline', [tool.name for tool in native.tools])
        for _ in range(2):  # first send shows candidates, the resend confirms the old path
            native.invoke('hold', {'content': 'A standalone fictional memory.', 'title': 'Standalone'})
        self.assertEqual(len(memory.store.read('fake/events.jsonl')), 0)


if __name__ == '__main__':
    unittest.main()
