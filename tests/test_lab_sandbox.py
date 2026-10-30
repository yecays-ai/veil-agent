"""Tests for Peter's isolated Lab Python sandbox."""
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
        (self.root / "outside.py").write_text("print('outside')", encoding="utf-8")

    def test_safe_lab_file_resolves_valid_script(self):
        resolved = _safe_lab_file(self.lab, "test_script.py")
        self.assertEqual(resolved, self.lab / "test_script.py")
        resolved_with_lab_prefix = _safe_lab_file(self.lab, "lab/test_script.py")
        self.assertEqual(resolved_with_lab_prefix, self.lab / "test_script.py")

    def test_safe_lab_file_rejects_escaping_paths(self):
        for bad in (
            "../outside.py",
            "..\\outside.py",
            "/outside.py",
            "C:/outside.py",
            "lab/../../outside.py",
            "",
            "   ",
            "test\x00script.py",
        ):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    _safe_lab_file(self.lab, bad)

    def test_safe_lab_file_rejects_missing_file_when_must_exist(self):
        with self.assertRaises(ValueError):
            _safe_lab_file(self.lab, "nonexistent.py", must_exist=True)

    def test_safe_lab_file_rejects_reparse_point(self):
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

    def test_non_py_extension_is_rejected(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (True, "29.0"))
        res, state, summary = sandbox.invoke({"path": "not_py.txt"})
        self.assertEqual(state, "被拒绝")
        self.assertIn("Only Python scripts (*.py) can be executed", res)

    def test_docker_unavailable_fails_closed(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (False, "Docker daemon stopped"))
        res, state, summary = sandbox.invoke({"path": "good.py"})
        self.assertEqual(state, "被拒绝")
        self.assertIn("Docker is not ready", res)
        self.assertIn("Execution on the host Python is strictly prohibited", res)

    def test_disabled_sandbox_is_rejected(self):
        sandbox = LabPythonSandbox(self.lab, enabled=False, docker_check_fn=lambda: (True, "29.0"))
        res, state, summary = sandbox.invoke({"path": "good.py"})
        self.assertEqual(state, "被拒绝")
        self.assertIn("disabled by configuration", res)

    def test_mock_successful_run(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (True, "29.0"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.communicate.return_value = ("", "")
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = ("Simulated Output\nAnswer: 42\n", "")
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py", "timeout": 10})

        self.assertEqual(state, "成功")
        self.assertIn("Status: Success (Exit Code: 0)", res)
        self.assertIn("Answer: 42", res)

    def test_mock_error_run_returns_real_traceback_with_success_state(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (True, "29.0"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.communicate.return_value = ("", "")
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = 1
            mock_proc.returncode = 1
            mock_proc.communicate.return_value = (
                "",
                "Traceback (most recent call last):\n  File 'bad.py', line 1, in <module>\n    1 / 0\nZeroDivisionError: division by zero\n",
            )
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "bad.py"})

        # State is '成功' so Peter receives the execution report and can debug it
        self.assertEqual(state, "成功")
        self.assertIn("Status: Failed (Exit Code: 1)", res)
        self.assertIn("ZeroDivisionError: division by zero", res)

    def test_mock_timeout_terminates_container(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (True, "29.0"))
        killed = []

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                killed.append(cmd)
                mock_proc.communicate.return_value = ("", "")
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = None
            mock_proc.returncode = 137
            mock_proc.communicate.return_value = ("", "Killed due to timeout")
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("time.monotonic", side_effect=[0.0, 0.1, 5.0, 5.1]):
            res, state, summary = sandbox.invoke({"path": "good.py", "timeout": 2})

        self.assertEqual(state, "成功")
        self.assertIn("Timed Out", res)
        self.assertTrue(any("docker" in k and "kill" in k for k in killed))

    def test_should_stop_cancellation_terminates_container(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (True, "29.0"))
        killed = []

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                killed.append(cmd)
                mock_proc.communicate.return_value = ("", "")
                mock_proc.returncode = 0
                return mock_proc

            mock_proc.poll.return_value = None
            mock_proc.returncode = 137
            mock_proc.communicate.return_value = ("", "")
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py"}, should_stop=lambda: True)

        self.assertEqual(state, "成功")
        self.assertIn("Cancelled", res)
        self.assertTrue(any("docker" in k and "kill" in k for k in killed))

    def test_generated_files_are_saved_back_to_lab(self):
        sandbox = LabPythonSandbox(self.lab, docker_check_fn=lambda: (True, "29.0"))

        def fake_popen(cmd, **kwargs):
            mock_proc = Mock()
            mock_proc.__enter__ = Mock(return_value=mock_proc)
            mock_proc.__exit__ = Mock(return_value=None)
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] in ("rm", "kill"):
                mock_proc.communicate.return_value = ("", "")
                mock_proc.returncode = 0
                return mock_proc

            # Find the workspace mounted in the command
            ws_idx = cmd.index("-v") + 1
            host_temp = cmd[ws_idx].split(":/workspace")[0]
            # Create simulated output file in host_temp
            out_file = Path(host_temp) / "result.json"
            out_file.write_text('{"planets": 8}', encoding="utf-8")

            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = ("Simulation finished.\n", "")
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            res, state, summary = sandbox.invoke({"path": "good.py"})

        self.assertEqual(state, "成功")
        self.assertIn("result.json", res)
        # Verify file exists in Peter's lab folder!
        saved_file = self.lab / "result.json"
        self.assertTrue(saved_file.is_file())
        self.assertEqual(saved_file.read_text(encoding="utf-8"), '{"planets": 8}')


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
        is_ready, _ = check_docker_available()
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

        sandbox = LabPythonSandbox(self.lab)
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

        sandbox = LabPythonSandbox(self.lab)
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

        sandbox = LabPythonSandbox(self.lab)
        res, state, summary = sandbox.invoke({"path": "net_test.py"})

        self.assertEqual(state, "成功")
        self.assertIn("BLOCKED: OSError", res)
        self.assertNotIn("CONNECTED", res)


if __name__ == "__main__":
    unittest.main()
