import unittest
from types import SimpleNamespace
from unittest.mock import patch

import workbench_switch


class ImageReadyTests(unittest.TestCase):
    @patch('workbench_switch.subprocess.run')
    def test_custom_image_is_never_treated_as_the_default_image(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout='')
        self.assertTrue(workbench_switch.image_ready('private/workbench:1'))
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['docker', 'image', 'inspect'])
        self.assertNotIn('--format', command)

    @patch('workbench_switch.subprocess.run')
    def test_default_image_requires_the_network_lock_label(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout='None\n')
        self.assertFalse(workbench_switch.image_ready())
        run.return_value = SimpleNamespace(returncode=0, stdout='1\n')
        self.assertTrue(workbench_switch.image_ready())
