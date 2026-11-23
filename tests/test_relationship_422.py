"""Ticket 422 acceptance: the stable relationship base and natural association.

Everything here is fictional and local: a fake Ombre backend over invented memories, a fake chat
model that only answers, and temporary data directories. No network, no real memory, no paid call.
The assertions read the real assembled request (Chat.send -> Memory.context -> the model argument),
not a hand-built message list.
"""
import json
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import natural_association
import relationship_base
from memory_agent import Memory, vault_path
from test_recall_rotation import FictionalOmbre
from veil_agent import Budget, Chat, Store, load_character, estimate_request_tokens

CONFIRMED = 'We agreed that a hard day gets a quiet evening together, not a plan.'
GUESS = 'Peter thinks she prefers tea in the morning, but she never said so.'
STALE = 'Old rule: she wanted every message answered within five minutes.'


class ProfileFixture(unittest.TestCase):
    """A real Chat with a real Learned Context, a real Memory and a fake model."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'peter.md').write_text('Peter is a fictional test companion.',
                                                        encoding='utf-8')
        (self.root / 'characters' / 'peter').mkdir(parents=True)
        (self.root / 'characters' / 'peter' / 'character.toml').write_text('''
name = "Peter"
provider = "fake"
persona = "persona/peter.md"
history_rounds = 20
max_output_tokens = 1024

[providers.fake]
model = "fake-model"
base_url = "https://fake.local/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 32768
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"

[tools]
enabled = false

[profile]
learned = true
file = "learned-context.json"
max_bytes = 4000
''', encoding='utf-8')
        self.store = Store(self.root / 'data')
        self.rows = {}

    def build(self, limit=32768, recall=None):
        cfg = load_character(self.root, 'peter')
        cfg.provider['max_input_tokens'] = limit
        cfg.memory['recall'] = recall or {'cooldown_turns': 0}
        cfg.memory.update({'model': 'fake-embedding', 'input_cny_per_million': '0',
                           'mode': 'native', 'base_url': 'https://example.invalid/v1'})
        budget = Budget(self.store)
        self.model = Mock(return_value=(types.SimpleNamespace(content='Fictional reply', tool_calls=[]),
                                        100, 20, 0))
        chat = Chat(cfg, self.store, budget, self.model, output=[].append)
        backend = FictionalOmbre(vault_path(self.store.root, 'peter') / 'dynamic', self.rows,
                                 drift=list(getattr(self, 'drift', ())))
        memory = Memory(cfg, self.store, budget, self.model, backend)
        chat.memory = memory
        self.chat, self.memory, self.backend = chat, memory, backend
        return chat, memory

    def seed_relationship(self, chat):
        chat.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': CONFIRMED, 'status': 'confirmed'})
        chat.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': GUESS, 'status': 'uncertain'})
        chat.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': STALE, 'status': 'confirmed'})
        chat.profile._invoke_profile('profile_replace', {'section': 'relationship',
                                                         'old_text': STALE, 'new_text': ''})

    @staticmethod
    def joined(call):
        return '\n'.join(message.get('content') if isinstance(message.get('content'), str) else ''
                         for message in call.args[0])

    def send(self, chat, text, message_id='q'):
        chat.send(text, message_id=message_id)
        return self.model.call_args


class RelationshipBaseRequestTests(ProfileFixture):
    """A. the base enters the request even when nothing was recalled."""

    def test_a_plain_turn_with_no_recall_still_carries_the_current_base(self):
        chat, memory = self.build()
        self.seed_relationship(chat)
        call = self.send(chat, '今天下班有点累，回家路上买了点橘子。')
        joined = self.joined(call)
        self.assertIn('CURRENT BETWEEN YOU AND LAN', joined)
        self.assertIn('quiet evening together', joined)          # the confirmed item
        self.assertIn('(not confirmed yet)', joined)             # his guess stays labelled
        self.assertIn('tea in the morning', joined)
        self.assertNotIn('five minutes', joined)                 # the retired rule is history only
        self.assertNotIn('[bucket_id:', joined)                  # and no memory was recalled
        base = memory.last_recall_debug['relationship_base']
        self.assertEqual(2, len(base['items']))       # the confirmed item and the labelled guess
        self.assertEqual(1, len([key for key, status in base['statuses'].items()
                                 if status == 'confirmed']))
        self.assertFalse(base['degraded'])
        stored = self.store.read('peter/recall-diagnostics.jsonl')[-1]
        self.assertTrue(stored['relationship_base']['items'])
        self.assertEqual([], stored['injected'])

    def test_the_base_is_data_and_does_not_change_tools_or_permissions(self):
        chat, _ = self.build()
        self.seed_relationship(chat)
        before = repr(sorted((chat.cfg.provider or {}).items(), key=lambda row: row[0]))
        call = self.send(chat, '随便聊聊。')
        messages = call.args[0]
        base_messages = [message for message in messages if isinstance(message.get('content'), str)
                         and 'quiet evening together' in message['content']]
        self.assertTrue(base_messages)
        for message in base_messages:
            self.assertEqual('system', message['role'])
        self.assertEqual(before, repr(sorted((chat.cfg.provider or {}).items(), key=lambda row: row[0])))
        tools = call.kwargs.get('tools') or []
        names = {item.get('function', {}).get('name') or item.get('name') for item in tools
                 if isinstance(item, dict)}
        self.assertFalse({name for name in names if name and 'relationship' in name})


class RelationshipBaseChangeTests(unittest.TestCase):
    """B. current understanding changes; the old version stays as history, not as a live rule."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from learned_context import LearnedContext
        self.store = Store(Path(self.temp.name) / 'data')
        self.profile = LearnedContext(self.store.root, 'fake',
                                      {'learned': True, 'file': 'learned-context.json',
                                       'max_bytes': 4000})

    def test_a_correction_replaces_the_live_item_and_keeps_the_old_one_as_history(self):
        self.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': 'I thought she hated surprises.'})
        self.profile._invoke_profile('profile_replace', {'section': 'relationship',
                                                         'old_text': 'I thought she hated surprises.',
                                                         'new_text': 'She likes small surprises, not public ones.'})
        items = self.profile.relationship_items()
        current = [item for item in items if item['status'] != 'outdated']
        old = [item for item in items if item['status'] == 'outdated']
        self.assertEqual(['She likes small surprises, not public ones.'],
                         [item['text'] for item in current])
        self.assertEqual('I thought she hated surprises.', old[0]['text'])
        self.assertEqual(current[0]['id'], old[0]['superseded_by'])
        base = relationship_base.build(self.profile, {'max_chars': 480})
        self.assertIn('small surprises', base['text'])
        self.assertNotIn('hated surprises', base['text'])

    def test_lan_editing_herself_makes_the_item_hers(self):
        self.profile.save_relationship([{'id': 'r1', 'text': 'She comes home after seven.',
                                         'status': 'confirmed', 'source': 'peter'}])
        item = self.profile.relationship_items()[0]
        self.assertEqual('lan', item['source'])
        # Her older, wrong version is in the version journal, so it can be read back and undone.
        rows = [json.loads(line) for line in
                (self.store.root / 'fake' / 'learned-context.versions.jsonl').read_text(
                    encoding='utf-8').splitlines()]
        self.assertTrue(any(row['tool'] == 'save_relationship' for row in rows))

    def test_an_outdated_item_is_not_counted_against_the_shared_budget(self):
        self.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': 'a long retired rule ' * 40})
        self.profile._invoke_profile('profile_replace', {'section': 'relationship',
                                                         'old_text': ('a long retired rule ' * 40).strip(),
                                                         'new_text': ''})
        status = self.profile.status()
        self.assertEqual(0, status['used_bytes'])


class NaturalAssociationTests(unittest.TestCase):
    """D. scored, deterministic, and allowed to choose nothing."""

    def metas(self, importance, pinned=False):
        return {'importance': importance, 'pinned': 'true' if pinned else 'false'}

    def test_a_meaningful_and_long_unseen_memory_wins_over_a_fresh_trivial_one(self):
        candidates = [('fresh', 'body fresh'), ('meaningful', 'body meaningful')]
        metas = {'fresh': self.metas(2), 'meaningful': self.metas(9)}
        choice = natural_association.choose('今天吃什么呀', candidates, metas, {}, 40, set(),
                                            {'enabled': True, 'min_importance': 4,
                                             'cooldown_turns': 8, 'light_chars': 40})
        self.assertEqual('meaningful', choice['key'])
        self.assertIn({'id': 'fresh', 'reason': 'not_meaningful'}, choice['dropped'])

    def test_a_recently_surfaced_memory_is_suppressed(self):
        choice = natural_association.choose('今天吃什么呀', [('old', 'b')], {'old': self.metas(9)},
                                            {'old': 39}, 40, set(),
                                            {'enabled': True, 'min_importance': 4,
                                             'cooldown_turns': 8, 'light_chars': 40})
        self.assertFalse(choice['key'])
        self.assertEqual([{'id': 'old', 'reason': 'association_cooldown'}], choice['dropped'])

    def test_a_work_message_or_a_work_memory_does_not_enter_a_dinner_conversation(self):
        busy = natural_association.choose('帮我看下这次的 CI 报错在哪', [('ci', 'b')],
                                          {'ci': self.metas(9)}, {}, 40, set(),
                                          {'enabled': True, 'min_importance': 4,
                                           'cooldown_turns': 8, 'light_chars': 40})
        self.assertFalse(busy['key'])
        self.assertEqual('mood', busy['reason'])
        trivial = natural_association.choose('今天吃什么呀', [('ci', 'b')],
                                             {'ci': self.metas(1)}, {}, 40, set(),
                                             {'enabled': True, 'min_importance': 4,
                                              'cooldown_turns': 8, 'light_chars': 40})
        self.assertFalse(trivial['key'])

    def test_the_same_state_always_gives_the_same_answer(self):
        candidates = [('a', 'x'), ('b', 'y'), ('c', 'z')]
        metas = {key: self.metas(6) for key, _ in candidates}
        options = {'enabled': True, 'min_importance': 4, 'cooldown_turns': 8, 'light_chars': 40}
        picks = {natural_association.choose('今天吃什么呀', candidates, metas, {}, 40, set(),
                                            options)['key'] for _ in range(50)}
        self.assertEqual(1, len(picks))

    def test_there_is_no_every_n_turns_rule(self):
        options = {'enabled': True, 'min_importance': 4, 'cooldown_turns': 8, 'light_chars': 40}
        empty = [natural_association.choose('今天吃什么呀', [], {}, {}, turn, set(), options)['key']
                 for turn in range(30)]
        self.assertEqual({''}, set(empty))
        steady = [natural_association.choose('今天吃什么呀', [('a', 'x')], {'a': self.metas(9)}, {},
                                            turn, set(), options)['key'] for turn in range(5)]
        self.assertEqual(['a'] * 5, steady)


class NaturalAssociationRequestTests(ProfileFixture):
    """C/F. related talk recalls a full story; unrelated talk gets only the base."""

    def test_talking_about_a_similar_situation_recalls_the_whole_story_without_asking(self):
        self.rows = {
            'game': {'content': 'We played the lantern game together and lost at the last gate, then '
                                'laughed about it on the balcony for an hour.',
                     'title': 'Lantern game night', 'importance': 7},
        }
        chat, memory = self.build()
        self.seed_relationship(chat)
        call = self.send(chat, '这个 lantern game 最后一关好难。')
        joined = self.joined(call)
        self.assertIn('lost at the last gate', joined)
        self.assertIn('balcony', joined)
        self.assertEqual(['game'], memory.last_recall_debug['relevant_recall'])
        self.assertEqual([], memory.last_recall_debug['natural_association'])
        self.assertIn('quiet evening together', joined)

    def test_unrelated_easy_talk_carries_only_the_base(self):
        chat, memory = self.build()
        self.seed_relationship(chat)
        call = self.send(chat, '今天天气不错，你呢？')
        joined = self.joined(call)
        self.assertNotIn('[bucket_id:', joined)
        self.assertIn('quiet evening together', joined)
        self.assertEqual([], memory.last_recall_debug['relevant_recall'])
        self.assertEqual([], memory.last_recall_debug['natural_association'])
        self.assertEqual('no_candidates', memory.last_recall_debug['association']['reason'])


class ContinuationTests(ProfileFixture):
    """E. one association may carry a few turns, then a new topic releases it."""

    def test_a_weak_association_is_reused_on_the_next_turn_and_released_after_a_topic_switch(self):
        self.drift = ['umbrella']
        self.rows = {'umbrella': {'content': 'We shared one umbrella in the rain and got soaked anyway.',
                                  'title': 'Rain walk', 'importance': 8}}
        chat, memory = self.build()
        first = self.send(chat, '外面阴阴的。', message_id='t1')
        self.assertIn('[bucket_id:umbrella]', self.joined(first))
        self.assertEqual(['umbrella'], memory.last_recall_debug['natural_association'])
        second = self.send(chat, '后来呢', message_id='t2')
        self.assertEqual(['umbrella'], memory.last_recall_debug['reused'])
        self.assertIn('got soaked anyway', self.joined(second))
        third = self.send(chat, '对了，今天吃什么？', message_id='t3')
        self.assertEqual([], memory.last_recall_debug['reused'])
        self.assertNotIn('[bucket_id:umbrella]', self.joined(third))
        self.assertEqual([], memory.last_recall_debug['natural_association'])
        # A restart or a new conversation does not turn last turn's association into a live rule.
        base = chat.profile.relationship_items()
        self.assertEqual([], [item for item in base if 'umbrella' in item['text']])


class BudgetTests(ProfileFixture):
    """G. the base has a small budget of its own; the request stays inside the limit."""

    def test_a_tight_request_keeps_the_base_and_reports_what_did_not_fit(self):
        self.drift = ['big']
        self.rows = {'big': {'content': 'A long fictional evening. ' * 200, 'title': 'Long evening',
                             'importance': 9}}
        chat, memory = self.build(limit=6000)
        self.seed_relationship(chat)
        for index in range(6):
            self.store.append('peter/chat.jsonl',
                              {'role': 'user', 'content': f'older round {index} ' + 'x' * 300,
                               'message_id': f'u{index}'})
            self.store.append('peter/chat.jsonl',
                              {'role': 'assistant', 'content': f'older reply {index} ' + 'y' * 300,
                               'message_id': f'a{index}'})
        call = self.send(chat, '外面阴阴的。')
        joined = self.joined(call)
        self.assertIn('quiet evening together', joined)
        self.assertLessEqual(estimate_request_tokens(call.args[0], None, 0.4), 6000)
        base = memory.last_recall_debug['relationship_base']
        self.assertIsNotNone(base.get('space_tokens'))
        self.assertGreaterEqual(base['space_tokens'], 0)

    def test_a_base_larger_than_its_own_budget_is_reported_as_degraded(self):
        self.profile_override = None
        chat, memory = self.build()
        chat.profile.save_relationship([
            {'id': f'r{i}', 'text': f'Current agreement number {i}: ' + 'detail ' * 30,
             'status': 'confirmed', 'source': 'lan'} for i in range(5)])
        self.store.append('peter/chat.jsonl', {'role': 'user', 'content': 'hello', 'message_id': 'h'})
        self.store.append('peter/chat.jsonl', {'role': 'assistant', 'content': 'hi', 'message_id': 'h2'})
        call = self.send(chat, '随便说说。')
        base = memory.last_recall_debug['relationship_base']
        self.assertTrue(base['degraded'])
        self.assertIn(base['reason'], ('space', 'space_and_cap', 'item_cap'))
        self.assertLess(len(base['items']), 5)
        self.assertTrue(any(row['reason'] == 'space' for row in base['dropped']))
        self.assertLessEqual(base['chars'], relationship_base.DEFAULTS['max_chars'])
        self.assertIn('Current agreement number 0', self.joined(call))


if __name__ == '__main__':
    unittest.main()
