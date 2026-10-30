import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from desktop_icons import DesktopIcon, DisplayMetrics, FakeDesktopIconsBackend
from long_tasks import READ_ONLY_TOOLS
from tools_agent import tool_policy
from veil_agent import Store
from web_agent import _get_desktop_icons_manager
from windows_interaction import (
    DesktopInputMonitor,
    WindowsInteractionBackend,
    WindowsInteractionTools,
)


class FakeInputMonitor:
    def __init__(self, idle=True):
        self.idle = idle

    def require_idle(self, _minimum_seconds):
        pass

    def idle_seconds(self):
        return 10 if self.idle else 0

    def snapshot(self):
        return 0

    def changed_since(self, snapshot):
        return False


class DesktopIconsToolsTests(unittest.TestCase):
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
        self.icons = [
            DesktopIcon('icon_calc_shortcut', '计算器', 'shortcut', 10, 10),
            DesktopIcon('icon_paint_shortcut', '画图', 'shortcut', 10, 110),
        ]
        self.backend = FakeDesktopIconsBackend(icons=self.icons, metrics=self.metrics)
        self.interaction_tools = WindowsInteractionTools(
            enabled=True,
            applications={},
            platform_enabled=True,
            input_monitor=FakeInputMonitor(),
            desktop_icons_backend=self.backend,
            store=self.store,
            character_id='peter',
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tool_policies_and_read_only_declarations(self):
        self.assertEqual(tool_policy('desktop_icons_read'), 'desktop_read')
        self.assertEqual(tool_policy('desktop_icons_arrange'), 'desktop_control')
        self.assertEqual(tool_policy('desktop_icons_restore'), 'desktop_control')
        self.assertNotIn('desktop_icons_read', READ_ONLY_TOOLS)

    def test_invoke_desktop_icons_read(self):
        result, state, summary = self.interaction_tools.invoke('desktop_icons_read', {})
        self.assertEqual(state, '成功')
        self.assertEqual(summary, 'Desktop icons inspection')
        self.assertIn('count', str(result))
        self.assertIn('计算器', str(result))

    def test_invoke_desktop_icons_arrange_and_restore(self):
        # 1. Arrange icons
        moves = [
            {'icon_id': 'icon_calc_shortcut', 'x': 300, 'y': 300},
            {'icon_id': 'icon_paint_shortcut', 'x': 300, 'y': 420},
        ]
        result, state, summary = self.interaction_tools.invoke(
            'desktop_icons_arrange',
            {'moves': moves, 'reason': 'Align on right'}
        )
        self.assertEqual(state, '成功')
        self.assertIn('300', str(result))
        self.assertIn('snap-', str(result))

        # 2. Check updated coordinates
        updated = {i.icon_id: i for i in self.backend.get_icons()}
        self.assertEqual((updated['icon_calc_shortcut'].x, updated['icon_calc_shortcut'].y), (300, 300))
        self.assertEqual((updated['icon_paint_shortcut'].x, updated['icon_paint_shortcut'].y), (300, 420))

        # 3. Restore
        res_restore, state_restore, summary_restore = self.interaction_tools.invoke(
            'desktop_icons_restore', {}
        )
        self.assertEqual(state_restore, '成功')
        self.assertIn('已恢复 2 个桌面图标', res_restore.activity_detail)

        restored = {i.icon_id: i for i in self.backend.get_icons()}
        self.assertEqual((restored['icon_calc_shortcut'].x, restored['icon_calc_shortcut'].y), (10, 10))
        self.assertEqual((restored['icon_paint_shortcut'].x, restored['icon_paint_shortcut'].y), (10, 110))

    def test_invoke_desktop_icons_arrange_validation_failure(self):
        # Out of bounds move
        bad_moves = [{'icon_id': 'icon_calc_shortcut', 'x': -500, 'y': 100}]
        result, state, _ = self.interaction_tools.invoke(
            'desktop_icons_arrange', {'moves': bad_moves}
        )
        self.assertEqual(state, '出错')
        self.assertIn('超出可见显示器区域', str(result))

    def test_web_api_manager_lookup(self):
        hub = SimpleNamespace(
            chat=SimpleNamespace(
                tools=SimpleNamespace(
                    windows_interaction_tools=self.interaction_tools
                )
            ),
            store_root=self.temp_dir.name,
            name='peter',
        )
        mgr = _get_desktop_icons_manager(hub)
        self.assertIsNotNone(mgr)
        self.assertEqual(mgr, self.interaction_tools.desktop_icons_manager)


if __name__ == '__main__':
    unittest.main()
