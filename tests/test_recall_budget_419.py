"""Ticket 419: the whole-round memory budget.

Covers the pure budget arithmetic (same estimator as the request builder), the protected recent
exchange, whole-memory selection, paragraph-boundary partials with a pointer, and the request-level
behaviour: several independent memories injected together, the recent conversation still present
under pressure, and the final request never carrying half a sentence.
"""
import json
from veil import prompts
import random
import re
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from veil.brain.event_memory import EventMemory
from veil.brain.memory_agent import Memory, vault_path
from veil.brain.memory_native import NativeMemoryTools
from veil.brain.memory_sidecar import Sidecar
from veil.brain.recall.recall_budget import (fit, partial_text, protected_recent_tokens, split,
                           tokens_for, tokens_for_messages)
from veil.brain.recall.recall_judge import DEFAULTS as JUDGE_DEFAULTS
from veil.brain.recall.recall_rotation import asks_about_past, is_followup
from test_recall_judge import FakeEmbedder, FakeJudgeModel
from test_recall_rotation import FictionalOmbre
from veil.core.veil_agent import Budget, Character, Store, estimate_message_tokens


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
        self.assertEqual(1000, plan['history'] + plan['memory'] + plan['auxiliary'])
        self.assertGreaterEqual(plan['history'], 300)
        self.assertEqual('proportional', plan['reason'])
        tiny = split(100, 8000, 2000, 300)
        self.assertEqual(100, tiny['history'] + tiny['memory'] + tiny['auxiliary'])
        self.assertEqual(100, tiny['history'])   # protected is capped by the space itself

    def test_optional_auxiliary_gets_a_share_but_never_eats_the_protected_exchange(self):
        plan = split(1000, 3000, 0, 120, auxiliary_need=5000)
        self.assertEqual(1000, plan['history'] + plan['memory'] + plan['auxiliary'])
        self.assertGreaterEqual(plan['history'], 120)
        self.assertGreater(plan['auxiliary'], 0)

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
        self.assertTrue(is_followup('嗯，好', recent))
        self.assertFalse(is_followup('嗯，好', [{'role': 'user', 'content': '今天吃什么'}]))
        # A new explicit past question is not a continuation of the previous subject.
        self.assertFalse(is_followup('还记得另一件完全不同的事吗', recent))


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
                      'recall': recall or {'cooldown_turns': 0, 'association_enabled': False}}
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake', memory_cfg)
        memory = Memory(cfg, self.store, Budget(self.store), None, self.backend)
        memory.rng = random.Random(7)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    def five_events(self):
        return {f'ev{i}': {'content': f'Video night {i}. We watched a brass lamp film. Then we talked for hours.',
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
        allowance = tokens_for('[bucket_id:ev0]\n' + self.five_events()['ev0']['content'], 0.4) * 2 + 150
        rows = memory.finish_recall(space=allowance, history_tokens=0, protected_tokens=0, calibration=0.4)
        text = '\n'.join(row['content'] for row in rows)
        self.assertEqual(2, text.count('[bucket_id:'))
        self.assertEqual(3, len(memory.last_recall_debug['dropped']))
        self.assertTrue(all(item['reason'] == 'budget' for item in memory.last_recall_debug['dropped']))
        self.assertEqual(memory.last_memory_budget['memory_used'],
                         tokens_for_messages(rows, 0.4))

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
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
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
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
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
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
        memory.context('rainy Tuesday tea broken umbrella', include_opening=False)
        text = memory.context('rainy Tuesday tea broken umbrella', include_opening=False)[-1]['content']
        self.assertIn('laughed about the broken umbrella', text)
        self.assertIn('argued about the broken umbrella', text)
        self.assertNotEqual(text.count('[bucket_id:'), 1)

    def test_an_immediate_follow_up_re_injects_what_was_just_shown(self):
        rows = {'video': {'content': 'the first video I picked was about brass refining', 'title': 'First video'}}
        memory = self.memory(rows, recall={'cooldown_turns': 3, 'association_enabled': False})
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
        self.rows = {f'ev{i}': {'content': f'Video night {i}. We watched a brass lamp film. Then we talked for hours.',
                                'title': f'Video night {i}', 'importance': 5,
                                'created': f'2026-10-{10 + i:02d}T10:00:00'} for i in range(5)}

    def build(self, limit, cooldown=0):
        from veil.core.veil_agent import Budget, Chat, load_character
        cfg = load_character(self.root, 'peter')
        cfg.provider['max_input_tokens'] = limit
        cfg.memory['recall'] = {'cooldown_turns': cooldown, 'association_enabled': False}
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

    def seed_history(self, rounds=8):
        # An older block of rounds plus the exchange that explains why she is upset now.
        for i in range(rounds):
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

    def test_a_tight_limit_keeps_at_least_one_memory_and_never_cuts_a_sentence(self):
        self.seed_history()
        chat, model, memory = self.build(6500)
        chat.send("你还记得第一次挑视频 video 那次吗", message_id='q2')
        sent = model.call_args.args[0]
        joined = '\n'.join(message.get('content') if isinstance(message.get('content'), str) else ''
                           for message in sent)
        self.assertIn('是灯的事情让你累了', joined)
        self.assertGreaterEqual(joined.count('[bucket_id:'), 1)
        self.assertLess(joined.count('[bucket_id:'), 5)
        self.assertTrue(memory.last_recall_debug['dropped'])
        match = re.search(r'\[bucket_id:[^\]]+\]\n(.*?)(?:\n\n|\Z)', joined, re.S)
        self.assertIsNotNone(match)
        body = match.group(1).split('\n(')[0].rstrip()
        self.assertTrue(body.endswith('.'), 'a partially shown memory must end on a sentence')
        budget = memory.last_memory_budget
        self.assertIsNotNone(budget)
        self.assertEqual('proportional', budget['reason'])
        self.assertGreaterEqual(budget['history_allowance'], 0)

    def test_optional_auxiliary_cannot_erase_the_recent_exchange(self):
        """Ticket 419 review P1: a huge optional background block must not make history_allowance 0."""
        self.seed_history()
        chat, model, memory = self.build(6000)
        chat.send('那怎么办？', message_id='aux',
                  extra_context='OPTIONAL_BACKGROUND ' + ('noise ' * 3000))
        sent = model.call_args.args[0]
        said = [(message.get('role'), message.get('content')) for message in sent
                if isinstance(message.get('content'), str)]
        self.assertTrue(any(role == 'user' and 'brass lamp' in content for role, content in said))
        self.assertTrue(any(role == 'assistant' and '是灯的事情让你累了' in content
                            for role, content in said))
        # The optional block may be trimmed; the recent exchange may not.
        joined = '\n'.join(content for _, content in said)
        self.assertNotIn('noise noise noise noise noise noise', joined)

    def test_removed_history_reuse_never_hides_the_current_recall_block(self):
        # P6-S1: reuse_recall() looked for memory text in chat history messages while the blocks are
        # injected in a system message, so it matched nothing (audit: 0/220). It was removed on
        # purpose; this test locks in that removing it never drops the block the current turn decided
        # to inject, even when an older history copy happens to contain the same text.
        self.rows = {'video': {'content': 'A brass lamp film. The ending is a fictional blue key.',
                               'title': 'Video night'}}
        block = '[bucket_id:video]\n' + self.rows['video']['content']
        self.store.append('peter/chat.jsonl', {'role': 'user', 'content': 'video night brass lamp',
                                             'message_id': 'old-u'})
        self.store.append('peter/chat.jsonl', {'role': 'assistant', 'content': block,
                                             'message_id': 'old-a'})
        chat, model, memory = self.build(9000)
        chat.send('还记得 brass lamp video night 吗', message_id='reuse-q')
        sent = model.call_args.args[0]
        recall_head = prompts.text('memory.recall.head')
        self.assertTrue(any(str(row.get('content')).startswith(recall_head)
                            and block in str(row.get('content')) for row in sent))
        self.assertEqual([], memory.last_recall_debug['reused'])
        self.assertFalse(hasattr(memory, 'reuse_recall'))

    def test_empty_memory_headers_cannot_displace_recent_history(self):
        self.seed_history()
        chat, model, memory = self.build(6000)
        chat.send('那怎么办？', message_id='huge-aux',
                  extra_context='OPTIONAL_BACKGROUND ' + 'noise ' * 300000)
        sent = model.call_args.args[0]
        self.assertTrue(any(row['role'] == 'user' and '我今天真的很烦' in str(row.get('content'))
                            for row in sent))
        self.assertTrue(any(row['role'] == 'assistant' and '是灯的事情' in str(row.get('content'))
                            for row in sent))
        budget = memory.last_memory_budget
        self.assertGreaterEqual(budget['history_allowance'], budget['protected'])
        self.assertLessEqual(budget['memory_used'], budget['memory_allowance'])

    def test_an_autonomous_wake_uses_the_same_budget_and_has_no_three_memory_cap(self):
        self.seed_history()
        chat, model, memory = self.build(24000)
        chat.send('wake context', autonomy=True, memory_query='video night brass lamp')
        joined = '\n'.join(message.get('content') if isinstance(message.get('content'), str) else ''
                           for message in model.call_args.args[0])
        self.assertEqual(5, joined.count('[bucket_id:'))
        self.assertIsNotNone(memory.last_memory_budget)
        self.assertTrue(all(kind == 'relevant' for kind in memory.last_context_kinds.values()))

    def test_a_restart_keeps_a_nonzero_cooldown_and_an_explicit_past_question_gets_through(self):
        self.rows = {'video': {'content': 'the first video I picked was about brass refining',
                               'title': 'First video'}}
        self.seed_history()
        chat1, model1, memory1 = self.build(24000, cooldown=3)
        chat1.send('video 的事情', message_id='r1')
        self.assertEqual(['video'], memory1.last_context_refs)
        first_turn = memory1.rotation.turn
        # A new app process over the same data: the cooldown turn counter is read from disk.
        chat2, model2, memory2 = self.build(24000, cooldown=3)
        self.assertGreaterEqual(memory2.rotation.turn, first_turn)
        memory2.rotation.turn += 1          # no longer the immediately-following turn
        chat2.send('video 的事情', message_id='r2')
        self.assertEqual([], memory2.last_context_refs)       # cooled through the restart
        chat2.send('还记得 video 那次吗', message_id='r3')
        self.assertEqual(['video'], memory2.last_context_refs)  # explicit past question gets through
        self.assertIsNotNone(memory2.last_memory_budget)


class AuthoritativeStoryTests(MemoryLevelBudgetTests):
    def test_a_shorter_authoritative_story_replaces_the_longer_old_fragment(self):
        rows = {'claude': {'content': 'the old fragment about the ban that was later reversed',
                           'title': 'The Claude account'}}
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
        self.store.append('fake/events.jsonl', {'event_id': 'claude', 'memory_id': 'claude',
                                                'title': 'The Claude account',
                                                'story': 'The ban was reversed. UNIQUE_NEW_END.',
                                                'first_at': '2026-10-01T10:00:00'})
        memory.context('ban reversed', include_opening=False, defer=True)
        text = memory.finish_recall()[-1]['content']
        self.assertIn('The ban was reversed. UNIQUE_NEW_END.', text)
        self.assertNotIn('the old fragment about the ban', text)


class ReadBackTests(MemoryLevelBudgetTests):
    def test_no_story_event_recovers_all_stages_through_timeline_and_memory_read(self):
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
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
        sidecar = Sidecar(self.store, 'fake', memory.budget.clock)
        for left, right in (('s2', 's1'), ('s3', 's2'), ('s4', 's3')):
            sidecar.link(left, right)
        # The event exists but has no prewritten story: only the stages are available.
        self.store.append('fake/events.jsonl', {'event_id': 'cat', 'memory_id': 's2', 'title': 'The cat',
                                                'story': '', 'status': 'open',
                                                'first_at': '2026-10-01T20:00:00',
                                                'last_at': '2026-10-20T20:00:00'})
        for index, key in enumerate(('s1', 's2', 's3', 's4'), 1):
            self.store.append('fake/event-stages.jsonl', {
                'stage_id': f'st{index}', 'event_id': 'cat', 'at': f'2026-10-0{index}T20:00:00',
                'text': rows[key]['content'], 'state': 'committed'})
        memory.context('misunderstanding abandoned carrier', include_opening=False)
        memory.finish_recall()
        self.assertIn('s2', memory.last_context_refs)
        pending = str(getattr(memory, '_pending_recall', {}))
        self.assertIn('memory_timeline', pending)
        self.assertIn('cat', pending)
        events = EventMemory(memory, {'enabled': True}, model=None, sidecar=sidecar)
        rendered, next_offset, total = events.timeline('cat')
        self.assertEqual(4, total)
        for key in ('s1', 's2', 's3', 's4'):
            self.assertIn(rows[key]['content'], rendered)
        tools = NativeMemoryTools(memory)
        result, state, _ = tools.invoke('memory_read', {'memory_id': 's4'})
        self.assertIn('the cat now lives with us', result)

    def test_a_long_story_can_be_read_to_its_unique_end(self):
        story = '\n\n'.join(LONG_PARAGRAPHS * 4) + '\n\nThe unique closing fact is the brass key.'
        memory = self.memory({'long': {'content': story, 'title': 'The brass lamp'}},
                             recall={'cooldown_turns': 0, 'association_enabled': False})
        tools = NativeMemoryTools(memory)
        offset, version, chunks = 0, None, []
        for _ in range(80):
            args = {'memory_id': 'long', 'offset': offset}
            if version:
                args['version'] = version
            result, state, _ = tools.invoke('memory_read', args)
            data = json.loads(result)
            chunks.append(data.get('text') or '')
            version = data.get('version')
            offset = data.get('next_offset')
            if offset is None:
                break
        self.assertIn('The unique closing fact is the brass key', ''.join(chunks))


class FollowUpReviewTests(unittest.TestCase):
    def test_only_the_immediately_preceding_user_message_counts(self):
        recent = [{'role': 'user', 'content': '还记得 V50 吗'},
                  {'role': 'assistant', 'content': '记得'},
                  {'role': 'user', 'content': '今天吃什么'},
                  {'role': 'assistant', 'content': '面'}]
        self.assertFalse(is_followup('嗯，好', recent))       # unrelated topic after the past question
        self.assertTrue(is_followup('那我当时是什么反应？', recent))  # explicit continuation phrase
        self.assertFalse(is_followup('还记得另一件完全不同的事吗', recent))  # a new past topic


class FinalStateTests(MemoryLevelBudgetTests):
    def test_empty_groups_are_omitted_when_only_recent_history_fits(self):
        memory = self.memory({})
        memory.context('unmatched', include_opening=True, defer=True)
        messages = memory.finish_recall(space=86, history_tokens=100,
                                        protected_tokens=86, auxiliary_tokens=5000)
        self.assertEqual([], messages)
        self.assertEqual(0, memory.last_memory_budget['memory_used'])
        self.assertEqual(86, memory.last_memory_budget['history_allowance'])

    def test_short_real_memory_fits_when_empty_notices_do_not(self):
        memory = self.memory({'short': {'content': 'Tiny fact.', 'title': 'Short'}})
        memory.context('Tiny', include_opening=True, defer=True)
        from veil import prompts
        actual = [{'role': 'system', 'content':
                   prompts.text('memory.recall.head') + '\n'
                   + memory._pending_recall['blocks'][0]['text']}]
        allowance = estimate_message_tokens(actual, 0.4)
        out = memory.finish_recall(space=86 + allowance, history_tokens=86, protected_tokens=86)
        self.assertIn('Tiny fact.', '\n'.join(row['content'] for row in out))
        self.assertGreaterEqual(memory.last_memory_budget['history_allowance'], 86)
        self.assertLessEqual(memory.last_memory_budget['memory_used'], allowance)

    def test_final_budget_guard_can_drop_an_oversized_opening_block(self):
        memory = self.memory({})
        memory.context('unmatched', include_opening=True, defer=True)
        block = {'id': 'opening', 'group': 'opening', 'priority': 500,
                 'text': '[bucket_id:opening]\n' + 'A long complete sentence. ' * 100,
                 'tokens': 10000, 'complete': True}
        memory._pending_recall['blocks'] = [block]
        with unittest.mock.patch('veil.brain.recall.recall_budget.fit', return_value=([block], [])):
            out = memory.finish_recall(space=300, history_tokens=250,
                                        protected_tokens=100, auxiliary_tokens=0)
        self.assertNotIn('opening', memory.last_context_refs)
        self.assertLessEqual(estimate_message_tokens(out, 0.4),
                             memory.last_memory_budget['memory_allowance'])
        self.assertGreaterEqual(memory.last_memory_budget['history_allowance'], 100)

    def test_memory_used_equals_the_messages_actually_sent(self):
        memory = self.memory(self.five_events(), recall={'cooldown_turns': 0, 'association_enabled': False})
        memory.context('video night brass lamp', include_opening=False, defer=True)
        messages = memory.finish_recall(space=5000, history_tokens=0, protected_tokens=0, calibration=0.4)
        self.assertEqual(memory.last_memory_budget['memory_used'],
                         tokens_for_messages(messages, 0.4))
        empty = self.memory({'x': {'content': 'unrelated filler'}},
                            recall={'cooldown_turns': 0, 'association_enabled': False})
        empty.context('quantum spreadsheets', include_opening=False, defer=True)
        messages = empty.finish_recall(space=5000, history_tokens=0, protected_tokens=0, calibration=0.4)
        self.assertEqual(empty.last_memory_budget['memory_used'],
                         tokens_for_messages(messages, 0.4))
        self.assertGreater(empty.last_memory_budget['memory_used'], 0)

    def test_final_trim_removes_a_memory_from_last_injected_refs(self):
        memory = self.memory(self.five_events(), recall={'cooldown_turns': 0, 'association_enabled': False})
        memory.context('video night brass lamp', include_opening=False, defer=True)
        memory.finish_recall()
        self.assertIn('ev0', memory.last_injected_refs)
        memory.note_budget_dropped(['ev0'])
        self.assertNotIn('ev0', memory.last_injected_refs)
        self.assertNotIn('ev0', memory.last_context_refs)
        self.assertIn('ev0', memory.last_recall_debug['budget_dropped'])
        self.assertTrue(memory.last_recall_debug['final_trim'])

    def test_a_failed_turn_restores_the_previous_successful_refs(self):
        memory = self.memory(self.five_events(), recall={'cooldown_turns': 0, 'association_enabled': False})
        memory.context('video night brass lamp', include_opening=False, defer=True)
        memory.finish_recall()
        first = list(memory.last_injected_refs)
        memory.context('video night brass lamp', include_opening=False, defer=True)
        memory.finish_recall()
        memory.rollback_recall()
        self.assertEqual(first, memory.last_injected_refs)

    def test_budget_drops_are_also_in_budget_dropped(self):
        memory = self.memory(self.five_events(), recall={'cooldown_turns': 0, 'association_enabled': False})
        memory.context('video night brass lamp', include_opening=False, defer=True)
        allowance = tokens_for('[bucket_id:ev0]\n' + self.five_events()['ev0']['content'], 0.4) * 2 + 150
        memory.finish_recall(space=allowance, history_tokens=0, protected_tokens=0, calibration=0.4)
        self.assertEqual(sorted(memory.last_recall_debug['budget_dropped']),
                         sorted(item['id'] for item in memory.last_recall_debug['dropped']
                                if item['reason'] == 'budget'))


class EffectiveHistoryReuseRemovedTests(MemoryLevelBudgetTests):
    def test_history_copy_never_suppresses_the_recall_block(self):
        # P6-S1: the old reuse_recall() searched chat history for memory text that is injected in a
        # system message, so it never matched (0/220 in the audit). It was removed; a history copy
        # must not make the current turn skip a memory it decided to inject.
        story = 'The first brass lamp.\n\n' + 'A long middle. ' * 20 + 'UNIQUE_END.'
        memory = self.memory({'long': {'content': story, 'title': 'The brass lamp'}})
        memory.context('brass lamp', include_opening=False, defer=True)
        messages = memory.finish_recall(space=3000)
        block = memory._pending_recall['blocks'][0]['text']
        self.assertFalse(hasattr(memory, 'reuse_recall'))
        self.assertIn(block, messages[-1]['content'])
        self.assertEqual([], memory.last_recall_debug['reused'])
        self.assertIn('long', memory.last_injected_refs)


class BoundaryTests(unittest.TestCase):
    def test_partial_sentence_keeps_closing_quotes(self):
        from veil.brain.recall.recall_rotation import _cut_on_boundary
        for sentence in ('“我会回来。”', '「我会回来。」', '“I will return.”', '（我会回来。）', 'First sentence.】'):
            text = sentence + ' ' + '后续还有很多内容。' * 20
            body, _, _ = partial_text(text, tokens_for(sentence, 0.4), 0.4)
            self.assertEqual(sentence, body)
            self.assertEqual('', _cut_on_boundary(sentence + '后续。', len(sentence) - 1))
            self.assertEqual(sentence, _cut_on_boundary(sentence + '后续。', len(sentence)))

    def test_wrapped_line_is_not_a_sentence_boundary(self):
        from veil.brain.recall.recall_rotation import _cut_on_boundary
        text = '这句话写到这里\n才接着说完。'
        self.assertEqual('', _cut_on_boundary(text, len('这句话写到这里')))
        self.assertEqual((None, 0, False),
                         partial_text(text, tokens_for('这句话写到这里', 0.4), 0.4))

    def test_chinese_sentences_split_without_spaces(self):
        text = '第一句话。第二句话。第三句话。'
        allowance = tokens_for('第一句话。第二句话。\n(read more)', 0.4)
        body, pieces, continues = partial_text(text, allowance, 0.4, '(read more)')
        self.assertTrue(continues)
        self.assertTrue(body.startswith('第一句话。'))
        self.assertTrue(body.endswith('。'))
        self.assertNotIn('第三句', body)

    def test_no_complete_sentence_fits_so_only_the_pointer_is_sent(self):
        text = 'one very long sentence without punctuation that keeps going and going ' * 40
        selected, dropped = fit([{'id': 'x', 'text': '[bucket_id:x]\n' + text,
                                  'pointer': '(Use memory_read with memory_id "x" for the rest.)',
                                  'priority': 1}], 120, 0.4)
        self.assertEqual(1, len(selected))
        self.assertTrue(selected[0].get('not_shown'))
        self.assertIn('memory_read', selected[0]['text'])
        self.assertNotIn('one very long sentence', selected[0]['text'])


class AdjacentStoryOwnershipTests(MemoryLevelBudgetTests):
    def test_two_adjacent_events_keep_their_own_endings(self):
        rows = {
            'a': {'content': 'the same rainy Tuesday we had tea at the corner cafe', 'title': 'Tea and umbrella',
                  'created': '2026-10-10T10:00:00'},
            'b': {'content': 'the same rainy Tuesday we had tea at the corner cafe', 'title': 'Tea and leaving',
                  'created': '2026-10-10T20:00:00'},
        }
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False, 'dedup': False})
        self.store.append('fake/events.jsonl', {'event_id': 'ea', 'memory_id': 'a', 'title': 'Tea and umbrella',
                                                'story': 'We laughed about the broken umbrella. OWN_END_A.',
                                                'first_at': '2026-10-10T10:00:00'})
        self.store.append('fake/events.jsonl', {'event_id': 'eb', 'memory_id': 'b', 'title': 'Tea and leaving',
                                                'story': 'We argued about the broken umbrella. OWN_END_B.',
                                                'first_at': '2026-10-10T20:00:00'})
        memory.context('rainy Tuesday tea broken umbrella', include_opening=False, defer=True)
        text = '\n'.join(message['content'] for message in memory.finish_recall())
        self.assertIn('OWN_END_A', text)
        self.assertIn('OWN_END_B', text)
        events = EventMemory(memory, {'enabled': True}, model=None)
        a, _, _ = events.timeline('ea')
        b, _, _ = events.timeline('eb')
        self.assertIn('OWN_END_A', a)
        self.assertNotIn('OWN_END_B', a)
        self.assertIn('OWN_END_B', b)
        self.assertNotIn('OWN_END_A', b)


class SequentialConversationTests(MemoryLevelBudgetTests):
    def test_recall_follow_up_topic_switch_return_and_restart(self):
        rows = {'video': {'content': 'The first video I picked was about brass refining. She said it would put her to sleep.',
                          'title': 'First video'}}
        memory = self.memory(rows, recall={'cooldown_turns': 3, 'association_enabled': False})
        self.assertEqual(['video'], memory.context('video', include_opening=False) and memory.last_context_refs)
        # Immediate follow-up: the same subject stays available even though the cooldown would block it.
        memory.context('那我当时是什么反应？', include_opening=False,
                       recent=[{'role': 'user', 'content': 'video'},
                               {'role': 'assistant', 'content': '黄铜精炼那个。'}])
        self.assertEqual(['video'], memory.last_context_refs)
        # A topic switch is not carried.
        memory.context('今天吃什么', include_opening=False,
                       recent=[{'role': 'user', 'content': 'video'},
                               {'role': 'assistant', 'content': '黄铜精炼那个。'},
                               {'role': 'user', 'content': '那我当时是什么反应？'},
                               {'role': 'assistant', 'content': '你笑了好久。'}])
        self.assertEqual([], memory.last_context_refs)
        # One turn later the cooldown still applies; the explicit past question gets through.
        memory.rotation.turn += 1
        memory.context('video', include_opening=False)
        self.assertEqual([], memory.last_context_refs)
        memory.context('还记得 video 那次吗', include_opening=False)
        self.assertEqual(['video'], memory.last_context_refs)


class ReuseRemovalTests(MemoryLevelBudgetTests):
    def test_history_with_only_the_opening_still_injects_the_whole_story(self):
        story = '\n\n'.join(LONG_PARAGRAPHS * 3) + '\n\nThe unique ending is the brass key.'
        memory = self.memory({'long': {'content': story, 'title': 'The brass lamp'}},
                             recall={'cooldown_turns': 0, 'association_enabled': False})
        memory.context('brass lamp', include_opening=False,
                       recent=[{'role': 'user', 'content': ('[bucket_id:long]\n' + story)[:120]}])
        text = memory.finish_recall()[-1]['content']
        self.assertIn('[bucket_id:long]\n' + story, text)
        self.assertEqual(['long'], memory.last_context_refs)
        self.assertEqual([], memory.last_recall_debug['reused'])

    def test_history_with_the_whole_text_does_not_suppress_injection(self):
        story = '\n\n'.join(LONG_PARAGRAPHS * 3) + '\n\nThe unique ending is the brass key.'
        memory = self.memory({'long': {'content': story, 'title': 'The brass lamp'}},
                             recall={'cooldown_turns': 0, 'association_enabled': False})
        memory.context('brass lamp', include_opening=False,
                       recent=[{'role': 'assistant', 'content': '[bucket_id:long]\n' + story}])
        text = memory.finish_recall()[-1]['content']
        self.assertIn('[bucket_id:long]\n' + story, text)
        self.assertEqual(['long'], memory.last_context_refs)
        self.assertEqual([], memory.last_recall_debug['reused'])


if __name__ == '__main__':
    unittest.main()
