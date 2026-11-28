import hashlib
import math
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from unittest.mock import Mock

from veil.brain.cognitive_consolidation import CognitiveConsolidator, _normalise_experience
from veil.core.agent_types import BudgetExceeded
from veil.core.veil_agent import Store


class P7S2CacheGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.cfg = SimpleNamespace(id='peter', memory={'model': 'fake'})
        self.cons = CognitiveConsolidator(self.cfg, self.store, SimpleNamespace(), SimpleNamespace())

    def test_legacy_candidate_vector_survives_normalized_merge(self):
        row = {'id': 'legacy', 'kind': 'tool_lesson', 'status': 'confirmed',
               'trigger_condition': 'legacy task', 'guidance': 'Use checkpoint.'}
        state = self.cons._load_state()
        state['experience_candidates'] = [row]
        self.cons._save_state(state)
        projected = _normalise_experience(row)
        text = f"{projected['trigger_condition']} | {projected['domain']} | {projected['experience_type']}"
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        self.assertTrue(self.cons.cache_experience_embedding(
            'legacy', fingerprint=fingerprint, model='fake', vector=[1.0, 0.0],
            vector_space='test-space'))
        saved = self.cons._load_state()['experience_candidates'][0]['trigger_embedding']
        self.assertEqual(saved['vector'], [1.0, 0.0])
        self.assertEqual(saved['vector_space'], 'test-space')

    def test_nonfinite_vector_is_not_persisted(self):
        row = {'id': 'bad', 'kind': 'tool_lesson', 'status': 'confirmed',
               'trigger_condition': 'bad vector', 'guidance': 'Retry carefully.'}
        state = self.cons._load_state()
        state['experience_candidates'] = [row]
        self.cons._save_state(state)
        for bad in (math.nan, math.inf, -math.inf):
            self.assertFalse(self.cons.cache_experience_embedding(
                'bad', fingerprint='fingerprint', model='fake', vector=[1.0, bad]))
        self.assertNotIn('trigger_embedding', self.cons._load_state()['experience_candidates'][0])
    def test_gray_review_uses_dedicated_cap_and_stops_after_exhaustion(self):
        @dataclass
        class ReviewConfig:
            id: str
            memory: dict
            provider: dict
            max_output_tokens: int

        cfg = ReviewConfig(id='peter', memory={'model': 'fake'},
                           provider={'max_input_tokens': 4096}, max_output_tokens=256)
        budget = Mock()
        budget.reserve.side_effect = BudgetExceeded('daily cap reached')
        cons = CognitiveConsolidator(cfg, self.store, budget, Mock())
        result = cons.review_experience_matches(
            SimpleNamespace(text='synthetic request', entry_mode='chat'),
            [{'id': 'fake', 'trigger_condition': 'synthetic', 'guidance': 'fake guidance'}],
            daily_cap=0.02)
        self.assertEqual(result, ([], 0.0))
        self.assertEqual(budget.reserve.call_args.kwargs['category'], 'p7_experience_gate')
        self.assertEqual(str(budget.reserve.call_args.kwargs['category_daily']), '0.02')
        cons.model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
