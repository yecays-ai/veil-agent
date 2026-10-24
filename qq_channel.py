"""Ticket 102: Peter and Lan talk through his QQ account by operating the QQ desktop window.

No injection, no protocol: like QQSafeChat (MIT) and qq-operations (MIT) it reads the chat through
Windows UI Automation and types with the clipboard, using the `uiautomation` package. QQ NT builds
its full accessibility tree only for the foreground window, so the chat window is brought forward
only while Lan has not touched the computer for a while, and the previous window is restored after.
Before typing, the chat header must show Lan's name; otherwise nothing is sent. Lan's QQ NT exposes no
class names, so the window is read by accessibility names and control types (`parse_chat`).
"""
import ctypes
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path


QQ_PREFIX = '[Lan sent this on QQ; she is away from the computer.]\n'
ENV_KEY = 'QQ_LAN_CONTACT'

# QQ NT accessibility names, as read on Lan's QQ on 2026-10-24 (its controls carry no class names).
# Change here after a QQ upgrade; .local-tmp/qq_check.py prints the current tree.
MESSAGE_LIST_NAME = '消息列表'     # WindowControl holding the bubbles; each bubble is a GroupControl named after its sender
TOOLBAR_NAME = '会话'             # ToolBarControl just above the input box
SEND_BUTTON_NAME = '发送'
OWN_INPUT_MARGIN_MS = 1500


def new_messages(seen, current):
    """Messages in `current` that were not on screen at the last read.

    Both are lists of (who, text) in screen order. The chat only grows at the bottom and may scroll,
    so the old tail reappears at the head of the new list; whatever follows that overlap is new.
    """
    for size in range(min(len(seen), len(current)), 0, -1):
        if seen[-size:] == current[:size]:
            return current[size:]
    # No overlap: everything scrolled away. Only messages after Peter's last one are safe to treat as new.
    last_self = max((i for i, (who, _) in enumerate(current) if who == 'self'), default=-1)
    return current[last_self + 1:]


def parse_chat(nodes, contact):
    """Read the chat window's accessibility tree.

    `nodes` is the tree in walk order as (depth, control type, name, control). Returns
    (header_ok, messages, editor): whether the header button shows `contact`, the bubbles as
    (who, text) with who 'other' for Lan and 'self' for Peter, and the input box control or None.
    """
    header_ok, messages, editor = False, [], None
    list_depth = group_depth = toolbar_seen = None
    current = None
    for depth, kind, name, control in nodes:
        name = (name or '').strip()
        if list_depth is not None and depth <= list_depth:
            list_depth = None          # left the message list
        if list_depth is None and current is not None:
            messages.append(current)
            current = None
        if kind == 'WindowControl' and name == MESSAGE_LIST_NAME:
            list_depth = depth
            continue
        if list_depth is not None:
            if kind == 'GroupControl' and name and (group_depth is None or depth == group_depth):
                group_depth = depth
                if current is not None:
                    messages.append(current)
                current = ['other' if name == contact else 'self', [], depth]
            elif current is not None and kind == 'TextControl' and depth <= current[2]:
                messages.append(current)   # a timestamp between bubbles ends the bubble
                current = None
            elif current is not None and depth >= current[2]:
                # the named group is the avatar; the bubble is its unnamed sibling at the same depth
                if kind == 'TextControl' and name:
                    current[1].append(name)
                elif kind == 'ImageControl' and name:   # the avatar picture has no name
                    current[1].append('[图片]')
            continue
        if toolbar_seen is None and not messages and kind == 'ButtonControl' and name == contact:
            header_ok = True
        if kind == 'ToolBarControl' and name == TOOLBAR_NAME:
            toolbar_seen = True
        elif toolbar_seen and editor is None and (kind in ('EditControl', 'DocumentControl')
                                                 or (kind == 'GroupControl' and name == contact)):
            editor = control
    if current is not None:
        messages.append(current)
    return header_ok, [(who, '\n'.join(parts) or '[图片]') for who, parts, _ in messages], editor


class IdleClock:
    """How long Lan has been away from the keyboard and mouse, ignoring input this program made."""

    def __init__(self, last_input_ms, now_ms):
        self.last_input_ms, self.now_ms = last_input_ms, now_ms
        self.own_input_until = 0
        self.human_input = now_ms()

    def mark_own_input(self):
        self.own_input_until = self.now_ms()

    def idle_seconds(self):
        last = self.last_input_ms()
        if last > self.own_input_until + OWN_INPUT_MARGIN_MS:
            self.human_input = max(self.human_input, last)
        return max(0, self.now_ms() - self.human_input) / 1000


def _windows_input_clock():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

    def last_input_ms():
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
        return info.dwTime

    def now_ms():
        return ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return last_input_ms, now_ms


def _process_name(pid):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        size = ctypes.c_uint(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
        return ''
    finally:
        kernel32.CloseHandle(handle)


class QQChannel:
    name, label, prefix = 'qq', 'QQ', QQ_PREFIX

    def __init__(self, store_root, character_id, contact, settings, ui=None, clock=None):
        self.contact = contact.strip()
        self.poll_seconds = settings['poll_seconds']
        self.idle_needed = settings['idle_seconds']
        self.daily_max_sent = settings['daily_max_sent']
        self.log_path = Path(store_root) / character_id / 'qq-log.jsonl'
        self.ui = ui or QQWindow(self.contact)
        self.clock = clock or IdleClock(*_windows_input_clock())
        self.seen = None
        self.pending_snapshot = None

    @classmethod
    def from_env(cls, root, cfg, store_root):
        settings = getattr(cfg, 'qq', None)
        if not settings or not settings.get('enabled') or sys.platform != 'win32':
            return None
        from dotenv import dotenv_values
        values = dotenv_values(Path(root) / '.env', interpolate=False, encoding='utf-8-sig')
        contact = (values.get(ENV_KEY) or '').strip()
        if not contact:
            return None
        return cls(store_root, cfg.id, contact, settings)

    def _log(self, row):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': datetime.now().astimezone().isoformat(), **row},
                                    ensure_ascii=False) + '\n')

    def sent_today(self):
        today = datetime.now().astimezone().date().isoformat()
        try:
            lines = self.log_path.read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            return 0
        count = 0
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            count += row.get('kind') == 'sent' and row.get('time', '')[:10] == today
        return count

    def away(self):
        # Idle long enough, and not watching something full screen (never pop QQ over a video).
        return self.clock.idle_seconds() >= self.idle_needed and not self.ui.other_app_full_screen()

    # away-bridge interface ----------------------------------------------------
    def ready_to_send(self):
        try:
            return self.away()
        except QQUnavailable:
            return False   # poll reports why

    def poll(self):
        from away_bridge import ChannelError, Incoming
        try:
            if not self.away():
                return []   # she is at the computer: never pull windows in front of her
            current = self.ui.read()
        except QQUnavailable as exc:
            raise ChannelError(str(exc)) from exc
        finally:
            self.clock.mark_own_input()
        if self.seen is None:
            self.seen = current   # first read after start: what is already there is old
            return []
        fresh = [text for who, text in new_messages(self.seen, current) if who == 'other' and text]
        if not fresh:
            self.seen = current
            return []
        self.pending_snapshot = current
        key = hashlib.sha256(json.dumps(current[-12:], ensure_ascii=False).encode('utf-8')).hexdigest()
        return [Incoming(key, '\n'.join(fresh))]

    def ack(self, incoming):
        if self.pending_snapshot is not None:
            self.seen = self.pending_snapshot
            self.pending_snapshot = None
        self._log({'kind': 'received'})

    def reply(self, text, _reply_ref=None):
        parts = [part.strip() for part in text.split('\n\n') if part.strip()]
        for part in parts:
            if self.sent_today() >= self.daily_max_sent:
                return f'今天的 QQ 消息已达到 {self.daily_max_sent} 条上限，剩下的没有发出。'
            try:
                self.ui.send(part)
            except QQUnavailable as exc:
                return str(exc)
            finally:
                self.clock.mark_own_input()
            self._log({'kind': 'sent'})
        try:
            self.seen = self.ui.read()   # his own messages are now on screen; don't mistake them later
        except QQUnavailable:
            pass
        finally:
            self.clock.mark_own_input()
        return None


class QQUnavailable(Exception):
    """The chat window cannot be used right now; the message is a Chinese line for the desktop."""


class QQWindow:
    """The real QQ NT window, through Windows UI Automation (Windows only)."""

    def __init__(self, contact):
        self.contact = contact

    def _auto(self):
        try:
            import uiautomation as auto
        except ImportError as exc:
            raise QQUnavailable('操作 QQ 的组件没有装好：请退出服务，重新运行 setup.bat，再打开 Peter。') from exc
        return auto

    def other_app_full_screen(self):
        auto = self._auto()
        handle = auto.GetForegroundWindow()
        if not handle:
            return False
        try:
            import ctypes.wintypes
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            if _process_name(pid.value) == 'qq.exe':
                return False
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect))
            monitor = ctypes.windll.user32.MonitorFromWindow(handle, 2)

            class MONITORINFO(ctypes.Structure):
                _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', ctypes.wintypes.RECT),
                            ('rcWork', ctypes.wintypes.RECT), ('dwFlags', ctypes.c_ulong)]
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info))
            screen = info.rcMonitor
            return (rect.left <= screen.left and rect.top <= screen.top
                    and rect.right >= screen.right and rect.bottom >= screen.bottom)
        except Exception:
            return False

    def _chat_window(self, auto):
        for window in auto.GetRootControl().GetChildren():
            try:
                if _process_name(window.ProcessId) != 'qq.exe':
                    continue
                rect = window.BoundingRectangle
                if rect.width() < 60 or rect.height() < 60:
                    continue
                if window.Name == self.contact or self.contact in (window.Name or ''):
                    return window
            except Exception:
                continue
        raise QQUnavailable(f'找不到和“{self.contact}”的 QQ 聊天窗口：请在 Peter 的 QQ 里把和你的聊天单独开成一个窗口，并保持打开。')

    @staticmethod
    def _tree(auto, window):
        nodes = []
        for control, depth in auto.WalkControl(window, maxDepth=60):
            try:
                nodes.append((depth, control.ControlTypeName, control.Name, control))
            except Exception:
                continue
        return nodes

    def _parse(self, auto, window):
        return parse_chat(self._tree(auto, window), self.contact)

    def _front(self, auto, window):
        previous = auto.GetForegroundWindow()
        handle = window.NativeWindowHandle
        if auto.IsIconic(handle):
            auto.ShowWindow(handle, 9)  # SW_RESTORE
        auto.SetForegroundWindow(handle)
        time.sleep(0.9)   # QQ NT rebuilds the accessibility tree once it is in front
        if not self._parse(auto, window)[0]:
            self._back(auto, previous)
            raise QQUnavailable('QQ 窗口打不开或对象不是你（电脑可能锁屏了）；为了不发错人，这次没有收发 QQ。')
        return previous

    @staticmethod
    def _back(auto, previous):
        if previous:
            try:
                auto.SetForegroundWindow(previous)
            except Exception:
                pass

    def read(self):
        auto = self._auto()
        with auto.UIAutomationInitializerInThread():
            window = self._chat_window(auto)
            header_ok, messages, _ = self._parse(auto, window)
            if header_ok:
                return messages   # the tree is complete without bringing QQ forward
            previous = self._front(auto, window)
            try:
                return self._parse(auto, window)[1]
            finally:
                self._back(auto, previous)

    def send(self, text):
        auto = self._auto()
        with auto.UIAutomationInitializerInThread():
            window = self._chat_window(auto)
            previous = self._front(auto, window)
            saved_clipboard = None
            try:
                editor = self._parse(auto, window)[2]
                if editor is None:
                    raise QQUnavailable('QQ 输入框找不到了（QQ 可能更新了界面），这条没有发出。')
                try:
                    saved_clipboard = auto.GetClipboardText()
                except Exception:
                    saved_clipboard = None
                rect = editor.BoundingRectangle
                auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.3)
                auto.SetClipboardText(text)
                auto.SendKeys('{Ctrl}a', waitTime=0.2)
                auto.SendKeys('{Ctrl}v', waitTime=0.6)
                typed = ''.join(child.Name or '' for child, _ in auto.WalkControl(editor, maxDepth=20)
                                if child.ControlTypeName == 'TextControl')
                if re.sub(r'\s+', '', typed) != re.sub(r'\s+', '', text):
                    auto.SendKeys('{Ctrl}a{Del}', waitTime=0.2)
                    raise QQUnavailable('QQ 输入框里的内容和要发的不一致，已清空，这条没有发出。')
                button = window.ButtonControl(Name=SEND_BUTTON_NAME, searchDepth=40)
                if not button.Exists(1):
                    auto.SendKeys('{Ctrl}a{Del}', waitTime=0.2)
                    raise QQUnavailable('QQ 发送按钮找不到了，这条没有发出。')
                button.Click(waitTime=1.0)
            finally:
                if saved_clipboard is not None:
                    try:
                        auto.SetClipboardText(saved_clipboard)
                    except Exception:
                        pass
                self._back(auto, previous)
