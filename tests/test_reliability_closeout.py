"""Isolated reliability closeout checks using fictional roles and local services."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import backup_restore
from autonomy import AutonomyController
from veil_agent import AUTONOMY_WAKE, ActivityState, Budget, Chat, Store, load_character, run_chat
from web_agent import Hub, serve_chat, start_server

REPO = Path(__file__).resolve().parents[1]


def make_character(root, role_id="fixture"):
    (root / "characters" / role_id).mkdir(parents=True)
    (root / "persona").mkdir(exist_ok=True)
    (root / "persona" / (role_id + ".md")).write_text(
        "Fictional runtime fixture persona.", encoding="utf-8")
    (root / "characters" / role_id / "character.toml").write_text(
        f'''name = "Fictional {role_id}"
provider = "offline"
persona = "persona/{role_id}.md"
max_output_tokens = 80
[providers.offline]
model = "offline-fixture"
base_url = "https://offline.invalid/v1"
api_key_env = "FIXTURE_UNUSED_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = false
[autonomy]
interval_minutes = 1
daily_max_runs = 4
daily_budget_cny = "2"
''', encoding="utf-8")


class ScheduledAutonomyCloseoutTests(unittest.TestCase):
    def test_enable_schedules_one_real_wake_then_pause_and_restart_stay_aligned(self):
        with tempfile.TemporaryDirectory(dir=REPO) as directory:
            root = Path(directory)
            make_character(root)
            cfg = load_character(root, "fixture")
            store = Store(root / "data")
            reply = SimpleNamespace(
                tool_calls=[],
                content="Decision: wait\nReason: no safe work is due.\nFocus: fictional fixture\nNext step: wait.",
                model_dump=lambda **_: {"role": "assistant", "tool_calls": []})
            model = Mock(return_value=(reply, 40, 8, 0))
            chat = Chat(cfg, store, Budget(store), model, output=lambda *_: None)
            activity = ActivityState(store.root / cfg.id / "resident-state.json")
            hub = Hub(cfg.name, activity)
            controller = AutonomyController(chat, hub)
            hub.autonomy = controller
            hub.activity_transition = lambda enabled: chat.set_activity_enabled(activity, enabled)
            hub.current_capabilities = chat.current_capability_snapshot
            hub.capability_record = lambda: chat.capability_history_record
            chat.activity_enabled = activity.enabled

            past = datetime.now().astimezone() - timedelta(minutes=3)
            controller.started_at = past
            with patch("veil_agent.now", return_value=past.isoformat()):
                self.assertTrue(hub.set_activity(True))

            self.assertTrue(activity.enabled())
            live = hub.snapshot(0)
            self.assertTrue(live["activity_enabled"])
            self.assertIn("Autonomous activity: enabled.", live["current_capabilities"])
            self.assertIn("- Available: long_task_status, long_task_update, capability_history, autonomy_history.",
                          live["current_capabilities"])
            self.assertTrue(live["next_wake_at"])

            controller.interval = timedelta(0)  # controlled time advancement for the due wake
            self.assertTrue(controller.due())
            wake = hub.input_fn()
            self.assertIs(wake, AUTONOMY_WAKE)
            inputs = iter((wake, '/quit'))
            run_chat(chat, input_fn=lambda _prompt='': next(inputs), output=lambda *_: None,
                     drop=lambda: False, activity=activity, autonomy=controller)

            rows = store.read("fixture/autonomy.jsonl")
            self.assertEqual(len(rows), 1)
            self.assertEqual(model.call_count, 1)

            # Simulate a restart boundary after the activity row is durable but
            # before its stale active marker is removed.
            controller.active_path.write_text(
                json.dumps({"started_at": rows[0]["started_at"]}) + "\n", encoding="utf-8")
            controller.interval = timedelta(minutes=1)
            restarted = AutonomyController(chat, hub)
            self.assertFalse(controller.active_path.exists())
            self.assertEqual(len(restarted.rows), 1)
            self.assertTrue(activity.enabled())
            self.assertFalse(restarted.due())  # restart waits a configured interval
            hub.autonomy = restarted
            live = hub.snapshot(0)
            self.assertTrue(live["activity_enabled"])
            self.assertIn("Autonomous activity: enabled.", live["current_capabilities"])

            self.assertTrue(hub.set_activity(False))
            self.assertFalse(ActivityState(activity.path).enabled())
            paused = hub.snapshot(0)
            self.assertFalse(paused["activity_enabled"])
            self.assertIn("Autonomous activity: paused or unavailable.",
                          paused["current_capabilities"])
            self.assertIsNone(AutonomyController(chat, hub).next_wake())
            self.assertEqual(len(store.read("fixture/autonomy.jsonl")), 1)
            model.assert_called_once()


class FictionalBackupRestoreCloseoutTests(unittest.TestCase):
    def test_backup_restore_preserves_two_role_datasets_and_failed_restore_keeps_target(self):
        with tempfile.TemporaryDirectory(dir=REPO) as directory:
            base = Path(directory)
            source = base / "source"
            backup_root = base / "backups"
            target = base / "restored"
            (source / "data" / "costs.jsonl").parent.mkdir(parents=True)
            cost_value = '{"fixture":"cost"}\n'
            env_value = "FIXTURE_ONLY_KEY=fictional\n"
            (source / "data" / "costs.jsonl").write_text(cost_value, encoding="utf-8")
            (source / ".env").write_text(env_value, encoding="utf-8")
            (source / "persona").mkdir()
            shared_expected = {"data/costs.jsonl": cost_value, ".env": env_value}
            expected = {}
            for role_id, label in (("fixture", "FICTIONAL_A"), ("ghost", "FICTIONAL_B")):
                role_data = source / "data" / role_id
                (role_data / "memory" / "_agent").mkdir(parents=True)
                (role_data / "workspace").mkdir()
                (source / "characters" / role_id).mkdir(parents=True)
                persona_value = "Fictional identity for " + label
                character_value = f'name = "{label}"\npersona = "persona/{role_id}.md"\n'
                (source / "persona" / (role_id + ".md")).write_text(
                    persona_value, encoding="utf-8")
                (source / "characters" / role_id / "character.toml").write_text(
                    character_value, encoding="utf-8")
                shared_expected["persona/" + role_id + ".md"] = persona_value
                shared_expected["characters/" + role_id + "/character.toml"] = character_value
                values = {
                    "chat.jsonl": '{"role":"user","content":"' + label + ' chat"}\n',
                    "memory/fiction.md": label + " memory\n",
                    "long-tasks.json": '{"goal":"' + label + ' task"}\n',
                    "append-grant.json": '{"path":"' + label + '-grant.txt"}\n',
                    "workspace/project.md": label + " workspace\n",
                }
                for relative, content in values.items():
                    path = role_data / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                expected[role_id] = values

            backup, _ = backup_restore.create_backup(source, backup_root)
            backup_restore.verify_backup(backup)
            manifest = backup_restore.verify_backup(backup); backup_restore.restore_backup(backup, target)
            self.assertEqual(manifest["components"], list(backup_restore.PAYLOAD_ROOTS))
            for relative, content in shared_expected.items():
                self.assertEqual((target / relative).read_text(encoding="utf-8"), content)
            for role_id, values in expected.items():
                for relative, content in values.items():
                    self.assertEqual((target / "data" / role_id / relative).read_text(encoding="utf-8"),
                                     content)
            self.assertNotEqual(
                (target / "data" / "fixture" / "memory" / "fiction.md").read_text(encoding="utf-8"),
                (target / "data" / "ghost" / "memory" / "fiction.md").read_text(encoding="utf-8"))

            keep = target / "data" / "fixture" / "workspace" / "local-keep.txt"
            keep.write_text("preserve this target", encoding="utf-8")
            (backup / "payload" / "data" / "ghost" / "chat.jsonl").write_text(
                "corrupted fictional backup", encoding="utf-8")
            with self.assertRaises(backup_restore.BackupError):
                backup_restore.restore_backup(backup, target)
            self.assertEqual(keep.read_text(encoding="utf-8"), "preserve this target")
            self.assertEqual(
                (target / "data" / "ghost" / "chat.jsonl").read_text(encoding="utf-8"),
                expected["ghost"]["chat.jsonl"])


def desktop_gate_ready():
    executable = os.environ.get("VEIL_ELECTRON")
    return (os.environ.get("VEIL_DESKTOP_RUNTIME_GATE") == "1" and
            bool(executable) and Path(executable).is_file())


@unittest.skipUnless(desktop_gate_ready(),
                     "set VEIL_DESKTOP_RUNTIME_GATE=1 and VEIL_ELECTRON to run local Electron integration")
class WebAgentElectronCloseoutTests(unittest.TestCase):
    def test_electron_uses_live_web_agent_and_recovers_after_one_model_failure(self):
        with tempfile.TemporaryDirectory(dir=REPO) as directory:
            root = Path(directory)
            make_character(root)
            cfg = load_character(root, "fixture")
            store = Store(root / "data")
            calls = []

            def offline_model(messages, **_kwargs):
                text = messages[-1]["content"]
                calls.append(text)
                if text == "temporary provider failure":
                    raise ConnectionError("fictional loopback provider failure")
                reply = SimpleNamespace(tool_calls=[], content="Fictional recovery reply.")
                return reply, 40, 8, 0

            chat = Chat(cfg, store, Budget(store), offline_model, output=lambda *_: None)
            activity = ActivityState(store.root / cfg.id / "resident-state.json")
            hub = Hub(cfg.name, activity)
            token = secrets.token_urlsafe(24)
            server = start_server(hub, token, 0)
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            environment = os.environ.copy()
            environment.update({
                "VEIL_RUNTIME_ORIGIN": origin,
                "VEIL_RUNTIME_TOKEN": token,
                "VEIL_RUNTIME_USER_DATA": str(root / "electron-user-data"),
            })
            state = None
            for _ in range(100):
                try:
                    from urllib.request import Request, urlopen
                    request = Request(origin + "/api/events?after=0",
                                      headers={"X-Token": token})
                    with urlopen(request, timeout=1) as response:
                        state = json.loads(response.read())
                    break
                except OSError:
                    time.sleep(0.05)
            self.assertIsNotNone(state, "isolated Web Agent did not become ready")

            runner_result = {}
            runner_error = {}

            def run_electron():
                try:
                    runner_result["result"] = subprocess.run(
                        [os.environ["VEIL_ELECTRON"],
                         str(REPO / "desktop" / "tests" / "runtime_closeout.cjs")],
                        cwd=REPO, env=environment, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=40)
                except BaseException as exc:
                    runner_error["error"] = exc
                finally:
                    hub.request_stop()

            runner = threading.Thread(target=run_electron, daemon=True)
            runner.start()
            try:
                # The production chat loop installs the SIGINT handler, so keep
                # it on the main thread just as the real service does.
                serve_chat(chat, hub)
            finally:
                hub.request_stop()
                runner.join(timeout=45)
                server.shutdown()
                hub.stop()
                server.server_close()

            self.assertFalse(runner.is_alive(), "Electron integration did not stop cleanly")
            if "error" in runner_error:
                raise runner_error["error"]
            result = runner_result.get("result")
            self.assertIsNotNone(result, "Electron integration produced no process result")
            self.assertEqual(result.returncode, 0,
                             f"Electron failed: stdout={result.stdout!r}; stderr={result.stderr!r}")
            self.assertEqual(calls, ["temporary provider failure", "continue after recovery"])
            rows = store.read("fixture/chat.jsonl")
            self.assertEqual([row["content"] for row in rows if row["role"] == "user"],
                             ["temporary provider failure", "continue after recovery"])
            self.assertEqual([row["content"] for row in rows if row["role"] == "assistant"],
                             ["Fictional recovery reply."])
            self.assertEqual(sum(event["kind"] == "error" for event in hub.events), 1)
            self.assertEqual(sum(event["kind"] == "assistant" for event in hub.events), 1)

if __name__ == "__main__":
    unittest.main()
