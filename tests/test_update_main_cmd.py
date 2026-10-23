import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@unittest.skipUnless(os.name == "nt", "Windows command script")
class UpdateMainCommandTests(unittest.TestCase):
    def test_updater_recovers_diverged_main_and_stays_on_main(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO) as temp:
            tmp_path = Path(temp)
            remote = tmp_path / "remote.git"
            seed = tmp_path / "seed"
            repo = tmp_path / "repo"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
            _git(seed, "config", "user.email", "test@example.invalid")
            _git(seed, "config", "user.name", "Updater Test")
            desktop = seed / "desktop"
            desktop.mkdir()
            shutil.copy2(REPO / "desktop" / "package.json", desktop / "package.json")
            shutil.copy2(REPO / ".gitignore", seed / ".gitignore")
            (seed / "state.txt").write_text("base\n", encoding="utf-8")
            updater = REPO / "update-main.cmd"
            (seed / "update-main.cmd").write_bytes(updater.read_bytes())
            collision_checker = REPO / "update-main-collisions.ps1"
            (seed / "update-main-collisions.ps1").write_bytes(collision_checker.read_bytes())
            _git(seed, "add", ".gitignore", "desktop/package.json", "state.txt",
                 "update-main.cmd", "update-main-collisions.ps1")
            _git(seed, "commit", "-m", "base")
            _git(seed, "remote", "add", "origin", str(remote))
            _git(seed, "push", "-u", "origin", "main")
            subprocess.run(["git", "clone", "-b", "main", str(remote), str(repo)], check=True, capture_output=True)
            _git(repo, "config", "user.email", "test@example.invalid")
            _git(repo, "config", "user.name", "Updater Test")

            # Model an installed desktop package without downloading Electron in this test.
            packager = repo / "desktop" / "node_modules" / ".bin" / "electron-packager.cmd"
            packager.parent.mkdir(parents=True)
            packager.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
            fake_bin = tmp_path / "fake-bin"
            fake_bin.mkdir()
            npm_log = tmp_path / "npm-arguments.txt"
            (fake_bin / "npm.cmd").write_text(
                "@echo off\r\necho %*>>\"%VEIL_TEST_NPM_LOG%\"\r\nexit /b 0\r\n",
                encoding="utf-8",
            )
            (fake_bin / "tasklist.cmd").write_text(
                "@echo off\r\necho INFO: No tasks are running which match the specified criteria.\r\nexit /b 0\r\n",
                encoding="utf-8",
            )
            updater_env = os.environ.copy()
            updater_env["PATH"] = str(fake_bin) + os.pathsep + updater_env.get("PATH", "")
            updater_env["VEIL_TEST_NPM_LOG"] = str(npm_log)

            _git(repo, "switch", "-c", "ticket/example")
            (repo / "ticket.txt").write_text("ticket\n", encoding="utf-8")
            _git(repo, "add", "ticket.txt")
            _git(repo, "commit", "-m", "ticket")
            ticket_head = _git(repo, "rev-parse", "HEAD")
            _git(repo, "branch", "--force", "main", ticket_head)

            git_info = Path(_git(repo, "rev-parse", "--git-dir"))
            if not git_info.is_absolute():
                git_info = repo / git_info
            with (git_info / "info" / "exclude").open("a", encoding="utf-8") as exclude:
                exclude.write("\n/本地缓存.txt\n/ignored-cache/\n")
            (repo / "本地缓存.txt").write_text("local-only\n", encoding="utf-8")
            ignored_cache = repo / "ignored-cache"
            ignored_cache.mkdir()
            for index in range(3000):
                (ignored_cache / f"item-{index:04}.txt").write_text("preserve\n", encoding="utf-8")

            (seed / "state.txt").write_text("remote\n", encoding="utf-8")
            (seed / "本地缓存.txt").write_text("remote\n", encoding="utf-8")
            _git(seed, "add", "state.txt", "本地缓存.txt")
            _git(seed, "commit", "-m", "remote")
            _git(seed, "push", "origin", "main")
            remote_head = _git(seed, "rev-parse", "HEAD")

            blocked = subprocess.run(
                ["cmd", "/d", "/c", "chcp 437>nul & update-main.cmd"],
                cwd=repo,
                input="\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=updater_env,
            )

            self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
            self.assertNotIn("Not possible to fast-forward", blocked.stdout + blocked.stderr)
            self.assertIn("No local file was overwritten", blocked.stdout)
            self.assertEqual((repo / "本地缓存.txt").read_text(encoding="utf-8"), "local-only\n")
            self.assertEqual(_git(repo, "branch", "--show-current"), "ticket/example")
            self.assertEqual(_git(repo, "rev-parse", "main"), ticket_head)

            (repo / "本地缓存.txt").unlink()
            completed = subprocess.run(
                ["cmd", "/d", "/c", "chcp 437>nul & update-main.cmd"],
                cwd=repo,
                input="\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=updater_env,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("run package", npm_log.read_text(encoding="utf-8"))
            self.assertIn("This folder will stay on main", completed.stdout)
            self.assertEqual(_git(repo, "branch", "--show-current"), "main")
            self.assertEqual(_git(repo, "rev-parse", "main"), remote_head)
            self.assertEqual((ignored_cache / "item-0000.txt").read_text(encoding="utf-8"), "preserve\n")
            self.assertEqual(
                _git(repo, "rev-parse", f"backup/main-before-update-{ticket_head[:12]}"),
                ticket_head,
            )

            repeated = subprocess.run(
                ["cmd", "/d", "/c", "chcp 437>nul & update-main.cmd"],
                cwd=repo,
                input="\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=updater_env,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            self.assertEqual(_git(repo, "branch", "--show-current"), "main")
            self.assertEqual(_git(repo, "rev-parse", "main"), remote_head)
