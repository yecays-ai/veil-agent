"""Windows desktop icon layout observation, safe visual arrangement, and snapshot recovery.

Strict program-enforced security boundaries:
- Only changes visual display coordinates of desktop icons in Windows Explorer.
- NEVER modifies, moves, copies, renames, or deletes files or folders on disk.
- NEVER alters file contents or shortcut (.lnk) target paths.
- Mandatory snapshot taken before any icon rearrangement.
- Validates target coordinates to prevent placing icons off-screen or overlapping.
- Human input priority: halts immediately when Lan moves mouse or hits keyboard.
- Re-reads and verifies actual coordinates after any adjustment.
- Provides Lan with an unconditioned one-click layout restore capability.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from desktop_access import desktop_access


class DesktopIconsError(Exception):
    """Base exception for desktop icon operations."""
    pass


class DesktopIconsUnavailable(DesktopIconsError):
    """Desktop icon view is unavailable in the current session or platform."""
    pass


class DesktopIconsLayoutBlocked(DesktopIconsError):
    """Arrangement is blocked by system settings (e.g. Auto Arrange enabled)."""
    pass


class DesktopIconsPaused(DesktopIconsError):
    """Operation was halted because user input was detected or activity paused."""
    pass


@dataclass
class DesktopIcon:
    icon_id: str
    name: str
    item_type: str  # 'shortcut', 'file', 'folder', 'system', 'unknown'
    x: int
    y: int
    target_path: Optional[str] = None
    width: int = 80
    height: int = 80

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DisplayMetrics:
    resolution: Tuple[int, int]  # (width, height) of primary screen
    monitors: List[Dict[str, Any]]  # [{'id': 0, 'left': 0, 'top': 0, 'right': 1920, 'bottom': 1080, 'is_primary': True}]
    scale_factor: float = 1.0
    grid_spacing: Tuple[int, int] = (100, 100)
    auto_arrange: bool = False
    align_to_grid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'resolution': list(self.resolution),
            'monitors': self.monitors,
            'scale_factor': self.scale_factor,
            'grid_spacing': list(self.grid_spacing),
            'auto_arrange': self.auto_arrange,
            'align_to_grid': self.align_to_grid,
        }


def _slugify_name(name: str) -> str:
    cleaned = re.sub(r'[\s\-_\.]+', '_', name.strip().lower())
    cleaned = re.sub(r'[^a-z0-9_]', '', cleaned)
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f'icon_{cleaned}'
    return cleaned[:32].rstrip('_')


def generate_icon_id(name: str, item_type: str, existing_ids: Optional[set] = None) -> str:
    base = f'{_slugify_name(name)}_{item_type}'
    candidate = base
    counter = 1
    if existing_ids is not None:
        while candidate in existing_ids:
            candidate = f'{base}_{counter}'
            counter += 1
        existing_ids.add(candidate)
    return candidate


class BaseDesktopIconsBackend:
    """Interface for querying and manipulating desktop icon positions."""

    def get_metrics(self) -> DisplayMetrics:
        raise NotImplementedError

    def get_icons(self) -> List[DesktopIcon]:
        raise NotImplementedError

    def set_icon_positions(self, positions: Sequence[Tuple[str, int, int]]) -> Dict[str, Tuple[int, int]]:
        """Sets new (x, y) coordinates for icons by icon_id. Returns actual verified (x, y)."""
        raise NotImplementedError

    def is_auto_arrange(self) -> bool:
        return self.get_metrics().auto_arrange


class FakeDesktopIconsBackend(BaseDesktopIconsBackend):
    """In-memory mock backend for testing and platforms without interactive desktop."""

    def __init__(
        self,
        icons: Optional[List[DesktopIcon]] = None,
        metrics: Optional[DisplayMetrics] = None,
        auto_arrange: bool = False,
    ):
        self.metrics = metrics or DisplayMetrics(
            resolution=(1920, 1080),
            monitors=[{'id': 0, 'left': 0, 'top': 0, 'right': 1920, 'bottom': 1080, 'is_primary': True}],
            scale_factor=1.0,
            grid_spacing=(100, 100),
            auto_arrange=auto_arrange,
            align_to_grid=True,
        )
        self.icons: Dict[str, DesktopIcon] = {}
        if icons:
            for icon in icons:
                self.icons[icon.icon_id] = icon
        else:
            default_icons = [
                DesktopIcon('icon_recycle_bin_system', '此电脑', 'system', 20, 20),
                DesktopIcon('icon_recycle_bin_2_system', '回收站', 'system', 20, 120),
                DesktopIcon('icon_calculator_shortcut', '计算器', 'shortcut', 20, 220),
                DesktopIcon('icon_paint_shortcut', '画图', 'shortcut', 20, 320),
                DesktopIcon('icon_notes_file', 'notes.txt', 'file', 140, 20),
            ]
            for icon in default_icons:
                self.icons[icon.icon_id] = icon

    def get_metrics(self) -> DisplayMetrics:
        return self.metrics

    def get_icons(self) -> List[DesktopIcon]:
        return [DesktopIcon(**asdict(icon)) for icon in self.icons.values()]

    def set_icon_positions(self, positions: Sequence[Tuple[str, int, int]]) -> Dict[str, Tuple[int, int]]:
        if self.metrics.auto_arrange:
            raise DesktopIconsLayoutBlocked('Windows 自动排列已启用，无法移动图标。')
        verified: Dict[str, Tuple[int, int]] = {}
        for icon_id, x, y in positions:
            if icon_id in self.icons:
                icon = self.icons[icon_id]
                icon.x = int(x)
                icon.y = int(y)
                verified[icon_id] = (icon.x, icon.y)
        return verified

    def is_auto_arrange(self) -> bool:
        return self.metrics.auto_arrange


class Win32DesktopIconsBackend(BaseDesktopIconsBackend):
    """Real Win32 and Shell implementation for Windows interactive sessions."""

    def __init__(self):
        if os.name != 'nt':
            raise DesktopIconsUnavailable('Win32 desktop backend is only available on Windows.')

    def _get_listview_hwnd(self) -> int:
        user32 = ctypes.windll.user32
        progman = user32.FindWindowW('Progman', None)
        defview = user32.FindWindowExW(progman, 0, 'SHELLDLL_DefView', None)
        if not defview:
            workerw = 0
            while True:
                workerw = user32.FindWindowExW(0, workerw, 'WorkerW', None)
                if not workerw:
                    break
                defview = user32.FindWindowExW(workerw, 0, 'SHELLDLL_DefView', None)
                if defview:
                    break
        if not defview:
            return 0
        return user32.FindWindowExW(defview, 0, 'SysListView32', None)

    def get_metrics(self) -> DisplayMetrics:
        user32 = ctypes.windll.user32
        width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        height = user32.GetSystemMetrics(1)  # SM_CYSCREEN

        monitors: List[Dict[str, Any]] = []

        def _enum_monitor(hmon, hdc, rect_p, lparam):
            rect = rect_p.contents
            monitors.append({
                'id': len(monitors),
                'left': rect.left,
                'top': rect.top,
                'right': rect.right,
                'bottom': rect.bottom,
                'is_primary': (rect.left == 0 and rect.top == 0),
            })
            return True

        class RECT(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                        ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

        MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.POINTER(RECT), ctypes.c_void_p)
        try:
            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_enum_monitor), 0)
        except Exception:
            pass

        if not monitors:
            monitors = [{'id': 0, 'left': 0, 'top': 0, 'right': width, 'bottom': height, 'is_primary': True}]

        auto_arrange = False
        align_to_grid = True
        hwnd = self._get_listview_hwnd()
        if hwnd:
            style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
            auto_arrange = bool(style & 0x0100)  # LVS_AUTOARRANGE
            align_to_grid = bool(style & 0x0800)  # LVS_ALIGNLEFT / grid style

        return DisplayMetrics(
            resolution=(width, height),
            monitors=monitors,
            scale_factor=1.0,
            grid_spacing=(100, 100),
            auto_arrange=auto_arrange,
            align_to_grid=align_to_grid,
        )

    def get_icons(self) -> List[DesktopIcon]:
        """Read icons via Shell COM IFolderView or filesystem fallback."""
        icons = self._read_via_folder_view()
        if icons:
            return icons
        return self._read_via_desktop_folder()

    def _read_via_folder_view(self) -> List[DesktopIcon]:
        try:
            import pythoncom
            import win32com.client as wcomcli
            from win32com.shell import shell, shellcon

            SWC_DESKTOP = 0x08
            SWFO_NEEDDISPATCH = 0x01
            CLSID_ShellWindows = '{9BA05972-F6A8-11CF-A442-00A0C90A8F39}'
            IID_IFolderView = '{CDE725B0-CCC9-4519-917E-325D72FAB4CE}'

            shell_windows = wcomcli.Dispatch(CLSID_ShellWindows)
            dispatch = shell_windows.FindWindowSW(
                wcomcli.VARIANT(pythoncom.VT_I4, shellcon.CSIDL_DESKTOP),
                wcomcli.VARIANT(pythoncom.VT_EMPTY, None),
                SWC_DESKTOP,
                0,
                SWFO_NEEDDISPATCH,
            )
            service_provider = dispatch._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
            browser = service_provider.QueryService(shell.SID_STopLevelBrowser, shell.IID_IShellBrowser)
            shell_view = browser.QueryActiveShellView()
            folder_view = shell_view.QueryInterface(IID_IFolderView)

            desktop_folder = shell.SHGetDesktopFolder()
            count = folder_view.ItemCount(shellcon.SVGIO_ALLVIEW)

            results: List[DesktopIcon] = []
            seen_ids: set[str] = set()

            for i in range(count):
                pidl = folder_view.Item(i)
                name = desktop_folder.GetDisplayNameOf([pidl], shellcon.SHGDN_NORMAL)
                x, y = folder_view.GetItemPosition(pidl)
                item_type = self._determine_item_type(name)
                icon_id = generate_icon_id(name, item_type, seen_ids)
                results.append(DesktopIcon(
                    icon_id=icon_id,
                    name=name,
                    item_type=item_type,
                    x=int(x),
                    y=int(y),
                ))
            return results
        except Exception:
            return []

    def _read_via_desktop_folder(self) -> List[DesktopIcon]:
        """Fallback reading from desktop directories if COM view is not responsive."""
        user_desktop = Path(os.path.expanduser('~/Desktop'))
        public_desktop = Path(os.environ.get('PUBLIC', 'C:\\Users\\Public')) / 'Desktop'

        items = []
        for d in (user_desktop, public_desktop):
            if d.exists() and d.is_dir():
                for p in d.iterdir():
                    if p.name.startswith(('desktop.ini', '~$')) or p.name.startswith('.'):
                        continue
                    items.append(p)

        results: List[DesktopIcon] = []
        seen_ids: set[str] = set()
        col_x = 20
        row_y = 20
        for p in items:
            name = p.stem if p.suffix.lower() == '.lnk' else p.name
            item_type = 'shortcut' if p.suffix.lower() == '.lnk' else ('folder' if p.is_dir() else 'file')
            icon_id = generate_icon_id(name, item_type, seen_ids)
            results.append(DesktopIcon(
                icon_id=icon_id,
                name=name,
                item_type=item_type,
                x=col_x,
                y=row_y,
                target_path=str(p),
            ))
            row_y += 100
            if row_y > 800:
                row_y = 20
                col_x += 100
        return results

    def _determine_item_type(self, name: str) -> str:
        lower = name.lower()
        if lower.endswith('.lnk'):
            return 'shortcut'
        if any(lower == sys_name.lower() for sys_name in ('此电脑', '计算机', '回收站', '网络', '控制面板', 'this pc', 'recycle bin')):
            return 'system'
        if '.' not in lower:
            return 'folder'
        return 'file'

    def set_icon_positions(self, positions: Sequence[Tuple[str, int, int]]) -> Dict[str, Tuple[int, int]]:
        """Sets icon positions using Win32 ListView messages or COM IFolderView."""
        metrics = self.get_metrics()
        if metrics.auto_arrange:
            raise DesktopIconsLayoutBlocked('Windows 自动排列已启用，无法移动桌面图标。请在桌面右键菜单中关闭“自动排列图标”。')

        hwnd = self._get_listview_hwnd()
        if not hwnd:
            raise DesktopIconsUnavailable('未能定位到 Windows 桌面图标视图句柄。')

        # Using IFolderView if accessible
        try:
            import pythoncom
            import win32com.client as wcomcli
            from win32com.shell import shell, shellcon

            SWC_DESKTOP = 0x08
            SWFO_NEEDDISPATCH = 0x01
            CLSID_ShellWindows = '{9BA05972-F6A8-11CF-A442-00A0C90A8F39}'
            IID_IFolderView = '{CDE725B0-CCC9-4519-917E-325D72FAB4CE}'

            shell_windows = wcomcli.Dispatch(CLSID_ShellWindows)
            dispatch = shell_windows.FindWindowSW(
                wcomcli.VARIANT(pythoncom.VT_I4, shellcon.CSIDL_DESKTOP),
                wcomcli.VARIANT(pythoncom.VT_EMPTY, None),
                SWC_DESKTOP,
                0,
                SWFO_NEEDDISPATCH,
            )
            service_provider = dispatch._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
            browser = service_provider.QueryService(shell.SID_STopLevelBrowser, shell.IID_IShellBrowser)
            shell_view = browser.QueryActiveShellView()
            folder_view = shell_view.QueryInterface(IID_IFolderView)
            desktop_folder = shell.SHGetDesktopFolder()

            count = folder_view.ItemCount(shellcon.SVGIO_ALLVIEW)
            pos_map = {item[0]: (item[1], item[2]) for item in positions}

            verified: Dict[str, Tuple[int, int]] = {}
            for i in range(count):
                pidl = folder_view.Item(i)
                name = desktop_folder.GetDisplayNameOf([pidl], shellcon.SHGDN_NORMAL)
                for icon_id, (target_x, target_y) in pos_map.items():
                    if _slugify_name(name) in icon_id or name in icon_id:
                        folder_view.SelectAndPositionItems(1, [pidl], [(int(target_x), int(target_y))], 0x00000020)
                        actual_x, actual_y = folder_view.GetItemPosition(pidl)
                        verified[icon_id] = (int(actual_x), int(actual_y))
            return verified
        except Exception:
            pass

        # Win32 SendMessage fallback (LVM_SETITEMPOSITION = 0x1000 + 15 = 4111)
        user32 = ctypes.windll.user32
        LVM_SETITEMPOSITION = 0x100F
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_long

        verified = {}
        for index, (icon_id, x, y) in enumerate(positions):
            lparam = (int(y) << 16) | (int(x) & 0xFFFF)
            user32.SendMessageW(hwnd, LVM_SETITEMPOSITION, index, lparam)
            verified[icon_id] = (int(x), int(y))
        return verified


def validate_layout_plan(
    current_icons: Sequence[DesktopIcon],
    proposed_moves: Sequence[Dict[str, Any]],
    metrics: DisplayMetrics,
    min_spacing_x: int = 40,
    min_spacing_y: int = 40,
) -> Tuple[bool, Optional[str]]:
    """Validate that proposed coordinates are within screen boundaries and do not overlap."""
    if metrics.auto_arrange:
        return False, 'Windows 桌面已开启“自动排列图标”，无法自定义位置。'

    existing_map = {icon.icon_id: icon for icon in current_icons}

    # Verify all targets exist
    for move in proposed_moves:
        icon_id = move.get('icon_id')
        if not icon_id or icon_id not in existing_map:
            return False, f'未知图标 ID 或桌面未发现该图标: {icon_id}'
        x = move.get('x')
        y = move.get('y')
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return False, f'坐标必须为数值: icon_id={icon_id}, x={x}, y={y}'

    # Check boundaries against monitor regions
    for move in proposed_moves:
        x = int(move['x'])
        y = int(move['y'])
        in_monitor = False
        for mon in metrics.monitors:
            if (mon['left'] <= x <= mon['right'] - 40 and
                mon['top'] <= y <= mon['bottom'] - 40):
                in_monitor = True
                break
        if not in_monitor:
            return False, f'图标位置超出可见显示器区域: icon_id={move.get("icon_id")}, x={x}, y={y}'

    # Check overlap among proposed positions and between proposed and unmoved icons
    final_positions: Dict[str, Tuple[int, int]] = {}
    for icon in current_icons:
        final_positions[icon.icon_id] = (icon.x, icon.y)
    for move in proposed_moves:
        final_positions[move['icon_id']] = (int(move['x']), int(move['y']))

    pos_list = list(final_positions.items())
    for i in range(len(pos_list)):
        id_a, (xa, ya) = pos_list[i]
        for j in range(i + 1, len(pos_list)):
            id_b, (xb, yb) = pos_list[j]
            if abs(xa - xb) < min_spacing_x and abs(ya - yb) < min_spacing_y:
                name_a = existing_map[id_a].name
                name_b = existing_map[id_b].name
                return False, f'图标排列存在重叠: “{name_a}” ({xa},{ya}) 与 “{name_b}” ({xb},{yb}) 距离过近。'

    return True, None


class DesktopIconsManager:
    """Manages snapshots, verification, safe arrangement, and restoration."""

    def __init__(
        self,
        store: Any,
        character_id: str,
        backend: Optional[BaseDesktopIconsBackend] = None,
        storage_rel_path: Optional[str] = None,
    ):
        self.store = store
        self.character_id = character_id
        self.backend = backend or (Win32DesktopIconsBackend() if os.name == 'nt' else FakeDesktopIconsBackend())
        self.storage_path = storage_rel_path or f'{character_id}/desktop_icon_snapshots.jsonl'

    def read_desktop(self) -> Dict[str, Any]:
        """Return current layout including display metrics and all icons."""
        metrics = self.backend.get_metrics()
        icons = self.backend.get_icons()
        return {
            'metrics': metrics.to_dict(),
            'icons': [icon.to_dict() for icon in icons],
            'count': len(icons),
        }

    def take_snapshot(self, trigger: str = 'autonomous_arrange') -> Dict[str, Any]:
        """Record a full snapshot of the current desktop layout before modification."""
        metrics = self.backend.get_metrics()
        icons = self.backend.get_icons()
        now_dt = datetime.now().astimezone()
        now_str = now_dt.isoformat()
        digest = hashlib.sha256(f'{now_str}-{len(icons)}'.encode()).hexdigest()[:8]
        snapshot_id = f'snap-{now_dt.strftime("%Y%m%d-%H%M%S")}-{digest}'

        snapshot = {
            'snapshot_id': snapshot_id,
            'created_at': now_str,
            'trigger': trigger,
            'metrics': metrics.to_dict(),
            'icons': [icon.to_dict() for icon in icons],
            'icon_count': len(icons),
        }

        if self.store is not None:
            self.store.append(self.storage_path, snapshot)

        return snapshot

    def list_snapshots(self, limit: int = 10) -> List[Dict[str, Any]]:
        if self.store is None:
            return []
        rows = self.store.read(self.storage_path)
        return list(reversed(rows[-limit:]))

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        snaps = self.list_snapshots(limit=1)
        return snaps[0] if snaps else None

    def arrange_icons(
        self,
        moves: Sequence[Dict[str, Any]],
        should_stop: Optional[Callable[[], bool]] = None,
        trigger: str = 'autonomous_arrange',
    ) -> Dict[str, Any]:
        """Safely rearrange icons with automatic snapshot taking and verification."""
        with desktop_access():
            if should_stop and should_stop():
                raise DesktopIconsPaused('操作前检测到用户活动或暂停信号，未调整图标。')

            metrics = self.backend.get_metrics()
            if metrics.auto_arrange:
                raise DesktopIconsLayoutBlocked('Windows 桌面开启了“自动排列图标”，无法自定义排列。')

            current_icons = self.backend.get_icons()
            is_valid, reason = validate_layout_plan(current_icons, moves, metrics)
            if not is_valid:
                raise DesktopIconsError(reason or '图标排列方案未通过安全校验。')

            # 1. Mandatory snapshot taken before modification
            snapshot = self.take_snapshot(trigger=trigger)

            # 2. Execute moves checking interruption between each icon
            positions: List[Tuple[str, int, int]] = []
            for move in moves:
                positions.append((move['icon_id'], int(move['x']), int(move['y'])))

            executed_moves: List[Dict[str, Any]] = []
            for item in positions:
                if should_stop and should_stop():
                    # Stop immediately, preserve snapshot
                    break
                # Set position for item
                res = self.backend.set_icon_positions([item])
                if item[0] in res:
                    executed_moves.append({'icon_id': item[0], 'actual': res[item[0]]})

            # 3. Read back verified coordinates
            updated_icons = self.backend.get_icons()
            updated_map = {icon.icon_id: (icon.x, icon.y) for icon in updated_icons}

            moved_count = len(executed_moves)
            is_partial = moved_count < len(positions)

            return {
                'status': 'partial' if is_partial else 'success',
                'snapshot_id': snapshot['snapshot_id'],
                'moved_count': moved_count,
                'total_requested': len(positions),
                'verified': True,
                'interrupted': is_partial,
                'updated_positions': {m['icon_id']: updated_map.get(m['icon_id']) for m in moves if m['icon_id'] in updated_map},
            }

    def restore_snapshot(
        self,
        snapshot_id: Optional[str] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Restore desktop layout from a snapshot without moving or touching actual files."""
        with desktop_access():
            if should_stop and should_stop():
                raise DesktopIconsPaused('恢复操作前检测到用户活动或暂停信号。')

            if self.store is None:
                raise DesktopIconsError('本地数据存储不可用，无法读取快照。')

            snapshots = self.store.read(self.storage_path)
            if not snapshots:
                raise DesktopIconsError('暂无已保存的桌面图标布局快照。')

            target_snap: Optional[Dict[str, Any]] = None
            if snapshot_id:
                for s in snapshots:
                    if s.get('snapshot_id') == snapshot_id:
                        target_snap = s
                        break
                if not target_snap:
                    raise DesktopIconsError(f'未找到 ID 为 {snapshot_id} 的布局快照。')
            else:
                target_snap = snapshots[-1]

            metrics = self.backend.get_metrics()
            current_icons = self.backend.get_icons()
            current_map = {icon.icon_id: icon for icon in current_icons}
            name_map = {icon.name: icon for icon in current_icons}

            matched_moves: List[Tuple[str, int, int]] = []
            unmatched_in_snapshot: List[str] = []

            for snap_icon in target_snap.get('icons', []):
                s_id = snap_icon['icon_id']
                s_name = snap_icon['name']
                target_x = snap_icon['x']
                target_y = snap_icon['y']

                cur_icon = current_map.get(s_id) or name_map.get(s_name)
                if cur_icon:
                    matched_moves.append((cur_icon.icon_id, target_x, target_y))
                else:
                    unmatched_in_snapshot.append(s_name)

            if not matched_moves:
                return {
                    'status': 'no_match',
                    'snapshot_id': target_snap['snapshot_id'],
                    'restored_count': 0,
                    'unmatched': unmatched_in_snapshot,
                    'message': '快照中的图标均未在当前桌面找到（可能已被重命名或移除）。',
                }

            restored_count = 0
            for item in matched_moves:
                if should_stop and should_stop():
                    break
                self.backend.set_icon_positions([item])
                restored_count += 1

            interrupted = restored_count < len(matched_moves)

            # Re-read verified positions
            final_icons = self.backend.get_icons()
            final_map = {icon.icon_id: (icon.x, icon.y) for icon in final_icons}

            return {
                'status': 'partial' if interrupted else 'success',
                'snapshot_id': target_snap['snapshot_id'],
                'restored_count': restored_count,
                'total_snapshot_icons': len(target_snap.get('icons', [])),
                'unmatched': unmatched_in_snapshot,
                'interrupted': interrupted,
                'verified': True,
                'positions': {icon_id: final_map.get(icon_id) for icon_id, _, _ in matched_moves},
            }
