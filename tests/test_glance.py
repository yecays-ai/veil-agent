import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from autonomy import GlanceController


def controller(results, root):
    windows = SimpleNamespace(invoke=Mock(side_effect=results))
    chat = SimpleNamespace(cfg=SimpleNamespace(id='peter', glance={'enabled': True}),
                           store=SimpleNamespace(root=Path(root)),
                           tools=SimpleNamespace(files=SimpleNamespace(windows_tools=windows)))
    hub = SimpleNamespace(activity=SimpleNamespace(enabled=lambda: True))
    return GlanceController(chat, hub, clock=object())


class GlanceObservationTests(unittest.TestCase):
    def test_no_identifiable_foreground_still_gives_peter_a_glance(self):
        # Lan on the bare desktop with only VEIL open used to end the glance as 失败.
        with tempfile.TemporaryDirectory() as root:
            glance = controller([('The foreground window is protected or could not be identified safely.',
                                  '出错', 'windows_foreground')], root)
            metadata, observed, error = glance._observe()
        self.assertIsNone(error)
        self.assertEqual(metadata['application'], 'no ordinary window identified')
        self.assertIn('nothing on screen was read', observed)

    def test_unreadable_window_keeps_application_and_title(self):
        header = 'ok\n' + json.dumps({'window_id': 'w1', 'application': 'Steam', 'title': 'Library'})
        with tempfile.TemporaryDirectory() as root:
            glance = controller([(header, '成功', 'Steam'),
                                 ('UI Automation could not read this window.', '出错', 'Steam')], root)
            metadata, observed, error = glance._observe()
        self.assertIsNone(error)
        self.assertEqual(metadata['title'], 'Library')
        self.assertIn('Steam', observed)


if __name__ == '__main__':
    unittest.main()
