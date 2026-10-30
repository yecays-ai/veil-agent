import json
import os
import tempfile
import unittest
from pathlib import Path

from desktop_icons import (
    DesktopIcon,
    DesktopIconsError,
    DesktopIconsLayoutBlocked,
    DesktopIconsManager,
    DesktopIconsPaused,
    DisplayMetrics,
    FakeDesktopIconsBackend,
    generate_icon_id,
    validate_layout_plan,
)
from veil_agent import Store


class DesktopIconsCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(self.temp_dir.name)
        self.metrics = DisplayMetrics(
            resolution=(1920, 1080),
            monitors=[{'id': 0, 'left': 0, 'top': 0, 'right': 1920, 'bottom': 1080, 'is_primary': True}],
            scale_factor=1.0,
            grid_spacing=(100, 100),
            auto_arrange=False,
            align_to_grid=True,
        )
        self.test_icons = [
            DesktopIcon('icon_recycle_bin_system', '回收站', 'system', 20, 20),
            DesktopIcon('icon_calculator_shortcut', '计算器', 'shortcut', 20, 120),
            DesktopIcon('icon_paint_shortcut', '画图', 'shortcut', 20, 220),
            DesktopIcon('icon_notes_file', 'notes.txt', 'file', 20, 320),
        ]
        self.backend = FakeDesktopIconsBackend(icons=self.test_icons, metrics=self.metrics)
        self.manager = DesktopIconsManager(
            store=self.store,
            character_id='peter',
            backend=self.backend,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_icon_id_slugification_and_deduplication(self):
        seen = set()
        id1 = generate_icon_id('Visual Studio Code', 'shortcut', seen)
        id2 = generate_icon_id('Visual Studio Code', 'shortcut', seen)
        id3 = generate_icon_id('123 App', 'file', seen)

        self.assertEqual(id1, 'visual_studio_code_shortcut')
        self.assertEqual(id2, 'visual_studio_code_shortcut_1')
        self.assertTrue(id3.startswith('item_'))

    def test_read_desktop_returns_metrics_and_icons(self):
        data = self.manager.read_desktop()
        self.assertEqual(data['count'], 4)
        self.assertEqual(data['metrics']['resolution'], [1920, 1080])
        self.assertFalse(data['metrics']['auto_arrange'])
        icon_ids = [i['icon_id'] for i in data['icons']]
        self.assertIn('icon_recycle_bin_system', icon_ids)
        self.assertIn('icon_calculator_shortcut', icon_ids)

    def test_validate_layout_plan_success(self):
        moves = [
            {'icon_id': 'icon_calculator_shortcut', 'x': 200, 'y': 200},
            {'icon_id': 'icon_paint_shortcut', 'x': 400, 'y': 400},
        ]
        ok, reason = validate_layout_plan(self.test_icons, moves, self.metrics)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_validate_layout_plan_rejects_out_of_bounds(self):
        moves = [
            {'icon_id': 'icon_calculator_shortcut', 'x': 2000, 'y': 500},
        ]
        ok, reason = validate_layout_plan(self.test_icons, moves, self.metrics)
        self.assertFalse(ok)
        self.assertIn('超出可见显示器区域', reason)

        moves_negative = [
            {'icon_id': 'icon_calculator_shortcut', 'x': -100, 'y': 200},
        ]
        ok_neg, reason_neg = validate_layout_plan(self.test_icons, moves_negative, self.metrics)
        self.assertFalse(ok_neg)
        self.assertIn('超出可见显示器区域', reason_neg)

    def test_validate_layout_plan_rejects_overlaps(self):
        # Overlapping two proposed moves
        moves = [
            {'icon_id': 'icon_calculator_shortcut', 'x': 200, 'y': 200},
            {'icon_id': 'icon_paint_shortcut', 'x': 210, 'y': 210},
        ]
        ok, reason = validate_layout_plan(self.test_icons, moves, self.metrics)
        self.assertFalse(ok)
        self.assertIn('重叠', reason)

        # Overlapping proposed move with an unmoved icon ('icon_recycle_bin_system' is at 20, 20)
        moves_unmoved_conflict = [
            {'icon_id': 'icon_calculator_shortcut', 'x': 25, 'y': 25},
        ]
        ok_unmoved, reason_unmoved = validate_layout_plan(self.test_icons, moves_unmoved_conflict, self.metrics)
        self.assertFalse(ok_unmoved)
        self.assertIn('重叠', reason_unmoved)

    def test_validate_layout_plan_rejects_auto_arrange(self):
        metrics_auto = DisplayMetrics(
            resolution=(1920, 1080),
            monitors=[{'id': 0, 'left': 0, 'top': 0, 'right': 1920, 'bottom': 1080, 'is_primary': True}],
            auto_arrange=True,
        )
        moves = [{'icon_id': 'icon_calculator_shortcut', 'x': 200, 'y': 200}]
        ok, reason = validate_layout_plan(self.test_icons, moves, metrics_auto)
        self.assertFalse(ok)
        self.assertIn('自动排列', reason)

    def test_take_snapshot_and_list_snapshots(self):
        snap = self.manager.take_snapshot(trigger='test')
        self.assertTrue(snap['snapshot_id'].startswith('snap-'))
        self.assertEqual(snap['icon_count'], 4)
        self.assertEqual(snap['trigger'], 'test')

        snaps = self.manager.list_snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]['snapshot_id'], snap['snapshot_id'])

        latest = self.manager.get_latest_snapshot()
        self.assertIsNotNone(latest)
        self.assertEqual(latest['snapshot_id'], snap['snapshot_id'])

    def test_arrange_icons_takes_snapshot_and_updates_coordinates(self):
        moves = [
            {'icon_id': 'icon_calculator_shortcut', 'x': 500, 'y': 100},
            {'icon_id': 'icon_paint_shortcut', 'x': 500, 'y': 220},
        ]
        result = self.manager.arrange_icons(moves, trigger='autonomous_tidy')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['moved_count'], 2)
        self.assertTrue(result['verified'])
        self.assertIn('snapshot_id', result)

        # Verify coordinates actually changed in backend
        icons = {i.icon_id: i for i in self.backend.get_icons()}
        self.assertEqual((icons['icon_calculator_shortcut'].x, icons['icon_calculator_shortcut'].y), (500, 100))
        self.assertEqual((icons['icon_paint_shortcut'].x, icons['icon_paint_shortcut'].y), (500, 220))

        # Verify snapshot was created before modification
        snapshots = self.manager.list_snapshots()
        self.assertGreaterEqual(len(snapshots), 1)
        prev_calc = [i for i in snapshots[0]['icons'] if i['icon_id'] == 'icon_calculator_shortcut'][0]
        self.assertEqual((prev_calc['x'], prev_calc['y']), (20, 120))

    def test_arrange_icons_interrupted_by_user_input(self):
        moves = [
            {'icon_id': 'icon_calculator_shortcut', 'x': 600, 'y': 100},
            {'icon_id': 'icon_paint_shortcut', 'x': 600, 'y': 220},
        ]
        # Interrupt immediately
        with self.assertRaises(DesktopIconsPaused):
            self.manager.arrange_icons(moves, should_stop=lambda: True)

    def test_restore_snapshot_reverts_positions(self):
        # 1. Arrange icons
        moves = [
            {'icon_id': 'icon_calculator_shortcut', 'x': 700, 'y': 300},
            {'icon_id': 'icon_paint_shortcut', 'x': 700, 'y': 420},
        ]
        arrange_res = self.manager.arrange_icons(moves)
        snap_id = arrange_res['snapshot_id']

        # 2. Restore from snapshot
        restore_res = self.manager.restore_snapshot(snapshot_id=snap_id)
        self.assertEqual(restore_res['status'], 'success')
        self.assertEqual(restore_res['restored_count'], 4)
        self.assertTrue(restore_res['verified'])

        # Check restored coordinates in backend match original values
        icons = {i.icon_id: i for i in self.backend.get_icons()}
        self.assertEqual((icons['icon_calculator_shortcut'].x, icons['icon_calculator_shortcut'].y), (20, 120))
        self.assertEqual((icons['icon_paint_shortcut'].x, icons['icon_paint_shortcut'].y), (20, 220))

    def test_validate_layout_plan_rejects_non_integers(self):
        moves_float = [{'icon_id': 'icon_calculator_shortcut', 'x': 200.5, 'y': 200}]
        ok, reason = validate_layout_plan(self.test_icons, moves_float, self.metrics)
        self.assertFalse(ok)
        self.assertIn('坐标必须为整数', reason)

        moves_bool = [{'icon_id': 'icon_calculator_shortcut', 'x': True, 'y': 200}]
        ok_b, reason_b = validate_layout_plan(self.test_icons, moves_bool, self.metrics)
        self.assertFalse(ok_b)
        self.assertIn('坐标必须为整数', reason_b)

    def test_take_snapshot_without_store_raises(self):
        mgr_no_store = DesktopIconsManager(store=None, character_id='peter', backend=self.backend)
        with self.assertRaises(DesktopIconsError):
            mgr_no_store.take_snapshot()

    def test_restore_snapshot_handles_unmatched_or_new_icons(self):
        # Take snapshot of 4 icons
        snap = self.manager.take_snapshot()

        # Remove notes.txt and add a new game shortcut that CONFLICTS with recycle bin at (20, 20)
        del self.backend.icons['icon_notes_file']
        self.backend.icons['icon_game_shortcut'] = DesktopIcon('icon_game_shortcut', '游戏', 'shortcut', 20, 20)

        # Restore
        res = self.manager.restore_snapshot(snapshot_id=snap['snapshot_id'])
        self.assertEqual(res['status'], 'partial')
        # notes.txt is unmatched
        self.assertIn('notes.txt', res['unmatched'])
        # recycle bin target (20, 20) conflicted with new game icon and was skipped safely
        self.assertEqual(len(res['skipped_conflicts']), 1)
        self.assertEqual(res['skipped_conflicts'][0]['icon_id'], 'icon_recycle_bin_system')
        # Calculator and paint were restored
        self.assertEqual(res['restored_count'], 2)
        # New icon at (20, 20) was preserved untouched without overlap
        self.assertIn('icon_game_shortcut', self.backend.icons)
        self.assertEqual((self.backend.icons['icon_game_shortcut'].x, self.backend.icons['icon_game_shortcut'].y), (20, 20))


if __name__ == '__main__':
    unittest.main()
