"""Ticket 353: automatic recall cooldown, same-event dedup, excerpts, rotation and random recall.

Deterministic: a fake Ombre backend over fictional memories, a seeded random source, no network.
"""
import array
import json
import random
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from veil.brain.memory_agent import SEARCH_SPARE_HITS, Memory, vault_path
from veil.brain.memory_sidecar import Sidecar
from veil.brain.recall.recall_rotation import (Cooldown, asks_about_past, directory_line, excerpt, experience_chain,
                             front_matter, rotate, settings)
from veil.core.veil_agent import Budget, Character, Store, memory_ref_fields


class FictionalOmbre:
    """Search returns the rows whose words overlap the query, best first, like Ombre's id block."""

    def __init__(self, vault, rows, drift=()):
        self.vault, self.rows, self.drift, self.calls = vault, rows, list(drift), []
        vault.mkdir(parents=True, exist_ok=True)
        for key, row in rows.items():
            links = ''.join(f'- target_bucket_id: {target}\n  type: {kind}\n  status: active\n'
                            for target, kind in row.get('links', ()))
            (vault / f'{key}.md').write_text(
                f'---\nid: {key}\ntitle: {row.get("title", "Fictional " + key)}\n'
                f"created: '{row.get('created', '2026-10-20T10:00:00')}'\n"
                f'importance: {row.get("importance", 5)}\n'
                + (f'relation_links:\n{links}' if links else '')
                + f'type: {row.get("type", "dynamic")}\n---\n{row["content"]}', encoding='utf-8')

    def render(self, keys):
        return '\n'.join(f'[bucket_id:{key}]\n{self.rows[key]["content"]}' for key in keys)

    def call(self, name, args):
        self.calls.append((name, args))
        if name == 'breath':
            return self.render(sorted(self.rows, key=lambda k: -self.rows[k].get('importance', 5)))
        if name == 'feel':
            return ''
        if name == 'breath_search':
            words = set(args['query'].lower().split())
            scored = sorted(((len(words & set(row['content'].lower().split())), key)
                             for key, row in self.rows.items()), key=lambda item: (-item[0], item[1]))
            hits = [key for score, key in scored if score][:args.get('max_results') or 3]
            text = self.render(hits)
            drift = [key for key in self.drift if key not in hits]
            if len(hits) < 3 and drift:
                text += ('\n=== 忽然想起来（非检索命中） ===\n' + '\n---\n'.join(
                    f'[联想浮现·非检索命中] [bucket_id:{key}]\n{self.rows[key]["content"]}' for key in drift))
            return text + '\n=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': hits}) + '\n```'
        raise AssertionError(name)


ROWS = {
    'garden': {'content': 'garden roses planted together in spring', 'importance': 8},
    'garden_again': {'content': 'garden roses watered the next morning',
                     'links': [('garden', 'same_event')], 'importance': 8},
    'train': {'content': 'train trip to the coast with a broken window'},
    'cooking': {'content': 'cooking dumplings failed then laughed'},
    'movie': {'content': 'movie night fell asleep halfway'},
    'long': {'content': 'letter ' + 'a quiet long fictional paragraph about the lake. ' * 60, 'title': 'Lake letter',
             'created': '2026-10-21T08:00:00'},
    'old': {'content': 'old fictional memory about a lost umbrella'},
}


class RecallRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')

    def memory(self, rows=ROWS, drift=(), recall=None, vectors=None):
        vault = vault_path(self.store.root, 'fake')
        self.backend = FictionalOmbre(vault / 'dynamic', rows, drift)
        if vectors:
            conn = sqlite3.connect(vault / 'embeddings.db')
            conn.execute('CREATE TABLE IF NOT EXISTS embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT NOT NULL, '
                         'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT "")')
            for key, vector in vectors.items():
                conn.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)',
                             (key, array.array('f', vector).tobytes(), 'x', ''))
            conn.commit()
            conn.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        memory_cfg = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                      'base_url': 'https://example.invalid/v1'}
        if recall is not None:
            memory_cfg['recall'] = recall
        cfg = Character('fake', 'Example', 'fictional persona', 20, 1024, provider, 'fake', memory_cfg)
        memory = Memory(cfg, self.store, Budget(self.store), Mock(), self.backend)
        memory.rng = random.Random(7)
        self.addCleanup(memory.__exit__, None, None, None)
        return memory

    @staticmethod
    def recall(memory, query, **kwargs):
        rows = memory.context(query, include_opening=False, **kwargs)
        return rows[-1]['content'], list(memory.last_context_refs)

    def test_an_archived_hit_does_not_take_a_visible_slot(self):
        """Ticket 381: Ombre returns archived memories too; they must be dropped before the visible hits are chosen.
        Ticket 419: there is no three-slot cap any more, so every visible hit is a candidate."""
        rows = {'gone': {'content': 'alpha beta gamma delta epsilon', 'type': 'archived'},
                'a1': {'content': 'alpha beta gamma delta'}, 'a2': {'content': 'alpha beta gamma'},
                'a3': {'content': 'alpha beta'}, 'a4': {'content': 'alpha'}}
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
        _, refs = self.recall(memory, 'alpha beta gamma delta epsilon')
        self.assertEqual(['a1', 'a2', 'a3', 'a4'], refs)
        asked = [args['max_results'] for name, args in self.backend.calls if name == 'breath_search']
        self.assertEqual([3 + SEARCH_SPARE_HITS], asked)

    def test_with_fewer_than_three_visible_hits_nothing_is_padded(self):
        rows = {'gone': {'content': 'alpha beta gamma', 'type': 'archived'},
                'gone2': {'content': 'alpha beta', 'type': 'archived'},
                'a1': {'content': 'alpha'}, 'other': {'content': 'unrelated words only'}}
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
        _, refs = self.recall(memory, 'alpha beta gamma')
        self.assertEqual(['a1'], refs)

    def test_a_search_that_only_finds_archived_memories_recalls_nothing(self):
        rows = {'gone': {'content': 'alpha beta gamma', 'type': 'archived'}, 'other': {'content': 'unrelated words only'}}
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
        _, refs = self.recall(memory, 'alpha beta gamma')
        self.assertEqual([], refs)

    def test_a_memory_is_not_shown_again_during_its_cooldown_and_the_slot_stays_empty(self):
        memory = self.memory(recall={'cooldown_turns': 2, 'association_enabled': False})
        self.assertEqual(self.recall(memory, 'train trip')[1], ['train'])
        self.assertEqual(self.recall(memory, 'train window')[1], [])
        self.assertEqual(self.recall(memory, 'train coast')[1], [])
        self.assertEqual(self.recall(memory, 'train again')[1], ['train'])

    def test_asking_about_the_past_gets_through_the_cooldown(self):
        memory = self.memory(recall={'cooldown_turns': 5, 'association_enabled': False})
        self.assertEqual(self.recall(memory, 'train trip')[1], ['train'])
        self.assertEqual(self.recall(memory, 'train window')[1], [])           # unprompted: cooled
        self.assertEqual(self.recall(memory, '还记得 train 那次吗')[1], ['train'])  # she asks: shown
        self.assertEqual(self.recall(memory, 'remember train trip')[1], ['train'])

    def test_no_relevant_memory_returns_none(self):
        memory = self.memory(recall={'association_enabled': False})
        text, refs = self.recall(memory, 'quantum spreadsheets')
        self.assertEqual(refs, [])
        self.assertIn('No relevant memories', text)

    def test_cooldown_survives_a_restart(self):
        memory = self.memory(recall={'cooldown_turns': 3, 'association_enabled': False})
        self.recall(memory, 'cooking dumplings')
        # Before delivery is confirmed, a crash/restart must not persist undelivered cooldown state.
        crashed = self.memory(recall={'cooldown_turns': 3, 'association_enabled': False})
        self.assertEqual(self.recall(crashed, 'cooking dumplings')[1], ['cooking'])
        # Once delivery is confirmed, cooldown persists across restarts.
        memory.confirm_delivery()
        restarted = self.memory(recall={'cooldown_turns': 3, 'association_enabled': False})
        self.assertEqual(self.recall(restarted, 'cooking dumplings')[1], [])
        saved = self.store.read('fake/memory/_agent/recall-rotation.jsonl')[-1]
        # IDs and turn numbers only, no memory text (ticket 422 adds which association surfaced last).
        self.assertLessEqual(set(saved), {'turn', 'last', 'time', 'association', 'leisure_association'})
        self.assertNotIn('dumplings', json.dumps(saved))

    def test_rollback_gives_back_the_cooldown_of_a_turn_that_delivered_nothing(self):
        memory = self.memory(recall={'cooldown_turns': 3, 'association_enabled': False})
        self.assertEqual(self.recall(memory, 'cooking dumplings')[1], ['cooking'])
        memory.rollback_recall()  # ticket 380: that turn produced no reply
        self.assertEqual(self.recall(memory, 'cooking dumplings')[1], ['cooking'])
        memory.confirm_delivery()  # this retry actually delivered a reply
        restarted = self.memory(recall={'cooldown_turns': 3, 'association_enabled': False})
        memory.rollback_recall()
        memory.rollback_recall()  # nothing left to undo: harmless
        self.assertEqual(self.recall(restarted, 'cooking dumplings')[1], [])  # the delivered turn still counts

    def test_rollback_restores_the_earlier_mark_not_nothing(self):
        memory = self.memory(recall={'cooldown_turns': 1, 'association_enabled': False})
        self.recall(memory, 'cooking dumplings')
        first = dict(memory.rotation.last)
        self.recall(memory, 'remember cooking dumplings')  # she asks: shown again, mark moves forward
        memory.rollback_recall()
        self.assertEqual(memory.rotation.last, first)

    def test_release_lets_a_regenerated_turn_recall_again(self):
        memory = self.memory(recall={'cooldown_turns': 3, 'association_enabled': False})
        self.recall(memory, 'cooking dumplings')
        self.assertEqual(self.recall(memory, 'cooking dumplings')[1], [])
        memory.release_recall(['cooking'])
        self.assertEqual(self.recall(memory, 'cooking dumplings')[1], ['cooking'])

    def test_same_event_by_link_shows_only_one(self):
        memory = self.memory(recall={'association_enabled': False})
        refs = self.recall(memory, 'garden roses')[1]
        self.assertEqual(refs, ['garden'])
        self.assertEqual(memory.last_recall_skipped['same_event'], 1)

    def test_same_event_by_stored_vector_similarity(self):
        rows = {'a': {'content': 'picnic by the river'}, 'b': {'content': 'picnic by the river again'},
                'c': {'content': 'picnic in the rain'}}
        memory = self.memory(rows, recall={'association_enabled': False},
                             vectors={'a': [1, 0, 0], 'b': [0.99, 0.05, 0], 'c': [0, 1, 0]})
        self.assertEqual(self.recall(memory, 'picnic river')[1], ['a', 'c'])

    def test_dedup_can_be_turned_off(self):
        memory = self.memory(recall={'dedup': False, 'association_enabled': False})
        self.assertEqual(self.recall(memory, 'garden roses')[1], ['garden', 'garden_again'])

    def test_long_memory_is_sent_whole_by_default_and_only_a_legacy_limit_cuts(self):
        """Ticket 419: the 600-character opening cut is gone; a memory is read by the request budget, not a
        per-memory character cap. An old config that still sets excerpt_chars keeps cutting, but on a boundary."""
        memory = self.memory(recall={'association_enabled': False})
        text, refs = self.recall(memory, 'lake letter')
        self.assertEqual(refs, ['long'])
        self.assertIn(ROWS['long']['content'], text)           # the whole body, not its opening
        self.assertNotIn('only its beginning is shown', text)
        limit = len(ROWS['long']['content']) // 2
        legacy = self.memory(recall={'association_enabled': False, 'cooldown_turns': 0, 'excerpt_chars': limit})
        cut, refs = self.recall(legacy, 'lake letter')
        self.assertEqual(refs, ['long'])
        self.assertIn('memory_read with memory_id "long"', cut)
        self.assertLess(len(cut), len(ROWS['long']['content']) + 300)
        stored = (vault_path(self.store.root, 'fake') / 'dynamic' / 'long.md').read_text(encoding='utf-8')
        self.assertIn(ROWS['long']['content'], stored)  # the memory itself is untouched

    def test_short_context_assisted_message_still_finds_its_topic(self):
        # A two-word reply finds nothing alone; with the last rounds it reaches the train memory.
        from test_recall_relevance import FakeEmbedder
        memory = self.memory(recall={'association_enabled': False}, vectors={'train': [1, 0], 'movie': [0, 1]})
        memory.cfg.memory['recall_context'] = {'enabled': True, 'min_similarity': 0.5}
        memory._embedder = FakeEmbedder((1.0, 0.0))
        recent = [{'role': 'user', 'content': 'remember the train trip'},
                  {'role': 'assistant', 'content': 'the coast one?'}]
        self.assertEqual(self.recall(memory, 'yes that', recent=recent)[1], ['train'])
        self.assertEqual(memory.last_context_kinds, {'train': 'context'})

    def test_a_weak_association_is_labelled_and_can_be_turned_off(self):
        """Ticket 422: a meaningful old memory may still reach a light turn, but it is scored
        (importance, time since it last surfaced, room in this moment) rather than drawn by chance."""
        rows = dict(ROWS)
        rows['old'] = {'content': 'old fictional memory about a lost umbrella', 'importance': 9}
        memory = self.memory(rows, drift=['old'],
                             recall={'association_enabled': True, 'cooldown_turns': 0})
        text, refs = self.recall(memory, 'movie night')
        self.assertEqual(refs, ['movie', 'old'])
        self.assertEqual(memory.last_context_kinds, {'movie': 'search', 'old': 'association'})
        self.assertIn('came back to you by itself', text)
        self.assertEqual(memory.last_recall_debug['natural_association'], ['old'])
        off = self.memory(rows, drift=['old'],
                          recall={'association_enabled': False, 'cooldown_turns': 0})
        self.assertEqual(self.recall(off, 'movie night')[1], ['movie'])

    def test_autonomous_wakes_no_longer_cap_at_three(self):
        """Ticket 419: a wake sees every candidate the pool offers, ordered by rotation, not the first three."""
        rows = {f'm{i}': {'content': f'wake note {i}'} for i in range(6)}
        memory = self.memory(rows, recall={'cooldown_turns': 0, 'association_enabled': False})
        seen = [tuple(self.recall(memory, 'wake note', rotate=True)[1]) for _ in range(4)]
        self.assertTrue(all(set(item) == set(rows) for item in seen))
        self.assertTrue(all(kind == 'rotation' for kind in memory.last_context_kinds.values()))

    def test_opening_keeps_higher_importance_first_and_shows_all_equal_ones(self):
        """Ticket 419: the opening group is budgeted, not capped at three."""
        rows = {'top': {'content': 'top', 'importance': 9},
                **{f'e{i}': {'content': f'equal {i}', 'importance': 5} for i in range(4)}}
        memory = self.memory(rows, recall={'association_enabled': False})
        first = memory.context('nothing', include_opening=True)
        first_refs = list(memory.last_context_refs)
        memory.refresh_opening()
        memory.context('nothing', include_opening=True)
        second_refs = list(memory.last_context_refs)
        self.assertEqual(first_refs[0], 'top')
        self.assertEqual(second_refs[0], 'top')
        self.assertEqual(set(first_refs[1:]), {f'e{i}' for i in range(4)})
        self.assertEqual(set(second_refs[1:]), {f'e{i}' for i in range(4)})
        self.assertIn('Opening memories', first[0]['content'])

    def test_all_off_restores_todays_selection(self):
        memory = self.memory(recall={'enabled': False}, drift=['old'])
        for _ in range(3):
            self.assertEqual(self.recall(memory, 'garden roses')[1], ['garden', 'garden_again'])

    def test_thirty_turn_fictional_scenario_spreads_recall(self):
        """Same 30 fictional turns: today's behaviour versus ticket 353 defaults."""
        topics = ['garden roses', 'train trip', 'cooking dumplings', 'movie night', 'garden', 'train']

        def run(recall):
            memory = self.memory(recall=recall)
            counts, chars, empty = {}, 0, 0
            for turn in range(30):
                text, refs = self.recall(memory, topics[turn % len(topics)])
                chars += len(text)
                empty += not refs
                for key in refs:
                    counts[key] = counts.get(key, 0) + 1
            return counts, chars

        before, before_chars = run({'enabled': False})
        # Ticket 365's experience directory adds a line to linked memories, which is not what this compares.
        # Ticket 422's weak association is measured by its own tests; keep this comparison about rotation.
        after, after_chars = run({'directory': False, 'association_enabled': False})
        top_before = max(before.values()) / sum(before.values())
        top_after = max(after.values()) / sum(after.values())
        self.assertLess(top_after, top_before + 1e-9)
        self.assertLess(after_chars, before_chars)

    STORY = {
        'story_a': {'content': 'domino night we stacked tiles until dawn', 'title': 'Domino night',
                    'created': '2026-10-25T21:00:00'},
        'story_b': {'content': 'tiles fell so she wrote me a letter about it', 'title': 'The letter',
                    'created': '2026-10-28T09:00:00'},
        'story_c': {'content': 'she woke up and found the finished spiral', 'title': 'Waking up to the spiral',
                    'created': '2026-10-31T08:00:00'},
        'lonely': {'content': 'a lone fictional memory about teapots', 'title': 'Teapots'},
    }

    def story(self, **kwargs):
        memory = self.memory(rows=self.STORY, **kwargs)
        sidecar = Sidecar(self.store, 'fake', memory.budget.clock)
        sidecar.link('story_b', 'story_a')
        sidecar.link('story_c', 'story_b')
        return memory

    def test_a_stage_of_a_longer_experience_comes_with_the_others_dates_and_titles(self):
        """Ticket 365: Peter's own link_to chain (sidecar) is listed, oldest first, with this stage marked."""
        memory = self.story(recall={'association_enabled': False})
        text, refs = self.recall(memory, 'domino stacked')
        self.assertEqual(refs, ['story_a'])
        self.assertIn('10-25 Domino night (this one) → 10-28 The letter [story_b] → '
                      '10-31 Waking up to the spiral [story_c]', text)
        self.assertNotIn('so she wrote me a letter', text)      # titles and dates only, not the other stages' text

    def test_stages_written_on_one_day_are_listed_in_the_order_they_happened(self):
        """Ids are random, so the directory used to put one day's stages in id order once created was cut to the date."""
        rows = {
            'zz_first': {'content': 'we started the paper crane on a rainy morning', 'title': 'Crane begins',
                         'created': '2026-11-04T06:02:20+08:00'},
            'mm_second': {'content': 'the crane wing tore so we taped it', 'title': 'Crane wing tears',
                          'created': '2026-11-04T07:57:10+08:00'},
            'aa_third': {'content': 'the crane finally stood on the shelf', 'title': 'Crane stands',
                         'created': '2026-11-04T08:20:58+08:00'},
        }
        memory = self.memory(rows=rows, recall={'association_enabled': False})
        sidecar = Sidecar(self.store, 'fake', memory.budget.clock)
        sidecar.link('mm_second', 'zz_first')
        sidecar.link('aa_third', 'mm_second')
        text, refs = self.recall(memory, 'crane rainy morning started')
        self.assertEqual(refs[0], 'zz_first')                   # ticket 369: other stages that are hits may join
        self.assertEqual(len(refs), 3)                          # ticket 419: no stage-count cap
        self.assertIn('Crane begins (this one) → 11-04 Crane wing tears [mm_second] → 11-04 Crane stands [aa_third]', text)

    def test_the_stage_dedup_blocks_still_shows_up_in_the_directory(self):
        memory = self.story(recall={'association_enabled': False})
        text, refs = self.recall(memory, 'tiles domino letter fell')
        self.assertEqual(len(refs), 2)                          # ticket 369: both stages that were hits come
        self.assertEqual(memory.last_recall_skipped['same_event'], 0)
        self.assertIn('The letter', text)
        self.assertIn('Domino night', text)

    def test_one_experience_can_supply_all_its_hit_stages(self):
        """Ticket 419: linked stages are not repeats; the budget, not a stage-slot count, decides how many fit."""
        memory = self.story(recall={'association_enabled': False, 'stage_slots': 1})
        text, refs = self.recall(memory, 'tiles domino letter fell')
        self.assertEqual(len(refs), 2)
        self.assertEqual(memory.last_recall_skipped['stage_cap'], 0)
        self.assertIn('Domino night', text)

    def test_continuation_chain_keeps_every_hit_stage(self):
        """Ticket 369 review: A <- B <- C joined only by Ombre continuation links is one experience of three
        stages; ticket 419 keeps all three when they are all hits."""
        rows = {
            'ca': {'content': 'kite flying first attempt windy hill', 'title': 'Kite one', 'created': '2026-11-01T08:00:00'},
            'cb': {'content': 'kite flying second attempt string snapped', 'title': 'Kite two',
                   'created': '2026-11-02T08:00:00', 'links': [('ca', 'continuation_of')]},
            'cc': {'content': 'kite flying third attempt finally soared', 'title': 'Kite three',
                   'created': '2026-11-03T08:00:00', 'links': [('cb', 'continuation_of')]},
        }
        memory = self.memory(rows=rows, recall={'association_enabled': False})
        text, refs = self.recall(memory, 'kite flying attempt')
        self.assertEqual(len(refs), 3)
        self.assertEqual(memory.last_recall_skipped['stage_cap'], 0)
        self.assertEqual(memory.last_recall_skipped['same_event'], 0)

    def test_ombre_event_links_make_a_directory_too_and_a_lone_memory_gets_none(self):
        memory = self.memory(recall={'association_enabled': False})
        text, refs = self.recall(memory, 'garden roses planted spring')
        self.assertEqual(refs, ['garden'])
        self.assertIn('(this one)', text)
        self.assertNotIn('one stage of a longer experience', self.recall(self.story(recall={'association_enabled': False}),
                                                                          'teapots lone')[0])

    def test_experience_stages_gives_the_other_stages_both_ways_and_skips_deleted_ones(self):
        """Ticket 368: the tidying asks for the same chain the recall directory uses."""
        memory = self.story(recall={'association_enabled': False})
        self.assertEqual(sorted(memory.experience_stages('story_a')), ['story_b', 'story_c'])
        self.assertEqual(sorted(memory.experience_stages('story_c')), ['story_a', 'story_b'])
        memory.state['entries']['story_b'] = {'title': 'The letter', 'time': 'x', 'deleted': True, 'text': ''}
        self.assertNotIn('story_b', memory.experience_stages('story_a'))
        self.assertEqual(memory.experience_stages('nobody'), [])

    def test_recently_created_finds_memories_by_their_creation_time_newest_first(self):
        """Ticket 368: the tidying finds what Peter saved lately even where the sidecar has no note of it."""
        from datetime import datetime, timezone
        memory = self.story(recall={'association_enabled': False})
        found = memory.recently_created(datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertEqual(found, ['story_c', 'story_b'])           # 10-31 then 10-28; 10-25 is before the cut
        self.assertEqual(memory.recently_created(datetime(2027, 1, 1, tzinfo=timezone.utc)), [])

    def test_archived_or_missing_stages_are_not_listed(self):
        memory = self.story(recall={'association_enabled': False})
        path = self.backend.vault / 'story_b.md'
        path.write_text(path.read_text(encoding='utf-8').replace('type: dynamic', 'type: archived'), encoding='utf-8')
        (self.backend.vault / 'story_c.md').unlink()
        text, _ = self.recall(memory, 'domino stacked')
        self.assertNotIn('The letter', text)
        self.assertNotIn('spiral', text)
        self.assertNotIn('one stage of a longer experience', text)   # nothing left to list

    def test_a_stage_deleted_on_the_memory_page_is_not_listed(self):
        memory = self.story(recall={'association_enabled': False})
        memory.state['entries']['story_b'] = {'title': 'The letter', 'time': 'x', 'deleted': True, 'text': ''}
        text, _ = self.recall(memory, 'domino stacked')
        self.assertNotIn('The letter', text)
        self.assertIn('Waking up to the spiral', text)     # the other stage is still there

    def test_the_directory_can_be_turned_off(self):
        memory = self.story(recall={'association_enabled': False, 'directory': False})
        text, refs = self.recall(memory, 'domino stacked')
        self.assertEqual(refs, ['story_a'])
        self.assertNotIn('one stage of a longer experience', text)
        self.assertFalse(settings({'recall': {'enabled': False}})['directory'])

    def test_a_damaged_sidecar_only_means_no_directory(self):
        memory = self.story(recall={'association_enabled': False})
        (self.store.root / 'fake' / 'memory-sidecar.jsonl').write_text('{not json', encoding='utf-8')
        text, refs = self.recall(memory, 'domino stacked')
        self.assertEqual(refs, ['story_a'])
        self.assertNotIn('one stage of a longer experience', text)


class HelperTests(unittest.TestCase):
    def test_experience_chain_and_directory_line(self):
        graph = {'a': {'b'}, 'b': {'a', 'c'}, 'c': {'b'}, 'x': set()}
        self.assertEqual(experience_chain('a', lambda key: graph.get(key, set())), ['a', 'b', 'c'])
        self.assertEqual(experience_chain('a', lambda key: graph.get(key, set()), limit=2), ['a', 'b'])
        self.assertEqual(experience_chain('x', lambda key: graph.get(key, set())), ['x'])
        entries = [(str(i), f'2026-10-{10 + i}', f't{i}') for i in range(9)]
        line = directory_line('4', entries, 4)
        self.assertIn('…10-12 t2 [2] → 10-13 t3 [3] → 10-14 t4 (this one) → 10-15 t5 [5] → …', line)
        self.assertEqual(directory_line('4', entries[:1], 4), '')
        self.assertIn('2025-12-30 old [o]', directory_line('n', [('o', '2025-12-30', 'old'), ('n', '2026-01-02', 'new')], 6))

    def test_stages_of_one_day_keep_the_order_they_happened_in(self):
        # Ids are random, so before whole timestamps were passed in, three stages written on one day came out shuffled.
        entries = [('zzz', '2026-11-04T08:20:58+08:00', 'late'), ('aaa', '2026-11-04T06:02:20+08:00', 'early'),
                   ('mmm', '2026-11-04T07:57:10+08:00', 'middle')]
        line = directory_line('mmm', entries, 6)
        self.assertLess(line.index('early'), line.index('middle'))
        self.assertLess(line.index('middle'), line.index('late'))
        self.assertIn('11-04 middle (this one)', line)

    def test_directory_still_copes_with_bare_dates_and_odd_values(self):
        line = directory_line('b', [('a', '2026-11-04', 'a'), ('b', 'not a date', 'b'), ('c', '', 'c')], 6)
        self.assertIn('b (this one)', line)

    def test_settings_defaults_and_bad_values(self):
        self.assertEqual(settings({})['cooldown_turns'], 3)
        self.assertEqual(settings({'recall': {'cooldown_turns': -1, 'random_chance': 3}})['random_chance'], 0.1)
        self.assertEqual(settings({'recall': {'enabled': False}})['random_chance'], 0.0)

    def test_front_matter_reads_links_and_importance(self):
        raw = ('---\nid: x\nimportance: 7\nrelation_links:\n- target_bucket_id: y\n  type: same_event\n'
               '  status: active\n- target_bucket_id: z\n  type: related_to\n  status: detached\ntags:\n- a\n---\nbody')
        meta = front_matter(raw)
        self.assertEqual(meta['importance'], 7)
        self.assertEqual(meta['links'], [('y', 'same_event')])

    def test_rotate_and_excerpt(self):
        metas = {'a': {'importance': 5}, 'b': {'importance': 5}, 'c': {'importance': 9}}
        order = rotate([('a', ''), ('b', ''), ('c', '')], metas, {'a': 3})
        self.assertEqual([key for key, _ in order], ['c', 'b', 'a'])
        self.assertEqual(excerpt('k', '[bucket_id:k]\nshort', {}, 600), '[bucket_id:k]\nshort')

    def test_past_reference_detector(self):
        for text in ('你还记得去年那张贺卡吗', '按钮那次是你自己修好的吧', 'do you remember it', 'last time we talked'):
            self.assertTrue(asks_about_past(text), text)
        for text in ('今天好热', '我的多肉长高了', '晚安', 'hello'):
            self.assertFalse(asks_about_past(text), text)

    def test_cooldown_ignores_damaged_state(self):
        state = Cooldown({'turn': 'x', 'last': {'a': 99, 'b': 'y'}})
        self.assertEqual(state.snapshot(), {'turn': 0, 'last': {},
                                            'association': {'key': '', 'turn': -1, 'repeat': 0},
                                            'leisure_association': {'key': '', 'turn': -1, 'repeat': 0}})

    def test_reply_row_carries_kinds_only_for_its_refs(self):
        chat = Mock(turn_memory_refs=['a'], turn_memory_kinds={'a': 'random', 'b': 'search'})
        self.assertEqual(memory_ref_fields(chat), {'memory_refs': ['a'], 'memory_ref_kinds': {'a': 'random'}})


if __name__ == '__main__':
    unittest.main()
