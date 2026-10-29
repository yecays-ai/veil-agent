"""Exercise shell-confirmed text -> persisted glance -> repeated/reopened chat requests."""
import json
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import test_chat
from agent_types import now
from autonomy import GlanceController
from recent_experiences import (begin_glance, record_glance, recent_experience_context,
                                record_screen_message)
from veil_agent import Chat, Store, ActivityState
from web_agent import Hub


class GlanceContinuityTests(unittest.TestCase):
    setUp = test_chat.ChatTests.setUp

    def shell(self, started_at, text='Caught you browsing the fictional AI forum!'):
        hub = Hub('Test', ActivityState(self.store.root / 'test/resident-state.json'))
        hub.chat = self.chat
        pop_id = 'a' * 32
        hub.screen_pop_requests[pop_id] = {
            'id': pop_id, 'command': {'text': text, 'count': 1},
            'request_id': 'fictional-request', 'call_id': 'fictional-call',
            'autonomy_run_id': 'glance:' + started_at, 'style': 'danmaku',
            'expected': None, 'created': time.monotonic(), 'shown': False,
            'failure': None, 'cancelled': False, 'events': set(), 'completed': set(),
        }
        return hub, pop_id

    def glance_row(self, started_at):
        return {
            'started_at': started_at, 'foreground': {
                'application': 'Chrome', 'title': 'Fictional AI Forum',
                'raw_uia': 'PRIVATE RAW WINDOW CONTENT'},
            'decision': 'I noticed a discussion about AI companions and made a joke.',
            'tools': [{'tool': 'screen_pop', 'path': 'danmaku', 'state': '成功'}],
            'result': '完成', 'result_reason': 'Displayed one joke.',
        }

    def shown_glance(self):
        started_at = now()
        begin_glance(self.store, self.cfg, started_at)
        hub, pop_id = self.shell(started_at)
        self.assertTrue(hub.record_screen_pop_event({
            'id': pop_id, 'event': 'shown', 'style': 'danmaku', 'total': 1}))
        self.assertTrue(hub.record_screen_pop_event({
            'id': pop_id, 'event': 'finished', 'item': 1}))
        record_glance(self.store, self.cfg, self.glance_row(started_at))
        return started_at

    def model_context(self):
        return '\n'.join(str(message.get('content', ''))
                         for message in self.model.call_args.args[0])

    def test_reply_to_danmaku_has_exact_text_window_time_across_turns_and_restart(self):
        stamp = self.shown_glance()
        for text in ('Why did you say that?', 'What was the window?', 'Continue after restart'):
            # Real persisted Store is reopened; this is not an in-memory handoff.
            reopened = Chat(self.cfg, Store(self.store.root), self.budget, self.model)
            reopened.send(text)
            context = self.model_context()
            for fact in (stamp, 'Caught you browsing the fictional AI forum!',
                         'Fictional AI Forum', 'AI companions', 'danmaku dismissed/finished'):
                self.assertIn(fact, context)
            self.assertIn('not a new event on each chat turn', context)
            self.assertIn('Do not announce or repeat', context)
            self.assertNotIn('No message text is stored here', context)
        self.assertNotIn('Caught you', json.dumps(self.store.read('test/chat.jsonl')))
        self.assertNotIn('PRIVATE RAW WINDOW CONTENT',
                         json.dumps(self.store.read('test/recent-experiences.json')))
        self.assertNotIn('Caught you', json.dumps(self.store.read('test/tools.jsonl')))

    def test_next_glance_replaces_old_at_start_even_if_new_observation_fails(self):
        self.shown_glance()
        cfg = replace(self.cfg, glance={'enabled': True})
        chat = Chat(cfg, self.store, self.budget, self.model)
        hub = Hub('Test', ActivityState(self.store.root / 'test/resident-state.json'))
        glance = GlanceController(chat, hub, clock=object())

        def failed_observation():
            # The old text must already be gone before any new observation/model call.
            context = recent_experience_context(self.store, cfg)
            self.assertNotIn('Caught you', context)
            self.assertNotIn('Fictional AI Forum', context)
            return None, None, 'Observation unavailable.'

        glance._observe = failed_observation
        glance.run()
        chat.send('What just happened?')
        context = self.model_context()
        self.assertNotIn('Caught you', context)
        self.assertIn('Observation unavailable.', context)
        self.assertEqual(self.model.call_count, 1)

    def test_glance_model_account_is_saved_but_live_uia_is_not(self):
        cfg = replace(self.cfg, glance={'enabled': True})
        chat = Chat(cfg, self.store, self.budget, self.model)
        activity = ActivityState(self.store.root / 'test/resident-state.json')
        activity.set(True)
        hub = Hub('Test', activity)
        glance = GlanceController(chat, hub, clock=object())
        glance._observe = Mock(return_value=(
            {'application': 'Chrome', 'title': 'Fictional AI Forum'},
            'PRIVATE RAW WINDOW CONTENT', None))
        glance._desktop_is_open = lambda: True
        self.model.return_value = ('I saw a fictional forum discussing AI companions.', 100, 20, 0)
        glance.run()
        self.assertIn('PRIVATE RAW WINDOW CONTENT', self.model_context())
        self.assertIn('private continuity note', self.model_context())
        self.chat.send('What was I reading?')
        context = self.model_context()
        self.assertIn('I saw a fictional forum discussing AI companions.', context)
        self.assertIn('Fictional AI Forum', context)
        self.assertNotIn('PRIVATE RAW WINDOW CONTENT', context)

    def test_unconfirmed_cancelled_and_stale_display_callbacks_cannot_invent_text(self):
        stamp = now()
        begin_glance(self.store, self.cfg, stamp)
        hub, pop_id = self.shell(stamp, 'Not displayed')
        # Invalid acknowledgement and cancelled requests are never remembered as shown.
        self.assertFalse(hub.record_screen_pop_event({
            'id': pop_id, 'event': 'shown', 'style': 'popup', 'total': 1}))
        hub.screen_pop_requests[pop_id]['cancelled'] = True
        self.assertFalse(hub.record_screen_pop_event({
            'id': pop_id, 'event': 'shown', 'style': 'danmaku', 'total': 1}))
        self.assertNotIn('Not displayed', recent_experience_context(self.store, self.cfg))
        begin_glance(self.store, self.cfg, '2099-01-01T00:00:00+08:00')
        hub.screen_pop_requests[pop_id]['cancelled'] = False
        hub.record_screen_pop_event({
            'id': pop_id, 'event': 'shown', 'style': 'danmaku', 'total': 1})
        self.assertNotIn('Not displayed', recent_experience_context(self.store, self.cfg))

    def test_last_glance_has_no_clock_expiry_and_does_not_cross_roles(self):
        stamp = '2020-01-01T05:35:44+08:00'
        begin_glance(self.store, self.cfg, stamp)
        record_glance(self.store, self.cfg, self.glance_row(stamp))
        record_screen_message(self.store, self.cfg, call_id='old-call', text='Old exact joke',
                              style='danmaku', stamp=stamp, run_id='glance:' + stamp)
        self.assertIn('Old exact joke', recent_experience_context(self.store, self.cfg))
        self.assertIsNone(recent_experience_context(self.store, SimpleNamespace(id='other-role')))
        self.assertLess(len(recent_experience_context(self.store, self.cfg).encode('utf-8')), 18000)


if __name__ == '__main__':
    unittest.main()
