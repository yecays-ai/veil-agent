import ast
import tempfile
import threading
import unittest
from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace

from veil.tools import tools_agent
from veil.tools import tool_failures
from veil.tools.tool_failures import UNKNOWN, finalize, internal_failure, refusal
from veil_agent import Budget, Chat, Store, load_character
from veil.tools.tools_agent import FilesystemTools
from veil.windows.windows_observer import WindowsObservationTools


class FinalizeTests(unittest.TestCase):
    def test_success_is_untouched_and_a_plain_failure_gets_the_backstop(self):
        self.assertEqual(finalize('saved', '成功'), ('saved', None))
        text, ledger = finalize('old_text appears 0 times', '被拒绝')
        self.assertTrue(text.startswith('old_text appears 0 times'))
        self.assertIn('[program] No further guidance was added', text)
        self.assertIn('程序未给出改法', ledger)

    def test_a_known_fix_and_the_received_arguments_are_added_after_the_reason(self):
        result = refusal('This tool takes no arguments.',
                         fix='Call it with {}.', received={'window_id': 'w-1'},
                         detail='参数无效。')
        text, ledger = finalize(result, '被拒绝', result.detail)
        self.assertTrue(text.startswith('This tool takes no arguments.'))
        self.assertIn('[program] arguments received: {"window_id": "w-1"}', text)
        self.assertIn('[program] correction: Call it with {}.', text)
        self.assertIn('收到参数：{"window_id": "w-1"}', ledger)
        self.assertIn('怎么改：Call it with {}.', ledger)

    def test_received_arguments_alone_do_not_invent_a_correction(self):
        text, _ledger = finalize(refusal('Could not use this call.', received={'a': 1}),
                                 '被拒绝', None)
        self.assertIn('arguments received: {"a": 1}', text)
        self.assertNotIn('correction:', text)

    def test_received_arguments_are_redacted_before_they_leave(self):
        # Truncation is not redaction: a token in the arguments must not reach the
        # model or the ledger.
        result = refusal('Bad call.', received={'api_key': 'sk-abcdef0123456789abcdef',
                                                'path': 'lab/x.txt'})
        text, ledger = finalize(result, '被拒绝', None)
        for surface in (text, ledger):
            self.assertNotIn('sk-abcdef0123456789abcdef', surface)
            self.assertIn('lab/x.txt', surface)

    def test_a_secret_with_spaces_or_commas_is_redacted_as_a_whole_value(self):
        # Review: the regex stopped at spaces/commas and leaked the rest.
        cases = ({'password': 'correct horse, battery staple'},
                 {'authorization': 'Bearer abc def ghi'},
                 {'api_key': 'sk-abc, def'},
                 {'nested': {'client_secret': 'a b, c'}})
        for received in cases:
            text, ledger = finalize(refusal('no', received=received), '被拒绝', None)
            for surface in (text, ledger):
                for leaked in ('correct horse', 'battery staple', 'abc def',
                               'sk-abc, def', 'a b, c'):
                    self.assertNotIn(leaked, surface)
                self.assertIn('已隐藏', surface)

    def test_a_secret_in_malformed_json_is_redacted(self):
        text, _ledger = finalize(refusal('no', received='{"token": "abc def, ghi"'),
                                 '被拒绝', None)
        self.assertNotIn('abc def', text)

    def test_a_long_opaque_value_is_redacted_too(self):
        token = 'AbCdEf0123456789AbCdEf0123456789AbCdEf01'
        text, _ledger = finalize(refusal('Bad call.', received={'value': token}), '被拒绝', None)
        self.assertNotIn(token, text)

    def test_an_unknown_fix_says_so_instead_of_guessing(self):
        text, ledger = finalize(refusal('The window could not be read.', fix=UNKNOWN), '出错', None)
        self.assertIn('does not know a corrective step', text)
        self.assertIn('不知道该怎么改', ledger)


class InternalFailureTests(unittest.TestCase):
    def test_the_exception_text_never_leaves_the_program(self):
        exc = ValueError(r'C:\Users\lan\Documents\secret.txt is locked')
        text, ledger, error_id = internal_failure('read_text_file', exc)
        self.assertTrue(error_id.startswith('err-'))
        self.assertIn(error_id, text)
        self.assertIn('ValueError', text)
        self.assertIn('failed inside the program', text)
        self.assertNotIn('secret.txt', text)
        self.assertNotIn('C:\\Users', text)
        self.assertNotIn('secret.txt', ledger)
        self.assertIn(error_id, ledger)

    def test_an_authored_user_error_keeps_its_own_words(self):
        # UserError / RequestFailed / BudgetExceeded are meant to be read; they must
        # not be relabelled as an internal fault or write a traceback row.
        from agent_types import UserError
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'data')
            fake = SimpleNamespace(store=store, cfg=SimpleNamespace(id='test'))
            note = Chat._internal_note(
                fake, 'long_task_update',
                UserError('Long-task state could not be saved safely; the previous version was preserved.'),
                '{"a": 1}')
            self.assertIn('could not be saved safely', note)
            self.assertNotIn('err-', note)
            self.assertEqual(store.read('test/tool-errors.jsonl'), [])

    def test_the_local_error_file_keeps_the_traceback_for_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'data')
            fake = SimpleNamespace(store=store, cfg=SimpleNamespace(id='test'))
            exc = ValueError(r'C:\Users\lan\Documents\secret.txt is locked')
            try:
                raise exc
            except ValueError:
                Chat._record_internal_failure(fake, 'read_text_file', 'err-12345678', exc, '{"path": "x"}')
            rows = store.read('test/tool-errors.jsonl')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['error_id'], 'err-12345678')
        self.assertEqual(rows[0]['tool'], 'read_text_file')
        self.assertIn('ValueError', rows[0]['traceback'])
        self.assertIn('secret.txt', rows[0]['traceback'])


class NoArgumentToolTests(unittest.TestCase):
    """The exact case lan saw: peek_screen / snooping_around rejected for taking no arguments."""

    def tools(self):
        return WindowsObservationTools(True, platform_enabled=True, backend=object())

    def test_listing_windows_with_arguments_says_what_it_received_and_how_to_fix(self):
        result, state, summary = self.tools().invoke('windows_list', {'window_id': 'w-1'})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(summary, 'open application windows')
        text, ledger = finalize(result, state, result.activity_detail)
        self.assertIn('This tool takes no arguments.', text)
        self.assertIn('[program] arguments received: {"window_id": "w-1"}', text)
        self.assertIn('[program] correction:', text)
        self.assertIn('windows_observe', text)
        self.assertIn('收到参数：{"window_id": "w-1"}', ledger)
        self.assertIn('怎么改：', ledger)

    def test_foreground_window_with_arguments_gets_the_same_treatment(self):
        result, state, _summary = self.tools().invoke('windows_foreground', {'app': 'Chrome'})
        self.assertEqual(state, '被拒绝')
        text, _ledger = finalize(result, state, result.activity_detail)
        self.assertIn('arguments received: {"app": "Chrome"}', text)
        self.assertIn('correction:', text)

    def test_a_non_object_argument_is_reported_as_such(self):
        result, state, _summary = self.tools().invoke('windows_observe', 'not a dict')
        self.assertEqual(state, '被拒绝')
        text, _ledger = finalize(result, state, result.activity_detail)
        self.assertIn('A JSON object of arguments is required.', text)
        self.assertIn('correction:', text)


class FailureSourceContractTests(unittest.TestCase):
    """Every authored failure return says something specific (ticket 415).

    This is the guard for NEW tools: a refusal or an error that only says
    "参数无效。" / "Error" (or nothing at all) turns CI red, so a future tool cannot
    silently drop back to the one meaningless sentence lan complained about.
    """

    MODULES = ('veil_agent.py', 'veil/tools/tools_agent.py', 'veil/windows/windows_observer.py',
               'veil/windows/windows_interaction.py', 'memory_agent.py', 'long_tasks.py')
    BARE = {
        '参数无效。', '参数无效', 'Error', 'Error.', 'Failed.', 'Failed',
        'The tool failed.', 'Invalid arguments.', 'Invalid arguments',
        'This tool does not accept arguments.',
    }

    @staticmethod
    def _literals(node):
        return ''.join(child.value for child in ast.walk(node)
                       if isinstance(child, ast.Constant) and isinstance(child.value, str))

    def test_no_failure_return_is_empty_or_a_bare_phrase(self):
        root = Path(__file__).resolve().parents[1]
        bad = []
        for name in self.MODULES:
            tree = ast.parse((root / name).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
                    continue
                if len(node.value.elts) < 2:
                    continue
                state = node.value.elts[1]
                if not (isinstance(state, ast.Constant)
                        and state.value in ('被拒绝', '出错')):
                    continue
                value = node.value.elts[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    reason = value.value.strip()
                elif (isinstance(value, ast.Call) and value.args
                      and isinstance(value.args[0], ast.Constant)
                      and isinstance(value.args[0].value, str)):
                    reason = value.args[0].value.strip()
                else:
                    # Shared authored constants (e.g. NOT_ASKED_TEXT) are reviewed elsewhere.
                    continue
                if not reason:
                    bad.append(f'{name}:{node.lineno} has no reason')
                elif reason in self.BARE:
                    bad.append(f'{name}:{node.lineno} only says {reason!r}')
        self.assertEqual(bad, [], 'tool failures must explain themselves: ' + '; '.join(bad))


class EveryOfferedToolContractTests(unittest.TestCase):
    """Walk every tool the model can call and prove a failure is never a bare sentence.

    This is the registry-wide guard the plan promised: a tool added later (in any
    module, with any return style) still goes through `_execute_tool_call`, so the
    probe fails CI if its failure skips the program backstop.
    """

    NAMES = sorted(
        tools_agent.ALLOW | tools_agent.ASK | tools_agent.LAB_ONLY | tools_agent.OWN_WORK
        | {'send_message', 'stay_silent', 'reply_later', 'expand_capability', 'hold',
           'breath_search', 'memory_catalog', 'memory_read', 'windows_list',
           'windows_foreground', 'windows_observe', 'desktop_icons_read', 'open_lab_game',
           'browser', 'screen_pop', 'delegate', 'life_record', 'thought', 'set_my_alarm',
           'what_did_i_do', 'search_chat_history', 'long_task_status', 'long_task_update',
           'autonomy_history', 'mcp_status', 'recall_message', 'card_action'})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text('''name = "Peter"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50

[providers.fake]
model = "fake"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"

[tools]
enabled = true
max_steps = 3
max_result_bytes = 200
''', encoding='utf-8')
        self.cfg = load_character(root, 'test')
        self.store = Store(root / 'data')
        tools = SimpleNamespace(
            tools=[SimpleNamespace(name=name, description='tool', inputSchema={})
                   for name in self.NAMES],
            capability_event=None,
            preview_summary=lambda name, args: name,
            invoke=Mock(return_value=('ok', '成功', 'x')))
        self.chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), tools=tools,
                         output=lambda *_: None)

    def test_every_offered_tool_explains_its_failure(self):
        offered = self.chat.tool_schemas()
        names = [schema['function']['name'] for schema in offered]
        self.assertGreater(len(names), 20)
        missing = []
        for name in names:
            call = SimpleNamespace(id='probe-' + name,
                                   function=SimpleNamespace(name=name, arguments='{}'))
            execution = self.chat._execute_tool_call(call, 'probe', 'step:0', False, offered)
            if execution.state == '成功':
                continue
            if '[program]' not in str(execution.result):
                missing.append(name)
        self.assertEqual(missing, [],
                         'failures that skip the program backstop: ' + ', '.join(missing))

    def test_json_parse_failure_redacts_the_arguments(self):
        # Review fix: this path used to echo the raw arguments into the message.
        call = SimpleNamespace(id='json', function=SimpleNamespace(
            name='read_text_file',
            arguments='{"api_key": "sk-abcdef0123456789abcdef", bad'))
        execution = self.chat._execute_tool_call(call, 'probe', 'step:0', False,
                                                self.chat.tool_schemas())
        text = str(execution.result)
        self.assertIn('not a valid JSON object', text)
        self.assertNotIn('sk-abcdef0123456789abcdef', text)
        self.assertNotIn('sk-abcdef0123456789abcdef', str(execution.failure_detail))

    def test_every_offered_tool_survives_a_crashing_backend(self):
        # A tool that reaches the program and raises is the case that used to be
        # reported as "arguments are not valid JSON".  Every such call must still
        # come back with a safe reason and a trackable id.
        offered = self.chat.tool_schemas()
        self.chat.tools.invoke.side_effect = ValueError('probe: backend exploded')
        unexplained = []
        for schema in offered:
            name = schema['function']['name']
            call = SimpleNamespace(id='probe-' + name,
                                   function=SimpleNamespace(name=name, arguments='{}'))
            execution = self.chat._execute_tool_call(call, 'probe', 'step:0', False, offered)
            text = str(execution.result)
            if execution.state == '成功':
                continue
            if '[program]' not in text and 'failed inside the program' not in text:
                unexplained.append(name)
        self.assertEqual(unexplained, [],
                         'program failures left unexplained: ' + ', '.join(unexplained))



class RealToolProbeTests(unittest.TestCase):
    """Probe REAL tool objects, not a mock that always says ok.

    A mock backend would prove only the funnel.  These are the real validator
    paths (filesystem path checks, window argument checks) driven through the
    same `_execute_tool_call` the model uses.
    """

    TOML = '''name = "Peter"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50

[providers.fake]
model = "fake"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"

[tools]
enabled = true
max_steps = 3
max_result_bytes = 200
'''

    NAMES = ('read_text_file', 'write_file', 'list_directory', 'directory_tree',
             'lab_file_digest', 'windows_list', 'windows_foreground', 'windows_observe')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text(self.TOML, encoding='utf-8')
        self.cfg = load_character(root, 'test')
        self.store = Store(root / 'data')
        workspace = root / 'workspace'
        workspace.mkdir()
        backend = SimpleNamespace(
            list_windows=lambda stopped: [],
            read_controls=lambda window, stopped: [],
            capture_window=lambda window, stopped: None)
        manager = FilesystemTools.__new__(FilesystemTools)
        manager.workspace = workspace
        manager.cfg = self.cfg
        manager.lab = None
        manager.lab_max_write_bytes = 200000
        manager.workspace_operation_lock = threading.RLock()
        manager.store = self.store
        manager.client = Mock()
        manager.capability_event = None
        manager.capability_event_model = None
        manager.tools = [SimpleNamespace(name=name, description='tool', inputSchema={})
                         for name in self.NAMES]
        manager.windows_tools = WindowsObservationTools(True, platform_enabled=True,
                                                        backend=backend)
        self.chat = Chat(self.cfg, self.store, Budget(self.store), Mock(), tools=manager,
                         output=lambda *_: None)

    def probe(self, name, arguments):
        offered = self.chat.tool_schemas()
        call = SimpleNamespace(id='real-' + name,
                               function=SimpleNamespace(name=name, arguments=arguments))
        return self.chat._execute_tool_call(call, 'probe', 'step:0', False, offered)

    def test_real_tools_explain_their_failures(self):
        probes = {
            'read_text_file': '{"path": "../../etc/passwd"}',
            'write_file': '{"path": "../../etc/passwd", "content": "x"}',
            'list_directory': '{"path": ".."}',
            'directory_tree': '{"path": ".."}',
            'lab_file_digest': '{"path": "../../etc/passwd"}',
            'windows_list': '{"window_id": "w-1"}',
            'windows_foreground': '{"app": "Chrome"}',
            'windows_observe': '{"window_id": "nope"}',
        }
        unexplained = []
        for name, arguments in probes.items():
            execution = self.probe(name, arguments)
            text = str(execution.result)
            self.assertNotEqual(execution.state, '成功', f'{name} must refuse junk arguments')
            if '[program]' not in text and 'failed inside the program' not in text:
                unexplained.append(f'{name}: {text[:80]}')
        self.assertEqual(unexplained, [],
                         'real tools whose failures skip the contract: ' + '; '.join(unexplained))


if __name__ == '__main__':
    unittest.main()
