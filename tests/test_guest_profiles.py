"""Ticket 207: Chrome Guest temporary profiles are owned, recycled and swept safely (fake folders/processes)."""
import os
import tempfile
import time
import unittest
from pathlib import Path

import guest_profiles as gp


class FakeProcess:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class GuestProfileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.parent = self.root / 'veil-chrome-guest'

    def test_create_marks_ownership_and_remove_deletes_only_owned_folders(self):
        profile = gp.create_profile(self.parent)
        (profile / 'Default').mkdir()
        self.assertTrue(gp.remove_profile(profile, self.parent))
        self.assertFalse(profile.exists())
        stranger = self.root / 'users-own-chrome-profile'
        stranger.mkdir()
        (stranger / 'Cookies').write_text('x')
        self.assertFalse(gp.remove_profile(stranger, self.parent))
        unmarked = self.parent / 'no-marker'
        unmarked.mkdir()
        self.assertFalse(gp.remove_profile(unmarked, self.parent))
        self.assertTrue(stranger.exists() and unmarked.exists())

    def test_one_watcher_thread_serves_many_launches_and_cleans_after_exit(self):
        watcher = gp.GuestProfileWatcher(poll_seconds=0.01, parent=self.parent)
        launches = [(FakeProcess(), gp.create_profile(self.parent)) for _ in range(5)]
        for process, directory in launches:
            watcher.watch(process, directory)
        threads = [t for t in __import__('threading').enumerate() if t.name == 'veil-chrome-guest-cleanup']
        self.assertEqual(len(threads), 1)
        self.assertEqual(watcher.poll_once(), 5)
        self.assertTrue(all(d.exists() for _, d in launches))
        launches[0][0].code = launches[2][0].code = 0
        self.assertEqual(watcher.poll_once(), 3)
        self.assertFalse(launches[0][1].exists() or launches[2][1].exists())
        self.assertTrue(launches[1][1].exists())
        for process, _ in launches:
            process.code = 0
        deadline = time.time() + 5
        while watcher.thread is not None and time.time() < deadline:
            time.sleep(0.02)
        self.assertIsNone(watcher.thread)
        self.assertEqual(list(p for p in self.parent.iterdir()), [])

    def test_sweep_removes_only_dead_owners_old_enough_and_skips_others(self):
        now = time.time()

        def make(name, created, owner):
            directory = self.parent / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / gp.MARKER).write_text(
                '{"owner_pid": %d, "created": %f}' % (owner, created), encoding='utf-8')
            return directory

        dead_old = make('dead-old', now - 7200, 111)
        dead_new = make('dead-new', now - 60, 111)
        alive_old = make('alive-old', now - 7200, 222)
        ancient = make('ancient', now - gp.STALE_ANYWAY - 5, 222)
        current = make('current', now - 7200, 111)
        corrupt = self.parent / 'corrupt'
        corrupt.mkdir()
        (corrupt / gp.MARKER).write_text('{broken', encoding='utf-8')
        loose_file = self.parent / 'note.txt'
        loose_file.write_text('x')
        removed = gp.sweep_stale(self.parent, now=now, alive=lambda pid: pid == 222, skip=[current])
        self.assertEqual({p.name for p in removed}, {'dead-old', 'ancient'})
        for kept in (dead_new, alive_old, current, corrupt, loose_file):
            self.assertTrue(kept.exists(), kept)

    def test_sweep_ignores_missing_parent_and_links(self):
        self.assertEqual(gp.sweep_stale(self.root / 'absent'), [])
        if hasattr(os, 'symlink'):
            target = self.root / 'precious'
            target.mkdir()
            (target / gp.MARKER).write_text('{"owner_pid": 1, "created": 0}')
            self.parent.mkdir()
            try:
                os.symlink(target, self.parent / 'link', target_is_directory=True)
            except OSError:
                self.skipTest('symlinks unavailable')
            self.assertEqual(gp.sweep_stale(self.parent, alive=lambda pid: False), [])
            self.assertTrue(target.exists())


if __name__ == '__main__':
    unittest.main()
