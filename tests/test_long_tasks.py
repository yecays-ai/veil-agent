import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from long_tasks import LongTaskStore
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
        self.assertIn('结果仍待核实', reopened.before_tool(
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
        self.tasks.finish_tool('append_file', 'lab/notes.txt', '出错')
        reopened = LongTaskStore(self.store, 'peter')
        reopened.set_tool_step(1)
        reopened.observe_tool_result('read_text_file', 'lab/notes.txt', '成功',
                                     target.read_text(encoding='utf-8'))
        self.assertEqual(reopened.read()['pending_action']['verified_in_step'], 1)
        reopened.observe_tool_result('read_text_file', 'lab/notes.txt', '成功', 'Existing\n')
        self.assertEqual(reopened.read()['pending_action']['verified_in_step'], 1)

    def test_pending_effect_prevents_completion_and_paused_tasks_stop_side_effects(self):
        task_id = self.create()
        self.assertIsNone(self.tasks.begin_tool('write_file', 'lab/part-one.md'))
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'completed'})
        self.assertEqual(state, '被拒绝')
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'paused'})
        self.assertEqual(state, '成功')
        self.assertIn('结果仍待核实', self.tasks.begin_tool('write_file', 'lab/part-two.md'))

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
        self.assertIn('结果仍待核实', chat.long_tasks.begin_tool('append_file', 'lab/part-one.md'))
        self.assertEqual(saved['tool_events'][-1]['tool'], 'append_file')
        self.assertEqual(saved['tool_events'][-1]['state'], '成功')
        self.assertEqual(saved['tool_events'][-1]['target'], 'lab/part-one.md')

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
