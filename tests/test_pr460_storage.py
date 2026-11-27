"""PR #460: isolated Learned Context persistence and migration regressions."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from veil.brain.learned_context import LearnedContext
from veil.brain.storage import brain_authority_init, brain_backup
from veil.core.agent_types import UserError
from veil.core.model_settings import ModelSettings


class TestPR460Storage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lc = self.instance()

    def instance(self):
        return LearnedContext(self.root, 'fixture', {'file': 'learned-context.json'})

    @staticmethod
    def crash_code():
        return """
import os, sys
from veil.brain.learned_context import LearnedContext
lc = LearnedContext(sys.argv[1], 'fixture', {'file': 'learned-context.json'})
record = lc._record
def record_then_exit(*args, **kwargs):
    record(*args, **kwargs)
    os._exit(17)
lc._record = record_then_exit
lc.save_section('facts', 'crashed write')
"""

    def test_failed_history_append_restores_both_files(self):
        self.lc.save_section('facts', 'before')
        body_before = self.lc.path.read_bytes()
        versions_before = self.lc.versions.read_bytes()
        record = self.lc._record

        def append_then_fail(*args, **kwargs):
            record(*args, **kwargs)
            raise UserError('injected flush failure after the row reached disk')

        with patch.object(self.lc, '_record', side_effect=append_then_fail):
            with self.assertRaises(UserError):
                self.lc.save_section('facts', 'failed write')
        self.assertEqual(self.lc.path.read_bytes(), body_before)
        self.assertEqual(self.lc.versions.read_bytes(), versions_before)

    def test_crash_after_history_append_recovers_on_next_read(self):
        self.lc.save_section('facts', 'before')
        versions_before = self.lc.versions.read_bytes()
        proc = subprocess.run([sys.executable, '-c', self.crash_code(), str(self.root)],
                              capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 17, proc.stderr)
        reader = self.instance()
        rows = reader.read_version_history()
        self.assertEqual(json.loads(rows[0]['after'])['facts']['text'], 'before')
        self.assertEqual(reader.read()['facts']['text'], 'before')
        self.assertEqual(self.lc.versions.read_bytes(), versions_before)

    def test_bundle_recovers_pending_learned_context_before_snapshot(self):
        self.lc.save_section('facts', 'before')
        body_before = self.lc.path.read_bytes()
        versions_before = self.lc.versions.read_bytes()
        brain_authority_init.initialize(self.lc.root / 'brain.db')
        proc = subprocess.run([sys.executable, '-c', self.crash_code(), str(self.root)],
                              capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 17, proc.stderr)
        bundle = self.root / 'bundle'
        brain_backup.backup_bundle(self.lc.root, bundle)
        self.assertEqual((bundle / self.lc.path.name).read_bytes(), body_before)
        self.assertEqual((bundle / self.lc.versions.name).read_bytes(), versions_before)

    def test_old_committed_marker_is_not_mistaken_for_new_save(self):
        self.lc.save_section('facts', 'before')
        self.lc._journal.write_bytes(b'{"state":"committed"}')
        original_unlink = Path.unlink
        atomic_bytes = ModelSettings._atomic_bytes

        def keep_marker(path, *args, **kwargs):
            if path == self.lc._journal:
                raise PermissionError('injected cleanup failure')
            return original_unlink(path, *args, **kwargs)

        def fail_prepare(path, data):
            if path == self.lc._journal:
                raise OSError('injected prepare failure')
            atomic_bytes(path, data)

        with patch.object(Path, 'unlink', new=keep_marker), \
                patch.object(ModelSettings, '_atomic_bytes', side_effect=fail_prepare):
            with self.assertRaises(UserError):
                self.lc.save_section('facts', 'not saved')
            self.assertEqual(self.lc.read()['facts']['text'], 'before')
        self.lc.save_section('facts', 'subsequent save')
        self.assertEqual(self.lc.read()['facts']['text'], 'subsequent save')

    def test_failed_bootstrap_leaves_neither_file(self):
        def append_then_fail(*args, **kwargs):
            self.lc.versions.write_bytes(b'{"partial":')
            raise UserError('short append')

        with patch.object(self.lc, '_record', side_effect=append_then_fail):
            with self.assertRaises(UserError):
                self.lc.read()
        self.assertFalse(self.lc.path.exists())
        self.assertFalse(self.lc.versions.exists())
        self.instance().read()
        self.assertEqual(len(self.lc.versions.read_bytes().splitlines()), 1)

    def test_two_instances_serialize_read_modify_write(self):
        self.lc.read()
        other = self.instance()
        loaded = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()
        second_loaded = threading.Event()
        first_load, second_load = self.lc._load, other._load

        def pause_after_load():
            data = first_load()
            loaded.set()
            if not release.wait(8):
                raise AssertionError('test did not release first writer')
            return data

        def observe_second_load():
            data = second_load()
            second_loaded.set()
            return data

        def second_write():
            second_entered.set()
            return other.save_section('growth', 'second update')

        with patch.object(self.lc, '_load', side_effect=pause_after_load), \
                patch.object(other, '_load', side_effect=observe_second_load), ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.lc.save_section, 'facts', 'first update')
            try:
                self.assertTrue(loaded.wait(4))
                second = pool.submit(second_write)
                self.assertTrue(second_entered.wait(4))
                self.assertFalse(second_loaded.wait(.3), 'second instance read an uncommitted snapshot')
            finally:
                release.set()
            first.result(timeout=5)
            second.result(timeout=5)
        data = self.instance().read()
        self.assertEqual(data['facts']['text'], 'first update')
        self.assertEqual(data['growth']['text'], 'second update')

    def test_rollback_reverses_only_unused_schema_field(self):
        self.lc.save_section('facts', 'legacy fact')
        legacy = self.lc.read()
        legacy['relationship'].pop('vetoed')
        self.lc.path.write_text(json.dumps(legacy), encoding='utf-8')
        plan = self.lc.migrate_storage(dry_run=False)
        self.assertTrue(plan['applied'])
        self.assertEqual(self.lc.read()['relationship']['vetoed'], [])
        self.lc.save_section('facts', 'replacement after migration')
        restored = self.lc.rollback_migration(plan['backup_path'])
        self.assertNotIn('vetoed', restored['relationship'])
        self.assertEqual(restored['facts']['text'], 'replacement after migration')
        self.assertTrue(self.lc.migrate_storage()['needs_upgrade'])

    def test_rollback_preserves_mutations_deletions_locks_and_history(self):
        lc = self.lc
        lc.save_section('facts', 'old facts')
        lc.save_section('growth', 'old growth')
        lc.save_relationship([{'id': rid, 'text': text, 'status': 'confirmed'} for rid, text in (
            ('edit', 'old rule'), ('delete', 'removed rule'), ('lock', 'locked later'),
            ('unlock', 'unlocked later'),
        )])
        lc.set_relationship_lock('unlock', True)
        for item in ('edited task', 'removed task'):
            lc.invoke('ongoing_update', {'action': 'add', 'text': item})
        legacy = lc.read()
        legacy['relationship'].pop('vetoed', None)
        lc.path.write_text(json.dumps(legacy), encoding='utf-8')
        before_dry = lc.path.read_bytes(), lc.versions.read_bytes()
        self.assertTrue(lc.migrate_storage()['needs_upgrade'])
        self.assertEqual((lc.path.read_bytes(), lc.versions.read_bytes()), before_dry)
        plan = lc.migrate_storage(dry_run=False)
        self.assertTrue(plan['applied'])
        self.assertFalse(lc.migrate_storage(dry_run=False)['applied'])

        lc.save_section('facts', 'replacement facts')
        lc.save_section('growth', '')
        edited = [row for row in lc.read()['relationship']['items'] if row['id'] != 'delete']
        next(row for row in edited if row['id'] == 'edit')['text'] = 'new rule'
        lc.save_relationship(edited)
        lc.set_relationship_lock('lock', True)
        lc.set_relationship_lock('unlock', False)
        ongoing = lc.read()['ongoing']['items'][0]
        ongoing['text'] = 'new task text'
        lc.save_ongoing([ongoing])
        lc.invoke('ongoing_update', {'action': 'complete', 'id': ongoing['id']})
        current = lc.read()
        versions_before = lc.versions.read_bytes()
        result = lc.rollback_migration(plan['backup_path'])
        for section in ('facts', 'growth', 'relationship', 'ongoing'):
            self.assertEqual(result[section], current[section])
        history = lc.versions.read_bytes()
        self.assertTrue(history.startswith(versions_before))
        self.assertEqual(len(history.splitlines()), len(versions_before.splitlines()) + 1)
        self.assertEqual(json.loads(history.splitlines()[-1])['tool'], 'rollback_s3_migration')


if __name__ == '__main__':
    unittest.main()
