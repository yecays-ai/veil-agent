"""Isolated verifier and real ledger using fake transport."""
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from test_learned_context_chat import CONFIG
from veil.brain.relationship_semantic import COST_CATEGORY, RelationshipSemanticWorker, _verdict
from veil.core.veil_agent import Budget, Chat, Store, load_character, run_chat


class RelationshipSemanticWorkerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        role = root / 'characters' / 'test'
        role.mkdir(parents=True)
        (role / 'character.toml').write_text(CONFIG, encoding='utf-8')
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional persona', encoding='utf-8')
        base = load_character(root, 'test')
        provider = {**base.provider, 'missing': False, 'cny_per_call': Decimal('0.01')}
        self.settings = {'daily_cap_cny': '0.06', 'timeout_seconds': 6}
        self.cfg = replace(base, provider=provider, background_provider=provider,
                           models=Mock(semantic_verification=lambda: dict(self.settings)))
        self.store = Store(root / 'data')
        self.clock = ['2026-11-27T12:00:00+08:00']
        self.budget = Budget(self.store, clock=lambda: self.clock[0])
        self.model = Mock(side_effect=self.model_reply)
        self.chat = Chat(self.cfg, self.store, self.budget, Mock(), background_model=self.model)
        self.worker = RelationshipSemanticWorker(self.cfg, self.budget, self.model,
                                                 self.cfg.models.semantic_verification,
                                                 profile=self.chat.profile)

    def append_chat(self, mid, text):
        self.store.append('test/chat.jsonl', {'role': 'user', 'message_id': mid, 'content': text})

    def queue(self):
        self.append_chat('origin', 'I put a yellow candle beside the window each evening.')
        self.chat.profile.submit_candidate('relationship', 'Lan lights a candle near the window at night.',
                                           status='confirmed', sources=('chat:origin',))
        self.assertTrue(self.chat.profile.pending_relationship_semantics())

    def ledger_types(self):
        return [row['type'] for row in self.store.read('costs.jsonl')]

    @staticmethod
    def model_reply(messages, **kwargs):
        payload = json.loads(messages[1]['content'])
        return (json.dumps({
            'verdict': 'supported',
            'supporting_evidence_ids': [payload['evidence'][0]['id']],
            'counterevidence': [{'id': c['id'], 'verdict': 'unrelated'} for c in payload['counterevidence']],
        }), 100, 30, 0, 'stop')

    def test_daily_cny_budget_resumes_same_candidate_and_uses_next_timeout(self):
        old, _ = self.budget.reserve(self.cfg, category=COST_CATEGORY)
        self.budget.mark_dispatched(old)
        self.budget.settle(old, self.cfg, 20, 10, 0, category=COST_CATEGORY)
        self.settings['daily_cap_cny'] = '0.01'
        self.queue()
        claim_id = self.chat.profile.pending_relationship_semantics()[0]['claim_id']
        self.worker.tick()
        self.model.assert_not_called()
        self.assertEqual(self.chat.profile.pending_relationship_semantics()[0]['claim_id'], claim_id)
        self.settings['timeout_seconds'] = 3
        self.clock[0] = '2026-11-28T09:00:00+08:00'
        self.assertTrue(self.worker.tick())
        self.assertEqual(self.model.call_args.kwargs['request_timeout'], 3)
        self.assertEqual(self.budget.category_totals(self.cfg.id, category=COST_CATEGORY), (Decimal('0.01'), 0))
        self.assertEqual(self.chat.profile.relationship_semantic_summary()['promoted'], 1)
        self.assertFalse(self.worker.tick())
        self.model.assert_called_once()

    def test_timeout_keeps_upper_bound_and_never_retries_after_restart(self):
        self.queue()
        self.model.side_effect = TimeoutError()
        self.worker.tick()
        self.assertEqual(self.ledger_types(), ['reserve', 'dispatch', 'settle'])
        self.assertEqual(self.budget.category_totals(self.cfg.id, category=COST_CATEGORY), (Decimal('0.01'), 0))
        self.assertFalse(self.chat.profile.pending_relationship_semantics())
        self.worker.budget = Budget(self.store, clock=lambda: self.clock[0])
        self.worker.profile = type(self.chat.profile)(self.store.root, self.cfg.id, self.cfg.profile)
        self.worker.tick()
        self.model.assert_called_once()

    def test_zero_disables_and_oversize_full_correction_is_never_sent(self):
        self.queue()
        self.settings['daily_cap_cny'] = '0'
        self.assertFalse(self.worker.tick())
        self.assertTrue(self.chat.profile.pending_relationship_semantics())
        self.settings['daily_cap_cny'] = '0.06'
        correction = 'Actually, I stopped doing this altogether. ' + 'z' * 18_000
        self.append_chat('last', correction)
        [task] = self.chat.profile.pending_relationship_semantics()
        self.assertEqual([len(c['text']) for c in task['payload']['counterevidence']], [len(correction)])
        self.worker.tick()
        self.assertEqual(self.ledger_types(), [])
        self.model.assert_not_called()
        self.assertFalse(self.chat.profile.pending_relationship_semantics())

    def test_secret_in_any_semantic_material_blocks_dispatch_and_reservation(self):
        secret = 'password=fictional-test-secret'
        for location in ('candidate', 'evidence', 'question', 'correction', 'localized_correction'):
            with self.subTest(location=location):
                profile = self.chat.profile
                profile.root.mkdir(parents=True, exist_ok=True)
                # A valid yes/no question keeps the fake secret in its preceding context.
                question = 'Do you prefer lighting the candle with ' + secret + '?'
                rows = [{'role': 'assistant', 'message_id': 'question', 'content': question}] if location == 'question' else []
                raw = ('Yes.' if location == 'question' else
                       'I put a yellow candle beside the window each evening. '
                       + (secret if location == 'evidence' else ''))
                rows.append({'role': 'user', 'message_id': 'origin', 'content': raw})
                if location == 'correction':
                    rows.append({'role': 'user', 'message_id': 'later', 'content': 'Actually, ' + secret})
                if location == 'localized_correction':
                    rows.append({'role': 'user', 'message_id': 'later', 'content': '更正：密码：fictional-test-only'})
                (profile.root / 'chat.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
                claim = 'Lan lights a candle near the window at night.'
                profile.submit_candidate('relationship', claim + (' ' + secret if location == 'candidate' else ''),
                                         status='confirmed', sources=('chat:origin',))
                with patch.object(self.budget, 'reserve', wraps=self.budget.reserve) as reserve:
                    self.worker.tick()
                    reserve.assert_not_called()
                self.model.assert_not_called()
                self.assertEqual(self.ledger_types(), [])
                self.assertEqual(profile.relationship_semantic_summary()['reasons'], {'sensitive_payload': 1})
                self.assertFalse(profile.pending_relationship_semantics())
                profile.save_relationship([])
                profile.path.unlink()

    def test_unrecognized_or_incomplete_model_response_cannot_promote(self):
        approval = {'verdict': 'supported', 'supporting_evidence_ids': ['e'],
                    'counterevidence': [{'id': 'tail', 'verdict': 'unrelated'}]}
        bad_outputs = [
            json.dumps(approval),
            'prefix ' + json.dumps(approval),
            '{"verdict":"insufficient", "verdict":"supported", "supporting_evidence_ids":["e"], "counterevidence":[]}',
            json.dumps({**approval, 'counterevidence': [
                {'id': 'first', 'verdict': 'unrelated'}, {'id': 'tail', 'verdict': 'uncertain'}]}),
            json.dumps({**approval, 'counterevidence': [
                {'id': 'first', 'verdict': 'unrelated'}, {'id': 'first', 'verdict': 'unrelated'}]}),
        ]
        for bad in bad_outputs:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _verdict(bad, {'e'}, {'first', 'tail'})

    def test_correction_write_during_provider_does_not_admit_stale_result(self):
        self.queue()
        background_entered = threading.Event()
        reply_allowed = threading.Event()

        def block_then_reply(*args, **kwargs):
            background_entered.set()
            if not reply_allowed.wait(5):
                raise TimeoutError()
            return self.model_reply(*args, **kwargs)

        self.model.side_effect = block_then_reply
        thread = threading.Thread(target=self.worker.tick)
        thread.start()
        try:
            self.assertTrue(background_entered.wait(4))
            writer = threading.Thread(target=lambda: self.chat.profile.save_section('facts', 'Written in parallel.'))
            writer.start()
            writer.join(timeout=2)
            self.assertFalse(writer.is_alive(), 'provider call held the LearnedContext write lock')
            self.append_chat('correction', 'Actually, I stopped doing this altogether.')
        finally:
            reply_allowed.set()
            thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.chat.profile.relationship_semantic_summary()['promoted'], 0)
        self.assertTrue(self.chat.profile.pending_relationship_semantics())

    def test_run_chat_starts_background_even_without_notes_and_stops_on_exit(self):
        self.assertIsNone(self.chat.short_term_notes)
        scheduler = self.chat.relationship_semantic_scheduler
        self.assertIsNotNone(scheduler)
        self.queue()
        finished = threading.Event()
        thread_ids = []
        tick = scheduler.callback

        def worker(stopped):
            thread_ids.append(threading.get_ident())
            if tick(stopped):
                finished.set()

        def console(_):
            self.assertTrue(finished.wait(3))
            return '/quit'

        with patch.object(scheduler, 'interval_seconds', 0.01), patch.object(scheduler, 'callback', worker):
            run_chat(self.chat, input_fn=console, output=lambda _: None)
        self.assertTrue(thread_ids)
        self.assertNotIn(threading.get_ident(), thread_ids)
        self.assertFalse(scheduler._thread.is_alive())
        self.model.assert_called_once()
        self.assertEqual(self.chat.profile.relationship_semantic_summary()['promoted'], 1)
