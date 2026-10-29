import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UpdateBatTests(unittest.TestCase):
    """Ticket 108: the one-click updater must stay small and never rearrange Lan's checkout."""

    def setUp(self):
        self.text = (ROOT / 'update.bat').read_text(encoding='utf-8')
        self.commands = '\n'.join(line for line in self.text.splitlines()
                                  if not line.lstrip().lower().startswith(('rem', 'echo')))

    def test_never_switches_stashes_or_resets(self):
        for forbidden in ('git stash', 'git switch', 'git checkout', 'git reset', 'git clean', 'git merge'):
            self.assertNotIn(forbidden, self.commands)

    def test_only_fast_forwards_main(self):
        self.assertIn('git pull --ff-only origin main', self.commands)
        self.assertRegex(self.commands, r'if /i not "%BRANCH%"=="main" goto not_main')

    def test_refuses_while_peter_runs(self):
        self.assertIn('IMAGENAME eq VEIL.exe', self.commands)
        self.assertIn('session.lock', self.commands)
        self.assertLess(self.commands.index('goto still_running'), self.commands.index('git pull'))

    def test_rebuilds_desktop_app_from_stamp(self):
        self.assertIn('npm run package', self.commands)
        self.assertIn('git rev-list -1 HEAD -- desktop', self.commands)
        self.assertRegex(self.commands, r'>"%STAMP%" echo %DESKTOP_REV%')

    def test_runs_from_a_copy_because_pull_may_rewrite_it(self):
        self.assertIn('--running-copy', self.commands)
        self.assertLess(self.commands.index('update-running.bat'), self.commands.index('git pull'))

    def test_helper_scripts_are_found_from_the_agent_folder_not_the_running_copy(self):
        # The running copy lives in .local-tmp, so %~dp0 would point there (ticket 170).
        self.assertNotIn('%~dp0scripts', self.commands)
        self.assertIn('-File "%CD%\\scripts\\install-nowhere.ps1"', self.commands)

    def test_checked_out_with_windows_line_endings(self):
        attributes = (ROOT / '.gitattributes').read_text(encoding='utf-8')
        self.assertTrue(re.search(r'^update\.bat\s+text\s+eol=crlf$', attributes, re.M))


if __name__ == '__main__':
    unittest.main()
