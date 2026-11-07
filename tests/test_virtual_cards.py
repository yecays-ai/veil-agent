import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil_agent import Chat, Budget, Store, UserError, load_character, send_message_schema
from virtual_cards import VirtualCardError, normalize_amount, normalize_gift, normalize_icon, normalize_note


class VirtualCardRuleTests(unittest.TestCase):
    def test_amount_note_icon_and_gift_limits(self):
        self.assertEqual(normalize_amount('12.5'), '12.50')
        self.assertEqual(normalize_amount(8), '8.00')
        self.assertEqual(normalize_amount('1000000000000'), '1000000000000.00')
        for bad in (0, -1, '0.00', '1e2', '1000000000000.01', True, None, 'abc'):
            with self.assertRaises(VirtualCardError):
                normalize_amount(bad)
        self.assertEqual(normalize_note(None), '')
        self.assertEqual(normalize_note('  hello '), 'hello')
        with self.assertRaises(VirtualCardError):
            normalize_note('x' * 241)
        self.assertEqual(normalize_icon(None), '🎁')
        self.assertEqual(normalize_icon(''), '🎁')
        with self.assertRaises(VirtualCardError):
            normalize_icon('花' * 25)
        with self.assertRaises(VirtualCardError):
            normalize_gift('   ')
        with self.assertRaises(VirtualCardError):
            normalize_gift('礼' * 501)


class VirtualCardChatTests(unittest.TestCase):
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
        self.outputs = []
        self.chat = Chat(self.cfg, self.store, self.budget, self.model, output=self.outputs.append)

    def rows(self):
        return self.store.read('test/chat.jsonl')

    def test_schema_offers_cards_and_card_action_with_messaging_tools(self):
        properties = send_message_schema()['function']['parameters']['properties']
        self.assertIn('transfer', properties)
        self.assertIn('gift', properties)
        self.assertEqual(send_message_schema()['function']['parameters']['required'], [])
        names = [schema['function']['name'] for schema in self.chat.tool_schemas()]
        self.assertNotIn('card_action', names)
        enabled = Chat(replace(self.cfg, tools={'enabled': True, 'max_steps': 2}), self.store, self.budget,
                       self.model, tools=SimpleNamespace(
                           tools=[], description='Tools. ', startup_notice=None,
                           capability_event=None, capability_event_model=None, capability_record=None,
                           consume_capability_event=Mock()))
        names = [schema['function']['name'] for schema in enabled.tool_schemas()]
        self.assertIn('send_message', names)
        self.assertIn('card_action', names)

    def test_user_and_peter_can_send_and_only_the_recipient_can_act(self):
        row, created = self.chat.create_virtual_card(
            'user', 'transfer', {'amount': '12.5', 'note': 'tea'}, client_action_id='clientid1')
        self.assertTrue(created)
        card = row['virtual_card']
        self.assertEqual(card['sender'], 'user')
        self.assertEqual(card['recipient'], 'assistant')
        self.assertEqual(card['status'], 'pending')
        self.assertEqual(card['amount'], '12.50')
        self.assertIn(f'[VEIL虚拟转账 {row["message_id"]}]', row['content'])
        self.assertIn('Lan', row['content'])
        self.assertIn('Test', row['content'])
        again, created_again = self.chat.create_virtual_card(
            'user', 'transfer', {'amount': '99', 'note': 'other'}, client_action_id='clientid1')
        self.assertFalse(created_again)
        self.assertEqual(again['message_id'], row['message_id'])
        self.assertEqual(len(self.rows()), 1)

        context = self.chat.virtual_card_context()
        self.assertIn(row['message_id'], context)
        self.assertIn('receive', context)

        with self.assertRaises(VirtualCardError):
            self.chat.act_on_virtual_card('user', row['message_id'], 'receive')
        with self.assertRaises(VirtualCardError):
            self.chat.act_on_virtual_card('assistant', row['message_id'], 'return')
        self.assertEqual(self.rows()[0]['virtual_card']['status'], 'pending')

        updated, event, content = self.chat.act_on_virtual_card('assistant', row['message_id'], 'receive')
        self.assertEqual(updated['status'], 'received')
        self.assertIn('已接收', content)
        self.assertEqual(event['virtual_card_event']['action'], 'receive')
        self.assertEqual(event['virtual_card_event']['actor'], 'assistant')
        self.assertIn('[VEIL转账结果', event['content'])
        self.assertIn('return', self.chat.virtual_card_context())

        with self.assertRaises(VirtualCardError):
            self.chat.act_on_virtual_card('user', row['message_id'], 'return')
        returned, return_event, _ = self.chat.act_on_virtual_card('assistant', row['message_id'], 'return')
        self.assertEqual(returned['status'], 'returned')
        self.assertEqual(return_event['virtual_card_event']['action'], 'return')
        with self.assertRaises(VirtualCardError):
            self.chat.act_on_virtual_card('assistant', row['message_id'], 'return')
        self.assertIsNone(self.chat.virtual_card_context())

        result, state, _ = self.chat.card_action(json.dumps({'message_id': row['message_id'], 'action': 'receive'}))
        self.assertEqual(state, '被拒绝')
        self.assertIn('Nothing was changed', result)

    def test_peter_sends_a_card_and_lan_is_the_only_one_who_can_answer(self):
        rejected, state, _ = self.chat.send_message(json.dumps({
            'transfer': {'amount': 3, 'note': 'no'}, 'gift': {'content': 'no'},
        }), 'request-both')
        self.assertEqual(state, '被拒绝')
        self.assertEqual(self.rows(), [])

        rejected, state, _ = self.chat.send_message(json.dumps({
            'gift': {'icon': '🌷', 'content': '一枝'}, 'sticker': 'pack/name',
        }), 'request-sticker')
        self.assertEqual(state, '被拒绝')
        self.assertEqual(self.rows(), [])

        result, state, _ = self.chat.send_message(json.dumps({
            'gift': {'icon': '🌷', 'content': '一枝'}, 'text': '给你', 'done': True,
        }), 'request-gift')
        self.assertEqual(state, '成功')
        self.assertTrue(self.chat.turn_done)
        gift, words = self.rows()
        self.assertEqual(gift['role'], 'assistant')
        self.assertEqual(gift['virtual_card']['sender'], 'assistant')
        self.assertEqual(gift['virtual_card']['recipient'], 'user')
        self.assertEqual(gift['virtual_card']['icon'], '🌷')
        self.assertEqual(words['content'], '给你')
        self.assertTrue(self.chat.turn_done)
        self.assertEqual(self.outputs[0].virtual_card['type'], 'gift')
        self.assertIsNone(self.chat.virtual_card_context())

        with self.assertRaises(VirtualCardError):
            self.chat.act_on_virtual_card('assistant', gift['message_id'], 'accept')
        updated, event, _ = self.chat.act_on_virtual_card('user', gift['message_id'], 'accept')
        self.assertEqual(updated['status'], 'accepted')
        self.assertEqual(event['role'], 'user')
        self.assertIn('已收下', self.rows()[0]['content'])

        with self.assertRaises(UserError) as caught:
            self.chat.edit_message(gift['message_id'], '改掉这张卡片')
        self.assertIn('虚拟卡片不能编辑', str(caught.exception))
        with self.assertRaises(UserError):
            self.chat.edit_message(event['message_id'], '改掉结果')

    def test_card_only_send_can_end_the_turn_and_a_declined_gift_is_final(self):
        result, state, _ = self.chat.send_message(json.dumps({
            'transfer': {'amount': 1, 'note': ''}, 'done': True,
        }), 'request-card-only')
        self.assertEqual(state, '成功')
        self.assertIn('turn ends', result)
        self.assertTrue(self.chat.turn_done)
        row = self.rows()[0]
        self.assertEqual(row['virtual_card']['note'], '')
        self.assertEqual(len(self.rows()), 1)

        fresh, _ = self.chat.create_virtual_card('user', 'gift', {'content': '先撤回'})
        self.chat.recall_message(fresh['message_id'])
        stored = next(item for item in self.rows() if item['message_id'] == fresh['message_id'])
        self.assertIsNone(stored['virtual_card'])
        self.assertEqual(stored['content'], '[Message recalled]')

        gift, _ = self.chat.create_virtual_card('user', 'gift', {'content': '星星'})
        declined, event, _ = self.chat.act_on_virtual_card('assistant', gift['message_id'], 'decline')
        self.assertEqual(declined['status'], 'declined')
        self.assertEqual(event['virtual_card_event']['type'], 'gift')
        with self.assertRaises(VirtualCardError):
            self.chat.act_on_virtual_card('assistant', gift['message_id'], 'accept')


if __name__ == '__main__':
    unittest.main()
