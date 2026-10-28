import unittest
from types import SimpleNamespace

from tools_agent import tool_policy
from windows_media import WINDOWS_MEDIA_TOOL_SCHEMA, WindowsMediaTools, _current_session_data


class FakeSession:
    def __init__(self, state='PLAYING', title='Song', artist='Artist'):
        self.state = state
        self.metadata = SimpleNamespace(title=title, artist=artist) if title or artist else None

    def get_playback_info(self):
        return SimpleNamespace(playback_status=SimpleNamespace(name=self.state))

    async def try_get_media_properties_async(self):
        return self.metadata


class FakeManager:
    session = None

    @classmethod
    async def request_async(cls):
        return cls

    @classmethod
    def get_current_session(cls):
        return cls.session


class WindowsMediaTests(unittest.TestCase):
    @staticmethod
    def read_immediate(manager_type):
        coroutine = _current_session_data(manager_type)
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        raise AssertionError('The fake WinRT operation unexpectedly suspended.')

    def test_reads_current_title_artist_and_state(self):
        FakeManager.session = FakeSession()
        self.assertEqual(self.read_immediate(FakeManager), {
            'state': 'playing', 'title': 'Song', 'artist': 'Artist'})
        tools = WindowsMediaTools(True, platform_enabled=True, reader=lambda: self.read_immediate(FakeManager))
        result, state, _ = tools.invoke('windows_now_playing', {})
        self.assertEqual(state, '成功')
        self.assertIn('Title: Song', result)
        self.assertIn('Artist: Artist', result)
        self.assertIn('State: playing', result)

    def test_reports_paused_and_missing_metadata_without_guessing(self):
        FakeManager.session = FakeSession('PAUSED', '', '')
        self.assertEqual(self.read_immediate(FakeManager), {'state': 'paused'})
        tools = WindowsMediaTools(True, platform_enabled=True, reader=lambda: {'state': 'paused'})
        result, state, _ = tools.invoke('windows_now_playing', {})
        self.assertEqual(state, '成功')
        self.assertIn('state: paused', result)
        self.assertIn('did not provide', result)

    def test_no_session_and_stopped_session_are_distinct(self):
        FakeManager.session = None
        self.assertEqual(self.read_immediate(FakeManager), {'state': 'none'})
        FakeManager.session = FakeSession('STOPPED', 'Old song', 'Old artist')
        self.assertEqual(self.read_immediate(FakeManager), {'state': 'stopped'})

    def test_failure_keeps_tool_loop_safe_and_disabled_permission_hides_tool(self):
        tools = WindowsMediaTools(True, platform_enabled=True, reader=lambda: (_ for _ in ()).throw(RuntimeError()))
        result, state, _ = tools.invoke('windows_now_playing', {})
        self.assertEqual(state, '出错')
        self.assertIn('no song was inferred', result)
        disabled = WindowsMediaTools(False, platform_enabled=True)
        self.assertEqual(disabled.tools, [])
        self.assertEqual(disabled.invoke('windows_now_playing', {})[1], '被拒绝')
        self.assertEqual(tool_policy('windows_now_playing'), 'desktop_read')
        self.assertEqual(WINDOWS_MEDIA_TOOL_SCHEMA.inputSchema['additionalProperties'], False)


if __name__ == '__main__':
    unittest.main()
