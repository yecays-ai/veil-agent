import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from veil.core.veil_agent import Chat


class P7S2ChatReviewTests(unittest.TestCase):
    def test_autonomy_entries_advance_without_user_history(self):
        chat = object.__new__(Chat)
        self.assertEqual([chat._next_p7_turn_index('session') for _ in range(6)],
                         [1, 2, 3, 4, 5, 6])
        self.assertEqual(chat._next_p7_turn_index('new-session'), 1)

    def test_provider_filter_diagnostics_do_not_break_render_budget(self):
        chat = object.__new__(Chat)
        provider = Mock(return_value=[{
            'id': 'large', 'status': 'confirmed', 'trigger_condition': 'synthetic trigger',
            'guidance': 'Synthetic guidance ' * 500,
        }])
        provider.settings.return_value = {'max_items': 4, 'max_tokens': 120}
        provider.confirm.return_value = ['large']
        provider.diagnostics.return_value = {
            'status': 'matched', 'selected': 1, 'dropped': [('other', 'threshold')],
        }
        chat.experience_hint_provider = provider
        debug = {}
        self.assertIsNone(chat.p7_experience_hints_context('synthetic', debug=debug))
        self.assertIsInstance(debug['dropped'], int)
        self.assertEqual(debug['dropped'], 1)
        self.assertEqual(debug['selected'], 0)
        self.assertEqual(debug['used_tokens'], 0)

    def test_distinct_tool_failures_have_distinct_opaque_cooldown_keys(self):
        chat = object.__new__(Chat)
        chat._resolve_tool_name = Mock(return_value='example')
        chat._trace = Mock()
        call = SimpleNamespace(function=SimpleNamespace(name='example'))
        summaries = []
        for detail in ('fictional failure E42', 'fictional failure E99'):
            execution = SimpleNamespace(summary='test path', state='失败',
                                        failure_detail=detail, result=detail,
                                        checkpoint_verified=False)
            chat._record_tool_result(call, execution, '失败', detail, summaries, Mock())
        self.assertNotEqual(summaries[0]['failure_digest'], summaries[1]['failure_digest'])
        self.assertNotIn('failure', summaries[0]['failure_digest'])


if __name__ == '__main__':
    unittest.main()
