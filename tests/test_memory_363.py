"""Ticket 363: no more silent gluing. Related memories are offered before a new save, an update rewrites the
whole memory, merged copies are archived (not deleted), Lan can lock, restore and see what changed."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from agent_types import UserError
from memory_agent import Memory
from memory_native import MEMORY_CHARS, NativeMemoryTools, ToolSet, dates_in
from ombre_resident import resident_config
from test_memory import Backend
from veil_agent import Budget, Character, Store


class MovingBackend(Backend):
    """Like Ombre, restoring moves the file back out of archive/."""
    def call(self, name, args):
        if name == 'trace' and isinstance(args.get('old_str'), str):
            self.calls.append((name, args))
            row = self.rows[args['bucket_id']]
            row['content'] = row['content'].replace(args['old_str'], str(args.get('new_str') or ''), 1)
            self.write_row(args['bucket_id'])
            return 'updated'
        return super().call(name, args)

    def write_row(self, key):
        if not self.rows[key].get('deleted'):
            (self.vault.parent / 'archive' / f'{key}.md').unlink(missing_ok=True)
        super().write_row(key)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 8192})
        self.backend = MovingBackend(self.store.root / 'fake' / 'memory' / 'dynamic')
        self.memory = Memory(self.cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='files ',
                                consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('denied', '被拒绝', n), preview_summary=lambda n, a: n)
        self.native = NativeMemoryTools(self.memory)
        self.tools = ToolSet(files, self.native)

    def hold(self, **args):
        return self.tools.invoke('hold', args, None)

    def saved(self, title, content):
        """Save a memory, confirming once if related memories are offered."""
        result, state, _ = self.hold(title=title, content=content)
        if state == '待选择':
            result, state, _ = self.hold(title=title, content=content)
        self.assertEqual(state, '成功', result)
        return result.split('memory_id: ')[1].split()[0]

    def holds(self):
        return [args for name, args in self.backend.calls if name == 'hold']


class NewSaveTests(Fixture):
    def test_first_memory_is_saved_directly_when_nothing_is_related(self):
        result, state, _ = self.hold(title='Lamp', content='she showed me the blue lamp on 11-02.')
        self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.holds()), 1)

    def test_related_memories_are_offered_first_and_sending_it_again_saves_it_new(self):
        old = self.saved('Lamp', 'she showed me the blue lamp on 11-02.')
        result, state, summary = self.hold(title='Lamp again', content='we talked about the lamp again.')
        self.assertEqual(state, '待选择')
        self.assertIn(old, result)
        self.assertIn('Not saved yet', result)
        self.assertIn('先看相关记忆', summary)
        self.assertEqual(len(self.holds()), 1)            # nothing written yet
        result, state, _ = self.hold(title='Lamp again', content='we talked about the lamp again.')
        self.assertEqual(state, '成功', result)
        self.assertEqual(len(self.holds()), 2)

    def test_changing_the_text_is_a_new_save_that_is_checked_again(self):
        self.saved('Lamp', 'she showed me the blue lamp.')
        self.assertEqual(self.hold(title='x', content='one')[1], '待选择')
        self.assertEqual(self.hold(title='x', content='two')[1], '待选择')

    def test_link_to_saves_a_later_stage_and_records_the_link(self):
        old = self.saved('Fight', 'we fought at 5am on 11-04 about my stiff apology lines.')
        self.tools.invoke('breath_search', {'query': 'fight'}, None)
        result, state, summary = self.hold(title='Made up', content='we made up the next evening.', link_to=old)
        self.assertEqual(state, '成功', result)
        new = result.split('memory_id: ')[1].split()[0]
        self.assertEqual(self.native.sidecar.get(new)['links'], [old])
        self.assertIn('接着', summary)
        panel = {item['memory_id']: item for item in self.native.panel_entries()['items']}
        self.assertEqual(panel[new]['links'][0]['memory_id'], old)
        self.assertEqual(panel[old]['continued_by'][0]['memory_id'], new)

    def test_link_to_an_unseen_memory_is_refused(self):
        self.assertEqual(self.hold(title='t', content='c', link_to='nope')[1], '被拒绝')

    def test_a_memory_longer_than_the_old_cap_is_saved_whole(self):
        # Ticket 421: the fixed 600-character cap is gone; long memories are kept, not cut into fragments.
        text = 'x' * 2000
        result, state, _ = self.hold(title='Long', content=text)
        self.assertEqual(state, '成功', result)
        self.assertEqual(self.holds()[-1]['content'], text)

    def test_every_save_records_when_it_happened(self):
        key = self.saved('Lamp', 'the blue lamp.')
        self.assertEqual(self.native.sidecar.get(key)['sources'][0]['via'], 'hold')


class RewriteTests(Fixture):
    def setUp(self):
        super().setUp()
        self.a = self.saved('Fog Tide', 'on 11-06 i bet her a week on fog tide.')
        self.b = self.saved('Fog Tide copy', 'fog tide week, honey and the axe.')
        self.tools.invoke('breath_search', {'query': 'fog'}, None)
        self.backend.calls.clear()

    def test_rewrite_replaces_the_whole_text_and_keeps_the_old_version(self):
        text = 'on 11-06 i bet her a week on fog tide; by 11-12 i let my rank slip for a canvas.'
        result, state, _ = self.hold(target_id=self.a, content=text)
        self.assertEqual(state, '成功', result)
        self.assertEqual(self.backend.rows[self.a]['content'], text)
        versions = self.store.read('fake/memory-versions.jsonl')
        self.assertIn('i bet her a week', versions[-1]['old_content'])
        self.assertEqual(versions[-1]['operation_reason'], 'hold(target_id) 重写')

    def test_merge_archives_the_copies_and_lan_can_restore_them(self):
        text = 'on 11-06 i bet her a week on fog tide: honey, the axe she checked, the cabin.'
        result, state, summary = self.hold(target_id=self.a, content=text, merge_ids=[self.b])
        self.assertEqual(state, '成功', result)
        self.assertIn('归档 1 条', summary)
        self.assertTrue(self.backend.rows[self.b]['deleted'])             # Ombre's archive, not an erase
        self.assertTrue((self.store.root / 'fake/memory/archive' / f'{self.b}.md').exists())
        self.assertNotIn(self.b, self.tools.invoke('breath_search', {'query': 'fog'}, None)[0])
        archived = self.native.panel_archived()
        self.assertEqual([row['memory_id'] for row in archived], [self.b])
        self.assertEqual(archived[0]['merged_into'], self.a)
        ok, message = self.native.panel_unarchive(self.b)
        self.assertTrue(ok, message)
        self.assertIn(self.b, self.tools.invoke('breath_search', {'query': 'fog'}, None)[0])
        self.assertEqual(self.native.panel_archived(), [])

    def test_dropping_a_date_is_asked_once_and_sending_it_again_confirms(self):
        result, state, _ = self.hold(target_id=self.a, content='i bet her a week on fog tide.')
        self.assertEqual(state, '待选择')
        self.assertIn('11-6', result)
        self.assertEqual(self.backend.calls, [])
        result, state, _ = self.hold(target_id=self.a, content='i bet her a week on fog tide.')
        self.assertEqual(state, '成功', result)

    def test_a_date_written_another_way_still_counts(self):
        result, state, _ = self.hold(target_id=self.a, content='on nov 6 i bet her a week on fog tide.')
        self.assertEqual(state, '成功', result)

    def test_lock_blocks_rewrite_merge_and_trace_but_not_lans_edit(self):
        self.assertTrue(self.native.panel_lock(self.a, True)[0])
        self.assertEqual(self.hold(target_id=self.a, content='on 11-06 changed.')[1], '被拒绝')
        self.assertEqual(self.hold(target_id=self.b, content='fog tide 11-06.', merge_ids=[self.a])[1], '被拒绝')
        result, state, _ = self.native._trace({'bucket_id': self.a, 'content': 'changed'}, None)
        self.assertEqual(state, '被拒绝')
        self.assertIn('locked', result)
        ok, message = self.native.panel_edit(self.a, {'content': 'Lan fixed this: 11-06, fog tide.'})
        self.assertTrue(ok, message)
        self.native.panel_lock(self.a, False)
        self.assertEqual(self.native._trace({'bucket_id': self.a, 'content': 'on 11-06 mine'}, None)[1], '成功')

    def test_lock_also_blocks_zero_and_false_values(self):
        self.native.panel_lock(self.a, True)
        self.assertEqual(self.native._trace({'bucket_id': self.a, 'resolved': 0}, None)[1], '被拒绝')
        self.assertEqual(self.native._trace({'bucket_id': self.a, 'pinned': 0}, None)[1], '被拒绝')

    def test_a_damaged_sidecar_does_not_read_as_unlocked(self):
        self.native.panel_lock(self.a, True)
        with open(self.store.root / 'fake' / 'memory-sidecar.jsonl', 'a', encoding='utf-8') as file:
            file.write('{broken')
        with self.assertRaises(UserError):                               # stops like any damaged ledger
            self.native._trace({'bucket_id': self.a, 'content': 'changed'}, None)
        self.assertNotEqual(self.backend.rows[self.a]['content'], 'changed')

    def test_a_replacement_may_grow_a_memory(self):
        # Ticket 421: the old 600-character ceiling no longer blocks a real update; this keeps the
        # old_str/new_str branch covered instead of switching to a whole-content rewrite.
        result, state, _ = self.native._trace(
            {'bucket_id': self.a, 'old_str': 'fog tide', 'new_str': 'z' * 2000}, None)
        self.assertEqual(state, '成功', result)

    def test_peter_changing_what_lan_corrected_is_flagged_until_she_looks(self):
        self.native.panel_edit(self.a, {'content': 'Lan fixed this: on 11-06 fog tide.'})
        self.tools.invoke('breath_search', {'query': 'fog'}, None)
        self.assertEqual(self.hold(target_id=self.a, content='on 11-06 fog tide, my way.')[1], '成功')
        item = next(i for i in self.native.panel_entries()['items'] if i['memory_id'] == self.a)
        self.assertTrue(item['peter_after_lan'])
        self.native.panel_seen(self.a)
        item = next(i for i in self.native.panel_entries()['items'] if i['memory_id'] == self.a)
        self.assertIsNone(item['peter_after_lan'])

    def test_lan_can_restore_an_earlier_version(self):
        self.hold(target_id=self.a, content='on 11-06 something else entirely.')
        versions = self.native.panel_versions(self.a)
        self.assertIn('i bet her a week', versions[0]['body'])
        ok, message = self.native.panel_restore_version(self.a, versions[0]['index'])
        self.assertTrue(ok, message)
        self.assertEqual(self.backend.rows[self.a]['content'], versions[0]['body'])

    def test_a_long_old_version_is_restored_whole_not_refused_or_cut(self):
        long_text = ('on 11-06 ' + 'fog tide, again and again. ' * 120).strip()   # well past the page box (1500) and recall (600)
        self.assertGreater(len(long_text), 1500)
        self.hold(target_id=self.a, content='on 11-06 something else entirely.')
        versions = self.native.panel_versions(self.a)
        versions[0]['body'] = long_text                      # as if that earlier version had been this long
        self.native.panel_versions = lambda memory_id: versions
        ok, message = self.native.panel_restore_version(self.a, versions[0]['index'])
        self.assertTrue(ok, message)
        self.assertEqual(self.backend.rows[self.a]['content'].strip(), long_text)
        self.assertNotIn('memory_read', message)   # ticket 421: recall reads the whole story now

    def test_a_version_ombre_itself_would_refuse_is_not_restored(self):
        self.hold(target_id=self.a, content='on 11-06 something else entirely.')
        versions = self.native.panel_versions(self.a)
        versions[0]['body'] = 'x' * (48 * 1024 + 1)
        self.native.panel_versions = lambda memory_id: versions
        ok, message = self.native.panel_restore_version(self.a, versions[0]['index'])
        self.assertFalse(ok)
        self.assertIn('没有恢复', message)

    def test_peters_trace_may_grow_a_memory(self):
        # Ticket 421: trace can rewrite a long memory whole; recall already reads full stories (ticket 419).
        result, state, _ = self.native._trace({'bucket_id': self.a, 'content': 'y' * 2000}, None)
        self.assertNotEqual(state, '被拒绝', result)
        self.assertEqual(self.backend.rows[self.a]['content'], 'y' * 2000)


class SmallPieces(unittest.TestCase):
    def test_dates_in_reads_the_usual_ways_of_writing_a_day(self):
        self.assertEqual(dates_in('10-31, 2026-11-05, 10月25日, oct 3, 12 november'),
                         {(10, 31), (11, 5), (10, 25), (10, 3), (11, 12)})
        self.assertEqual(dates_in('no dates here, rank 12:30'), set())
        self.assertEqual(dates_in('since 11-06 we'), {(11, 6)})          # an ordinary word must not eat the date

    def test_ombre_merge_is_off_unless_asked_for(self):
        cfg = SimpleNamespace(name='Peter', provider={'model': 'm', 'base_url': 'https://x'},
                              memory={'model': 'e', 'base_url': 'https://y'})
        self.assertEqual(resident_config(cfg, Path('v'), 't' * 64)['merge_threshold'], 1000)
        cfg.memory['ombre_auto_merge'] = True
        self.assertNotIn('merge_threshold', resident_config(cfg, Path('v'), 't' * 64))


if __name__ == '__main__':
    unittest.main()
