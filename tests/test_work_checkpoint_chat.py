import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from agent_types import now
from veil_agent import Budget, Chat, Store, load_character


def system_text(messages):
    return '\n'.join(item['content'] for item in messages
                     if item.get('role') == 'system' and isinstance(item.get('content'), str))


class WorkCheckpointChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'test').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (self.root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
''', encoding='utf-8')
        self.cfg = load_character(self.root, 'test')
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store)
        self.model = Mock(return_value=('A fictional reply', 100, 20, 10))
        self.chat = Chat(self.cfg, self.store, self.budget, self.model)
        self.chat.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.chat.session_path.write_text(json.dumps({'start': 0}), encoding='utf-8')
        self.chat.work_checkpoint._save({
            'id': 'w1', 'goal': 'build the page', 'goal_source': 'request', 'status': 'open',
            'confirmed': [{'tool': 'write_file', 'path': 'lab/a.html', 'state': '成功', 'time': now()}],
            'unconfirmed': [], 'failed': [], 'next_step': '',
            'injected_key': '', 'injected_dropped': 0, 'captured_at': now(), 'updated_at': now()})

    def test_the_checkpoint_is_offered_on_the_first_request_and_not_again(self):
        self.chat.send('first')
        self.assertIn('PROGRAM-RECORDED WORK CHECKPOINT', system_text(self.model.call_args.args[0]))
        self.chat.send('second')
        self.assertNotIn('PROGRAM-RECORDED WORK CHECKPOINT', system_text(self.model.call_args.args[0]))

    def test_a_new_conversation_gets_the_checkpoint_again(self):
        self.chat.send('first')
        self.chat.start_new_conversation()
        self.chat.send('fresh topic')
        self.assertIn('build the page', system_text(self.model.call_args.args[0]))

    def test_lan_can_switch_the_feature_off(self):
        chat = Chat(replace(self.cfg, context={'work_checkpoint': False}), self.store, self.budget, self.model)
        chat.session_path.parent.mkdir(parents=True, exist_ok=True)
        chat.session_path.write_text(json.dumps({'start': 0}), encoding='utf-8')
        chat.send('hello')
        self.assertNotIn('PROGRAM-RECORDED WORK CHECKPOINT', system_text(self.model.call_args.args[0]))

    def test_the_context_log_records_how_much_history_was_left_out(self):
        self.chat.send('first')
        rows = self.store.read('test/context-log.jsonl')
        self.assertTrue(rows)
        self.assertIn('history_total_rounds', rows[-1])
        self.assertIn('history_dropped_rounds', rows[-1])
        self.assertGreaterEqual(rows[-1]['history_total_rounds'], rows[-1]['history_rounds'])

    def test_checkpoint_context_is_protected_before_other_auxiliary_blocks(self):
        self.chat.send('first')
        messages = self.model.call_args.args[0]
        index = next(i for i, item in enumerate(messages)
                     if item.get('role') == 'system' and 'WORK CHECKPOINT' in str(item.get('content')))
        later = [i for i, item in enumerate(messages) if i > index and item.get('role') == 'system']
        self.assertTrue(later)  # the time/ledger block still follows it


if __name__ == '__main__':
    unittest.main()
