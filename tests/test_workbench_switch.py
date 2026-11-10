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
        run.return_value = SimpleNamespace(returncode=0, stdout='None/None\n')
        self.assertFalse(workbench_switch.image_ready())
        run.return_value = SimpleNamespace(returncode=0, stdout='1/None\n')  # built before the install venv
        self.assertFalse(workbench_switch.image_ready())
        run.return_value = SimpleNamespace(returncode=0, stdout='1/1\n')
        self.assertTrue(workbench_switch.image_ready())


class WebHostTests(unittest.TestCase):
    def test_hosts_can_be_added_removed_and_survive_a_save(self):
        import tempfile
        state = workbench_switch.load_state(tempfile.gettempdir() + '/none-here')
        self.assertEqual(workbench_switch.add_host(state, 'https://GitHub.com/a/b?x=1'), (True, '已加入：github.com'))
        self.assertFalse(workbench_switch.add_host(state, 'github.com')[0])
        self.assertFalse(workbench_switch.add_host(state, 'not a host')[0])
        self.assertFalse(workbench_switch.add_host(state, 'localhost')[0])
        with tempfile.TemporaryDirectory() as directory:
            workbench_switch.save_state(state, directory)
            self.assertEqual(workbench_switch.load_state(directory)['web_hosts'], ['github.com'])
            self.assertTrue(workbench_switch.remove_host(state, 'github.com'))
            self.assertFalse(workbench_switch.remove_host(state, 'github.com'))
