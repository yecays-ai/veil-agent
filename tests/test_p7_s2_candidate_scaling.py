import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from veil.brain.cognitive_consolidation import CognitiveConsolidator
from veil.brain.experience_provider import ExperienceHintProvider, SituationEnvelope
from veil.core.veil_agent import Store
from tests.test_p7_s2_experience_matching import FakeEmbedder, FakeMemory, candidate


class P7S2CandidateScalingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.cfg = SimpleNamespace(id='peter', memory={'model': 'fake-embed-v1'})
        self.cons = CognitiveConsolidator(
            self.cfg, self.store, SimpleNamespace(), SimpleNamespace())

    def seed(self, rows):
        state = self.cons._load_state()
        state['experience_candidates'] = rows
        self.cons._save_state(state)

    def test_veto_scan_only_receives_vetoed_rows(self):
        rows = [candidate(str(i), f'topic {i}', guidance=f'guidance {i}') for i in range(206)]
        rows.append(candidate('blocked', 'blocked topic', guidance='Do not use the blocked procedure.'))
        rows.append(candidate('veto', 'blocked topic', status='vetoed',
                              guidance='Do not use the blocked procedure.'))
        self.seed(rows)
        original = self.cons._is_vetoed_experience
        scanned_sizes = []
        def checked(guidance, exp_list, profile_data, vetoed_ledger=None):
            scanned_sizes.append(len(exp_list))
            return original(guidance, exp_list, profile_data, vetoed_ledger)
        with patch.object(self.cons, '_is_vetoed_experience', side_effect=checked):
            result = self.cons.query_experience_candidates(semantic_pool=True)
        self.assertEqual(len(scanned_sizes), 207)
        self.assertEqual(sum(scanned_sizes), 207, 'scan one veto row per candidate, not the entire pool')
        self.assertEqual(len(result), 206)
        self.assertNotIn('blocked', {row['id'] for row in result})

    def test_no_veto_pool_has_zero_repeated_row_scan(self):
        self.seed([candidate(str(i), f'topic {i}') for i in range(206)])
        original = self.cons._is_vetoed_experience
        scanned_sizes = []
        def checked(guidance, exp_list, profile_data, vetoed_ledger=None):
            scanned_sizes.append(len(exp_list))
            return original(guidance, exp_list, profile_data, vetoed_ledger)
        with patch.object(self.cons, '_is_vetoed_experience', side_effect=checked):
            result = self.cons.query_experience_candidates(semantic_pool=True)
        self.assertEqual(len(result), 206)
        self.assertEqual(sum(scanned_sizes), 0)

    def test_real_semantic_pool_keeps_old_target_after_205_decoys(self):
        rows = [candidate(f'decoy-{i}', f'unrelated topic {i}') for i in range(205)]
        rows.append(candidate('old-target', 'Record a checkpoint before promising follow-up',
                              updated_at='2020-01-01T00:00:00+00:00'))
        self.seed(rows)
        embedder = FakeEmbedder(lambda text: (1, 0) if text.startswith('Record') else (0, 1))
        provider = ExperienceHintProvider(
            self.cfg, self.store, consolidator=self.cons, memory=FakeMemory(embedder))
        provider.validator.validate = lambda _row, **_kwargs: 'valid'
        self.assertTrue(provider.update_settings({'enabled': True}, expected_revision=1)['ok'])
        self.assertEqual(len(self.cons.query_experience_candidates(semantic_pool=True)), 206)
        result = provider(SituationEnvelope(text='别再只口头答应', query_vector=(1, 0)))
        self.assertEqual([row['id'] for row in result], ['old-target'])

    def test_provider_reads_large_pool_in_bounded_pages_without_newest_first_cutoff(self):
        rows = [candidate(f'decoy-{i}', f'unrelated topic {i}') for i in range(700)]
        rows[620] = candidate('older-target', 'Record evidence before changing the repair')
        self.seed(rows)
        embedder = FakeEmbedder(lambda text: (1, 0) if text.startswith('Record') else (0, 1))
        provider = ExperienceHintProvider(
            self.cfg, self.store, consolidator=self.cons, memory=FakeMemory(embedder))
        provider.validator.validate = lambda _row, **_kwargs: 'valid'
        self.assertTrue(provider.update_settings({'enabled': True}, expected_revision=1)['ok'])
        with patch.object(self.cons, 'query_experience_candidates',
                          wraps=self.cons.query_experience_candidates) as query:
            result = provider(SituationEnvelope(text='先留证据再动修复', query_vector=(1, 0),
                                                context_signal={'is_work_task': True}))
        self.assertEqual([row['id'] for row in result], ['older-target'])
        page_calls = [call.kwargs for call in query.call_args_list if call.kwargs.get('semantic_offset') is not None]
        self.assertGreaterEqual(len(page_calls), 3)
        self.assertTrue(all(call['limit'] == 256 for call in page_calls))


if __name__ == '__main__':
    unittest.main()
