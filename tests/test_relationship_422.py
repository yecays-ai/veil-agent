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
from recall_judge import DEFAULTS as JUDGE_DEFAULTS
from test_recall_judge import FakeEmbedder, FakeJudgeModel
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

    def build(self, limit=32768, recall=None, judge_pick=None):
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
        if judge_pick is not None:
            # The judge reads the vault's vector table; an empty but readable one is enough here.
            import array
            import sqlite3
            vault = vault_path(self.store.root, 'peter')
            vault.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(vault / 'embeddings.db')
            conn.execute('CREATE TABLE IF NOT EXISTS embeddings (bucket_id TEXT PRIMARY KEY, '
                         'embedding TEXT NOT NULL, updated_at TEXT NOT NULL, '
                         'content_hash TEXT NOT NULL DEFAULT "")')
            conn.commit()
            conn.close()
            # The real default configuration runs the related-recall judge; tests must opt in explicitly.
            memory.attach_recall_judge(cfg, FakeJudgeModel(pick=judge_pick),
                                       {**JUDGE_DEFAULTS, 'timeout_seconds': 2.0})
            memory.judge.make_embedder = lambda: FakeEmbedder()
        chat.memory = memory
        self.chat, self.memory, self.backend = chat, memory, backend
        return chat, memory

    def seed_relationship(self, chat):
        # A confirmed item needs a checkable source: here, a real message in this role's chat log.
        self.store.append('peter/chat.jsonl', {'role': 'user', 'message_id': 'm-quiet',
                                               'content': 'a hard day should get a quiet evening'})
        chat.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': CONFIRMED, 'status': 'confirmed',
                                                        'sources': ['chat:m-quiet']})
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
        chat.tool_mode = True
        chat.cfg.tools = {**(chat.cfg.tools or {}), 'enabled': True, 'max_steps': 2}
        files = types.SimpleNamespace(
            tools=[types.SimpleNamespace(name='lab_file_digest', description='Read one lab file.',
                                         inputSchema={'type': 'object',
                                                      'properties': {'path': {'type': 'string'}}})],
            description='Fictional Lab tool.', startup_notice=None, enabled=True, client=object(),
            capability_event=None, capability_event_model=None, capability_record=None,
            consume_capability_event=Mock())
        chat.tools = types.SimpleNamespace(
            tools=files.tools, files=files, description=files.description,
            capability_event=None, capability_event_model=None, capability_record=None,
            consume_capability_event=Mock(), invoke=Mock(return_value=('ok', '成功', 'x')))
        before_tools = chat.current_request_tool_schemas()
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
        after_tools = chat.current_request_tool_schemas()
        self.assertEqual(before_tools, after_tools)          # same tools, same order, same permissions
        sent = call.kwargs.get('tools') or call.args[1] if len(call.args) > 1 else call.kwargs.get('tools')
        sent_names = {(item.get('function') or {}).get('name') or item.get('name')
                      for item in (sent or []) if isinstance(item, dict)}
        before_names = {(item.get('function') or {}).get('name') or item.get('name')
                        for item in before_tools if isinstance(item, dict)}
        self.assertEqual(before_names, sent_names)


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
        self.store.append('fake/chat.jsonl', {'role': 'user', 'message_id': 'm-she',
                                              'content': 'small surprises are fine, public ones are not'})
        self.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': 'I thought she hated surprises.'})
        self.profile._invoke_profile('profile_replace', {'section': 'relationship',
                                                         'old_text': 'I thought she hated surprises.',
                                                         'new_text': 'She likes small surprises, not public ones.',
                                                         'sources': ['chat:m-she']})
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
        self.assertEqual(['umbrella'], memory.last_recall_debug['carried'])
        self.assertEqual([], memory.last_recall_debug['reused'])   # it is pasted, not "already in history"
        self.assertIn('got soaked anyway', self.joined(second))
        third = self.send(chat, '对了，今天吃什么？', message_id='t3')
        self.assertEqual([], memory.last_recall_debug['carried'])
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
        # The long memory cannot be pasted whole here; the diagnostic must show the constraint.
        debug = memory.last_recall_debug
        self.assertTrue(any(item.get('complete') is False for item in debug['items'])
                        or debug['budget_dropped'])
        self.assertTrue(debug['budget']['reason'] in ('proportional', 'budget'))
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



class JudgeAndAssociationRequestTests(ProfileFixture):
    """Review 1: a working judge that says "nothing fits" must not switch natural association off."""

    def test_the_judge_answering_none_still_lets_a_weak_association_into_the_final_request(self):
        self.rows = {
            'lamp': {'content': 'We fixed the brass lamp together and it finally worked.',
                     'title': 'Brass lamp', 'importance': 9},
            'filler': {'content': 'weather talk about the week', 'title': 'Filler', 'importance': 1},
        }
        chat, memory = self.build(judge_pick=[])
        call = self.send(chat, '外面阴阴的 weather。')
        joined = self.joined(call)
        self.assertIn('brass lamp', joined)
        self.assertEqual([], memory.last_recall_debug['relevant_recall'])
        self.assertEqual(['lamp'], memory.last_recall_debug['natural_association'])
        self.assertEqual('empty', memory.last_recall_debug['judge']['status'])
        stored = self.store.read('peter/recall-diagnostics.jsonl')[-1]
        self.assertEqual(['lamp'], stored['natural_association'])
        self.assertEqual([], stored['relevant_recall'])

    def test_the_judged_memory_and_the_association_do_not_inject_the_same_event_twice(self):
        self.rows = {'lamp': {'content': 'We fixed the brass lamp together and it finally worked.',
                              'title': 'Brass lamp', 'importance': 9}}
        chat, memory = self.build(judge_pick=['Brass lamp'])
        call = self.send(chat, '你还记得 brass lamp 那次吗')
        joined = self.joined(call)
        self.assertEqual(1, joined.count('[bucket_id:lamp]'))
        self.assertEqual(['lamp'], memory.last_recall_debug['relevant_recall'])
        self.assertEqual([], memory.last_recall_debug['natural_association'])


class CandidatePoolTests(ProfileFixture):
    """Review 2: the pool is Ombre's importance-ordered list, not only its random draw."""

    def test_a_meaningful_memory_from_the_importance_list_beats_a_trivial_random_draw(self):
        self.rows = {
            'important': {'content': 'The evening we decided quiet is better than plans.', 'title': 'Quiet',
                          'importance': 9, 'created': '2026-09-01T10:00:00'},
            'trivial': {'content': 'a passing note about the weather', 'title': 'Weather', 'importance': 1},
            'archived_big': {'content': 'archived memory with high importance', 'title': 'Archived',
                             'importance': 10, 'type': 'archived'},
        }
        self.drift = ['trivial']
        chat, memory = self.build()
        chat.opening_pending = False
        self.send(chat, '外面阴阴的。', message_id='pool1')
        debug = memory.last_recall_debug
        self.assertEqual(['important'], debug['natural_association'])
        self.assertNotIn('trivial', debug['natural_association'])
        self.assertNotIn('archived_big', debug['natural_association'])
        self.assertGreaterEqual(debug['association']['from_importance'], 2)
        self.assertIn({'id': 'trivial', 'reason': 'not_meaningful'}, debug['association']['dropped'])

    def test_the_same_library_and_turn_give_the_same_candidate(self):
        self.rows = {
            'a': {'content': 'memory one about the balcony', 'title': 'Balcony', 'importance': 6},
            'b': {'content': 'memory two about the market', 'title': 'Market', 'importance': 6},
        }
        picks = []
        for message_id in ('same1', 'same2'):
            self.setUp()   # a fresh store, so the two turns see exactly the same library and history
            self.rows = {
                'a': {'content': 'memory one about the balcony', 'title': 'Balcony', 'importance': 6},
                'b': {'content': 'memory two about the market', 'title': 'Market', 'importance': 6},
            }
            chat, memory = self.build()
            chat.opening_pending = False
            self.send(chat, '今天吃什么呀', message_id=message_id)
            picks.append(memory.last_recall_debug['natural_association'])
        self.assertEqual(picks[0], picks[1])
        self.assertEqual(1, len(picks[0]))


class PageSaveProvenanceTests(unittest.TestCase):
    """Review 3: saving the page only relabels what Lan actually edited."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from learned_context import LearnedContext
        self.store = Store(Path(self.temp.name) / 'data')
        self.profile = LearnedContext(self.store.root, 'fake',
                                      {'learned': True, 'file': 'learned-context.json',
                                       'max_bytes': 4000})
        self.profile.save_relationship([{'id': 'lan-1', 'text': 'She comes home after seven.',
                                         'status': 'confirmed'}])
        self.store.append('fake/chat.jsonl', {'role': 'user', 'message_id': 'm-e',
                                              'content': 'I like small surprises'})
        self.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': 'She said small surprises are fine.',
                                                        'status': 'confirmed', 'sources': ['chat:m-e']})
        self.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': 'Maybe she prefers mornings.',
                                                        'status': 'uncertain'})

    def test_only_the_edited_item_becomes_lans_word(self):
        before = {item['id']: dict(item) for item in self.profile.relationship_items()}
        evidence = next(item for item in before.values() if item['source'] == 'evidence')
        guess = next(item for item in before.values() if item['source'] == 'peter')
        items = [dict(item) for item in before.values()]
        edited = next(item for item in items if item['id'] == 'lan-1')
        edited['text'] = 'She comes home after eight now.'
        self.profile.save_relationship(items)
        after = {item['id']: item for item in self.profile.relationship_items()}
        self.assertEqual('lan', after['lan-1']['source'])
        self.assertEqual('She comes home after eight now.', after['lan-1']['text'])
        self.assertEqual('evidence', after[evidence['id']]['source'])
        self.assertEqual(evidence['sources'], after[evidence['id']]['sources'])
        self.assertEqual(evidence['created_at'], after[evidence['id']]['created_at'])
        self.assertEqual(evidence['updated_at'], after[evidence['id']]['updated_at'])
        self.assertEqual('peter', after[guess['id']]['source'])
        self.assertEqual(before[guess['id']]['created_at'], after[guess['id']]['created_at'])

    def test_a_status_change_lan_makes_is_hers_and_a_new_row_is_hers(self):
        items = [dict(item) for item in self.profile.relationship_items()]
        items.append({'id': 'lan-2', 'text': 'No plans on Sundays.', 'status': 'confirmed'})
        self.profile.save_relationship(items)
        after = {item['id']: item for item in self.profile.relationship_items()}
        self.assertEqual('lan', after['lan-2']['source'])
        self.assertEqual('lan', after['lan-1']['source'])


class EvidencePathTests(unittest.TestCase):
    """Review 4: `confirmed` needs checkable evidence; a model claim alone stays a guess."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from learned_context import LearnedContext
        self.store = Store(Path(self.temp.name) / 'data')
        self.profile = LearnedContext(self.store.root, 'fake',
                                      {'learned': True, 'file': 'learned-context.json',
                                       'max_bytes': 4000})

    def test_confirmed_without_a_source_is_downgraded_to_peters_own_reading(self):
        result, state, _ = self.profile._invoke_profile(
            'profile_append', {'section': 'relationship', 'text': 'She loves early mornings.',
                               'status': 'confirmed'})
        self.assertEqual('成功', state)
        item = self.profile.relationship_items()[0]
        self.assertEqual('uncertain', item['status'])
        self.assertEqual('peter', item['source'])
        self.assertEqual([], item['sources'])
        self.assertIn('own reading', result)
        self.assertIn('uncertain', result)

    def test_a_real_message_reference_makes_it_evidence(self):
        self.store.append('fake/chat.jsonl', {'role': 'user', 'message_id': 'm-kind',
                                              'content': 'I do love early mornings actually'})
        result, state, _ = self.profile._invoke_profile(
            'profile_append', {'section': 'relationship', 'text': 'She loves early mornings.',
                               'status': 'confirmed', 'sources': ['chat:m-kind']})
        self.assertEqual('成功', state)
        item = self.profile.relationship_items()[0]
        self.assertEqual('confirmed', item['status'])
        self.assertEqual('evidence', item['source'])
        self.assertEqual(['chat:m-kind'], item['sources'])
        self.assertIn('evidence', result)

    def test_a_fabricated_message_reference_cannot_make_it_current(self):
        result, state, _ = self.profile._invoke_profile(
            'profile_append', {'section': 'relationship', 'text': 'She loves early mornings.',
                               'status': 'confirmed', 'sources': ['chat:no-such-message']})
        self.assertEqual('成功', state)
        item = self.profile.relationship_items()[0]
        self.assertEqual('uncertain', item['status'])
        self.assertEqual('peter', item['source'])
        self.assertIn('no-such-message', result)

    def test_a_correction_with_evidence_keeps_the_old_reading_as_history_and_the_event_story_untouched(self):
        events = self.store.root / 'fake' / 'events.jsonl'
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text('{"event_id":"e1","story":"The old fictional story."}\n', encoding='utf-8')
        self.store.append('fake/chat.jsonl', {'role': 'user', 'message_id': 'm-fix',
                                              'content': 'no, it is not mornings, it is the evenings'})
        self.profile._invoke_profile('profile_append',
                                     {'section': 'relationship', 'text': 'She loves early mornings.'})
        self.profile._invoke_profile('profile_replace',
                                     {'section': 'relationship',
                                      'old_text': 'She loves early mornings.',
                                      'new_text': 'She loves quiet evenings, not mornings.',
                                      'sources': ['chat:m-fix']})
        items = self.profile.relationship_items()
        current = [item for item in items if item['status'] != 'outdated']
        old = [item for item in items if item['status'] == 'outdated']
        self.assertEqual('evidence', current[0]['source'])
        self.assertEqual(['chat:m-fix'], current[0]['sources'])
        self.assertEqual('She loves early mornings.', old[0]['text'])
        self.assertEqual(current[0]['id'], old[0]['superseded_by'])
        self.assertEqual('{"event_id":"e1","story":"The old fictional story."}\n',
                         events.read_text(encoding='utf-8'))


class ToolLoopEvidenceTests(ProfileFixture):
    """Review 4 end-to-end: a note written through Peter's real tool loop keeps its message source."""

    def install_tools(self, chat):
        from unittest.mock import Mock as _Mock
        files = types.SimpleNamespace(
            tools=[types.SimpleNamespace(name='lab_file_digest', description='Read one lab file.',
                                         inputSchema={'type': 'object',
                                                      'properties': {'path': {'type': 'string'}}})],
            description='Fictional Lab tool.', startup_notice=None, enabled=True, client=object(),
            capability_event=None, capability_event_model=None, capability_record=None,
            consume_capability_event=_Mock())
        chat.tool_mode = True
        chat.cfg.tools = {**(chat.cfg.tools or {}), 'enabled': True, 'max_steps': 2}
        chat.tools = types.SimpleNamespace(
            tools=files.tools, files=files, description=files.description,
            capability_event=None, capability_event_model=None, capability_record=None,
            consume_capability_event=_Mock(),
            invoke=_Mock(return_value=('ok', '成功', 'lab/x.txt')))

    def test_a_note_from_the_tool_loop_is_in_the_next_base_with_its_source(self):
        chat, memory = self.build()
        self.install_tools(chat)
        arguments = json.dumps({'section': 'relationship',
                                'text': 'She said a hard day wants a quiet evening, not a plan.',
                                'status': 'confirmed', 'sources': ['chat:m-now']})
        tool_call = types.SimpleNamespace(id='call-1', function=types.SimpleNamespace(
            name='profile_append', arguments=arguments))
        first = types.SimpleNamespace(
            content=None, tool_calls=[tool_call],
            model_dump=lambda exclude_none=True: {
                'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call-1', 'type': 'function',
                                'function': {'name': 'profile_append', 'arguments': arguments}}]})
        final = types.SimpleNamespace(content='Fictional reply', tool_calls=[],
                                      model_dump=lambda exclude_none=True: {
                                          'role': 'assistant', 'content': 'Fictional reply'})
        self.model.side_effect = [(first, 100, 20, 0), (final, 120, 5, 0)]
        chat.send('今天好累', message_id='m-now')
        item = chat.profile.relationship_items()[0]
        self.assertEqual('confirmed', item['status'])
        self.assertEqual('evidence', item['source'])
        self.assertEqual(['chat:m-now'], item['sources'])
        self.model.side_effect = None
        self.model.return_value = (final, 100, 20, 0)
        second = self.send(chat, '嗯，那我今晚早点睡。', message_id='m-next')
        self.assertIn('quiet evening, not a plan', self.joined(second))


class DataSafetyTests(unittest.TestCase):
    """Review 3/4/25/24: a broken block is refused, provenance is not bypassed, edits are explicit."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from learned_context import LearnedContext
        self.store = Store(Path(self.temp.name) / 'data')
        self.profile = LearnedContext(self.store.root, 'fake',
                                      {'learned': True, 'file': 'learned-context.json',
                                       'max_bytes': 4000})

    def test_a_malformed_relationship_block_is_refused_not_emptied(self):
        from agent_types import UserError
        self.profile.save_relationship([{'id': 'r1', 'text': 'She comes home after seven.',
                                         'status': 'confirmed'}])
        path = self.store.root / 'fake' / 'learned-context.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['relationship'] = {'items': 'not-a-list', 'updated_at': ''}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(UserError):
            self.profile.read()
        status = self.profile.status()
        self.assertFalse(status['available'])
        # The bad data is still on disk, not silently replaced with an empty block.
        self.assertEqual('not-a-list',
                         json.loads(path.read_text(encoding='utf-8'))['relationship']['items'])

    def test_an_empty_expected_updated_at_does_not_bypass_the_concurrency_check(self):
        from agent_types import UserError
        self.profile.save_relationship([{'id': 'r1', 'text': 'She comes home after seven.',
                                         'status': 'confirmed'}])
        with self.assertRaises(UserError):
            self.profile.save_relationship([{'id': 'r1', 'text': 'Changed while away.',
                                             'status': 'confirmed'}], expected_updated_at='')

    def test_whitespace_only_new_text_is_rejected_not_treated_as_removal(self):
        self.profile._invoke_profile('profile_append', {'section': 'relationship',
                                                        'text': 'She comes home after seven.'})
        result, state, _ = self.profile._invoke_profile(
            'profile_replace', {'section': 'relationship', 'old_text': 'She comes home after seven.',
                                'new_text': '   '})
        self.assertEqual('被拒绝', state)
        items = self.profile.relationship_items()
        self.assertEqual('She comes home after seven.', items[0]['text'])
        self.assertNotEqual('outdated', items[0]['status'])

    def test_only_a_whole_matching_note_line_counts_as_a_duplicate(self):
        self.profile.save_section('facts', '- She comes home after seven.\n- She likes the balcony.')
        self.profile.save_relationship([
            {'id': 'same', 'text': 'She comes home after seven.', 'status': 'confirmed'},
            {'id': 'part', 'text': 'likes the balcony', 'status': 'confirmed'}])
        base = relationship_base.build(self.profile, {'max_chars': 480})
        self.assertEqual(['part'], [item['id'] for item in base['items']])
        self.assertIn({'id': 'same', 'reason': 'duplicate'}, base['dropped'])

    def test_the_same_sentence_under_two_ids_is_rendered_once(self):
        chosen = relationship_base.select([
            {'id': 'a', 'text': 'She comes home after seven.', 'status': 'confirmed', 'source': 'lan'},
            {'id': 'b', 'text': '  She comes home after seven. ', 'status': 'confirmed',
             'source': 'peter'}], notes='')
        self.assertEqual(1, len(chosen['items']))
        self.assertIn({'id': 'b', 'reason': 'duplicate'}, chosen['dropped'])


class NoteLineTests(DataSafetyTests):
    """Third review: list markers are normalized without eating a negative sign."""

    def test_a_negative_number_is_not_mistaken_for_a_bullet(self):
        self.profile.save_section('facts', '-5°C mornings are hard for her.')
        self.profile.save_relationship([
            {'id': 'neg', 'text': '-5°C mornings are hard for her.', 'status': 'confirmed'},
            {'id': 'plus', 'text': '5°C mornings are hard for her.', 'status': 'confirmed'}])
        base = relationship_base.build(self.profile, {'max_chars': 480})
        self.assertEqual(['plus'], [item['id'] for item in base['items']])
        self.assertIn({'id': 'neg', 'reason': 'duplicate'}, base['dropped'])

    def test_plus_and_numbered_note_lines_are_recognised(self):
        self.profile.save_section('facts', '+ She keeps the balcony warm.\n2. She likes the market.')
        self.profile.save_relationship([
            {'id': 'balcony', 'text': 'She keeps the balcony warm.', 'status': 'confirmed'},
            {'id': 'market', 'text': 'She likes the market.', 'status': 'confirmed'},
            {'id': 'other', 'text': 'She comes home after seven.', 'status': 'confirmed'}])
        base = relationship_base.build(self.profile, {'max_chars': 480})
        self.assertEqual(['other'], [item['id'] for item in base['items']])


class LegacyOptOutTests(unittest.TestCase):
    """Third review: an existing random_chance = 0 configuration keeps associations off."""

    def test_zero_still_switches_associations_off(self):
        self.assertFalse(natural_association.settings({'random_chance': 0})['enabled'])
        self.assertTrue(natural_association.settings({'random_chance': 0.1})['enabled'])
        self.assertFalse(natural_association.settings({'association_enabled': False})['enabled'])


class ConfigReachTests(unittest.TestCase):
    """Review 7: the documented [profile.relationship_base] table really reaches the request."""

    def test_nested_limits_survive_load_character(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'persona').mkdir()
            (root / 'persona' / 'peter.md').write_text('Peter is a fictional test companion.',
                                                        encoding='utf-8')
            (root / 'characters' / 'peter').mkdir(parents=True)
            (root / 'characters' / 'peter' / 'character.toml').write_text('''
name = "Peter"
provider = "fake"
persona = "persona/peter.md"

[providers.fake]
model = "fake-model"
base_url = "https://fake.local/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 4096
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"

[profile]
learned = true
file = "learned-context.json"
max_bytes = 4000
[profile.relationship_base]
enabled = false
max_chars = 120
max_items = 3
''', encoding='utf-8')
            cfg = load_character(root, 'peter')
            nested = cfg.profile['relationship_base']
            self.assertFalse(nested['enabled'])
            self.assertEqual(120, nested['max_chars'])
            self.assertEqual(3, nested['max_items'])


class PageRoundTripTests(PageSaveProvenanceTests):
    """Review 14: a status change and a removal reach disk and the rendered base."""

    def test_status_change_and_removal_reach_disk_and_the_base(self):
        items = [dict(item) for item in self.profile.relationship_items()]
        for item in items:
            if item['source'] == 'evidence':
                item['status'] = 'outdated'
        items = [item for item in items if item['id'] != 'lan-1']
        self.profile.save_relationship(items)
        after = self.profile.relationship_items()
        self.assertNotIn('lan-1', [item['id'] for item in after])
        evidence = next(item for item in after if 'small surprises' in item['text'])
        # Lan moving it to "no longer true" is her own edit, so the source becomes hers; the evidence
        # that once supported it stays attached for the history.
        self.assertEqual('outdated', evidence['status'])
        self.assertEqual('lan', evidence['source'])
        self.assertEqual(['chat:m-e'], evidence['sources'])
        base = relationship_base.build(self.profile, {'max_chars': 480})
        self.assertNotIn('small surprises', base['text'])
        self.assertIn('Maybe she prefers mornings.', base['text'])


class AssociationPolicyTests(unittest.TestCase):
    """Review 16/21/28: a work memory cannot surface in a light moment; repeats slow down; off is explained."""

    def metas(self, importance):
        return {'importance': importance, 'pinned': 'false'}

    def options(self, **overrides):
        return {'association_enabled': True, 'association_min_importance': 4,
                'association_cooldown_turns': 4, 'association_light_chars': 40, **overrides}

    def test_a_work_or_log_memory_is_rejected_by_its_body(self):
        choice = natural_association.choose(
            '今天吃什么呀', [('ci', '今天的 CI 日志又报错，线上故障还没修完')], {'ci': self.metas(9)},
            {}, 10, set(), self.options())
        self.assertFalse(choice['key'])
        self.assertIn({'id': 'ci', 'reason': 'work_topic'}, choice['dropped'])

    def test_a_memory_that_keeps_surfacing_gets_a_growing_gap(self):
        candidates = [('sole', 'the quiet evening we decided on')]
        metas = {'sole': self.metas(9)}
        first = natural_association.choose('今天吃什么呀', candidates, metas, {}, 1, set(), self.options())
        self.assertEqual('sole', first['key'])
        still_cooled = natural_association.choose(
            '今天吃什么呀', candidates, metas, {'sole': 1}, 6, set(), self.options(),
            previous={'key': 'sole', 'turn': 1, 'repeat': 1})
        self.assertFalse(still_cooled['key'])      # gap doubled to 8, so 5 turns is not enough
        normal_gap = natural_association.choose(
            '今天吃什么呀', candidates, metas, {'sole': 1}, 6, set(), self.options(),
            previous={'key': 'other', 'turn': 3, 'repeat': 3})
        self.assertEqual('sole', normal_gap['key'])

    def test_switching_it_off_names_every_candidate(self):
        choice = natural_association.choose('今天吃什么呀', [('a', 'x'), ('b', 'y')],
                                            {'a': self.metas(9), 'b': self.metas(9)}, {}, 3, set(),
                                            self.options(association_enabled=False))
        self.assertEqual('disabled', choice['reason'])
        self.assertEqual([{'id': 'a', 'reason': 'disabled'}, {'id': 'b', 'reason': 'disabled'}],
                         choice['dropped'])


class BudgetTrimTests(ProfileFixture):
    """Review 23: the required base is trimmed against the real provider limit, not only its own cap."""

    def test_the_base_yields_whole_entries_when_the_limit_is_already_tight(self):
        chat, memory = self.build()
        chat.profile.save_relationship([
            {'id': f'r{i}', 'text': f'Agreement {i}: ' + 'we keep this quiet ' * 3,
             'status': 'confirmed', 'source': 'lan'} for i in range(6)])
        first = self.send(chat, '随便聊聊。', message_id='b1')
        base = memory.last_recall_debug['relationship_base']
        self.assertFalse(base['degraded'])
        full_items = len(base['items'])
        required = 32768 - base['space_tokens']
        chat.cfg.provider['max_input_tokens'] = required - 40
        call = self.send(chat, '嗯。', message_id='b2')
        trimmed = memory.last_recall_debug['relationship_base']
        self.assertEqual('budget', trimmed['reason'])
        self.assertTrue(trimmed['degraded'])
        self.assertLess(len(trimmed['items']), full_items)
        self.assertTrue(any(row['reason'] == 'budget' for row in trimmed['dropped']))
        self.assertLessEqual(estimate_request_tokens(call.args[0], None, 0.4),
                             chat.cfg.provider['max_input_tokens'])


class AssociationDiagnosticsTests(ProfileFixture):
    """Review 31: diagnostics stay bounded and keep the count of what they did not name."""

    def test_a_large_candidate_pool_is_capped_and_counted(self):
        self.rows = {f'm{i}': {'content': f'fictional memory {i} about an evening', 'title': f'Memory {i}',
                               'importance': 9} for i in range(60)}
        chat, memory = self.build()
        chat.opening_pending = False
        self.send(chat, '外面阴阴的。', message_id='cap1')
        association = memory.last_recall_debug['association']
        self.assertLessEqual(len(association['dropped']), 40)
        self.assertGreater(association['dropped_more'], 0)
        self.assertEqual(60, association['from_importance'])


if __name__ == '__main__':
    unittest.main()


class EvidenceVerificationTests(EvidencePathTests):
    """Third review: only Lan's own message can promote a claim; pointers alone cannot; old ids count."""

    def test_peters_own_reply_is_not_evidence(self):
        self.store.append('fake/chat.jsonl', {'role': 'assistant', 'message_id': 'm-peter',
                                              'content': 'I think she loves mornings'})
        result, state, _ = self.profile._invoke_profile(
            'profile_append', {'section': 'relationship', 'text': 'She loves early mornings.',
                               'status': 'confirmed', 'sources': ['chat:m-peter']})
        self.assertEqual('成功', state)
        item = self.profile.relationship_items()[0]
        self.assertEqual('uncertain', item['status'])
        self.assertEqual('peter', item['source'])
        self.assertIn('m-peter', result)

    def test_a_memory_or_event_pointer_alone_cannot_confirm(self):
        result, state, _ = self.profile._invoke_profile(
            'profile_append', {'section': 'relationship', 'text': 'She loves early mornings.',
                               'status': 'confirmed',
                               'sources': ['memory:abc123', 'event:ev-9']})
        item = self.profile.relationship_items()[0]
        self.assertEqual('uncertain', item['status'])
        self.assertEqual('peter', item['source'])
        # The pointers are kept for traceability, but the tool result says why it did not become current.
        self.assertEqual(['memory:abc123', 'event:ev-9'], item['sources'])
        self.assertIn('own reading', result)

    def test_an_old_message_beyond_the_tail_still_counts(self):
        path = self.store.root / 'fake' / 'chat.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as handle:
            handle.write(json.dumps({'role': 'user', 'message_id': 'm-old',
                                     'content': 'I really do love early mornings'}) + '\n')
            for index in range(1200):
                handle.write(json.dumps({'role': 'user', 'message_id': f'filler{index}',
                                         'content': 'fictional chatter ' * 40}) + '\n')
        self.profile._invoke_profile('profile_append',
                                     {'section': 'relationship', 'text': 'She loves early mornings.',
                                      'status': 'confirmed', 'sources': ['chat:m-old']})
        item = self.profile.relationship_items()[0]
        self.assertEqual('confirmed', item['status'])
        self.assertEqual('evidence', item['source'])
        self.assertEqual(['chat:m-old'], item['sources'])


class AssociationRollbackTests(ProfileFixture):
    """Third review: an association that never reached her must not keep its cooldown streak."""

    def test_a_turn_that_delivered_nothing_restores_the_association_state(self):
        self.rows = {'lamp': {'content': 'We fixed the brass lamp together and it finally worked.',
                              'title': 'Brass lamp', 'importance': 9}}
        chat, memory = self.build()
        chat.opening_pending = False
        before = memory.rotation.previous_association()
        self.send(chat, '外面阴阴的。', message_id='roll1')
        self.assertEqual(['lamp'], memory.last_recall_debug['natural_association'])
        self.assertNotEqual(before, memory.rotation.previous_association())
        memory.rollback_recall()                  # what a stopped/errored turn does
        self.assertEqual(before, memory.rotation.previous_association())
