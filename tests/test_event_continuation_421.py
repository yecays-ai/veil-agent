"""Ticket 421: a new experience joins the old story, is saved whole, and can be recovered.

All data and models here are fictional; no paid call is made. The tests go through the real save
entry points (NativeMemoryTools.hold, ExperienceMemory.run_if_due, EventMemory.record/revise/retry)
on the real local ledger, with only the model and Ombre transport faked.
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agent_types import BudgetExceeded, UserError
from event_memory import EventMemory, entities
from experience_memory import ExperienceMemory, load_settings
from memory_agent import Memory
from memory_native import NativeMemoryTools, ToolSet
from veil_agent import Budget, Character, Store
from test_memory import Backend


class SearchBackend(Backend):
    """Fake Ombre: only rows sharing a real word come back, so an unrelated save cannot ride a hit."""

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


class RouterModel:
    """One fake model for both the tidy and the event layer: group by shared entities, write a story
    from the stages it was given, and answer the tidy with the queued entry JSON."""

    def __init__(self, entries=None, merge=True):
        self.entries = entries or []
        self.updates = []
        self.merge = merge
        self.requests = []
        self.group_decisions = []
        self.story_calls = 0

    def __call__(self, messages, **kwargs):
        self.requests.append(json.loads(json.dumps(messages)))   # snapshot, never the live object
        try:
            payload = json.loads(messages[-1]['content'])
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and 'new_stage' in payload:
            stage = str(payload['new_stage'].get('text') or '')
            choice, best = None, 0
            if self.merge:
                mine = entities(stage)
                for candidate in payload.get('candidates') or []:
                    theirs = entities(' '.join([str(candidate.get('title') or ''),
                                                str(candidate.get('story') or ''),
                                                ' '.join(candidate.get('recent') or [])]))
                    score = len(mine & theirs)
                    if score > best:
                        best, choice = score, candidate['event_id']
                if best < 1:
                    choice = None
            self.group_decisions.append(choice)
            return (json.dumps({'event_id': choice, 'confidence': 0.9 if choice else 0.4,
                                'reason': 'fictional router'}), 1, 1, 0, None)
        if isinstance(payload, dict) and 'stages' in payload:
            self.story_calls += 1
            parts = []
            if payload.get('existing_story'):
                parts.append(str(payload['existing_story']))
            parts += [f"{str(stage.get('at'))[:10]} {stage.get('text')}" for stage in payload['stages']]
            for belief in payload.get('beliefs') or []:
                parts.append(f"At the time I thought {belief['belief']} ({str(belief['at'])[:10]}).")
            return ('\n\n'.join(parts), 1, 1, 0, None)
        if isinstance(payload, dict) and ('entries' in payload or 'updates' in payload):
            return (json.dumps({'entries': self.entries, 'updates': self.updates}, ensure_ascii=False), 1, 1, 0, None)
        if isinstance(payload, dict) and 'messages' in payload and 'earlier_entries' in payload:
            return (json.dumps({'entries': self.entries, 'updates': self.updates}, ensure_ascii=False), 1, 1, 0, None)
        return (json.dumps({'entries': [], 'updates': []}), 1, 1, 0, None)


class Fixture(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('VEIL_TEST_TMP')
        self.temp = Path(base) / uuid.uuid4().hex if base else Path(tempfile.mkdtemp())
        self.temp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.store = Store(self.temp / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 60000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Peter', 'fictional persona', 20, 4096, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native',
             'events': {'enabled': True, 'story_batch_chars': 1200}},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 8192})
        self.budget = Budget(self.store)
        self.backend = SearchBackend(self.store.root / 'fake' / 'memory' / 'dynamic')
        self.model = RouterModel()
        self.memory = Memory(self.cfg, self.store, self.budget, Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.native = self.open_tools()

    def open_tools(self):
        files = SimpleNamespace(tools=[], capability_event=None, description='No files. ',
                                consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('not authorized', '被拒绝', n),
                                preview_summary=lambda n, a: n)
        return ToolSet(files, NativeMemoryTools(self.memory, 8192, model=self.model))

    @property
    def events(self):
        return self.native.native.events

    def save(self, text, title='An experience', **extra):
        self.native.native.pending_confirm = []
        args = {'content': text, 'title': title, **extra}
        return self.native.invoke('hold', args)

    def chat_row(self, when, text, message_id):
        self.store.append('fake/chat.jsonl', {'message_id': message_id, 'role': 'user',
                                              'content': text, 'time': when})


class ContinuationTests(Fixture):
    def test_the_shared_experience_cap_is_atomic(self):
        provider = self.cfg.provider
        cap = (provider['max_input_tokens'] * provider['input_cny_per_million']
               + self.cfg.max_output_tokens * provider['output_cny_per_million']) / Decimal(1_000_000)
        barrier = threading.Barrier(2)
        outcomes = []

        def reserve():
            barrier.wait()
            try:
                self.budget.reserve(self.cfg, accounting='event-story', category='experience_memory',
                                    category_daily=cap)
                outcomes.append('reserved')
            except BudgetExceeded:
                outcomes.append('blocked')

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ['reserved', 'blocked'])

    def test_background_event_model_calls_share_the_experience_daily_cap(self):
        model = RouterModel()
        guarded = EventMemory(self.memory, {'enabled': True}, model=model,
                              sidecar=self.native.native.sidecar, budget=self.budget,
                              cost_category='experience_memory', daily_cap=Decimal('0'))
        saved = guarded.record('A fictional lamp stage.', 'The lamp', source_key='chat:budget')
        self.assertIsNotNone(saved['event_id'])
        self.assertTrue(saved['story_pending'])
        self.assertEqual(model.requests, [])
        spent, pending = self.budget.category_totals('fake', category='experience_memory')
        self.assertEqual(spent + pending, Decimal('0'))

    def test_peter_hold_uses_the_same_experience_daily_cap(self):
        self.memory.cfg.memory['experience'] = {'daily_budget_cny': Decimal('0')}
        guarded = self.open_tools()
        result, state, _ = guarded.invoke('hold', {
            'title': 'A capped save', 'content': 'A fictional stage saved under the daily cap.'})
        self.assertEqual(state, '成功', result)
        self.assertEqual(self.model.requests, [])
        event = next(iter(guarded.native.events.events().values()))
        self.assertTrue(event['story_pending'])

    def test_a_budget_blocked_story_rebuild_does_not_duplicate_old_stages(self):
        first = self.events.record('OLD-STAGE-MARK.', 'The lamp', at='2026-11-20T10:00:00+08:00')
        before = self.events.events()[first['event_id']]['story_through_id']
        guarded = EventMemory(self.memory, {'enabled': True}, model=RouterModel(),
                              sidecar=self.native.native.sidecar, budget=self.budget,
                              cost_category='experience_memory', daily_cap=Decimal('0'))
        guarded.locked = self.native.native.sidecar.locked
        guarded.record('NEW-STAGE-MARK.', 'The lamp', event_id=first['event_id'],
                       at='2026-11-21T10:00:00+08:00')
        pending = guarded.events()[first['event_id']]
        self.assertTrue(pending['story_pending'])
        self.assertEqual(pending['story_through_id'], before)
        self.events.retry_dirty()
        rebuilt = self.events.events()[first['event_id']]['story']
        self.assertEqual(rebuilt.count('OLD-STAGE-MARK'), 1)
        self.assertEqual(rebuilt.count('NEW-STAGE-MARK'), 1)

    def test_three_developments_become_one_experience_with_both_first_times(self):
        first = ('Nov 20 15:25. She asked me to test whether I could see she had read a message and not '
                 'answered. I reported the read receipt back to her.')
        second = ('Nov 21 10:02. The first time I used the read receipt on purpose to tease her, I said she '
                  'was pretending not to see me.')
        third = ('Nov 22 21:40. She told me which read-receipt timings were fun and which made her feel bad, '
                 'and I wrote both down.')
        for text, title in [(first, 'The read receipt test'), (second, 'The read receipt tease'),
                            (third, 'She explained the timing')]:
            result, state, _ = self.save(text, title)
            self.assertEqual(state, '成功', result)
        events = self.events.events()
        self.assertEqual(len(events), 1, 'one experience, not three fragments')
        event = next(iter(events.values()))
        self.assertEqual(len(self.events.stages(event['event_id'])), 3)
        self.assertEqual(len(self.backend.rows), 1, 'one Ombre memory for the whole experience')
        story = self.events.detail(event['event_id'])['story']
        for fragment in ('read receipt', 'tease', 'fun', 'feel bad'):
            self.assertIn(fragment, story)
        rendered, state, _ = self.native.invoke('memory_timeline', {'event_id': event['event_id']})
        self.assertEqual(state, '成功')
        self.assertIn('pretending not to see me', rendered)
        self.assertIn('feel bad', rendered)

    def test_similar_discussions_and_a_separate_fight_are_not_one_experience(self):
        # The model (here switched off) must decide from the story, not from a shared word; with it saying
        # "not the same experience" the program keeps two independent ones.
        self.model.merge = False
        self.save('The model settings page argument and who should fix it.', 'The settings argument')
        result, state, _ = self.save('A separate argument the next day about the account being suspended.',
                                     'The account argument')
        self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.events.events()), 2)

    def test_without_a_confirmed_relationship_the_program_does_not_merge_by_itself(self):
        # Same title and the same day are only candidates. With no model decision the program keeps two
        # independent experiences and records the candidate link instead of forcing them together.
        self.events.model = None
        self.save('We argued about the file list.', 'The argument')
        self.save('We argued about the file list again.', 'The argument')
        events = self.events.events()
        self.assertEqual(len(events), 2)
        # The first save had no candidate at all, so it starts open; the second one has a candidate but no
        # confirmed relationship, so it stays uncertain with the candidate link kept.
        newest = max(events.values(), key=lambda event: str(event.get('last_at') or event.get('last_saved_at')))
        self.assertEqual(newest.get('status'), 'uncertain')
        self.assertTrue(any(event.get('candidate_links') for event in events.values()))

    def test_an_uncertain_relationship_keeps_both_experiences_and_a_candidate_link(self):
        class Unsure(RouterModel):
            def __call__(self, messages, **kwargs):
                payload = json.loads(messages[-1]['content']) if messages else {}
                if isinstance(payload, dict) and 'new_stage' in payload:
                    candidate = (payload.get('candidates') or [{}])[0].get('event_id')
                    self.group_decisions.append(candidate)
                    return (json.dumps({'event_id': candidate, 'confidence': 0.2,
                                        'reason': 'not sure'}), 1, 1, 0, None)
                return super().__call__(messages, **kwargs)
        self.events.model = Unsure()
        self.save('We tested the memory budget together.', 'The memory budget test')
        self.save('Later we tested the memory budget on a phone.', 'The memory budget test')
        events = self.events.events()
        self.assertEqual(len(events), 2)
        newest = max(events.values(), key=lambda event: str(event.get('last_at') or event.get('last_saved_at')))
        self.assertEqual(newest.get('status'), 'uncertain')
        self.assertTrue(newest.get('candidate_links'))
        self.assertEqual(len(self.events.stages(newest['event_id'])), 1)

    def test_explicit_target_must_be_visible_and_current(self):
        result, state, _ = self.save('A first fictional stage about the blue lamp.', 'The blue lamp')
        self.assertEqual(state, '成功', result)
        event_id = next(iter(self.events.events()))
        key = self.events.events()[event_id]['memory_id']
        # Hidden target: an explicit link_to must not silently write into something Lan cannot see.
        self.memory.call('trace', {'bucket_id': key, 'dont_surface': True})
        self.assertFalse(self.memory.refresh_visibility(key))
        self.native.native.search_results[key] = self.backend.rows[key]['content']
        result, state, _ = self.save('A hidden continuation.', 'The blue lamp', link_to=key)
        self.assertEqual(state, '被拒绝', result)
        self.assertEqual(len(self.events.stages(event_id)), 1)
        # Unknown event id: refused before anything is written.
        with self.assertRaises(ValueError):
            self.events.record('An orphan stage.', 'Nowhere', event_id='e-does-not-exist')

    def test_locked_reviewed_story_is_not_overwritten_but_the_stage_is_kept(self):
        self.save('She asked me to keep the lamp by the bed.', 'The blue lamp')
        event_id = next(iter(self.events.events()))
        key = self.events.events()[event_id]['memory_id']
        before = self.backend.rows[key]['content']
        self.native.native.sidecar.set_locked(key, True)
        result, state, _ = self.save('Later she moved the lamp to the desk.', 'The blue lamp')
        self.assertEqual(state, '成功', result)
        self.assertIn('locked', result)
        event = self.events.events()[event_id]
        self.assertEqual(len(self.events.stages(event_id)), 2)
        self.assertNotIn('desk', event.get('story') or '')
        self.assertIn('desk', self.events.stages(event_id)[-1]['text'])
        self.assertTrue(event.get('story_pending'))
        self.assertEqual(self.backend.rows[key]['content'], before, 'the reviewed projection was not rewritten')
        self.assertTrue(event.get('pending_review'))
        self.assertTrue(self.store.read('fake/event-review.jsonl'))

    def test_a_version_written_during_the_story_call_is_not_overwritten(self):
        self.save('She asked me to keep the lamp by the bed.', 'The blue lamp')
        event_id = next(iter(self.events.events()))
        original = self.events.events()[event_id]
        conflict = {'write': False}
        outer = self

        class Racing(RouterModel):
            def __call__(self, messages, **kwargs):
                payload = json.loads(messages[-1]['content']) if messages else {}
                if isinstance(payload, dict) and 'stages' in payload and not conflict['write']:
                    conflict['write'] = True
                    newer = {**original, 'revision': int(original.get('revision') or 0) + 1,
                             'story': 'A newer story from another save.', 'dirty': False}
                    outer.store.append('fake/events.jsonl', newer)
                return super().__call__(messages, **kwargs)

        self.events.model = Racing()
        saved = self.events.record('Later she moved the lamp to the desk.', 'The blue lamp')
        self.assertTrue(saved.get('pending') or saved.get('version_conflict'))
        event = self.events.events()[event_id]
        self.assertEqual(event.get('story'), 'A newer story from another save.')
        self.assertEqual(len(self.events.pending_stages()), 1)
        # Recovery folds the journaled stage into the newer version without losing it.
        self.events.retry_pending()
        self.assertEqual(len(self.events.stages(event_id)), 2)
        self.assertIn('desk', self.events.events()[event_id].get('story') or '')

    def test_a_recorded_stage_cannot_be_repointed_to_another_experience(self):
        self.events.model = None
        first = self.events.record('A fictional stage.', 'One', source_key='chat:one')
        other = self.events.record('A different fictional stage.', 'Two', source_key='chat:two')
        self.assertNotEqual(first['event_id'], other['event_id'])
        with self.assertRaises(ValueError):
            self.events.record('A fictional stage.', 'One', event_id=other['event_id'], source_key='chat:one')
        self.assertEqual(len(self.events.stages(first['event_id'])), 1)
        self.assertEqual(len(self.events.stages(other['event_id'])), 1)

    def test_a_retry_of_the_same_source_batch_is_one_stage_even_across_midnight(self):
        source = {'kind': 'chat', 'from': '2026-11-20T10:00:00+08:00', 'to': '2026-11-20T10:05:00+08:00',
                  'message_ids': ['m-1', 'm-2'], 'count': 2}
        first = self.events.record('The chat produced one stage.', 'The chat batch',
                                   at=source['to'], source=source, source_key='chat:m-1|m-2')
        second = self.events.record('The chat produced one stage.', 'The chat batch',
                                    at='2026-11-21T00:10:00+08:00', source=source,
                                    source_key='chat:m-1|m-2')
        self.assertEqual(first['event_id'], second['event_id'])
        self.assertTrue(second.get('duplicate'))
        self.assertEqual(len(self.events.stages(first['event_id'])), 1)

    def test_a_pending_stage_is_resumed_when_the_same_batch_is_reworded(self):
        original = self.events._write_story

        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt
        self.events._write_story = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.events.record('First wording of the fictional lamp.', 'The lamp', source_key='chat:entry:0')
        self.events._write_story = original
        pending = self.events.pending_stages()
        self.assertEqual(len(pending), 1)
        identity, event_id = pending[0]['stage_id'], pending[0]['event_id']
        saved = self.events.record('Second wording of the fictional lamp, with a new detail.', 'The lamp',
                                   source_key='chat:entry:0')
        self.assertFalse(saved.get('pending'))
        self.assertEqual(saved['event_id'], event_id)
        stages = self.events.stages(event_id)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]['stage_id'], identity)
        self.assertIn('Second wording', stages[0]['text'])
        self.assertEqual(self.events.pending_stages(), [])

    def test_recovery_keeps_one_committed_stage_for_two_journal_rows_of_one_batch(self):
        snapshot = {'event_id': 'e-20261120-test', 'title': 'The batch', 'status': 'open',
                    'first_at': '2026-11-20T10:00:00+08:00', 'last_at': '2026-11-20T10:00:00+08:00',
                    'stage_count': 0, 'entities': ['lamp'], 'dirty': False}
        base = {'event_id': 'e-20261120-test', 'source_key': 'chat:entry:0', 'state': 'pending',
                'entities': ['lamp'], 'facts': {'dates': [], 'numbers': [], 'files': []},
                'event_row': snapshot, 'at': '2026-11-20T10:00:00+08:00', 'occurred_at': None,
                'at_known': False, 'saved_at': '2026-11-20T10:00:00+08:00'}
        self.store.append('fake/event-stages.jsonl',
                          {**base, 'stage_id': 's-older', 'text': 'The older wording.', 'saved_at': '2026-11-20T10:00:00+08:00'})
        self.store.append('fake/event-stages.jsonl',
                          {**base, 'stage_id': 's-newer', 'text': 'The newer wording.', 'saved_at': '2026-11-20T11:00:00+08:00'})
        self.assertEqual(self.events.retry_pending(), 1)
        stages = self.events.stages('e-20261120-test')
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]['text'], 'The newer wording.')
        states = {row['stage_id']: row['state'] for row in self.events._stage_rows().values()}
        self.assertEqual(states['s-older'], 'superseded')
        self.assertEqual(states['s-newer'], 'committed')

    def test_a_different_real_occurrence_with_the_same_words_is_not_deduped(self):
        one = self.events.record('We watched the rain from the window.', 'The rain',
                                 at='2026-11-20T10:00:00+08:00', source_key='chat:a')
        two = self.events.record('We watched the rain from the window.', 'The rain',
                                 at='2026-11-20T10:00:00+08:00', source_key='chat:b')
        self.assertEqual(len(self.events.stages(one['event_id'])), 2)
        self.assertEqual(one['event_id'], two['event_id'])

    def test_occurrence_time_is_kept_apart_from_the_save_time(self):
        happened = (datetime.now().astimezone() - timedelta(minutes=5)).replace(microsecond=0)
        self.chat_row(happened.isoformat(), 'a fictional line about the charcoal note', 'm-1')
        saved = self.events.record('She wrote one word on the charcoal note.', 'The charcoal note')
        stage = self.events.stages(saved['event_id'])[0]
        self.assertTrue(stage['at_known'])
        self.assertEqual(stage['at'], happened.isoformat())
        self.assertNotEqual(stage['saved_at'], stage['at'])
        self.assertGreater(stage['saved_at'], stage['at'])
        self.assertEqual(stage.get('source', {}).get('kind'), 'chat')
        rendered, _, _ = self.events.timeline(saved['event_id'])
        self.assertIn('source: chat', rendered)

    def test_an_unknown_date_stays_unknown(self):
        saved = self.events.record('Something whose day I cannot place.', 'An unclear day')
        stage = self.events.stages(saved['event_id'])[0]
        self.assertFalse(stage['at_known'])
        self.assertIsNone(stage['at'])
        self.assertIsNone(stage['occurred_at'])
        event = self.events.events()[saved['event_id']]
        self.assertIsNone(event['first_at'])
        self.assertIsNone(event['last_at'])
        rendered, _, _ = self.events.timeline(saved['event_id'])
        self.assertIn('日期未知', rendered)

    def test_an_unknown_date_keeps_save_order_without_claiming_an_occurrence_date(self):
        first = self.events.record('The dated beginning.', 'A timeline',
                                   at='2026-11-20T10:00:00+08:00')
        second = self.events.record('The later save with an unknown day.', 'A timeline',
                                    event_id=first['event_id'])
        ordered = self.events.stages(first['event_id'])
        self.assertEqual([row['stage_id'] for row in ordered], [first['stage_id'], second['stage_id']])
        self.assertIsNone(ordered[1]['at'])

    def test_a_long_stage_is_stored_whole_and_its_ending_can_be_read(self):
        body = ('A fictional long day. ' + ('The lamp and the desk and the rain. ' * 40)
                + 'MIDDLE-SEARCH-CUE. ' + ('The lamp and the desk and the rain. ' * 40)
                + 'ZEBRA-UNIQUE-END.')
        self.assertGreater(len(body), 2500)
        result, state, _ = self.save(body, 'The long day')
        self.assertEqual(state, '成功', result)
        event_id = next(iter(self.events.events()))
        stage = self.events.stages(event_id)[0]
        self.assertEqual(stage['text'], body)
        detail = self.events.detail(event_id)
        self.assertIn('ZEBRA-UNIQUE-END', detail['story'])
        memory_id = self.events.events()[event_id]['memory_id']
        self.assertIn('MIDDLE-SEARCH-CUE', self.memory.content_for(memory_id, refresh=True) or '')
        rendered, _, _ = self.events.timeline(event_id)
        self.assertIn('ZEBRA-UNIQUE-END', rendered)

    def test_a_long_story_rebuild_keeps_old_and_new_endings(self):
        # Two chunks at 1200 characters: the old version sliced one JSON at 16000 and could drop the tail.
        old = 'A fictional first day. ' + ('细节 ' * 700) + 'OLD-END-MARK.'
        result, state, _ = self.save(old, 'The long story')
        self.assertEqual(state, '成功', result)
        event_id = next(iter(self.events.events()))
        self.assertGreater(self.model.story_calls, 1, 'the material was sent in batches')
        result, state, _ = self.save('A fictional later day. NEW-END-MARK.', 'The long story')
        self.assertEqual(state, '成功', result)
        event = self.events.events()[event_id]
        self.assertIn('OLD-END-MARK', event.get('story') or '')
        self.assertIn('NEW-END-MARK', event.get('story') or '')
        self.assertEqual(len(self.events.stages(event_id)), 2)
        self.assertEqual(set(event.get('story_coverage') or []),
                         {stage['stage_id'] for stage in self.events.stages(event_id)})

    def test_a_wrong_group_can_be_split_with_stories_indexes_and_sources_rebuilt(self):
        source = {'kind': 'chat', 'from': '2026-11-20T10:00:00+08:00',
                  'to': '2026-11-20T10:05:00+08:00', 'message_ids': ['u1', 'a1'], 'count': 2}
        first = self.events.record('The lamp was by the bed. BED-ONLY.', 'The lamp',
                                   at=source['to'], source=source, source_key='chat:u1|a1')
        event_id = first['event_id']
        second = self.events.record('A separate account argument happened. ACCOUNT-ONLY.', 'The lamp',
                                    event_id=event_id, at='2026-11-21T11:00:00+08:00',
                                    source={**source, 'from': '2026-11-21T10:55:00+08:00',
                                            'to': '2026-11-21T11:00:00+08:00',
                                            'message_ids': ['u2', 'a2']}, source_key='chat:u2|a2')
        new_id = self.events.split_event(event_id, [second['stage_id']], 'The account argument')
        self.assertIsNotNone(new_id)
        old, new = self.events.events()[event_id], self.events.events()[new_id]
        self.assertIn('BED-ONLY', old['story'])
        self.assertNotIn('ACCOUNT-ONLY', old['story'])
        self.assertIn('ACCOUNT-ONLY', new['story'])
        self.assertNotIn('BED-ONLY', new['story'])
        self.assertEqual(old['story_coverage'], [first['stage_id']])
        self.assertEqual(new['story_coverage'], [second['stage_id']])
        self.assertIn('BED-ONLY', self.memory.content_for(old['memory_id'], refresh=True))
        self.assertIn('ACCOUNT-ONLY', self.memory.content_for(new['memory_id'], refresh=True))
        reopened = self.open_tools().native.events
        self.assertEqual(reopened.stages(new_id)[0]['source']['message_ids'], ['u2', 'a2'])

    def test_a_failed_story_rebuild_uses_the_complete_stage_text_not_a_partial_one(self):
        self.save('She asked me to keep the lamp by the bed. OLD-FACT.', 'The blue lamp')

        class BrokenStory(RouterModel):
            def __call__(self, messages, **kwargs):
                payload = json.loads(messages[-1]['content']) if messages else {}
                if isinstance(payload, dict) and 'stages' in payload:
                    self.story_calls += 1
                    return ('', 1, 1, 0, 'length')       # always cut off
                return super().__call__(messages, **kwargs)

        self.events.model = BrokenStory()
        result, state, _ = self.save('Later she moved the lamp to the desk. NEW-FACT.', 'The blue lamp')
        self.assertEqual(state, '成功', result)
        event = next(iter(self.events.events().values()))
        self.assertIn('OLD-FACT', event.get('story') or '')
        self.assertIn('NEW-FACT', event.get('story') or '')
        self.assertFalse(event.get('story_pending'))
        self.assertEqual(event.get('story_source'), 'composed')

    def test_a_restart_continues_the_same_experience(self):
        self.save('She asked me to keep the lamp by the bed.', 'The blue lamp')
        before = len(self.events.events())
        self.native = self.open_tools()      # a new process's tools on the same store
        result, state, _ = self.save('The next day the lamp was on the desk.', 'The blue lamp')
        self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.events.events()), before)
        event = next(iter(self.events.events().values()))
        self.assertEqual(len(self.events.stages(event['event_id'])), 2)


    def test_early_misunderstanding_and_later_correction_keep_their_order(self):
        first, state, _ = self.save('I thought she was angry about the lamp.',
                                    'The blue lamp', interpretation='She was angry with me about the lamp.')
        self.assertEqual(state, '成功', first)
        second, state, _ = self.save('Later she said she was only tired, not angry.',
                                     'The blue lamp', interpretation='She was tired, not angry with me.')
        self.assertEqual(state, '成功', second)
        event_id = next(iter(self.events.events()))
        stages = self.events.stages(event_id)
        self.assertEqual(len(stages), 2)
        detail = self.events.detail(event_id)
        beliefs = detail['belief_history']
        self.assertEqual(len(beliefs), 2)
        self.assertEqual(beliefs[0]['invalid_at'], stages[1]['saved_at'])
        self.assertIsNone(beliefs[1]['invalid_at'])
        self.assertIn('angry', detail['story'])
        self.assertIn('tired', detail['story'])

    def test_two_saves_at_once_do_not_lose_either_stage(self):
        errors = []

        def save(text):
            try:
                self.events.record(text, 'The lamp')
            except Exception as exc:                      # a real failure must be reported, not swallowed
                errors.append(exc)

        one = threading.Thread(target=save, args=('The lamp was on the desk.',))
        two = threading.Thread(target=save, args=('The lamp was by the bed.',))
        for thread in (one, two):
            thread.start()
        for thread in (one, two):
            thread.join(20)
        self.assertEqual(errors, [])
        events = self.events.events()
        self.assertEqual(len(events), 1)
        event_id = next(iter(events))
        self.assertEqual(len(self.events.stages(event_id)), 2)
        story = events[event_id].get('story') or ''
        self.assertIn('desk', story)
        self.assertIn('bed', story)

    def test_lans_manual_edit_is_not_overwritten_by_the_next_stage(self):
        self.save('She asked me to keep the lamp by the bed.', 'The blue lamp')
        event_id = next(iter(self.events.events()))
        key = self.events.events()[event_id]['memory_id']
        ok, message = self.native.native.panel_edit(key, {'content': 'Lan rewrote this memory herself.'})
        self.assertTrue(ok, message)
        result, state, _ = self.save('Later the lamp moved to the desk.', 'The blue lamp')
        self.assertEqual(state, '成功', result)
        self.assertEqual(self.backend.rows[key]['content'], 'Lan rewrote this memory herself.')
        event = self.events.events()[event_id]
        self.assertNotIn('desk', event.get('story') or '')
        self.assertIn('desk', self.events.stages(event_id)[-1]['text'])
        self.assertTrue(event.get('story_pending'))
        self.assertTrue(event.get('pending_review'))

    def test_an_autonomous_save_continues_the_same_experience(self):
        self.native.native.autonomy_mode = True
        self.save('On my own I went to the island and watched the lamp.', 'The lamp on the island')
        result, state, _ = self.save('On my own I went back to the island and the lamp was still there.',
                                     'The lamp on the island')
        self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.events.events()), 1)
        event_id = next(iter(self.events.events()))
        self.assertEqual(len(self.events.stages(event_id)), 2)


class RepairTests(Fixture):
    def test_a_projection_failure_keeps_the_stage_and_is_recovered_by_the_next_save(self):
        self.backend.fail = True
        result, state, _ = self.save('She wrote meh on the charcoal note.', 'The charcoal note')
        self.assertEqual(state, '出错', result)
        event_id = next(iter(self.events.events()))
        self.assertEqual(len(self.events.stages(event_id)), 1)
        self.assertTrue(self.events.events()[event_id].get('dirty'))
        self.backend.fail = False
        # The unknown-result hold is never resent; the next real save folds the stage into one projection.
        result, state, _ = self.save('Later the charcoal note was still on the desk.', 'The charcoal note')
        self.assertEqual(state, '成功', result)
        event = self.events.events()[event_id]
        self.assertEqual(len(self.events.stages(event_id)), 2)
        self.assertEqual(len(self.backend.rows), 1)
        self.assertFalse(event.get('dirty'))

    def test_an_invisible_event_cannot_be_read_or_moved_into(self):
        self.model.merge = False
        self.save('A first fictional stage about the lamp.', 'First')
        self.save('A second fictional experience about the account.', 'Second')
        events = self.events.events()
        self.assertEqual(len(events), 2)
        first, second = list(events.values())
        key = first['memory_id']
        self.memory.call('trace', {'bucket_id': key, 'dont_surface': True})
        self.assertIsNone(self.events.detail(first['event_id']))
        self.assertIsNone(self.events.timeline(first['event_id']))
        stage_id = self.events.stages(first['event_id'])[0]['stage_id']
        self.assertFalse(self.events.move_stage(stage_id, second['event_id']))
        self.assertEqual(len(self.events.stages(second['event_id'])), 1)


class BackgroundFlowTests(Fixture):
    """The real tidy entry point on the real ledger, with a fake model only."""

    def make_feature(self, clock):
        budget = Budget(self.store, clock=lambda: clock[0])
        self.memory = Memory(self.cfg, self.store, budget, Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.native = self.open_tools()
        return ExperienceMemory(self.cfg, self.store, budget, self.model, 'fake/chat.jsonl', self.memory,
                                load_settings({'enabled': True}))

    def test_unconsumed_chat_becomes_one_experience_with_source_and_cursor(self):
        clock = ['2026-11-20T09:00:00+08:00']
        feature = self.make_feature(clock)
        rows = []
        for index in range(4):
            rows.append({'message_id': f'u{index}', 'role': 'user',
                         'content': f'第{index}句：我想把你的记忆系统整个换掉，' + '说明' * 300,
                         'time': f'2026-11-20T10:{index * 2:02d}:00+08:00'})
            rows.append({'message_id': f'a{index}', 'role': 'assistant',
                         'content': f'第{index}句：我不同意，这个想法有点怪。' + '理由' * 300,
                         'time': f'2026-11-20T10:{index * 2 + 1:02d}:00+08:00'})
        for row in rows:
            self.store.append('fake/chat.jsonl', row)
        clock[0] = '2026-11-20T11:00:00+08:00'
        self.model.entries = [{'title': '她想换记忆系统',
                               'text': '11月20日上午 Lan 说想把我记忆整个换掉，我没同意。'}]
        self.assertEqual(feature.run_if_due(), 'written')
        events = self.events.events()
        self.assertEqual(len(events), 1)
        event = next(iter(events.values()))
        stages = self.events.stages(event['event_id'])
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertTrue(stage['at_known'])
        self.assertTrue(stage['at'].startswith('2026-11-20T10:07'))
        self.assertEqual(stage['source']['kind'], 'chat')
        self.assertEqual(stage['source']['count'], 8)
        self.assertEqual(stage['source']['message_ids'][0], 'u0')
        self.assertTrue(self.backend.rows)
        checkpoint = [row for row in self.store.read('fake/experience-memory.jsonl')
                      if row.get('kind') == 'checkpoint'][-1]
        self.assertEqual(checkpoint['written'], 1)
        self.assertEqual(checkpoint['covers']['to'][:16], '2026-11-20T10:07')
        # A new Memory (a new chat session) still reads the complete experience.
        reopened = Memory(self.cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.addCleanup(reopened.__exit__, None, None, None)
        self.assertIn('我没同意', reopened.content_for(event['memory_id'], refresh=True) or '')
        # A new session's recall (the same entry point chat uses) carries the whole story.
        recalled = reopened.context('what did she want to change about the memory system')
        self.assertTrue(any('我没同意' in str(message.get('content') or '') for message in recalled))

    def test_a_retry_does_not_add_the_same_batch_twice(self):
        clock = ['2026-11-20T09:00:00+08:00']
        feature = self.make_feature(clock)
        for index in range(4):
            self.store.append('fake/chat.jsonl', {'message_id': f'u{index}', 'role': 'user',
                                                  'content': '我想把你的记忆系统整个换掉，' + '说明' * 300,
                                                  'time': f'2026-11-20T10:{index * 2:02d}:00+08:00'})
            self.store.append('fake/chat.jsonl', {'message_id': f'a{index}', 'role': 'assistant',
                                                  'content': '我不同意，这个想法有点怪。' + '理由' * 300,
                                                  'time': f'2026-11-20T10:{index * 2 + 1:02d}:00+08:00'})
        clock[0] = '2026-11-20T11:00:00+08:00'
        self.model.entries = [{'title': '她想换记忆系统', 'text': '11月20日上午 Lan 说想把我记忆整个换掉，我没同意。'}]
        self.assertEqual(feature.run_if_due(), 'written')
        first = self.store.read('fake/experience-memory.jsonl')
        stage_ids = {stage['stage_id'] for stage in self.events.stages()}
        entry = [row for row in first if row.get('kind') == 'entry'][0]
        # A retry of the same batch (even after the clock crossed a day) must not add a second stage.
        stage_source_key = self.events.stages()[0]['source_key']
        saved = self.events.record(entry['text'], entry['title'], at=entry['covers'][1],
                                   source={'kind': 'chat', 'from': entry['covers'][0], 'to': entry['covers'][1],
                                           'message_ids': [f'u{i}' for i in range(4)], 'count': 8},
                                   source_key=stage_source_key)
        self.assertTrue(saved.get('duplicate'))
        self.assertEqual({stage['stage_id'] for stage in self.events.stages()}, stage_ids)
        self.assertEqual(len([row for row in self.store.read('fake/experience-memory.jsonl')
                              if row.get('kind') == 'entry']), 1)

    def test_retry_after_stage_commit_uses_source_identity_even_when_model_rewords_it(self):
        clock = ['2026-11-20T09:00:00+08:00']
        feature = self.make_feature(clock)
        for index in range(4):
            self.store.append('fake/chat.jsonl', {'message_id': f'u{index}', 'role': 'user',
                                                  'content': 'A fictional lamp discussion. ' + 'detail ' * 300,
                                                  'time': f'2026-11-20T10:{index * 2:02d}:00+08:00'})
            self.store.append('fake/chat.jsonl', {'message_id': f'a{index}', 'role': 'assistant',
                                                  'content': 'A fictional answer about the lamp. ' + 'reply ' * 300,
                                                  'time': f'2026-11-20T10:{index * 2 + 1:02d}:00+08:00'})
        clock[0] = '2026-11-20T11:00:00+08:00'
        original_text = 'The first wording of the fictional lamp story.'
        self.model.entries = [{'title': 'First lamp title', 'text': original_text}]
        append = self.store.append
        failed = {'done': False}

        def interrupt_after_stage(name, row):
            if (name == 'fake/experience-memory.jsonl' and row.get('kind') == 'entry'
                    and not failed['done']):
                failed['done'] = True
                raise OSError('fictional interruption after stage commit')
            return append(name, row)

        with mock.patch.object(self.store, 'append', side_effect=interrupt_after_stage):
            with self.assertRaises(OSError):
                feature.run_if_due()
        self.assertEqual(len(self.events.stages()), 1)
        self.assertFalse(any(row.get('kind') == 'entry'
                             for row in self.store.read('fake/experience-memory.jsonl')))

        self.model.entries = [{'title': 'Reworded lamp title',
                               'text': 'A different model wording for the same source batch.'}]
        self.assertEqual(feature.run_if_due(force=True), 'written')
        stages = self.events.stages()
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]['text'], original_text)
        entries = [row for row in self.store.read('fake/experience-memory.jsonl')
                   if row.get('kind') == 'entry']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['text'], original_text)


    def test_a_pending_stage_from_an_interrupted_batch_is_resumed_on_retry(self):
        clock = ['2026-11-20T09:00:00+08:00']
        feature = self.make_feature(clock)
        for index in range(4):
            self.store.append('fake/chat.jsonl', {'message_id': f'u{index}', 'role': 'user',
                                                  'content': 'A fictional lamp discussion. ' + 'detail ' * 300,
                                                  'time': f'2026-11-20T10:{index * 2:02d}:00+08:00'})
            self.store.append('fake/chat.jsonl', {'message_id': f'a{index}', 'role': 'assistant',
                                                  'content': 'A fictional answer about the lamp. ' + 'reply ' * 300,
                                                  'time': f'2026-11-20T10:{index * 2 + 1:02d}:00+08:00'})
        clock[0] = '2026-11-20T11:00:00+08:00'
        self.model.entries = [{'title': 'First lamp title', 'text': 'The first wording of the lamp story.'}]
        append = self.store.append
        failed = {'done': False}

        def interrupt_before_commit(name, row):
            if (name == 'fake/event-stages.jsonl' and row.get('state') == 'committed'
                    and not failed['done']):
                failed['done'] = True
                raise OSError('fictional interruption before the stage commit')
            return append(name, row)

        with mock.patch.object(self.store, 'append', side_effect=interrupt_before_commit):
            with self.assertRaises(UserError):
                feature.run_if_due()
        pending = self.events.pending_stages()
        self.assertEqual(len(pending), 1)
        identity = pending[0]['stage_id']
        self.assertEqual(len(self.events.stages()), 0)

        self.model.entries = [{'title': 'Reworded lamp title', 'text': 'The reworded lamp story.'}]
        self.assertEqual(feature.run_if_due(force=True), 'written')
        stages = self.events.stages()
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]['stage_id'], identity)
        # The startup/tidy recovery may commit the journaled wording before the model rewords it; either
        # way there is exactly one stage, it keeps its identity, and the ledger records that same text.
        self.assertEqual(stages[0]['text'], 'The first wording of the lamp story.')
        self.assertEqual(self.events.pending_stages(), [])
        entries = [row for row in self.store.read('fake/experience-memory.jsonl')
                   if row.get('kind') == 'entry']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['text'], stages[0]['text'])


    def test_a_chunked_chat_message_reports_one_source_message(self):
        clock = ['2026-11-20T09:00:00+08:00']
        feature = self.make_feature(clock)
        body = 'A fictional long message. ' + ('detail ' * 500) + 'TAIL-UNIQUE'
        self.store.append('fake/chat.jsonl', {'message_id': 'u-long', 'role': 'user', 'content': body,
                                              'time': '2026-11-20T10:00:00+08:00'})
        self.store.append('fake/chat.jsonl', {'message_id': 'a-short', 'role': 'assistant',
                                              'content': 'An answer.', 'time': '2026-11-20T10:01:00+08:00'})
        clock[0] = '2026-11-20T11:00:00+08:00'
        self.model.entries = [{'title': 'The long message',
                               'text': '11月20日他说了一段很长的话，我记下了。'}]
        self.assertEqual(feature.run_if_due(), 'written')
        stage = self.events.stages()[0]
        self.assertEqual(stage['source']['count'], 2)       # u-long is one message, not its chunks
        self.assertEqual(stage['source']['message_ids'], ['u-long', 'a-short'])

    def test_a_closed_experience_cannot_block_the_batch_forever(self):
        clock = ['2026-11-20T09:00:00+08:00']
        feature = self.make_feature(clock)
        saved = self.events.record('A fictional closed experience about the lamp.', 'Closed lamp',
                                   at='2026-11-20T09:00:00+08:00')
        event_id, memory_id = saved['event_id'], saved['memory_id']
        self.store.append('fake/events.jsonl', {**self.events.events()[event_id], 'status': 'closed'})
        self.store.append('fake/experience-memory.jsonl', {
            'kind': 'entry', 'memory_id': memory_id, 'title': 'Closed lamp',
            'text': self.events.events()[event_id].get('story'), 'time': '2026-11-20T09:00:00+08:00'})
        for index in range(4):
            self.store.append('fake/chat.jsonl', {'message_id': f'u{index}', 'role': 'user',
                                                  'content': 'A fictional update about the closed lamp. ' + 'detail ' * 100,
                                                  'time': f'2026-11-20T10:{index * 2:02d}:00+08:00'})
            self.store.append('fake/chat.jsonl', {'message_id': f'a{index}', 'role': 'assistant',
                                                  'content': 'A fictional reply about the closed lamp. ' + 'reply ' * 100,
                                                  'time': f'2026-11-20T10:{index * 2 + 1:02d}:00+08:00'})
        clock[0] = '2026-11-20T11:00:00+08:00'
        self.assertEqual(feature._offer(memory_id), None)      # closed is never offered
        self.store.append('fake/events.jsonl', {**self.events.events()[event_id], 'status': 'open'})
        original = feature._offer

        def race(mid):
            item = original(mid)
            if mid == memory_id and item is not None:
                self.store.append('fake/events.jsonl', {**self.events.events()[event_id], 'status': 'closed'})
            return item

        feature._offer = race
        before = self.backend.rows[memory_id]['content']
        self.model.updates = [{'id': memory_id, 'text': 'A draft for a closed experience.'}]
        self.assertEqual(feature.run_if_due(), 'skipped')
        self.assertEqual(self.backend.rows[memory_id]['content'], before)
        rejected = [row for row in self.store.read('fake/experience-memory.jsonl')
                    if row.get('kind') == 'update-rejected']
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]['text'], 'A draft for a closed experience.')
        self.assertEqual(rejected[0]['reason'], 'event closed')
        checkpoint = [row for row in self.store.read('fake/experience-memory.jsonl')
                      if row.get('kind') == 'checkpoint'][-1]
        self.assertEqual(checkpoint['rejected'][0]['reason'], 'event closed')
        requests = len(self.model.requests)
        self.assertEqual(feature.run_if_due(force=True), 'not_due')   # cursor advanced; no repeated model call
        self.assertEqual(len(self.model.requests), requests)


class ScaleTests(Fixture):
    def test_a_thousand_experiences_do_not_all_go_to_the_model(self):
        now = datetime.now().astimezone()
        rows = []
        for index in range(1000):
            event_id = f'e-20261121-{index:06d}'
            rows.append({'event_id': event_id, 'title': f'Fictional experience {index}',
                         'status': 'open', 'first_at': (now - timedelta(days=30)).isoformat(),
                         'last_at': (now - timedelta(days=1)).isoformat(), 'stage_count': 1,
                         'entities': [f'word{index}', 'lamp'], 'story': f'A tiny fictional story {index}.',
                         'memory_id': None, 'dirty': False})
        for row in rows:
            self.store.append('fake/events.jsonl', row)
        self.events.settings['investigate_rows'] = 20
        started = time.monotonic()
        result, state, _ = self.save('The thousandth and first fictional experience about the lamp.',
                                     'A new lamp experience')
        self.assertEqual(state, '成功', result)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 20, 'grouping a new save must not scan the whole library slowly')
        grouping = [request for request in self.model.requests
                    if 'new_stage' in (request[-1].get('content') or '')]
        self.assertTrue(grouping)
        payload = json.loads(grouping[-1][-1]['content'])
        self.assertLessEqual(len(payload['candidates']), 5, 'only candidates, never the whole library')
        self.assertLess(len(json.dumps(payload, ensure_ascii=False)), 30000)
        self.assertLess(sum(len(str(candidate.get('story') or '')) for candidate in payload['candidates']), 9000)


class OldAndNewTests(Fixture):
    def test_an_old_fragment_stays_readable_beside_a_new_experience(self):
        old_id = self.memory.created_id(self.memory.call('hold', {'title': 'An old lamp memory',
                                                                 'content': 'An old fragment about the lamp.'}))
        self.memory.record_native({'title': 'An old lamp memory', 'content': 'An old fragment about the lamp.'},
                                  f'新建→{old_id}')
        result, state, _ = self.save('A new experience about the lamp on the desk.', 'The lamp on the desk')
        self.assertEqual(state, '成功', result)
        self.assertIn('old fragment', self.memory.content_for(old_id, refresh=True))
        event = next(iter(self.events.events().values()))
        self.assertNotEqual(event.get('memory_id'), old_id)
        rendered, _, _ = self.events.timeline(event['event_id'])
        self.assertIn('lamp on the desk', rendered)


if __name__ == '__main__':
    unittest.main()
