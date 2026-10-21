import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import backup_restore

REPO = Path(__file__).resolve().parents[1]


class ColdBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=REPO)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.backups = self.base / "external-backups"
        self.target = self.base / "restored-project"
        self._make_project(self.source)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _make_project(root):
        (root / "data" / "fixture" / "memory" / "_agent").mkdir(parents=True)
        (root / "data" / "fixture" / "workspace").mkdir(parents=True)
        (root / "characters" / "fixture").mkdir(parents=True)
        (root / "persona").mkdir()
        (root / ".env").write_bytes(b"FAKE_API_KEY=fiction-only\n")
        (root / "persona" / "fixture.md").write_bytes(b"fictional persona\r\n")
        (root / "characters" / "fixture" / "character.toml").write_text(
            'name = "Fixture"\npersona = "persona/fixture.md"\n', encoding="utf-8"
        )
        (root / "data" / "costs.jsonl").write_bytes(b'{"type":"settle"}\n')
        (root / "data" / "fixture" / "chat.jsonl").write_bytes(b'{"role":"user","content":"fiction"}\n')
        (root / "data" / "fixture" / "memory" / "fiction.md").write_bytes(b"synthetic memory\n")
        (root / "data" / "fixture" / "memory" / "_agent" / "state.json").write_bytes(b'{"cursor":1}\n')
        (root / "data" / "fixture" / "memory" / "_agent" / "process.log").write_text(
            "volatile log", encoding="utf-8"
        )
        (root / "data" / "fixture" / "memory" / "_agent" / "ombre.lock").write_bytes(b"0")
        (root / "data" / "fixture" / "memory" / "_agent" / "temporary.tmp").write_text("volatile")
        (root / "data" / "fixture" / "append-grant.json").write_bytes(b'{"path":"fake.txt"}\n')
        (root / "data" / "fixture" / "long-tasks.json").write_bytes(b'{"task":"fiction"}\n')
        (root / "data" / "fixture" / "workspace" / "fiction.txt").write_bytes(b"workspace data")
        (root / "data" / "fixture" / "workspace" / "process.log").write_bytes(b"user data")
        (root / "data" / "fixture" / "workspace" / "ordinary.tmp").write_bytes(b"user temp")
        (root / "data" / "fixture" / "memory" / "_agent" / "nested").mkdir()
        (root / "data" / "fixture" / "memory" / "_agent" / "nested" / "process.log").write_bytes(
            b"persistent nested data"
        )
        (root / "data" / "fixture" / "memory" / "_agent" / "tmp").mkdir()
        (root / "data" / "fixture" / "memory" / "_agent" / "tmp" / "cache.json").write_bytes(
            b"runtime cache"
        )

    def _create(self):
        return backup_restore.create_backup(self.source, self.backups)[0]

    def test_create_manifest_covers_local_state_and_omits_only_runtime_files(self):
        backup, count = backup_restore.create_backup(self.source, self.backups)
        self.assertEqual(count, 13)
        manifest = backup_restore.verify_backup(backup)
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn("payload/.env", paths)
        self.assertIn("payload/data/costs.jsonl", paths)
        self.assertIn("payload/data/fixture/chat.jsonl", paths)
        self.assertIn("payload/data/fixture/memory/fiction.md", paths)
        self.assertIn("payload/data/fixture/memory/_agent/state.json", paths)
        self.assertIn("payload/data/fixture/append-grant.json", paths)
        self.assertIn("payload/data/fixture/long-tasks.json", paths)
        self.assertIn("payload/data/fixture/workspace/fiction.txt", paths)
        self.assertIn("payload/data/fixture/workspace/process.log", paths)
        self.assertIn("payload/data/fixture/workspace/ordinary.tmp", paths)
        self.assertIn("payload/data/fixture/memory/_agent/nested/process.log", paths)
        self.assertNotIn("payload/data/fixture/memory/_agent/process.log", paths)
        self.assertNotIn("payload/data/fixture/memory/_agent/ombre.lock", paths)
        self.assertNotIn("payload/data/fixture/memory/_agent/temporary.tmp", paths)
        self.assertNotIn("payload/data/fixture/memory/_agent/tmp/cache.json", paths)
        self.assertEqual(
            (backup / "payload/data/fixture/memory/fiction.md").read_bytes(), b"synthetic memory\n"
        )
        self.assertIn("does not encrypt", (backup / "README.txt").read_text(encoding="utf-8"))

    def test_backup_destination_inside_project_is_rejected(self):
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.create_backup(self.source, self.source / "data" / "backup")

    def test_running_chat_lock_blocks_backup_without_copying_data(self):
        lock_path = self.source / "data" / "session.lock"
        code = (
            "from pathlib import Path; import sys; from backup_restore import _FileLock; "
            "lock=_FileLock(Path(sys.argv[1]), create=True); print('ready', flush=True); "
            "sys.stdin.readline(); lock.close()"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path)], cwd=REPO,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with self.assertRaises(backup_restore.BackupError):
                backup_restore.create_backup(self.source, self.backups)
            self.assertFalse(list(self.backups.glob("VEIL-Agent-Backup-*")))
        finally:
            process.stdin.write("\n")
            process.stdin.flush()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_running_ombre_lock_blocks_backup(self):
        lock_path = self.source / "data" / "fixture" / "memory" / "_agent" / "ombre.lock"
        code = (
            "from pathlib import Path; import sys; from backup_restore import _FileLock; "
            "lock=_FileLock(Path(sys.argv[1]), create=True); print('ready', flush=True); "
            "sys.stdin.readline(); lock.close()"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path)], cwd=REPO,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with self.assertRaises(backup_restore.BackupError):
                backup_restore.create_backup(self.source, self.backups)
            self.assertFalse(self.backups.exists())
        finally:
            process.stdin.write("\n")
            process.stdin.flush()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_corruption_is_detected_before_target_changes(self):
        backup = self._create()
        (backup / "payload/data/fixture/chat.jsonl").write_bytes(b"corrupt")
        self.target.mkdir()
        marker = self.target / "keep.txt"
        marker.write_bytes(b"keep exactly")
        with self.assertRaisesRegex(backup_restore.BackupError, "完整性校验失败"):
            backup_restore.restore_backup(backup, self.target)
        self.assertEqual(marker.read_bytes(), b"keep exactly")
        self.assertEqual(list(self.target.iterdir()), [marker])

    def test_conflicts_are_refused_by_default_and_never_overwritten(self):
        backup = self._create()
        self.target.mkdir()
        existing = self.target / ".env"
        existing.write_bytes(b"KEEP=existing\n")
        with self.assertRaisesRegex(backup_restore.BackupError, "未覆盖任何内容"):
            backup_restore.restore_backup(backup, self.target)
        self.assertEqual(existing.read_bytes(), b"KEEP=existing\n")
        self.assertEqual(list(self.target.iterdir()), [existing])

    def test_explicit_replace_preserves_safety_copy_before_restore(self):
        backup = self._create()
        self.target.mkdir()
        existing = self.target / ".env"
        existing.write_bytes(b"OLD_SECRET=fake\n")
        count, safety = backup_restore.restore_backup(
            backup, self.target, replace=True, confirm=lambda _: "RESTORE"
        )
        self.assertGreater(count, 0)
        self.assertEqual(existing.read_bytes(), b"FAKE_API_KEY=fiction-only\n")
        self.assertEqual((safety / ".env").read_bytes(), b"OLD_SECRET=fake\n")
        self.assertTrue(safety.is_dir())

    def test_failed_commit_rolls_back_replacements_and_new_files(self):
        backup = self._create()
        self.target.mkdir()
        existing = self.target / ".env"
        existing.write_bytes(b"OLD=keep\n")
        original = backup_restore._copy_atomic
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated destination failure")
            return original(source, destination)

        with mock.patch.object(backup_restore, "_copy_atomic", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated destination failure"):
                backup_restore.restore_backup(
                    backup, self.target, replace=True, confirm=lambda _: "RESTORE"
                )
        self.assertEqual(existing.read_bytes(), b"OLD=keep\n")
        self.assertEqual([path.name for path in self.target.iterdir()], [".env"])
        self.assertEqual(len(list(self.base.glob("restored-project.before-restore-*"))), 1)

    def test_failed_restore_to_new_target_removes_partial_target(self):
        backup = self._create()

        def fail_first(source, destination):
            raise OSError("simulated destination failure")

        with mock.patch.object(backup_restore, "_copy_atomic", side_effect=fail_first):
            with self.assertRaisesRegex(OSError, "simulated destination failure"):
                backup_restore.restore_backup(backup, self.target)
        self.assertFalse(self.target.exists())

    def test_unsupported_layout_is_rejected_even_with_valid_manifest_checksum(self):
        backup = self._create()
        manifest_path = backup / backup_restore.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["data_layout_version"] = 999
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        manifest_path.write_bytes(raw)
        (backup / backup_restore.MANIFEST_HASH_NAME).write_text(
            hashlib.sha256(raw).hexdigest() + "  manifest.json\n", encoding="ascii"
        )
        with self.assertRaisesRegex(backup_restore.BackupError, "不受当前程序支持"):
            backup_restore.verify_backup(backup)


if __name__ == "__main__":
    unittest.main()
