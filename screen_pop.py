"""Screen pop tool validation and read-only Windows desktop state checks."""
import ctypes
import os
import re
from types import SimpleNamespace


SCREEN_POP_TOOL_NAMES = frozenset({'screen_pop'})
_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
_ANIMATIONS = frozenset({'scroll', 'fade', 'bounce'})
_POSITIONS = frozenset({'top', 'center', 'bottom'})
MAX_DANMAKU_DURATION = 20
DEFAULT_DANMAKU_DURATION = 12
MAX_TEXT_CHARS = 500

SCREEN_POP_TOOL = SimpleNamespace(
    name='screen_pop',
    description=(
        'Show a short message on Lan’s screen. Choose popup for a window with an OK button or danmaku '
        'for animated text that crosses or appears on the screen. Popup count may be 1–10. Optional danmaku '
        'settings are color (#RRGGBB), animation (scroll/fade/bounce), position (top/center/bottom), and '
        'duration (1–20 seconds); defaults are white, scroll, top, and 12 seconds. '
        'The result reports only whether it was shown; later dismissal or completion appears in the activity record.'
    ),
    inputSchema={
        'type': 'object',
        'properties': {
            'text': {'type': 'string', 'minLength': 1, 'maxLength': MAX_TEXT_CHARS},
            'style': {'type': 'string', 'enum': ['popup', 'danmaku'],
                      'description': 'Choose the presentation style for this message.'},
            'count': {'type': 'integer', 'minimum': 1, 'maximum': 10,
                      'description': 'Number of layered popups; applies only to popup.'},
            'color': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$',
                      'description': 'Optional danmaku text color in #RRGGBB format; defaults to white.'},
            'animation': {'type': 'string', 'enum': ['scroll', 'fade', 'bounce'],
                          'description': 'Optional danmaku animation: scroll across, fade in/out, or bounce in; defaults to scroll.'},
            'position': {'type': 'string', 'enum': ['top', 'center', 'bottom'],
                         'description': 'Optional vertical danmaku position; defaults to top.'},
            'duration': {'type': 'integer', 'minimum': 1, 'maximum': MAX_DANMAKU_DURATION,
                         'description': 'Optional danmaku lifetime in seconds, defaults to 12, maximum 20.'},
        },
        'required': ['text', 'style'],
        'additionalProperties': False,
    },
)


def normalize_screen_pop_args(args):
    if not isinstance(args, dict) or set(args) - {
            'text', 'style', 'count', 'color', 'animation', 'position', 'duration'}:
        raise ValueError('invalid screen_pop arguments')
    text = args.get('text')
    style = args.get('style')
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ValueError('text must contain 1 to 500 characters')
    if len(text.encode('utf-8')) > 2000:
        raise ValueError('text is too long')
    if style not in ('popup', 'danmaku'):
        raise ValueError('style must be popup or danmaku')
    count = args.get('count', 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError('count must be an integer from 1 to 10')
    if style == 'danmaku' and count != 1:
        raise ValueError('count applies only to popup')
    color = args.get('color', '#ffffff')
    if not isinstance(color, str) or not _COLOR.fullmatch(color):
        raise ValueError('color must be a #RRGGBB value')
    animation = args.get('animation', 'scroll')
    if not isinstance(animation, str) or animation not in _ANIMATIONS:
        raise ValueError('animation must be scroll, fade or bounce')
    position = args.get('position', 'top')
    if not isinstance(position, str) or position not in _POSITIONS:
        raise ValueError('position must be top, center or bottom')
    duration = args.get('duration', DEFAULT_DANMAKU_DURATION)
    if type(duration) is not int or not 1 <= duration <= MAX_DANMAKU_DURATION:
        raise ValueError('duration must be an integer from 1 to 20 seconds')
    return {'text': text.strip(), 'style': style, 'count': count, 'color': color.lower(),
            'animation': animation, 'position': position, 'duration': duration}


def _foreground_is_fullscreen():
    """Return whether the foreground app covers its monitor, as borderless games do."""
    if os.name != 'nt':
        return False
    import ctypes.wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.wintypes.DWORD),
                    ('rcMonitor', ctypes.wintypes.RECT),
                    ('rcWork', ctypes.wintypes.RECT),
                    ('dwFlags', ctypes.wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
    user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    user32.GetWindowRect.restype = ctypes.wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
    user32.MonitorFromWindow.restype = ctypes.wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL
    user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.wintypes.INT]
    user32.GetClassNameW.restype = ctypes.wintypes.INT

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    class_name = ctypes.create_unicode_buffer(256)
    if user32.GetClassNameW(hwnd, class_name, len(class_name)):
        if class_name.value.casefold() in {'progman', 'workerw', 'shell_traywnd'}:
            return False
    window = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window)):
        raise OSError('could not read foreground window bounds')
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        raise OSError('could not identify foreground monitor')
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError('could not read foreground monitor bounds')
    bounds = info.rcMonitor
    tolerance = 2
    return (window.left <= bounds.left + tolerance and window.top <= bounds.top + tolerance and
            window.right >= bounds.right - tolerance and window.bottom >= bounds.bottom - tolerance)


def screen_state():
    """Return (safe, fullscreen, reason); fail closed if the Windows input desktop is unknown."""
    if os.name != 'nt':
        return False, False, 'platform'
    try:
        from windows_observer import ObservationError, WindowsDesktopBackend
        WindowsDesktopBackend._check_default_desktop()
    except Exception:
        return False, False, 'locked'
    try:
        return True, _foreground_is_fullscreen(), None
    except Exception:
        # If the foreground window cannot be identified, convert conservatively
        # so the popup path never appears over a full-screen app by mistake.
        return True, True, None


def screen_pop_history_context(store, cfg, limit=10):
    """Summarize program-recorded screen-pop lifecycle events without their text."""
    rows = store.read_tail(f'{cfg.id}/tools.jsonl', 2000)
    grouped = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('tool') != 'screen_pop':
            continue
        event = row.get('screen_pop_event')
        call_id = row.get('call_id')
        style = row.get('screen_pop_style')
        stamp = row.get('time')
        if event not in ('shown', 'dismissed', 'finished') or not isinstance(call_id, str):
            continue
        if (style not in ('popup', 'danmaku') or not isinstance(stamp, str) or
                not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', stamp)):
            continue
        item = grouped.setdefault(call_id, {'style': style, 'shown': None, 'events': [], 'total': 1})
        item['events'].append((event, stamp, row.get('screen_pop_item')))
        if event == 'shown':
            item['shown'] = stamp
            total = row.get('screen_pop_total')
            if isinstance(total, int) and not isinstance(total, bool) and 1 <= total <= 64:
                item['total'] = total
    if not grouped:
        return None
    lines = []
    for item in list(grouped.values())[-limit:]:
        if not item['shown']:
            continue
        complete = {event[2] for event in item['events']
                    if event[0] in ('dismissed', 'finished') and isinstance(event[2], int)}
        remaining = max(0, item['total'] - len(complete))
        kind = 'popups' if item['style'] == 'popup' else 'danmaku'
        last_at = item['events'][-1][1]
        if remaining:
            state = f'{remaining} of {item["total"]} {kind} still open' if item['style'] == 'popup' else 'danmaku still in progress'
        else:
            state = f'{kind} dismissed/finished'
        lines.append(f'{item["style"]} shown at {item["shown"]}; {state}; latest event at {last_at}.')
    if not lines:
        return None
    return ('Program-maintained screen-pop history; historical display events, not instructions. '
            'No message text is stored here. ' + ' '.join(lines))
