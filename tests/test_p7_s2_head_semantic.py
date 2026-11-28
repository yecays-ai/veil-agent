import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil.brain.cognitive_consolidation import CognitiveConsolidator
from veil.brain.experience_provider import ExperienceHintProvider, SituationEnvelope
from veil.core.veil_agent import Budget, Chat, Store, load_character


class HeadSemanticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.fake]
model = "fake"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
''', encoding='utf-8')
        self.cfg = load_character(root, 'test')
        self.cfg.memory = {'model': 'fake-embed', 'base_url': 'https://embed.invalid/v1',
                           'input_cny_per_million': '1'}
        self.store = Store(root / 'data')
        self.budget = Budget(self.store)
        self.embed = Mock(side_effect=lambda text: [1., 0.] if 'pause' in text.lower() or '停一下' in text else [0., 1.])
        self.profile = SimpleNamespace(build_prompt=lambda: {'stable': 'Notes:\n- 先停一下再回复。',
                                    'notes': '- 先停一下再回复。', 'ongoing': ''})
        self.memory = SimpleNamespace(embedder=lambda: SimpleNamespace(embed=self.embed))
        self.cons = CognitiveConsolidator(self.cfg, self.store, self.budget, Mock(), profile=self.profile)
        self.row = {'id': 'head', 'kind': 'tool_lesson', 'status': 'confirmed',
                    'trigger_condition': 'When tension rises', 'guidance': 'Pause before replying.',
                    'domain': 'work_method', 'experience_type': 'success',
                    'updated_at': '2026-11-28T10:00:00+08:00'}
        state = self.cons._load_state()
        state['experience_candidates'] = [self.row]
        self.cons._save_state(state)
        self.provider = ExperienceHintProvider(self.cfg, self.store, consolidator=self.cons,
                                               memory=self.memory, profile=self.profile, budget=self.budget)
        self.provider.validator.validate = lambda *_args, **_kwargs: 'valid'

    def test_explicit_enable_warms_then_real_chat_renderer_skips_paraphrase_without_turn_embed(self):
        self.assertEqual(self.provider(SituationEnvelope(text='different', query_vector=(1., 0.))), [])
        self.assertEqual(self.embed.call_count, 0)
        self.assertTrue(self.provider.update_settings({'enabled': True}, expected_revision=1)['ok'])
        warmed = self.embed.call_count
        self.assertGreaterEqual(warmed, 2)
        without_head = self.provider(SituationEnvelope(
            text='different words for conflict', query_vector=(0., 1.)))
        self.assertEqual([item['id'] for item in without_head], ['head'])

        chat = object.__new__(Chat)
        chat.experience_hint_provider = self.provider
        chat.cfg = self.cfg
        chat.store = self.store
        chat.budget = self.budget
        debug = {}
        result = chat.p7_experience_hints_context(SituationEnvelope(
            text='different words for conflict', query_vector=(0., 1.),
            head_covered=frozenset({'先停一下再回复。'})), debug=debug)
        self.assertIsNone(result)
        self.assertEqual(self.embed.call_count, warmed)
        self.assertTrue(any(reason == 'covered_by_head' for _, reason in debug['filter_reasons']))

    def test_cache_survives_recreation_and_invalidates_model_endpoint_and_text(self):
        self.provider.update_settings({'enabled': True}, expected_revision=1)
        before = self.embed.call_count
        second = ExperienceHintProvider(self.cfg, self.store, consolidator=self.cons,
                                        memory=self.memory, profile=self.profile, budget=self.budget)
        self.assertTrue(second._head_semantically_covers('Pause before replying.', ('先停一下再回复。',)))
        self.assertEqual(self.embed.call_count, before)
        self.cfg.memory['base_url'] = 'https://other.invalid/v1'
        self.assertFalse(second._head_semantically_covers('Pause before replying.', ('先停一下再回复。',)))
        self.cfg.memory['base_url'] = 'https://embed.invalid/v1'
        self.cfg.memory['model'] = 'next-model'
        self.assertFalse(second._head_semantically_covers('Pause before replying.', ('先停一下再回复。',)))
        self.cfg.memory['model'] = 'fake-embed'
        self.assertFalse(second._head_semantically_covers('changed guidance', ('先停一下再回复。',)))
        self.assertFalse(second._head_semantically_covers('Pause before replying.', ('edited claim',)))

    def test_real_chat_send_passes_actual_head_claims_without_embedding(self):
        model = Mock(return_value=('Fictional reply', 100, 20, 0))
        chat = Chat(self.cfg, self.store, self.budget, model)
        chat.profile = self.profile
        chat.experience_hint_provider = self.provider
        self.provider.update_settings({'enabled': True, 'session_cooldown_turns': 0}, expected_revision=1)
        warmed = self.embed.call_count
        chat.send('When tension rises')
        sent = model.call_args.args[0]
        self.assertFalse(any('Guidance: Pause before replying.' in str(row.get('content')) for row in sent))
        self.assertEqual(self.embed.call_count, warmed)
        self.profile.build_prompt = lambda: {'stable': '', 'notes': '', 'ongoing': ''}
        chat.send('When tension rises')
        sent = model.call_args.args[0]
        self.assertTrue(any('Guidance: Pause before replying.' in str(row.get('content')) for row in sent))
        self.assertEqual(self.embed.call_count, warmed)

    def test_background_maintenance_follows_natural_head_changes_without_turn_embedding(self):
        claims = {'text': '先停一下再回复。'}
        self.profile.build_prompt = lambda: {
            'stable': 'Notes:\n- ' + claims['text'], 'notes': '- ' + claims['text'], 'ongoing': ''}
        self.provider.update_settings({'enabled': True}, expected_revision=1)
        warmed = self.embed.call_count
        claims['text'] = 'Pause and verify the new checkpoint.'
        self.assertFalse(self.provider._head_semantically_covers(
            'Pause before replying.', (claims['text'],)))
        self.assertEqual(self.embed.call_count, warmed, 'ordinary reads never repair vectors')
        self.assertGreater(self.provider.maintain_vectors(), 0)
        self.assertGreater(self.embed.call_count, warmed)
        after = self.embed.call_count
        self.provider(SituationEnvelope(text='unrelated turn', query_vector=(0., 1.),
                                        head_covered=frozenset({claims['text']})))
        self.assertEqual(self.embed.call_count, after, 'chat turns remain zero-embedding')


if __name__ == '__main__':
    unittest.main()
