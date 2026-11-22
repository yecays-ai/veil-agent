"""Ticket 419: the whole-round memory budget.

Covers the pure budget arithmetic (same estimator as the request builder), the protected recent
exchange, whole-memory selection, paragraph-boundary partials with a pointer, and the request-level
behaviour: several independent memories injected together, the recent conversation still present
under pressure, and the final request never carrying half a sentence.
"""
import json
import random
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from memory_agent import Memory, vault_path
from memory_sidecar import Sidecar
from recall_budget import (fit, partial_text, protected_recent_tokens, split,
                           tokens_for, tokens_for_messages)
from recall_judge import DEFAULTS as JUDGE_DEFAULTS
from recall_rotation import asks_about_past, is_followup
from test_recall_judge import FakeEmbedder, FakeJudgeModel
from test_recall_rotation import FictionalOmbre
from veil_agent import Budget, Character, Store, estimate_message_tokens


LONG_PARAGRAPHS = [
    'The first evening we found the little brass lamp and polished it until the table shook.',
    'She laughed so hard that the tea went cold, and we agreed never to tell anyone about it.',
    'The next morning the lamp was gone, and only a note on the table said she had taken it home.',
]


class BudgetArithmeticTests(unittest.TestCase):
    def test_the_token_estimate_matches_the_request_builder(self):
        text = '一段中文和三 English words'
        self.assertEqual(estimate_message_tokens([{'role': 'system', 'content': text}], 0.4),
                         tokens_for(text, 0.4))
        self.assertEqual(estimate_message_tokens([{'role': 'system', 'content': text},
                                                  {'role': 'user', 'content': 'hi'}], 0.37),
                         tokens_for_messages([{'role': 'system', 'content': text},
                                              {'role': 'user', 'content': 'hi'}], 0.37))

    def test_both_needs_fit_keeps_each_its_own(self):
        plan = split(1000, 300, 400, 200)
        self.assertEqual(('fits', 300, 400), (plan['reason'], plan['history'], plan['memory']))

    def test_overflow_splits_in_proportion_and_never_drops_below_protected(self):
        plan = split(1000, 8000, 2000, 300)
        self.assertEqual(1000, plan['history'] + plan['memory'])
        self.assertGreaterEqual(plan['history'], 300)
        self.assertEqual('proportional', plan['reason'])
        tiny = split(100, 8000, 2000, 300)
        self.assertEqual(100, tiny['history'] + tiny['memory'])
        self.assertEqual(100, tiny['history'])   # protected is capped by the space itself

    def test_protected_exchange_is_only_the_newest_user_message_and_reply(self):
        rows = [
            {'role': 'user', 'content': 'older question'},
            {'role': 'assistant', 'content': 'older answer'},
            {'role': 'user', 'content': 'she is angry because the lamp broke'},
            {'role': 'assistant', 'content': 'I said I would fix it tomorrow'},
        ]
        tokens = protected_recent_tokens(rows, 0.4)
        self.assertEqual(estimate_message_tokens(rows[2:], 0.4), tokens)
        self.assertLess(tokens, estimate_message_tokens(rows, 0.4))
        self.assertEqual(0, protected_recent_tokens([], 0.4))

    def test_a_memory_too_big_for_the_remaining_room_is_kept_as_whole_paragraphs(self):
        text = '\n\n'.join(LONG_PARAGRAPHS)
        allowance = tokens_for('\n\n'.join(LONG_PARAGRAPHS[:2]), 0.4) + 20
        body, paragraphs, continues = partial_text(text, allowance, 0.4, '(read the rest)')
        self.assertTrue(continues)
        self.assertEqual(2, paragraphs)
        self.assertEqual('\n\n'.join(LONG_PARAGRAPHS[:2]), body)
        self.assertTrue(body.endswith('about it.'))     # ends on a sentence, not mid-word

    def test_fit_keeps_whole_memories_before_partials(self):
        blocks = [{'id': f'b{i}', 'text': 'small memory body ' * 5, 'priority': i} for i in range(5)]
        selected, dropped = fit(blocks, 10_000, 0.4)
        self.assertEqual(5, len(selected))
        self.assertEqual([], dropped)
        tight = fit(blocks, tokens_for(blocks[0]['text'], 0.4) * 2 + 2, 0.4)
        self.assertEqual(['b0', 'b1'], [item['id'] for item in tight[0]])
        self.assertEqual(['b2', 'b3', 'b4'], [item['id'] for item in tight[1]])


class FollowUpTests(unittest.TestCase):
    def test_a_follow_up_sentence_is_recognised(self):
        self.assertTrue(is_followup('那我当时是什么反应？'))
        self.assertTrue(is_followup('后来呢'))
        self.assertTrue(is_followup('what happened next'))
        self.assertFalse(is_followup('我有点烦'))
        self.assertFalse(is_followup('今天吃什么'))
        self.assertTrue(asks_about_past('还记得那次海边的旅行吗'))
        self.assertFalse(asks_about_past('今天天气不错'))

    def test_a_follow_up_after_a_past_question_counts(self):
        recent = [{'role': 'user', 'content': '你还记得我们第一次挑视频吗'},
                  {'role': 'assistant', 'content': '记得，是那个黄铜精炼的。'}]
        self.assertTrue(is_followup('嗯，那你继续', recent))
        self.assertFalse(is_followup('嗯，那你继续', [{'role': 'user', 'content': '今天吃什么'}]))


class MemoryLevelBudgetTests(unittest.TestCase):
    """A real Memory over fictional files: no network, no paid call."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')

    def memory(self, rows, recall=None):
        vault = vault_path(self.store.root, 'fake')
        self.backend = FictionalOmbre(vault / 'dynamic', rows)
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        memory_cfg = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                      'base_url': 'https://example.invalid/v1',
                      'recall': recall or {'cooldown_turns': 0, 'random_chance': 0}}
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake', memory_cfg)
        memory = Memory(cfg, self.store, Budget(self.store), None, self.backend)
        memory.rng = random.Random(7)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    def five_events(self):
        return {f'ev{i}': {'content': f'video night number {i} with a brass lamp and a long talk',
                           'title': f'Video night {i}', 'importance': 5,
                           'created': f'2026-10-{10 + i:02d}T10:00:00'} for i in range(5)}

    def test_five_independent_memories_all_enter_when_there_is_room(self):
        memory = self.memory(self.five_events())
        memory.context('video night brass lamp', include_opening=False, defer=True)
        rows = memory.finish_recall()          # unbudgeted
        text = '\n'.join(row['content'] for row in rows)
        self.assertEqual(5, text.count('[bucket_id:'))
        self.assertEqual(5, len(memory.last_context_refs))

    def test_a_tight_budget_keeps_whole_memories_and_drops_only_the_rest(self):
        memory = self.memory(self.five_events())
        memory.context('video night brass lamp', include_opening=False, defer=True)
        allowance = tokens_for('[bucket_id:ev0]\n' + self.five_events()['ev0']['content'], 0.4) * 2 + 40
        rows = memory.finish_recall(space=allowance, history_tokens=0, protected_tokens=0, calibration=0.4)
        text = '\n'.join(row['content'] for row in rows)
        self.assertEqual(2, text.count('[bucket_id:'))
        self.assertEqual(3, len(memory.last_recall_debug['dropped']))
        self.assertTrue(all(item['reason'] == 'budget' for item in memory.last_recall_debug['dropped']))

    def test_a_long_story_gets_whole_paragraphs_and_a_read_pointer(self):
        story = '\n\n'.join(LONG_PARAGRAPHS * 8)
        rows = {'long': {'content': story, 'title': 'The brass lamp'}}
        memory = self.memory(rows)
        memory.context('brass lamp', include_opening=False, defer=True)
        allowance = tokens_for('[bucket_id:long]\n' + '\n\n'.join((LONG_PARAGRAPHS * 8)[:2]) + ' ' + 'p' * 400, 0.4)
        out = memory.finish_recall(space=allowance, history_tokens=0, protected_tokens=0, calibration=0.4)
        text = out[-1]['content']
        self.assertIn(LONG_PARAGRAPHS[0], text)
        self.assertIn('memory_read with memory_id "long"', text)
        self.assertTrue(len(text) < len(story))
        self.assertIn('"complete": false', json.dumps(memory.last_recall_debug['items']))

    def test_a_complete_event_story_is_preferred_over_one_old_fragment(self):
        rows = {'frag': {'content': 'the lamp arrived at the shop', 'title': 'The lamp'}}
        memory = self.memory(rows)
        story = ('She bought the lamp one winter afternoon.\n\n'
                 'Weeks later the seal on the base finally broke and she found the letter inside.')
        self.store.append('fake/events.jsonl', {'event_id': 'frag', 'memory_id': 'frag',
                                                'title': 'The lamp', 'story': story,
                                                'first_at': '2026-10-01T10:00:00'})
        memory.context('lamp', include_opening=False, defer=True)
        out = memory.finish_recall()
        text = out[-1]['content']
        self.assertIn('the seal on the base finally broke', text)   # the ending is present
        self.assertEqual(['frag'], memory.last_context_refs)

    def test_four_stages_of_one_story_recover_the_before_and_after(self):
        """Acceptance 1: only the middle stage is a search hit; the event story supplies the opening,
        the misunderstanding, the correction and the result."""
        rows = {
            's1': {'content': 'we met the stray cat at the station on a rainy night', 'title': 'The cat',
                   'created': '2026-10-01T20:00:00'},
            's2': {'content': 'I thought she had abandoned the cat because she left early', 'title': 'The misunderstanding',
                   'created': '2026-10-02T20:00:00'},
            's3': {'content': 'she explained she had gone to buy a carrier and we both cried',
                   'title': 'The correction', 'created': '2026-10-03T20:00:00'},
            's4': {'content': 'the cat now lives with us and sleeps on the windowsill',
                   'title': 'The result', 'created': '2026-10-20T20:00:00'},
        }
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'random_chance': 0})
        sidecar = Sidecar(self.store, 'fake', memory.budget.clock)
        for left, right in (('s2', 's1'), ('s3', 's2'), ('s4', 's3')):
            sidecar.link(left, right)
        story = ('We met the stray cat at the station on a rainy night. I thought she had abandoned it '
                 'because she left early, but she had gone to buy a carrier; we both cried. The cat now '
                 'lives with us and sleeps on the windowsill.')
        self.store.append('fake/events.jsonl', {'event_id': 'cat', 'memory_id': 's2', 'title': 'The cat',
                                                'story': story, 'first_at': '2026-10-01T20:00:00'})
        memory.context('misunderstanding abandoned carrier', include_opening=False, defer=True)
        out = memory.finish_recall()
        text = out[-1]['content']
        self.assertIn('We met the stray cat', text)                 # the beginning
        self.assertIn('The cat now lives with us', text)            # the result / ending
        self.assertIn('memory_timeline', text)                      # the rest is reachable by event id

    def test_an_ending_only_question_still_finds_the_event(self):
        """Acceptance 2: the question names only the last fact, not the title."""
        rows = {'claude': {'content': ('We spent months getting the Claude account through the long '
                                       'verification queue, and after all of it the account was finally '
                                       'suspended without any notice.'),
                           'title': 'The Claude account',
                           'created': '2026-10-10T10:00:00'}}
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'random_chance': 0})
        memory.attach_recall_judge(memory.cfg, FakeJudgeModel(pick=['suspended']),
                                   {**JUDGE_DEFAULTS, 'timeout_seconds': 2.0})
        memory.judge.make_embedder = lambda: FakeEmbedder()
        memory.context('was it ever suspended', include_opening=False)
        self.assertEqual(['claude'], memory.last_context_refs)
        self.assertIn('finally suspended', memory.context('was it ever suspended', include_opening=False)[-1]['content'])

    def test_two_adjacent_stories_with_different_endings_are_not_mixed(self):
        rows = {
            'a': {'content': 'the same rainy Tuesday we had tea at the corner cafe, and that evening we '
                             'laughed about the broken umbrella', 'title': 'Tea and umbrella'},
            'b': {'content': 'the same rainy Tuesday we had tea at the corner cafe, but that evening we '
                             'argued about the broken umbrella and she left', 'title': 'Tea and leaving'},
        }
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'random_chance': 0})
        memory.context('rainy Tuesday tea broken umbrella', include_opening=False)
        text = memory.context('rainy Tuesday tea broken umbrella', include_opening=False)[-1]['content']
        self.assertIn('laughed about the broken umbrella', text)
        self.assertIn('argued about the broken umbrella', text)
        self.assertNotEqual(text.count('[bucket_id:'), 1)

    def test_an_immediate_follow_up_re_injects_what_was_just_shown(self):
        rows = {'video': {'content': 'the first video I picked was about brass refining', 'title': 'First video'}}
        memory = self.memory(rows, recall={'cooldown_turns': 3, 'random_chance': 0})
        first = memory.context('还记得 video 那次吗', include_opening=False)
        self.assertEqual(['video'], memory.last_context_refs)
        # The follow-up sentence contains no searchable word for the memory, and the cooldown would
        # drop it; the carry-over from the previous turn keeps it.
        memory.context('那我当时是什么反应？', include_opening=False,
                       recent=[{'role': 'user', 'content': '你还记得第一次挑视频吗'},
                               {'role': 'assistant', 'content': '是黄铜精炼那个。'}])
        self.assertEqual(['video'], memory.last_context_refs)
        self.assertEqual('followup', memory.last_context_kinds['video'])
        self.assertTrue(first)


class ChatAssemblyBudgetTests(unittest.TestCase):
    """The real request builder over a real Memory: what Lan's model actually receives."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'peter.md').write_text('Peter is a test companion.', encoding='utf-8')
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
enabled = true
max_steps = 2
''', encoding='utf-8')
        self.store = Store(self.root / 'data')
        self.rows = {f'ev{i}': {'content': f'video night number {i} with a brass lamp and a long talk',
                                'title': f'Video night {i}', 'importance': 5,
                                'created': f'2026-10-{10 + i:02d}T10:00:00'} for i in range(5)}

    def build(self, limit):
        from veil_agent import Budget, Chat, load_character
        cfg = load_character(self.root, 'peter')
        cfg.provider['max_input_tokens'] = limit
        cfg.memory['recall'] = {'cooldown_turns': 0, 'random_chance': 0}
        cfg.memory.update({'model': 'fake-embedding', 'input_cny_per_million': '0',
                           'mode': 'native', 'base_url': 'https://example.invalid/v1'})
        budget = Budget(self.store)
        model = Mock(return_value=(types.SimpleNamespace(content='Fictional reply', tool_calls=[]),
                                   100, 20, 0))
        chat = Chat(cfg, self.store, budget, model, output=[].append)
        vault = vault_path(self.store.root, 'peter')
        backend = FictionalOmbre(vault / 'dynamic', self.rows)
        memory = Memory(cfg, self.store, budget, model, backend)
        memory.attach_recall_judge(cfg, FakeJudgeModel(pick=[row['title'] for row in self.rows.values()]),
                                   {**JUDGE_DEFAULTS, 'timeout_seconds': 2.0})
        memory.judge.make_embedder = lambda: FakeEmbedder()
        chat.memory = memory
        return chat, model, memory

    def seed_history(self):
        # An older block of rounds plus the exchange that explains why she is upset now.
        for i in range(8):
            self.store.append('peter/chat.jsonl', {'role': 'user', 'content': f'older round {i} ' + 'x' * 400,
                                                   'message_id': f'u{i}'})
            self.store.append('peter/chat.jsonl', {'role': 'assistant', 'content': f'older reply {i} ' + 'y' * 400,
                                                   'message_id': f'a{i}'})
        self.store.append('peter/chat.jsonl', {'role': 'user', 'content': '我今天真的很烦，因为那个 brass lamp 又裂了',
                                               'message_id': 'anger'})
        self.store.append('peter/chat.jsonl', {'role': 'assistant', 'content': '我知道你为什么烦，是灯的事情让你累了。',
                                               'message_id': 'anger-reply'})

    def test_several_memories_and_the_recent_exchange_reach_the_model_together(self):
        self.seed_history()
        chat, model, memory = self.build(24000)
        chat.send("你还记得第一次挑视频 video 那次吗", message_id='q1')
        sent = model.call_args.args[0]
        joined = '\n'.join(message.get('content') if isinstance(message.get('content'), str) else ''
                           for message in sent)
        self.assertGreaterEqual(joined.count('[bucket_id:'), 4)
        self.assertIn('是灯的事情让你累了', joined)          # the reason she is upset survives
        self.assertNotIn('…\n(Only the beginning', joined)

    def test_a_tight_limit_keeps_the_recent_reason_and_never_cuts_a_sentence(self):
        self.seed_history()
        chat, model, memory = self.build(6000)
        chat.send("你还记得第一次挑视频 video 那次吗", message_id='q2')
        sent = model.call_args.args[0]
        joined = '\n'.join(message.get('content') if isinstance(message.get('content'), str) else ''
                           for message in sent)
        self.assertIn('是灯的事情让你累了', joined)
        self.assertLess(joined.count('[bucket_id:'), 5)
        self.assertNotIn('…\n(Only the beginning', joined)
        self.assertTrue(memory.last_recall_debug['dropped'])
        budget = memory.last_memory_budget
        self.assertIsNotNone(budget)
        self.assertEqual('proportional', budget['reason'])
        self.assertGreaterEqual(budget['history_allowance'], 0)

    def test_an_autonomous_wake_uses_the_same_budget_and_has_no_three_memory_cap(self):
        self.seed_history()
        chat, model, memory = self.build(24000)
        chat.send('wake context', autonomy=True, memory_query='video night brass lamp')
        joined = '\n'.join(message.get('content') if isinstance(message.get('content'), str) else ''
                           for message in model.call_args.args[0])
        self.assertEqual(5, joined.count('[bucket_id:'))
        self.assertIsNotNone(memory.last_memory_budget)
        self.assertTrue(all(kind == 'relevant' for kind in memory.last_context_kinds.values()))

    def test_a_restart_keeps_the_cooldown_state_and_the_budget_still_applies(self):
        self.rows = {'video': {'content': 'the first video I picked was about brass refining',
                               'title': 'First video'}}
        self.seed_history()
        chat1, model1, memory1 = self.build(24000)
        chat1.send('还记得 video 那次吗', message_id='r1')
        self.assertEqual(['video'], memory1.last_context_refs)
        first_turn = memory1.rotation.turn
        # A new app process over the same data: the cooldown turn counter is read from disk.
        chat2, model2, memory2 = self.build(24000)
        self.assertGreaterEqual(memory2.rotation.turn, first_turn)
        chat2.send('还记得 video 那次吗', message_id='r2')
        self.assertEqual(['video'], memory2.last_context_refs)
        self.assertIsNotNone(memory2.last_memory_budget)


if __name__ == '__main__':
    unittest.main()
