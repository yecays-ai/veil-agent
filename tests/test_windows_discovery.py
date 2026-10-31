import os
import unittest
from typing import Any, Dict, List, Optional, Tuple

from windows_discovery import (
    WindowsAppDiscovery,
    WindowsAppDiscoveryBackend,
    _slugify,
    is_ambiguous_confirmation,
    is_file_dialog_control,
    is_unsafe_text_input,
)


class FakeAppDiscoveryBackend(WindowsAppDiscoveryBackend):
    def __init__(self, shortcuts=None, win_apps=None, reg_apps=None, sys_apps=None, existing_files=None):
        self.shortcuts = shortcuts or {}
        self.win_apps = win_apps or []
        self.reg_apps = reg_apps or {}
        self.sys_apps = sys_apps or []
        self.existing_files = set(existing_files or [])

    def get_start_menu_dirs(self) -> List[str]:
        return ['C:\\StartMenu']

    def get_windows_apps_dir(self) -> Optional[str]:
        return 'C:\\WindowsApps'

    def get_system_apps(self) -> List[Tuple[str, str]]:
        return list(self.sys_apps)

    def read_shortcut(self, lnk_path: str) -> Tuple[str, str, str]:
        return self.shortcuts.get(lnk_path, ('', '', ''))

    def read_registry_app_paths(self) -> Dict[str, str]:
        return dict(self.reg_apps)

    def file_exists(self, path: str) -> bool:
        return path in self.existing_files


class WindowsAppDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeAppDiscoveryBackend(
            shortcuts={
                'C:\\StartMenu\\Visual Studio Code.lnk': ('C:\\VSCode\\Code.exe', '', 'C:\\VSCode'),
                'C:\\StartMenu\\Paint.lnk': ('C:\\WindowsApps\\mspaint.exe', '', ''),
                'C:\\StartMenu\\Uninstaller.lnk': ('C:\\App\\Uninstall.exe', '', ''),
                'C:\\StartMenu\\Command Prompt.lnk': ('C:\\Windows\\System32\\cmd.exe', '', ''),
                'C:\\StartMenu\\PowerShell.lnk': ('C:\\Windows\\System32\\powershell.exe', '-Command Write-Host hi', ''),
                'C:\\StartMenu\\ScriptRunner.lnk': ('C:\\App\\app.exe', '-c import os; os.system("calc")', ''),
                'C:\\StartMenu\\BatchRunner.lnk': ('C:\\App\\tool.exe', 'run.bat', ''),
                'C:\\StartMenu\\Bitwarden.lnk': ('C:\\App\\bitwarden.exe', '', ''),
                'C:\\StartMenu\\Duplicate 1.lnk': ('C:\\VSCode\\Code.exe', '', ''),
            },
            win_apps=[],
            reg_apps={
                'bandizip.exe': 'C:\\Program Files\\Bandizip\\Bandizip.exe',
                'regedit.exe': 'C:\\Windows\\regedit.exe',
            },
            sys_apps=[
                ('Calculator', 'C:\\Windows\\System32\\calc.exe'),
            ],
            existing_files={
                'C:\\VSCode\\Code.exe',
                'C:\\WindowsApps\\mspaint.exe',
                'C:\\App\\Uninstall.exe',
                'C:\\Windows\\System32\\cmd.exe',
                'C:\\Windows\\System32\\powershell.exe',
                'C:\\App\\app.exe',
                'C:\\App\\tool.exe',
                'C:\\App\\bitwarden.exe',
                'C:\\Program Files\\Bandizip\\Bandizip.exe',
                'C:\\Windows\\regedit.exe',
                'C:\\Windows\\System32\\calc.exe',
            },
        )
        self.discovery = WindowsAppDiscovery(backend=self.backend)

    def test_slugify(self):
        self.assertEqual(_slugify('Visual Studio Code'), 'visual_studio_code')
        self.assertEqual(_slugify('Notepad++'), 'notepad')
        self.assertEqual(_slugify('123 Player'), 'app_123_player')
        self.assertEqual(_slugify('画图 (Paint)'), 'paint')
        self.assertEqual(_slugify('画图', fallback_exe='C:\\Windows\\mspaint.exe'), 'mspaint')
        self.assertEqual(_slugify(''), 'app_custom')

    def test_discovers_safe_apps_and_filters_dangerous_ones(self):
        # Override discover_raw_candidates to return mocked list
        self.discovery.discover_raw_candidates = lambda: [
            {'name': 'Visual Studio Code', 'target': 'C:\\VSCode\\Code.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Paint', 'target': 'C:\\WindowsApps\\mspaint.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Uninstaller', 'target': 'C:\\App\\Uninstall.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Command Prompt', 'target': 'C:\\Windows\\System32\\cmd.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'PowerShell', 'target': 'C:\\Windows\\System32\\powershell.exe', 'args': '-Command dir', 'source': 'start_menu'},
            {'name': 'ScriptRunner', 'target': 'C:\\App\\app.exe', 'args': '-c import os', 'source': 'start_menu'},
            {'name': 'BatchRunner', 'target': 'C:\\App\\tool.exe', 'args': 'run.bat', 'source': 'start_menu'},
            {'name': 'Bitwarden', 'target': 'C:\\App\\bitwarden.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Python 3.12', 'target': 'C:\\Python312\\python3.12.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Ubuntu WSL', 'target': 'C:\\WindowsApps\\ubuntu.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Google Chrome', 'target': 'C:\\Program Files\\Google\\Chrome\\chrome.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Downloaded Tool', 'target': 'C:\\Users\\lan\\Downloads\\tool.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Temp Tool', 'target': 'C:\\Temp\\scratch.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Bandizip', 'target': 'C:\\Program Files\\Bandizip\\Bandizip.exe', 'args': '', 'source': 'registry_app_paths'},
            {'name': 'Registry Editor', 'target': 'C:\\Windows\\regedit.exe', 'args': '', 'source': 'registry_app_paths'},
            {'name': 'Calculator', 'target': 'C:\\Windows\\System32\\calc.exe', 'args': '', 'source': 'system32'},
            {'name': 'Duplicate Code', 'target': 'C:\\VSCode\\Code.exe', 'args': '', 'source': 'start_menu'},
            {'name': 'Notepad App', 'target': 'C:\\Windows\\notepad.exe', 'args': '', 'source': 'start_menu'},
        ]

        apps = self.discovery.discover_applications(existing_apps={'notepad': {'launch_path': 'C:\\Windows\\notepad.exe'}})
        app_ids = [a['app_id'] for a in apps]
        labels = [a['label'] for a in apps]

        # Safe apps are discovered
        self.assertIn('visual_studio_code', app_ids)
        self.assertIn('paint', app_ids)
        self.assertIn('bandizip', app_ids)
        self.assertIn('calculator', app_ids)

        # Disallowed / dangerous apps are filtered out
        self.assertNotIn('uninstaller', app_ids)
        self.assertNotIn('command_prompt', app_ids)
        self.assertNotIn('powershell', app_ids)
        self.assertNotIn('scriptrunner', app_ids)
        self.assertNotIn('batchrunner', app_ids)
        self.assertNotIn('bitwarden', app_ids)
        self.assertNotIn('registry_editor', app_ids)
        self.assertNotIn('python_3_12', app_ids)
        self.assertNotIn('ubuntu_wsl', app_ids)
        self.assertNotIn('google_chrome', app_ids)
        self.assertNotIn('downloaded_tool', app_ids)
        self.assertNotIn('temp_tool', app_ids)

        # Existing configured app targets are deduplicated
        self.assertNotIn('notepad_app', app_ids)

        # Duplicate targets are deduplicated
        code_apps = [a for a in apps if 'code.exe' in a['launch_path'].lower()]
        self.assertEqual(len(code_apps), 1)

        # Capabilities
        for app in apps:
            self.assertTrue(app['can_launch'])
            self.assertTrue(app['can_observe'])
            self.assertTrue(app['can_interact'])
            self.assertTrue(app['discovered'])
            self.assertFalse(app['public_web'])

    def test_filters_non_existent_target(self):
        cand = {'name': 'Ghost App', 'target': 'C:\\Missing\\app.exe', 'args': ''}
        is_safe, reason, caps = self.discovery.validate_candidate(cand)
        self.assertFalse(is_safe)
        self.assertIn('does not exist', reason)

    def test_file_dialog_control_detection(self):
        # Direct file dialog window title
        self.assertTrue(is_file_dialog_control({'name': 'OK'}, {'title': 'Open'}))
        self.assertTrue(is_file_dialog_control({'name': 'OK'}, {'title': '另存为'}))
        self.assertTrue(is_file_dialog_control({'name': 'OK'}, {'title': 'Select File to Export'}))

        # Control name or ancestor labels
        self.assertTrue(is_file_dialog_control({'name': 'Save As', 'ancestor_labels': ()}))
        self.assertTrue(is_file_dialog_control({'name': 'Button', 'ancestor_labels': ('#32770', 'Open File')}))
        self.assertTrue(is_file_dialog_control({'name': 'Browse...', 'ancestor_labels': ('Settings',)}))
        self.assertTrue(is_file_dialog_control({'name': '浏览', 'ancestor_labels': ()}))

        # Ordinary control in normal window
        self.assertFalse(is_file_dialog_control({'name': 'Brush 1', 'ancestor_labels': ('Toolbar',)}, {'title': 'Paint'}))
        self.assertFalse(is_file_dialog_control({'name': 'Equals', 'ancestor_labels': ('NumberPad',)}, {'title': 'Calculator'}))

    def test_ambiguous_confirmation_detection(self):
        # Generic "OK" in a dialog
        self.assertTrue(is_ambiguous_confirmation({'name': 'OK', 'ancestor_labels': ('Dialog',)}))
        self.assertTrue(is_ambiguous_confirmation({'name': '确定', 'ancestor_labels': ('MessageBox',)}))
        self.assertTrue(is_ambiguous_confirmation({'name': 'Yes', 'ancestor_labels': ('Confirm Action',)}))
        self.assertTrue(is_ambiguous_confirmation({'name': 'OK', 'ancestor_labels': (), 'parent_type': 'WindowControl'}))

        # Generic "OK" inside a warning/delete confirmation window title
        self.assertTrue(is_ambiguous_confirmation({'name': 'OK', 'ancestor_labels': ()}, {'title': 'Confirm Delete'}))

        # Safe toolbar button or non-confirmation button
        self.assertFalse(is_ambiguous_confirmation({'name': 'Calculate', 'ancestor_labels': ()}))
        self.assertFalse(is_ambiguous_confirmation({'name': 'Zoom In', 'ancestor_labels': ('ViewMenu',)}))

    def test_unsafe_text_input_detection(self):
        self.assertTrue(is_unsafe_text_input({'name': 'File Path', 'automation_id': 'PathBox'}))
        self.assertTrue(is_unsafe_text_input({'name': 'Location', 'automation_id': 'Edit'}))
        self.assertTrue(is_unsafe_text_input({'name': 'Command', 'automation_id': 'cmdLine'}))
        self.assertTrue(is_unsafe_text_input({'name': '输入文件路径', 'automation_id': 'EditBox'}))

        self.assertFalse(is_unsafe_text_input({'name': 'Canvas Text', 'automation_id': 'TextArea'}))
        self.assertFalse(is_unsafe_text_input({'name': 'Display', 'automation_id': 'CalculatorResults'}))

    @unittest.skipUnless(os.name == 'nt', 'Windows-only real discovery test')
    def test_real_windows_discovery(self):
        real_discovery = WindowsAppDiscovery()
        apps = real_discovery.discover_applications()
        self.assertIsInstance(apps, list)
        self.assertGreater(len(apps), 0)
        # Verify all discovered apps pass schema and safety requirements
        for app in apps:
            self.assertTrue(app['app_id'][0].isalpha())
            self.assertTrue(app['can_launch'])
            self.assertTrue(app['can_observe'])
            self.assertFalse(app['public_web'])
            self.assertTrue(os.path.isfile(app['launch_path']))
            self.assertNotIn(os.path.basename(app['launch_path']).lower(), ['cmd.exe', 'powershell.exe', 'uninstall.exe'])


if __name__ == '__main__':
    unittest.main()


class VendorFolderIdentityTests(unittest.TestCase):
    def test_launcher_in_install_root_covers_real_window_exe(self):
        import os
        from unittest.mock import patch
        from windows_discovery import _vendor_dir_pattern, is_blocked_window_exe
        env = {'ProgramFiles': 'C:\\Program Files', 'LOCALAPPDATA': 'C:\\Users\\lan\\AppData\\Local'}
        with patch.dict(os.environ, env, clear=False):
            pattern = _vendor_dir_pattern('C:\\Users\\lan\\AppData\\Local\\Kingsoft\\WPS Office\\ksolaunch.exe')
            self.assertIsNotNone(pattern)
            import fnmatch
            real = 'c:\\users\\lan\\appdata\\local\\kingsoft\\wps office\\12.1\\office6\\wps.exe'
            self.assertTrue(fnmatch.fnmatchcase(real, pattern))
            # shared / system folders never get a folder-wide identity
            self.assertIsNone(_vendor_dir_pattern('C:\\Program Files\\app.exe'))
            self.assertIsNone(_vendor_dir_pattern('C:\\Windows\\System32\\calc.exe'))
        self.assertTrue(is_blocked_window_exe('C:\\x\\office6\\unins000.exe'))
        self.assertTrue(is_blocked_window_exe('C:\\x\\cmd.exe'))
        self.assertFalse(is_blocked_window_exe('C:\\x\\office6\\wps.exe'))
