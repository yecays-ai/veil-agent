"""Relationship restore regressions with temporary data and a fake chat model."""
from test_relationship_422 import ProfileFixture
from veil.core.agent_types import UserError
from veil.server.web_agent import Hub


class RelationshipRestoreTests(ProfileFixture):
    def test_only_outdated_items_can_be_restored(self):
        chat, _ = self.build()
        profile = chat.profile
        profile.save_relationship([
            {'id': 'confirmed', 'status': 'confirmed', 'text': 'Use the blue notebook.'},
            {'id': 'uncertain', 'status': 'uncertain', 'text': 'Consider the green folder.'},
        ])
        before = (profile.path.read_bytes(), profile.versions.read_bytes())
        for item_id in ('confirmed', 'uncertain'):
            with self.subTest(item_id=item_id), self.assertRaises(UserError):
                profile.restore_relationship_item(item_id)
        self.assertEqual(before, (profile.path.read_bytes(), profile.versions.read_bytes()))

    def test_restore_replaces_latest_successor_in_the_actual_request_and_is_idempotent(self):
        chat, _ = self.build()
        profile = chat.profile
        original = 'Send one reminder before lunch, unless the recipient already replied.'
        intermediate = 'Send a reminder after lunch instead.'
        latest = 'Send two reminders before dinner.'
        unrelated = 'Keep a paper map in the green folder.'
        for name, args in (
            ('profile_append', {'text': original}),
            ('profile_replace', {'old_text': original, 'new_text': intermediate}),
            ('profile_replace', {'old_text': intermediate, 'new_text': latest}),
            ('profile_append', {'text': unrelated}),
        ):
            self.assertEqual('成功', profile.invoke(name, {'section': 'relationship', **args})[1])
        old, middle, last, other = profile.read()['relationship']['items']
        profile.set_relationship_lock(last['id'], True)
        latest_before = profile.read()['relationship']['items'][2]

        after = profile.restore_relationship_item(old['id'])['relationship']
        restored = after['items'][-1]
        self.assertEqual(old, after['items'][0])
        self.assertEqual(middle, after['items'][1])
        self.assertEqual({**latest_before, 'status': 'outdated', 'superseded_by': restored['id'],
                          'updated_at': after['updated_at']}, after['items'][2])
        self.assertEqual(other, after['items'][3])
        self.assertEqual(original, restored['text'])
        self.assertEqual(old['id'], restored['restored_from'])
        self.assertEqual('lan', restored['source'])
        self.assertEqual('confirmed', restored['status'])
        before_retry = (profile.path.read_bytes(), profile.versions.read_bytes())
        profile.restore_relationship_item(old['id'])
        self.assertEqual(before_retry, (profile.path.read_bytes(), profile.versions.read_bytes()))

        request = self.joined(self.send(chat, 'A fictional greeting.'))
        self.assertEqual(1, request.count(original))
        self.assertNotIn(intermediate, request)
        self.assertNotIn(latest, request)
        self.assertIn(unrelated, request)
        hub = Hub.__new__(Hub)
        hub.chat = chat
        injected = hub.live_context([])['turns'][0]['recall']['relationship_base']['injected_items']
        self.assertEqual({restored['id'], other['id']}, set(injected))

        # Restore a different historical member after the first restore: follow both link types.
        after_second = profile.restore_relationship_item(middle['id'])['relationship']['items']
        next_current = after_second[-1]
        self.assertEqual(intermediate, next_current['text'])
        self.assertEqual('outdated', after_second[-2]['status'])
        self.assertEqual(next_current['id'], after_second[-2]['superseded_by'])
        self.assertEqual({next_current['id'], other['id']},
                         {r['id'] for r in after_second if r['status'] != 'outdated'})

    def test_unlinked_current_duplicate_is_not_rewritten_or_cloned(self):
        chat, _ = self.build()
        profile = chat.profile
        profile.save_relationship([
            {'id': 'old', 'status': 'outdated', 'text': 'Use the blue notebook.'},
            {'id': 'unlinked', 'status': 'confirmed', 'text': 'Use the blue notebook.'},
        ])
        before = (profile.path.read_bytes(), profile.versions.read_bytes())
        with self.assertRaises(UserError):
            profile.restore_relationship_item('old')
        self.assertEqual(before, (profile.path.read_bytes(), profile.versions.read_bytes()))
