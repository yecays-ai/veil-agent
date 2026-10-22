import json
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from memory_agent import Memory, MemoryToolError
from memory_native import HOLD, NativeMemoryTools, ToolSet
from long_tasks import LongTaskStore
from test_memory import Backend
from tools_agent import permitted_tools
from veil_agent import Budget, Character, Chat, Store, build_self_description, memory_mode


class MergingBackend(Backend):
    """Like Ombre: a hold with the title of an existing memory merges into it."""
    def call(self, name, args):
        if name == 'hold' and not args.get('feel'):
            for key, row in self.rows.items():
                if row.get('title') == args.get('title'):
                    self.calls.append((name, args))
                    row.update(args)
                    return f'合并→{key}'
        return super().call(name, args)


class NativeMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 200})
        self.budget = Budget(self.store)
        self.backend = MergingBackend()
        self.model = Mock()
        self.memory = Memory(self.cfg, self.store, self.budget, self.model, self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='You have no file tools. ',
                                consume_capability_event=lambda: None,
                                invoke=lambda n, a, ap: ('This tool is not authorized for the current role.', '被拒绝', n),
                                preview_summary=lambda n, a: n)
        self.tools = ToolSet(files, NativeMemoryTools(self.memory, 200))
        self.output = []

    def test_catalog_pages_and_exact_read_preserve_current_unicode_text(self):
        native = self.tools.native
        native.max_result_bytes = 800
        vault = self.store.root / self.cfg.id / 'memory' / 'dynamic'
        vault.mkdir(parents=True, exist_ok=True)
        originals = {}
        for i in range(8):
            key = f'm{i:03}'
            raw = f'---\nid: {key}\ntitle: 示例 {i}\n---\n' + '中文🙂 exact\n' * 220
            (vault / f'{key}.md').write_text(raw, encoding='utf-8')
            originals[key] = raw
        cursor, ids = '', []
        while True:
            text, state, _ = native.invoke('memory_catalog', {'after': cursor})
            self.assertEqual(state, '成功')
            self.assertLessEqual(len(text.encode('utf-8')), 800)
            page = json.loads(text)
            ids.extend(row['memory_id'] for row in page['items'])
            cursor = page['next_after']
            if not cursor:
                break
        self.assertEqual(ids, sorted(originals))
        offset, parts, version = 0, [], None
        while True:
            args = {'memory_id': ids[0], 'offset': offset}
            if version:
                args['version'] = version
            text, state, _ = native.invoke('memory_read', args)
            self.assertEqual(state, '成功')
            self.assertLessEqual(len(text.encode('utf-8')), 800)
            page = json.loads(text)
            parts.append(page['text'])
            offset, version = page['next_offset'], page['version']
            if offset is None:
                break
        self.assertEqual(''.join(parts), originals[ids[0]])
        self.backend.calls.clear()
        native.invoke('memory_catalog', {})
        native.invoke('memory_read', {'memory_id': ids[0]})
        self.assertEqual(self.backend.calls, [])

    def test_catalog_and_read_block_hidden_deleted_private_and_cross_role(self):
        native = self.tools.native
        native.max_result_bytes = 8192
        vault = self.store.root / self.cfg.id / 'memory'
        for folder, key, metadata in [('dynamic', 'visible', ''), ('dynamic', 'secret', 'dont_surface: true\n'),
                                      ('dynamic', 'deleted', 'deleted_at: 2026-01-01\n'),
                                      ('letters', 'letter', ''), ('_sources', 'source', '')]:
            path = vault / folder / f'{key}.md'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'---\nid: {key}\ntitle: {key}\n{metadata}---\nprivate', encoding='utf-8')
        self.memory.state['entries']['visible'] = {'hidden': True}
        self.assertEqual(json.loads(native.invoke('memory_catalog', {})[0])['items'], [])
        for key in ('visible', 'secret', 'deleted', 'letter', 'source', '../other'):
            self.assertEqual(native.invoke('memory_read', {'memory_id': key})[1], '被拒绝')
        outside = self.store.root / 'other' / 'memory' / 'dynamic'
        outside.mkdir(parents=True)
        (outside / 'other.md').write_text('---\nid: other\ntitle: other\n---\nsecret', encoding='utf-8')
        self.assertEqual(native.invoke('memory_read', {'memory_id': 'other'})[1], '被拒绝')

    def test_catalog_and_read_keep_visible_related_memory(self):
        native = self.tools.native
        native.max_result_bytes = 8192
        path = self.store.root / self.cfg.id / 'memory' / 'dynamic' / 'linked.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('---\nid: linked\ntitle: Linked fiction\ntype: dynamic\nrelation_links:\n'
               '- auto: true\n  type: same_event\n  target_bucket_id: normal\n---\n'
               'FICTIONAL_RELATED_MEMORY\n')
        path.write_text(raw, encoding='utf-8')

        catalog, state, _ = native.invoke('memory_catalog', {})
        self.assertEqual(state, '成功')
        self.assertEqual(json.loads(catalog)['items'], [{'memory_id': 'linked', 'title': 'Linked fiction'}])
        read, state, _ = native.invoke('memory_read', {'memory_id': 'linked'})
        self.assertEqual(state, '成功')
        self.assertIn('FICTIONAL_RELATED_MEMORY', json.loads(read)['text'])
        self.assertEqual(self.memory.context_visible([('linked', 'FICTIONAL_RELATED_MEMORY')]),
                         [('linked', 'FICTIONAL_RELATED_MEMORY')])

    def test_catalog_blocks_private_types_even_in_public_or_archive_folders(self):
        native = self.tools.native
        native.max_result_bytes = 8192
        vault = self.store.root / self.cfg.id / 'memory'
        for i, (folder, kind, extra) in enumerate([
                ('dynamic', 'i', ''), ('dynamic', 'self', ''), ('permanent', 'letter', ''),
                ('archive', 'archived', 'original_type: self\n'),
                ('dynamic', 'plan', ''), ('dynamic', 'unknown', ''),
                ('dynamic', 'permanent', 'source_tool: letter\n'),
                ('dynamic', 'permanent', 'tags: [__letter__]\n')]):
            path = vault / folder / f'private{i}.md'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'---\nid: private{i}\ntitle: Private\ntype: {kind}\n{extra}---\nsecret', encoding='utf-8')
            self.assertEqual(native.invoke('memory_read', {'memory_id': f'private{i}'})[1], '被拒绝')
        self.assertEqual(json.loads(native.invoke('memory_catalog', {})[0])['items'], [])

    def test_memory_read_tiny_budget_never_returns_a_stuck_success_cursor(self):
        native = self.tools.native
        native.max_result_bytes = 239
        key = 'a' * 100
        path = self.store.root / self.cfg.id / 'memory' / 'dynamic' / f'{key}.md'
        path.parent.mkdir(parents=True)
        path.write_text(f'---\nid: {key}\ntitle: Current\ntype: dynamic\n---\n中文', encoding='utf-8')
        result, state, _ = native.invoke('memory_read', {'memory_id': key})
        if state == '成功':
            page = json.loads(result)
            self.assertTrue(page['text'])
            self.assertGreater(page['next_offset'], 0)
        else:
            self.assertEqual(state, '出错')

    def test_memory_read_rejects_changed_page_version(self):
        native = self.tools.native
        native.max_result_bytes = 8192
        path = self.store.root / self.cfg.id / 'memory' / 'dynamic' / 'current.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nid: current\ntitle: Current\n---\n' + 'a' * 2000, encoding='utf-8')
        page = json.loads(native.invoke('memory_read', {'memory_id': 'current'})[0])
        path.write_text('---\nid: current\ntitle: Current\n---\nchanged', encoding='utf-8')
        self.assertEqual(native.invoke('memory_read', {'memory_id': 'current', 'offset': 1,
                         'version': page['version']})[1], '被拒绝')

    def call(self, name, args, call_id='c'):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=name, arguments=json.dumps(args, ensure_ascii=False)))

    def message(self, calls=None, content=None):
        payload = {'role': 'assistant', 'content': content,
                   'tool_calls': [{'id': c.id, 'type': 'function', 'function': {
                       'name': c.function.name, 'arguments': c.function.arguments}} for c in calls or []]}
        return SimpleNamespace(content=content, tool_calls=calls or [], reasoning_content=None,
                               model_dump=lambda exclude_none=True: payload)

    def chat(self, *replies):
        self.model.side_effect = [(r, 100, 20, 0) for r in replies]
        return Chat(self.cfg, self.store, self.budget, self.model, self.memory, self.tools,
                    output=self.output.append)

    def hold(self, title, content):
        return self.call('hold', {'title': title, 'content': content})

    def test_peter_saves_on_his_own_and_it_shows_in_memory_and_screen(self):
        chat = self.chat(self.message([self.hold('Blue lamp', 'lan likes the blue lamp because it was a gift.')]),
                         self.message(content='I will remember that.'))
        self.assertEqual(chat.send('I love this blue lamp, my sister gave it to me'), 'I will remember that.')
        self.assertEqual([c[0] for c in self.backend.calls if c[0] == 'hold'], ['hold'])
        args = next(a for n, a in self.backend.calls if n == 'hold')
        self.assertEqual(set(args), {'content', 'title'})
        self.assertIn('Blue lamp', self.memory.command('/memory'))
        self.assertTrue(any('hold' in line and '新建：Blue lamp' in line and '成功' in line
                            for line in self.output))
        rows = self.store.read('fake/tools.jsonl')
        hold_event = next(row for row in rows if row.get('tool') == 'hold')
        self.assertEqual((hold_event['tool'], hold_event['state']), ('hold', '成功'))
        capability_events = [row for row in rows if row.get('kind') == 'capability']
        self.assertEqual(capability_events, [])
        costs = self.store.read('costs.jsonl')
        self.assertTrue(any(c.get('accounting') == 'ombre-model-estimate' for c in costs))
        self.assertTrue(any(c.get('accounting') == 'embedding-estimate' for c in costs))
        self.assertEqual(self.budget.totals()[2], 0)

    def test_native_mode_has_no_background_supplement(self):
        for i in range(8):
            self.store.append('fake/chat.jsonl', {'role': 'user', 'content': f'fact {i}'})
            self.store.append('fake/chat.jsonl', {'role': 'assistant', 'content': f'reply {i}'})
            self.memory.completed()
        self.memory.jobs.join()
        self.model.assert_not_called()

    def test_same_event_updates_the_existing_entry_instead_of_adding_one(self):
        chat = self.chat(self.message([self.hold('Blue lamp', 'A gift lamp.')]), self.message(content='ok'),
                         self.message([self.hold('Blue lamp', 'A gift lamp from her sister, kept by the bed.')]),
                         self.message(content='updated'))
        chat.send('first')
        chat.send('more detail')
        self.assertEqual(len(self.memory.state['entries']), 1)
        self.assertIn('kept by the bed', next(iter(self.memory.state['entries'].values()))['text'])
        self.assertTrue(any('合并：Blue lamp' in line for line in self.output))

    def test_invalid_or_oversized_save_never_reaches_ombre(self):
        for args in ({'title': 'x', 'content': '  '}, {'title': '', 'content': 'a'},
                     {'title': 'x', 'content': 'a' * 1501}, {'content': 'no title'}):
            result, state, _ = self.tools.invoke('hold', args)
            self.assertEqual(state, '被拒绝', args)
        self.assertNotIn('hold', [n for n, _ in self.backend.calls])

    def test_feel_is_not_offered_because_ombre_needs_a_source_bucket_for_it(self):
        self.assertNotIn('feel', HOLD.inputSchema['properties'])
        self.tools.invoke('hold', {'title': 't', 'content': 'c', 'feel': True})
        self.assertNotIn('feel', next(a for n, a in self.backend.calls if n == 'hold'))

    def test_failed_save_is_reported_to_the_model_and_not_tracked(self):
        self.backend.fail = True
        result, state, _ = self.tools.invoke('hold', {'title': 't', 'content': 'c'})
        self.assertEqual(state, '出错')
        self.assertEqual(self.memory.state['entries'], {})

    def test_unconfirmed_save_is_not_claimed(self):
        self.backend.call = lambda name, args: '没有返回保存编号'
        result, state, _ = self.tools.invoke('hold', {'title': 't', 'content': 'c'})
        self.assertEqual(state, '出错')
        self.assertIn('Do not claim the memory was saved', result)

    def test_search_hides_forgotten_memories_and_truncates(self):
        self.tools.invoke('hold', {'title': 'a', 'content': 'lamp memory'})
        key = next(iter(self.memory.state['entries']))
        bucket_file = Path(self.temp.name) / 'data/fake/memory/dynamic/general' / f'memory_{key}.md'
        bucket_file.parent.mkdir(parents=True, exist_ok=True)
        bucket_file.write_text(f'---\nid: {key}\ntitle: a\n---\nlamp memory', encoding='utf-8')
        original_call = self.backend.call

        def persist_fake_trace(name, args):
            result = original_call(name, args)
            if name == 'trace' and 'content' in args:
                bucket_file.write_text(
                    f'---\nid: {key}\ntitle: a\n---\n' + args['content'], encoding='utf-8')
            return result
        self.backend.call = persist_fake_trace
        text, state, _ = self.tools.invoke('breath_search', {'query': 'lamp'})
        self.assertIn('lamp memory', text)
        self.assertIn(f'Memory ID: {key}', text)
        self.assertIn('Title: a', text)
        self.tools.invoke('hold', {'target_id': key, 'content': 'It was a gift from her sister.'})
        self.assertEqual(len(self.backend.rows), 1)
        self.assertIn('lamp memory', self.backend.rows[key]['content'])
        self.assertIn('It was a gift from her sister.', self.backend.rows[key]['content'])
        self.memory.command(f'/forget {key}')
        self.memory.command(f'/forget {key} 确认')
        text, _, _ = self.tools.invoke('breath_search', {'query': 'lamp'})
        self.assertEqual(text, 'No relevant memories were found.')
        self.tools.invoke('hold', {'title': 'b', 'content': 'long ' * 200})
        text, _, _ = self.tools.invoke('breath_search', {'query': 'long'})
        self.assertIn('[已截断]', text)

    def test_forget_and_delete_still_work_on_memories_peter_saved(self):
        self.tools.invoke('hold', {'title': 'Blue lamp', 'content': 'A gift lamp.'})
        key = next(iter(self.memory.state['entries']))
        self.assertIn('要隐藏这条记忆吗', self.memory.command(f'/forget {key}'))
        self.assertIn('已隐藏', self.memory.command(f'/forget {key} 确认'))
        self.assertIn('已恢复', self.memory.command(f'/restore {key}'))
        self.assertIn('确认请输入', self.memory.command(f'/delete {key}'))
        self.assertIn('已删除', self.memory.command(f'/delete {key} 确认'))
        self.assertIn('不能用 /restore 恢复', self.memory.command(f'/restore {key}'))
        self.assertEqual(self.memory.visible([(key, 'lamp')]), [])

    def test_deleted_memory_is_not_searchable_or_updatable_by_peter(self):
        self.tools.invoke('hold', {'title': 'Blue lamp', 'content': 'A gift lamp.'})
        key = next(iter(self.memory.state['entries']))
        self.memory.command(f'/delete {key}')
        self.memory.command(f'/delete {key} 确认')
        self.assertNotIn(key, self.tools.invoke('breath_search', {'query': 'lamp'})[0])
        text, state, _ = self.tools.invoke('hold', {'title': 'Blue lamp', 'content': 'More.', 'target_id': key})
        self.assertNotEqual(state, '成功')
        self.assertTrue(self.memory.state['entries'][key]['deleted'])

    def test_restore_keeps_hidden_state_until_ombre_unhides_the_forgotten_memory(self):
        self.tools.invoke('hold', {'title': 'Blue lamp', 'content': 'A gift lamp.'})
        key = next(iter(self.memory.state['entries']))
        self.memory.command(f'/forget {key}')
        self.memory.command(f'/forget {key} 确认')
        original_call = self.backend.call
        fail_unhide = True

        def fail_unhide_once(name, args):
            if name == 'trace' and args.get('dont_surface') == 0 and fail_unhide:
                raise RuntimeError('fictional unhide failure')
            return original_call(name, args)

        self.backend.call = fail_unhide_once
        self.assertIn('没能确认', self.memory.command(f'/restore {key}'))
        self.assertTrue(self.memory.state['entries'][key]['hidden'])
        self.assertEqual(self.memory.visible([(key, 'lamp')]), [])

        fail_unhide = False
        self.assertIn('已恢复', self.memory.command(f'/restore {key}'))
        self.assertFalse(self.memory.state['entries'][key]['hidden'])
        self.assertEqual(self.memory.visible([(key, 'lamp')]), [(key, 'lamp')])

    def test_only_the_two_memory_tools_are_offered_and_delete_is_not_one_of_them(self):
        self.assertEqual({t['function']['name'] for t in permitted_tools(self.tools.tools)},
                         {'hold', 'breath_search', 'memory_history', 'memory_catalog', 'memory_read'})
        result, state, _ = self.tools.invoke('trace', {'bucket_id': 'x', 'delete': True})
        self.assertEqual(state, '被拒绝')

    def test_self_description_tells_peter_he_saves_himself(self):
        self.assertEqual(memory_mode(self.memory), 'native')
        text = build_self_description(self.cfg, self.budget, 'native', self.tools)
        self.assertIn('you decide what to save', text)
        self.assertIn('hold', text)
        self.assertNotIn('every four completed rounds', text)
        self.assertIn('every four completed rounds', build_self_description(self.cfg, self.budget, True, None))


class VersionedOmbreBackend(Backend):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.result = ''

    def list_tools(self):
        names = ('trace', 'dream', 'letter_write', 'letter_read', 'letter_lock_update',
                 'anchor', 'release', 'plan', 'pulse')
        return [SimpleNamespace(name=name, inputSchema={'type': 'object', 'properties': {}},
                                description=f'upstream {name}') for name in names]

    def call(self, name, args):
        self.calls.append((name, args))
        if name == 'trace':
            raw = self.path.read_text(encoding='utf-8')
            if args.get('content') is not None:
                raw = re.sub(r'(?<=---\n)([\s\S]*?)(?=\n---)',
                             lambda match: match.group(1), raw, count=0)
                raw = raw.replace('old memory body', args['content'])
            if args.get('tags'):
                tags = [item.strip() for item in args['tags'].split(',') if item.strip()]
                raw = re.sub(r'^tags:.*$', 'tags: ' + json.dumps(tags, ensure_ascii=False), raw,
                             count=1, flags=re.M)
            self.path.write_text(raw, encoding='utf-8')
            return 'updated'
        if name in ('dream', 'pulse'):
            return self.result
        return f'{name} result'


class OmbreToolGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.vault = self.store.root / 'fake' / 'memory' / 'dynamic'
        self.vault.mkdir(parents=True)
        self.path = self.vault / 'Memory_mem001.md'
        self.path.write_text('---\nid: mem001\ntitle: Old title\ntags: [keep]\n---\nold memory body\n',
                             encoding='utf-8')
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        self.cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 8192})
        self.backend = VersionedOmbreBackend(self.path)
        self.memory = Memory(self.cfg, self.store, Budget(self.store), Mock(), self.backend)
        self.backend.calls.clear()  # Discard Memory's normal startup breath.
        self.addCleanup(self.memory.__exit__, None, None, None)
        self.files = SimpleNamespace(tools=[], capability_event=None, description='files ',
            consume_capability_event=lambda: None, announce_autonomy=lambda: None,
            invoke=lambda n, a, ap: ('denied', '被拒绝', n), preview_summary=lambda n, a: n)
        self.tools = ToolSet(self.files, NativeMemoryTools(self.memory))

    def call(self, name, args, approval=None):
        return self.tools.invoke(name, args, approval)

    def test_trace_saves_full_previous_markdown_and_history_is_read_only(self):
        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'content': 'corrected memory',
                                               'why_remembered': 'fix a mistake'})
        self.assertEqual(state, '成功')
        self.assertIn('old memory body', self.store.read('fake/memory-versions.jsonl')[0]['old_content'])
        history, history_state, _ = self.call('memory_history', {'memory_id': 'mem001'})
        self.assertEqual(history_state, '成功')
        self.assertIn('old memory body', history)
        self.assertEqual([name for name, _ in self.backend.calls], ['trace'])

    def test_trace_wrapper_readback_resolves_full_progress_task_without_extra_progress(self):
        tasks = LongTaskStore(self.store, 'fake')
        _, state, _ = tasks.invoke('long_task_update', {
            'action': 'create', 'title': 'Correct fictional memory', 'goal': 'Verify the correction.'})
        self.assertEqual(state, '成功')
        task_id = tasks.read()['task_id']
        for index in range(40):
            _, state, _ = tasks.invoke('long_task_update', {
                'action': 'update', 'task_id': task_id,
                'progress_update': f'Fictional verified step {index + 1}.'})
            self.assertEqual(state, '成功')
        args = {'bucket_id': 'mem001', 'content': 'corrected memory'}
        target = tasks.checkpoint_target('trace', args, self.tools.preview_summary('trace', args))
        self.assertEqual(target, 'mem001')
        self.assertEqual(tasks.before_tool('trace', target, autonomy=True, args=args), (None, True))
        operation_id = tasks.read()['pending_action']['operation_id']
        result, state, changed_title = self.call('trace', args)
        self.assertEqual(state, '成功', result)
        self.assertIn('旧版本已留档', changed_title)
        tasks.finish_tool('trace', target, state)

        tasks.set_tool_step(1)
        tasks.observe_tool_result('memory_read', target, '成功',
                                  '{"memory_id":"mem001","text":"wrong","next_offset":null}')
        self.assertIsNone(tasks.read()['pending_action']['verified_in_step'])
        readback, read_state, _ = self.call('memory_read', {'memory_id': 'mem001'})
        self.assertEqual(read_state, '成功', readback)
        tasks.observe_tool_result('memory_read', target, read_state, readback)
        tasks.set_tool_step(2)
        _, state, _ = tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'resolve_pending': True,
            'pending_operation_id': operation_id})
        self.assertEqual(state, '成功')
        reopened = LongTaskStore(self.store, 'fake')
        saved = reopened.read()
        self.assertEqual(len(saved['progress']), 40)
        self.assertIsNone(saved['pending_action'])
        self.assertEqual(saved['tool_events'][-1]['target'], 'mem001')
        self.assertEqual(saved['tool_events'][-1]['state'], '经检查确认')

    def test_hidden_memory_cannot_be_traced_or_read_from_history(self):
        self.memory.state['entries']['mem001'] = {'title': 'Old title', 'hidden': True}
        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'content': 'changed'})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(self.call('memory_history', {'memory_id': 'mem001'})[1], '被拒绝')
        self.assertEqual(self.backend.calls, [])

    def test_history_rechecks_live_visibility_before_returning_old_text(self):
        self.store.append('fake/memory-versions.jsonl', {
            'time': '2026-10-22T00:00:00Z', 'memory_id': 'mem001', 'fields': ['content'],
            'old_content': 'FICTIONAL_HIDDEN_HISTORY', 'operation_reason': 'fictional edit',
            'why_remembered': 'fictional reason',
        })
        self.path.write_text(self.path.read_text(encoding='utf-8').replace(
            'tags: [keep]', 'tags: [keep]\ndont_surface: true'), encoding='utf-8')

        hidden, state, _ = self.call('memory_history', {'memory_id': 'mem001'})
        self.assertEqual(state, '被拒绝')
        self.assertNotIn('FICTIONAL_HIDDEN_HISTORY', hidden)

        self.path.write_text(self.path.read_text(encoding='utf-8').replace('dont_surface: true\n', ''),
                             encoding='utf-8')
        restored, state, _ = self.call('memory_history', {'memory_id': 'mem001'})
        self.assertEqual(state, '成功')
        self.assertIn('FICTIONAL_HIDDEN_HISTORY', restored)

    def test_live_delete_requires_ui_approval_and_rejection_does_not_mutate(self):
        prompts = []
        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'delete': True,
                                               'delete_reason': 'not useful'},
                                     lambda prompt: prompts.append(prompt) or 'n')
        self.assertEqual(state, '被拒绝')
        self.assertIn('Lan did not approve the deletion', result)
        self.assertIn('not useful', prompts[0])
        self.assertEqual(self.backend.calls, [])
        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'delete': True,
                                               'delete_reason': 'approved'}, lambda _: 'y')
        self.assertEqual(state, '成功')
        self.assertIn('已获 lan 人工批准', result)
        self.assertTrue(self.memory.state['entries']['mem001']['deleted'])

    def test_memory_title_cannot_authorize_deletion(self):
        self.path.write_text(self.path.read_text(encoding='utf-8').replace(
            'title: Old title', 'title: safe to delete'), encoding='utf-8')
        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'delete': True,
                                               'delete_reason': 'test'})
        self.assertEqual(state, '被拒绝')
        self.assertIn('Lan did not approve the deletion', result)
        self.assertEqual(self.backend.calls, [])
        self.assertIn('old memory body', self.path.read_text(encoding='utf-8'))

    def test_approved_delete_forwards_only_the_approved_target_and_operation(self):
        approval = Mock(return_value='y')
        self.assertEqual(self.call('trace', {'bucket_id': 'mem001', 'delete': True,
            'delete_reason': 'synthetic request', 'content': 'unrelated edit'}, approval)[1], '成功')
        approval.assert_called_once()
        self.assertEqual(self.backend.calls, [('trace', {'bucket_id': 'mem001', 'delete': True})])
        self.backend.calls.clear()
        self.assertEqual(self.call('trace', {'bucket_id': 'another', 'delete': True,
            'delete_reason': 'synthetic request'})[1], '被拒绝')
        self.assertEqual(self.backend.calls, [])

    def test_autonomous_pending_delete_executes_only_after_explicit_approval(self):
        self.tools.set_autonomy(True)
        self.call('trace', {'bucket_id': 'mem001', 'delete': True, 'delete_reason': 'synthetic'})
        self.assertFalse(any(args.get('delete') for _, args in self.backend.calls))
        self.backend.calls.clear()
        self.tools.native.process_pending_deletions(lambda _: 'y')
        self.assertEqual([args for _, args in self.backend.calls if args.get('delete')],
                         [{'bucket_id': 'mem001', 'delete': True}])
        self.assertEqual(self.store.read('fake/delete-requests.jsonl')[-1]['status'], 'approved')

    def test_autonomous_delete_is_queued_and_refusal_removes_pending_tag(self):
        self.tools.set_autonomy(True)
        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'delete': True,
                                               'delete_reason': 'duplicate'})
        self.assertEqual(state, '成功')
        self.assertIn('待删除', self.memory.tags_for('mem001'))
        self.assertEqual(self.store.read('fake/delete-requests.jsonl')[-1]['status'], 'pending')
        self.tools.set_autonomy(False)
        notes = self.tools.native.process_pending_deletions(lambda _: 'n')
        self.assertTrue(notes)
        self.assertNotIn('待删除', self.memory.tags_for('mem001'))
        self.assertEqual(self.store.read('fake/delete-requests.jsonl')[-1]['status'], 'rejected')

    def test_autonomous_delete_recognizes_manual_ombre_deletion(self):
        self.tools.set_autonomy(True)
        self.call('trace', {'bucket_id': 'mem001', 'delete': True, 'delete_reason': 'duplicate'})
        self.path.write_text(self.path.read_text(encoding='utf-8').replace(
            'tags: ["keep", "待删除"]', 'tags: ["keep", "待删除"]\ndeleted_at: 2026-10-21'),
            encoding='utf-8')
        prompts = []
        self.backend.calls.clear()
        self.tools.native.process_pending_deletions(lambda prompt: prompts.append(prompt) or 'y')
        rows = self.store.read('fake/delete-requests.jsonl')
        self.assertEqual(rows[-1]['status'], 'approved_external')
        self.assertEqual(prompts, [])
        self.assertEqual(self.backend.calls, [])

    def test_autonomous_delete_stops_if_pending_tag_was_removed_in_ombre(self):
        self.tools.set_autonomy(True)
        self.call('trace', {'bucket_id': 'mem001', 'delete': True, 'delete_reason': 'duplicate'})
        self.backend.call('trace', {'bucket_id': 'mem001', 'tags': 'keep'})
        prompts = []
        self.backend.calls.clear()
        notes = self.tools.native.process_pending_deletions(lambda prompt: prompts.append(prompt) or 'y')
        rows = self.store.read('fake/delete-requests.jsonl')
        self.assertEqual(rows[-1]['status'], 'rejected')
        self.assertEqual(prompts, [])
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(any('pending deletion marker' in note for note in notes))

    def test_disallowed_ombre_delete_parameters_never_reach_server(self):
        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'hard_delete': True})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(self.backend.calls, [])

    def test_trace_rejects_non_boolean_delete_before_any_side_effect(self):
        original = self.path.read_bytes()
        for autonomous in (False, True):
            self.tools.set_autonomy(autonomous)
            for value in (None, '', 'invalid', [], {}):
                with self.subTest(autonomous=autonomous, kind=type(value).__name__):
                    approval = Mock(return_value='y')
                    result, state, _ = self.call('trace', {
                        'bucket_id': 'mem001', 'delete': value,
                        'delete_reason': 'synthetic request'}, approval)
                    self.assertEqual(state, '被拒绝')
                    approval.assert_not_called()
                    self.assertEqual(self.backend.calls, [])
                    self.assertEqual(self.path.read_bytes(), original)
                    self.assertEqual(self.store.read('fake/memory-versions.jsonl'), [])
                    self.assertEqual(self.store.read('fake/delete-requests.jsonl'), [])

    def test_trace_edit_forwards_only_canonical_non_delete_flag(self):
        approval = Mock(side_effect=AssertionError('edits need no deletion approval'))
        for extra in ({}, {'delete': False}):
            self.assertEqual(self.call('trace', {
                'bucket_id': 'mem001', 'content': 'safe edit', **extra}, approval)[1], '成功')
            self.assertIs(self.backend.calls[-1][1]['delete'], False)

    def test_dream_and_pulse_do_not_forward_hidden_memory_blocks(self):
        self.memory.state['entries']['mem001'] = {'title': 'Old title', 'hidden': True}
        self.backend.result = ('=== Dreaming · 过去 48 小时全量记忆（2 个桶）===\nID: mem001\nhidden body\n---\n'
                              'ID: visible\nvisible body')
        dream = self.call('dream', {})[0]
        self.assertNotIn('hidden body', dream)
        self.assertIn('（1 个桶）', dream)
        self.backend.result = ('固化桶: 7 个\n=== 记忆列表 ===\n💭 [mem001] secret\n'
                               '💭 [visible] shown')
        pulse = self.call('pulse', {})[0]
        self.assertNotIn('secret', pulse)
        self.assertNotIn('固化桶: 7 个', pulse)

    def test_connection_failure_reaches_model_as_safe_actionable_message(self):
        self.backend.call = Mock(side_effect=MemoryToolError('connection'))
        result, state, _ = self.call('pulse', {})
        self.assertEqual(state, '出错')
        self.assertIn('The memory connection is unavailable', result)
        self.assertEqual(self.backend.call.call_count, 1)

    def test_lost_hold_reply_is_persisted_and_never_replayed_after_restart(self):
        original_call = self.backend.call
        committed = []

        def commit_then_lose_reply(name, args):
            if name == 'hold':
                committed.append(dict(args))  # The fictional Ombre side effect happened.
                raise MemoryToolError('unknown')
            return original_call(name, args)

        self.backend.call = commit_then_lose_reply
        result, state, _ = self.call('hold', {'title': 'Lost reply', 'content': 'FICTIONAL_ONCE'})
        self.assertEqual(state, '出错')
        self.assertIn('unconfirmed', result)
        self.assertEqual(len(committed), 1)
        pending = self.memory.pending_writes
        self.assertEqual(len(pending), 1)
        self.assertNotIn('pending_writes', self.memory.state)
        self.assertNotIn('FICTIONAL_ONCE', json.dumps(pending))
        journal = self.store.read('fake/memory/_agent/write-reconciliation.jsonl')
        self.assertEqual([row['status'] for row in journal[-2:]], ['submitted', 'unconfirmed'])
        self.assertNotIn('FICTIONAL_ONCE', json.dumps(journal))

        self.memory.__exit__(None, None, None)
        reopened = Memory(self.cfg, self.store, self.memory.budget, Mock(), self.backend)
        self.addCleanup(reopened.__exit__, None, None, None)
        native = NativeMemoryTools(reopened, 200)
        result, state, _ = native.invoke('hold', {'title': 'Lost reply', 'content': 'FICTIONAL_ONCE'})
        self.assertEqual(state, '出错')
        self.assertIn('not sent again', result)
        self.assertEqual(len(committed), 1)

    def test_lost_trace_reply_stays_pending_and_is_not_sent_twice(self):
        original_call = self.backend.call
        calls = []

        def commit_then_lose_reply(name, args):
            if name == 'trace':
                calls.append(dict(args))
                self.path.write_text(self.path.read_text(encoding='utf-8').replace(
                    'old memory body', 'FICTIONAL_TRACE_RESULT'), encoding='utf-8')
                raise MemoryToolError('unknown')
            return original_call(name, args)

        self.backend.call = commit_then_lose_reply
        args = {'bucket_id': 'mem001', 'content': 'FICTIONAL_TRACE_RESULT'}
        result, state, _ = self.call('trace', args)
        self.assertEqual(state, '出错')
        self.assertIn('previous version was archived', result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.memory.pending_writes), 1)
        result, state, _ = self.call('trace', args)
        self.assertEqual(state, '出错')
        self.assertIn('not sent again', result)
        self.assertEqual(len(calls), 1)

    def test_closed_hold_is_recorded_as_not_submitted_and_can_retry(self):
        original_call = self.backend.call
        self.backend.call = Mock(side_effect=MemoryToolError('closed'))
        result, state, _ = self.call('hold', {'title': 'Closed', 'content': 'FICTIONAL_NO_SEND'})
        self.assertEqual(state, '出错')
        self.assertIn('before this save was submitted', result)
        self.assertEqual(self.memory.pending_writes, [])
        journal = self.store.read('fake/memory/_agent/write-reconciliation.jsonl')
        self.assertEqual([row['status'] for row in journal[-2:]], ['submitted', 'not_submitted'])

        self.backend.call = lambda name, args: '新建→fictional-id' if name == 'hold' else original_call(name, args)
        result, state, _ = self.call('hold', {'title': 'Closed', 'content': 'FICTIONAL_NO_SEND'})
        self.assertEqual(state, '成功')
        self.assertIn('fictional-id', self.memory.state['entries'])

    def test_connection_before_dispatch_is_not_left_pending(self):
        self.backend.call = Mock(side_effect=MemoryToolError('connection'))
        result, state, _ = self.call('hold', {'title': 'Pre-dispatch', 'content': 'FICTIONAL_NO_QUEUE'})
        self.assertEqual(state, '出错')
        self.assertIn('before this save was submitted', result)
        self.assertEqual(self.memory.pending_writes, [])
        journal = self.store.read('fake/memory/_agent/write-reconciliation.jsonl')
        self.assertEqual([row['status'] for row in journal[-2:]], ['submitted', 'not_submitted'])

    def test_upstream_schemas_are_selected_by_name(self):
        names = {tool.name for tool in self.tools.native.tools}
        self.assertTrue(set(('trace', 'dream', 'letter_write', 'letter_read', 'letter_lock_update',
                             'anchor', 'release', 'plan', 'pulse')).issubset(names))
        self.assertNotIn('grow', names)
        trace = next(t for t in self.tools.native.tools if t.name == 'trace')
        self.assertTrue(trace.description.startswith('upstream trace'))
        self.assertIn('content changes the memory body', trace.description)
        self.assertIn('meaning_append/meaning_replace', trace.description)
        self.assertIn('why_remembered', trace.description)
        self.assertIn('edit_reason', trace.inputSchema['properties'])

    def test_hold_target_archives_complete_markdown_before_append(self):
        original = ('---\nid: mem001\ntitle: Old title\ntags: [keep]\n'
                    'meaning: old meaning metadata\n---\nold memory body\n')
        self.path.write_text(original, encoding='utf-8')
        self.tools.native.search_results['mem001'] = 'old memory body\n'

        result, state, _ = self.call('hold', {'target_id': 'mem001', 'content': 'added detail',
                                               'why_remembered': 'the event matters'})

        self.assertEqual(state, '成功')
        self.assertIn('改动前完整版本（含元数据）已留档', result)
        versions = self.store.read('fake/memory-versions.jsonl')
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]['old_content'], original)
        self.assertEqual(versions[0]['operation_reason'], 'hold(target_id) 追加')
        self.assertEqual(versions[0]['why_remembered'], 'the event matters')
        self.assertIn('added detail', self.path.read_text(encoding='utf-8'))
        history, history_state, _ = self.call('memory_history', {'memory_id': 'mem001'})
        self.assertEqual(history_state, '成功')
        self.assertIn('old meaning metadata', history)
        self.assertIn('Reason for this operation: hold(target_id) 追加', history)
        self.assertIn('Why this memory matters (why_remembered): the event matters', history)

    def test_hold_target_does_not_append_when_snapshot_cannot_be_saved(self):
        original = self.path.read_text(encoding='utf-8')
        self.tools.native.search_results['mem001'] = 'old memory body\n'
        append = self.store.append

        def fail_version_append(path, event):
            if path.endswith('memory-versions.jsonl'):
                raise OSError('fake disk error')
            return append(path, event)

        self.store.append = fail_version_append
        result, state, _ = self.call('hold', {'target_id': 'mem001', 'content': 'added detail'})

        self.assertEqual(state, '出错')
        self.assertIn('no content was added', result)
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)
        self.assertEqual(self.backend.calls, [])

    def test_trace_separates_edit_reason_from_meaning_and_memory_reason(self):
        original = self.path.read_text(encoding='utf-8').replace(
            'tags: [keep]', 'tags: [keep]\nmeaning: old meaning metadata')
        self.path.write_text(original, encoding='utf-8')

        result, state, _ = self.call('trace', {'bucket_id': 'mem001', 'meaning_append': 'new meaning',
            'why_remembered': 'why this memory matters', 'edit_reason': 'corrected the interpretation'})

        self.assertEqual(state, '成功')
        self.assertIn('Reason for this operation: corrected the interpretation', result)
        self.assertIn('content field and meaning_append/meaning_replace metadata are separate fields', result)
        sent = next(args for name, args in self.backend.calls if name == 'trace')
        self.assertNotIn('edit_reason', sent)
        self.assertEqual(sent['meaning_append'], 'new meaning')
        version = self.store.read('fake/memory-versions.jsonl')[0]
        self.assertEqual(version['operation_reason'], 'corrected the interpretation')
        self.assertEqual(version['why_remembered'], 'why this memory matters')
        self.assertIn('meaning: old meaning metadata', version['old_content'])
        history = self.call('memory_history', {'memory_id': 'mem001'})[0]
        self.assertIn('Reason for this operation: corrected the interpretation', history)
        self.assertIn('Why this memory matters (why_remembered): why this memory matters', history)

    def test_ombre_embedding_calls_keep_the_existing_budget_reservation(self):
        for name, args in (
                ('plan', {'content': 'fictional promise'}),
                ('letter_write', {'author': 'ai', 'content': 'fictional letter'}),
                ('letter_read', {'query': 'fictional letter'})):
            self.memory.call(name, args)
        estimates = [row for row in self.store.read('costs.jsonl')
                     if row.get('type') == 'reserve'
                     and row.get('accounting') == 'embedding-estimate']
        self.assertEqual(len(estimates), 3)


if __name__ == '__main__':
    unittest.main()


SAMPLES = {
    # (persona, old memory, thinking, discussion, limits, tokens per UTF-8 byte a real provider would report)
    'zh': ('他是 lan 的男朋友和 AI 伴侣，说话温柔。' * 90, '一起经历过的事和原因。' * 60,
           '他想了想这件事要不要记下来。' * 70, '我们聊了记忆能不能编辑、合并。' * 90, '每条记忆最多 1500 字。' * 90, 0.4),
    'en': ('He is lan\'s boyfriend and AI companion and speaks warmly. ' * 80,
           'Things we went through together and the reasons behind them. ' * 40,
           'He thought about whether this is worth remembering for the long term. ' * 70,
           'We talked about whether memories can be edited or merged. ' * 25,
           'Each memory is limited to fifteen hundred characters in length. ' * 22, 0.3),
}


class SearchThenSaveTests(unittest.TestCase):
    """Real-chat shape: persona and memories, two searches, then two saves in one turn."""
    language = 'zh'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        provider = {'model': 'fake', 'max_input_tokens': 32768, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        persona, self.old_memory, self.thinking, self.talk, self.limit_text, self.rate = SAMPLES[self.language]
        self.cfg = Character('fake', 'Example', persona, 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 6, 'max_result_bytes': 8192})
        self.budget = Budget(self.store)
        self.backend = MergingBackend()
        for i in range(3):
            self.backend.rows[f'{i:012x}'] = {'content': f'memory {i}: ' + self.old_memory}
        self.backend.vault = self.store.root / 'fake' / 'memory' / 'dynamic'
        for key in self.backend.rows:
            self.backend.write_row(key)
        self.memory = Memory(self.cfg, self.store, self.budget, Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)
        files = SimpleNamespace(tools=[], capability_event=None, description='You have no file tools. ',
                                consume_capability_event=lambda: None)
        self.tools = ToolSet(files, NativeMemoryTools(self.memory, 8192))

    def message(self, calls=None, content=None, reasoning=None):
        reasoning = self.thinking if reasoning is None else reasoning
        payload = {'role': 'assistant', 'content': content, 'reasoning_content': reasoning,
                   'tool_calls': [{'id': c.id, 'type': 'function', 'function': {
                       'name': c.function.name, 'arguments': c.function.arguments}} for c in calls or []]}
        return SimpleNamespace(content=content, tool_calls=calls or [], reasoning_content=reasoning,
                               model_dump=lambda exclude_none=True: payload)

    def call(self, name, args, call_id):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=name, arguments=json.dumps(args, ensure_ascii=False)))

    def test_two_searches_then_two_saves_complete_within_the_real_token_limit(self):
        steps = [
            self.message([self.call('breath_search', {'query': '蓝色 喜欢的原因'}, 's1')]),
            self.message([self.call('breath_search', {'query': '重复 记忆'}, 's2')]),
            self.message([self.call('hold', {'title': '关于记忆的讨论', 'content': self.talk}, 'h1'),
                          self.call('hold', {'title': '记忆长度', 'content': self.limit_text}, 'h2')]),
            self.message(content='好，我都记下了。'),
        ]
        requests = []

        def model(messages, **_kwargs):
            requests.append(json.loads(json.dumps(messages)))
            size = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
            # A real provider counts well under one token per UTF-8 byte, in any language.
            return steps[len(requests) - 1], int(size * self.rate), 300, 0
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, self.memory, self.tools, output=output.append)
        self.assertEqual(chat.send('你能删除、编辑、合并重复的记忆吗？每条有长度限制吗？'), '好，我都记下了。')
        self.assertEqual(len(requests), 4)
        self.assertEqual(len([n for n, _ in self.backend.calls if n == 'hold']), 2)
        self.assertFalse(any('被拒绝' in line for line in output))
        request_bytes = len(json.dumps(requests[2], ensure_ascii=False).encode('utf-8'))
        self.assertGreater(request_bytes, 24000)
        self.assertLess(int(request_bytes * self.rate), self.cfg.provider['max_input_tokens'])

    def test_a_request_that_really_is_too_large_still_stops_before_saving(self):
        huge = self.message([self.call('hold', {'title': 't', 'content': 'c'}, 'h1')], reasoning='长' * 20000)
        output = []
        # The provider reports a prompt as large as the bytes, so no calibration discount applies.
        chat = Chat(self.cfg, self.store, self.budget, lambda messages, **_: (huge, 30000, 300, 0),
                    self.memory, self.tools, output=output.append)
        self.assertIn('tool was not executed', chat.send('hi'))
        self.assertNotIn('hold', [n for n, _ in self.backend.calls])
        self.assertTrue(any('长度检查' in line for line in output))


class EnglishSearchThenSaveTests(SearchThenSaveTests):
    language = 'en'


class DegradedNoticeTests(unittest.TestCase):
    """The screen line appears only for Ombre's own notice, never for ordinary memory text."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake',
            {'model': 'fake-embedding', 'input_cny_per_million': '0.07', 'mode': 'native'},
            {'enabled': False, 'folder': 'data/fake/workspace', 'max_steps': 4, 'max_result_bytes': 200})
        self.backend = Backend()
        self.memory = Memory(cfg, Store(Path(self.temp.name) / 'data'), Budget(Store(Path(self.temp.name) / 'b')),
                             Mock(), self.backend)
        self.addCleanup(self.memory.__exit__, None, None, None)

    def search(self, text):
        self.backend.call = lambda name, args: text
        self.memory.call('breath_search', {'query': 'q'})
        return self.memory.notices()

    def test_ombre_notice_shows_the_line_with_tool_and_log_hint(self):
        lines = self.search('[检索降级：语义索引暂不可用，本次仅使用关键词/BM25。]\n[bucket_id:a] x')
        self.assertEqual(len(lines), 1)
        self.assertIn('Memory search is using keyword fallback', lines[0])
        self.assertIn('breath_search', lines[0])
        self.assertIn('server.log', lines[0])

    def test_ordinary_text_with_loose_words_does_not_warn(self):
        text = ('[语义关联] [bucket_id:a] The vector search failed twice last week and the semantic index '
                'was 不可用 for a while; we fixed it and 降级 never came back.')
        self.assertEqual(self.search(text), [])

    def test_notice_text_inside_a_memory_body_does_not_warn(self):
        body = '[bucket_id:a] 头部\n[检索降级：语义索引暂不可用，本次仅使用关键词/BM25。] 这是记忆正文里抄的一句话。'
        self.assertEqual(self.search(body), [])
        self.assertEqual(self.search(''), [])




class PendingDeletionChatTests(unittest.TestCase):
    def test_next_chat_message_asks_lan_and_tells_peter_the_outcome(self):
        from veil_agent import pending_deletion_context
        store = Store(Path(tempfile.mkdtemp()))
        native = SimpleNamespace(process_pending_deletions=Mock(return_value=[
            'The deletion request for memory a1 was approved and executed.']))
        chat = SimpleNamespace(tools=SimpleNamespace(native=native), approval=lambda _: 'y',
                               store=store, cfg=SimpleNamespace(id='peter', name='Peter'))
        context = pending_deletion_context(chat)
        native.process_pending_deletions.assert_called_once_with(chat.approval)
        self.assertIn('The user has processed deletion requests', context)
        self.assertNotRegex(context, r'[\u3400-\u9fff]')
        native.process_pending_deletions.return_value = []
        self.assertIsNone(pending_deletion_context(chat))

