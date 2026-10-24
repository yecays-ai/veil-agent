"""Ticket 102: Peter and Lan talk through his QQ account by operating the QQ desktop window.

No injection, no protocol: like QQSafeChat (MIT) and qq-operations (MIT) it reads the chat through
Windows UI Automation and types with the clipboard, using the `uiautomation` package. QQ NT builds
its full accessibility tree only for the foreground window, so the chat window is brought forward
only while Lan has not touched the computer for a while, and the previous window is restored after.
Before typing, the chat header must show Lan's name; otherwise nothing is sent.
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

# QQ NT class names (from qq-operations' qq_config.ps1); change here after a QQ upgrade.
EDITOR_CLASS = re.compile(r'qq-msg-editor')
HEADER_CLASS = re.compile(r'chat-header__contact-name')
MESSAGE_CLASS = re.compile(r'^(message-container|msg-content-container)')
SELF_CLASS = re.compile(r'container--self')
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
        return self.away()

    def poll(self):
        from away_bridge import ChannelError, Incoming
        if not self.away():
            return []   # she is at the computer: never pull windows in front of her
        try:
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
        import uiautomation as auto
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

    def _find(self, auto, root, pattern):
        for control, _depth in auto.WalkControl(root, maxDepth=40):
            try:
                if pattern.search(control.ClassName or ''):
                    return control
            except Exception:
                continue
        return None

    def _front(self, auto, window):
        previous = auto.GetForegroundWindow()
        handle = window.NativeWindowHandle
        if auto.IsIconic(handle):
            auto.ShowWindow(handle, 9)  # SW_RESTORE
        auto.SetForegroundWindow(handle)
        time.sleep(0.9)   # QQ NT rebuilds the accessibility tree once it is in front
        header = self._find(auto, window, HEADER_CLASS)
        if header is None or self.contact not in (header.Name or ''):
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

    def _messages(self, auto, window):
        found = []
        for control, _depth in auto.WalkControl(window, maxDepth=40):
            try:
                cls = control.ClassName or ''
            except Exception:
                continue
            if not MESSAGE_CLASS.search(cls):
                continue
            texts = []
            for child, _ in auto.WalkControl(control, maxDepth=20):
                try:
                    if child.ControlTypeName == 'TextControl' and (child.Name or '').strip():
                        texts.append(child.Name.strip())
                except Exception:
                    continue
            found.append(('self' if SELF_CLASS.search(cls) else 'other', ''.join(texts) or '[图片]'))
        return found

    def read(self):
        auto = self._auto()
        with auto.UIAutomationInitializerInThread():
            window = self._chat_window(auto)
            header = self._find(auto, window, HEADER_CLASS)
            if header is not None and self.contact in (header.Name or ''):
                return self._messages(auto, window)   # the tree is complete without bringing QQ forward
            previous = self._front(auto, window)
            try:
                return self._messages(auto, window)
            finally:
                self._back(auto, previous)

    def send(self, text):
        auto = self._auto()
        with auto.UIAutomationInitializerInThread():
            window = self._chat_window(auto)
            previous = self._front(auto, window)
            saved_clipboard = None
            try:
                editor = self._find(auto, window, EDITOR_CLASS)
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
