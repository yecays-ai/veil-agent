import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from memory_agent import Memory
from memory_native import HOLD, MEMORY_CATALOG, PINNED_LIMIT, SEARCH, NativeMemoryTools, ToolSet
from test_memory import Backend
from veil_agent import Budget, Character, Store


class PinningBackend(Backend):
    """Like Ombre: hold(pinned=True) answers with the pin line, not 新建."""
    def list_tools(self):
        names = ('trace', 'dream', 'letter_write', 'letter_read', 'letter_lock_update',
                 'anchor', 'release', 'plan', 'pulse', 'I')
        return [SimpleNamespace(name=name, inputSchema={'type': 'object', 'properties': {}},
                                description=f'upstream {name}') for name in names]

    def call(self, name, args):
        if name == 'hold' and args.get('pinned'):
            self.calls.append((name, args))
            key = f'pin{len(self.rows):09x}'
            self.rows[key] = dict(args)
            return f'📌钉选→{key} 未分类'
        return super().call(name, args)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 8192})
        self.backend = PinningBackend()
        self.memory = Memory(self.cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='files ',
            consume_capability_event=lambda: None, announce_autonomy=lambda: None,
            invoke=lambda n, a, ap: ('denied', '被拒绝', n), preview_summary=lambda n, a: n)
        self.tools = ToolSet(files, NativeMemoryTools(self.memory, 8192))
        self.native = self.tools.native
        self.vault = self.store.root / 'fake' / 'memory'

    def write(self, key, created=None, folder='dynamic', extra=''):
        path = self.vault / folder / f'{key}.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = f"created: '{created}'\n" if created else ''
        path.write_text(f'---\nid: {key}\ntitle: T {key}\n{stamp}{extra}---\nbody {key}\n', encoding='utf-8')

    def holds(self):
        return [args for name, args in self.backend.calls if name == 'hold']


class HoldImportanceTests(Base):
    def test_importance_is_forwarded_for_a_new_memory(self):
        result, state, _ = self.tools.invoke('hold', {'title': 'A', 'content': 'a', 'importance': 8})
        self.assertEqual(state, '成功', result)
        self.assertEqual(self.holds()[-1]['importance'], 8)
        _, state, _ = self.tools.invoke('hold', {'title': 'B', 'content': 'b'})
        self.assertEqual(state, '成功')
        self.assertNotIn('importance', self.holds()[-1])

    def test_bad_importance_or_pinned_values_never_reach_ombre(self):
        for extra in ({'importance': 0}, {'importance': 11}, {'importance': True}, {'importance': '7'},
                      {'importance': 5.5}, {'pinned': 'yes'}, {'pinned': 1}):
            _, state, _ = self.tools.invoke('hold', {'title': 'x', 'content': 'x', **extra})
            self.assertEqual(state, '被拒绝', extra)
        self.assertEqual(self.holds(), [])

    def test_importance_and_pinned_are_refused_when_updating_an_existing_memory(self):
        self.memory.mark_surfaced(['mem001'])
        for extra in ({'importance': 9}, {'pinned': True}):
            result, state, _ = self.tools.invoke('hold', {'target_id': 'mem001', 'content': 'more', **extra})
            self.assertEqual(state, '被拒绝')
            self.assertIn('trace', result)
        self.assertEqual(self.holds(), [])

    def test_schema_documents_both_fields(self):
        self.assertEqual(HOLD.inputSchema['properties']['importance']['maximum'], 10)
        self.assertEqual(HOLD.inputSchema['properties']['pinned']['type'], 'boolean')
        self.assertIn(str(PINNED_LIMIT), HOLD.inputSchema['properties']['pinned']['description'])


class PinnedTests(Base):
    def test_pinned_needs_a_reason_and_forbids_importance(self):
        _, state, _ = self.tools.invoke('hold', {'title': 'P', 'content': 'p', 'pinned': True})
        self.assertEqual(state, '被拒绝')
        _, state, _ = self.tools.invoke('hold', {'title': 'P', 'content': 'p', 'pinned': True,
                                                 'why_remembered': 'always', 'importance': 7})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(self.holds(), [])

    def test_pinned_save_is_recorded_even_though_ombre_answers_with_a_pin_line(self):
        result, state, _ = self.tools.invoke('hold', {'title': 'P', 'content': 'p', 'pinned': True,
                                                      'why_remembered': 'must surface first'})
        self.assertEqual(state, '成功', result)
        sent = self.holds()[-1]
        self.assertIs(sent['pinned'], True)
        self.assertNotIn('importance', sent)
        self.assertIn('memory_id: pin', result)
        self.assertEqual(len(self.memory.state['entries']), 1)

    def test_pinned_is_capped_below_ombres_own_limit(self):
        for i in range(PINNED_LIMIT):
            self.write(f'p{i}', folder='permanent', extra='pinned: true\ntype: permanent\n')
        result, state, _ = self.tools.invoke('hold', {'title': 'P', 'content': 'p', 'pinned': True,
                                                      'why_remembered': 'again'})
        self.assertEqual(state, '被拒绝')
        self.assertIn(str(PINNED_LIMIT), result)
        self.assertEqual(self.holds(), [])
        # An ordinary save is unaffected by a full pinned shelf.
        _, state, _ = self.tools.invoke('hold', {'title': 'N', 'content': 'n', 'importance': 7})
        self.assertEqual(state, '成功')

    def test_pinned_is_not_available_during_autonomous_activity(self):
        self.tools.set_autonomy(True)
        result, state, _ = self.tools.invoke('hold', {'title': 'P', 'content': 'p', 'pinned': True,
                                                      'why_remembered': 'x'})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(self.holds(), [])

    def test_hidden_pinned_memories_still_count_toward_the_cap(self):
        for i in range(PINNED_LIMIT):
            extra = 'pinned: true\ntype: permanent\n' + ('dont_surface: true\n' if i == 0 else '')
            self.write(f'p{i}', folder='permanent', extra=extra)
        self.assertEqual(len(self.native._pinned_ids()), PINNED_LIMIT)
        _, state, _ = self.tools.invoke('hold', {'title': 'P', 'content': 'p', 'pinned': True,
                                                 'why_remembered': 'again'})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(self.holds(), [])

    def test_trace_cannot_pin_during_autonomous_activity(self):
        self.write('plain')
        self.memory.state['entries']['plain'] = {'title': 'T plain', 'time': 'x', 'signature': None, 'hidden': False, 'text': 'body plain'}
        self.tools.set_autonomy(True)
        result, state, _ = self.tools.invoke('trace', {'bucket_id': 'plain', 'pinned': 1})
        self.assertEqual(state, '被拒绝')
        self.assertIn('autonomous', result)
        self.assertNotIn('trace', [n for n, _ in self.backend.calls])

    def test_trace_pin_needs_a_reason(self):
        self.write('plain')
        self.memory.state['entries']['plain'] = {'title': 'T plain', 'time': 'x', 'signature': None, 'hidden': False, 'text': 'body plain'}
        result, state, _ = self.tools.invoke('trace', {'bucket_id': 'plain', 'pinned': 1})
        self.assertEqual(state, '被拒绝')
        self.assertIn('edit_reason', result)
        self.assertNotIn('trace', [n for n, _ in self.backend.calls])
        _, state, _ = self.tools.invoke('trace', {'bucket_id': 'plain', 'pinned': 1, 'edit_reason': '  '})
        self.assertEqual(state, '被拒绝')
        self.assertNotIn('trace', [n for n, _ in self.backend.calls])
        for odd_pin in ('true', '1', 1.0):
            _, state, _ = self.tools.invoke('trace', {'bucket_id': 'plain', 'pinned': odd_pin, 'edit_reason': '\u200b '})
            self.assertEqual(state, '被拒绝', odd_pin)
        self.assertNotIn('trace', [n for n, _ in self.backend.calls])

    def test_trace_cannot_pin_past_the_cap(self):
        for i in range(PINNED_LIMIT):
            self.write(f'p{i}', folder='permanent', extra='pinned: true\ntype: permanent\n')
        self.write('plain')
        self.memory.state['entries']['plain'] = {'title': 'T plain', 'time': 'x', 'signature': None, 'hidden': False, 'text': 'body plain'}
        self.memory.state['entries']['p0'] = {'title': 'T p0', 'time': 'x', 'signature': None, 'hidden': False, 'text': 'body p0'}
        result, state, _ = self.tools.invoke('trace', {'bucket_id': 'plain', 'pinned': 1, 'edit_reason': 'why'})
        self.assertEqual(state, '被拒绝')
        self.assertIn(str(PINNED_LIMIT), result)
        self.assertNotIn('trace', [n for n, _ in self.backend.calls])

    def test_trace_cannot_hide_a_pinned_memory_before_unpinning(self):
        self.write('p0', folder='permanent', extra='pinned: true\ntype: permanent\n')
        self.memory.state['entries']['p0'] = {'title': 'T p0', 'time': 'x', 'signature': None, 'hidden': False, 'text': 'body p0'}
        result, state, _ = self.tools.invoke('trace', {'bucket_id': 'p0', 'dont_surface': 1})
        self.assertEqual(state, '被拒绝')
        self.assertIn('Unpin', result)
        self.assertNotIn('trace', [n for n, _ in self.backend.calls])


class CatalogBrowseTests(Base):
    def seed(self):
        days = ['2026-10-19', '2026-10-19', '2026-10-21', '2026-10-25', '2026-10-25', '2026-10-30', '2026-11-01']
        for i, day in enumerate(days):
            self.write(f'm{i}', created=f'{day}T1{i}:00:00+08:00')
        self.write('nodate')

    def walk(self, **args):
        cursor, seen, total = '', [], None
        while True:
            text, state, _ = self.native.invoke('memory_catalog', {**args, 'after': cursor})
            self.assertEqual(state, '成功', text)
            page = json.loads(text)
            total = page['total']
            seen.extend(page['items'])
            cursor = page['next_after']
            if not cursor:
                return seen, total

    def test_newest_and_oldest_walk_every_memory_exactly_once(self):
        self.seed()
        newest, total = self.walk(order='newest')
        self.assertEqual(total, 8)
        ids = [row['memory_id'] for row in newest]
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8)
        self.assertEqual(ids[:3], ['m6', 'm5', 'm4'])
        self.assertEqual(ids[-1], 'nodate')
        oldest, _ = self.walk(order='oldest')
        self.assertEqual([row['memory_id'] for row in oldest], list(reversed(ids)))
        self.assertEqual(newest[0]['created'], '2026-11-01')

    def test_date_range_is_inclusive_and_pages_without_gaps(self):
        self.seed()
        rows, total = self.walk(order='oldest', created_from='2026-10-19', created_to='2026-10-25')
        self.assertEqual([row['memory_id'] for row in rows], ['m0', 'm1', 'm2', 'm3', 'm4'])
        self.assertEqual(total, 5)
        rows, total = self.walk(created_from='2026-10-30')
        self.assertEqual(sorted(row['memory_id'] for row in rows), ['m5', 'm6'])
        # A memory with no readable creation date cannot prove it is in the period.
        self.assertNotIn('nodate', [row['memory_id'] for row in rows])

    def test_default_order_and_item_shape_stay_compatible(self):
        self.seed()
        rows, total = self.walk()
        self.assertEqual([row['memory_id'] for row in rows], sorted(row['memory_id'] for row in rows))
        self.assertEqual(set(rows[0]), {'memory_id', 'title', 'created'})

    def test_bad_arguments_are_refused(self):
        self.seed()
        for args in ({'order': 'random'}, {'created_from': '2026-13-01'}, {'created_to': 'yesterday'},
                     {'created_from': '2026-11-02', 'created_to': '2026-11-01'}, {'created_from': 5}):
            self.assertEqual(self.native.invoke('memory_catalog', args)[1], '被拒绝', args)
        # A cursor that is no longer in the listing asks to restart instead of silently skipping.
        self.assertEqual(self.native.invoke('memory_catalog', {'order': 'newest', 'after': 'gone'})[1], '被拒绝')

    def test_hidden_memories_stay_out_of_time_browsing(self):
        self.write('secret', created='2026-10-20T10:00:00+08:00', extra='dont_surface: true\n')
        self.write('open', created='2026-10-20T11:00:00+08:00')
        rows, total = self.walk(order='newest', created_from='2026-10-20', created_to='2026-10-20')
        self.assertEqual([row['memory_id'] for row in rows], ['open'])
        self.assertEqual(total, 1)


class SearchDateTests(Base):
    def test_dates_reach_ombre_only_when_valid(self):
        calls = []
        self.memory.call = lambda name, args, **kw: calls.append((name, args)) or ''
        text, state, _ = self.native.invoke('breath_search', {'query': 'x', 'date_from': '2026-10-19', 'date_to': '2026-10-25'})
        self.assertEqual(state, '成功')
        self.assertEqual(calls[-1][1]['date_from'], '2026-10-19')
        self.assertEqual(calls[-1][1]['date_to'], '2026-10-25')
        self.native.invoke('breath_search', {'query': 'x'})
        self.assertNotIn('date_from', calls[-1][1])
        n = len(calls)
        for args in ({'date_from': 'last week'}, {'date_from': '2026-11-02', 'date_to': '2026-11-01'}):
            self.assertEqual(self.native.invoke('breath_search', {'query': 'x', **args})[1], '被拒绝')
        self.assertEqual(len(calls), n)

    def test_schemas_offer_the_new_arguments(self):
        self.assertIn('date_from', SEARCH.inputSchema['properties'])
        self.assertIn('created_from', MEMORY_CATALOG.inputSchema['properties'])


if __name__ == '__main__':
    unittest.main()
