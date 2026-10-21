import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


class DependencyLockTests(unittest.TestCase):
    def test_every_direct_requirement_is_exactly_present_in_lock(self):
        direct = {}
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*([A-Za-z0-9_.-]+)==([^\s;]+)", line)
            self.assertIsNotNone(match, f"Direct dependency is not exactly pinned: {line}")
            direct[normalize(match.group(1))] = match.group(2)

        locked = {}
        for line in (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*([A-Za-z0-9_.-]+)==([^\s;]+)", line)
            if match:
                locked[normalize(match.group(1))] = match.group(2)

        self.assertTrue(direct)
        for name, version in direct.items():
            self.assertEqual(locked.get(name), version, f"{name} is missing or differs in requirements.lock.txt")

    def test_installers_use_locked_requirements_and_supported_python(self):
        setup = (ROOT / "setup.bat").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("requirements.lock.txt", setup)
        self.assertIn("requirements.lock.txt", workflow)
        self.assertIn("(3, 11) <= sys.version_info[:2] < (3, 13)", setup)

    def test_ombre_installer_uses_the_archive_locked_requirements(self):
        installer = (ROOT / "setup_memory.py").read_text(encoding="utf-8")
        self.assertIn("source / 'requirements.lock.txt'", installer)
        self.assertIn("ARCHIVE_SHA256", installer)

    def test_security_workflow_is_manual_scheduled_and_report_only(self):
        workflow = (ROOT / ".github" / "workflows" / "dependency-security.yml").read_text(encoding="utf-8")
        sample = (ROOT / "tests" / "fixtures" / "vulnerable-requirements.txt").read_text(encoding="utf-8")
        production_lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("pip-audit", workflow)
        self.assertNotIn("--fix", workflow)
        self.assertIn("idna==2.5", sample)
        self.assertNotIn("idna==2.5", production_lock)


if __name__ == "__main__":
    unittest.main()
