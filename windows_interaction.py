"""Whitelisted, bounded Windows desktop interaction through UI Automation."""
import ctypes
import fnmatch
import hashlib
import json
import ntpath
import os
import queue
import re
import shutil
import shlex
import subprocess
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

from agent_types import NOT_ASKED, NOT_ASKED_TEXT
from desktop_access import DesktopBusy, desktop_access, program_input_made, register_input_tracker
from desktop_icons import (
    DesktopIconsError,
    DesktopIconsLayoutBlocked,
    DesktopIconsPaused,
    DesktopIconsUnavailable,
)
from windows_discovery import (
    WindowsAppDiscovery,
    is_ambiguous_confirmation,
    is_file_dialog_control,
    is_unsafe_text_input,
)
from windows_observer import (
    MAX_CONTROLS,
    MAX_RESULT_BYTES,
    MAX_TEXT_CHARS,
    SENSITIVE_LABEL,
    WINDOWS_OBSERVATION_TOOL_NAMES,
    ObservationError,
    ObservationPaused,
    ProtectedWindow,
    WindowsDesktopBackend,
    WindowsToolResult,
    _control_identity,
    _control_patterns,
    _text,
)


WINDOWS_INTERACTION_TOOL_NAMES = frozenset({
    'windows_discover_apps', 'windows_launch', 'windows_manage', 'windows_control_action',
    'desktop_icons_read', 'desktop_icons_arrange', 'desktop_icons_restore',
})
WINDOWS_DESKTOP_TOOL_NAMES = WINDOWS_OBSERVATION_TOOL_NAMES | WINDOWS_INTERACTION_TOOL_NAMES

APP_WINDOW_WAIT_SECONDS = 7
PUBLIC_WEB_WINDOW_WAIT_SECONDS = 25

WINDOWS_INTERACTION_TOOL_SCHEMAS = [
    SimpleNamespace(
        name='windows_discover_apps',
        description=(
            'Discover installed Windows desktop applications from the Start Menu, App Paths and WindowsApps. '
            'Returns a list of applications with app_id, name, can_launch, can_observe, can_interact. '
            'Optional query filters applications by name or app_id. Read-only.'
        ),
        inputSchema={'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Optional search term to filter applications by name or app_id.'},
        }, 'additionalProperties': False},
    ),
    SimpleNamespace(
        name='windows_launch',
        description=(
            'Open one allowlisted app by app_id, or reuse its open window. An app configured for public_web must '
            'receive a public HTTP(S) URL; the browser opens it in a Guest window. If several windows are open, '
            'the result includes their window_ids.'
        ),
        inputSchema={'type': 'object', 'properties': {
            'app_id': {'type': 'string', 'description': 'Exact id from the Windows application allowlist.'},
            'url': {'type': 'string', 'description': 'Optional public HTTP(S) page URL; only apps explicitly configured for public_web can use it.'},
        }, 'required': ['app_id'], 'additionalProperties': False},
    ),
    SimpleNamespace(
        name='windows_manage',
        description=(
            'Activate, minimize or restore one allowlisted app window by window_id.'
        ),
        inputSchema={'type': 'object', 'properties': {
            'app_id': {'type': 'string'},
            'window_id': {'type': 'string', 'description': 'Exact current window_id.'},
            'action': {'type': 'string', 'enum': ['activate', 'minimize', 'restore']},
        }, 'required': ['app_id', 'window_id', 'action'], 'additionalProperties': False},
    ),
    SimpleNamespace(
        name='windows_control_action',
        description=(
            'Do one action (invoke, select, toggle, expand, collapse, set_text) on a control_id from a fresh '
            'windows_observe of the same window, only when allowed for that app. Clicks, navigation, selection and typing run directly. Saving, '
            'sending, submitting, publishing or overwriting asks Lan first (in VEIL or on QQ); delete, install, '
            'purchase and administrator actions are refused. It stops if Lan is using the computer. Observe '
            'again before the next action.'
        ),
        inputSchema={'type': 'object', 'properties': {
            'app_id': {'type': 'string'},
            'window_id': {'type': 'string', 'description': 'Exact current window_id.'},
            'control_id': {'type': 'string', 'description': 'Exact control_id from a fresh windows_observe result.'},
            'action': {'type': 'string', 'enum': [
                'invoke', 'select', 'toggle', 'expand', 'collapse', 'set_text',
            ]},
            'text': {'type': 'string', 'description': 'Required only for set_text; replaces the complete value.'},
        }, 'required': ['app_id', 'window_id', 'control_id', 'action'],
            'additionalProperties': False},
    ),
    SimpleNamespace(
        name='desktop_icons_read',
        description=(
            'Read the current Windows desktop icons layout: names, icon_ids, item_types '
            '(shortcut, file, folder, system), coordinates (x, y), screen resolution, monitors, '
            'and auto-arrange status. Read-only. Does not move, rename or modify any files.'
        ),
        inputSchema={'type': 'object', 'properties': {}, 'additionalProperties': False},
    ),
    SimpleNamespace(
        name='desktop_icons_arrange',
        description=(
            'Safely adjust visual display coordinates of desktop icons. Takes a list of moves '
            '[{"icon_id": "...", "x": 100, "y": 100}]. Automatically takes a layout snapshot '
            'before moving. Validates coordinates to prevent off-screen placement and overlaps. '
            'NEVER modifies, moves, copies, renames, or deletes files or folders on disk. '
            'Stops immediately if Lan moves mouse or hits keyboard. Returns verified coordinates.'
        ),
        inputSchema={
            'type': 'object',
            'properties': {
                'moves': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'icon_id': {'type': 'string', 'description': 'Exact icon_id from desktop_icons_read.'},
                            'x': {'type': 'integer', 'description': 'Target X pixel coordinate on visible screen.'},
                            'y': {'type': 'integer', 'description': 'Target Y pixel coordinate on visible screen.'},
                        },
                        'required': ['icon_id', 'x', 'y'],
                        'additionalProperties': False,
                    },
                    'description': 'List of icon moves with target display coordinates.',
                },
                'reason': {'type': 'string', 'description': 'Optional short note on why this layout was chosen.'},
            },
            'required': ['moves'],
            'additionalProperties': False,
        },
    ),
    SimpleNamespace(
        name='desktop_icons_restore',
        description=(
            'Restore desktop icon positions from a previously saved snapshot. By default restores the '
            'latest snapshot taken before the last arrangement. Does not touch or change actual files on disk.'
        ),
        inputSchema={
            'type': 'object',
            'properties': {
                'snapshot_id': {'type': 'string', 'description': 'Optional snapshot_id. If omitted, restores the latest layout snapshot.'},
            },
            'additionalProperties': False,
        },
    ),
]

_APP_ID = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
_WINDOW_ID = re.compile(r'^w-[0-9a-f]{24}$')
_CONTROL_ID = re.compile(r'^c-[0-9a-f]{24}$')
_ALLOWED_ACTIONS = frozenset({
    'activate', 'minimize', 'restore', 'invoke', 'select', 'toggle',
    'expand', 'collapse', 'set_text',
})
_BLOCKED_CONTROL = re.compile(
    r'(?<![a-z0-9])(?:delete|remove|uninstall|install|purchase|buy|checkout|'
    r'administrator|run as admin|elevate|uac|删除|卸载|安装|购买|付款|管理员|权限提升)(?![a-z0-9])',
    re.IGNORECASE,
)
# Ticket 114: ordinary clicks, navigation and text edits run without asking. Only an action on a
# control that saves, sends, submits, publishes or overwrites asks Lan first (in VEIL or on QQ).
_RISKY_CONTROL = re.compile(
    r'(?<![a-z0-9])(?:save|send|submit|post|publish|overwrite|'
    r'保存|另存为|发送|提交|发布|发表|覆盖)(?![a-z0-9])',
    re.IGNORECASE,
)
# A plain "Yes"/"OK" inside a file-overwrite confirmation is identified by the dialog around it.
_RISKY_SCOPE = re.compile(
    r'(?<![a-z0-9])(?:confirm save as|already exists|overwrite|replace it|'
    r'确认另存为|已存在|覆盖)(?![a-z0-9])',
    re.IGNORECASE,
)
_RISKY_ACTIONS = frozenset({'invoke', 'toggle', 'select'})
_IDLE_TICK_MARGIN_MS = 35
MAX_KEYBOARD_FALLBACK_CHARS = 200
# After Lan approves in VEIL, wait up to this long for 1 second without input.
APPROVAL_IDLE_SECONDS = 1
APPROVAL_IDLE_WAIT_SECONDS = 4


class InteractionError(RuntimeError):
    pass


class InteractionPaused(InteractionError):
    pass


class ApprovalInputBusy(InteractionPaused):
    pass


class HumanTookOver(InteractionError):
    pass


class InteractionUncertain(InteractionError):
    pass


def _expand_windows_path(value, env=None, *, allow_wildcards=False):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('Windows application paths must be non-empty strings.')
    values = os.environ if env is None else env

    def replace(match):
        key = match.group(1)
        if key.casefold() not in {'systemroot', 'programfiles', 'programfiles(x86)', 'localappdata'}:
            raise ValueError('Only standard Windows installation path variables are allowed.')
        resolved = next((v for k, v in values.items() if k.casefold() == key.casefold()), None)
        if not resolved:
            raise ValueError(f'Windows path variable %{key}% is unavailable.')
        return resolved

    expanded = re.sub(r'%([^%]+)%', replace, value.strip())
    if '%' in expanded or not ntpath.isabs(expanded):
        raise ValueError('Allowlisted application paths must resolve to absolute Windows paths.')
    parts = ntpath.splitdrive(expanded)[1].replace('/', '\\').split('\\')
    if '..' in parts:
        raise ValueError('Parent-directory segments are not allowed in application paths.')
    normalized = ntpath.normpath(expanded)
    if not allow_wildcards and any(char in normalized for char in '*?[]'):
        raise ValueError('Launch paths must identify one executable, not a wildcard.')
    return ntpath.normcase(normalized)


def validate_applications(applications):
    if not isinstance(applications, dict) or not applications:
        raise ValueError('At least one explicitly allowlisted Windows application is required.')
    result = {}
    for app_id, item in applications.items():
        if not isinstance(app_id, str) or not _APP_ID.fullmatch(app_id) or not isinstance(item, dict):
            raise ValueError('Windows application entries need a safe lowercase app id and a table.')
        label, launch_path = item.get('label'), item.get('launch_path')
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise ValueError(f'{app_id} needs a short display label.')
        launch = _expand_windows_path(launch_path)
        if ntpath.splitext(launch)[1].casefold() != '.exe':
            raise ValueError(f'{app_id} launch_path must point to one .exe file.')
        process_paths = item.get('process_paths')
        if (not isinstance(process_paths, list) or not process_paths
                or any(not isinstance(path, str) for path in process_paths)):
            raise ValueError(f'{app_id} needs one or more process_paths identity patterns.')
        identities = [_expand_windows_path(path, allow_wildcards=True) for path in process_paths]
        actions = item.get('actions')
        if (not isinstance(actions, list)
                or any(not isinstance(action, str) or action not in _ALLOWED_ACTIONS for action in actions)):
            raise ValueError(f'{app_id} needs an explicit list of supported interaction actions.')
        result[app_id] = {
            'app_id': app_id,
            'label': label.strip(),
            'launch_path': launch,
            'process_paths': tuple(identities),
            'actions': frozenset(actions),
            'public_web': item.get('public_web', False),
        }
        if type(result[app_id]['public_web']) is not bool:
            raise ValueError(f'{app_id} public_web must be boolean.')
    return result


def load_application_allowlist(root, character_id, settings):
    if not isinstance(settings, dict) or settings.get('enabled') is not True:
        return {}
    filename = settings.get('apps_file')
    if (not isinstance(filename, str) or not filename.endswith('.toml')
            or Path(filename).name != filename or filename in ('', '.', '..')):
        raise ValueError('windows_interaction.apps_file must name a local TOML file.')
    character_root = (Path(root) / 'characters' / character_id).resolve()
    config_path = (character_root / filename).resolve()
    if not config_path.is_relative_to(character_root):
        raise ValueError('The Windows application allowlist must remain inside this character folder.')
    with config_path.open('rb') as stream:
        config = tomllib.load(stream)
    return validate_applications(config.get('applications'))


def _tick_delta(later, earlier):
    return (int(later) - int(earlier)) & 0xFFFFFFFF


def _tick_is_after(candidate, reference):
    delta = _tick_delta(candidate, reference)
    return 0 < delta < 0x80000000


def _needs_approval(action, metadata):
    if action not in _RISKY_ACTIONS:
        return False
    own = ' '.join((metadata.get('name') or '', metadata.get('automation_id') or ''))
    return bool(_RISKY_CONTROL.search(own)
                or _RISKY_SCOPE.search(' '.join(metadata.get('ancestor_labels', ()))))


def _approval_control_label(metadata):
    value = f'{metadata.get("type", "control")} {metadata.get("name") or "(unnamed control)"}'
    value = ''.join(char if ord(char) >= 32 and ord(char) != 127 else ' ' for char in value)
    return re.sub(r'\s+', ' ', value).strip()[:200]


class DesktopInputMonitor:
    """Tracks recent human input while excluding input generated by this process."""

    def __init__(self, last_input_tick=None, now_tick=None):
        self._last_input_tick = last_input_tick or self._windows_last_input_tick
        self._now_tick = now_tick or self._windows_now_tick
        self._lock = threading.Lock()
        self._human_tick = self._last_input_tick() if os.name == 'nt' else 0
        self._own_tick = None
        if last_input_tick is None and now_tick is None and os.name == 'nt':
            register_input_tracker(self)

    @staticmethod
    def _windows_last_input_tick():
        if os.name != 'nt':
            raise InteractionError('Windows input state is unavailable on this platform.')
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            raise InteractionError('The recent-input state could not be checked safely.')
        return int(info.dwTime)

    @staticmethod
    def _windows_now_tick():
        if os.name != 'nt':
            raise InteractionError('Windows input state is unavailable on this platform.')
        return int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF

    def _refresh(self):
        current = int(self._last_input_tick()) & 0xFFFFFFFF
        with self._lock:
            if self._own_tick is None:
                if _tick_is_after(current, self._human_tick):
                    self._human_tick = current
            elif _tick_is_after(current, (self._own_tick + _IDLE_TICK_MARGIN_MS) & 0xFFFFFFFF):
                self._human_tick = current
            return self._human_tick

    def snapshot(self):
        return self._refresh()

    def changed_since(self, snapshot):
        return self._refresh() != snapshot

    def idle_seconds(self):
        human_tick = self._refresh()
        return _tick_delta(self._now_tick(), human_tick) / 1000

    def require_idle(self, minimum_seconds):
        if self.idle_seconds() < minimum_seconds:
            raise InteractionPaused('Recent human keyboard or mouse input has priority.')

    def note_program_input(self):
        with self._lock:
            self._own_tick = int(self._now_tick()) & 0xFFFFFFFF

    def mark_own_input(self):
        self.note_program_input()
        program_input_made(self)


class WindowsInteractionBackend(WindowsDesktopBackend):
    """UIA and Win32 actions, always scoped to the observer's current window identity."""

    @staticmethod
    def _require_unchanged_input(input_monitor, input_snapshot):
        if input_monitor.changed_since(input_snapshot):
            raise HumanTookOver('Human input arrived during target verification. No desktop action was sent.')

    def _find_control(self, selected, control_id, stopped):
        self._cancelled(stopped)
        current = self._revalidate(selected, stopped)
        auto = self._uia()
        root = auto.ControlFromHandle(current['handle'])
        if not root:
            raise ObservationError('The selected window no longer has a UI Automation root.')
        matches = []
        pending = [(root, 0, (), None, ())]
        count = 0
        while pending and count < MAX_CONTROLS:
            self._cancelled(stopped)
            control, depth, path, parent_type, ancestor_labels = pending.pop()
            count += 1
            try:
                kind = str(control.ControlTypeName or '')
                name = _text(control.Name, 240)
                automation_id = _text(control.AutomationId, 160)
                if ((name and SENSITIVE_LABEL.search(name))
                        or (automation_id and SENSITIVE_LABEL.search(automation_id))):
                    raise ProtectedWindow('A protected security or credential label was detected.')
                if kind in ('EditControl', 'DocumentControl'):
                    try:
                        if bool(control.IsPassword):
                            raise ProtectedWindow('A password field was detected.')
                    except ProtectedWindow:
                        raise
                    except Exception as exc:
                        raise ObservationError('The input field could not be checked safely.') from exc
                if _control_identity(current['id'], control, path) == control_id:
                    metadata = {
                        'control_id': control_id,
                        'type': kind,
                        'name': name,
                        'automation_id': automation_id,
                        'parent_type': parent_type,
                        'ancestor_labels': ancestor_labels,
                        'enabled': bool(control.IsEnabled),
                        'offscreen': bool(control.IsOffscreen),
                        'patterns': _control_patterns(control),
                    }
                    if kind in ('EditControl', 'DocumentControl'):
                        metadata['read_only'] = bool(control.IsReadOnly)
                    if kind in ('ListItemControl', 'TabItemControl', 'RadioButtonControl'):
                        metadata['selected'] = bool(control.IsSelected)
                    if 'toggle' in metadata['patterns']:
                        metadata['toggle_state'] = int(control.GetTogglePattern().ToggleState)
                    if 'expand-collapse' in metadata['patterns']:
                        metadata['expand_state'] = int(control.GetExpandCollapsePattern().ExpandCollapseState)
                    matches.append((control, metadata))
                if depth < 8:
                    try:
                        children = control.GetChildren()
                    except Exception:
                        children = []
                    labels = ancestor_labels + ((name,) if name else ())
                    for child_index in range(len(children) - 1, -1, -1):
                        if len(pending) + count < MAX_CONTROLS:
                            pending.append((children[child_index], depth + 1,
                                            path + (child_index,), kind, labels))
            except (ProtectedWindow, ObservationPaused, ObservationError):
                raise
            except Exception:
                continue
        if len(matches) != 1:
            raise ObservationError('That control_id is stale or ambiguous. Observe the current window again; no fallback target was used.')
        control, metadata = matches[0]
        if metadata['offscreen'] or not metadata['enabled']:
            raise ObservationError('The selected control is not currently visible and enabled.')
        return control, metadata

    def inspect_control(self, selected, control_id, stopped):
        current = self._revalidate(selected, stopped)
        self._check_default_desktop()
        auto = self._uia()
        with auto.UIAutomationInitializerInThread():
            old_timeout = self._set_search_timeout(auto, 2)
            try:
                _, metadata = self._find_control(current, control_id, stopped)
                return metadata
            finally:
                self._restore_search_timeout(auto, old_timeout)

    def _read_control_value(self, control, kind):
        return self._control_value(control, kind)

    def _run_pattern(self, control, selected, metadata, action, text, stopped,
                     input_monitor, input_snapshot):
        self._cancelled(stopped)
        kind = metadata['type']
        if action == 'invoke':
            pattern = control.GetInvokePattern()
            if not pattern:
                raise ObservationError('The selected control does not expose InvokePattern.')
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            if pattern.Invoke(waitTime=0) is False:
                raise InteractionUncertain('InvokePattern did not confirm the action; do not repeat it automatically.')
            return {'confirmed': None, 'verification': 'read_after_action'}
        if action == 'select':
            if kind not in ('TabItemControl', 'ListItemControl', 'RadioButtonControl'):
                raise ObservationError('Selection is limited to a tab, list item, or radio item.')
            pattern = control.GetSelectionItemPattern()
            if not pattern:
                raise ObservationError('The selected control does not expose SelectionItemPattern.')
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            if pattern.Select(waitTime=0) is False:
                raise InteractionUncertain('SelectionItemPattern did not confirm the selection.')
            return {'confirmed': None, 'verification': 'selected'}
        if action == 'toggle':
            pattern = control.GetTogglePattern()
            if not pattern:
                raise ObservationError('The selected control does not expose TogglePattern.')
            before = int(pattern.ToggleState)
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            if pattern.Toggle(waitTime=0) is False:
                raise InteractionUncertain('TogglePattern did not confirm the state change.')
            after = int(pattern.ToggleState)
            return {'confirmed': after != before, 'verification': 'toggle_state_changed'}
        if action in ('expand', 'collapse'):
            pattern = control.GetExpandCollapsePattern()
            if not pattern:
                raise ObservationError('The selected control does not expose ExpandCollapsePattern.')
            desired = 1 if action == 'expand' else 0
            state = int(pattern.ExpandCollapseState)
            if state == desired:
                return {'confirmed': True, 'verification': 'already_in_requested_state'}
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            result = pattern.Expand(waitTime=0) if action == 'expand' else pattern.Collapse(waitTime=0)
            if result is False or int(pattern.ExpandCollapseState) != desired:
                raise InteractionUncertain('The requested expand/collapse state was not confirmed.')
            return {'confirmed': True, 'verification': 'expand_collapse_state'}
        if action == 'set_text':
            if kind not in ('EditControl', 'DocumentControl') or metadata.get('read_only'):
                raise ObservationError('Text replacement is limited to writable UIA Edit or Document controls.')
            pattern = control.GetValuePattern()
            if pattern:
                if pattern.IsReadOnly:
                    raise ObservationError('The text control is read-only.')
                self._require_unchanged_input(input_monitor, input_snapshot)
                self._cancelled(stopped)
                if pattern.SetValue(text, waitTime=0) is False:
                    raise InteractionUncertain('ValuePattern did not confirm the text replacement; do not retry automatically.')
                readback = self._read_control_value(control, kind)
                return {'confirmed': readback == text, 'verification': 'value_readback'}
            # The last resort injects keys only into the already foreground, revalidated target.
            if '\n' in text or '\r' in text:
                raise ObservationError('Keyboard fallback is refused for multiline text; this control needs a settable ValuePattern.')
            if len(text) > MAX_KEYBOARD_FALLBACK_CHARS:
                raise ObservationError(
                    f'Keyboard fallback is limited to {MAX_KEYBOARD_FALLBACK_CHARS} characters; '
                    'this control needs a settable ValuePattern.')
            if int(self._foreground_window()) != int(metadata.get('window_handle', 0)):
                raise InteractionPaused('Keyboard fallback requires the verified target window to be in front.')
            self._revalidate(selected, stopped)
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            control.SetFocus()
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._revalidate(selected, stopped)
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            if int(self._foreground_window()) != int(metadata.get('window_handle', 0)):
                raise InteractionPaused('The target window lost the foreground before keyboard input.')
            input_monitor.mark_own_input()
            escaped = text.replace('{', '{{}').replace('}', '{}}')
            # interval=0 keeps typing far inside the tool timeout, so no keys can land in
            # another window after the call has returned; mark again so the typed keys are
            # not mistaken for Lan taking over.
            control.SendKeys('{Ctrl}a' + escaped, interval=0, waitTime=0)
            input_monitor.mark_own_input()
            readback = self._read_control_value(control, kind)
            return {'confirmed': readback == text, 'verification': 'keyboard_readback'}
        raise ObservationError('Unsupported UI Automation action.')

    @staticmethod
    def _foreground_window():
        if os.name != 'nt':
            return 0
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        return int(user32.GetForegroundWindow() or 0)

    def apply_control_action(self, selected, control_id, action, text, stopped,
                             input_monitor, input_snapshot):
        current = self._revalidate(selected, stopped)
        self._check_default_desktop()
        before_controls = self.read_controls(current, stopped) if action == 'invoke' else None
        before_snapshot = (json.dumps(list(before_controls), ensure_ascii=False,
                                      sort_keys=True, separators=(',', ':'))
                           if before_controls is not None else None)
        auto = self._uia()
        with auto.UIAutomationInitializerInThread():
            old_timeout = self._set_search_timeout(auto, 2)
            try:
                control, metadata = self._find_control(current, control_id, stopped)
                metadata['window_handle'] = current['handle']
                action_result = self._run_pattern(
                    control, current, metadata, action, text, stopped,
                    input_monitor, input_snapshot)
            finally:
                self._restore_search_timeout(auto, old_timeout)
        self._cancelled(stopped)
        current_after = self._revalidate(current, stopped)
        if action == 'invoke':
            controls_after = self.read_controls(current_after, stopped)
            after_snapshot = json.dumps(list(controls_after), ensure_ascii=False,
                                        sort_keys=True, separators=(',', ':'))
            action_result['confirmed'] = before_snapshot != after_snapshot
            action_result['verification'] = 'uia_tree_changed'
        elif action == 'select':
            after = self.inspect_control(current_after, control_id, stopped)
            if not after.get('selected', False):
                action_result['confirmed'] = False
            else:
                action_result['confirmed'] = True
        return action_result

    def _window_api(self):
        if os.name != 'nt':
            raise ObservationError('Windows window actions are only available on Windows.')
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
        user32.IsWindow.restype = ctypes.wintypes.BOOL
        user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
        user32.IsIconic.restype = ctypes.wintypes.BOOL
        user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
        user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
        return user32

    def manage_window(self, selected, action, stopped, input_monitor, input_snapshot):
        current = self._revalidate(selected, stopped)
        self._check_default_desktop()
        user32 = self._window_api()
        hwnd = current['handle']
        if not user32.IsWindow(hwnd):
            raise ObservationError('The selected window closed before the action.')
        if action == 'activate':
            if user32.IsIconic(hwnd):
                self._require_unchanged_input(input_monitor, input_snapshot)
                self._cancelled(stopped)
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            user32.SetForegroundWindow(hwnd)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                self._cancelled(stopped)
                if self._foreground_window() == hwnd:
                    self._revalidate(current, stopped)
                    return {'confirmed': True, 'verification': 'foreground_window'}
                time.sleep(0.025)
            raise InteractionUncertain('Windows did not confirm that the selected window became foreground.')
        if action == 'minimize':
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            if bool(user32.IsIconic(hwnd)):
                self._revalidate(current, stopped)
                return {'confirmed': True, 'verification': 'window_minimized'}
            raise InteractionUncertain('The window did not enter the minimized state.')
        if action == 'restore':
            self._require_unchanged_input(input_monitor, input_snapshot)
            self._cancelled(stopped)
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            if not bool(user32.IsIconic(hwnd)):
                self._revalidate(current, stopped)
                return {'confirmed': True, 'verification': 'window_restored_without_activation'}
            raise InteractionUncertain('The window did not leave the minimized state.')
        raise ObservationError('Unsupported window action.')

    def start_application(self, app, stopped, input_monitor, input_snapshot, *, url=None):
        launch_path = app['launch_path']
        if os.name != 'nt' or not os.path.isfile(launch_path):
            if app.get('public_web') is True:
                raise ObservationError(
                    f'{app["label"]} is not installed at the configured path or its executable is unavailable.')
            raise ObservationError('The configured Windows application path does not exist on this computer.')
        self._check_default_desktop()
        self._cancelled(stopped)
        self._require_unchanged_input(input_monitor, input_snapshot)
        profile_directory = None
        try:
            args = [launch_path]
            if app.get('launch_args') and url is None:
                try:
                    extra_args = shlex.split(app['launch_args'], posix=False)
                    args.extend(extra_args)
                except Exception:
                    pass
            if url is not None:
                if app.get('public_web') is not True:
                    raise ObservationError('This application is not authorized to open public webpages.')
                # A separate user-data root prevents Chrome from routing this launch to Lan's
                # regular profile. Guest is preferred; incognito keeps the isolated fallback
                # private if a Chrome policy disables Guest mode.
                profile_directory = tempfile.mkdtemp(prefix='veil-chrome-guest-')
                # A fresh user-data directory counts as a first run; skip Chrome's welcome and
                # default-browser prompts so the only new window is the requested page.
                args.extend(['--guest', '--incognito', '--new-window',
                             f'--user-data-dir={profile_directory}', '--no-first-run',
                             '--no-default-browser-check', url])
            process = subprocess.Popen(args, shell=False, cwd=ntpath.dirname(launch_path),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, close_fds=True)
        except (OSError, ValueError) as exc:
            if profile_directory:
                shutil.rmtree(profile_directory, ignore_errors=True)
            raise ObservationError(f'Windows could not start {app["label"]}. Check that the app can be launched.') from exc
        if profile_directory:
            def clean_profile_after_exit():
                try:
                    process.wait()
                except Exception:
                    return
                shutil.rmtree(profile_directory, ignore_errors=True)
            threading.Thread(target=clean_profile_after_exit,
                             name='veil-chrome-guest-cleanup', daemon=True).start()
        return process


class WindowsInteractionTools:
    """Program-enforced allowlist, approval, human-priority, and result confirmation."""

    def __init__(self, enabled, applications, *, max_result_bytes=8192, backend=None,
                 platform_enabled=None, idle_seconds=3, timeout_seconds=8,
                 input_monitor=None, discovery=None, desktop_icons_manager=None,
                 desktop_icons_backend=None, store=None, character_id=None):
        self.enabled = enabled is True
        self.platform_enabled = (os.name == 'nt') if platform_enabled is None else bool(platform_enabled)
        self.applications = applications or {}
        self.discovery = discovery or WindowsAppDiscovery()
        self.discovered_applications = {}
        self.backend = backend or WindowsInteractionBackend()
        self.max_result_bytes = max(512, min(int(max_result_bytes), MAX_RESULT_BYTES))
        self.idle_seconds = max(1, min(int(idle_seconds), 30))
        self.timeout_seconds = max(1, min(int(timeout_seconds), 8))
        self.input_monitor = input_monitor or DesktopInputMonitor()
        self.store = store
        self.character_id = character_id or 'default'

        if desktop_icons_manager is not None:
            self.desktop_icons_manager = desktop_icons_manager
        else:
            from desktop_icons import DesktopIconsManager, FakeDesktopIconsBackend, Win32DesktopIconsBackend
            if desktop_icons_backend is None:
                desktop_icons_backend = (Win32DesktopIconsBackend()
                                         if self.platform_enabled else FakeDesktopIconsBackend())
            self.desktop_icons_manager = DesktopIconsManager(
                store=self.store,
                character_id=self.character_id,
                backend=desktop_icons_backend,
            )

        self.tools = (list(WINDOWS_INTERACTION_TOOL_SCHEMAS)
                      if self.enabled and self.platform_enabled else [])

    def get_app(self, app_id):
        if not app_id:
            return None
        return self.applications.get(app_id) or self.discovered_applications.get(app_id)

    def application_id_for(self, window):
        path = str(window.get('process_path', ''))
        if not path:
            return None
        normalized = ntpath.normcase(ntpath.normpath(path))
        for app_id, app in self.applications.items():
            if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in app['process_paths']):
                return app_id
        for app_id, app in self.discovered_applications.items():
            if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in app['process_paths']):
                return app_id
        return None

    def preview_summary(self, name, args):
        if name == 'windows_discover_apps':
            return 'Windows application discovery'
        if name == 'desktop_icons_read':
            return 'Desktop icons inspection'
        if name == 'desktop_icons_arrange':
            return 'Desktop icons arrangement'
        if name == 'desktop_icons_restore':
            return 'Desktop icons restore'
        app_id = args.get('app_id') if isinstance(args, dict) else None
        app = self.get_app(app_id)
        if name == 'windows_launch' and app:
            return f'{app["label"]} launch'
        return app['label'] if app else 'Windows application'

    def _is_stopped(self, should_stop, cancelled):
        if cancelled.is_set():
            return True
        if should_stop:
            try:
                return bool(should_stop())
            except Exception:
                return True
        return False

    def _run(self, operation, summary, should_stop, *, timeout, side_effect):
        cancelled = threading.Event()
        result_queue = queue.Queue(maxsize=1)

        def worker():
            try:
                with desktop_access(timeout=0.1):
                    if self._is_stopped(should_stop, cancelled):
                        raise InteractionPaused('The autonomous activity was paused.')
                    result = operation(lambda: self._is_stopped(should_stop, cancelled))
                result_queue.put((result, None))
            except BaseException as exc:
                result_queue.put((None, exc))

        thread = threading.Thread(target=worker, name='veil-windows-interaction', daemon=True)
        thread.start()
        try:
            result, error = result_queue.get(timeout=timeout)
        except queue.Empty:
            cancelled.set()
            text = ('The Windows action timed out; its result is uncertain. Do not repeat it. '
                    'Observe the target window before deciding what to do next.') if side_effect else (
                    'The Windows UIA lookup timed out; no action was performed.')
            state = '结果不确定' if side_effect else '出错'
            return WindowsToolResult(text, activity_detail=f'{summary} 操作超时；未自动重试。'), state
        if error is not None:
            return self._error(error, summary, side_effect)
        return result, None

    def _error(self, error, summary, side_effect):
        if isinstance(error, DesktopIconsLayoutBlocked):
            return WindowsToolResult(
                str(error), activity_detail=f'{summary} 自动排列阻止调整。'), '被拒绝'
        if isinstance(error, DesktopIconsPaused):
            return WindowsToolResult(
                'Desktop icons operation was interrupted by user activity.',
                activity_detail=f'{summary} 用户操作打断。'), '中断'
        if isinstance(error, DesktopIconsError):
            return WindowsToolResult(
                str(error), activity_detail=f'{summary} 桌面图标操作失败。'), '出错'
        if isinstance(error, DesktopBusy):
            return WindowsToolResult(
                'The shared Windows desktop is busy. No action was performed; retry after it is free.',
                activity_detail=f'{summary} 桌面互斥锁忙；未操作。'), '搁置'
        if isinstance(error, ApprovalInputBusy):
            return WindowsToolResult(str(error),
                                     activity_detail=f'{summary} 已批准，但批准后鼠标键盘一直在动；未操作。'), '中断'
        if isinstance(error, (InteractionPaused, ObservationPaused)):
            return WindowsToolResult(
                'Recent human input or a pause request has priority. Windows interaction stopped before another action.',
                activity_detail=f'{summary} 人工操作优先或自主活动暂停；已停止后续操作。'), '中断'
        if isinstance(error, HumanTookOver):
            return WindowsToolResult(
                'Human keyboard or mouse activity was detected during the action. No further desktop action was started.',
                activity_detail=f'{summary} 检测到人工接管；已停止后续操作。'), '中断'
        if isinstance(error, InteractionUncertain):
            return WindowsToolResult(
                f'{error} Observe the target window before deciding what to do next.',
                activity_detail=f'{summary} 操作结果不确定；未自动重试。'), '结果不确定'
        if isinstance(error, ProtectedWindow):
            return WindowsToolResult(
                'This window or control may contain credentials or protected security content. No interaction was performed.',
                activity_detail=f'{summary} 凭据或安全界面被拦截。'), '被拒绝'
        if isinstance(error, ImportError):
            return WindowsToolResult(
                'Windows UI Automation is unavailable. Close VEIL, rerun setup.bat, and restart it; no desktop action was performed.',
                activity_detail=f'{summary} UI Automation 组件不可用；未操作。'), '出错'
        if isinstance(error, (ObservationError, InteractionError, ValueError)):
            return WindowsToolResult(str(error), activity_detail=f'{summary} {str(error)[:180]}'), (
                '被拒绝' if isinstance(error, ValueError) else '出错')
        return WindowsToolResult(
            'Windows interaction failed safely. The target was not retried.',
            activity_detail=f'{summary} 操作失败；未自动重试。'), '出错'

    def _success(self, payload, detail, summary):
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if len((detail + '\n' + raw).encode('utf-8')) > self.max_result_bytes:
            raw = json.dumps({'truncated': True}, separators=(',', ':'))
        return WindowsToolResult(f'{detail}\n{raw}', activity_detail=summary)

    @staticmethod
    def _valid_window_id(value):
        return isinstance(value, str) and bool(_WINDOW_ID.fullmatch(value))

    def _selected_window(self, app_id, window_id, stopped):
        if not self.get_app(app_id):
            raise ObservationError('The requested app_id is not in this character’s Windows allowlist or discovered applications.')
        if not self._valid_window_id(window_id):
            raise ObservationError('Use an exact window_id returned by windows_list or windows_foreground.')
        windows = self.backend.list_windows(stopped)
        selected = next((item for item in windows if item.get('id') == window_id), None)
        if selected is None:
            raise ObservationError('The selected window is closed or changed. List windows again; no title-based fallback is allowed.')
        if self.application_id_for(selected) != app_id:
            raise ObservationError('The selected window process path does not match the requested allowlisted application.')
        return selected

    def _wait_for_idle_after_approval(self, stopped):
        # Lan's own click on "approve" is fresh mouse input. Give her hand a moment to
        # leave the mouse instead of treating the approval itself as a takeover.
        deadline = time.monotonic() + APPROVAL_IDLE_WAIT_SECONDS
        while self.input_monitor.idle_seconds() < APPROVAL_IDLE_SECONDS:
            if stopped():
                raise InteractionPaused('The autonomous activity was paused.')
            if time.monotonic() >= deadline:
                raise ApprovalInputBusy(
                    'Lan approved, but her mouse or keyboard stayed busy afterwards, so nothing was done. '
                    'Ask her to let go of the mouse for a moment and request the action again.')
            time.sleep(0.1)

    def _check_before_action(self, stopped, *, approved=False):
        if stopped():
            raise InteractionPaused('The autonomous activity was paused.')
        self.backend._check_default_desktop()
        if approved:
            self._wait_for_idle_after_approval(stopped)
            snapshot = self.input_monitor.snapshot()
            if self.input_monitor.changed_since(snapshot):
                raise HumanTookOver('Human input arrived during the pre-action idle check.')
            return snapshot
        snapshot = self.input_monitor.snapshot()
        self.input_monitor.require_idle(self.idle_seconds)
        if self.input_monitor.changed_since(snapshot):
            raise HumanTookOver('Human input arrived during the pre-action idle check.')
        return snapshot

    def _run_action_with_input_guard(self, action, stopped, *, approved=False):
        snapshot = self._check_before_action(stopped, approved=approved)
        result = action(stopped, snapshot)
        if self.input_monitor.changed_since(snapshot) or stopped():
            # The action was already sent; reporting it as "not executed" could make
            # Peter repeat a toggle or a save.
            raise InteractionUncertain(
                'The action was already sent, then human input or a pause arrived; its result is '
                'uncertain. Do not repeat it automatically.')
        return result, snapshot

    def invoke(self, name, args, approval=None, *, should_stop=None):
        summary = self.preview_summary(name, args)
        if (not self.enabled or not self.platform_enabled or name not in WINDOWS_INTERACTION_TOOL_NAMES
                or name not in {tool.name for tool in self.tools}):
            return WindowsToolResult(
                'Windows interaction is unavailable for this character, platform, or allowlist.',
                activity_detail=f'{summary} 桌面交互当前不可用。'), '被拒绝', summary
        if not isinstance(args, dict):
            return WindowsToolResult('A JSON object of arguments is required.',
                                     activity_detail=f'{summary} 参数无效。'), '被拒绝', summary
        try:
            result, state = self._invoke(name, args, approval, should_stop, summary)
        except (ValueError, TypeError):
            result, state = WindowsToolResult('The program rejected invalid interaction arguments.',
                                              activity_detail=f'{summary} 参数无效。'), '被拒绝'
        if state is not None:
            return result, state, summary
        return result, '成功', summary

    def _invoke(self, name, args, approval, should_stop, summary):
        if name == 'windows_discover_apps':
            if set(args) - {'query'} or ('query' in args and not isinstance(args.get('query'), str)):
                return WindowsToolResult('Invalid arguments. Optional query must be a string.', activity_detail='参数无效。'), '被拒绝'
            query = (args.get('query') or '').strip().lower()

            def discover(stopped):
                if stopped():
                    raise InteractionPaused('The autonomous activity was paused.')
                discovered_list = self.discovery.discover_applications(existing_apps=self.applications)
                for item in discovered_list:
                    self.discovered_applications[item['app_id']] = item

                matches = []
                for item in discovered_list:
                    if not query or query in item['app_id'].lower() or query in item['label'].lower():
                        matches.append({
                            'app_id': item['app_id'],
                            'name': item['label'],
                            'can_launch': item['can_launch'],
                            'can_observe': item['can_observe'],
                            'can_interact': item['can_interact'],
                        })

                launchable = sum(1 for m in matches if m['can_launch'])
                detail = f'发现 Windows 软件 {len(matches)} 款（可启动 {launchable} 款）。'
                summary_msg = f'已发现 {len(matches)} 款可用应用。'
                return self._success({
                    'count': len(matches),
                    'applications': matches[:30],
                    'query': query or None,
                }, detail, summary_msg)

            return self._run(discover, summary, should_stop, timeout=self.timeout_seconds, side_effect=False)

        if name == 'desktop_icons_read':
            if args:
                return WindowsToolResult('desktop_icons_read accepts no arguments.', activity_detail='参数无效。'), '被拒绝'

            def read_icons(stopped):
                if stopped():
                    raise InteractionPaused('The autonomous activity was paused.')
                data = self.desktop_icons_manager.read_desktop()
                icon_count = data['count']
                auto_arr = data['metrics']['auto_arrange']
                detail = f'读取桌面图标 {icon_count} 个（自动排列: {"开启" if auto_arr else "关闭"}）。'
                summary_msg = f'已读取 {icon_count} 个桌面图标布局。'
                return self._success(data, detail, summary_msg)

            return self._run(read_icons, summary, should_stop, timeout=self.timeout_seconds, side_effect=False)

        if name == 'desktop_icons_arrange':
            if (set(args) - {'moves', 'reason'} or 'moves' not in args
                    or not isinstance(args.get('moves'), list) or not args['moves']):
                return WindowsToolResult('desktop_icons_arrange requires a non-empty moves list.', activity_detail='参数无效。'), '被拒绝'
            moves = args['moves']
            for m in moves:
                if (not isinstance(m, dict) or not isinstance(m.get('icon_id'), str)
                        or not isinstance(m.get('x'), (int, float)) or not isinstance(m.get('y'), (int, float))):
                    return WindowsToolResult('Each move must specify icon_id (str), x (int), and y (int).', activity_detail='参数无效。'), '被拒绝'

            def arrange_icons(stopped):
                result = self.desktop_icons_manager.arrange_icons(
                    moves=moves, should_stop=stopped, trigger='autonomous_arrange'
                )
                moved = result['moved_count']
                snap_id = result['snapshot_id']
                detail = (f'整理桌面图标 {moved} 个（备份快照: {snap_id}）。'
                          if not result.get('interrupted')
                          else f'整理桌面图标被用户操作打断，已完成 {moved} 个。')
                summary_msg = f'已移动 {moved} 个图标。' if not result.get('interrupted') else '用户操作打断图标整理。'
                return self._success(result, detail, summary_msg)

            return self._run(arrange_icons, summary, should_stop, timeout=self.timeout_seconds, side_effect=True)

        if name == 'desktop_icons_restore':
            if set(args) - {'snapshot_id'} or ('snapshot_id' in args and not isinstance(args.get('snapshot_id'), str)):
                return WindowsToolResult('Optional snapshot_id must be a string.', activity_detail='参数无效。'), '被拒绝'
            snap_id = args.get('snapshot_id')

            def restore_icons(stopped):
                result = self.desktop_icons_manager.restore_snapshot(
                    snapshot_id=snap_id, should_stop=stopped
                )
                restored = result['restored_count']
                target_id = result['snapshot_id']
                detail = (f'恢复桌面图标布局 {restored} 个（快照: {target_id}）。'
                          if not result.get('interrupted')
                          else f'恢复图标布局被用户操作打断，已恢复 {restored} 个。')
                summary_msg = f'已恢复 {restored} 个桌面图标。' if not result.get('interrupted') else '用户操作打断图标恢复。'
                return self._success(result, detail, summary_msg)

            return self._run(restore_icons, summary, should_stop, timeout=self.timeout_seconds, side_effect=True)

            return self._run(restore_icons, summary, should_stop, timeout=self.timeout_seconds, side_effect=True)

        if name == 'windows_launch':
            if (set(args) - {'app_id', 'url'} or 'app_id' not in args
                    or not isinstance(args.get('app_id'), str)
                    or ('url' in args and not isinstance(args.get('url'), str))):
                return WindowsToolResult('Use an allowlisted app_id and, optionally, one public HTTP(S) URL.', activity_detail='参数无效。'), '被拒绝'
            app = self.get_app(args['app_id'])
            if app is None:
                return WindowsToolResult('That app_id is not allowlisted or discovered; no executable was started.',
                                         activity_detail=f'{summary} 未找到该应用。'), '被拒绝'
            if app.get('discovered') and not app.get('can_launch'):
                return WindowsToolResult('This discovered application cannot be safely launched automatically; launch was refused.',
                                         activity_detail=f'{summary} 安全规则拒绝启动。'), '被拒绝'
            url = None
            if app.get('public_web') is True and 'url' not in args:
                return WindowsToolResult('This browser permission only opens a supplied public webpage URL.',
                                         activity_detail=f'{summary} 需要公开网页 URL。'), '被拒绝'
            if 'url' in args:
                if app.get('public_web') is not True:
                    return WindowsToolResult('This allowlisted application is not authorized to open webpage URLs.',
                                             activity_detail=f'{summary} 网页打开未授权。'), '被拒绝'
                from web_tools import public_http_url
                url = public_http_url(args['url'])
                if not url:
                    return WindowsToolResult('Only a valid public HTTP(S) webpage URL can be opened.',
                                             activity_detail=f'{summary} 仅允许公开网页 URL。'), '被拒绝'
                from urllib.parse import urlsplit
                if urlsplit(url).path.casefold().endswith((
                        '.exe', '.msi', '.msix', '.bat', '.cmd', '.ps1', '.scr', '.com', '.dll')):
                    return WindowsToolResult('Direct executable or installer download URLs are not opened.',
                                             activity_detail=f'{summary} 可执行文件下载被拒绝。'), '被拒绝'
            def launch(stopped):
                def work(stop):
                    windows = [item for item in self.backend.list_windows(stop)
                               if self.application_id_for(item) == app['app_id']]
                    existing_window_ids = {item['id'] for item in windows}
                    if windows and url is None:
                        foreground = [item for item in windows if item.get('foreground')]
                        if len(foreground) == 1:
                            selected = foreground[0]
                        elif len(windows) == 1:
                            selected = windows[0]
                        else:
                            raise ObservationError('Several windows of this app are open. Select an exact window_id before activation.')
                        result, _snapshot = self._run_action_with_input_guard(
                            lambda inner_stop, _snapshot: self.backend.manage_window(
                                selected, 'activate', inner_stop, self.input_monitor,
                                _snapshot), stop)
                        return self._success({
                            'app_id': app['app_id'], 'application': app['label'],
                            'window_id': selected['id'], 'reused': True,
                            'confirmed': result['confirmed'],
                        }, '已有窗口已复用并切换到前台。', f'{app["label"]} 已复用现有窗口。')
                    _process, launch_input_snapshot = self._run_action_with_input_guard(
                        lambda inner_stop, _snapshot: self.backend.start_application(
                            app, inner_stop, self.input_monitor, _snapshot, url=url), stop)
                    # Ticket 151 follow-up: a fresh Guest profile can take well over 7 seconds to show
                    # its first window on Lan's PC, so a browser launch waits longer.
                    deadline = time.monotonic() + (PUBLIC_WEB_WINDOW_WAIT_SECONDS if url is not None
                                                   else APP_WINDOW_WAIT_SECONDS)
                    while time.monotonic() < deadline:
                        if stop():
                            raise InteractionPaused('The autonomous activity was paused.')
                        if self.input_monitor.changed_since(launch_input_snapshot):
                            raise HumanTookOver('Human input arrived while waiting for the app window.')
                        matches = [item for item in self.backend.list_windows(stop)
                                   if self.application_id_for(item) == app['app_id']
                                   and (url is None or item['id'] not in existing_window_ids)]
                        if matches:
                            foreground = [item for item in matches if item.get('foreground')]
                            selected = foreground[0] if len(foreground) == 1 else matches[0]
                            if self.input_monitor.changed_since(launch_input_snapshot):
                                raise HumanTookOver('Human input arrived before the app result was confirmed.')
                            return self._success({
                                'app_id': app['app_id'], 'application': app['label'],
                                'window_id': selected['id'], 'reused': False,
                                'confirmed': True,
                            }, '应用窗口已出现，并已按程序路径确认身份。', f'{app["label"]} 已启动并确认窗口。')
                        time.sleep(0.1)
                    if url is not None:
                        raise InteractionUncertain(
                            f'{app["label"]} was started, but its new Guest window was not seen within '
                            f'{PUBLIC_WEB_WINDOW_WAIT_SECONDS} seconds; it may still be opening. Check with '
                            'windows_list before launching again; no existing browser window was activated.')
                    raise InteractionUncertain('The app process was started but its allowlisted window did not appear before timeout; do not launch it again automatically.')
                return work(stopped)
            return self._run(launch, summary, should_stop,
                             timeout=self.timeout_seconds, side_effect=True)

        if name == 'windows_manage':
            if (set(args) != {'app_id', 'window_id', 'action'}
                    or not isinstance(args.get('app_id'), str)
                    or args.get('action') not in ('activate', 'minimize', 'restore')):
                return WindowsToolResult('Use an allowlisted app_id, exact window_id, and activate/minimize/restore action.',
                                         activity_detail='参数无效。'), '被拒绝'
            app = self.get_app(args['app_id'])
            if app is None or args['action'] not in app['actions']:
                return WindowsToolResult('This app or window action is not authorized by the program allowlist.',
                                         activity_detail=f'{summary} 窗口动作未授权。'), '被拒绝'
            def manage(stopped):
                def work(stop):
                    selected = self._selected_window(app['app_id'], args['window_id'], stop)
                    result, _snapshot = self._run_action_with_input_guard(
                        lambda inner_stop, _snapshot: self.backend.manage_window(
                            selected, args['action'], inner_stop, self.input_monitor,
                            _snapshot), stop)
                    return self._success({
                        'app_id': app['app_id'], 'window_id': selected['id'],
                        'action': args['action'], 'confirmed': result['confirmed'],
                        'verification': result['verification'],
                    }, '窗口动作完成并已核对当前状态。', f'{app["label"]} 窗口 {args["action"]} 已核验。')
                return work(stopped)
            return self._run(manage, summary, should_stop,
                             timeout=self.timeout_seconds, side_effect=True)

        if name == 'windows_control_action':
            allowed_keys = {'app_id', 'window_id', 'control_id', 'action', 'text'}
            action = args.get('action')
            required = {'app_id', 'window_id', 'control_id', 'action'}
            if (set(args) - allowed_keys or not required.issubset(args)
                    or not isinstance(args.get('app_id'), str)
                    or not self._valid_window_id(args.get('window_id'))
                    or not isinstance(args.get('control_id'), str)
                    or not _CONTROL_ID.fullmatch(args['control_id'])
                    or action not in _ALLOWED_ACTIONS):
                return WindowsToolResult('Use an exact allowlisted app_id, current window_id, fresh control_id, and supported action.',
                                         activity_detail='参数无效。'), '被拒绝'
            if action == 'set_text':
                text_value = args.get('text')
                if (not isinstance(text_value, str) or len(text_value) > MAX_TEXT_CHARS
                        or '\x00' in text_value):
                    return WindowsToolResult(f'text must be a string of at most {MAX_TEXT_CHARS} characters without NUL.',
                                             activity_detail='文本输入参数无效。'), '被拒绝'
            elif 'text' in args:
                return WindowsToolResult('text is accepted only for set_text.', activity_detail='参数无效。'), '被拒绝'
            app = self.get_app(args['app_id'])
            if app is None or action not in app['actions']:
                return WindowsToolResult('This app or UIA action is not authorized by the program allowlist.',
                                         activity_detail=f'{summary} UIA 动作未授权。'), '被拒绝'
            if app.get('discovered') and not app.get('can_interact'):
                return WindowsToolResult('This discovered application has observation-only permissions; interaction is not authorized.',
                                         activity_detail=f'{summary} 交互权限未开放。'), '被拒绝'
            if should_stop and should_stop():
                return WindowsToolResult('The autonomous activity is paused; no desktop action was started.',
                                         activity_detail=f'{summary} 自主活动暂停；未操作。'), '中断'

            def inspect(stopped):
                selected = self._selected_window(app['app_id'], args['window_id'], stopped)
                return selected, self.backend.inspect_control(selected, args['control_id'], stopped)

            inspected, error = self._run(inspect, summary, should_stop, timeout=3, side_effect=False)
            if error is not None:
                return inspected, error
            selected, metadata = inspected
            if app.get('discovered'):
                if is_file_dialog_control(metadata, selected):
                    return WindowsToolResult(
                        'This control is inside a file open/save dialog. File dialog operations are prohibited on discovered apps to protect files.',
                        activity_detail=f'{summary} 文件对话框被程序拒绝。'), '被拒绝'
                if action == 'invoke' and is_ambiguous_confirmation(metadata, selected):
                    return WindowsToolResult(
                        'Generic confirmation button in unverified context is rejected for safety.',
                        activity_detail=f'{summary} 模糊确认按钮被程序拒绝。'), '被拒绝'
                if action == 'set_text' and is_unsafe_text_input(metadata):
                    return WindowsToolResult(
                        'Text input on file paths, command inputs, or address fields is prohibited on discovered apps.',
                        activity_detail=f'{summary} 危险文本输入被程序拒绝。'), '被拒绝'
            if _BLOCKED_CONTROL.search(' '.join((metadata.get('name', ''),
                                                 metadata.get('automation_id', ''),
                                                 *metadata.get('ancestor_labels', ())))):
                return WindowsToolResult(
                    'This control is within a delete/install/purchase/administrator action scope that this ticket does not open.',
                    activity_detail=f'{summary} 高风险动作被程序拒绝。'), '被拒绝'
            if action not in metadata.get('patterns', ()):
                # Pattern-specific operation still gets checked again during the actual call.
                if action not in ('expand', 'collapse') or 'expand-collapse' not in metadata.get('patterns', ()):
                    keyboard_value_control = (
                        action == 'set_text'
                        and metadata.get('type') in ('EditControl', 'DocumentControl')
                    )
                    if action != 'set_text' or ('value' not in metadata.get('patterns', ())
                                                 and not keyboard_value_control):
                        return WindowsToolResult('The current control does not advertise the requested UIA pattern.',
                                                 activity_detail=f'{summary} 控件不支持该 UIA 模式。'), '被拒绝'
            if action == 'select' and metadata['type'] not in (
                    'TabItemControl', 'ListItemControl', 'RadioButtonControl'):
                return WindowsToolResult('Selection is limited to a tab, list item, or radio item.',
                                         activity_detail=f'{summary} 控件类型不允许选择。'), '被拒绝'
            if action == 'set_text' and (metadata['type'] not in ('EditControl', 'DocumentControl')
                                         or metadata.get('read_only')):
                return WindowsToolResult('Text replacement is limited to writable UIA Edit or Document controls.',
                                         activity_detail=f'{summary} 文本控件不可写。'), '被拒绝'
            needs_approval = _needs_approval(action, metadata)
            if needs_approval:
                if approval is None:
                    return WindowsToolResult('Saving, sending, submitting or overwriting needs Lan approval, '
                                             'and none can be asked here; the window was not changed.',
                                             activity_detail=f'{summary} 需要 Lan 明确批准。'), '被拒绝'
                control_label = _approval_control_label(metadata)
                prompt = (f'Peter 想在{app["label"]}里点「{control_label}」'
                          '（保存、发送、提交或覆盖类操作）。同意这一次吗？y/N ')
                try:
                    answer = approval(prompt)
                except Exception:
                    return WindowsToolResult(
                        'VEIL could not collect approval; the window was not changed.',
                        activity_detail=f'{summary} 无法取得 Lan 批准；未操作。'), '被拒绝'
                if answer == NOT_ASKED:
                    return WindowsToolResult(NOT_ASKED_TEXT + ' The window was not changed.',
                                             activity_detail=f'{summary} 自主活动时不询问 Lan；未操作。'), '被拒绝'
                if str(answer or '').strip().casefold() != 'y':
                    return WindowsToolResult('Lan did not approve this action; the window was not changed.',
                                             activity_detail=f'{summary} Lan 未批准 UIA 动作；未操作。'), '被拒绝'

            def act(stopped):
                def work(stop):
                    current = self._selected_window(app['app_id'], args['window_id'], stop)
                    fresh = self.backend.inspect_control(current, args['control_id'], stop)
                    if (fresh['type'] != metadata['type']
                            or fresh.get('automation_id') != metadata.get('automation_id')
                            or fresh.get('name') != metadata.get('name')
                            or fresh.get('parent_type') != metadata.get('parent_type')
                            or fresh.get('ancestor_labels') != metadata.get('ancestor_labels')):
                        raise ObservationError('The selected control changed after approval. Observe it again; no action was performed.')
                    if app.get('discovered'):
                        if is_file_dialog_control(fresh, current):
                            raise ObservationError('The selected control is inside a file dialog; action was refused.')
                        if action == 'invoke' and is_ambiguous_confirmation(fresh, current):
                            raise ObservationError('Generic confirmation button in unverified context was refused.')
                        if action == 'set_text' and is_unsafe_text_input(fresh):
                            raise ObservationError('Text input on path or command field was refused.')
                    if _BLOCKED_CONTROL.search(' '.join((fresh.get('name', ''),
                                                         fresh.get('automation_id', ''),
                                                         *fresh.get('ancestor_labels', ())))):
                        raise ObservationError('The selected control is in a blocked action scope; no action was performed.')
                    text_value = args.get('text') if action == 'set_text' else None
                    result, _snapshot = self._run_action_with_input_guard(
                        lambda inner_stop, input_snapshot: self.backend.apply_control_action(
                            current, args['control_id'], action, text_value,
                            inner_stop, self.input_monitor, input_snapshot), stop,
                        approved=needs_approval)
                    if result.get('confirmed') is not True:
                        raise InteractionUncertain(
                            'The action was attempted but its resulting UIA state could not be confirmed; '
                            'do not repeat it automatically.')
                    payload = {
                        'app_id': app['app_id'], 'window_id': current['id'],
                        'control_id': args['control_id'], 'action': action,
                        'confirmed': True, 'verification': result.get('verification'),
                    }
                    if action == 'set_text':
                        payload['text_matches'] = True
                        payload['text_characters'] = len(text_value)
                    return self._success(payload, '控件动作已执行并重新读取 UIA 状态确认。',
                                          f'{app["label"]} UIA {action} 已核验；未记录控件文字。')
                return work(stopped)
            return self._run(act, summary, should_stop,
                             timeout=self.timeout_seconds, side_effect=True)

        return WindowsToolResult('This interaction tool is not registered.',
                                 activity_detail=f'{summary} 工具未注册。'), '被拒绝'
