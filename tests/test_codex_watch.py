import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from codex_watch import CodexWatchController


class CodexWatchAnnouncementTests(unittest.TestCase):
    def test_notice_reaches_tools_wrapped_with_long_term_memory(self):
        # With memory on, chat.tools is a ToolSet; the announcer lives on ToolSet.files.
        files = SimpleNamespace(announce_codex_watch=Mock(return_value=True))
        tools = SimpleNamespace(files=files, capability_event='notice', capability_event_model=None)
        with tempfile.TemporaryDirectory() as root:
            chat = SimpleNamespace(cfg=SimpleNamespace(id='peter'), store=SimpleNamespace(root=Path(root)),
                                   tools=tools, pending_capability_event=None,
                                   pending_capability_event_model=None)
            CodexWatchController(chat, SimpleNamespace())
        files.announce_codex_watch.assert_called_once_with()
        self.assertEqual(chat.pending_capability_event, 'notice')


if __name__ == '__main__':
    unittest.main()
