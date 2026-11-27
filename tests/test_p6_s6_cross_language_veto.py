"""Ticket 461: S6 gate — cross-language veto path, unrelated positive, explicit restore."""
import json
import tempfile
import unittest
from pathlib import Path

from veil.brain import relationship_base
from veil.brain.learned_context import LearnedContext, _matches_governed_claim
from veil.brain.relationship_semantic import _verdict


class CrossLanguageVetoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = LearnedContext(self.root, 'fake', {'file': 'learned-context.json'})
        self.profile.root.mkdir()
        self.chat = self.profile.root / 'chat.jsonl'

    def write_chat(self, *rows):
        self.chat.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                             encoding='utf-8')

    def add_lan_veto(self, text):
        self.profile.save_relationship([{'id': 'veto-1', 'text': text, 'status': 'confirmed'}])
        self.profile.save_relationship([])  # deleting a current Lan note records the veto

    def pending(self, text):
        candidate = next(row for row in self.profile.pending_relationship_semantics()
                         if row['payload']['candidate']['text'] == text)
        return candidate

    def finish(self, task, verdict):
        self.assertTrue(self.profile.claim_relationship_semantic(task['claim_id']))
        self.assertTrue(self.profile.finish_relationship_semantic(task['claim_id'], verdict=verdict))

    def item(self, text=None):
        return next(row for row in self.profile.relationship_items() if row['text'] == (text or self.claim))

    def test_provider_fenced_json_is_accepted_but_other_wrapping_is_still_rejected(self):
        payload = json.dumps({'verdict': 'supported', 'supporting_evidence_ids': ['chat:u1'],
                              'counterevidence': [{'id': 'counter:x', 'verdict': 'unrelated'}]},
                             ensure_ascii=False)
        fence = chr(96) * 3
        self.assertEqual('supported', _verdict(fence + '\n' + payload + '\n' + fence,
                                              {'chat:u1'}, {'counter:x'}))
        with self.assertRaises(ValueError):
            _verdict('prefix ' + payload, {'chat:u1'}, {'counter:x'})

    def test_translated_candidate_with_no_shared_text_anchor_stays_unconfirmed_and_must_be_judged(self):
        veto = '以后见面不要用那个称呼叫我。'
        evidence = '我希望每次见面时，你先叫我小船长。'
        claim = 'Greet me as Little Captain whenever we meet.'
        self.add_lan_veto(veto)
        self.write_chat({'id': 'u1', 'role': 'user', 'content': evidence})
        # This really has no shared lexical anchor or quoted phrase: only a semantic judge can decide.
        self.assertFalse(_matches_governed_claim(claim, veto))
        self.assertFalse(_matches_governed_claim(evidence, veto))
        _, state, _ = self.profile.submit_candidate('relationship', claim, status='confirmed',
                                                    sources=('chat:u1',))
        self.assertEqual('成功', state)
        task = self.pending(claim)
        self.assertIn(veto, [row['text'] for row in task['payload']['counterevidence']])
        self.assertNotIn(claim, relationship_base.build(self.profile)['text'])
        self.finish(task, 'contradicted')
        self.assertNotIn(claim, relationship_base.build(self.profile)['text'])
        self.assertEqual('contradicted', self.item(claim)['semantic_state'])
        self.assertTrue(self.item(claim)['prompt_vetoed'])

    def test_unrelated_positive_still_promotes_with_the_same_veto_present(self):
        veto = '以后见面不要用那个称呼叫我。'
        evidence = '我们约好了，每次分开超过三天，重逢时先拥抱再开口。'
        claim = 'Whenever we have been apart for more than three days, we hug before either of us speaks.'
        self.add_lan_veto(veto)
        self.write_chat({'id': 'u2', 'role': 'user', 'content': evidence})
        self.assertFalse(_matches_governed_claim(claim, veto))
        self.assertFalse(_matches_governed_claim(evidence, veto))
        _, state, _ = self.profile.submit_candidate('relationship', claim, status='confirmed',
                                                    sources=('chat:u2',))
        self.assertEqual('成功', state)
        task = self.pending(claim)
        self.assertIn(veto, [row['text'] for row in task['payload']['counterevidence']])
        self.assertNotIn(claim, relationship_base.build(self.profile)['text'])
        self.finish(task, 'supported')
        self.assertIn(claim, relationship_base.build(self.profile)['text'])
        self.assertEqual('confirmed', self.item(claim)['status'])

    def test_lan_can_explicitly_restore_an_old_cognition_and_it_is_injected_again(self):
        text = '见面时叫我小船长。'
        self.profile.save_relationship([{'id': 'old-1', 'text': text, 'status': 'confirmed'}])
        self.profile.save_relationship([{'id': 'old-1', 'text': text, 'status': 'outdated'}])
        self.assertNotIn(text, relationship_base.build(self.profile)['text'])
        self.profile.restore_relationship_item('old-1')
        current = [row for row in self.profile.relationship_items()
                   if row['text'] == text and row['status'] != 'outdated']
        self.assertEqual(1, len(current))
        self.assertEqual('lan', current[0]['source'])
        self.assertEqual('confirmed', current[0]['status'])
        self.assertEqual([], self.profile.read()['relationship'].get('vetoed') or [])
        self.assertIn(text, relationship_base.build(self.profile)['text'])


if __name__ == '__main__':
    unittest.main()
