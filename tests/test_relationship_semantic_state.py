import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from veil.brain import relationship_base
from veil.brain.learned_context import LearnedContext


class RelationshipSemanticStateTests(unittest.TestCase):
    claim = 'I prefer quiet birthdays.'
    raw = 'For my birthday, I want a calm celebration.'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = self.reopen()
        self.profile.root.mkdir()
        self.chat = self.profile.root / 'chat.jsonl'

    def reopen(self):
        return LearnedContext(self.root, 'fake', {'file': 'learned-context.json'})

    def user(self, text=None, *, extra=()):
        rows = [] if text is None else [{'id': 'u1', 'role': 'user', 'content': text}]
        rows.extend(extra)
        self.chat.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    def submit(self, text=None, sources=('chat:u1',), **kwargs):
        _, state, _ = self.profile.submit_candidate('relationship', text or self.claim, status='confirmed',
                                                    sources=sources, **kwargs)
        self.assertEqual(state, '成功')

    def pending(self):
        return self.profile.pending_relationship_semantics()

    def item(self, text=None):
        return next(row for row in self.profile.relationship_items() if row['text'] == (text or self.claim))

    def finish(self, token, verdict):
        self.assertTrue(self.profile.claim_relationship_semantic(token))
        self.assertTrue(self.profile.finish_relationship_semantic(token, verdict=verdict))

    def test_evidence_is_raw_deduplicated_and_not_retained_in_cognition_history(self):
        self.user(self.raw)
        self.submit(sources=('chat:u1', 'chat:u1'))
        before = self.profile.versions.read_bytes()
        self.assertEqual(self.item()['semantic_state'], 'pending')
        self.assertNotIn(self.claim, relationship_base.build(self.profile)['text'])
        self.assertEqual(before, self.profile.versions.read_bytes())
        task, = self.pending()
        self.assertEqual(task['payload']['evidence'], [{'id': 'chat:u1', 'text': self.raw}])
        self.finish(task['claim_id'], 'supported')
        self.assertEqual(self.item()['status'], 'confirmed')
        self.assertEqual(self.profile.relationship_semantic_summary(),
                         {'pending': 0, 'promoted': 1, 'reasons': {'supported': 1}})
        self.assertIn(self.claim, relationship_base.build(self.profile)['text'])
        self.assertNotIn(self.raw, self.profile.versions.read_text(encoding='utf-8'))
        self.assertNotIn('verification', self.profile.status(with_text=False)['relationship']['items'][0])
        self.profile.reverify_relationship_evidence()
        self.assertEqual(self.item()['status'], 'confirmed')
        self.assertEqual(self.pending(), [])

    def test_budget_does_not_consume_claim_and_restart_cannot_dispatch_twice(self):
        self.user(self.raw)
        self.submit()
        token = self.pending()[0]['claim_id']
        self.assertTrue(self.profile.finish_relationship_semantic(token, verdict='budget', reason='budget_exhausted'))
        history = self.profile.versions.read_bytes()
        self.assertTrue(self.profile.finish_relationship_semantic(token, verdict='budget', reason='budget_exhausted'))
        self.assertEqual(history, self.profile.versions.read_bytes())
        self.assertEqual(self.profile.relationship_semantic_summary()['reasons'], {'budget_exhausted': 1})
        restarted = self.reopen()
        self.assertEqual(restarted.pending_relationship_semantics()[0]['claim_id'], token)
        with ThreadPoolExecutor(2) as workers:
            results = list(workers.map(lambda store: store.claim_relationship_semantic(token),
                                       (self.profile, restarted)))
        self.assertEqual(sorted(results), [False, True])
        self.assertFalse(restarted.finish_relationship_semantic(token, verdict='budget'))
        self.assertFalse(restarted.claim_relationship_semantic(token))
        self.assertEqual(restarted.pending_relationship_semantics(), [])
        self.assertTrue(self.profile.finish_relationship_semantic(token, verdict='failed', reason='timeout'))
        self.assertFalse(self.profile.finish_relationship_semantic(token, verdict='supported'))
        self.assertEqual(self.profile.relationship_semantic_summary()['reasons'], {'timeout': 1})

    def test_paid_finish_commits_when_the_recheck_times_out(self):
        self.user(self.raw)
        self.submit()
        token = self.pending()[0]['claim_id']
        self.assertTrue(self.profile.claim_relationship_semantic(token))
        real_eval = self.profile._evaluate_sources

        def expire_during_recheck(sources, claim_text='', data=None, *, chat_index=None, budget=None):
            if budget is not None:
                budget.deadline = 0
            return real_eval(sources, claim_text, data, chat_index=chat_index, budget=budget)

        with patch.object(self.profile, '_evaluate_sources', side_effect=expire_during_recheck):
            committed = self.profile.finish_relationship_semantic(token, verdict='supported')
        self.assertTrue(committed)
        stored = self.profile.read()['relationship']['items'][0]
        self.assertEqual('confirmed', stored['status'])
        self.assertEqual('evidence', stored['source'])
        self.assertEqual([], self.profile.pending_relationship_semantics())

    def test_evidence_change_between_claim_and_finish_requires_a_new_receipt(self):
        self.user(self.raw)
        self.submit()
        token = self.pending()[0]['claim_id']
        self.assertTrue(self.profile.claim_relationship_semantic(token))
        self.user('I want a peaceful birthday, not a loud party.')
        self.assertFalse(self.profile.finish_relationship_semantic(token, verdict='supported'))
        fresh = self.pending()[0]['claim_id']
        self.assertNotEqual(token, fresh)
        self.finish(fresh, 'supported')
        self.user()
        self.assertEqual(self.item()['semantic_state'], 'stale')
        self.assertTrue(self.item()['prompt_vetoed'])
        self.assertEqual(self.pending(), [])
        self.assertNotIn(self.claim, relationship_base.build(self.profile)['text'])

    def test_fresh_citation_reuses_candidate_but_not_contradicted_receipt(self):
        self.user(self.raw, extra=[{'id': 'u2', 'role': 'user', 'content': 'I like birthdays with no noise.'}])
        self.submit()
        first, = self.pending()
        self.finish(first['claim_id'], 'contradicted')
        original_id = self.item()['id']
        self.submit(sources=('chat:u2',))
        second, = self.pending()
        self.assertEqual(self.item()['id'], original_id)
        self.assertNotEqual(first['claim_id'], second['claim_id'])
        self.assertEqual([row['id'] for row in second['payload']['evidence']], ['chat:u2'])
        self.finish(second['claim_id'], 'supported')
        self.assertEqual(self.item()['status'], 'confirmed')
        self.assertEqual(len(self.profile.relationship_items()), 1)

    def test_claim_and_finish_roll_back_body_and_version_on_write_failure(self):
        self.user(self.raw)
        self.submit()
        token = self.pending()[0]['claim_id']
        history = self.profile.versions.read_bytes()
        from veil.core.agent_types import UserError
        with patch.object(self.profile, '_record', side_effect=OSError('fake disk failure')):
            with self.assertRaises(UserError):
                self.profile.claim_relationship_semantic(token)
        self.assertEqual(self.profile.versions.read_bytes(), history)
        self.assertEqual(self.item()['semantic_state'], 'pending')
        self.assertTrue(self.profile.claim_relationship_semantic(token))
        history = self.profile.versions.read_bytes()
        with patch.object(self.profile, '_record', side_effect=OSError('fake disk failure')):
            with self.assertRaises(UserError):
                self.profile.finish_relationship_semantic(token, verdict='supported')
        self.assertEqual(self.profile.versions.read_bytes(), history)
        self.assertEqual(self.item()['semantic_state'], 'dispatched')
        self.assertEqual(self.pending(), [])
        self.assertTrue(self.profile.finish_relationship_semantic(token, verdict='supported'))

    def test_literal_evidence_not_a_semantic_veto_bypass(self):
        self.profile.save_relationship([{'id': 'v', 'text': 'No rowdy celebrations!', 'status': 'confirmed'}])
        self.profile.save_relationship([])
        self.user(self.claim)
        _, state, _ = self.profile.submit_candidate('facts', self.claim)
        self.assertEqual(state, '成功')
        self.submit()
        row, = self.pending()
        self.assertEqual(row['payload']['counterevidence'][0]['kind'], 'veto')
        self.assertNotIn(self.claim, self.profile.build_prompt()['stable'] or '')
        self.finish(row['claim_id'], 'contradicted')
        self.assertEqual(self.item()['semantic_state'], 'contradicted')
        self.assertNotIn(self.claim, relationship_base.build(self.profile)['text'])
        self.assertNotIn(self.claim, self.profile.build_prompt()['stable'] or '')
        self.assertEqual([v['text'] for v in self.profile.read()['relationship']['vetoed']], ['No rowdy celebrations!'])
        self.user(self.raw)
        fresh, = self.pending()
        self.assertNotEqual(row['claim_id'], fresh['claim_id'])
        self.assertEqual(fresh['payload']['counterevidence'], row['payload']['counterevidence'])
        self.assertTrue(self.item()['prompt_vetoed'])

    def test_veto_anchor_in_original_evidence_cannot_be_laundered_by_translation(self):
        veto = '每次相见时以「小船长」称呼我。'
        self.profile.save_relationship([{'id': 'v', 'text': veto, 'status': 'confirmed'}])
        self.profile.save_relationship([])
        claim = 'Greet me as Little Captain whenever we meet.'
        self.user('我希望今后每次见面时，你先叫我小船长。', extra=[
            {'id': 'u2', 'role': 'user', 'content': '生日聚会我想安静一点。'},
        ])
        self.submit(claim)
        self.assertEqual(self.item(claim)['effective_status'], 'uncertain')
        self.assertNotIn(claim, relationship_base.build(self.profile)['text'])
        self.submit(sources=('chat:u2',))
        task, = self.pending()
        self.assertEqual(task['payload']['candidate']['text'], self.claim)
        self.finish(task['claim_id'], 'supported')
        self.assertIn(self.claim, relationship_base.build(self.profile)['text'])
        self.assertNotIn(claim, relationship_base.build(self.profile)['text'])

    def test_unverified_source_never_dispatches_and_governance_fails_closed(self):
        self.profile.save_relationship([{'id': 'l', 'text': 'Keep surprise events private.',
                                          'status': 'confirmed', 'locked': True}])
        self.user(extra=[{'id': 'a1', 'role': 'assistant', 'content': self.claim}])
        self.submit(sources=('chat:a1', 'long_task:t1'))
        self.assertEqual(self.pending(), [])
        self.assertTrue(self.item()['prompt_vetoed'])
        self.assertNotIn(self.claim, relationship_base.build(self.profile)['text'])

    def test_later_correction_invalidates_snapshot_and_live_verified_output(self):
        self.user(self.raw)
        self.submit()
        token = self.pending()[0]['claim_id']
        self.finish(token, 'supported')
        self.user(self.raw, extra=[{'id': 'u2', 'role': 'user', 'content': 'Actually, throw me a rave.'}])
        current, = self.pending()
        self.assertNotEqual(token, current['claim_id'])
        self.assertEqual(current['payload']['counterevidence'][0]['kind'], 'correction')
        self.assertTrue(self.item()['prompt_vetoed'])
        self.assertFalse(self.profile.claim_relationship_semantic(token))

    def test_chinese_update_overrides_cross_language_permission(self):
        note = 'You have blanket permission to share all photography previews with our group.'
        self.user('以后我所有摄影小样你都可以传给我们的小组。')
        self.submit(note)
        self.finish(self.pending()[0]['claim_id'], 'supported')
        correction = '更新一下：小样仅我自己看；小组以后只看我挑好的成品。'
        self.user('以后我所有摄影小样你都可以传给我们的小组。',
                  extra=[{'id': 'u2', 'role': 'user', 'content': correction}])
        self.assertNotIn(note, relationship_base.build(self.profile)['text'])
        task, = self.pending()
        self.assertIn(correction, [row['text'] for row in task['payload']['counterevidence']])
        self.finish(task['claim_id'], 'contradicted')
        self.assertNotIn(note, relationship_base.build(self.profile)['text'])

    def test_recalled_original_invalidates_cached_verdict_even_with_retained_content(self):
        self.user(self.raw)
        self.submit()
        token = self.pending()[0]['claim_id']
        self.finish(token, 'supported')
        self.assertIn(self.claim, relationship_base.build(self.profile)['text'])
        self.user(extra=[{'id': 'u1', 'role': 'user', 'content': self.raw, 'recalled': True}])
        self.assertNotIn('u1', self.profile._chat_user_index())
        self.assertEqual(self.item()['effective_status'], 'uncertain')
        self.assertNotIn(self.claim, relationship_base.build(self.profile)['text'])
        self.assertEqual(self.pending(), [])
        self.assertFalse(self.profile.finish_relationship_semantic(token, verdict='supported'))

    def test_lock_or_rule_edit_between_snapshot_and_claim_prevents_dispatch(self):
        self.user(self.raw)
        self.submit()
        token = self.pending()[0]['claim_id']
        with patch.object(LearnedContext, '_semantic_rule', return_value='updated-prompt-digest'):
            self.assertFalse(self.profile.claim_relationship_semantic(token))
            newer, = self.pending()
            self.assertNotEqual(newer['claim_id'], token)
        self.profile.set_relationship_lock(self.item()['id'], True)
        self.assertFalse(self.profile.claim_relationship_semantic(token))
        self.assertEqual(self.pending(), [])

    def test_pending_replacement_does_not_remove_old_note_and_rechecks_lock(self):
        self.user(self.raw)
        old = 'Former birthday agreement.'
        _, state, _ = self.profile.submit_candidate('relationship', old)
        self.assertEqual(state, '成功')
        self.submit(replace_old_text=old)
        token = self.pending()[0]['claim_id']
        self.assertIn(old, relationship_base.build(self.profile)['text'])
        self.assertTrue(self.profile.claim_relationship_semantic(token))
        self.profile.set_relationship_lock(self.item(old)['id'], True)
        self.assertFalse(self.profile.finish_relationship_semantic(token, verdict='supported'))
        self.assertEqual(self.item(old)['status'], 'uncertain')
        self.assertTrue(self.item(old)['locked'])
        self.assertTrue(self.item()['prompt_vetoed'])
