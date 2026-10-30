"""Tests for Peter's isolated Lab Python sandbox."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lab_sandbox import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_FILE_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_SCRIPT_BYTES,
    MAX_TIMEOUT_SECONDS,
    MAX_WORKSPACE_DISK_BYTES,
    LabPythonSandbox,
    _is_reparse_point,
    _safe_lab_file,
    check_docker_available,
)
from tools_agent import FilesystemTools, LAB_ONLY, permitted_tools, tool_policy


class LabSandboxPathTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.lab = self.root / "lab"
        self.lab.mkdir()
        (self.lab / "test_script.py").write_text("print('hello')", encoding="utf-8")
        (self.lab / "notes.txt").write_text("notes", encoding="utf-8")

    def test_safe_path_resolves_inside_lab(self):
        resolved = _safe_lab_file(self.lab, "test_script.py", must_exist=True)
        self.assertEqual(resolved, (self.lab / "test_script.py").resolve())

    def test_path_traversal_is_blocked(self):
        for bad in ("../outside.py", "../../secret.py", "/etc/passwd", "C:\\secret.txt"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _safe_lab_file(self.lab, bad, must_exist=False)

    def test_device_and_reserved_names_are_blocked(self):
        for reserved in ("CON.py", "NUL.py", "COM1.py", "LPT1.py"):
            with self.subTest(reserved=reserved):
                with self.assertRaises(ValueError):
                    _safe_lab_file(self.lab, reserved, must_exist=False)

    def test_reparse_point_parent_is_rejected(self):
        sub = self.lab / "sub"
        sub.mkdir()
        target = sub / "script.py"
        target.write_text("print(1)", encoding="utf-8")

        with patch("lab_sandbox._is_reparse_point", side_effect=lambda p: Path(p) == sub):
            with self.assertRaises(ValueError):
                _safe_lab_file(self.lab, "sub/script.py", must_exist=True)

    def test_reparse_point_file_is_rejected(self):
        target = self.lab / "test_script.py"
        with patch("lab_sandbox._is_reparse_point", side_effect=lambda p: Path(p) == target):
            with self.assertRaises(ValueError):
                _safe_lab_file(self.lab, "test_script.py", must_exist=True)


class LabSandboxExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.lab = self.root / "lab"
        self.lab.mkdir()

        (self.lab / "good.py").write_text("print('calculation complete')\n", encoding="utf-8")
        (self.lab / "bad.py").write_text("1 / 0\n", encoding="utf-8")
        (self.lab / "not_py.txt").write_text("text only\n", encoding="utf-8")

    def test_disabled_by_default_sandbox_is_rejected(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (True, "29.0", "linux"))
        res, state, summary = sandbox.invoke({"path": "good.py"})
        self.assertEqual(state, "被拒绝")
        self.assertIn("disabled by configuration", res)

    def test_non_py_extension_is_rejected(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))
        res, state, summary = sandbox.invoke({"path": "not_py.txt"})
        self.assertEqual(state, "被拒绝")
        self.assertIn("Only Python scripts (*.py) can be executed", res)

    def test_docker_unavailable_fails_closed(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (False, "Docker daemon stopped", ""))
        res, state, summary = sandbox.invoke({"path": "good.py"})
        self.assertEqual(state, "被拒绝")
        self.assertIn("Docker is not ready", res)
        self.assertIn("Execution on the host Python is strictly prohibited", res)

    def test_mock_successful_run(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_proc.stdout = io.StringIO("Simulated Output\nAnswer: 42\n")
            mock_proc.stderr = io.StringIO("")
            mock_proc.wait = Mock(return_value=0)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py", "timeout": 10})

        self.assertEqual(state, "成功")
        self.assertIn("Status: Success (Exit Code: 0)", res)
        self.assertIn("Answer: 42", res)

    def test_mock_error_run_returns_real_traceback_with_success_state(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = 1
            mock_proc.returncode = 1
            mock_proc.stdout = io.StringIO("")
            mock_proc.stderr = io.StringIO(
                "Traceback (most recent call last):\n  File 'bad.py', line 1, in <module>\n    1 / 0\nZeroDivisionError: division by zero\n"
            )
            mock_proc.wait = Mock(return_value=1)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "bad.py"})

        self.assertEqual(state, "成功")
        self.assertIn("Status: Failed (Exit Code: 1)", res)
        self.assertIn("ZeroDivisionError: division by zero", res)

    def test_mock_timeout_terminates_container(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))
        killed = []

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                killed.append(cmd)
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = None
            mock_proc.returncode = 137
            mock_proc.stdout = io.StringIO("")
            mock_proc.stderr = io.StringIO("Killed due to timeout\n")
            mock_proc.wait = Mock(return_value=137)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("time.monotonic", side_effect=[0.0, 0.1, 5.0, 5.1]):
            res, state, summary = sandbox.invoke({"path": "good.py", "timeout": 2})

        self.assertEqual(state, "成功")
        self.assertIn("Timed Out", res)
        self.assertTrue(any("docker" in k and "kill" in k for k in killed))

    def test_should_stop_cancellation_terminates_container(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))
        killed = []

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                killed.append(cmd)
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = None
            mock_proc.returncode = 137
            mock_proc.stdout = io.StringIO("")
            mock_proc.stderr = io.StringIO("")
            mock_proc.wait = Mock(return_value=137)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py"}, should_stop=lambda: True)

        self.assertEqual(state, "成功")
        self.assertIn("Cancelled", res)
        self.assertTrue(any("docker" in k and "kill" in k for k in killed))

    def test_generated_files_are_saved_back_to_lab(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.returncode = 0
                return mock_proc

            # Find the workspace mounted in the command
            ws_idx = cmd.index("-v") + 1
            host_temp = cmd[ws_idx].split(":/workspace")[0]
            out_file = Path(host_temp) / "result.json"
            out_file.write_text('{"planets": 8}', encoding="utf-8")

            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_proc.stdout = io.StringIO("Simulation finished.\n")
            mock_proc.stderr = io.StringIO("")
            mock_proc.wait = Mock(return_value=0)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py"})

        self.assertEqual(state, "成功")
        self.assertIn("result.json", res)
        # Verify file exists in Peter's lab folder!
        saved_file = self.lab / "result.json"
        self.assertTrue(saved_file.is_file())
        self.assertEqual(saved_file.read_text(encoding="utf-8"), '{"planets": 8}')

    def test_docker_run_command_contains_all_isolation_flags(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.returncode = 0
                return mock_proc

            captured_cmd.extend(cmd)
            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_proc.stdout = io.StringIO("ok\n")
            mock_proc.stderr = io.StringIO("")
            mock_proc.wait = Mock(return_value=0)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py"})

        self.assertEqual(state, "成功")
        # Check security flags
        self.assertIn("--network", captured_cmd)
        self.assertEqual(captured_cmd[captured_cmd.index("--network") + 1], "none")
        self.assertIn("--memory", captured_cmd)
        self.assertEqual(captured_cmd[captured_cmd.index("--memory") + 1], "256m")
        self.assertIn("--memory-swap", captured_cmd)
        self.assertEqual(captured_cmd[captured_cmd.index("--memory-swap") + 1], "256m")
        self.assertIn("--cpus", captured_cmd)
        self.assertEqual(captured_cmd[captured_cmd.index("--cpus") + 1], "1.0")
        self.assertIn("--pids-limit", captured_cmd)
        self.assertEqual(captured_cmd[captured_cmd.index("--pids-limit") + 1], "64")
        self.assertIn("--cap-drop", captured_cmd)
        self.assertEqual(captured_cmd[captured_cmd.index("--cap-drop") + 1], "ALL")
        self.assertIn("--security-opt", captured_cmd)
        self.assertEqual(captured_cmd[captured_cmd.index("--security-opt") + 1], "no-new-privileges")
        self.assertIn("--read-only", captured_cmd)
        self.assertIn("--tmpfs", captured_cmd)
        # Check environment variables
        self.assertIn("PYTHONUNBUFFERED=1", captured_cmd)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", captured_cmd)
        self.assertIn("HOME=/workspace", captured_cmd)

    def test_timeout_skips_output_file_write_back(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.returncode = 0
                return mock_proc

            ws_idx = cmd.index("-v") + 1
            host_temp = cmd[ws_idx].split(":/workspace")[0]
            out_file = Path(host_temp) / "corrupt.json"
            out_file.write_text('{"truncated": true', encoding="utf-8")

            mock_proc.poll.return_value = None
            mock_proc.returncode = 137
            mock_proc.stdout = io.StringIO("")
            mock_proc.stderr = io.StringIO("Timeout killed\n")
            mock_proc.wait = Mock(return_value=137)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("time.monotonic", side_effect=[0.0, 0.1, 5.0, 5.1]):
            res, state, summary = sandbox.invoke({"path": "good.py", "timeout": 2})

        self.assertEqual(state, "成功")
        self.assertIn("Timed Out", res)
        self.assertFalse((self.lab / "corrupt.json").exists())

    def test_workspace_disk_limit_terminates_container(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))
        killed = []

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                killed.append(cmd)
                mock_proc.returncode = 0
                return mock_proc

            ws_idx = cmd.index("-v") + 1
            host_temp = cmd[ws_idx].split(":/workspace")[0]
            # Write a large file to exceed the workspace quota
            large_file = Path(host_temp) / "huge.dat"
            large_file.write_bytes(b"0" * 1024)

            mock_proc.poll.return_value = None
            mock_proc.returncode = 137
            mock_proc.stdout = io.StringIO("")
            mock_proc.stderr = io.StringIO("")
            mock_proc.wait = Mock(return_value=137)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("lab_sandbox.MAX_WORKSPACE_DISK_BYTES", 512):
            res, state, summary = sandbox.invoke({"path": "good.py"})

        self.assertEqual(state, "成功")
        self.assertIn("Workspace disk limit", res)
        self.assertTrue(any("docker" in k and "kill" in k for k in killed))

    def test_large_output_stream_drains_without_pipe_deadlock(self):
        sandbox = LabPythonSandbox(self.lab, enabled=True, docker_check_fn=lambda: (True, "29.0", "linux"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            # 500 lines of output to simulate substantial stream
            mock_proc.stdout = io.StringIO("Data line\n" * 500)
            mock_proc.stderr = io.StringIO("Log line\n" * 50)
            mock_proc.wait = Mock(return_value=0)
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py"})

        self.assertEqual(state, "成功")
        self.assertIn("Data line", res)


class LabSandboxToolPolicyTests(unittest.TestCase):
    def test_tool_policy_and_permitted_tools(self):
        self.assertEqual(tool_policy("run_lab_python"), "lab")
        self.assertIn("run_lab_python", LAB_ONLY)

        schema = SimpleNamespace(
            name="run_lab_python",
            description="run python",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        tools = permitted_tools([schema])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["function"]["name"], "run_lab_python")


class LabSandboxRealDockerTests(unittest.TestCase):
    """Integration test executing a real Python script inside Docker Desktop."""

    def setUp(self):
        if os.environ.get("CI") and os.environ.get("VEIL_DOCKER_SANDBOX_TEST") != "1":
            self.skipTest("Real Docker execution tests in CI require VEIL_DOCKER_SANDBOX_TEST=1")
        is_ready, _, _ = check_docker_available()
        if not is_ready:
            self.skipTest("Docker daemon is not running on this machine.")

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.lab = self.root / "lab"
        self.lab.mkdir()

    def test_real_docker_execution_success_and_file_generation(self):
        code = (
            "import os\n"
            "print('Hello from real container sandbox!')\n"
            "with open('solar_system.json', 'w') as f:\n"
            "    f.write('{\"planets\": 8}')\n"
        )
        (self.lab / "solar.py").write_text(code, encoding="utf-8")

        sandbox = LabPythonSandbox(self.lab, enabled=True)
        res, state, summary = sandbox.invoke({"path": "solar.py"})

        self.assertEqual(state, "成功")
        self.assertIn("Hello from real container sandbox!", res)
        self.assertIn("solar_system.json", res)

        generated = self.lab / "solar_system.json"
        self.assertTrue(generated.is_file())
        self.assertEqual(generated.read_text(encoding="utf-8"), '{"planets": 8}')

    def test_real_docker_error_capture(self):
        code = "val = 10 / 0\n"
        (self.lab / "div_zero.py").write_text(code, encoding="utf-8")

        sandbox = LabPythonSandbox(self.lab, enabled=True)
        res, state, summary = sandbox.invoke({"path": "div_zero.py"})

        self.assertEqual(state, "成功")
        self.assertIn("ZeroDivisionError: division by zero", res)
        self.assertIn("Status: Failed (Exit Code: 1)", res)

    def test_real_docker_network_blocked(self):
        code = (
            "import socket\n"
            "try:\n"
            "    s = socket.create_connection(('8.8.8.8', 53), timeout=1)\n"
            "    print('CONNECTED')\n"
            "except Exception as e:\n"
            "    print(f'BLOCKED: {type(e).__name__}')\n"
        )
        (self.lab / "net_test.py").write_text(code, encoding="utf-8")

        sandbox = LabPythonSandbox(self.lab, enabled=True)
        res, state, summary = sandbox.invoke({"path": "net_test.py"})

        self.assertEqual(state, "成功")
        self.assertIn("BLOCKED: OSError", res)
        self.assertNotIn("CONNECTED", res)


if __name__ == "__main__":
    unittest.main()
