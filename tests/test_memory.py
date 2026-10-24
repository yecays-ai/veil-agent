import json
from decimal import Decimal
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from memory_agent import MCP, Memory, MemoryToolError, ordinary_hits, vault_path
from ombre_resident import resident_config
from veil_agent import Budget, Character, Chat, Store, UserError


class Backend:
    def __init__(self, vault=None):
        self.vault = vault
        self.rows = {}
        self.calls = []
        self.fail = False

    def write_row(self, key):
        if self.vault is None:
            return
        row = self.rows[key]
        # Like Ombre, a delete moves the Markdown file into archive/.
        folder = self.vault.parent / 'archive' if row.get('deleted') else self.vault
        folder.mkdir(parents=True, exist_ok=True)
        (self.vault / f'{key}.md').unlink(missing_ok=True)
        (folder / f'{key}.md').write_text(
            f'---\nid: {key}\ntitle: Fictional memory\n'
            f'dont_surface: {str(bool(row.get("hidden"))).lower()}\n'
            f'type: {"archived" if row.get("deleted") else "dynamic"}\n---\n{row["content"]}',
            encoding='utf-8')

    def call(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            raise RuntimeError('fictional failure')
        if name == 'breath':
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}' for k, v in self.rows.items())
        if name == 'hold':
            key = 'feel_' + str(len(self.rows)) if args.get('feel') else f'{len(self.rows):012x}'
            self.rows[key] = dict(args)
            self.write_row(key)
            return ('feel→' if args.get('feel') else '新建→') + key
        if name == 'trace':
            if args.get('delete'):
                self.rows[args['bucket_id']]['deleted'] = True
            elif args.get('restore'):
                self.rows[args['bucket_id']]['deleted'] = False
            elif 'content' in args:
                self.rows[args['bucket_id']]['content'] = args['content']
            else:
                self.rows[args['bucket_id']]['hidden'] = bool(args['dont_surface'])
            self.write_row(args['bucket_id'])
            return 'updated'
        if name == 'feel':
            # Simulate the upstream feel route ignoring dont_surface.
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}'
                for k, v in self.rows.items() if v.get('feel'))
        if name == 'breath_search':
            rows = [(k, v) for k, v in self.rows.items()
                    if not v.get('hidden') and not v.get('deleted') and not v.get('feel')]
            return '\n'.join(f'[bucket_id:{k}] {v["content"]}' for k, v in rows) + (
                '\n=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': [k for k, _ in rows]}) + '\n```')
        raise AssertionError(name)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024,
            {'model': 'fake', 'base_url': 'https://example.invalid/v1',
             'max_input_tokens': 10000, 'input_cny_per_million': Decimal(2),
             'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}, 'fake',
            {'model': 'fake-embedding', 'base_url': 'https://example.invalid/v1',
             'dimensions': 3, 'input_cny_per_million': '0.07'})
        self.budget = Budget(self.store)
        self.backend = Backend(self.store.root / 'fake' / 'memory' / 'dynamic')
        self.model = Mock()
        self.memory = self.open()

    def open(self):
        memory = Memory(self.cfg, self.store, self.budget, self.model, self.backend)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    def save(self, content='Fictional memory body'):
        args = {'content': content, 'title': 'Fictional memory'}
        text = self.memory.call('hold', args)
        self.memory.record_native(args, text)
        return self.memory.created_id(text)


    def test_delete_needs_confirmation_then_removes_the_memory_from_list_and_recall(self):
        key = self.save()
        asked = self.memory.command(f'/delete {key}')
        self.assertIn(f'/delete {key} 确认', asked)
        self.assertIn('不能恢复', asked)
        self.assertNotIn('/forget', asked)
        self.assertNotIn('deleted', self.backend.rows[key])
        done = self.memory.command(f'/delete {key} 确认')
        self.assertIn('已删除', done)
        self.assertIn('不能恢复', done)
        self.assertTrue(self.backend.rows[key]['deleted'])
        self.assertNotIn(key, self.memory.command('/memory'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [])
        self.assertIn('没有这个记忆编号', self.memory.command(f'/delete {key} 确认'))

    def test_confirmed_delete_erases_the_file_and_local_copies(self):
        key, other = self.save('secret body'), self.save('kept body')
        self.store.append('fake/memory-versions.jsonl', {'memory_id': key, 'old_content': 'old secret'})
        self.store.append('fake/memory-versions.jsonl', {'memory_id': other, 'old_content': 'old kept'})
        self.store.append('fake/delete-requests.jsonl', {'memory_id': key, 'title': 'Secret', 'reason': 'why'})
        self.memory.command(f'/delete {key}')
        self.assertIn('抹掉', self.memory.command(f'/delete {key} 确认'))
        memory_dir = self.store.root / 'fake' / 'memory'
        self.assertFalse(any(key in path.name for path in memory_dir.rglob('*.md')))
        self.assertTrue(any(other in path.name for path in memory_dir.rglob('*.md')))
        self.assertEqual([row['memory_id'] for row in self.store.read('fake/memory-versions.jsonl')], [other])
        self.assertEqual(self.store.read('fake/delete-requests.jsonl')[0]['title'], '')
        entry = self.memory.state['entries'][key]
        self.assertEqual((entry['title'], entry.get('text')), ('', None))
        self.assertTrue(entry['erased'])
        self.assertEqual(self.memory.visible([(key, 'x')]), [])
        self.assertNotIn('secret body', self.store.read('fake/memory/_agent/state.jsonl')[-1]['state'].__repr__())

    def test_unconfirmed_delete_erases_nothing_until_retried(self):
        key = self.save('secret body')
        self.backend.fail = True
        self.memory.command(f'/delete {key}')
        self.memory.command(f'/delete {key} 确认')
        self.backend.fail = False
        memory_dir = self.store.root / 'fake' / 'memory'
        self.assertTrue(any(key in path.name for path in memory_dir.rglob('*.md')))
        reopened = self.open()
        self.assertTrue(any(key in path.name for path in memory_dir.rglob('*.md')))
        reopened.command(f'/delete {key}')
        self.assertFalse(any(key in path.name for path in memory_dir.rglob('*.md')))

    def test_memories_deleted_before_erase_existed_are_erased_once_on_start(self):
        key = self.save('old secret')
        self.backend.call('trace', {'bucket_id': key, 'delete': True})
        with self.memory.mutex:
            self.memory.state['entries'][key].update(hidden=True, deleted=True)
            self.memory.persist()
        decayed = self.save('faded by Ombre')
        self.backend.rows[decayed]['deleted'] = True  # Archived by Ombre, not deleted by Lan.
        self.backend.write_row(decayed)
        reopened = self.open()
        memory_dir = self.store.root / 'fake' / 'memory'
        self.assertFalse(any(key in path.name for path in memory_dir.rglob('*.md')))
        self.assertTrue(any(decayed in path.name for path in memory_dir.rglob('*.md')))
        self.assertIn('彻底抹掉 1 条', ' '.join(reopened.notices()))
        self.assertEqual(self.open().notices(), [])

    def test_failed_delete_stays_blocked_after_restart_and_can_be_retried(self):
        kept, deleted = self.save('kept body'), self.save('deleted body')
        self.backend.fail = True
        self.memory.command(f'/delete {deleted}')
        self.assertIn('没能确认', self.memory.command(f'/delete {deleted} 确认'))
        self.backend.fail = False
        reopened = self.open()
        calls_before = len(self.backend.calls)
        self.assertEqual(reopened.visible([(kept, 'x'), (deleted, 'y')]), [(kept, 'x')])
        self.assertNotIn(deleted, reopened.command('/memory'))
        self.assertEqual(len(self.backend.calls), calls_before)  # Restart touches nothing.
        self.assertIn('已删除', reopened.command(f'/delete {deleted}') + reopened.command(f'/delete {deleted} 确认'))
        self.assertTrue(self.backend.rows[deleted]['deleted'])

    def test_hide_commands_are_retired_and_change_nothing(self):
        key = self.save()
        calls_before = len(self.backend.calls)
        for command in (f'/forget {key}', f'/forget {key} 确认', f'/restore {key}'):
            self.assertIn('隐藏功能已取消', self.memory.command(command))
        self.assertEqual(len(self.backend.calls), calls_before)
        self.assertIn(key, self.memory.command('/memory'))
        self.assertNotIn('/forget', self.memory.command('/memory'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [(key, 'x')])

    def test_legacy_extract_state_loads_without_its_chat_cursor(self):
        key = self.save()
        state = self.store.read('fake/memory/_agent/state.jsonl')[-1]['state']
        self.store.append('fake/memory/_agent/state.jsonl',
                          {'state': {**state, 'cursor': 4, 'pending': [{'id': 0, 'attempts': 1}]}})
        reopened = self.open()
        self.assertNotIn('cursor', reopened.state)
        self.assertNotIn('pending', reopened.state)
        self.assertIn(key, reopened.command('/memory'))

    def test_delete_stays_out_of_recall_even_if_ombre_does_not_confirm(self):
        key = self.save()
        self.backend.fail = True
        self.memory.command(f'/delete {key}')
        self.assertIn('没能确认', self.memory.command(f'/delete {key} 确认'))
        self.assertEqual(self.memory.visible([(key, 'x')]), [])
        self.assertNotIn(key, self.memory.command('/memory'))
        self.backend.fail = False
        self.assertIn('已删除', self.memory.command(f'/delete {key}'))
        self.assertIn('没有这个记忆编号', self.memory.command(f'/delete {key} 确认'))

    def test_delete_confirm_without_asking_first_only_shows_the_memory(self):
        key = self.save()
        self.assertIn(f'/delete {key} 确认', self.memory.command(f'/delete {key} 确认'))
        self.assertNotIn('deleted', self.memory.state['entries'][key])
        self.assertIn('已删除', self.memory.command(f'/delete {key} 确认'))

    def test_delete_unknown_number_changes_nothing(self):
        self.assertIn('没有这个记忆编号', self.memory.command('/delete nope 确认'))
        self.assertEqual(self.backend.calls[-1][0], 'breath')

    def test_ombre_model_calls_are_estimated_into_the_shared_ledger(self):
        from memory_agent import ombre_model_tokens
        content = '下周有安排。' * 40
        normal_in, normal_out = ombre_model_tokens(content)
        expected = (Decimal(normal_in) * Decimal('2')
                    + Decimal(normal_out) * Decimal('8')) / 1_000_000
        self.memory.call('hold', {'content': content})
        costs = self.store.read('costs.jsonl')
        modelled = [c for c in costs if c.get('accounting') == 'ombre-model-estimate']
        self.assertEqual(len(modelled), 2)  # reserve + settle
        self.assertGreater(Decimal(modelled[0]['amount']), Decimal(modelled[1]['amount']))
        self.assertEqual(Decimal(modelled[1]['amount']), expected)
        self.assertGreater(Decimal(modelled[1]['amount']), 0)
        self.assertGreater(Decimal(modelled[0]['amount'])-Decimal(modelled[1]['amount']), 0)
        self.assertEqual(self.budget.totals()[2], 0)

    def test_ombre_model_can_be_switched_off(self):
        self.cfg.memory.update(ombre_model=False, base_url='https://example.invalid/v1')
        self.memory.call('hold', {'content': 'x'})
        costs = self.store.read('costs.jsonl')
        self.assertFalse(any(c.get('accounting') == 'ombre-model-estimate' for c in costs))
        self.assertEqual(resident_config(self.cfg, Path('v'), 'x' * 64)['dehydration'], {'api_key': ''})

    def test_ombre_config_uses_chat_model_without_a_key_in_the_file(self):
        self.cfg.memory['base_url'] = 'https://example.invalid/v1'
        self.cfg.provider['base_url'] = 'https://api.deepseek.com'
        dehydration = resident_config(self.cfg, Path('v'), 'x' * 64)['dehydration']
        self.assertEqual(dehydration['model'], 'fake')
        self.assertEqual(dehydration['api_key'], '')
        self.assertEqual(dehydration['extra_body'], {'thinking': {'type': 'disabled'}})
        self.cfg.provider['base_url'] = 'https://api.openai.com/v1'
        self.assertNotIn('extra_body', resident_config(self.cfg, Path('v'), 'x' * 64)['dehydration'])

    def test_ombre_model_budget_blocks_before_the_backend(self):
        self.budget.daily = Decimal('0.0000001')
        before = len(self.backend.calls)
        with self.assertRaises(UserError):
            self.memory.call('hold', {'content': 'x'})
        self.assertEqual(len(self.backend.calls), before)

    def test_top_three_provenance_and_nohit(self):
        empty = self.memory.context('something new')
        self.assertIn('do not claim to remember', empty[-1]['content'])
        self.assertIn('not newly said by the user', empty[-1]['content'])
        self.assertNotRegex('\n'.join(item['content'] for item in empty), r'[\u3400-\u9fff]')
        for i in range(5):
            self.backend.rows[str(i)] = {'content': f'body {i}'}
            self.backend.write_row(str(i))
        text = self.memory.context('plan')[-1]['content']
        self.assertEqual(text.count('[bucket_id:'), 3)
        self.assertNotIn('body 4', text)

    def test_random_surfacing_and_exact_id_are_not_hits(self):
        raw = '[bucket_id:bad] unrelated\n=== ombre:result-ids ===\n```json\n{"bucket_ids": []}\n```'
        self.assertEqual(ordinary_hits(raw), [])
        self.assertEqual(ordinary_hits('[bucket_id:bad] exact'), [])

    def diagnostics_text(self):
        return (Path(self.temp.name) / 'data/fake/memory/_agent/diagnostics.jsonl').read_text(encoding='utf-8')

    def test_runtime_monitor_receives_mcp_event_when_diagnostic_append_fails(self):
        backend = MCP.__new__(MCP)
        backend.call = Mock(return_value='')
        monitor = Mock()
        memory = Memory(self.cfg, self.store, self.budget, self.model, backend,
                        runtime_monitor=monitor)
        self.addCleanup(memory.__exit__, None, None, None)
        facts = {'code': 'mcp_call', 'tool': 'hold', 'attempt': 1}
        original_append = self.store.append
        self.store.append = Mock(side_effect=OSError('fictional diagnostics disk failure'))
        try:
            backend.diagnostic(facts)
        finally:
            self.store.append = original_append
        monitor.observe_memory.assert_called_once_with(facts)

    def test_failed_memory_tool_diagnostic_omits_arguments_and_memory_body(self):
        self.backend.fail = True
        with self.assertRaises(RuntimeError):
            self.memory.call('hold', {'content': 'FICTIONAL_MEMORY_BODY',
                                      'query': 'FICTIONAL_QUERY', 'title': 'fictional'})
        row = json.loads(self.diagnostics_text().splitlines()[-1])
        self.assertEqual(row['code'], 'memory_tool_failed')
        self.assertEqual(row['tool'], 'hold')
        self.assertEqual(row['error_type'], 'RuntimeError')
        self.assertNotIn('FICTIONAL_MEMORY_BODY', self.diagnostics_text())
        self.assertNotIn('FICTIONAL_QUERY', self.diagnostics_text())

    def test_memory_tool_diagnostic_keeps_only_the_safe_mcp_error_code(self):
        self.backend.call = Mock(side_effect=MemoryToolError('unknown'))
        with self.assertRaises(MemoryToolError):
            self.memory.call('breath_search', {'query': 'FICTIONAL_QUERY'})
        row = json.loads(self.diagnostics_text().splitlines()[-1])
        self.assertEqual(row, {
            'time': row['time'], 'code': 'memory_tool_failed', 'tool': 'breath_search',
            'error_type': 'MemoryToolError', 'error_code': 'unknown',
        })
        self.assertNotIn('FICTIONAL_QUERY', self.diagnostics_text())

    def test_retrieval_failure_records_safe_mcp_stage_without_query_or_memory(self):
        def fail_search(name, args):
            if name == 'breath_search':
                raise MemoryToolError('unknown', stage='mcp_initialize')
            return ''
        self.backend.call = Mock(side_effect=fail_search)
        self.memory.context('FICTIONAL_QUERY')
        row = json.loads(self.diagnostics_text().splitlines()[-1])
        self.assertEqual(row, {
            'time': row['time'], 'code': 'retrieval_failed', 'stage': 'breath_search',
            'error_type': 'MemoryToolError', 'error_code': 'unknown',
            'mcp_stage': 'mcp_initialize',
        })
        notice = ' '.join(self.memory.notices())
        self.assertIn('Safe failure stage: mcp_initialize.', notice)
        self.assertNotIn('FICTIONAL_QUERY', self.diagnostics_text())

    def state_reset(self):
        self.memory.state['cursor'] = 0
        self.memory.state['pending'] = []

    def test_roles_and_redirected_directories(self):
        self.assertNotEqual(vault_path(self.store.root, 'a'), vault_path(self.store.root, 'b'))
        for key in ('../b', 'a/b', '..'):
            with self.assertRaises(ValueError):
                vault_path(self.store.root, key)
        self.cfg.id = 'second'
        other = self.open()
        self.assertEqual(other.state['entries'], {})

    def test_embedding_budget_blocks_before_backend(self):
        self.budget.daily = Decimal('0.00001')
        before = len(self.backend.calls)
        with self.assertRaises(UserError):
            self.memory.call('hold', {'content': 'fake'})
        self.assertEqual(len(self.backend.calls), before)

    def test_chat_injects_memory_and_updates_truth(self):
        model = Mock(return_value=('reply', 200, 50, 0))
        chat = Chat(self.cfg, self.store, self.budget, model, self.memory)
        chat.send('hello')
        sent = model.call_args.args[0]
        self.assertIn('Long-term memory:', sent[1]['content'])
        self.assertIn('not newly said by the user', sent[3]['content'])
        self.assertEqual([r['role'] for r in self.store.read('fake/chat.jsonl')], ['user', 'assistant'])

    def test_concurrent_reservations_share_one_atomic_limit(self):
        self.budget.daily = Decimal('0.15')
        results = []
        def reserve():
            try:
                self.budget.estimate('fake', 'embed', 100000, '1')
                results.append(True)
            except UserError:
                results.append(False)
        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [False, True])

    def test_ombre_model_estimate_covers_pinned_output_caps_and_retries(self):
        from memory_agent import ombre_model_tokens
        tokens_in, tokens_out = ombre_model_tokens('fictional memory', worst_case=True)
        normal_in, normal_out = ombre_model_tokens('fictional memory')
        self.assertGreaterEqual(tokens_in, 3 * ((3200 + 16 + 1000 + 16 + 5400) // 2))
        self.assertEqual(tokens_out, 3 * (4096 + 1024))
        self.assertEqual(normal_in, (3200 + 16 + 1000 + 16 + 5400) // 2)
        self.assertEqual(normal_out, 800)

    def test_three_fake_child_retries_are_inside_the_shared_reservation(self):
        from memory_agent import ombre_model_tokens
        tokens_in, tokens_out = ombre_model_tokens('fictional memory', worst_case=True)
        normal_in, normal_out = ombre_model_tokens('fictional memory')
        maximum = (Decimal(tokens_in) * Decimal('2') + Decimal(tokens_out) * Decimal('8')) / 1_000_000
        normal = (Decimal(normal_in) * Decimal('2') + Decimal(normal_out) * Decimal('8')) / 1_000_000
        attempts = []

        def fake_child_call(name, args):
            reserve = next(row for row in self.store.read('costs.jsonl')
                           if row.get('type') == 'reserve'
                           and row.get('accounting') == 'ombre-model-estimate')
            self.assertGreaterEqual(Decimal(reserve['amount']), maximum)
            attempts.extend([1, 2, 3])
            return 'fictional success after retries'

        self.backend.call = Mock(side_effect=fake_child_call)
        self.memory.call('hold', {'content': 'fictional memory'})
        self.assertEqual(attempts, [1, 2, 3])
        rows = self.store.read('costs.jsonl')
        model_reserve = next(r for r in rows if r['type'] == 'reserve' and r.get('accounting') == 'ombre-model-estimate')
        model_settle = next(r for r in rows if r['type'] == 'settle' and r.get('id') == model_reserve['id'])
        self.assertEqual(Decimal(model_settle['amount']), normal)
        self.assertEqual(Decimal(model_settle['released_amount']), Decimal(model_reserve['amount'])-normal)
        self.assertEqual(self.budget.totals()[2], 0)

    def test_failed_ombre_call_settles_conservative_reservations(self):
        original = self.backend.call
        self.backend.call = Mock(side_effect=RuntimeError('fictional timeout'))
        with self.assertRaises(RuntimeError):
            self.memory.call('hold', {'content': 'fictional memory'})
        self.assertEqual(self.budget.totals()[2], 0)
        rows = self.store.read('costs.jsonl')
        reservations = [row for row in rows if row['type'] == 'reserve']
        settlements = [row for row in rows if row['type'] == 'settle']
        self.assertEqual(len(reservations), 2)
        by_id = {row['id']: row for row in reservations}
        self.assertEqual(set(by_id), {row['id'] for row in settlements})
        for settlement in settlements:
            self.assertEqual(Decimal(settlement['amount']), Decimal(by_id[settlement['id']]['amount']))
            self.assertEqual(Decimal(settlement['released_amount']), Decimal('0'))
        self.backend.call = original

    def test_successful_embedding_settles_normal_estimate_and_releases_difference(self):
        args = {'query': 'fictional recall'}
        normal_tokens = max(8192, len(json.dumps(args, ensure_ascii=False).encode('utf-8')) + 2048)
        rate = Decimal(str(self.cfg.memory['input_cny_per_million']))
        self.backend.call = Mock(return_value='fictional result')
        self.memory.call('breath_search', args)
        rows = self.store.read('costs.jsonl')
        reserve = next(r for r in rows if r['type'] == 'reserve' and r.get('accounting') == 'embedding-estimate')
        settle = next(r for r in rows if r['type'] == 'settle' and r['id'] == reserve['id'])
        normal_amount = Decimal(normal_tokens) * rate / Decimal(1_000_000)
        self.assertGreater(Decimal(reserve['amount']), normal_amount)
        self.assertEqual(Decimal(settle['amount']), normal_amount)
        self.assertEqual(Decimal(settle['released_amount']),
                         Decimal(reserve['amount']) - normal_amount)
        self.assertEqual(self.budget.totals()[2], 0)

    def test_later_preflight_rejection_releases_only_unsent_reservation(self):
        original = self.backend.call
        self.budget.estimate_model = Mock(side_effect=UserError('fictional budget limit'))
        with self.assertRaises(UserError):
            self.memory.call('hold', {'content': 'fictional memory'})
        self.backend.call = original
        self.assertEqual(self.backend.calls, [('breath', {})])
        rows = self.store.read('costs.jsonl')
        reserves = [row for row in rows if row['type'] == 'reserve']
        settlements = [row for row in rows if row['type'] == 'settle']
        self.assertEqual(len(reserves), 1)
        self.assertEqual(len(settlements), 1)
        self.assertEqual(settlements[0]['amount'], '0')
        self.assertEqual(self.budget.totals()[2], 0)

    def test_recovered_ombre_reservation_is_settled_once_after_restart(self):
        pending = self.budget.estimate_model('fake', 'fake-chat', 100, 50, '1', '2')
        self.memory.__exit__(None, None, None)
        reopened = self.open()
        self.assertEqual(self.budget.totals()[2], 0)
        rows = self.store.read('costs.jsonl')
        settles = [row for row in rows if row['type'] == 'settle' and row['id'] == pending[0]]
        self.assertEqual(len(settles), 1)
        self.assertEqual(settles[0]['amount'], str(pending[1]))
        self.assertEqual(self.budget.settle_estimate(pending, 'duplicate-recovery'), False)
        self.assertEqual(len([row for row in self.store.read('costs.jsonl')
                              if row['type'] == 'settle' and row['id'] == pending[0]]), 1)
        reopened.__exit__(None, None, None)

