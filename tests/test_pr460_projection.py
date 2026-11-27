"""PR #460: compare actual Chat.send requests with the management projections."""
import json

from test_relationship_422 import ProfileFixture
from veil.core.veil_agent import estimate_request_tokens
from veil.server.web_agent import Hub


class ProjectionTests(ProfileFixture):
    def management(self, chat):
        hub = Hub.__new__(Hub)
        hub.chat = chat
        return hub.peter_page()['learned'], hub.live_context([])['turns'][0]['recall']['relationship_base']

    def test_outdated_and_vetoed_copies_never_enter_the_model_but_stay_in_management(self):
        chat, _ = self.build()
        old = 'Send a morning check-in every weekday.'
        veto = 'Always send an unsolicited late-night ping.'
        unaffected = 'Keep a separate notebook.'
        data = chat.profile.read()
        data['facts']['text'] = f'- {old}\n- {unaffected}'
        data['growth']['text'] = f'1. {veto}'
        data['ongoing']['items'] = [{'id': 'old-task', 'status': 'active', 'text': old,
                                     'created_at': '', 'updated_at': ''}]
        data['relationship']['items'] = [{'id': 'retired', 'status': 'outdated', 'text': old,
                                           'source': 'lan', 'created_at': '', 'updated_at': ''}]
        data['relationship']['vetoed'] = [{'text': veto, 'norm_text': veto.lower()}]
        chat.profile.path.write_text(json.dumps(data), encoding='utf-8')
        before = chat.profile.path.read_bytes()
        for turn, autonomy in enumerate((False, True)):
            chat.send('A fictional greeting.', message_id=f'greeting-{turn}', autonomy=autonomy)
            request = self.joined(self.model.call_args)
            self.assertNotIn(old, request)
            self.assertNotIn(veto, request)
            self.assertIn(unaffected, request)
        management, recall = self.management(chat)
        self.assertIn(old, management['sections']['facts']['text'])
        self.assertIn(veto, management['sections']['growth']['text'])
        self.assertEqual(old, management['ongoing']['items'][0]['text'])
        self.assertEqual(old, management['relationship']['items'][0]['text'])
        self.assertEqual(before, chat.profile.path.read_bytes())
        self.assertEqual(3, len(recall['cross_section_conflicts']))

    def test_projection_respects_explicit_lan_text_and_suppresses_unmarked_copies(self):
        chat, _ = self.build()
        protected = 'Make a paper copy of the airline ticket.'
        removed = 'Send an automated reminder before lunch.'
        data = chat.profile.read()
        data['facts'].update(text=protected, lan_text=[protected])
        data['growth']['text'] = removed
        data['ongoing']['items'] = [
            {'id': 'protected', 'status': 'active', 'text': protected, 'lan_text': True,
             'created_at': '', 'updated_at': ''},
        ]
        data['relationship']['vetoed'] = [{'text': protected}, {'text': removed}]
        chat.profile.path.write_text(json.dumps(data), encoding='utf-8')
        request = self.joined(self.send(chat, 'A fictional greeting.'))
        self.assertEqual(2, request.count(protected))
        self.assertNotIn(removed, request)
        _, recall = self.management(chat)
        self.assertEqual(['growth:1'], recall['cross_section_conflicts'])

    def test_long_locked_rule_is_not_shortened_into_a_different_rule(self):
        chat, _ = self.build()
        chat.cfg.profile['relationship_base'] = {'max_chars': 100, 'max_items': 1}
        opening = 'Do not send more than one reminder while the recipient is away.'
        rule = opening + ' Keep the message brief. Unless the recipient explicitly asks for two reminders.'
        chat.profile.save_relationship([
            {'id': 'unsafe', 'source': 'lan', 'status': 'confirmed', 'locked': True, 'text': rule},
            {'id': 'locked', 'source': 'lan', 'status': 'uncertain', 'locked': True,
             'text': 'Use the blue notebook.'},
            {'id': 'confirmed', 'source': 'lan', 'status': 'confirmed', 'text': 'Use the green folder.'},
        ])
        request = self.joined(self.send(chat, 'A fictional greeting.'))
        self.assertNotIn(opening, request)
        self.assertIn('(not confirmed yet) Use the blue notebook.', request)
        self.assertNotIn('Use the green folder.', request)
        management, recall = self.management(chat)
        self.assertIn(rule, [item['text'] for item in management['relationship']['items']])
        self.assertTrue(recall['degraded'])
        self.assertEqual({'unsafe': 'space', 'confirmed': 'item_cap'},
                         {row['id']: row['reason'] for row in recall['dropped']})
        self.assertEqual(['locked'], recall['injected_items'])

    def test_management_initial_selection_and_provider_budget_match_real_request(self):
        chat, _ = self.build()
        agreements = [
            'Use the trail guide and mark picnic sites with a red triangle.',
            'Paint sea cliffs in watercolor whenever our weekly art group meets.',
            'Carry a small thermos of peppermint tea on the ferry ride.',
            'Always file spare brass keys under the cabinet marked kitchen.',
            'Send the draft ceramics catalogue to Mira before the exhibition.',
            'Check that balcony tomatoes have enough water before sunrise.',
        ]
        rows = [{'id': f'r{i}', 'text': text, 'source': 'lan', 'status': 'confirmed'}
                for i, text in enumerate(agreements)]
        chat.profile.save_relationship(rows)
        self.send(chat, 'First fictional turn.', message_id='budget-1')
        _, first = self.management(chat)
        chat.cfg.provider['max_input_tokens'] = 32768 - first['space_tokens'] - 40
        call = self.send(chat, 'Second turn.', message_id='budget-2')
        request = self.joined(call)
        management, recall = self.management(chat)
        injected = [row['id'] for row in rows if row['text'].strip() in request]
        self.assertLess(len(injected), len(rows))
        self.assertEqual(len(rows), len(management['relationship']['items']))
        self.assertEqual([row['id'] for row in rows], recall['preselected_items'])
        self.assertEqual(injected, recall['selected_items'])
        self.assertEqual(injected, recall['injected_items'])
        self.assertEqual(injected, recall['items'])
        self.assertEqual('budget', recall['reason'])
        self.assertEqual(set(first['items']) - set(injected),
                         {row['id'] for row in recall['dropped'] if row['reason'] == 'budget'})
        self.assertLessEqual(estimate_request_tokens(call.args[0], None, 0.4),
                             chat.cfg.provider['max_input_tokens'])
