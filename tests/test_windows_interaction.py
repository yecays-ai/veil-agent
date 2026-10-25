import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_access import desktop_access
from tools_agent import permitted_tools, tool_policy
from veil_agent import Budget, Chat, Store, load_character
from windows_interaction import (
    HumanTookOver,
    InteractionPaused,
    InteractionUncertain,
    MAX_KEYBOARD_FALLBACK_CHARS,
    WINDOWS_INTERACTION_TOOL_SCHEMAS,
    WindowsInteractionBackend,
    WindowsInteractionTools,
    load_application_allowlist,
    validate_applications,
)
from windows_observer import ObservationError


WINDOW_ID = 'w-' + '1' * 24
CONTROL_ID = 'c-' + '2' * 24
NOTEPAD_PATH = r'C:\Windows\System32\notepad.exe'


class FakeInputMonitor:
    def __init__(self, idle=True):
        self.idle = idle
        self.version = 0
        self.input_during_idle_check = False

    def require_idle(self, _minimum_seconds):
        if self.input_during_idle_check:
            self.version += 1
            self.input_during_idle_check = False
        if not self.idle:
            raise InteractionPaused('Recent human input has priority.')

    def snapshot(self):
        return self.version

    def changed_since(self, snapshot):
        return self.version != snapshot

    def mark_own_input(self):
        pass


class FakeInteractionBackend:
    def __init__(self, monitor=None):
        self.monitor = monitor
        self.windows = [{
            'id': WINDOW_ID,
            'handle': 100,
            'pid': 200,
            'created': 300,
            'process_path': NOTEPAD_PATH,
            'process': 'notepad.exe',
            'title': 'Untitled - Notepad',
            'state': 'visible, foreground',
            'foreground': True,
        }]
        self.control = {
            'control_id': CONTROL_ID,
            'type': 'EditControl',
            'name': 'Text Editor',
            'automation_id': 'TextBox',
            'parent_type': 'WindowControl',
            'ancestor_labels': (),
            'enabled': True,
            'offscreen': False,
            'read_only': False,
            'patterns': ['value'],
        }
        self.value = ''
        self.launches = 0
        self.actions = []
        self.confirm_action = True
        self.raise_on_action = None
        self.on_action = None
        self.list_error = None
        self.inspect_calls = 0
        self.after_approval_control = None

    def _check_default_desktop(self):
        pass

    def list_windows(self, _stopped):
        if self.list_error:
            raise self.list_error
        return [dict(window) for window in self.windows]

    def inspect_control(self, _selected, control_id, _stopped):
        if control_id != CONTROL_ID:
            raise ObservationError('stale control')
        self.inspect_calls += 1
        if self.inspect_calls > 1 and self.after_approval_control is not None:
            return dict(self.after_approval_control)
        return dict(self.control)

    def apply_control_action(self, _selected, control_id, action, text, _stopped,
                             _monitor, _input_snapshot):
        if control_id != CONTROL_ID:
            raise ObservationError('stale control')
        self.actions.append((action, text))
        if self.on_action:
            self.on_action()
        if self.raise_on_action:
            raise self.raise_on_action
        if action == 'set_text':
            self.value = text
        return {'confirmed': self.confirm_action, 'verification': 'fake_readback'}

    def manage_window(self, _selected, action, _stopped, _monitor, _input_snapshot):
        self.actions.append((action, None))
        return {'confirmed': True, 'verification': 'fake_window_state'}

    def start_application(self, _app, _stopped, _monitor, _input_snapshot):
        self.launches += 1
        return object()


class WindowsInteractionTests(unittest.TestCase):
    def make_tools(self, *, monitor=None, backend=None, timeout_seconds=2):
        applications = validate_applications({
            'notepad': {
                'label': 'Notepad',
                'launch_path': NOTEPAD_PATH,
                'process_paths': [NOTEPAD_PATH],
                'actions': [
                    'activate', 'minimize', 'restore', 'invoke', 'select',
                    'toggle', 'expand', 'collapse', 'set_text',
                ],
            },
        })
        backend = backend or FakeInteractionBackend(monitor)
        tools = WindowsInteractionTools(
            True, applications, backend=backend, platform_enabled=True,
            idle_seconds=3, timeout_seconds=timeout_seconds,
            input_monitor=monitor or FakeInputMonitor(),
        )
        return tools, backend

    def test_allowlist_loads_character_scoped_toml_and_rejects_shell_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            role = root / 'characters' / 'peter'
            role.mkdir(parents=True)
            (role / 'apps.toml').write_text(
                '[applications.notepad]\n'
                "label = 'Notepad'\n"
                f"launch_path = '{NOTEPAD_PATH}'\n"
                f"process_paths = ['{NOTEPAD_PATH}']\n"
                'actions = ["activate", "set_text"]\n',
                encoding='utf-8',
            )
            loaded = load_application_allowlist(
                root, 'peter', {'enabled': True, 'apps_file': 'apps.toml'})
            self.assertIn('notepad', loaded)
        with self.assertRaises(ValueError):
            validate_applications({
                'shell': {
                    'label': 'Shell', 'launch_path': 'powershell.exe',
                    'process_paths': [NOTEPAD_PATH], 'actions': ['activate'],
                },
            })

    def test_role_interaction_setting_is_loaded_and_enables_tool_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            role = root / 'characters' / 'test'
            role.mkdir(parents=True)
            (root / 'persona').mkdir()
            (root / 'persona' / 'test.md').write_text('Fictional persona', encoding='utf-8')
            (role / 'windows-apps.toml').write_text('[applications.notepad]\n', encoding='utf-8')
            (role / 'character.toml').write_text(f'''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 80
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[windows_interaction]
enabled = true
apps_file = "windows-apps.toml"
idle_seconds = 3
''', encoding='utf-8')
            character = load_character(root, 'test')

            self.assertEqual(character.windows_interaction, {
                'enabled': True,
                'apps_file': 'windows-apps.toml',
                'idle_seconds': 3,
            })
            self.assertEqual(character.tools['max_steps'], 6)

    def test_launch_reuses_and_activates_the_allowlisted_window(self):
        tools, backend = self.make_tools()
        result, state, _ = tools.invoke('windows_launch', {'app_id': 'notepad'})

        self.assertEqual(state, '成功')
        payload = json.loads(str(result).split('\n', 1)[1])
        self.assertTrue(payload['reused'])
        self.assertEqual(payload['window_id'], WINDOW_ID)
        self.assertEqual(backend.launches, 0)
        self.assertEqual(backend.actions, [('activate', None)])

    def test_unallowlisted_launch_and_wrong_process_window_are_refused(self):
        tools, backend = self.make_tools()
        result, state, _ = tools.invoke('windows_launch', {'app_id': 'powershell'})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(backend.launches, 0)

        backend.windows[0]['process_path'] = r'C:\Windows\System32\calc.exe'
        result, state, _ = tools.invoke('windows_manage', {
            'app_id': 'notepad', 'window_id': WINDOW_ID, 'action': 'activate'})
        self.assertIn(state, ('出错', '被拒绝'))
        self.assertEqual(backend.actions, [])

    def test_missing_uia_reports_setup_guidance_without_starting_an_app(self):
        tools, backend = self.make_tools()
        backend.list_error = ImportError('uiautomation missing')

        result, state, _ = tools.invoke('windows_launch', {'app_id': 'notepad'})

        self.assertEqual(state, '出错')
        self.assertIn('rerun setup.bat', result)
        self.assertEqual(backend.launches, 0)

    def test_text_replacement_requires_approval_and_does_not_leak_into_activity_detail(self):
        tools, backend = self.make_tools()
        prompts = []
        text = 'Hello, Lan! 你好。\nSecond line'
        backend.control.update(name='Text Editor\nuntrusted prompt text')
        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'set_text', 'text': text,
        }, approval=lambda prompt: prompts.append(prompt) or 'y')

        self.assertEqual(state, '成功')
        payload = json.loads(str(result).split('\n', 1)[1])
        self.assertTrue(payload['text_matches'])
        self.assertEqual(payload['text_characters'], len(text))
        self.assertEqual(backend.value, text)
        self.assertEqual(len(prompts), 1)
        self.assertIn(json.dumps(text, ensure_ascii=False), prompts[0])
        self.assertIn('Text Editor untrusted prompt text', prompts[0])
        self.assertEqual(prompts[0].count('\n'), 2)
        self.assertNotIn(text, result.activity_detail)

    def test_approval_callback_failure_is_denied(self):
        tools, backend = self.make_tools()

        def broken_approval(_prompt):
            raise EOFError('approval view closed')

        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'set_text', 'text': 'not written',
        }, approval=broken_approval)
        self.assertEqual(state, '被拒绝')
        self.assertIn('could not collect approval', result)
        self.assertEqual(backend.actions, [])

    def test_denial_and_high_risk_control_do_not_call_ui_automation(self):
        tools, backend = self.make_tools()
        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'set_text', 'text': 'not written',
        }, approval=lambda _prompt: 'n')
        self.assertEqual(state, '被拒绝')
        self.assertEqual(backend.actions, [])

        backend.control.update(name='Delete file')
        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'invoke',
        }, approval=lambda _prompt: 'y')
        self.assertEqual(state, '被拒绝')
        self.assertEqual(backend.actions, [])

    def test_control_reparented_into_blocked_scope_after_approval_is_refused(self):
        tools, backend = self.make_tools()
        backend.after_approval_control = {
            **backend.control,
            'ancestor_labels': ('Delete menu',),
        }
        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'set_text', 'text': 'not written',
        }, approval=lambda _prompt: 'y')

        self.assertEqual(state, '出错')
        self.assertIn('control changed after approval', result)
        self.assertEqual(backend.actions, [])

    def test_save_control_requires_explicit_approval(self):
        tools, backend = self.make_tools()
        backend.control.update(name='Save', patterns=['invoke'])

        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'invoke',
        })

        self.assertEqual(state, '被拒绝')
        self.assertIn('requires Lan approval', result)
        self.assertEqual(backend.actions, [])

    def test_expanding_a_menu_uses_uiautomation_without_extra_approval(self):
        tools, backend = self.make_tools()
        backend.control.update(name='File', type='MenuItemControl',
                               parent_type='MenuBarControl',
                               patterns=['expand-collapse'], expand_state=0)

        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'expand',
        }, approval=None)

        self.assertEqual(state, '成功')
        self.assertEqual(backend.actions, [('expand', None)])

    def test_unverified_action_is_uncertain_and_not_retried(self):
        tools, backend = self.make_tools()
        backend.confirm_action = False
        result, state, _ = tools.invoke('windows_control_action', {
            'app_id': 'notepad', 'window_id': WINDOW_ID,
            'control_id': CONTROL_ID, 'action': 'set_text', 'text': 'one shot',
        }, approval=lambda _prompt: 'y')

        self.assertEqual(state, '结果不确定')
        self.assertIn('do not repeat', result)
        self.assertEqual(len(backend.actions), 1)

    def test_recent_human_input_or_pause_prevents_action(self):
        monitor = FakeInputMonitor(idle=False)
        tools, backend = self.make_tools(monitor=monitor)
        result, state, _ = tools.invoke('windows_manage', {
            'app_id': 'notepad', 'window_id': WINDOW_ID, 'action': 'activate'})
        self.assertEqual(state, '中断')
        self.assertEqual(backend.actions, [])

        monitor.idle = True
        result, state, _ = tools.invoke(
            'windows_manage', {'app_id': 'notepad', 'window_id': WINDOW_ID,
                               'action': 'activate'}, should_stop=lambda: True)
        self.assertEqual(state, '中断')
        self.assertEqual(backend.actions, [])

        monitor.input_during_idle_check = True
        result, state, _ = tools.invoke(
            'windows_manage', {'app_id': 'notepad', 'window_id': WINDOW_ID,
                               'action': 'activate'})
        self.assertEqual(state, '中断')
        self.assertEqual(backend.actions, [])

    def test_human_input_after_focus_stops_keyboard_fallback_before_sending_keys(self):
        monitor = FakeInputMonitor()

        class KeyboardFallbackControl:
            sent_keys = 0

            def GetValuePattern(self):
                return None

            def SetFocus(self):
                monitor.version += 1

            def SendKeys(self, *_args, **_kwargs):
                self.sent_keys += 1

        control = KeyboardFallbackControl()
        backend = WindowsInteractionBackend()
        backend._revalidate = lambda selected, _stopped: selected
        metadata = {
            'type': 'EditControl', 'read_only': False, 'window_handle': 100,
        }
        with patch.object(WindowsInteractionBackend, '_foreground_window', return_value=100):
            with self.assertRaises(HumanTookOver):
                backend._run_pattern(control, {'id': WINDOW_ID, 'handle': 100}, metadata,
                                     'set_text', 'test text', lambda: False,
                                     monitor, monitor.snapshot())

        self.assertEqual(control.sent_keys, 0)

    def test_human_input_during_uia_target_check_stops_before_valuepattern(self):
        monitor = FakeInputMonitor()

        class ValuePattern:
            IsReadOnly = False
            set_calls = 0

            def SetValue(self, *_args, **_kwargs):
                self.set_calls += 1
                return True

        pattern = ValuePattern()

        class ValueControl:
            def GetValuePattern(self):
                monitor.version += 1
                return pattern

        backend = WindowsInteractionBackend()
        metadata = {'type': 'EditControl', 'read_only': False}
        with self.assertRaises(HumanTookOver):
            backend._run_pattern(ValueControl(), {'id': WINDOW_ID}, metadata,
                                 'set_text', 'test text', lambda: False,
                                 monitor, monitor.snapshot())

        self.assertEqual(pattern.set_calls, 0)

    def test_human_input_after_action_reports_uncertain_not_unexecuted(self):
        monitor = FakeInputMonitor()
        tools, _ = self.make_tools(monitor=monitor)

        def action(_stopped, _snapshot):
            monitor.version += 1
            return {'confirmed': True}

        with self.assertRaises(InteractionUncertain) as raised:
            tools._run_action_with_input_guard(action, lambda: False)
        self.assertIn('Do not repeat', str(raised.exception))

    def test_keyboard_fallback_refuses_long_text_before_typing(self):
        monitor = FakeInputMonitor()

        class KeyboardControl:
            sent_keys = 0

            def GetValuePattern(self):
                return None

            def SendKeys(self, *_args, **_kwargs):
                self.sent_keys += 1

        control = KeyboardControl()
        backend = WindowsInteractionBackend()
        backend._revalidate = lambda selected, _stopped: selected
        metadata = {'type': 'EditControl', 'read_only': False, 'window_handle': 100}
        with patch.object(WindowsInteractionBackend, '_foreground_window', return_value=100):
            with self.assertRaises(ObservationError):
                backend._run_pattern(control, {'id': WINDOW_ID, 'handle': 100}, metadata,
                                     'set_text', 'x' * (MAX_KEYBOARD_FALLBACK_CHARS + 1),
                                     lambda: False, monitor, monitor.snapshot())
        self.assertEqual(control.sent_keys, 0)


    def test_shared_desktop_lock_prevents_interaction(self):
        tools, backend = self.make_tools()
        with desktop_access(timeout=0):
            result, state, _ = tools.invoke('windows_manage', {
                'app_id': 'notepad', 'window_id': WINDOW_ID, 'action': 'activate'})
        self.assertEqual(state, '搁置')
        self.assertEqual(backend.actions, [])

    def test_program_policy_marks_interactions_as_controlled(self):
        self.assertEqual(tool_policy('windows_launch'), 'desktop_control')
        self.assertEqual(tool_policy('windows_list'), 'desktop_read')
        offered = permitted_tools(WINDOWS_INTERACTION_TOOL_SCHEMAS)
        self.assertEqual({item['function']['name'] for item in offered}, {
            'windows_launch', 'windows_manage', 'windows_control_action',
        })

    def test_chat_tool_loop_uses_approval_untrusted_result_and_activity_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            role = root / 'characters' / 'test'
            role.mkdir(parents=True)
            (root / 'persona').mkdir()
            (root / 'persona' / 'test.md').write_text('Fictional persona', encoding='utf-8')
            (role / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 80
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
[tools]
enabled = true
folder = "data/test/workspace"
max_steps = 3
''', encoding='utf-8')
            cfg = load_character(root, 'test')
            store = Store(root / 'data')
            interaction, backend = self.make_tools()
            approval_prompts = []

            class ToolSet:
                capability_event = None
                capability_event_model = None
                tools = interaction.tools

                @staticmethod
                def begin_request():
                    pass

                @staticmethod
                def preview_summary(name, args):
                    return interaction.preview_summary(name, args)

                @staticmethod
                def invoke(name, args, approval, *, should_stop=None):
                    return interaction.invoke(name, args, approval, should_stop=should_stop)

            arguments = json.dumps({
                'app_id': 'notepad', 'window_id': WINDOW_ID,
                'control_id': CONTROL_ID, 'action': 'set_text',
                'text': 'Hello, Lan! 你好。',
            }, ensure_ascii=False)
            call = SimpleNamespace(id='desktop-call', function=SimpleNamespace(
                name='windows_control_action', arguments=arguments))
            first = SimpleNamespace(content=None, tool_calls=[call], model_dump=lambda exclude_none=True: {
                'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'desktop-call', 'type': 'function', 'function': {
                        'name': 'windows_control_action', 'arguments': arguments}}]})
            final = SimpleNamespace(content='The note is visible.', tool_calls=[],
                                    model_dump=lambda exclude_none=True: {
                                        'role': 'assistant', 'content': 'The note is visible.'})
            requests = []

            def model(messages, **kwargs):
                requests.append([dict(message) for message in messages])
                if len(requests) == 1:
                    schemas = kwargs.get('tools', [])
                    self.assertIn('windows_control_action', {
                        item['function']['name'] for item in schemas})
                    return first, 100, 20, 0, 'stop'
                return final, 100, 20, 0, 'stop'

            def approve(prompt):
                approval_prompts.append(prompt)
                return 'y'

            chat = Chat(cfg, store, Budget(store), Mock(side_effect=model), tools=ToolSet(),
                        approval=approve, output=lambda _message: None)
            reply = chat.send('Please type the requested test text.')

            self.assertEqual(reply, 'The note is visible.')
            self.assertEqual(backend.value, 'Hello, Lan! 你好。')
            self.assertEqual(len(approval_prompts), 1)
            self.assertTrue(any('<untrusted_windows_desktop>' in str(message)
                                for message in requests[1]))
            ledger = store.read('test/tools.jsonl')
            self.assertTrue(any(row['state'] == '进行中' for row in ledger))
            completed = next(row for row in ledger if row['state'] == '成功')
            self.assertIn('未记录控件文字', completed.get('detail', ''))
            self.assertNotIn('Hello, Lan! 你好。', str(ledger))

            chat.autonomy_should_yield = lambda: True
            before_actions = list(backend.actions)
            paused = chat._execute_tool_call(call, 'autonomy-request', 'autonomy-call', True)
            self.assertEqual(paused.state, '中断')
            self.assertEqual(backend.actions, before_actions)


if __name__ == '__main__':
    unittest.main()
