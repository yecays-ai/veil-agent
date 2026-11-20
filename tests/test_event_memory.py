import json
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


class StubModel:
    """Deterministic stand-in for the grouping model: same words -> first candidate, unrelated -> null."""
    def __call__(self, messages, **kwargs):
        payload = json.loads(messages[-1]['content'])
        stage = payload['new_stage']['text'].lower()
        candidates = payload.get('candidates') or []
        if not candidates or any(word in stage for word in ('oslo', 'quantum', 'conference')):
            choice = None
        else:
            choice = candidates[0]['event_id']
            best = 0
            for candidate in candidates:
                score = len(set(stage.split()) & set(str(candidate).lower().split()))
                if score > best:
                    best, choice = score, candidate['event_id']
        return (json.dumps({'event_id': choice, 'confidence': 0.9, 'reason': 'stub'}),
                1, 1, 0, None)


class LowConfidenceModel(StubModel):
    def __call__(self, messages, **kwargs):
        text, *rest = super().__call__(messages, **kwargs)
        payload = json.loads(text)
        payload['confidence'] = 0.2
        return (json.dumps(payload), *rest)


class StoryModel(StubModel):
    """Grouping stays JSON; the story request gets a natural narrative that keeps every stage."""
    def __call__(self, messages, **kwargs):
        if 'Rewrite the dated notes' in messages[0]['content'] or 'natural, complete story' in messages[0]['content']:
            payload = json.loads(messages[-1]['content'])
            parts = [f"{stage['at'][:10]} {stage['text']}" for stage in payload['stages']]
            for belief in payload.get('beliefs') or []:
                parts.append(f"At the time I thought {belief['belief']} ({belief['at'][:10]}).")
            return ('\n\n'.join(parts), 1, 1, 0, None)
        return super().__call__(messages, **kwargs)


class SearchBackend(Backend):
    """Fake Ombre search: only rows sharing a real word come back, so an unrelated save cannot ride the
    'memory search hit' path by accident."""

    def call(self, name, args):
        if name == 'breath_search':
            self.calls.append((name, args))
            words = {word.strip('.,;:!?').lower() for word in str(args.get('query') or '').split()
                     if len(word.strip('.,;:!?')) >= 4}
            hits = []
            for key, row in self.rows.items():
                text = str(row.get('content') or '').lower()
                if any(word in text for word in words):
                    hits.append(f'[bucket_id:{key}]')
            return '\n'.join(hits)
        return super().call(name, args)


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
        self.backend = SearchBackend(self.store.root / 'fake' / 'memory' / 'dynamic')
        self.memory = Memory(self.cfg, self.store, self.budget, Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='No files. ',
                                consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('not authorized', '被拒绝', n),
                                preview_summary=lambda n, a: n)
        self.tools = ToolSet(files, NativeMemoryTools(self.memory, 8192, model=StubModel()))
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
        detail = self.native.events.detail(event['event_id'])
        self.assertIn('3:42', detail['story'])
        self.assertIn('meh', detail['story'])
        detail = self.native.events.detail(event['event_id'])
        self.assertTrue(detail['versions'])
        self.assertEqual(len(detail['stages']), 3)

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

    def test_different_wording_with_one_shared_name_stays_one_event(self):
        for text, title in [
            ('She was angry about the file list and left the charcoal note on the desk. '
             'I answered with 关于兰-001.html.', 'The file list was the argument'),
            ('Later, the charcoal note had one word from her that I did not write.',
             'The charcoal note'),
        ]:
            result, state, _ = self.save(text, title)
            self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.native.events.events()), 1)

    def test_failed_projection_never_saves_twice_and_is_retried(self):
        self.backend.fail = True
        text, state, _ = self.save('She wrote meh on the charcoal note after the file list argument.',
                                   'The charcoal note')
        self.assertEqual(state, '出错')
        self.assertEqual(len(self.native.events.stages()), 1)
        self.assertEqual(len(self.backend.rows), 0)
        self.backend.fail = False
        # An unknown-result hold is never resent; the next real save creates the one projection and folds
        # the pending stage into it.
        self.native.events.retry_dirty()
        self.assertEqual(len(self.backend.rows), 0)
        self.save('A later part of the charcoal note day.', 'The charcoal note')
        self.assertEqual(len(self.backend.rows), 1)
        self.assertEqual(len(self.native.events.stages()), 2)

    def test_hidden_event_cannot_be_read_through_the_timeline(self):
        result, state, _ = self.save('She wrote meh on the charcoal note after the file list argument.',
                                     'The charcoal note')
        self.assertEqual(state, '成功', result)
        event = next(iter(self.native.events.events().values()))
        key = event['memory_id']
        path = self.store.root / 'fake' / 'memory' / 'dynamic' / f'{key}.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nid: {key}\ntitle: hidden\ndont_surface: true\n---\nsecret',
                        encoding='utf-8')
        self.assertIsNone(self.native.events.timeline(event['event_id']))
        self.assertIsNone(self.native.events.detail(event['event_id']))
        self.assertEqual(self.native.events.visible_events(), [])
        text, state, _ = self.native.invoke('memory_timeline', {'event_id': event['event_id']})
        self.assertEqual(state, '被拒绝')

    def test_rewrite_archives_memory_version_and_respects_lan_edit(self):
        self.save('She wrote meh on the charcoal note after the file list argument.', 'The charcoal note')
        key = next(iter(self.backend.rows))
        self.save('Later the charcoal note still sat on the desk.', 'The charcoal note')
        versions = self.store.read('fake/memory-versions.jsonl')
        self.assertTrue(any(row.get('memory_id') == key for row in versions))
        self.native.sidecar.lan_edited(key)
        before = self.backend.rows[key]['content']
        self.save('A third part of the charcoal note story.', 'The charcoal note')
        self.assertEqual(self.backend.rows[key]['content'], before)
        reviews = self.store.read('fake/event-review.jsonl')
        self.assertTrue(any('Lan edited' in str(row.get('reason')) for row in reviews))

    def test_timeline_pages_every_stage_and_shows_evidence(self):
        for index in range(25):
            self.native.events.record(f'Charcoal note part {index} on the desk, shared detail {index}.',
                                      'The charcoal note', at=f'2026-11-20T{8 + index % 12:02d}:{index:02d}:00+08:00')
        event_id = next(iter(self.native.events.events()))
        first = self.native.events.timeline(event_id, offset=0, limit=10)
        self.assertIsNotNone(first)
        text, next_offset, total = first
        self.assertEqual(total, 25)
        self.assertEqual(next_offset, 10)
        self.assertIn('stages 1-10 of 25', text)
        last = self.native.events.timeline(event_id, offset=20, limit=10)
        self.assertIsNone(last[1])
        self.assertIn('stages 21-25 of 25', last[0])

    def test_event_index_failure_leaves_a_resumable_pending_stage(self):
        original = self.store.append
        state = {'fail': True}

        def append(path, event):
            if state['fail'] and str(path).endswith('events.jsonl'):
                raise OSError('fictional index failure')
            return original(path, event)

        self.store.append = append
        result = self.native.events.record('She wrote meh on the charcoal note after the file list.',
                                           'The charcoal note')
        self.assertTrue(result.get('pending'))
        self.assertEqual(len(self.native.events.stages()), 0)
        state['fail'] = False
        self.native.events.retry_pending()
        self.assertEqual(len(self.native.events.stages()), 1)
        self.assertEqual(len(self.native.events.events()), 1)

    def test_three_saves_tidy_and_restart_stay_one_event_one_memory(self):
        first = 'She was angry about the file list and the charcoal note on the desk.'
        second = 'Later the charcoal note had one word from her that I did not write.'
        third = 'In the evening the charcoal note was still there, so I left another paper beside it.'
        self.save(first, 'The file list was the argument')
        self.save(second, 'The charcoal note')
        original = self.store.append
        state = {'fail': True}

        def append(path, event):
            if state['fail'] and str(path).endswith('events.jsonl'):
                raise OSError('fictional interruption')
            return original(path, event)

        self.store.append = append
        pending = self.native.events.record(third, 'The charcoal note')
        self.assertTrue(pending.get('pending'))
        self.store.append = original
        from event_memory import EventMemory
        restarted = EventMemory(self.memory, {'enabled': True}, model=StubModel(), sidecar=self.native.sidecar)
        restarted.retry_pending()
        restarted.retry_dirty()
        events = restarted.events()
        self.assertEqual(len(events), 1)
        event_id = next(iter(events))
        self.assertEqual(len(restarted.stages(event_id)), 3)
        self.assertEqual(len(self.backend.rows), 1)

        key = events[event_id]['memory_id']
        current = self.memory.content_for(key, refresh=True)
        self.assertTrue(restarted.revise(event_id, '整理后的完整故事：从争执到晚上那张纸。',
                                         'The charcoal note', seen=current, why='测试整理'))
        detail = restarted.detail(event_id)
        self.assertEqual(detail['story'], '整理后的完整故事：从争执到晚上那张纸。')
        self.assertEqual(len(detail['stages']), 3)
        self.assertTrue(detail['versions'])

        self.native.sidecar.lan_edited(key)
        before = self.backend.rows[key]['content']
        restarted.record('A fourth part arrives after Lan fixed the story.', 'The charcoal note')
        self.assertEqual(self.backend.rows[key]['content'], before)

    def test_retry_pending_never_overwrites_a_newer_index(self):
        first = self.native.events.record('She wrote meh on the charcoal note after the file list.',
                                          'The charcoal note')
        event_id = first['event_id']
        stale = {**self.native.events.events()[event_id], 'memory_id': 'stale-id', 'story': 'stale story',
                 'stage_count': 0, 'dirty': False}
        self.store.append('fake/event-stages.jsonl', {
            'stage_id': 's-pending', 'event_id': event_id, 'at': '2026-11-20T17:00:00+08:00',
            'text': 'Later the charcoal note still sat there.', 'entities': [], 'facts': {},
            'state': 'pending', 'event_row': stale})
        second = self.native.events.record('Evening: the charcoal note was still on the desk.',
                                           'The charcoal note', event_id=event_id)
        self.assertEqual(second['event_id'], event_id)
        self.native.events.retry_pending()
        event = self.native.events.events()[event_id]
        self.assertNotEqual(event.get('memory_id'), 'stale-id')
        self.assertIn('Evening', event.get('story') or '')
        self.assertIn('Later', event.get('story') or '')
        self.assertEqual(len(self.native.events.stages(event_id)), 3)

    def test_dirty_projection_retry_keeps_the_same_memory(self):
        self.save('She wrote meh on the charcoal note after the file list argument.', 'The charcoal note')
        key = next(iter(self.backend.rows))
        event_id = next(iter(self.native.events.events()))
        self.backend.fail = True
        self.save('Later the charcoal note was still on the desk.', 'The charcoal note')
        self.backend.fail = False
        self.assertEqual(len(self.backend.rows), 1)
        self.native.events.retry_dirty()
        event = self.native.events.events()[event_id]
        self.assertEqual(event['memory_id'], key)
        self.assertEqual(len(self.backend.rows), 1)
        self.assertFalse(event.get('dirty'))

    def test_same_save_retried_with_a_new_clock_is_idempotent(self):
        stage = 'She wrote meh on the charcoal note after the file list argument.'
        first = self.native.events.record(stage, 'The charcoal note')
        second = self.native.events.record(stage, 'The charcoal note', at='2027-01-01T00:00:00+08:00')
        self.assertEqual(first['event_id'], second['event_id'])
        self.assertTrue(second.get('duplicate'))
        self.assertEqual(len(self.native.events.stages(first['event_id'])), 1)

    def test_target_id_and_link_to_update_the_event_not_a_new_memory(self):
        self.save('She wrote meh on the charcoal note after the file list argument.',
                  'The charcoal note')
        event_id = next(iter(self.native.events.events()))
        key = self.native.events.events()[event_id]['memory_id']
        original = self.backend.rows[key]['content']
        result, state, _ = self.native.invoke('hold', {'content': 'A rewritten whole story about the note.',
                                                       'title': 'The charcoal note', 'target_id': key})
        self.assertEqual(state, '成功', result)
        self.assertNotEqual(self.backend.rows[key]['content'], original)
        self.native.search_results[key] = self.memory.content_for(key, refresh=True) or ''
        result, state, _ = self.native.invoke('hold', {'content': 'A later part of the same note day.',
                                                       'title': 'The charcoal note', 'link_to': key})
        self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.native.events.events()), 1)
        self.assertEqual(len(self.backend.rows), 1)
        self.assertEqual(len(self.native.events.stages(event_id)), 2)

    def test_tidy_revise_and_restart_keep_one_complete_event(self):
        self.save('She was angry about the file list and the charcoal note on the desk.',
                  'The file list was the argument')
        self.save('Later the charcoal note had one word from her that I did not write.',
                  'The charcoal note')
        event_id = next(iter(self.native.events.events()))
        key = self.native.events.events()[event_id]['memory_id']
        projection = self.memory.content_for(key, refresh=True)
        from experience_memory import ExperienceMemory
        tidy = ExperienceMemory(self.cfg, self.store, self.budget, StubModel(), 'fake/chat.jsonl',
                                self.memory, {'enabled': True})
        self.assertTrue(tidy._save_update({'id': key, 'text': '整理后的完整故事：争执、meh、那张纸。'},
                                          seen=projection))
        self.assertEqual(len(self.native.events.stages(event_id)), 2)
        self.assertEqual(self.native.events.events()[event_id]['story'],
                         '整理后的完整故事：争执、meh、那张纸。')
        self.assertEqual(len(self.backend.rows), 1)

        # A new process builds fresh tools on the same files; recovery must not add a second event/memory.
        restarted = ToolSet(SimpleNamespace(tools=[], capability_event=None, description='',
                                            consume_capability_event=lambda: None,
                                            invoke=lambda n, a, ap: ('no', '被拒绝', n),
                                            preview_summary=lambda n, a: n),
                            NativeMemoryTools(self.memory, 8192, model=StubModel())).native
        self.assertEqual(len(restarted.events.events()), 1)
        self.assertEqual(len(restarted.events.stages(event_id)), 2)
        self.assertEqual(len(self.backend.rows), 1)

    def test_belief_changes_get_validity_dates_and_dream_state_is_recorded(self):
        self.native.invoke('hold', {'content': 'She was quiet after the file list argument.',
                                    'title': 'The quiet', 'interpretation': 'I thought she wanted space.'})
        self.native.invoke('hold', {'content': 'Later she said the silence was not space but anger.',
                                    'title': 'The quiet', 'interpretation': 'I learned it was anger, not space.'})
        event = next(iter(self.native.events.events().values()))
        beliefs = event.get('belief_history') or []
        self.assertEqual(len(beliefs), 2)
        self.assertTrue(beliefs[0].get('invalid_at'))
        self.assertEqual(beliefs[0]['valid_from'], beliefs[0]['at'])
        self.assertIsNone(beliefs[1].get('invalid_at'))
        self.native.events.dream_pass('test')
        state = self.native.events.dream_state()
        self.assertEqual(state.get('status'), 'idle')
        self.assertTrue((self.store.root / 'fake' / 'event-dream-report.jsonl').exists())

    def test_split_and_move_rebuild_both_events(self):
        for text in ('First part of the charcoal note day.',
                     'Second part of the charcoal note day.',
                     'Third part of the charcoal note day.'):
            self.save(text, 'The charcoal note')
        event_id = next(iter(self.native.events.events()))
        stages = self.native.events.stages(event_id)
        new_id = self.native.events.split_event(event_id, [stages[-1]['stage_id']], 'Later that day')
        self.assertIsNotNone(new_id)
        self.assertEqual(len(self.native.events.stages(event_id)), 2)
        self.assertEqual(len(self.native.events.stages(new_id)), 1)
        self.assertTrue(self.native.events.move_stage(stages[0]['stage_id'], new_id))
        self.assertEqual(len(self.native.events.stages(event_id)), 1)
        self.assertEqual(len(self.native.events.stages(new_id)), 2)
        self.assertTrue(self.store.read('fake/event-ops.jsonl'))

    def test_story_is_written_as_one_narrative_with_belief_history(self):
        self.native.events.model = StoryModel()
        self.save('Nov 20, 3:42 she was angry about the file list and the charcoal note.',
                  'The file list was the argument')
        self.save('Later the charcoal note had one word from her: meh.', 'The charcoal note')
        event = next(iter(self.native.events.events().values()))
        self.assertEqual(event.get('story_source'), 'model')
        self.assertIn('3:42', event.get('story') or '')
        self.assertIn('meh', event.get('story') or '')
        detail = self.native.events.detail(event['event_id'])
        self.assertEqual(detail['story'], event['story'])

    def test_low_confidence_model_does_not_merge(self):
        self.save('She was angry about the file list and the charcoal note on the desk.',
                  'The file list was the argument')
        self.native.events.model = LowConfidenceModel()
        self.save('Later the charcoal note had one word from her.', 'The charcoal note')
        events = self.native.events.events()
        self.assertEqual(len(events), 2)
        newest = max(events.values(), key=lambda event: str(event.get('last_at')))
        self.assertEqual(newest.get('status'), 'uncertain')
        self.assertTrue(self.store.read('fake/event-review.jsonl'))

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
