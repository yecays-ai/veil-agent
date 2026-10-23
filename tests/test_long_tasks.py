import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from long_tasks import LongTaskStore
from tools_agent import FilesystemTools
from veil_agent import Budget, Chat, Store, UserError, load_character


class LongTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.tasks = LongTaskStore(self.store, 'peter')

    def create(self):
        result, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'create', 'title': 'Three-part study', 'goal': 'Produce three verified sections.'})
        self.assertEqual(state, '成功', result)
        return self.tasks.read()['task_id']

    def test_create_updates_and_refuses_to_replace_unfinished_task(self):
        task_id = self.create()
        result, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'create', 'title': 'Replacement', 'goal': 'New goal'})
        self.assertEqual(state, '被拒绝')
        self.assertIn(task_id, result)
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'progress_update': 'Part one is in lab/part-one.md.',
            'next_step': 'Write part two.'})
        self.assertEqual(state, '成功')
        saved = self.tasks.read()
        self.assertEqual(saved['progress'][0]['text'], 'Part one is in lab/part-one.md.')
        self.assertEqual(saved['next_step'], 'Write part two.')

    def test_checkpoint_verified_signal_requires_persisted_readback_resolution(self):
        task_id = self.create()
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'progress_update': 'Ordinary progress only.'})
        self.assertEqual(state, '成功')
        self.assertFalse(self.tasks.take_checkpoint_verified())

        self.assertEqual(self.tasks.before_tool('write_file', 'fixture.txt', True,
            {'content': 'FICTIONAL_EXPECTED'}), (None, True))
        self.tasks.finish_tool('write_file', 'fixture.txt', '成功', 'written')
        self.tasks.set_tool_step(1)
        self.tasks.observe_tool_result('read_text_file', 'fixture.txt', '成功', 'FICTIONAL_EXPECTED')
        operation_id = self.tasks.read()['pending_action']['operation_id']
        self.tasks.set_tool_step(2)
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'resolve_pending': True,
            'pending_operation_id': operation_id, 'progress_update': 'Readback matched exactly.'})
        self.assertEqual(state, '成功')
        self.assertTrue(self.tasks.take_checkpoint_verified())
        self.assertFalse(self.tasks.take_checkpoint_verified())

    def test_tasks_are_separated_by_role(self):
        self.create()
        ghost = LongTaskStore(self.store, 'ghost')
        self.assertIsNone(ghost.read())
        _, state, _ = ghost.invoke('long_task_update', {
            'action': 'create', 'title': 'Ghost task', 'goal': 'Separate role task.'})
        self.assertEqual(state, '成功')
        self.assertEqual(self.tasks.read()['title'], 'Three-part study')
        self.assertEqual(ghost.read()['title'], 'Ghost task')

    def test_atomic_write_failure_keeps_previous_valid_snapshot(self):
        self.create()
        previous = self.tasks.read()
        with patch('long_tasks.os.replace', side_effect=OSError('simulated interruption')):
            with self.assertRaises(UserError):
                self.tasks._update({'action': 'update', 'task_id': previous['task_id'],
                                    'progress_update': 'This update cannot be committed.'})
        self.assertEqual(self.tasks.read(), previous)

    def test_nonfile_effect_can_be_verified_by_exact_target_readback(self):
        task_id = self.create()
        directory = Path(self.temp.name) / 'lab' / 'new-folder'
        self.assertEqual(self.tasks.before_tool(
            'create_directory', 'lab/new-folder', autonomy=True, args={}), (None, True))
        operation_id = self.tasks.read()['pending_action']['operation_id']
        directory.mkdir(parents=True)
        self.tasks.finish_tool('create_directory', 'lab/new-folder', '成功')

        self.tasks.set_tool_step(1)
        self.tasks.observe_tool_result('get_file_info', 'lab/new-folder-copy', '成功',
                                       '{"type":"directory"}')
        self.assertIsNone(self.tasks.read()['pending_action']['verified_in_step'])
        self.tasks.observe_tool_result('get_file_info', 'lab/new-folder', '成功',
                                       '{"type":"directory"}')
        self.assertEqual(self.tasks.read()['pending_action']['verified_in_step'], 1)
        self.tasks.set_tool_step(2)
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'resolve_pending': True,
            'pending_operation_id': operation_id, 'progress_update': 'The folder exists.'})
        self.assertEqual(state, '成功')
        self.assertIsNone(self.tasks.read()['pending_action'])

    def test_legacy_nonfile_pending_remains_recoverable_with_stable_id(self):
        task_id = self.create()
        self.assertEqual(self.tasks.before_tool(
            'create_directory', 'lab/archive', autonomy=True, args={}), (None, True))
        directory = Path(self.temp.name) / 'lab' / 'archive'
        directory.mkdir(parents=True)
        self.tasks.finish_tool('create_directory', 'lab/archive', '成功')
        saved = self.tasks.read()
        for key in ('operation_id', 'normalized_target', 'expected_effect'):
            saved['pending_action'].pop(key, None)
        self.tasks._save(saved)

        reopened = LongTaskStore(self.store, 'peter')
        pending = json.loads(reopened.status_text())['pending_action']
        operation_id = pending['operation_id']
        reopened.set_tool_step(1)
        reopened.observe_tool_result('get_file_info', 'lab/archive', '成功',
                                     '{"type":"directory"}')
        reopened.set_tool_step(2)
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'resolve_pending': True,
            'pending_operation_id': operation_id, 'progress_update': 'The directory exists.'})
        self.assertEqual(state, '成功')
        self.assertIsNone(reopened.read()['pending_action'])

    def test_pending_file_write_requires_exact_path_and_actual_contents_after_restart(self):
        self.create()
        task_id = self.tasks.read()['task_id']
        lab = Path(self.temp.name) / 'lab'
        lab.mkdir()
        target = lab / 'test-a.txt'
        requested = 'Fictional verified content.'
        self.assertEqual(self.tasks.before_tool(
            'write_file', 'lab/test-a.txt', autonomy=True, args={'content': requested}), (None, True))
        operation_id = self.tasks.read()['pending_action']['operation_id']
        target.write_text(requested, encoding='utf-8')
        with self.assertRaises(UserError):
            self.tasks.finish_tool('write_file', 'lab/test-a-copy.txt', '成功')
        self.assertEqual(self.tasks.read()['pending_action']['operation_id'], operation_id)
        self.tasks.finish_tool('write_file', 'lab/test-a.txt', '成功')

        reopened = LongTaskStore(self.store, 'peter')
        reopened.begin_tool_run()
        pending = reopened.read()['pending_action']
        self.assertEqual(pending['operation_id'], operation_id)
        self.assertEqual(pending['target'], 'lab/test-a.txt')
        self.assertIn('is still unverified', reopened.before_tool(
            'write_file', 'lab/test-a.txt', autonomy=False)[0])

        reopened.set_tool_step(1)
        reopened.observe_tool_result('read_text_file', 'lab/test-a-copy.txt', '成功', target.read_text())
        reopened.observe_tool_result('read_multiple_files', 'lab/test-a.txt', '成功', target.read_text())
        reopened.observe_tool_result('read_text_file', 'lab/test-a.txt', '成功', 'Wrong contents.')
        self.assertIsNone(reopened.read()['pending_action']['verified_in_step'])

        reopened.observe_tool_result('read_text_file', 'lab/test-a.txt', '成功', target.read_text(encoding='utf-8'))
        self.assertEqual(reopened.read()['pending_action']['verified_in_step'], 1)
        self.assertEqual(reopened.read()['pending_action']['verified_by'], 'read_text_file')
        reopened.set_tool_step(2)
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'active',
            'resolve_pending': True, 'pending_operation_id': 'wrong-id',
            'progress_update': 'The stored file has the requested content.'})
        self.assertEqual(state, '被拒绝')
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'active',
            'resolve_pending': True, 'pending_operation_id': operation_id,
            'progress_update': 'The stored file has the requested content.'})
        self.assertEqual(state, '成功')
        self.assertIsNone(reopened.read()['pending_action'])
        event = reopened.read()['tool_events'][-1]
        self.assertEqual(event['state'], '经检查确认')
        self.assertIn(operation_id, event['evidence'])

    def test_pending_lab_move_requires_destination_digest_after_restart(self):
        self.create()
        task_id = self.tasks.read()['task_id']
        content = b'Fictional archived file, complete.\r\n'
        digest = hashlib.sha256(content).hexdigest()
        destination = 'lab/archive/play/v1.html'
        self.assertEqual(self.tasks.before_tool(
            'move_lab_file', destination, autonomy=True,
            args={'source': 'lab/play/v1.html', 'destination': destination}), (None, True))
        operation_id = self.tasks.read()['pending_action']['operation_id']
        self.tasks.finish_tool('move_lab_file', destination, '成功',
                               f'File moved intact ({len(content)} bytes, SHA-256 {digest}).')

        reopened = LongTaskStore(self.store, 'peter')
        reopened.set_tool_step(1)
        reopened.observe_tool_result('read_text_file', destination, '成功', 'different')
        self.assertIsNone(reopened.read()['pending_action']['verified_in_step'])
        reopened.observe_tool_result('read_text_file', destination, '成功', content.decode())
        self.assertEqual(reopened.read()['pending_action']['verified_in_step'], 1)
        reopened.set_tool_step(2)
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'resolve_pending': True,
            'pending_operation_id': operation_id, 'progress_update': 'Archived file readback matched.'})
        self.assertEqual(state, '成功')
        self.assertIsNone(reopened.read()['pending_action'])

        self.assertEqual(reopened.before_tool(
            'move_lab_file', destination, autonomy=True,
            args={'source': 'lab/play/v1.html', 'destination': destination}), (None, True))
        reopened.set_tool_step(3)
        reopened.observe_tool_result('read_text_file', destination, '成功', content.decode())
        self.assertIsNone(reopened.read()['pending_action']['verified_in_step'])

    def test_large_lab_move_can_be_verified_with_bounded_digest_result(self):
        self.create()
        task_id = self.tasks.read()['task_id']
        content = bytes(range(256)) * 128
        digest = hashlib.sha256(content).hexdigest()
        destination = 'lab/archive/play/large.bin'
        self.assertEqual(self.tasks.before_tool(
            'move_lab_file', destination, autonomy=True,
            args={'source': 'lab/play/large.bin', 'destination': destination}), (None, True))
        operation_id = self.tasks.read()['pending_action']['operation_id']
        self.tasks.finish_tool('move_lab_file', destination, '成功',
                               f'File moved intact ({len(content)} bytes, SHA-256 {digest}).')
        self.tasks.set_tool_step(1)
        self.tasks.observe_tool_result('lab_file_digest', destination, '成功', json.dumps({
            'path': destination, 'byte_length': len(content), 'sha256': '0' * 64}))
        self.assertIsNone(self.tasks.read()['pending_action']['verified_in_step'])
        self.tasks.observe_tool_result('lab_file_digest', destination, '成功', json.dumps({
            'path': destination, 'byte_length': len(content), 'sha256': digest}))
        self.assertEqual(self.tasks.read()['pending_action']['verified_in_step'], 1)
        self.tasks.set_tool_step(2)
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'resolve_pending': True,
            'pending_operation_id': operation_id,
            'progress_update': 'Large archived file digest matched.'})
        self.assertEqual(state, '成功')

    def test_append_verification_checks_the_written_suffix(self):
        self.create()
        lab = Path(self.temp.name) / 'lab'
        lab.mkdir()
        target = lab / 'notes.txt'
        target.write_text('Existing\n', encoding='utf-8')
        chunk = 'Appended section\n'
        self.assertEqual(self.tasks.before_tool(
            'append_file', 'lab/notes.txt', autonomy=True, args={'content': chunk}), (None, True))
        target.write_text(target.read_text(encoding='utf-8') + chunk, encoding='utf-8')
        self.tasks.finish_tool('append_file', 'lab/notes.txt', '成功')
        reopened = LongTaskStore(self.store, 'peter')
        reopened.set_tool_step(1)
        reopened.observe_tool_result('read_text_file', 'lab/notes.txt', '成功',
                                     target.read_text(encoding='utf-8'))
        self.assertEqual(reopened.read()['pending_action']['verified_in_step'], 1)
        reopened.observe_tool_result('read_text_file', 'lab/notes.txt', '成功', 'Existing\n')
        self.assertEqual(reopened.read()['pending_action']['verified_in_step'], 1)

    def test_ambiguous_append_with_preexisting_matching_suffix_stays_pending(self):
        self.create()
        lab = Path(self.temp.name) / 'lab'
        lab.mkdir()
        target = lab / 'notes.txt'
        chunk = 'Already present\n'
        target.write_text(chunk, encoding='utf-8')
        self.assertEqual(self.tasks.before_tool(
            'append_file', 'lab/notes.txt', autonomy=True, args={'content': chunk}), (None, True))
        self.tasks.finish_tool('append_file', 'lab/notes.txt', '出错')
        self.tasks.set_tool_step(1)
        self.tasks.observe_tool_result('read_text_file', 'lab/notes.txt', '成功',
                                       target.read_text(encoding='utf-8'))
        self.assertIsNone(self.tasks.read()['pending_action']['verified_in_step'])

    def test_pending_effect_prevents_completion_and_paused_tasks_stop_side_effects(self):
        task_id = self.create()
        self.assertIsNone(self.tasks.begin_tool('write_file', 'lab/part-one.md'))
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'completed'})
        self.assertEqual(state, '被拒绝')
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'paused'})
        self.assertEqual(state, '成功')
        self.assertIn('is still unverified', self.tasks.begin_tool('write_file', 'lab/part-two.md'))

    def test_status_tool_returns_only_current_role_state(self):
        self.create()
        text, state, _ = self.tasks.invoke('long_task_status', {})
        self.assertEqual(state, '成功')
        self.assertEqual(json.loads(text)['title'], 'Three-part study')
        self.assertIsNone(LongTaskStore(self.store, 'ghost').read())

    def test_autonomous_chat_checkpoints_side_effect_tool_around_execution(self):
        (self.store.root.parent / 'characters' / 'peter').mkdir(parents=True)
        (self.store.root.parent / 'persona').mkdir()
        (self.store.root.parent / 'persona' / 'peter.md').write_text('Fictional persona', encoding='utf-8')
        character = self.store.root.parent / 'characters' / 'peter' / 'character.toml'
        character.write_text('''name = "Peter"
provider = "fake"
persona = "persona/peter.md"
max_output_tokens = 80
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = true
max_steps = 3
max_output_tokens = 80
max_result_bytes = 4096
''', encoding='utf-8')
        cfg = load_character(self.store.root.parent, 'peter')
        fake_tools = SimpleNamespace(
            tools=[SimpleNamespace(name='append_file', description='append in lab', inputSchema={
                'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}})],
            capability_event=None,
            preview_summary=lambda name, args: args['path'],
            invoke=lambda name, args, approval: ('Append completed.', '成功', args['path']))
        calls = [SimpleNamespace(id='call-one', function=SimpleNamespace(
            name='append_file', arguments=json.dumps({'path': 'lab/part-one.md', 'content': 'Fictional first section.'}))),]
        class FakeMessage:
            def __init__(self, tool_calls, content):
                self.tool_calls, self.content = tool_calls, content

            def model_dump(self, exclude_none=True):
                value = {'role': 'assistant', 'tool_calls': [
                    {'id': call.id, 'type': 'function', 'function': {
                        'name': call.function.name, 'arguments': call.function.arguments}}
                    for call in self.tool_calls]}
                if self.content is not None:
                    value['content'] = self.content
                return value

        first = FakeMessage(calls, None)
        final = FakeMessage([], 'Saved the first part.')
        model = Mock(side_effect=[(first, 100, 10, 0), (final, 100, 10, 0)])
        budget = Budget(self.store)
        chat = Chat(cfg, self.store, budget, model, tools=fake_tools, output=lambda *_: None)
        self.create()
        request_id, _ = budget.reserve(cfg)

        result, _ = chat._tool_chat([{'role': 'user', 'content': 'Continue the task.'}],
                                    request_id, 0, history_start=0, autonomy=True)

        saved = chat.long_tasks.read()
        self.assertEqual(result, 'Saved the first part.')
        self.assertEqual(saved['pending_action']['state'], '成功，需核实')
        self.assertIn('is still unverified', chat.long_tasks.begin_tool('append_file', 'lab/part-one.md'))
        self.assertEqual(saved['tool_events'][-1]['tool'], 'append_file')
        self.assertEqual(saved['tool_events'][-1]['state'], '成功')
        self.assertEqual(saved['tool_events'][-1]['target'], 'lab/part-one.md')

    def test_autonomous_trace_checkpoints_the_memory_id_not_changed_display_title(self):
        (self.store.root.parent / 'characters' / 'peter').mkdir(parents=True)
        (self.store.root.parent / 'persona').mkdir()
        (self.store.root.parent / 'persona' / 'peter.md').write_text('Fictional persona', encoding='utf-8')
        character = self.store.root.parent / 'characters' / 'peter' / 'character.toml'
        character.write_text('''name = "Peter"
provider = "fake"
persona = "persona/peter.md"
max_output_tokens = 80
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = true
max_steps = 3
max_output_tokens = 80
max_result_bytes = 4096
''', encoding='utf-8')
        cfg = load_character(self.store.root.parent, 'peter')
        fake_tools = SimpleNamespace(
            tools=[SimpleNamespace(name='trace', description='edit memory', inputSchema={
                'type': 'object', 'properties': {'bucket_id': {'type': 'string'}, 'content': {'type': 'string'}}})],
            capability_event=None,
            preview_summary=lambda name, args: 'Fictional old title',
            invoke=lambda name, args, approval: ('Memory changed.', '成功', 'Fictional new title（旧版本已留档）'))
        calls = [SimpleNamespace(id='trace-one', function=SimpleNamespace(
            name='trace', arguments=json.dumps({'bucket_id': 'memory-001', 'content': 'Corrected fictional memory.'}))),]

        class FakeMessage:
            def __init__(self, tool_calls, content):
                self.tool_calls, self.content = tool_calls, content

            def model_dump(self, exclude_none=True):
                return {'role': 'assistant', 'content': self.content, 'tool_calls': [
                    {'id': call.id, 'type': 'function', 'function': {
                        'name': call.function.name, 'arguments': call.function.arguments}}
                    for call in self.tool_calls]}

        model = Mock(side_effect=[(FakeMessage(calls, None), 100, 10, 0),
                                  (FakeMessage([], 'Memory updated.'), 100, 10, 0)])
        budget = Budget(self.store)
        chat = Chat(cfg, self.store, budget, model, tools=fake_tools, output=lambda *_: None)
        self.create()
        request_id, _ = budget.reserve(cfg)

        result, _ = chat._tool_chat([{'role': 'user', 'content': 'Correct the memory.'}],
                                    request_id, 0, history_start=0, autonomy=True)

        saved = chat.long_tasks.read()
        self.assertEqual(result, 'Memory updated.')
        self.assertEqual(saved['pending_action']['target'], 'memory-001')
        self.assertEqual(saved['tool_events'][-1]['target'], 'memory-001')
        self.assertEqual(saved['pending_action']['state'], '成功，需核实')

    def test_completion_at_progress_limit_persists_evidence_and_allows_next_task(self):
        task_id = self.create()
        for index in range(40):
            _, state, _ = self.tasks.invoke('long_task_update', {
                'action': 'update', 'task_id': task_id,
                'progress_update': f'Fictional verified step {index + 1}.'})
            self.assertEqual(state, '成功')

        final_evidence = 'All 40 fictional steps are verified; final artifact is lab/final.md.'
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'completed',
            'progress_update': final_evidence})
        self.assertEqual(state, '成功')

        stored = json.loads(self.tasks.path.read_text(encoding='utf-8'))
        self.assertEqual(stored['status'], 'completed')
        self.assertEqual(len(stored['progress']), 40)
        self.assertEqual([row['text'] for row in stored['progress']],
                         [f'Fictional verified step {index + 1}.' for index in range(40)])
        self.assertEqual(stored['completion_evidence'], final_evidence)

        reopened = LongTaskStore(self.store, 'peter')
        self.assertEqual(reopened.read(), stored)
        status = json.loads(reopened.status_text())
        self.assertEqual(status['completion_evidence'], final_evidence)
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'completed',
            'progress_update': 'Replacement evidence must not overwrite the first result.'})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(reopened.read(), stored)

        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'create', 'title': 'Next fictional task', 'goal': 'Verify a separate result.'})
        self.assertEqual(state, '成功')
        self.assertEqual(reopened.read()['title'], 'Next fictional task')

    def test_verification_at_progress_limit_records_existing_readback_without_41st_progress(self):
        task_id = self.create()
        for index in range(40):
            _, state, _ = self.tasks.invoke('long_task_update', {
                'action': 'update', 'task_id': task_id,
                'progress_update': f'Fictional verified step {index + 1}.'})
            self.assertEqual(state, '成功')
        self.assertEqual(self.tasks.before_tool(
            'trace', 'memory-001', autonomy=True, args={'bucket_id': 'memory-001'}), (None, True))
        operation_id = self.tasks.read()['pending_action']['operation_id']
        self.tasks.finish_tool('trace', 'memory-001', '成功')
        reopened = LongTaskStore(self.store, 'peter')
        reopened.set_tool_step(1)
        reopened.observe_tool_result('memory_read', 'memory-001', '成功',
                                     '{"memory_id":"memory-001","text":"corrected"}')
        reopened.set_tool_step(2)
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'resolve_pending': True,
            'pending_operation_id': operation_id})
        self.assertEqual(state, '成功')
        saved = reopened.read()
        self.assertEqual(len(saved['progress']), 40)
        self.assertIsNone(saved['pending_action'])
        self.assertEqual(saved['tool_events'][-1]['state'], '经检查确认')

    def test_completion_save_failure_keeps_full_active_snapshot(self):
        task_id = self.create()
        for index in range(40):
            _, state, _ = self.tasks.invoke('long_task_update', {
                'action': 'update', 'task_id': task_id,
                'progress_update': f'Fictional verified step {index + 1}.'})
            self.assertEqual(state, '成功')

        before = self.tasks.path.read_bytes()
        previous = self.tasks.read()
        with patch('long_tasks.os.replace', side_effect=OSError('simulated interruption')):
            with self.assertRaises(UserError):
                self.tasks.invoke('long_task_update', {
                    'action': 'update', 'task_id': task_id, 'status': 'completed',
                    'progress_update': 'Fictional completion evidence.'})
        self.assertEqual(self.tasks.path.read_bytes(), before)
        self.assertEqual(self.tasks.read(), previous)
        self.assertEqual(self.tasks.read()['status'], 'active')
        self.assertEqual(self.tasks.read().get('completion_evidence', ''), '')

    def test_dispatch_recovers_real_file_effect_after_model_failure_at_progress_limit(self):
        """The fake model chooses calls; checkpointing and file I/O stay real."""
        root = self.store.root.parent
        (root / 'characters' / 'peter').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'peter.md').write_text('Fictional persona', encoding='utf-8')
        (root / 'characters' / 'peter' / 'character.toml').write_text('''name = "Peter"
provider = "fake"
persona = "persona/peter.md"
max_output_tokens = 80
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = true
max_steps = 6
max_output_tokens = 80
max_result_bytes = 4096
''', encoding='utf-8')
        cfg = load_character(root, 'peter')
        workspace = self.store.root / 'peter' / 'workspace'
        workspace.mkdir(parents=True)
        files = FilesystemTools.__new__(FilesystemTools)
        files.workspace, files.cfg, files.lab = workspace, cfg, None
        files.tools = [
            SimpleNamespace(name='write_file', description='Write a fictional file.', inputSchema={}),
            SimpleNamespace(name='read_text_file', description='Read a fictional file.', inputSchema={}),
        ]
        files.client = Mock()
        files.client.call.side_effect = lambda name, args: Path(args['path']).read_text(encoding='utf-8')
        files.capability_event = None

        def call(name, args, call_id):
            return SimpleNamespace(id=call_id, function=SimpleNamespace(
                name=name, arguments=json.dumps(args)))

        class Message:
            def __init__(self, calls=(), content=None):
                self.tool_calls, self.content = list(calls), content

            def model_dump(self, exclude_none=True):
                return {'role': 'assistant', 'content': self.content, 'tool_calls': [
                    {'id': item.id, 'type': 'function', 'function': {
                        'name': item.function.name, 'arguments': item.function.arguments}}
                    for item in self.tool_calls]}

        budget = Budget(self.store)
        first_model = Mock(side_effect=[
            (Message([call('write_file', {'path': 'checkpoint.txt', 'content': 'FICTIONAL_EFFECT'}, 'write')]), 100, 10, 0),
            RuntimeError('fictional provider disconnect'),
        ])
        chat = Chat(cfg, self.store, budget, first_model, tools=files, approval=lambda _prompt: 'y', output=lambda *_: None)
        task_id = chat.long_tasks.invoke('long_task_update', {
            'action': 'create', 'title': 'Fictional recovery', 'goal': 'Verify exactly once.'})[2]
        task_id = chat.long_tasks.read()['task_id']
        for index in range(40):
            self.assertEqual(chat.long_tasks.invoke('long_task_update', {
                'action': 'update', 'task_id': task_id,
                'progress_update': f'Fictional prior progress {index + 1}.'})[1], '成功')
        request_id, _ = budget.reserve(cfg)
        with self.assertRaises(Exception):
            chat._tool_chat([{'role': 'user', 'content': 'Run fictional task.'}], request_id, 0,
                            history_start=0, autonomy=True)
        self.assertEqual((workspace / 'checkpoint.txt').read_text(encoding='utf-8'), 'FICTIONAL_EFFECT')
        pending = chat.long_tasks.read()['pending_action']
        self.assertEqual(pending['state'], '成功，需核实')

        resumed = Chat(cfg, self.store, budget, None, tools=files, approval=lambda _prompt: 'y', output=lambda *_: None)
        readbacks = iter(['WRONG_FICTIONAL_EFFECT', 'FICTIONAL_EFFECT'])
        files.client.call.side_effect = lambda _name, _args: next(readbacks)
        def resume_model(*_args, **_kwargs):
            step = resume_model.step
            resume_model.step += 1
            operation_id = resumed.long_tasks.read()['pending_action']['operation_id'] if step in (1, 3) else None
            responses = [
                Message([call('read_text_file', {'path': 'checkpoint.txt'}, 'read')]),
                Message([call('long_task_update', {'action': 'update', 'task_id': task_id,
                    'resolve_pending': True, 'pending_operation_id': operation_id}, 'reject-bad-readback')]),
                Message([call('read_text_file', {'path': 'checkpoint.txt'}, 'read-again')]),
                Message([call('long_task_update', {'action': 'update', 'task_id': task_id,
                    'resolve_pending': True, 'pending_operation_id': operation_id}, 'resolve')]),
                Message([call('long_task_update', {'action': 'update', 'task_id': task_id, 'status': 'completed',
                    'progress_update': 'The existing fictional file was read back exactly.'}, 'complete')]),
                Message(content='Fictional task completed after verification.'),
            ]
            return responses[step], 100, 10, 0
        resume_model.step = 0
        resumed.model = resume_model
        request_id, _ = budget.reserve(cfg)
        reply, _ = resumed._tool_chat([{'role': 'user', 'content': 'Resume safely.'}], request_id, 0,
                                      history_start=0, autonomy=True)
        saved = resumed.long_tasks.read()
        self.assertEqual(reply, 'Fictional task completed after verification.')
        self.assertEqual(len(saved['progress']), 40)
        self.assertEqual(saved['completion_evidence'], 'The existing fictional file was read back exactly.')
        self.assertIsNone(saved['pending_action'])
        self.assertEqual(files.client.call.call_count, 2)
        reopened = LongTaskStore(self.store, 'peter')
        self.assertEqual(reopened.read(), saved)
        self.assertEqual(reopened.invoke('long_task_update', {
            'action': 'create', 'title': 'Next fictional task', 'goal': 'Not blocked by the completed one.'})[1], '成功')

    def test_completed_task_does_not_disable_other_autonomous_work(self):
        task_id = self.create()
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'completed',
            'progress_update': 'All three verified sections are in the lab.'})
        self.assertEqual(state, '成功')
        saved = self.tasks.read()
        self.assertEqual(saved['completion_evidence'], '')
        self.assertEqual(saved['progress'][-1]['text'], 'All three verified sections are in the lab.')
        self.assertEqual(self.tasks.before_tool('append_file', 'lab/new-note.md', autonomy=True),
                         (None, False))


if __name__ == '__main__':
    unittest.main()
