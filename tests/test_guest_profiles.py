"""Ticket 207: Chrome Guest temporary profiles are owned, recycled and swept safely (fake folders/processes)."""
import os
import tempfile
import threading
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

    def test_watcher_cleans_each_browser_after_it_exits(self):
        watcher = gp.GuestProfileWatcher(poll_seconds=3600, parent=self.parent)  # polled by hand here
        launches = [(FakeProcess(), gp.create_profile(self.parent)) for _ in range(5)]
        for process, directory in launches:
            watcher.watch(process, directory)
        threads = [t for t in threading.enumerate() if t.name == 'veil-chrome-guest-cleanup']
        self.assertEqual(len(threads), 1)
        self.assertEqual(watcher.poll_once(), 5)
        self.assertTrue(all(d.exists() for _, d in launches))
        launches[0][0].code = launches[2][0].code = 0
        self.assertEqual(watcher.poll_once(), 3)
        self.assertFalse(launches[0][1].exists() or launches[2][1].exists())
        self.assertTrue(launches[1][1].exists())

    def test_watcher_thread_ends_when_no_browser_is_left(self):
        watcher = gp.GuestProfileWatcher(poll_seconds=0.01, parent=self.parent)
        process, directory = FakeProcess(), gp.create_profile(self.parent)
        process.code = 0
        watcher.watch(process, directory)
        deadline = time.time() + 5
        while watcher.thread is not None and time.time() < deadline:
            time.sleep(0.02)
        self.assertIsNone(watcher.thread)
        self.assertFalse(directory.exists())

    def test_locked_profile_is_retried_not_forgotten(self):
        watcher = gp.GuestProfileWatcher(poll_seconds=3600, parent=self.parent)
        process, directory = FakeProcess(), gp.create_profile(self.parent)
        process.code = 0
        real_remove = gp.remove_profile
        gp.remove_profile = lambda *a, **k: False  # Chrome / antivirus still holds a file
        try:
            watcher.watched.append((process, directory))
            self.assertEqual(watcher.poll_once(), 1)
        finally:
            gp.remove_profile = real_remove
        self.assertEqual(watcher.poll_once(), 0)
        self.assertFalse(directory.exists())

    def test_legacy_temp_folders_are_swept_only_when_old_real_and_prefixed(self):
        now = time.time()
        old, young, other, plain = (self.root / n for n in ('veil-chrome-guest-abc', 'veil-chrome-guest-new',
                                                            'my-chrome-profile', 'veil-chrome-guest'))
        for folder in (old, young, other, plain):
            folder.mkdir()
        os.utime(old, (now - gp.LEGACY_STALE_AFTER - 10,) * 2)
        os.utime(other, (now - gp.LEGACY_STALE_AFTER - 10,) * 2)
        os.utime(plain, (now - gp.LEGACY_STALE_AFTER - 10,) * 2)
        self.assertEqual(gp.sweep_legacy(self.root, now=now), [old])
        self.assertTrue(young.exists() and other.exists() and plain.exists())

    def test_marker_failure_leaves_no_folder_and_link_parent_is_refused(self):
        real_write = Path.write_text
        Path.write_text = lambda *a, **k: (_ for _ in ()).throw(OSError('disk full'))
        try:
            with self.assertRaises(OSError):
                gp.create_profile(self.parent)
        finally:
            Path.write_text = real_write
        self.assertEqual(list(self.parent.iterdir()), [])
        if hasattr(os, 'symlink'):
            target = self.root / 'elsewhere'
            target.mkdir()
            link = self.root / 'linked-parent'
            try:
                os.symlink(target, link, target_is_directory=True)
            except OSError:
                return
            with self.assertRaises(OSError):
                gp.create_profile(link)
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(gp.sweep_stale(link), [])

    def test_sweep_removes_only_dead_owners_old_enough_and_skips_others(self):
        now = time.time()

        def make(name, created, owner):
            directory = self.parent / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / gp.MARKER).write_text(
                '{"owner_pid": %d, "created": %f}' % (owner, created), encoding='utf-8')
            return directory

        dead_old = make('dead-old', now - gp.STALE_AFTER_DEAD_OWNER - 60, 111)
        dead_new = make('dead-new', now - 3600, 111)
        alive_old = make('alive-old', now - gp.STALE_AFTER_DEAD_OWNER - 60, 222)
        ancient = make('ancient', now - gp.STALE_ANYWAY - 5, 222)
        current = make('current', now - gp.STALE_AFTER_DEAD_OWNER - 60, 111)
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
