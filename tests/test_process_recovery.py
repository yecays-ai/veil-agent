"""Real child-process interruption contracts for local task and restore state."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import backup_restore
from long_tasks import LongTaskStore
from veil_agent import Store, UserError


REPO = Path(__file__).resolve().parents[1]
READY = "READY-FOR-KILL"


def _project(root, label):
    (root / "data" / "peter" / "memory").mkdir(parents=True)
    (root / "characters" / "peter").mkdir(parents=True)
    (root / "persona").mkdir()
    (root / ".env").write_text(f"FAKE_KEY={label}\n", encoding="utf-8")
    (root / "characters" / "peter" / "character.toml").write_text(
        'name = "Peter"\npersona = "persona/peter.md"\n', encoding="utf-8")
    (root / "persona" / "peter.md").write_text(label, encoding="utf-8")
    (root / "data" / "costs.jsonl").write_text('{"type":"settle","id":"once"}\n', encoding="utf-8")
    (root / "data" / "peter" / "chat.jsonl").write_text(
        f'{{"content":"{label} chat"}}\n', encoding="utf-8")
    (root / "data" / "peter" / "memory" / "fixture.md").write_text(label, encoding="utf-8")
    (root / "data" / "peter" / "long-task.json").write_text('{"fixture":"state"}\n', encoding="utf-8")


def _child_code():
    return r'''
import json, os, sys, time
from pathlib import Path
import backup_restore
from long_tasks import LongTaskStore
from veil_agent import Store

mode = sys.argv[1]
if mode == "long-write":
    root = Path(sys.argv[2]); target = root / "workspace" / "effect.txt"
    tasks = LongTaskStore(Store(root / "data"), "peter")
    _, state, _ = tasks.invoke("long_task_update", {"action":"create", "title":"fixture", "goal":"write once"})
    assert state == "成功"
    tasks.before_tool("write_file", "workspace/effect.txt", autonomy=True, args={"content":"effect"})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("effect", encoding="utf-8")
    (root / "workspace" / "executions.jsonl").write_text('{"operation":"write-once"}\n', encoding="utf-8")
    print("READY-FOR-KILL", flush=True)
    time.sleep(30)
elif mode == "long-state":
    root = Path(sys.argv[2]); tasks = LongTaskStore(Store(root / "data"), "peter")
    print(json.dumps(tasks.read(), ensure_ascii=False), flush=True)
elif mode == "restore":
    backup, target, boundary = map(Path, sys.argv[2:5])
    original = backup_restore.os.replace
    installed = 0
    def interrupt(source, destination):
        nonlocal_installed = None
        global installed
        result = original(source, destination)
        if source.parent == target and destination.parent.name.startswith(target.name + ".before-restore-"):
            if boundary.name == "old-moved": print("READY-FOR-KILL", flush=True); time.sleep(30)
        if destination.parent == target and source.name in backup_restore.PAYLOAD_ROOTS:
            installed += 1
            if ((boundary.name == "partial-new" and installed == 1) or
                    (boundary.name == "new-installed" and installed == len(backup_restore.PAYLOAD_ROOTS))):
                print("READY-FOR-KILL", flush=True); time.sleep(30)
        return result
    backup_restore.os.replace = interrupt
    backup_restore.restore_backup(backup, target, replace=True, confirm=lambda _: "RESTORE")
'''


class ProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=REPO)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _start(self, *args):
        return subprocess.Popen([sys.executable, "-c", _child_code(), *map(str, args)], cwd=REPO,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _kill_at_boundary(self, process):
        self.assertEqual(process.stdout.readline().strip(), READY)
        process.kill()
        process.wait(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        stderr = process.stderr.read()
        process.stdout.close(); process.stderr.close()
        return stderr

    def test_real_process_kill_after_effect_keeps_pending_and_never_replays(self):
        state = self.root / "task-state"
        process = self._start("long-write", state)
        self._kill_at_boundary(process)
        target = state / "workspace" / "effect.txt"
        self.assertEqual(target.read_text(encoding="utf-8"), "effect")
        self.assertEqual((state / "workspace" / "executions.jsonl").read_text().count("write-once"), 1)

        restarted = subprocess.run([sys.executable, "-c", _child_code(), "long-state", str(state)],
                                  cwd=REPO, capture_output=True, text=True, timeout=10, check=True)
        pending = json.loads(restarted.stdout)["pending_action"]
        self.assertEqual(pending["tool"], "write_file")
        # The new process saw a real persisted checkpoint; readback, not replay, resolves it.
        tasks = LongTaskStore(Store(state / "data"), "peter")
        operation_id = tasks.read()["pending_action"]["operation_id"]
        tasks.set_tool_step(1)
        tasks.observe_tool_result("read_text_file", "workspace/effect.txt", "成功", target.read_text())
        tasks.set_tool_step(2)
        _, result, _ = tasks.invoke("long_task_update", {"action":"update", "task_id":tasks.read()["task_id"],
            "resolve_pending":True, "pending_operation_id":operation_id, "progress_update":"read back effect"})
        self.assertEqual(result, "成功")
        self.assertIsNone(tasks.read()["pending_action"])
        self.assertEqual((state / "workspace" / "executions.jsonl").read_text().count("write-once"), 1)

    def test_real_process_kills_fail_closed_at_every_replace_switch_boundary(self):
        source, target, backups = self.root / "source", self.root / "target", self.root / "backups"
        _project(source, "new")
        backup, _ = backup_restore.create_backup(source, backups)
        for boundary in ("old-moved", "partial-new", "new-installed"):
            with self.subTest(boundary=boundary):
                if target.exists():
                    import shutil
                    shutil.rmtree(target)
                _project(target, "old")
                process = self._start("restore", backup, target, boundary)
                self._kill_at_boundary(process)
                self.assertTrue((target / backup_restore.RECOVERY_MARKER_NAME).exists())
                with self.assertRaisesRegex(UserError, "未完成的备份恢复"):
                    Store(target / "data")
                safety = list(self.root.glob("target.before-restore-*"))
                self.assertTrue(safety)
                self.assertTrue(any((item / "data" / "peter" / "chat.jsonl").exists() for item in safety))
                # A deliberate rerun has a clear path: it preserves the interrupted target in a new safety copy.
                backup_restore.restore_backup(backup, target, replace=True, confirm=lambda _: "RESTORE")
                self.assertFalse((target / backup_restore.RECOVERY_MARKER_NAME).exists())
                self.assertIn("new chat", (target / "data" / "peter" / "chat.jsonl").read_text())
                self.assertEqual((target / "data" / "costs.jsonl").read_text().count("settle"), 1)


if __name__ == "__main__":
    unittest.main()
