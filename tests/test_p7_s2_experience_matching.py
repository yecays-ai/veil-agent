import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from veil.brain.cognitive_consolidation import CognitiveConsolidator
from veil.brain.experience_provider import ExperienceHintProvider, SituationEnvelope
from veil.core.veil_agent import Store


class FakeEmbedder:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return list(self.vectors(text))


class FakeBudget:
    def __init__(self):
        self.estimates = []
        self.settlements = []

    def estimate(self, role, model, tokens, rate):
        self.estimates.append((role, model, tokens, str(rate)))
        return ('embed-reservation', 0)

    def settle_estimate(self, reservation, **kwargs):
        self.settlements.append((reservation, kwargs))
        return True


class FakeMemory:
    def __init__(self, embedder):
        self._embedder = embedder

    def embedder(self):
        return self._embedder


class FakeConsolidator:
    def __init__(self, cfg, rows):
        self.cfg = cfg
        self.rows = rows
        self.queries = 0
        self.cache_writes = 0

    def query_experience_candidates(self, **_kwargs):
        self.queries += 1
        return [dict(row) for row in self.rows]

    def confirm_experience_candidates(self, ids):
        current = {row['id'] for row in self.rows if row.get('status') == 'confirmed'}
        return [item for item in ids if item in current]

    def cache_experience_embedding(self, candidate_id, *, fingerprint, model, vector):
        row = next(item for item in self.rows if item['id'] == candidate_id)
        row['trigger_embedding'] = {
            'fingerprint': fingerprint, 'model': model,
            'dimension': len(vector), 'vector': list(vector),
        }
        self.cache_writes += 1
        return True


def candidate(candidate_id, trigger, *, domain='work_method', guidance='Use the proven method.', **extra):
    return {
        'id': candidate_id, 'status': 'confirmed', 'kind': 'tool_lesson',
        'experience_type': 'success', 'domain': domain,
        'trigger_condition': trigger, 'guidance': guidance,
        'updated_at': '2026-11-28T10:00:00+08:00',
        **extra,
    }


class P7S2ExperienceMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.cfg = SimpleNamespace(id='peter', memory={
            'model': 'fake-embed-v1', 'input_cny_per_million': 0.01,
        })

    def tearDown(self):
        self.temp.cleanup()

    def provider(self, rows, vector_fn, **kwargs):
        embedder = FakeEmbedder(vector_fn)
        cons = FakeConsolidator(self.cfg, rows)
        provider = ExperienceHintProvider(
            self.cfg, self.store, consolidator=cons,
            memory=FakeMemory(embedder), **kwargs,
        )
        provider.validator.validate = lambda _row, **_kwargs: 'valid'
        return provider, cons, embedder

    def enable(self, provider, **extra):
        result = provider.update_settings({'enabled': True, **extra}, expected_revision=1)
        self.assertTrue(result['ok'])

    def test_disabled_costs_zero_and_enable_warms_once(self):
        rows = [candidate('cross', 'When a deployment quietly loses its prior state')]
        provider, cons, embedder = self.provider(rows, lambda _text: (1.0, 0.0))
        envelope = SituationEnvelope(text='这次上线后历史突然全没了', query_vector=(1.0, 0.0))
        self.assertEqual(provider(envelope), [])
        self.assertEqual((cons.queries, embedder.calls), (0, []))
        self.enable(provider)
        self.assertEqual(len(embedder.calls), 1)
        self.assertEqual([row['id'] for row in provider(envelope)], ['cross'])
        self.assertEqual([row['id'] for row in provider(envelope)], ['cross'])
        self.assertEqual(len(embedder.calls), 1, 'chat turns must not add embedding calls')

    def test_zero_lexical_cross_language_and_target_after_200_are_scored(self):
        rows = [candidate(f'decoy-{i}', f'unrelated topic {i}') for i in range(205)]
        rows.append(candidate('old-target', 'Never promise follow-up without recording a checkpoint'))
        def vectors(text):
            return (1.0, 0.0) if text.startswith('Never promise') else (0.0, 1.0)
        provider, cons, _embedder = self.provider(rows, vectors)
        self.enable(provider)
        result = provider(SituationEnvelope(
            text='等会儿记得回来核对，别只口头答应',
            query_vector=(1.0, 0.0), context_signal={'has_commitment_signal': True},
        ))
        self.assertEqual([row['id'] for row in result], ['old-target'])
        self.assertGreater(len(cons.rows), 200)

    def test_short_ack_and_unrelated_scene_are_zero_injection(self):
        rows = [candidate('work', 'deployment failure')]
        provider, cons, _embedder = self.provider(rows, lambda _text: (0.0, 1.0))
        self.enable(provider)
        queries = cons.queries
        self.assertEqual(provider(SituationEnvelope(text='嗯', query_vector=(1.0, 0.0))), [])
        self.assertEqual(cons.queries, queries)
        self.assertEqual(provider(SituationEnvelope(text='今天天气不错', query_vector=(1.0, 0.0))), [])
        self.assertEqual(provider.diagnostics()['status'], 'no_match')

    def test_entry_domains_exception_head_and_cooldown(self):
        rows = [
            candidate('work', 'same failure', domain='work_method'),
            candidate('rel', 'when tension rises', domain='relationship_consensus', guidance='Pause before replying.'),
            candidate('idle', 'during quiet exploration', domain='autonomous_practice'),
        ]
        provider, _cons, _embedder = self.provider(rows, lambda text: (
            (1.0, 0.0) if text.startswith('same failure') else (0.8, 0.6)))
        self.enable(provider, session_cooldown_turns=4)
        tool = provider(SituationEnvelope(
            text='same failure E42', entry_mode='tool_step', query_vector=(1.0, 0.0),
            tool_context={'error': True, 'error_code': 'E42'}, session_key='s', turn_index=1,
        ))
        self.assertEqual([row['id'] for row in tool], ['work'])
        self.assertEqual(provider(SituationEnvelope(
            text='same failure E42', entry_mode='tool_step', query_vector=(1.0, 0.0),
            tool_context={'error': True, 'error_code': 'E42'}, session_key='s', turn_index=2,
        )), [])
        self.assertEqual([row['id'] for row in provider(SituationEnvelope(
            text='same failure E99', entry_mode='tool_step', query_vector=(1.0, 0.0),
            tool_context={'error': True, 'error_code': 'E99'}, session_key='s', turn_index=3,
        ))], ['work'])
        idle = provider(SituationEnvelope(
            text='quietly explore', entry_mode='autonomy', query_vector=(0.8, 0.6), wake_context={}))
        self.assertEqual([row['id'] for row in idle], ['idle'])
        head_result = provider(SituationEnvelope(
            text='tension rises', query_vector=(0.8, 0.6),
            context_signal={'has_emotion_signal': True}, head_covered=frozenset({'Pause before replying.'}),
        ))
        self.assertNotIn('rel', [row['id'] for row in head_result])

    def test_exception_and_conflict_priority_are_deterministic(self):
        rows = [
            candidate('old', 'retry upload', guidance='Retry immediately.',
                      origin_evidence_tier='L2_objective_action', conflicts_with=['lan']),
            candidate('lan', 'retry upload', guidance='Do not retry before asking.',
                      origin_evidence_tier='L1_user_feedback', locked=True,
                      exceptions=['the user explicitly asks for an immediate retry']),
        ]
        provider, _cons, _embedder = self.provider(rows, lambda _text: (1.0, 0.0))
        self.enable(provider)
        result = provider(SituationEnvelope(text='upload failed', query_vector=(1.0, 0.0)))
        self.assertEqual([row['id'] for row in result], ['lan'])
        result = provider(SituationEnvelope(
            text='the user explicitly asks for an immediate retry', query_vector=(1.0, 0.0)))
        self.assertEqual([row['id'] for row in result], ['old'])

    def test_confirm_after_enable_warms_candidate_and_stale_save_keeps_vector(self):
        embedder = FakeEmbedder(lambda _text: (1.0, 0.0))
        budget = FakeBudget()
        cons = CognitiveConsolidator(
            self.cfg, self.store, SimpleNamespace(), SimpleNamespace(),
            memory=FakeMemory(embedder),
        )
        state = cons._load_state()
        row = candidate('new', 'Record a checkpoint before promising follow-up')
        row['status'] = 'uncertain'
        state['experience_candidates'] = [row]
        cons._save_state(state)
        stale = cons._load_state()
        trigger_text = ('Record a checkpoint before promising follow-up'
                        ' | work_method | success')
        stale['experience_candidates'][0]['trigger_embedding'] = {
            'fingerprint': hashlib.sha256(trigger_text.encode()).hexdigest(),
            'model': 'old-model', 'dimension': 1, 'vector': [1.0],
        }
        provider = ExperienceHintProvider(
            self.cfg, self.store, consolidator=cons,
            memory=FakeMemory(embedder), budget=budget,
        )
        provider.validator.validate = lambda _row, **_kwargs: 'valid'
        self.enable(provider)
        self.assertEqual(embedder.calls, [])
        self.assertTrue(cons.decide_experience_candidate('new', 'confirm')['ok'])
        self.assertEqual(len(embedder.calls), 1)
        self.assertEqual(len(budget.estimates), 1)
        self.assertEqual(len(budget.settlements), 1)
        cons._save_state(stale)
        saved = cons._load_state()['experience_candidates'][0]
        self.assertEqual(saved['status'], 'confirmed')
        self.assertEqual(saved['trigger_embedding']['model'], 'fake-embed-v1')
        self.assertEqual(saved['trigger_embedding']['dimension'], 2)
        result = provider(SituationEnvelope(text='别再只口头答应', query_vector=(1.0, 0.0)))
        self.assertEqual([item['id'] for item in result], ['new'])
        self.assertEqual(len(embedder.calls), 1, 'the chat turn must only read the cached vector')

    def test_same_second_model_switch_survives_interleaved_stale_saves(self):
        cons_a = CognitiveConsolidator(
            self.cfg, self.store, SimpleNamespace(), SimpleNamespace())
        cons_b = CognitiveConsolidator(
            self.cfg, self.store, SimpleNamespace(), SimpleNamespace())
        stamp = '2026-11-28T10:00:00+08:00'
        cons_a._now = cons_b._now = lambda: stamp
        row = candidate('switch', 'Record a checkpoint before promising follow-up')
        fingerprint = hashlib.sha256(
            ExperienceHintProvider._trigger_text(row).encode()).hexdigest()
        state = cons_a._load_state()
        state['experience_candidates'] = [row]
        cons_a._save_state(state)
        self.assertTrue(cons_a.cache_experience_embedding(
            'switch', fingerprint=fingerprint, model='model-A', vector=[1.0]))
        stale_a = cons_a._load_state()
        self.assertTrue(cons_b.cache_experience_embedding(
            'switch', fingerprint=fingerprint, model='model-B', vector=[1.0, 0.0]))
        saved_b = cons_b._load_state()['experience_candidates'][0]['trigger_embedding']
        self.assertEqual(stale_a['experience_candidates'][0]['trigger_embedding']['cached_at'],
                         saved_b['cached_at'])
        cons_a._save_state(stale_a)
        saved = cons_b._load_state()['experience_candidates'][0]
        self.assertEqual(saved['trigger_embedding']['model'], 'model-B')
        self.assertEqual(saved['trigger_embedding']['vector'], [1.0, 0.0])
        self.assertEqual(saved['trigger_embedding']['generation'], 2)
        self.cfg.memory['model'] = 'model-B'
        embedder = FakeEmbedder(lambda _text: self.fail('must reuse model-B cache'))
        provider = ExperienceHintProvider(
            self.cfg, self.store, consolidator=cons_b, memory=FakeMemory(embedder))
        provider.validator.validate = lambda _row, **_kwargs: 'valid'
        self.enable(provider)
        result = provider(SituationEnvelope(text='别再只口头答应', query_vector=(1.0, 0.0)))
        self.assertEqual([item['id'] for item in result], ['switch'])
        self.assertEqual(embedder.calls, [])

    def test_gray_gate_covers_full_configured_range_before_direct_threshold(self):
        rows = [candidate(name, name) for name in ('score41', 'score42', 'score47', 'score48')]
        cosines = {'score41': 0.7454545, 'score42': 0.7636364,
                   'score47': 0.8545455, 'score48': 0.88}
        def vectors(text):
            cosine = next(value for key, value in cosines.items() if text.startswith(key))
            return (cosine, (1.0 - cosine * cosine) ** 0.5)
        calls = []
        def gate(_envelope, candidates, timeout, daily_cap):
            calls.append(([row['id'] for row in candidates], timeout, daily_cap))
            return ([row['id'] for row in candidates], 0.0)
        provider, _cons, _embedder = self.provider(rows, vectors, semantic_gate=gate)
        self.enable(provider, semantic_gate_enabled=True)
        result = provider(SituationEnvelope(text='other context', query_vector=(1.0, 0.0)))
        self.assertEqual(calls, [(['score41', 'score42', 'score47'], 2.0, 0.02)])
        self.assertEqual({row['id'] for row in result}, {'score41', 'score42', 'score47', 'score48'})

    def test_gray_gate_default_off_and_independent_cap(self):
        rows = [candidate('gray', 'borderline case')]
        calls = []
        def gate(_envelope, candidates, timeout, daily_cap):
            calls.append((len(candidates), timeout, daily_cap))
            return ([candidates[0]['id']], 0.02)
        provider, _cons, _embedder = self.provider(rows, lambda _text: (0.7, 0.714142842), semantic_gate=gate)
        self.enable(provider)
        envelope = SituationEnvelope(text='unrelated words', query_vector=(1.0, 0.0))
        self.assertEqual(provider(envelope), [])
        self.assertEqual(calls, [])
        revision = provider.settings_revision()
        provider.update_settings({'semantic_gate_enabled': True}, expected_revision=revision)
        gated = provider(envelope)
        self.assertEqual([row['id'] for row in gated], ['gray'])
        self.assertEqual(calls, [(1, 2.0, 0.02)])
        self.assertLessEqual(provider._gate_spent, 0.02)
        provider(envelope)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
