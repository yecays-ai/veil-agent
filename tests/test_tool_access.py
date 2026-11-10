import unittest

import delegate_agent
import long_tasks
from tool_access import TOOL_ACCESS, tools_with


class ToolAccessTests(unittest.TestCase):
    def test_the_three_old_lists_come_from_the_one_table(self):
        self.assertEqual(long_tasks.READ_ONLY_TOOLS, tools_with('r'))
        self.assertEqual(long_tasks.SIDE_EFFECT_TOOLS, tools_with('w'))
        self.assertEqual(delegate_agent.READ_ONLY_TOOLS, tools_with('d'))

    def test_no_tool_is_both_read_only_and_a_side_effect(self):
        self.assertEqual(tools_with('r') & tools_with('w'), frozenset())

    def test_delegated_investigations_never_get_side_effect_tools(self):
        self.assertEqual(tools_with('d') & tools_with('w'), frozenset())

    def test_flags_are_known(self):
        self.assertTrue(all(set(flags) <= set('rwd') for flags in TOOL_ACCESS.values()))


if __name__ == '__main__':
    unittest.main()
