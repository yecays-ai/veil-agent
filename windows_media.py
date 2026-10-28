"""Read-only access to the current Windows media session metadata."""
import asyncio
import re
from types import SimpleNamespace


WINDOWS_MEDIA_TOOL_NAMES = frozenset({'windows_now_playing'})
MEDIA_SESSION_TIMEOUT_SECONDS = 5
MAX_METADATA_CHARS = 240

WINDOWS_MEDIA_TOOL_SCHEMA = SimpleNamespace(
    name='windows_now_playing',
    description=(
        'Read the current Windows media session title, artist and playback state (playing, paused, stopped, or no '
        'available information). Read-only: this tool cannot play, pause, skip, change volume, or access audio.'
    ),
    inputSchema={'type': 'object', 'properties': {}, 'additionalProperties': False},
)


def _plain(value):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    value = ' '.join(value.split())
    return value[:MAX_METADATA_CHARS]


async def _current_session_data(manager_type):
    manager = await manager_type.request_async()
    session = manager.get_current_session()
    if session is None:
        return {'state': 'none'}

    playback_info = session.get_playback_info()
    playback_status = getattr(playback_info, 'playback_status', None)
    status = getattr(playback_status, 'name', str(playback_status)).upper()
    state = {
        'PLAYING': 'playing',
        'PAUSED': 'paused',
        'STOPPED': 'stopped',
        'CLOSED': 'stopped',
        'OPENED': 'unknown',
        'CHANGING': 'unknown',
    }.get(status, 'unknown')
    if state in {'none', 'stopped'}:
        return {'state': state}

    metadata = await session.try_get_media_properties_async()
    title = _plain(getattr(metadata, 'title', '')) if metadata is not None else ''
    artist = _plain(getattr(metadata, 'artist', '')) if metadata is not None else ''
    result = {'state': state}
    if title:
        result['title'] = title
    if artist:
        result['artist'] = artist
    return result


def read_current_session(*, manager_type=None, timeout=MEDIA_SESSION_TIMEOUT_SECONDS):
    """Query system supplied metadata. Deliberately imports WinRT only on Windows use."""
    if manager_type is None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
        manager_type = GlobalSystemMediaTransportControlsSessionManager
    return asyncio.run(asyncio.wait_for(_current_session_data(manager_type), timeout=timeout))


class WindowsMediaTools:
    def __init__(self, enabled, *, platform_enabled=None, reader=read_current_session):
        import os
        self.enabled = enabled is True and (os.name == 'nt' if platform_enabled is None else bool(platform_enabled))
        self.reader = reader
        self.tools = [WINDOWS_MEDIA_TOOL_SCHEMA] if self.enabled else []

    def invoke(self, name, args):
        if not self.enabled or name not in WINDOWS_MEDIA_TOOL_NAMES or name not in {tool.name for tool in self.tools}:
            return 'Windows music status is disabled for this character or platform.', '被拒绝', name
        if not isinstance(args, dict) or args:
            return 'This read-only status tool takes no arguments.', '被拒绝', name
        try:
            data = self.reader()
        except ImportError:
            return 'Windows media-session support is not installed; no song or playback state was read.', '出错', name
        except Exception:
            return 'Windows did not provide readable media-session information; no song was inferred.', '出错', name
        if not isinstance(data, dict):
            return 'Windows did not provide readable media-session information; no song was inferred.', '出错', name
        state = data.get('state')
        if state == 'none':
            return ('There is no current Windows media session. Make sure the player exposes Windows SMTC; '
                    'in NetEase Cloud Music, enable SMTC in Settings > System and restart the player.'), '成功', name
        if state == 'stopped':
            return 'The current Windows media session is stopped.', '成功', name
        if state not in {'playing', 'paused', 'unknown'}:
            return 'Windows did not provide a recognized media-session state.', '成功', name
        title = _plain(data.get('title'))
        artist = _plain(data.get('artist'))
        if not title and not artist:
            return (f'Playback state: {state}. Windows did not provide a song title or artist. Make sure the '
                    'player exposes Windows SMTC; in NetEase Cloud Music, enable SMTC in Settings > System.'), '成功', name
        fields = []
        if title:
            fields.append(f'Title: {title}')
        if artist:
            fields.append(f'Artist: {artist}')
        fields.append(f'State: {state}')
        return '\n'.join(fields), '成功', name
