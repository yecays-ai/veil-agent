"""Bounded, container-isolated Python execution sandbox for Peter's Lab."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

DEFAULT_TIMEOUT_SECONDS = 15
MAX_TIMEOUT_SECONDS = 30
MAX_OUTPUT_BYTES = 50 * 1024        # 50 KB before truncation
MAX_OUTPUT_LINES = 1000
MAX_SCRIPT_BYTES = 2 * 1024 * 1024   # 2 MB max script size
MAX_GENERATED_FILES = 20
MAX_FILE_BYTES = 5 * 1024 * 1024     # 5 MB per generated file
MAX_TOTAL_OUTPUT_BYTES = 20 * 1024 * 1024  # 20 MB total

DOCKER_IMAGE = "python:3.12-slim"
CONTAINER_MEMORY = "256m"
CONTAINER_CPUS = "1.0"
CONTAINER_PIDS = 64


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & REPARSE_POINT
    )


def _path_key(path: Path) -> str:
    value = os.path.normcase(os.path.normpath(str(path)))
    if os.name == "nt":
        value = value.removeprefix("\\\\?\\").removeprefix("\\\\.\\")
    return value.rstrip("\\/")


def _safe_lab_file(lab: Path, relative: str, *, must_exist: bool = True) -> Path:
    """Resolve a relative Lab path safely, forbidding path traversal and reparse points."""
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise ValueError("The path must be a non-empty string.")
    relative = relative.strip()
    if relative.casefold().startswith(f"{lab.name.casefold()}/"):
        relative = relative[len(lab.name) + 1 :]
    elif relative.casefold().startswith(f"{lab.name.casefold()}\\"):
        relative = relative[len(lab.name) + 1 :]
    if relative.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", relative) or "\\" in relative:
        raise ValueError("The path must be relative to Lab and use forward slashes.")
    parts = relative.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    if (
        any(part in ("", ".", "..") for part in parts)
        or any(
            any(ord(c) < 32 or c in '<>:"|?*' for c in part)
            or part.endswith((" ", "."))
            or part.split(".")[0].upper() in reserved
            or re.fullmatch(r"(COM|LPT)[1-9\u00b9\u00b2\u00b3]", part.split(".")[0].upper())
            for part in parts
        )
    ):
        raise ValueError("The path must stay inside Lab and use valid file names.")

    lab = lab.resolve(strict=True)
    candidate = lab
    for part in parts:
        candidate = candidate / part
        if candidate.exists() or candidate.is_symlink():
            if _is_reparse_point(candidate):
                raise ValueError("Links and junctions are not allowed in a Lab path.")
    if must_exist:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"The Lab file '{relative}' does not exist.") from exc
        if _path_key(resolved) != _path_key(candidate) or not resolved.is_relative_to(lab):
            raise ValueError("The path must stay inside Lab.")
        if not resolved.is_file():
            raise ValueError(f"'{relative}' must be a regular file.")
        return resolved
    return candidate


_DOCKER_CACHE: Tuple[float, bool, str] = (0.0, False, "")
_CACHE_TTL = 5.0


def check_docker_available(timeout: float = 2.0) -> Tuple[bool, str]:
    """Check if the local Docker engine is accessible (with 5s debounce cache)."""
    global _DOCKER_CACHE
    now = time.monotonic()
    last_time, cached_ok, cached_ver = _DOCKER_CACHE
    if now - last_time < _CACHE_TTL:
        return cached_ok, cached_ver
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            res = (True, proc.stdout.strip())
            _DOCKER_CACHE = (now, res[0], res[1])
            return res
        err = proc.stderr.strip() or "Docker daemon is not running."
        res = (False, err)
        _DOCKER_CACHE = (now, res[0], res[1])
        return res
    except FileNotFoundError:
        res = (False, "Docker executable 'docker' was not found on PATH.")
        _DOCKER_CACHE = (now, res[0], res[1])
        return res
    except subprocess.TimeoutExpired:
        res = (False, "Docker engine response timed out.")
        _DOCKER_CACHE = (now, res[0], res[1])
        return res
    except Exception as exc:
        res = (False, str(exc))
        _DOCKER_CACHE = (now, res[0], res[1])
        return res


class LabPythonSandbox:
    """Manages isolated Python executions inside dedicated Docker containers."""

    def __init__(self, lab: Path, *, enabled: bool = True, docker_check_fn: Optional[Callable[[], Tuple[bool, str]]] = None):
        self.lab = Path(lab).resolve()
        self.enabled = bool(enabled)
        self._docker_check_fn = docker_check_fn or check_docker_available
        self._lock = threading.Lock()

        self.tools = [
            SimpleNamespace(
                name="run_lab_python",
                description=(
                    "Execute a Python script in Peter's Lab inside an isolated, network-disabled container sandbox. "
                    "Captures stdout, stderr, exit code, duration, and generated files. "
                    "The execution has no network access and cannot access files outside Lab or host credentials. "
                    "Files created in the experiment workspace are verified and saved back to Lab."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path to the Python script in Lab (e.g. 'lab/experiment.py' or 'experiment.py').",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional command-line arguments to pass to the script.",
                        },
                        "project_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of additional Lab file paths needed by the experiment "
                                "(e.g. helper modules or data files). If omitted, sibling files in the script's folder are staged."
                            ),
                        },
                        "timeout": {
                            "type": "integer",
                            "description": f"Execution timeout in seconds (default {DEFAULT_TIMEOUT_SECONDS}, maximum {MAX_TIMEOUT_SECONDS}).",
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            )
        ]

    def begin_request(self) -> None:
        pass

    def invoke(
        self,
        args: Dict[str, Any],
        *,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Tuple[str, str, str]:
        summary = "run_lab_python"
        if not self.enabled:
            return "Lab Python execution is disabled by configuration.", "被拒绝", summary

        if not isinstance(args, dict) or "path" not in args:
            return "A 'path' parameter is required to specify the Python script.", "被拒绝", summary

        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "Script path must be a non-empty string.", "被拒绝", summary

        summary = raw_path

        # Validate script path
        try:
            script_file = _safe_lab_file(self.lab, raw_path, must_exist=True)
        except ValueError as exc:
            return f"Cannot access script: {exc}", "被拒绝", summary

        if script_file.suffix.casefold() != ".py":
            return f"Only Python scripts (*.py) can be executed. Received: '{raw_path}'.", "被拒绝", summary

        try:
            if script_file.stat().st_size > MAX_SCRIPT_BYTES:
                return (
                    f"Script size ({script_file.stat().st_size} bytes) exceeds maximum allowed size ({MAX_SCRIPT_BYTES} bytes).",
                    "被拒绝",
                    summary,
                )
        except OSError as exc:
            return f"Failed to inspect script file: {exc}", "出错", summary

        # Check Docker availability; strictly forbid host execution
        is_ready, detail = self._docker_check_fn()
        if not is_ready:
            return (
                f"Lab Python execution is unavailable because Docker is not ready: {detail}. "
                "Execution on the host Python is strictly prohibited to protect the system.",
                "被拒绝",
                summary,
            )

        # Parse options
        raw_timeout = args.get("timeout", DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout = max(1, min(int(raw_timeout), MAX_TIMEOUT_SECONDS))
        except (ValueError, TypeError):
            timeout = DEFAULT_TIMEOUT_SECONDS

        raw_args = args.get("args") or []
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            return "'args' must be a list of string arguments.", "被拒绝", summary

        raw_project_files = args.get("project_files")
        if raw_project_files is not None:
            if not isinstance(raw_project_files, list) or not all(isinstance(f, str) for f in raw_project_files):
                return "'project_files' must be a list of string file paths.", "被拒绝", summary

        # Collect files to stage
        try:
            staged_files = self._collect_staged_files(script_file, raw_project_files)
        except ValueError as exc:
            return f"Invalid project file: {exc}", "被拒绝", summary

        # Execute in isolated temporary directory
        with self._lock:
            with tempfile.TemporaryDirectory(prefix="veil-lab-run-") as temp_dir:
                temp_path = Path(temp_dir).resolve()
                return self._run_sandbox(
                    temp_path=temp_path,
                    script_file=script_file,
                    staged_files=staged_files,
                    args=raw_args,
                    timeout=timeout,
                    summary=summary,
                    should_stop=should_stop,
                )

    def _collect_staged_files(
        self, script_file: Path, explicit_files: Optional[List[str]]
    ) -> Dict[str, Path]:
        """Determine all files to copy into the sandbox workspace."""
        staged: Dict[str, Path] = {}
        script_rel = script_file.relative_to(self.lab).as_posix()
        staged[script_rel] = script_file

        if explicit_files is not None:
            for item in explicit_files:
                resolved = _safe_lab_file(self.lab, item, must_exist=True)
                rel = resolved.relative_to(self.lab).as_posix()
                staged[rel] = resolved
        else:
            # Stage sibling regular files in the script's directory up to reasonable limits
            script_dir = script_file.parent
            count = 0
            for entry in script_dir.iterdir():
                if count >= 30:
                    break
                if entry.is_file() and not _is_reparse_point(entry):
                    try:
                        rel = entry.relative_to(self.lab).as_posix()
                        staged[rel] = entry
                        count += 1
                    except ValueError:
                        continue
        return staged

    @staticmethod
    def _file_hash(path: Path) -> Optional[bytes]:
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.digest()
        except Exception:
            return None

    def _run_sandbox(
        self,
        *,
        temp_path: Path,
        script_file: Path,
        staged_files: Dict[str, Path],
        args: List[str],
        timeout: int,
        summary: str,
        should_stop: Optional[Callable[[], bool]],
    ) -> Tuple[str, str, str]:
        # Enforce total staged file size limit
        try:
            total_staged_bytes = sum(src.stat().st_size for src in staged_files.values())
        except OSError as exc:
            return f"Failed to access staged experiment files: {exc}", "出错", summary

        if total_staged_bytes > MAX_TOTAL_OUTPUT_BYTES:
            return (
                f"Staged project files exceed total limit ({total_staged_bytes} > {MAX_TOTAL_OUTPUT_BYTES} bytes).",
                "被拒绝",
                summary,
            )

        # 1. Copy staged files into temp_path and record content hashes
        initial_hashes: Dict[str, bytes] = {}
        script_rel_in_lab = script_file.relative_to(self.lab).as_posix()

        # Workspace directory permissions for container access
        try:
            os.chmod(temp_path, 0o700)
        except Exception:
            pass

        try:
            for rel, src in staged_files.items():
                dest = temp_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                curr = dest.parent
                while curr != temp_path and curr != curr.parent:
                    try:
                        os.chmod(curr, 0o700)
                    except Exception:
                        pass
                    curr = curr.parent
                shutil.copy2(src, dest)
                try:
                    os.chmod(dest, stat.S_IMODE(src.stat().st_mode) | stat.S_IRUSR | stat.S_IWUSR)
                except Exception:
                    pass
                h = self._file_hash(src)
                if h is not None:
                    initial_hashes[rel] = h
        except Exception as exc:
            return f"Failed to stage experiment files: {exc}", "出错", summary

        # 2. Build docker run invocation
        container_name = f"veil-lab-{uuid.uuid4().hex[:12]}"
        script_in_workspace = script_rel_in_lab

        cmd = [
            "docker",
            "run",
            "--name",
            container_name,
            "--rm",
            "--network",
            "none",
            "--cpus",
            CONTAINER_CPUS,
            "--memory",
            CONTAINER_MEMORY,
            "--pids-limit",
            str(CONTAINER_PIDS),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
        ]
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        cmd.extend([
            "-v",
            f"{temp_path}:/workspace",
            "-w",
            "/workspace",
            "-e",
            "PYTHONUNBUFFERED=1",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "HOME=/workspace",
            DOCKER_IMAGE,
            "python",
            script_in_workspace,
        ])
        cmd.extend(args)

        # 3. Launch container process
        start_time = time.monotonic()
        timed_out = False
        cancelled = False

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            return f"Failed to start sandbox container: {exc}", "出错", summary

        # 4. Monitor loop with timeout and should_stop
        while True:
            ret = proc.poll()
            if ret is not None:
                break
            if should_stop and should_stop():
                cancelled = True
                self._terminate_container(container_name, proc)
                break
            if time.monotonic() - start_time > timeout:
                timed_out = True
                self._terminate_container(container_name, proc)
                break
            time.sleep(0.05)

        duration = time.monotonic() - start_time
        stdout, stderr = proc.communicate()

        # Ensure container cleanup
        try:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=5)
        except Exception:
            pass

        # 5. Format execution status
        if cancelled:
            status_desc = "Cancelled (interrupted by user or system)"
        elif timed_out:
            status_desc = f"Timed Out (exceeded {timeout}s limit)"
        elif proc.returncode == 0:
            status_desc = "Success (Exit Code: 0)"
        else:
            status_desc = f"Failed (Exit Code: {proc.returncode})"

        # 6. Check and write back generated files (skip on timeout or cancellation to prevent corrupting artifacts)
        if cancelled or timed_out:
            generated_files, write_back_errors = [], []
        else:
            generated_files, write_back_errors = self._sync_output_files(temp_path, initial_hashes)

        # 7. Format output report for Peter
        report = self._build_report(
            script=script_rel_in_lab,
            status=status_desc,
            exit_code=proc.returncode if not (cancelled or timed_out) else None,
            duration=duration,
            stdout=stdout,
            stderr=stderr,
            generated_files=generated_files,
            write_back_errors=write_back_errors,
        )

        return report, "成功", summary

    def _terminate_container(self, container_name: str, proc: subprocess.Popen) -> None:
        try:
            subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass

    def _sync_output_files(
        self, temp_path: Path, initial_hashes: Dict[str, bytes]
    ) -> Tuple[List[Tuple[str, int]], List[str]]:
        """Inspect the workspace after execution and safely copy new/modified files back to Lab."""
        new_or_modified: List[Tuple[str, Path]] = []
        errors: List[str] = []

        total_bytes = 0
        file_count = 0

        for root, dirs, files in os.walk(temp_path):
            dirs[:] = [d for d in dirs if not _is_reparse_point(Path(root) / d)]
            for file_name in files:
                full_path = Path(root) / file_name
                if _is_reparse_point(full_path) or not full_path.is_file():
                    continue

                rel_posix = full_path.relative_to(temp_path).as_posix()
                try:
                    stat_info = full_path.stat()
                except OSError:
                    continue

                # Check if new or modified by content hash
                current_hash = self._file_hash(full_path)
                if rel_posix not in initial_hashes or (current_hash is not None and initial_hashes[rel_posix] != current_hash):
                    # Validate relative path and file size
                    if stat_info.st_size > MAX_FILE_BYTES:
                        errors.append(f"File '{rel_posix}' ({stat_info.st_size} bytes) exceeded per-file limit ({MAX_FILE_BYTES} bytes); skipped.")
                        continue
                    if file_count >= MAX_GENERATED_FILES:
                        errors.append(f"Exceeded max generated files count ({MAX_GENERATED_FILES}); '{rel_posix}' skipped.")
                        continue
                    if total_bytes + stat_info.st_size > MAX_TOTAL_OUTPUT_BYTES:
                        errors.append(f"Total output size exceeded limit ({MAX_TOTAL_OUTPUT_BYTES} bytes); '{rel_posix}' skipped.")
                        continue

                    # Validate filename safety
                    try:
                        _safe_lab_file(self.lab, rel_posix, must_exist=False)
                    except ValueError as exc:
                        errors.append(f"File '{rel_posix}' has an invalid path ({exc}); skipped.")
                        continue

                    new_or_modified.append((rel_posix, full_path))
                    total_bytes += stat_info.st_size
                    file_count += 1

        saved: List[Tuple[str, int]] = []
        for rel_posix, src_path in new_or_modified:
            tmp_dest = None
            try:
                dest_path = _safe_lab_file(self.lab, rel_posix, must_exist=False)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                curr = dest_path.parent
                while curr != self.lab and curr != curr.parent:
                    if _is_reparse_point(curr):
                        raise ValueError("Reparse point or symlink detected in destination parent directory.")
                    curr = curr.parent

                # Atomic write-back via temporary sibling file and replace
                tmp_dest = dest_path.with_name(f".tmp.{uuid.uuid4().hex}.{dest_path.name}")
                shutil.copy2(src_path, tmp_dest)
                os.replace(tmp_dest, dest_path)
                saved.append((f"{self.lab.name}/{rel_posix}", src_path.stat().st_size))
            except Exception as exc:
                errors.append(f"Failed to save '{rel_posix}' to Lab: {exc}")
                if tmp_dest is not None:
                    try:
                        if tmp_dest.exists():
                            tmp_dest.unlink()
                    except Exception:
                        pass

        return saved, errors

    def _build_report(
        self,
        *,
        script: str,
        status: str,
        exit_code: Optional[int],
        duration: float,
        stdout: str,
        stderr: str,
        generated_files: List[Tuple[str, int]],
        write_back_errors: List[str],
    ) -> str:
        lines = [
            "=== Lab Python Execution Report ===",
            f"Script: {self.lab.name}/{script}",
            f"Status: {status}",
            f"Duration: {duration:.2f}s",
            "",
            "--- Standard Output ---",
            self._truncate_stream(stdout) if stdout.strip() else "(None)",
            "",
            "--- Standard Error ---",
            self._truncate_stream(stderr) if stderr.strip() else "(None)",
        ]

        if generated_files:
            lines.append("")
            lines.append(f"--- Generated / Updated Files ({len(generated_files)}) ---")
            for path, size in generated_files:
                lines.append(f"- {path} ({self._format_bytes(size)})")

        if write_back_errors:
            lines.append("")
            lines.append("--- File Save Warnings ---")
            for err in write_back_errors:
                lines.append(f"- {err}")

        return "\n".join(lines)

    @staticmethod
    def _truncate_stream(text: str) -> str:
        text = text.rstrip()
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= MAX_OUTPUT_BYTES:
            lines = text.splitlines()
            if len(lines) <= MAX_OUTPUT_LINES:
                return text
            return "\n".join(lines[:MAX_OUTPUT_LINES]) + f"\n... [Output truncated; showing first {MAX_OUTPUT_LINES} lines]"

        truncated = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
        return truncated + f"\n... [Output truncated; exceeded {MAX_OUTPUT_BYTES // 1024} KB limit]"

    @staticmethod
    def _format_bytes(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"
