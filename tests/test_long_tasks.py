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

    def test_unconfirmed_side_effect_survives_restart_and_blocks_repeat(self):
        self.create()
        self.assertIsNone(self.tasks.begin_tool('append_file', 'lab/part-one.md'))
        self.tasks.finish_tool('append_file', 'lab/part-one.md', '出错')

        reopened = LongTaskStore(self.store, 'peter')
        pending = reopened.read()['pending_action']
        self.assertEqual(pending['target'], 'lab/part-one.md')
        self.assertIn('结果仍待核实', reopened.begin_tool('append_file', 'lab/part-one.md'))
        blocked, checkpoint = reopened.before_tool('append_file', 'lab/part-one.md', autonomy=False)
        self.assertIn('结果仍待核实', blocked)
        self.assertFalse(checkpoint)
        self.assertEqual(reopened.before_tool('append_file', 'lab/unrelated.md', autonomy=False),
                         (None, False))

        reopened.set_tool_step(1)
        reopened.observe_tool_result('read_text_file', 'lab/part-one.md', '成功')
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': reopened.read()['task_id'], 'status': 'active',
            'resolve_pending': True,
            'progress_update': 'Read lab/part-one.md; the requested section is already present.'})
        self.assertEqual(state, '被拒绝')
        reopened.set_tool_step(2)
        _, state, _ = reopened.invoke('long_task_update', {
            'action': 'update', 'task_id': reopened.read()['task_id'], 'status': 'active',
            'resolve_pending': True,
            'progress_update': 'Read lab/part-one.md; the requested section is already present.'})
        self.assertEqual(state, '成功')
        self.assertIsNone(reopened.read()['pending_action'])
        self.assertEqual(reopened.read()['tool_events'][-1]['state'], '经检查确认')

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
                'type': 'object', 'properties': {'path': {'type': 'string'}}})],
            capability_event=None,
            preview_summary=lambda name, args: args['path'],
            invoke=lambda name, args, approval: ('Append completed.', '成功', args['path']))
        calls = [SimpleNamespace(id='call-one', function=SimpleNamespace(
            name='append_file', arguments=json.dumps({'path': 'lab/part-one.md'}))),]
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

    def test_completed_task_does_not_disable_other_autonomous_work(self):
        task_id = self.create()
        _, state, _ = self.tasks.invoke('long_task_update', {
            'action': 'update', 'task_id': task_id, 'status': 'completed',
            'progress_update': 'All three verified sections are in the lab.'})
        self.assertEqual(state, '成功')
        self.assertEqual(self.tasks.before_tool('append_file', 'lab/new-note.md', autonomy=True),
                         (None, False))


if __name__ == '__main__':
    unittest.main()
