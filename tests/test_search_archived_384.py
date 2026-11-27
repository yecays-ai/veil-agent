"""Ticket 384: Peter's own memory search, the candidates offered before a save, and the tidying's library lookup
drop archived memories before keeping three, as automatic recall does since ticket 381."""
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from veil.brain.experience_memory import ExperienceMemory
from veil.brain.memory_agent import Memory
from veil.brain.memory_native import NativeMemoryTools, ToolSet
from test_memory import Backend
from veil.core.veil_agent import Budget, Character, Store


class OmbreLikeBackend(Backend):
    """Like real Ombre: breath_search also returns archived (deleted) memories, in rank order."""
    def call(self, name, args):
        if name == 'breath_search':
            self.calls.append((name, args))
            keys = [k for k, v in self.rows.items() if not v.get('feel')][:args.get('max_results', 3)]
            return '\n'.join(f'[bucket_id:{k}] {self.rows[k]["content"]}' for k in keys) + (
                '\n=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': keys}) + '\n```')
        return super().call(name, args)


class SearchSkipsArchivedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 4000})
        self.backend = OmbreLikeBackend(Path(self.temp.name) / 'data/fake/memory/dynamic')
        self.memory = Memory(self.cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='', consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('not authorized', '被拒绝', n), preview_summary=lambda n, a: n)
        self.native = NativeMemoryTools(self.memory, 4000)
        self.tools = ToolSet(files, self.native)
        # Two archived old versions rank first, then four current memories.
        for index, archived in enumerate([True, True, False, False, False, False]):
            self.backend.call('hold', {'title': f'm{index}', 'content': f'garden memory {index}'})
            if archived:
                self.backend.call('trace', {'bucket_id': f'{index:012x}', 'delete': True})
        self.archived = {f'{0:012x}', f'{1:012x}'}
        self.current = [f'{index:012x}' for index in (2, 3, 4)]

    def test_search_shows_three_current_memories_and_no_archived_text(self):
        text, state, _ = self.tools.invoke('breath_search', {'query': 'garden'})
        self.assertEqual(state, '成功')
        shown = [key for key in self.current + sorted(self.archived) if f'Memory ID: {key}' in text]
        self.assertEqual(shown, self.current)
        self.assertNotIn('garden memory 0', text)
        self.assertNotIn('garden memory 1', text)
        self.assertGreater(dict(self.backend.calls[-1:])['breath_search']['max_results'], 3)

    def test_archived_memory_found_by_search_cannot_be_rewritten(self):
        self.tools.invoke('breath_search', {'query': 'garden'})
        result, state, _ = self.tools.invoke('hold', {'target_id': f'{0:012x}', 'content': 'revived'})
        self.assertEqual(state, '被拒绝')
        self.assertNotEqual(self.backend.rows[f'{0:012x}']['content'], 'revived')

    def test_candidates_before_a_new_save_skip_archived(self):
        found = self.native._related('garden', 'garden memory')
        self.assertEqual([key for key, _ in found], self.current)

    def test_tidying_library_lookup_skips_archived(self):
        tidy = SimpleNamespace(memory=self.memory)
        tidy._alive = lambda key: ExperienceMemory._alive(tidy, key)
        self.assertEqual(ExperienceMemory._search_ids(tidy, 'garden'), self.current)


if __name__ == '__main__':
    unittest.main()
