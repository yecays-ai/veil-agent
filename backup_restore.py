"""Manual, offline cold backup and guarded restore for VEIL Agent local state."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid


FORMAT_VERSION = 1
DATA_LAYOUT_VERSION = 1
PAYLOAD_ROOTS = ("data", "characters", "persona", ".env")
OMBRE_RUNTIME_FILES = {
    "ombre.lock", "ombre.process.json", "ombre.stop", "ombre-http-ready.json",
    "process.log",
}
MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.sha256"
README_NAME = "README.txt"


class BackupError(Exception):
    """A safe, user-displayable backup or restore failure."""


def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry_is_excluded(relative):
    parts = PurePosixPath(relative).parts
    name = parts[-1]
    if relative == "data/session.lock":
        return True
    if "_agent" not in parts:
        return False
    after = parts[parts.index("_agent") + 1:]
    if len(after) == 1 and (name in OMBRE_RUNTIME_FILES or name.endswith(".tmp")):
        return True
    return "tmp" in after


def _payload_entries(root):
    files, directories = [], []
    for component in PAYLOAD_ROOTS:
        source = root / component
        if not source.exists() and not source.is_symlink():
            raise BackupError(f"缺少必要数据项：{component}")
        if source.is_symlink():
            raise BackupError(f"数据项是符号链接，已停止：{component}")
        if source.is_file():
            files.append(component)
            continue
        if not source.is_dir():
            raise BackupError(f"必要数据项类型不正确：{component}")
        directories.append(component)
        for current, dirnames, filenames in os.walk(source, followlinks=False):
            current_path = Path(current)
            safe_dirs = []
            for name in sorted(dirnames):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    raise BackupError(f"数据目录含符号链接，已停止：{relative}")
                if _entry_is_excluded(relative):
                    continue
                safe_dirs.append(name)
                directories.append(relative)
            dirnames[:] = safe_dirs
            for name in sorted(filenames):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if _entry_is_excluded(relative):
                    continue
                if path.is_symlink() or not path.is_file():
                    raise BackupError(f"数据文件类型不受支持，已停止：{relative}")
                files.append(relative)
    return sorted(set(files)), sorted(set(directories))


def _git_revision(root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True,
            text=True, timeout=3,
        )
        value = result.stdout.strip()
        return value if len(value) == 40 and all(c in "0123456789abcdef" for c in value) else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class _FileLock:
    def __init__(self, path, create):
        self.path = path
        self.handle = None
        if path.is_symlink():
            raise BackupError("服务锁文件是符号链接，已停止。")
        if not create and not path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = path.open("a+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.handle:
                self.handle.close()
                self.handle = None
            raise BackupError("VEIL 或 Ombre 仍在运行或正在写入，请先完整关闭服务后重试。") from exc

    def close(self):
        if self.handle is None:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def _acquire_cold_locks(root, *, create_session=False):
    locks = []
    try:
        data = root / "data"
        if data.is_symlink():
            raise BackupError("data 目录是符号链接，已停止。")
        locks.append(_FileLock(data / "session.lock", create=create_session and data.exists()))
        for path in sorted(data.glob("*/memory/_agent/ombre.lock")):
            relative = path.relative_to(data)
            candidate = data
            for part in relative.parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    raise BackupError("Ombre 服务锁路径含符号链接，已停止。")
            locks.append(_FileLock(path, create=False))
        return locks
    except Exception:
        for lock in reversed(locks):
            lock.close()
        raise


@contextmanager
def _cold_lock(root, *, create_session=False):
    locks = []
    try:
        locks.extend(_acquire_cold_locks(root, create_session=create_session))
        yield locks
    finally:
        for lock in reversed(locks):
            lock.close()


def _validate_character_files(staged_payload):
    characters = staged_payload / "characters"
    personas = staged_payload / "persona"
    configs = sorted(characters.glob("*/character.toml"))
    if not configs:
        raise BackupError("备份中没有角色配置（characters/<id>/character.toml）。")
    for config_path in configs:
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
            persona = config["persona"]
            relative = PurePosixPath(persona)
            if not isinstance(persona, str) or relative.is_absolute() or ".." in relative.parts:
                raise ValueError("invalid persona path")
            if not relative.parts or relative.parts[0] != "persona":
                raise ValueError("persona must be inside persona/")
            persona_path = staged_payload.joinpath(*relative.parts)
            if not persona_path.is_file() or persona_path.is_symlink():
                raise ValueError("persona file is missing")
        except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
            raise BackupError(f"角色配置无法被当前程序读取：{config_path.name}") from exc


def create_backup(root, destination):
    root = Path(root).resolve()
    destination = Path(destination).expanduser()
    if destination.is_symlink():
        raise BackupError("备份位置不能是符号链接。")
    destination = destination.resolve()
    if _inside(destination, root):
        raise BackupError("备份位置必须在 VEIL 项目目录之外，建议使用另一块磁盘。")
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise BackupError("备份位置必须是普通目录。")

    stage = destination / f".veil-backup-{uuid.uuid4().hex}.tmp"
    final = destination / f"VEIL-Agent-Backup-{_utc_stamp()}-{uuid.uuid4().hex[:8]}"
    try:
        with _cold_lock(root, create_session=True):
            destination.mkdir(parents=True, exist_ok=True)
            files, directories = _payload_entries(root)
            stage.mkdir()
            payload = stage / "payload"
            payload.mkdir()
            for relative in directories:
                (payload / Path(*PurePosixPath(relative).parts)).mkdir(parents=True, exist_ok=True)
            manifest_files = []
            for relative in files:
                source = root / Path(*PurePosixPath(relative).parts)
                target = payload / Path(*PurePosixPath(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                manifest_files.append({
                    "path": f"payload/{relative}", "size": target.stat().st_size,
                    "sha256": _sha256(target),
                })
            manifest = {
                "application": "veil-agent",
                "backup_format_version": FORMAT_VERSION,
                "data_layout_version": DATA_LAYOUT_VERSION,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_revision": _git_revision(root),
                "components": list(PAYLOAD_ROOTS),
                "directories": [f"payload/{path}" for path in directories],
                "files": manifest_files,
                "excluded_runtime_items": [
                    "data/session.lock", "data/<role>/memory/_agent/ombre.lock",
                    "data/<role>/memory/_agent/ombre.process.json",
                    "data/<role>/memory/_agent/ombre.stop",
                    "data/<role>/memory/_agent/ombre-http-ready.json",
                    "data/<role>/memory/_agent/process.log",
                    "data/<role>/memory/_agent/tmp/", "*.tmp",
                ],
            }
            manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            (stage / MANIFEST_NAME).write_bytes(manifest_bytes)
            (stage / MANIFEST_HASH_NAME).write_text(
                hashlib.sha256(manifest_bytes).hexdigest() + "  " + MANIFEST_NAME + "\n", encoding="ascii"
            )
            (stage / README_NAME).write_text(
                "VEIL Agent cold backup. This folder contains plaintext personal data and may contain API keys in .env.\n"
                "Store it only on an encrypted local drive or other access-controlled offline media. Do not put it in\n"
                "cloud-synced folders, GitHub, logs, or third-party services. The backup program does not encrypt or upload it.\n",
                encoding="utf-8",
            )
            verify_backup(stage)
            os.replace(stage, final)
            return final, len(manifest_files)
    except Exception:
        if stage.exists() and _inside(stage.resolve(), destination):
            shutil.rmtree(stage, ignore_errors=True)
        raise


def _safe_manifest_path(value):
    if not isinstance(value, str) or "\\" in value:
        raise BackupError("备份清单含无效路径。")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise BackupError("备份清单含越界路径。")
    if path.parts[0] != "payload":
        raise BackupError("备份清单含越界路径。")
    return path


def verify_backup(backup):
    backup = Path(backup).expanduser()
    if backup.is_symlink():
        raise BackupError("备份目录不能是符号链接。")
    backup = backup.resolve()
    if not backup.is_dir():
        raise BackupError("备份目录不存在或不是普通目录。")
    if {path.name for path in backup.iterdir()} != {
        MANIFEST_NAME, MANIFEST_HASH_NAME, README_NAME, "payload"
    }:
        raise BackupError("备份根目录含缺失或未登记的项目。")
    for name in (MANIFEST_NAME, MANIFEST_HASH_NAME, README_NAME, "payload"):
        path = backup / name
        if not path.exists() or path.is_symlink():
            raise BackupError(f"备份缺少必要项目：{name}")
        if name == "payload" and not path.is_dir():
            raise BackupError("备份 payload 必须是目录。")
    raw = (backup / MANIFEST_NAME).read_bytes()
    recorded = (backup / MANIFEST_HASH_NAME).read_text(encoding="ascii").split()
    if len(recorded) != 2 or recorded[1] != MANIFEST_NAME or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), recorded[0]
    ):
        raise BackupError("备份清单校验失败。")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise BackupError("备份清单格式损坏。") from exc
    if not isinstance(manifest, dict):
        raise BackupError("备份清单格式损坏。")
    if (manifest.get("application") != "veil-agent" or
            manifest.get("backup_format_version") != FORMAT_VERSION or
            manifest.get("data_layout_version") != DATA_LAYOUT_VERSION):
        raise BackupError("此备份格式或数据布局版本不受当前程序支持，未改动目标目录。")
    if manifest.get("components") != list(PAYLOAD_ROOTS):
        raise BackupError("备份组件清单与当前程序不兼容。")

    file_entries = manifest.get("files")
    directory_entries = manifest.get("directories")
    if not isinstance(file_entries, list) or not isinstance(directory_entries, list):
        raise BackupError("备份清单缺少文件或目录列表。")
    expected_files, expected_dirs = set(), set()
    casefold_paths = set()
    for entry in file_entries:
        if not isinstance(entry, dict):
            raise BackupError("备份文件清单格式损坏。")
        path = _safe_manifest_path(entry.get("path"))
        if path.as_posix() in expected_files:
            raise BackupError("备份清单含重复文件。")
        if path.as_posix().casefold() in casefold_paths:
            raise BackupError("备份含 Windows 不兼容的大小写重复路径。")
        casefold_paths.add(path.as_posix().casefold())
        expected_files.add(path.as_posix())
        if (type(entry.get("size")) is not int or entry["size"] < 0 or
                not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64 or
                any(ch not in "0123456789abcdef" for ch in entry["sha256"])):
            raise BackupError("备份文件清单格式损坏。")
    for value in manifest.get("directories", []):
        path = _safe_manifest_path(value)
        if path.as_posix().casefold() in casefold_paths:
            raise BackupError("备份含 Windows 不兼容的大小写重复路径。")
        casefold_paths.add(path.as_posix().casefold())
        expected_dirs.add(path.as_posix())
    if expected_files & expected_dirs:
        raise BackupError("备份清单将同一路径登记为文件和目录。")
    if "payload/.env" not in expected_files or "payload/data" not in expected_dirs:
        raise BackupError("备份缺少必要的环境或角色数据。")

    payload = backup / "payload"
    actual_files, actual_dirs = set(), {"payload"}
    for current, dirnames, filenames in os.walk(payload, followlinks=False):
        current_path = Path(current)
        for name in list(dirnames):
            path = current_path / name
            if path.is_symlink():
                raise BackupError("备份含符号链接，拒绝恢复。")
            actual_dirs.add(path.relative_to(backup).as_posix())
        for name in filenames:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise BackupError("备份含不支持的文件类型，拒绝恢复。")
            actual_files.add(path.relative_to(backup).as_posix())
    if actual_files != expected_files or actual_dirs != expected_dirs | {"payload"}:
        raise BackupError("备份内容与清单不一致，拒绝恢复。")
    for entry in manifest["files"]:
        path = backup / Path(*PurePosixPath(entry["path"]).parts)
        if path.stat().st_size != entry["size"] or not hmac.compare_digest(
            _sha256(path), entry["sha256"]
        ):
            raise BackupError(f"备份文件完整性校验失败：{PurePosixPath(entry['path']).name}")
    _validate_character_files(payload)
    return manifest


def _validate_target_paths(target, manifest):
    files = [entry["path"] for entry in manifest["files"]]
    dirs = manifest["directories"]
    conflicts = []
    for relative in sorted(dirs + files):
        safe = _safe_manifest_path(relative)
        parts = safe.parts[1:]
        current = target
        for index, part in enumerate(parts):
            current = current / part
            if not current.exists() and not current.is_symlink():
                continue
            if current.is_symlink():
                raise BackupError("恢复目标路径含符号链接，已停止以避免写到目标目录之外。")
            is_expected_dir = index < len(parts) - 1 or relative in dirs
            if is_expected_dir and not current.is_dir():
                raise BackupError("恢复目标中有文件占用了备份所需的目录位置，未改动目标。")
            if not is_expected_dir and current.exists():
                if not current.is_file():
                    raise BackupError("恢复目标中文件类型与备份冲突，未改动目标。")
                conflicts.append((relative, current))
    return conflicts


def _copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".restore-{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_backup(backup, target, *, replace=False, confirm=input):
    manifest = verify_backup(backup)
    backup = Path(backup).expanduser().resolve()
    target = Path(target).expanduser()
    if target.is_symlink():
        raise BackupError("恢复目标不能是符号链接。")
    target = target.resolve()
    if _inside(target, backup) or _inside(backup, target):
        raise BackupError("备份与恢复目标不能互相包含。")
    if target.exists() and (target.is_symlink() or not target.is_dir()):
        raise BackupError("恢复目标必须是普通目录或尚不存在的目录。")
    if not target.parent.is_dir():
        raise BackupError("恢复目标的上级目录不存在。")

    stage = Path(tempfile.mkdtemp(prefix=".veil-restore-stage-", dir=target.parent))
    target_created = not target.exists()
    created_dirs = []
    written = []
    safety = None
    try:
        with _cold_lock(target) as locks:
            source_payload = backup / "payload"
            for relative in manifest["directories"]:
                _safe_manifest_path(relative)
                (stage / Path(*PurePosixPath(relative).parts[1:])).mkdir(parents=True, exist_ok=True)
            for entry in manifest["files"]:
                source = source_payload / Path(*PurePosixPath(entry["path"]).parts[1:])
                staged = stage / Path(*PurePosixPath(entry["path"]).parts[1:])
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staged)
                if staged.stat().st_size != entry["size"] or _sha256(staged) != entry["sha256"]:
                    raise BackupError("备份在校验后发生变化，恢复已取消。")

            if replace:
                existing_components = []
                if target.exists():
                    for name in PAYLOAD_ROOTS:
                        component = target / name
                        if component.is_symlink():
                            raise BackupError(f"恢复目标的 {name} 不能是符号链接。")
                        if component.exists():
                            existing_components.append(name)
                if existing_components:
                    answer = confirm(
                        "将把目标现有的 data/、characters/、persona/ 和 .env 整体移入安全副本，"
                        "再恢复干净备份。输入 RESTORE 确认："
                    )
                    if answer != "RESTORE":
                        raise BackupError("未确认替换，目标目录未改动。")
                    safety = target.parent / f"{target.name}.before-restore-{_utc_stamp()}-{uuid.uuid4().hex[:8]}"
                    safety.mkdir()
                else:
                    conflicts = _validate_target_paths(target, manifest) if target.exists() else []
                    if conflicts:
                        raise BackupError("恢复目标已有冲突数据；未改动目标目录。")

                moved = []
                installed = []
                for lock in reversed(locks):
                    lock.close()
                locks.clear()
                try:
                    try:
                        if not target.exists():
                            target.mkdir()
                        for name in existing_components:
                            component = target / name
                            saved = safety / name
                            os.replace(component, saved)
                            moved.append((component, saved))
                        for name in PAYLOAD_ROOTS:
                            staged_component = stage / name
                            if staged_component.exists():
                                destination = target / name
                                os.replace(staged_component, destination)
                                installed.append((destination, staged_component))
                        for entry in manifest["files"]:
                            destination = target / Path(*PurePosixPath(entry["path"]).parts[1:])
                            if destination.stat().st_size != entry["size"] or _sha256(destination) != entry["sha256"]:
                                raise BackupError("恢复后完整性复核失败，正在回滚整个目标数据集。")
                    except Exception as failure:
                        rollback_errors = []
                        for destination, staged_component in reversed(installed):
                            try:
                                os.replace(destination, staged_component)
                            except OSError as exc:
                                rollback_errors.append(exc)
                        for original_path, saved_path in reversed(moved):
                            try:
                                if not original_path.exists() and not original_path.is_symlink():
                                    os.replace(saved_path, original_path)
                            except OSError as exc:
                                rollback_errors.append(exc)
                        if rollback_errors:
                            raise BackupError(
                                f"恢复失败且回滚未能全部完成；原有数据安全副本保留在：{safety}"
                            ) from failure
                        if safety is not None:
                            shutil.rmtree(safety, ignore_errors=True)
                        raise
                finally:
                    data = target / "data"
                    if data.is_dir() and not data.is_symlink():
                        try:
                            locks.extend(_acquire_cold_locks(target, create_session=True))
                        except (OSError, BackupError) as exc:
                            raise BackupError(
                                f"恢复后无法重新锁定 data/；原有数据安全副本位置：{safety}"
                            ) from exc
            else:
                conflicts = _validate_target_paths(target, manifest) if target.exists() else []
                if conflicts:
                    raise BackupError(
                        f"恢复目标已有 {len(conflicts)} 个同名数据文件；未覆盖任何内容。"
                        "若确认覆盖，请使用 --replace 并在提示时输入 RESTORE。"
                    )
                if not target.exists():
                    target.mkdir()
                all_directories = set(manifest["directories"])
                for relative in sorted(all_directories, key=lambda value: (len(PurePosixPath(value).parts), value)):
                    destination = target / Path(*PurePosixPath(relative).parts[1:])
                    if not destination.exists():
                        destination.mkdir()
                        created_dirs.append(destination)
                try:
                    for entry in manifest["files"]:
                        source = stage / Path(*PurePosixPath(entry["path"]).parts[1:])
                        destination = target / Path(*PurePosixPath(entry["path"]).parts[1:])
                        _copy_atomic(source, destination)
                        written.append(destination)
                    for entry in manifest["files"]:
                        destination = target / Path(*PurePosixPath(entry["path"]).parts[1:])
                        if destination.stat().st_size != entry["size"] or _sha256(destination) != entry["sha256"]:
                            raise BackupError("恢复后完整性复核失败，正在回滚目标文件。")
                except Exception:
                    for destination in reversed(written):
                        destination.unlink(missing_ok=True)
                    for directory in reversed(created_dirs):
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
                    raise
        return len(manifest["files"]), safety
    except Exception:
        if target_created and target.exists() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        if safety is not None and safety.exists() and not any(safety.iterdir()):
            safety.rmdir()
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _parser():
    parser = argparse.ArgumentParser(description="VEIL Agent 手动冷备份与恢复（离线）")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="VEIL 完全关闭后创建一份新备份")
    create.add_argument("--destination", required=True, help="备份根目录，必须位于项目目录之外")
    verify = sub.add_parser("verify", help="检查备份清单、格式版本和每个文件的 SHA-256")
    verify.add_argument("--backup", required=True, help="备份目录")
    restore = sub.add_parser("restore", help="校验并恢复至明确指定的目录")
    restore.add_argument("--backup", required=True, help="备份目录")
    restore.add_argument("--target", required=True, help="明确指定的项目根目录")
    restore.add_argument("--replace", action="store_true", help="确认后先整体保存现有角色数据，再恢复干净备份")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parent
    try:
        if args.command == "create":
            path, count = create_backup(root, args.destination)
            print(f"备份成功：{path}（{count} 个文件）")
            print("安全提示：备份未加密，包含人设、记忆和 .env；只存放在加密的本地磁盘/受保护离线介质。")
        elif args.command == "verify":
            manifest = verify_backup(args.backup)
            print(f"校验通过：{len(manifest['files'])} 个文件；数据布局 v{manifest['data_layout_version']}。")
        else:
            count, safety = restore_backup(args.backup, args.target, replace=args.replace)
            print(f"恢复成功：{args.target}（{count} 个文件）")
            if safety:
                print(f"原有角色数据安全副本：{safety}")
    except (BackupError, OSError) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
