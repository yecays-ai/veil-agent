import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil.brain.cognitive_consolidation import CognitiveConsolidator, _normalise_experience
from veil.brain.experience_provider import EvidenceValidator, ExperienceHintProvider, SituationEnvelope
from veil.brain.learned_context import LearnedContext
from veil.brain.storage import brain_backup, brain_events, brain_writer, veil_brain
from veil.core.config_registry import experience_injection_config_overview
from veil.core.veil_agent import Store


def _init_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(veil_brain.SCHEMA)
    brain_events.ensure_schema(conn)
    brain_writer.ensure_schema(conn)
    brain_writer.apply_create_memory(conn, {
        'id': 'mem-1', 'body': 'fictional export failed', 'title': 'Export lesson',
        'type': 'story', 'importance': 5, 'domain': [], 'tags': [],
    })
    brain_events.upsert_event(conn, {
        'event_id': 'event-1', 'memory_id': 'mem-1', 'title': 'Export event',
        'story': 'fictional export failed', 'status': 'active', 'sources': [],
    }, commit=False, ensure=False)
    conn.commit()
    brain_writer.refresh_digest(conn)
    conn.close()


def _candidate(candidate_id='exp-1', ref='memory:mem-1'):
    return {
        'id': candidate_id,
        'schema_version': 'p7.experience.v1',
        'content_key': candidate_id,
        'kind': 'tool_lesson',
        'trigger_condition': 'fictional export failure',
        'guidance': 'read the local error before retrying',
        'reason': 'verified evidence',
        'evidence_refs': [ref],
        'status': 'confirmed',
        'created_at': '2026-11-28T10:00:00+08:00',
        'updated_at': '2026-11-28T10:00:00+08:00',
    }


class P7S1ExperienceProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent,
                                                ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.cfg = SimpleNamespace(id='peter')
        self.role = self.root / 'peter'
        self.db = self.role / 'brain.db'
        _init_db(self.db)
        self.profile = LearnedContext(self.root, 'peter', {'file': 'learned-context.json'})
        self.memory = SimpleNamespace(brain_authority=SimpleNamespace(db_path=self.db))
        self.cons = CognitiveConsolidator(
            self.cfg, self.store, SimpleNamespace(), SimpleNamespace(),
            memory=self.memory, profile=self.profile,
        )
        state = self.cons._load_state()
        state['experience_candidates'] = [_candidate()]
        self.cons._save_state(state)
        self.provider = ExperienceHintProvider(
            self.cfg, self.store, consolidator=self.cons,
            memory=self.memory, profile=self.profile,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_default_off_persists_with_revision_and_zero_candidates(self):
        situation = SituationEnvelope(
            text='The fictional export failed.', query_vector=(1.0, 0.0),
            context_signal={'mode': 'technical_task'},
        )
        original_query = self.cons.query_experience_candidates
        self.cons.query_experience_candidates = Mock(side_effect=AssertionError('disabled P7 queried candidates'))
        self.assertEqual(self.provider(situation), [])
        self.cons.query_experience_candidates.assert_not_called()
        self.cons.query_experience_candidates = original_query
        self.assertFalse((self.role / 'experience-injection.json').exists())

        saved = self.provider.update_settings({'enabled': True}, expected_revision=1)
        self.assertTrue(saved['ok'])
        self.assertEqual(saved['settings_revision'], 2)
        self.assertEqual(len(self.provider(situation)), 1)
        stale = self.provider.update_settings({'max_items': 2}, expected_revision=1)
        self.assertEqual(stale['status_code'], 409)

        overview = experience_injection_config_overview(
            role_id='peter', persisted={'enabled': True}, runtime=self.provider.settings())
        enabled = next(row for row in overview['entries'] if row['key'] == 'memory.p7.enabled')
        self.assertTrue(enabled['effective']['value'])

    def test_role_isolation_and_veto_recheck(self):
        self.provider.update_settings({'enabled': True}, expected_revision=1)
        ghost_cfg = SimpleNamespace(id='ghost')
        ghost_db = self.root / 'ghost' / 'brain.db'
        _init_db(ghost_db)
        ghost_cons = CognitiveConsolidator(
            ghost_cfg, self.store, SimpleNamespace(), SimpleNamespace(),
            memory=SimpleNamespace(brain_authority=SimpleNamespace(db_path=ghost_db)),
        )
        ghost = ExperienceHintProvider(ghost_cfg, self.store, consolidator=ghost_cons)
        ghost.update_settings({'enabled': True}, expected_revision=1)
        self.assertEqual(ghost('fictional export failure'), [])

        original = self.cons.query_experience_candidates
        calls = {'count': 0}

        def veto_between_queries(**kwargs):
            rows = original(**kwargs)
            calls['count'] += 1
            if calls['count'] == 1:
                self.cons.decide_experience_candidate('exp-1', 'veto')
            return rows

        self.cons.query_experience_candidates = veto_between_queries
        self.assertEqual(self.provider('fictional export failure'), [])

    def test_all_evidence_types_fail_closed_after_delete_or_change(self):
        self.store.append('peter/chat.jsonl', {
            'role': 'user', 'content': 'fictional export failed', 'message_id': 'chat-1',
        })
        self.store.append('peter/autonomy.jsonl', {
            'started_at': 'auto-1', 'output_audit': {'clean': True, 'issues': []},
        })
        self.store.append('peter/tools.jsonl', {
            'request_id': 'req-1', 'call_id': 'tool-1', 'tool': 'fictional', 'state': '成功',
        })
        (self.role / 'long-task.json').write_text(
            json.dumps({'task_id': 'task-1'}), encoding='utf-8')
        validator = EvidenceValidator(self.cfg, self.store, memory=self.memory, profile=self.profile)
        refs = ('memory:mem-1', 'event:event-1', 'chat:chat-1', 'autonomy:auto-1',
                'tool:tool-1', 'long_task:task-1')
        for index, ref in enumerate(refs):
            self.assertEqual(validator.validate(_candidate(f'exp-{index}', ref)), 'valid', ref)

        conn = sqlite3.connect(self.db)
        brain_writer.hard_delete(conn, 'mem-1', reason='test')
        conn.execute("DELETE FROM events WHERE event_id='event-1'")
        conn.commit()
        brain_writer.refresh_digest(conn)
        conn.close()
        self.store.remove_records('peter/chat.jsonl', {0})
        self.profile.invalidate_evidence_cache()
        (self.role / 'autonomy.jsonl').write_text(
            json.dumps({'started_at': 'auto-1', 'output_audit': {'clean': False, 'issues': ['bad']}}) + '\n',
            encoding='utf-8')
        (self.role / 'tools.jsonl').write_text('', encoding='utf-8')
        (self.role / 'long-task.json').write_text(json.dumps({'task_id': 'task-2'}), encoding='utf-8')
        for index, ref in enumerate(refs):
            self.assertNotIn(validator.validate(_candidate(f'exp-x-{index}', ref)), ('valid', 'partial_stale'), ref)

    def test_legacy_contract_is_additive_and_conservative(self):
        projected = _normalise_experience(_candidate())
        self.assertEqual(projected['schema_version'], 'p7.experience.v1')
        self.assertEqual(projected['experience_type'], 'unknown_legacy')
        self.assertEqual(projected['domain'], 'work_method')
        self.assertEqual(projected['origin_evidence_tier'], 'L4_self_eval')
        self.assertEqual(projected['exceptions'], [])
        self.assertFalse(projected['locked'])

    def test_backup_restore_preserves_later_veto_and_generation(self):
        backup_dir = self.root / 'backup'
        backed = brain_backup.backup_bundle(self.role, backup_dir)
        self.assertEqual(len(backed['manifest']['generation']), 32)
        for name in ('cognitive-consolidation-state.json', 'experience-injection.json'):
            if name == 'experience-injection.json':
                self.provider.update_settings({'enabled': True}, expected_revision=1)
                backed = brain_backup.backup_bundle(self.role, backup_dir / 'with-config')
                self.assertIn(name, backed['manifest']['companions'])
            else:
                self.assertIn(name, backed['manifest']['companions'])

        self.cons.decide_experience_candidate('exp-1', 'veto', note='later veto')
        result = brain_backup.restore_bundle(backup_dir, self.role)
        restored = json.loads((self.role / 'cognitive-consolidation-state.json').read_text(encoding='utf-8'))
        self.assertIn('read the local error before retrying', restored['vetoed_experiences'])
        row = next(item for item in restored['experience_candidates'] if item['id'] == 'exp-1')
        self.assertEqual(row['status'], 'vetoed')
        self.assertEqual(len(result['generation']), 32)
        self.assertEqual(result['reconcile']['p7_candidates_vetoed'], 1)

    def test_backup_waits_for_atomic_p7_config_write(self):
        entered = threading.Event()
        release = threading.Event()
        backup_done = threading.Event()
        original_replace = self.store.replace

        def blocked_replace(name, value):
            if str(name).endswith('experience-injection.json'):
                entered.set()
                self.assertTrue(release.wait(2))
            return original_replace(name, value)

        self.store.replace = blocked_replace
        writer = threading.Thread(
            target=lambda: self.provider.update_settings({'enabled': True}, expected_revision=1))
        writer.start()
        self.assertTrue(entered.wait(2))
        backup_dir = self.root / 'concurrent-backup'

        def backup():
            brain_backup.backup_bundle(self.role, backup_dir)
            backup_done.set()

        reader = threading.Thread(target=backup)
        reader.start()
        time.sleep(0.05)
        self.assertFalse(backup_done.is_set())
        release.set()
        writer.join(2)
        reader.join(3)
        self.assertTrue(backup_done.is_set())
        saved = json.loads((backup_dir / 'experience-injection.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['enabled'])


if __name__ == '__main__':
    unittest.main()
