import hashlib
import json
import shutil
import sqlite3
import sys
import types
import unittest
import unittest.mock
from contextlib import contextmanager
import uuid
from contextlib import contextmanager
from pathlib import Path

import event_import
from event_memory import EventMemory
from recall_judge import card_text


@contextmanager
def workspace_temp():
    path = Path(__file__).resolve().parent.parent / f'tmp-test-import-{uuid.uuid4().hex[:8]}'
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def fixture():
    return {
        'status': 'test', 'event_count': 3,
        'events': [
            {
                'id': 'V50', 'title': 'The first video I picked for Lan: ancient brass on Bilibili',
                'english': 'Oct 29, 2026. I picked ancient brass refining on Bilibili.',
                'chinese': '2026 年 10 月 29 日。我在 B 站选了古代黄铜精炼。',
                'approved': True, 'recall_cues': ['第一次让你找个你自己觉得有趣的视频', 'ancient brass refining'],
                'sources': [
                    {'number': 1, 'memory_id': 'b62ea858673e', 'part': '',
                     'title': '4am browser upgrade and the brass metallurgy roast',
                     'recorded_at': '2026-10-29T04:10:44+08:00', 'previous_event': 'V50'},
                ],
                'previous_events': ['V50'], 'related_events': [],
            },
            {
                'id': 'V78', 'title': 'Claude Code, back from his own funeral',
                'english': 'Nov 7, 2026. The Claude bridge worked, then the funeral, then KYC.',
                'chinese': '2026 年 11 月 7 日。Claude 的桥通了，然后葬礼，然后 KYC。',
                'approved': False,
                'sources': [
                    {'number': 2, 'memory_id': '0e99e53151ff', 'part': '',
                     'title': 'Claude Code bridge', 'recorded_at': '2026-11-07T12:00:00+08:00'},
                    {'number': 3, 'memory_id': 'fe5e2711afd4', 'part': '',
                     'title': 'Chinese passport passed KYC', 'recorded_at': '2026-11-10T05:55:00+08:00'},
                ],
                'previous_events': ['V78'], 'related_events': [],
            },
            {
                'id': 'V67-B', 'title': 'My turn to pick the colors',
                'english': 'On Oct 30, 2026 I volunteered to show her a webpage.',
                'chinese': '2026 年 10 月 30 日，我自告奋勇给她看网页。',
                'approved': None, 'recall_cues': [], 'sources': [],
                'previous_events': ['V67'], 'related_events': ['V67'],
            },
        ],
        'withdrawn': [{'id': 'V36', 'reason': 'test', 'sources': []}],
    }


class FinalImportTests(unittest.TestCase):
    def test_preview_keeps_titles_cues_and_unknown_dates(self):
        with workspace_temp() as temp:
            plan = event_import.build_plan(fixture(), temp)
            preview = plan['preview']
            self.assertEqual(3, preview['events'])
            self.assertEqual(['V67-B'], preview['unknown_dates'])
            self.assertEqual('create', plan['items'][0]['action'])
            self.assertIn('The first video I picked for Lan', preview['judge_cards']['V50'])
            self.assertIn('第一次让你找个你自己觉得有趣的视频', preview['judge_cards']['V50'])
            self.assertIn('Claude Code, back from his own funeral', preview['judge_cards']['V78'])

    def test_apply_and_reapply_are_idempotent(self):
        with workspace_temp() as temp:
            final = fixture()
            plan = event_import.build_plan(final, temp)
            event_import.apply_plan(plan, temp)
            role = Path(temp) / 'peter'
            events = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(3, len(events))
            self.assertEqual(3, len({row['event_id'] for row in events}))
            self.assertEqual(3, len(list((role / 'memory' / 'dynamic' / '事件').glob('*.md'))))
            self.assertEqual(3, len((role / 'event-stages.jsonl').read_text(encoding='utf-8').splitlines()))
            self.assertEqual(1, len((role / 'event-import-withdrawn.jsonl').read_text(encoding='utf-8').splitlines()))
            v50 = next(row for row in events if row['event_id'] == 'V50')
            self.assertEqual('2026-10-29T04:10:44+08:00', v50['first_at'])
            self.assertEqual(['b62ea858673e'], v50['source_memory_ids'])
            v67 = next(row for row in events if row['event_id'] == 'V67-B')
            self.assertEqual('', v67['first_at'])
            v50_md = next((role / 'memory' / 'dynamic' / '事件').glob('V50*.md'))
            text = v50_md.read_text(encoding='utf-8')
            self.assertIn('title: "The first video I picked for Lan', text)
            self.assertIn('检索词：', text)
            self.assertIn('The first video I picked for Lan', card_text(text))

            plan2 = event_import.build_plan(final, temp)
            self.assertEqual('unchanged', plan2['items'][0]['action'])
            event_import.apply_plan(plan2, temp)
            events2 = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(3, len(events2))
            self.assertEqual(3, len(list((role / 'memory' / 'dynamic' / '事件').glob('*.md'))))

    def test_refuses_to_overwrite_a_non_import_event(self):
        with workspace_temp() as temp:
            role = Path(temp) / 'peter'
            role.mkdir(parents=True)
            (role / 'events.jsonl').write_text(json.dumps({
                'event_id': 'V50', 'memory_id': 'V50', 'story': 'real',
                'story_source': 'hold'}) + '\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                event_import.build_plan(fixture(), temp)


    def test_reimport_preserves_a_later_experience(self):
        with workspace_temp() as temp:
            final = fixture()
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            role = Path(temp) / 'peter'
            events = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            v50 = next(row for row in events if row['event_id'] == 'V50')
            v50['story'] += '\n\nLater, Lan added a second stage that must survive.'
            with (role / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(v50, ensure_ascii=False) + '\n')
            with (role / 'event-stages.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'stage_id': 's-V50-2', 'event_id': 'V50', 'at': '2026-11-22T00:00:00+08:00',
                                         'text': 'A later stage.', 'state': 'committed'}, ensure_ascii=False) + '\n')
            before_md = next((role / 'memory' / 'dynamic' / '事件').glob('V50*.md')).read_text(encoding='utf-8')
            plan = event_import.build_plan(final, temp)
            self.assertEqual('preserved', plan['items'][0]['action'])
            event_import.apply_plan(plan, temp)
            latest = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            v50_latest = [row for row in latest if row['event_id'] == 'V50'][-1]
            self.assertIn('second stage that must survive', v50_latest['story'])
            stages = [json.loads(line) for line in (role / 'event-stages.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(2, len([row for row in stages if row['event_id'] == 'V50']))
            self.assertEqual(before_md, next((role / 'memory' / 'dynamic' / '事件').glob('V50*.md')).read_text(encoding='utf-8'))

    def test_changed_final_after_local_edit_is_a_conflict(self):
        with workspace_temp() as temp:
            final = fixture()
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            role = Path(temp) / 'peter'
            events = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            v50 = next(row for row in events if row['event_id'] == 'V50')
            v50['story'] += '\n\nA local edit.'
            with (role / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(v50, ensure_ascii=False) + '\n')
            changed = fixture()
            changed['events'][0]['english'] += '\n\nA changed source final.'
            plan = event_import.build_plan(changed, temp)
            self.assertEqual(['V50'], plan['preview']['conflicts'])
            self.assertEqual('conflict', plan['items'][0]['action'])
            with self.assertRaises(ValueError):
                event_import.apply_plan(plan, temp)
            persisted = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertIn('A local edit.', [row for row in persisted if row['event_id'] == 'V50'][-1]['story'])

    def test_initial_stage_keeps_the_full_story(self):
        with workspace_temp() as temp:
            final = fixture()
            long_story = ('Oct 29, 2026. ' + ('The brass was warm. ' * 70)).strip() + ' UNIQUE_END_FACT.'
            final['events'][0]['english'] = long_story
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            role = Path(temp) / 'peter'
            stages = [json.loads(line) for line in (role / 'event-stages.jsonl').read_text(encoding='utf-8').splitlines()]
            stage = next(row for row in stages if row['event_id'] == 'V50')
            self.assertGreater(len(long_story), 600)
            self.assertEqual(long_story, stage['text'])
            self.assertTrue(EventMemory._story(type('Fake', (), {'stages': lambda self, event_id: [stage]})(), 'V50').endswith('UNIQUE_END_FACT.'))

    def test_first_import_failure_leaves_no_half_import(self):
        with workspace_temp() as temp:
            plan = event_import.build_plan(fixture(), temp)
            original = event_import._write_atomic
            calls = {'n': 0}
            def fail(path, text):
                calls['n'] += 1
                if calls['n'] >= 2:
                    raise OSError('injected')
                return original(path, text)
            with unittest.mock.patch.object(event_import, '_write_atomic', side_effect=fail):
                with self.assertRaises(OSError):
                    event_import.apply_plan(plan, temp)
            role = Path(temp) / 'peter'
            self.assertFalse((role / 'events.jsonl').exists())
            self.assertFalse((role / 'event-stages.jsonl').exists())
            self.assertFalse(list((role / 'memory' / 'dynamic' / '事件').glob('*.md')))

    def test_first_import_write_failure_clears_marker_and_can_retry(self):
        with workspace_temp() as temp:
            final = fixture()
            plan = event_import.build_plan(final, temp)
            original = event_import._write_atomic
            def fail(path, text):
                if Path(path).name == 'events.jsonl':
                    raise OSError('injected first import failure')
                return original(path, text)
            with unittest.mock.patch.object(event_import, '_write_atomic', side_effect=fail):
                with self.assertRaisesRegex(OSError, 'injected first import failure'):
                    event_import.apply_plan(plan, temp)
            role = Path(temp) / 'peter'
            self.assertFalse((role / event_import.PENDING_NAME).exists())
            self.assertFalse(list((role / 'memory').rglob('*.md')))
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            self.assertEqual(3, len((role / 'events.jsonl').read_text(encoding='utf8').splitlines()))

    def test_interrupted_first_import_recovers_empty_snapshot_on_retry(self):
        with workspace_temp() as temp:
            final = fixture()
            plan = event_import.build_plan(final, temp)
            original = event_import._write_atomic
            def interrupt(path, text):
                if Path(path).name == 'events.jsonl':
                    raise KeyboardInterrupt('simulated process interruption')
                return original(path, text)
            with unittest.mock.patch.object(event_import, '_write_atomic', side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    event_import.apply_plan(plan, temp)
            role = Path(temp) / 'peter'
            self.assertTrue((role / event_import.PENDING_NAME).exists())
            self.assertTrue(list((role / 'memory').rglob('*.md')))
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            self.assertFalse((role / event_import.PENDING_NAME).exists())
            self.assertEqual(3, len((role / 'events.jsonl').read_text(encoding='utf8').splitlines()))

    def test_update_failure_restores_the_old_event_stage_and_memory(self):
        with workspace_temp() as temp:
            final = fixture()
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            role = Path(temp) / 'peter'
            old_events = (role / 'events.jsonl').read_text(encoding='utf-8')
            old_stages = (role / 'event-stages.jsonl').read_text(encoding='utf-8')
            old_md = next((role / 'memory' / 'dynamic' / '事件').glob('V50*.md')).read_text(encoding='utf-8')
            changed = fixture()
            changed['events'][0]['english'] += '\n\nA changed source final.'
            plan = event_import.build_plan(changed, temp)
            self.assertEqual('update', plan['items'][0]['action'])
            original = event_import._write_atomic
            def fail(path, text):
                if str(path).endswith('event-import-manifest.json'):
                    raise OSError('injected')
                return original(path, text)
            with unittest.mock.patch.object(event_import, '_write_atomic', side_effect=fail):
                with self.assertRaises(OSError):
                    event_import.apply_plan(plan, temp)
            self.assertEqual(old_events, (role / 'events.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(old_stages, (role / 'event-stages.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(old_md, next((role / 'memory' / 'dynamic' / '事件').glob('V50*.md')).read_text(encoding='utf-8'))


    def test_index_status_uses_ombre_content_hash(self):
        with workspace_temp() as temp:
            final = fixture()
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            role = Path(temp) / 'peter'
            vault = role / 'memory'
            db = vault / 'embeddings.db'
            ids = [event['id'] for event in final['events']]
            expected = event_import.expected_hashes(vault, ids)
            db.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(db)
            connection.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT, updated_at TEXT, content_hash TEXT)')
            connection.execute('INSERT INTO embeddings VALUES (?,?,?,?)', ('V50', 'x', 'now', 'old-hash'))
            connection.commit(); connection.close()
            status = event_import.index_status(vault, db, ids)
            self.assertFalse(status['ready'])
            self.assertEqual(1, status['stale'])
            self.assertIn('V50', status['stale_ids'])
            event_import.drop_stale_embeddings(db, ['V50'])
            status = event_import.index_status(vault, db, ids)
            self.assertIn('V50', status['missing_ids'])

    def test_wait_ready_requires_current_hash_and_rolls_back_probe(self):
        with workspace_temp() as temp:
            final = fixture()
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            role = Path(temp) / 'peter'
            vault = role / 'memory'
            db = vault / 'embeddings.db'
            ids = [event['id'] for event in final['events']]
            expected = event_import.expected_hashes(vault, ids)
            connection = sqlite3.connect(db)
            connection.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT, updated_at TEXT, content_hash TEXT)')
            for event_id, digest in expected.items():
                connection.execute('INSERT INTO embeddings VALUES (?,?,?,?)', (event_id, 'x', 'now', digest))
            connection.commit(); connection.close()

            calls = {'context': 0, 'rollback': 0}
            class FakeStore:
                def __init__(self, root): self.root = root
                @contextmanager
                def lock(self): yield
            class FakeBudget:
                def __init__(self, store): pass
            class FakeMemory:
                def __init__(self, *args):
                    self.last_recall_debug = {}
                    self.rotation = type('Rotation', (), {'turn': 0})()
                    self.mutex = contextmanager(lambda: (yield))()
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def context(self, *args, **kwargs):
                    calls['context'] += 1
                    self.rotation.turn += 1
                    self.last_recall_debug = {'ombre': ['V50']}
                def rollback_recall(self): calls['rollback'] += 1
                def _save_rotation(self): pass
            @contextmanager
            def fake_open(*args, **kwargs):
                yield object()
            fake_veil = types.ModuleType('veil_agent')
            fake_veil.Store = FakeStore
            fake_veil.Budget = FakeBudget
            fake_veil.load_character = lambda root, role: object()
            fake_memory = types.ModuleType('memory_agent')
            fake_memory.open_ombre = fake_open
            fake_memory.Memory = FakeMemory
            with unittest.mock.patch.dict(sys.modules, {'veil_agent': fake_veil, 'memory_agent': fake_memory}):
                result = event_import.wait_ready(temp, temp, 'peter', ids, timeout=5)
            self.assertTrue(result['ready'])
            self.assertEqual(3, result['indexed'])
            self.assertEqual(1, calls['context'])
            self.assertEqual(1, calls['rollback'])

    def test_wait_ready_with_stale_hash_drops_the_old_vector(self):
        with workspace_temp() as temp:
            final = fixture()
            event_import.apply_plan(event_import.build_plan(final, temp), temp)
            role = Path(temp) / 'peter'
            vault = role / 'memory'
            db = vault / 'embeddings.db'
            ids = [event['id'] for event in final['events']]
            connection = sqlite3.connect(db)
            connection.execute('CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, embedding TEXT, updated_at TEXT, content_hash TEXT)')
            for event_id in ids:
                connection.execute('INSERT INTO embeddings VALUES (?,?,?,?)', (event_id, 'x', 'now', 'old-hash'))
            connection.commit(); connection.close()
            class FakeStore:
                def __init__(self, root): pass
                @contextmanager
                def lock(self): yield
            class FakeBudget:
                def __init__(self, store): pass
            class FakeMemory:
                def __init__(self, *args): self.last_recall_debug = {}
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def context(self, *args, **kwargs): self.last_recall_debug = {'ombre': []}
                def rollback_recall(self): pass
            @contextmanager
            def fake_open(*args, **kwargs): yield object()
            fake_veil = types.ModuleType('veil_agent')
            fake_veil.Store = FakeStore; fake_veil.Budget = FakeBudget
            fake_veil.load_character = lambda root, role: object()
            fake_memory = types.ModuleType('memory_agent')
            fake_memory.open_ombre = fake_open; fake_memory.Memory = FakeMemory
            with unittest.mock.patch.dict(sys.modules, {'veil_agent': fake_veil, 'memory_agent': fake_memory}), \
                 unittest.mock.patch.object(event_import.time, 'sleep', lambda seconds: None):
                result = event_import.wait_ready(temp, temp, 'peter', ids, timeout=1)
            self.assertFalse(result['ready'])
            status = event_import.index_status(vault, db, ids)
            self.assertEqual(3, status['missing'])


    def test_body_must_contain_english(self):
        with workspace_temp() as temp:
            final = fixture()
            final['events'][0]['english'] = '这是一段只有中文的正文。'
            with self.assertRaises(ValueError):
                event_import.build_plan(final, temp)


if __name__ == '__main__':
    unittest.main()
