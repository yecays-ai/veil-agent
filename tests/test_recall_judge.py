"""Ticket 382: the recall relevance judge. Fictional memories, a fake Ombre, a fake judge model and a fake embedder: no network, no paid call.

Covers: it picks from Ombre's hits plus the nearest memories by vector; "none" means no memory and nothing is filled back; hidden,
archived, digested and feel memories never reach it; a timeout, a request error, broken JSON, a letter it was never offered, no embedding
and an exhausted budget all keep ticket 381's recall within the timeout; three failures in a row pause it and it comes back; the switch
off restores 381's recall at once with no model call; nothing written to a memory file; the cost is booked under its own category; wakes
and the short-context path do not use it."""
import array
import json
import random
import re
import sqlite3
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from agent_types import UserError
from memory_agent import Memory, vault_path
from recall_judge import DEFAULTS, JudgeFailed, card_text, load_settings, parse_choice
from test_recall_rotation import FictionalOmbre
from veil_agent import Budget, Character, Store

QUERY = 'my succulent died again'
VECTORS = {
    'cactus': [0.9, 0.1, 0.0], 'junk': [0.0, 1.0, 0.0], 'quake': [0.2, 0.0, 1.0], 'gone': [0.95, 0.05, 0.0],
    'hidden': [0.93, 0.0, 0.0], 'digested': [0.92, 0.0, 0.0], 'plan': [0.91, 0.0, 0.0], 'second': [0.7, 0.3, 0.1]}
ROWS = {
    'cactus': {'content': 'she kills plants, even cacti, and the mint needs a schedule instead of hope', 'title': 'Kills plants'},
    'junk': {'content': 'again again a long filler talk about the weather and nothing else', 'title': 'Weather filler'},
    'quake': {'content': 'a small earthquake, she said she is fine', 'title': 'Small earthquake'},
    'gone': {'content': 'archived succulent story', 'title': 'Archived plants', 'type': 'archived'},
    'hidden': {'content': 'hidden succulent story', 'title': 'Hidden plants', 'type': 'dynamic'},
    'digested': {'content': 'digested old story about dead leaves', 'title': 'Digested plants'},
    'plan': {'content': 'plan about succulents', 'title': 'A plan', 'type': 'plan'},
    'second': {'content': 'she waters the mint on a schedule now', 'title': 'Mint schedule'},
}


class FakeEmbedder:
    def __init__(self, vector=(1.0, 0.0, 0.0), error=None):
        self.vector, self.error, self.calls = list(vector), error, 0

    def embed(self, text):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.vector)


class FakeJudgeModel:
    """Answers like the real judge: JSON with the letters of the cards that contain `pick` words. Records every call."""

    def __init__(self, pick=(), delay=0.0, error=None, answer=None, usage=(900, 7, 0)):
        self.pick, self.delay, self.error, self.answer, self.usage = list(pick), delay, error, answer, usage
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        if self.answer is not None:
            return (self.answer, *self.usage)
        letters = [line[0] for line in messages[1]['content'].splitlines()
                   if re.match(r'^[A-Z]\) ', line) and any(word in line for word in self.pick)]
        return (json.dumps({'ids': letters}), *self.usage)


class RecallJudgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')

    def memory(self, rows=ROWS, judge=True, model=None, embedder=None, settings=None, recall=None, drift=()):
        vault = vault_path(self.store.root, 'fake')
        self.backend = FictionalOmbre(vault / 'dynamic', rows, drift)
        # the files the fake Ombre wrote: make `hidden` hidden and `digested` digested the way Ombre does
        (vault / 'dynamic' / 'hidden.md').write_text(
            '---\nid: hidden\ntitle: Hidden plants\ndont_surface: true\ntype: dynamic\n---\nhidden succulent story', encoding='utf-8')
        (vault / 'dynamic' / 'digested.md').write_text(
            '---\nid: digested\ntitle: Digested plants\ndigested: true\ntype: dynamic\n---\ndigested old story about dead leaves', encoding='utf-8')
        conn = sqlite3.connect(vault / 'embeddings.db')
        conn.execute('CREATE TABLE IF NOT EXISTS embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT NOT NULL, '
                     'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT "")')
        for key, vector in VECTORS.items():
            conn.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)', (key, array.array('f', vector).tobytes(), 'x', ''))
        conn.commit()
        conn.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        memory_cfg = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                      'base_url': 'https://example.invalid/v1', 'recall': recall or {'cooldown_turns': 0, 'random_chance': 0}}
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake', memory_cfg)
        memory = Memory(cfg, self.store, Budget(self.store), Mock(), self.backend)
        memory.rng = random.Random(7)
        self.addCleanup(memory.__exit__, None, None, None)
        self.model = model if model is not None else FakeJudgeModel(pick=['Kills plants'])
        self.embedder = embedder or FakeEmbedder()
        if judge:
            merged = {**DEFAULTS, 'timeout_seconds': 0.4, **(settings or {})}
            memory.attach_recall_judge(cfg, self.model, merged)
            memory.judge.make_embedder = lambda: self.embedder
        return memory

    def recall(self, memory, query=QUERY, **kwargs):
        rows = memory.context(query, include_opening=False, **kwargs)
        return list(memory.last_context_refs), rows[-1]['content']

    # --- what it chooses ---------------------------------------------------------------------------------------
    def test_a_memory_found_only_by_vector_is_recalled_and_the_weak_ombre_hit_is_dropped(self):
        memory = self.memory()
        refs, text = self.recall(memory)
        self.assertEqual(['cactus'], refs)
        self.assertIn('mint needs a schedule', text)
        self.assertNotIn('weather', text)
        self.assertEqual('ok', memory.judge.last['kind'])
        self.assertEqual('chosen', memory.judge.last_choice['status'])
        self.assertIn('cactus', memory.judge.last_choice['picked'])
        self.assertIn('cactus', memory.judge.last_choice['vector'])

    def test_without_the_judge_the_weak_ombre_hit_is_recalled_as_before(self):
        refs, _ = self.recall(self.memory(judge=False))
        self.assertEqual(['junk'], refs)

    def test_it_may_pick_every_candidate_it_judges_relevant(self):
        """Ticket 419: no hard cap of two; the whole-round budget decides how many fit."""
        memory = self.memory(model=FakeJudgeModel(pick=['Kills plants', 'Mint schedule', 'Small earthquake', 'Weather']),
                             recall={'cooldown_turns': 0, 'random_chance': 0, 'dedup': False})
        refs, _ = self.recall(memory)
        self.assertEqual(4, len(refs))

    def test_none_is_an_answer_nothing_is_filled_back_from_ombre(self):
        memory = self.memory(model=FakeJudgeModel(pick=[]))
        refs, text = self.recall(memory)
        self.assertEqual([], refs)
        self.assertIn('No relevant memories were found', text)
        self.assertEqual('empty', memory.judge.last['kind'])
        self.assertEqual('empty', memory.judge.last_choice['status'])
        self.assertEqual([], memory.judge.last_choice['picked'])
        self.assertEqual(0, memory.judge.failures)

    def test_hidden_archived_digested_and_plan_memories_never_reach_the_judge(self):
        memory = self.memory()
        self.recall(memory)
        sent = self.model.calls[0][0][1]['content']
        for banned in ('Archived plants', 'Hidden plants', 'Digested plants', 'A plan'):
            self.assertNotIn(banned, sent)
        self.assertIn('Kills plants', sent)

    def test_the_judge_reads_only_the_card_and_the_prompt_does_not_ask_him_to_talk_about_the_past(self):
        memory = self.memory()
        self.recall(memory)
        system = self.model.calls[0][0][0]['content']
        user = self.model.calls[0][0][1]['content']
        self.assertNotIn('mention', system.lower())
        self.assertNotIn('remember', system.lower())
        self.assertIn('…opening:', user)   # ticket 419 review: opening and ending are both on the card
        self.assertEqual({'json_output': True, 'max_tokens': 200, 'thinking': 'disabled', 'request_timeout': 0.4},
                         {key: value for key, value in self.model.calls[0][1].items()})

    def test_an_immediate_follow_up_keeps_what_the_judge_just_picked_and_a_gap_still_cools_it(self):
        """Ticket 419 review: the second turn is a real follow-up and the judge returns none; the
        previous pick is carried. One turn later the cooldown applies again."""
        memory = self.memory(recall={'cooldown_turns': 3, 'random_chance': 0})
        self.assertEqual(['cactus'], self.recall(memory)[0])
        self.model.answer = '{"ids": []}'
        self.assertEqual(['cactus'], self.recall(memory, '那我当时是什么反应？')[0])
        memory.rotation.turn += 1
        self.model.answer = None
        self.assertEqual([], self.recall(memory)[0])

    # --- failures keep ticket 381's recall, quickly ---------------------------------------------------------------
    def assert_falls_back(self, memory, reason, limit=1.5):
        started = time.monotonic()
        refs, _ = self.recall(memory)
        self.assertLess(time.monotonic() - started, limit)
        self.assertEqual(['junk'], refs)   # today's recall
        self.assertEqual('failed', memory.judge.last['kind'])
        self.assertEqual(reason, memory.judge.last['reason'])

    def test_timeout_keeps_todays_recall_within_the_timeout(self):
        memory = self.memory(model=FakeJudgeModel(pick=['Kills plants'], delay=2.0))
        self.assert_falls_back(memory, 'timeout', limit=1.2)

    def test_request_error_keeps_todays_recall(self):
        self.assert_falls_back(self.memory(model=FakeJudgeModel(error=ConnectionError('network is down'))), 'request_error')

    def test_broken_json_keeps_todays_recall(self):
        self.assert_falls_back(self.memory(model=FakeJudgeModel(answer='sure! {"ids": [')), 'bad_json')

    def test_wrong_shape_or_an_unoffered_letter_keeps_todays_recall(self):
        self.assert_falls_back(self.memory(model=FakeJudgeModel(answer='{"ids": "A"}')), 'bad_format')
        self.assert_falls_back(self.memory(model=FakeJudgeModel(answer='{"ids": ["Z"]}')), 'bad_format')

    def test_no_embedding_keeps_todays_recall(self):
        self.assert_falls_back(self.memory(embedder=FakeEmbedder(error=ConnectionError('no route'))), 'embedding_error')

    def test_a_missing_embeddings_database_keeps_todays_recall(self):
        memory = self.memory()
        (vault_path(self.store.root, 'fake') / 'embeddings.db').unlink()
        self.assert_falls_back(memory, 'vectors_unreadable')

    def test_an_exhausted_budget_keeps_todays_recall_and_sends_nothing(self):
        memory = self.memory()
        real_estimate = memory.budget.estimate

        def refuse_the_judge_only(character, model, tokens, rate):
            if tokens == max(512, len(QUERY.encode('utf-8'))):       # the judge's own embedding request, not Ombre's
                raise UserError('向量请求超过共享预算，未发送。')
            return real_estimate(character, model, tokens, rate)
        memory.budget.estimate = refuse_the_judge_only
        self.assert_falls_back(memory, 'embedding_error')
        self.assertEqual([], self.model.calls)

    def test_the_judge_s_embedding_request_is_booked_like_other_vector_requests(self):
        def estimates(memory):
            self.recall(memory)
            events = [json.loads(line) for line in (self.store.root / 'costs.jsonl').read_text(encoding='utf-8').splitlines()]
            return sum(1 for event in events if event['type'] == 'reserve' and event.get('accounting') == 'embedding-estimate')
        off = estimates(self.memory(judge=False))
        self.setUp()
        on = estimates(self.memory())
        self.assertEqual(off + 1, on)

    def test_a_refused_model_budget_keeps_todays_recall(self):
        memory = self.memory()
        real_reserve = memory.budget.reserve

        def refuse(cfg, **kwargs):
            if kwargs.get('category') == 'recall_judge':
                raise UserError('本次费用上限会超过，未发送。')
            return real_reserve(cfg, **kwargs)
        memory.budget.reserve = refuse
        self.assert_falls_back(memory, 'request_error')
        self.assertEqual([], self.model.calls)

    def test_three_failures_in_a_row_pause_the_judge_and_it_comes_back_later(self):
        memory = self.memory(model=FakeJudgeModel(error=ConnectionError('down')), settings={'breaker_failures': 3, 'breaker_seconds': 60})
        now = [1000.0]
        memory.judge.clock = lambda: now[0]
        for _ in range(3):
            self.assertEqual(['junk'], self.recall(memory)[0])
        self.assertEqual(3, len(self.model.calls))
        self.assertTrue(memory.judge.breaker_open())
        calls = len(self.model.calls)
        started = time.monotonic()
        self.assertEqual(['junk'], self.recall(memory)[0])
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual(calls, len(self.model.calls))   # paused: no request, no delay
        now[0] += 61
        self.model.error = None
        self.model.pick = ['Kills plants']
        self.assertEqual(['cactus'], self.recall(memory)[0])
        self.assertEqual(0, memory.judge.failures)

    # --- the switch ----------------------------------------------------------------------------------------------
    def test_switch_off_restores_the_old_recall_at_once_without_any_request(self):
        memory = self.memory()
        self.assertEqual(['cactus'], self.recall(memory)[0])
        embeds, calls = self.embedder.calls, len(self.model.calls)
        memory.judge.set_enabled(False)
        self.assertEqual(['junk'], self.recall(memory)[0])
        self.assertEqual((embeds, calls), (self.embedder.calls, len(self.model.calls)))
        self.assertFalse(memory.judge.status()['enabled'])
        memory.judge.set_enabled(True)
        self.assertEqual(['cactus'], self.recall(memory)[0])

    def test_the_switch_survives_a_restart_and_the_table_can_turn_it_off_by_default(self):
        memory = self.memory()
        memory.judge.set_enabled(False)
        self.assertFalse(self.memory().judge.enabled())
        memory.judge.set_enabled(True)
        self.assertTrue(self.memory().judge.enabled())
        off = self.memory(settings={'enabled': False})
        (self.store.root / 'fake' / 'recall-judge.json').unlink()
        self.assertFalse(off.judge.enabled())

    def test_the_judge_never_writes_a_memory_file(self):
        memory = self.memory()
        vault = vault_path(self.store.root, 'fake')
        before = {path: path.read_bytes() for path in vault.rglob('*.md')}
        self.recall(memory)
        memory.judge.set_enabled(False)
        self.recall(memory)
        self.assertEqual(before, {path: path.read_bytes() for path in vault.rglob('*.md')})

    # --- cost, scope, helpers --------------------------------------------------------------------------------------
    def test_the_cost_is_booked_under_its_own_category(self):
        memory = self.memory()
        self.recall(memory)
        events = [json.loads(line) for line in (self.store.root / 'costs.jsonl').read_text(encoding='utf-8').splitlines()]
        reserve = [event for event in events if event['type'] == 'reserve' and event.get('category') == 'recall_judge']
        self.assertEqual(1, len(reserve))
        settle = [event for event in events if event['type'] == 'settle' and event['id'] == reserve[0]['id']]
        self.assertEqual('recall-judge', reserve[0]['accounting'])
        self.assertEqual(1, len(settle))
        self.assertGreater(Decimal(settle[0]['amount']), 0)
        self.assertGreater(Decimal(memory.judge.status()['spent_today']), 0)

    # --- the real chat turn already holds the model-settings locks (CodeRabbit, PR 382) -----------------------------
    def test_the_judge_uses_the_lock_free_request_function_while_the_chat_turn_holds_the_model_locks(self):
        import threading
        lock = threading.Lock()
        fake = FakeJudgeModel(pick=['Kills plants'])
        seen = []

        def locked_call(messages, **kwargs):
            with lock:                       # what make_model's call does: wait for the settings locks
                return fake(messages, **kwargs)

        def raw(messages, **kwargs):
            seen.append(kwargs.get('_provider'))
            return fake(messages, **{key: value for key, value in kwargs.items() if key != '_provider'})
        locked_call.raw = raw
        memory = self.memory(model=locked_call)
        with lock:                           # the chat turn holds them for its whole length
            started = time.monotonic()
            refs, _ = self.recall(memory)
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(['cactus'], refs)
        self.assertEqual(1, len(seen))
        self.assertEqual('fake', seen[0]['model'])   # a snapshot of the background provider

    def test_the_request_is_marked_as_dispatched_before_it_is_sent(self):
        memory = self.memory()
        self.recall(memory)
        events = [json.loads(line) for line in (self.store.root / 'costs.jsonl').read_text(encoding='utf-8').splitlines()]
        reserve = next(event for event in events if event['type'] == 'reserve' and event.get('category') == 'recall_judge')
        self.assertIn({'type': 'dispatch', 'id': reserve['id']}, [{'type': e['type'], 'id': e.get('id')} for e in events])

    def test_a_random_recollection_is_not_added_after_the_judge_has_answered(self):
        rows = {key: ROWS[key] for key in ('cactus', 'junk', 'quake', 'second')}
        rows['wander'] = {'content': 'an old wandering memory about nothing', 'title': 'Wander'}
        recall = {'cooldown_turns': 0, 'random_chance': 1}
        empty = self.memory(rows, model=FakeJudgeModel(pick=[]), recall=recall, drift=['wander'])
        self.assertEqual([], self.recall(empty)[0])                  # "none fits" stays none
        self.setUp()
        picked = self.memory(rows, recall=recall, drift=['wander'])
        self.assertEqual(['cactus'], self.recall(picked)[0])          # nothing unjudged is added to what it picked
        self.setUp()
        failed = self.memory(rows, model=FakeJudgeModel(error=ConnectionError('down')), recall=recall, drift=['wander'])
        self.assertIn('wander', self.recall(failed)[0])               # a technical failure keeps today's recall, random one included

    def test_a_wake_with_three_or_fewer_candidates_does_not_use_the_judge_either(self):
        memory = self.memory(recall={'cooldown_turns': 0, 'random_chance': 0, 'rotation_candidates': 3})
        memory.context('a wake text that names a topic', include_opening=False, rotate=True)
        self.assertEqual([], self.model.calls)

    def test_wakes_and_the_short_context_path_do_not_use_the_judge(self):
        memory = self.memory(recall={'cooldown_turns': 0, 'random_chance': 0, 'rotation_candidates': 8})
        memory.context('', include_opening=False, rotate=True)
        self.assertEqual([], self.model.calls)
        memory.assisted_settings = lambda query, recent: {'threshold': 0.99, 'rounds': 2}
        memory.assisted_recall = lambda *args, **kwargs: []
        memory.context('ok', include_opening=False, recent=[{'role': 'user', 'content': 'x'}])
        self.assertEqual([], self.model.calls)

    def test_a_blank_sentence_is_not_judged(self):
        memory = self.memory()
        memory.context('   ', include_opening=False)
        self.assertEqual([], self.model.calls)

    def test_the_app_wires_the_judge_not_the_tests(self):
        self.assertIsNone(self.memory(judge=False).judge)

    def test_settings_fall_back_to_defaults_on_a_bad_value(self):
        self.assertEqual(DEFAULTS, load_settings(None))
        self.assertEqual(DEFAULTS, load_settings({'timeout_seconds': 'fast', 'enabled': 'yes', 'breaker_failures': 0}))
        self.assertEqual(2.0, load_settings({'timeout_seconds': 2})['timeout_seconds'])
        self.assertFalse(load_settings({'enabled': False})['enabled'])

    def test_parse_choice_and_card_text(self):
        letters = {'A': 'one', 'B': 'two', 'C': 'three'}
        self.assertEqual([], parse_choice('{"ids": []}', letters))
        self.assertEqual(['two', 'one', 'three'], parse_choice('{"ids": ["B", "A", "C"]}', letters))
        self.assertEqual(['one'], parse_choice('{"ids": ["A", "A"]}', letters))
        for bad in ('', 'null', '[]', '{"ids": null}', '{"ids": [1]}', '{"ids": ["Q"]}', None):
            with self.assertRaises(JudgeFailed):
                parse_choice(bad, letters)
        raw = "---\nid: x\ntitle: 'A title'\ntags:\n- one\n- 'two words'\n---\nbody\n\n关键词：甲、乙"
        self.assertEqual('A title | 甲、乙 | …opening: body 关键词：甲、乙 | one two words', card_text(raw))
        old = "---\nid: old\nname: 'Old name'\ntags:\n- self\n---\nbody"
        self.assertEqual('Old name | …opening: body | self', card_text(old))

    def test_labels_stay_parseable_past_twenty_six_candidates(self):
        from recall_judge import labels_for
        labels = labels_for(32)
        self.assertEqual(32, len(set(labels)))
        self.assertEqual('A', labels[0])
        self.assertEqual('Z', labels[25])
        self.assertTrue(all(label.isalnum() for label in labels))
        letters = {label: f'm{index}' for index, label in enumerate(labels)}
        self.assertEqual(['m26', 'm27'], parse_choice('{"ids": ["A2", "B2"]}', letters))


if __name__ == '__main__':
    unittest.main()
