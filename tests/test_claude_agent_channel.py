"""Claude Code chat channel tests. A fake `claude` process stands in for the real one; no network."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import claude_agent_channel as channel  # noqa: E402

TOML = '''name = "Peter"
provider = "cc"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.cc]
kind = "claude_agent"
model = "sonnet"
max_input_tokens = 60000
[tools]
enabled = true
folder = "data/test/workspace"
max_steps = 6
'''


def post(server, path, payload, token=None):
    request = urllib.request.Request(
        f'http://127.0.0.1:{server.port}{path}', data=json.dumps(payload).encode('utf-8'), method='POST',
        headers={'X-Veil-Token': token or server.token})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode('utf-8'))


class FakeProcess:
    """What `claude -p --output-format stream-json` prints, driven by a script that may call the tool server."""

    def __init__(self, script, stdout_lines):
        self.script, self.lines = script, stdout_lines
        self.stdin = SimpleNamespace(write=lambda text: None, close=lambda: None)
        self.stderr = SimpleNamespace(read=lambda: '')
        self.returncode = 0
        self.pid = 1
        self.killed = False

    @property
    def stdout(self):
        self.script()
        return iter(self.lines)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def result_line(**extra):
    return json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '',
                       'total_cost_usd': 0.04, 'num_turns': 2, 'duration_ms': 900, **extra}) + '\n'


class ChannelChatTests(unittest.TestCase):
    def setUp(self):
        from veil_agent import Budget, Chat, Store, load_character
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Test persona.', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text(TOML, encoding='utf-8')
        cfg = load_character(root, 'test')
        self.assertEqual(cfg.provider['kind'], 'claude_agent')
        self.store = Store(root / 'data')
        self.shown = []

        def no_model(*_a, **_k):
            raise AssertionError('the channel must not call the API model')

        self.approvals = []
        self.answers = ['y']
        self.chat = Chat(cfg, self.store, Budget(self.store), no_model, output=self.shown.append,
                         approval=lambda prompt: (self.approvals.append(prompt), self.answers.pop(0))[1])

    def run_chat(self, script_for, text='hello'):
        """script_for(server) -> callable run while the fake process 'prints'."""
        holder = {}

        def popen(cmd, **kwargs):
            holder['cmd'], holder['cwd'], holder['env'] = cmd, kwargs.get('cwd'), kwargs.get('env')
            server = self.chat._claude_agent.server
            process = FakeProcess(lambda: script_for(server), [result_line()])
            holder['process'] = process
            return process

        original = channel.find_claude
        channel.find_claude = lambda provider=None, env_file=None: 'claude'
        self.addCleanup(lambda: setattr(channel, 'find_claude', original))
        self.chat._claude_agent = channel.ClaudeAgentChannel(
            self.chat, Path(self.tmp.name) / 'data' / 'test' / 'claude-agent', self.chat.cfg.provider, popen=popen)
        self.addCleanup(lambda: self.chat._claude_agent.server and self.chat._claude_agent.server.close())
        reply = self.chat.send(text)
        return reply, holder

    def test_turn_runs_through_claude_code_and_bubbles_are_saved(self):
        def script(server):
            for text in ('hey', 'you there'):
                answer = post(server, '/call', {'name': 'send_message', 'arguments': {'text': text}})
                self.assertEqual(answer['state'], '成功')
            post(server, '/call', {'name': 'send_message', 'arguments': {'text': 'bye', 'done': True}})

        reply, holder = self.run_chat(script)
        self.assertEqual(reply, '')
        rows = self.store.read('test/chat.jsonl')
        self.assertEqual([r['content'] for r in rows if r['role'] == 'assistant'], ['hey', 'you there', 'bye'])
        self.assertTrue(self.chat.turn_done)
        self.assertEqual(holder['cmd'][holder['cmd'].index('--model') + 1], 'sonnet')
        cmd = holder['cmd']
        self.assertIn('--max-turns', cmd)
        self.assertEqual(cmd[cmd.index('--max-turns') + 1], '30')
        self.assertEqual(cmd[cmd.index('--max-budget-usd') + 1], '1.5')
        self.assertEqual(cmd[cmd.index('--permission-prompt-tool') + 1], 'mcp__veil__approve')
        self.assertNotIn('ANTHROPIC_API_KEY', holder['env'])
        costs = self.store.read('test/claude-agent-costs.jsonl')
        self.assertEqual(costs[-1]['usd_estimate'], 0.04)

    def test_the_tool_list_is_what_veil_offers_this_turn(self):
        seen = {}

        def script(server):
            seen['names'] = [t['name'] for t in post(server, '/list', {})['tools']]
            post(server, '/call', {'name': 'stay_silent', 'arguments': {}})

        self.run_chat(script)
        self.assertIn('send_message', seen['names'])
        self.assertIn('stay_silent', seen['names'])

    def test_delegate_is_available_to_claude_code_only_when_selected(self):
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        seen, delegated = {}, Mock(return_value=SimpleNamespace(
            text='fictional research result', state='成功'))
        self.chat._run_delegate = delegated
        provider = {'model': 'fixture-research-model', 'label': 'Fixture Research',
                    'max_input_tokens': 10000, 'max_output_tokens': 1200,
                    'input_cny_per_million': 1, 'cached_input_cny_per_million': 0.5,
                    'output_cny_per_million': 2}

        class FakeChannel:
            def run_turn(_self, _system, _prompt, request_id, schemas, **_kwargs):
                seen['names'] = {schema['function']['name'] for schema in schemas}
                call = SimpleNamespace(id='fixture-call', function=SimpleNamespace(
                    name='delegate', arguments='{"task":"Find a public article about fictional garden design."}'))
                execution = self.chat._execute_tool_call(call, request_id, 'fixture-call', False, schemas)
                seen['result'] = execution.result
                seen['state'] = execution.state
                return [], {'result': '', 'total_cost_usd': 0, 'num_turns': 1, 'duration_ms': 1}

        with (patch.object(self.chat.cfg.models, 'selected', return_value='explicit-delegate-model'),
              patch.object(self.chat.cfg.models, 'chosen', return_value=(
                  'explicit-delegate-model', 'fixture', provider))):
            self.chat._claude_agent = FakeChannel()
            self.chat._claude_agent_chat([{'role': 'system', 'content': 'test system'},
                                         {'role': 'user', 'content': 'test request'}],
                                         'fixture-request', self.chat.cfg,
                                         self.chat.current_request_tool_schemas())

        self.assertIn('delegate', seen['names'])
        self.assertEqual(seen['result'], 'fictional research result')
        self.assertEqual(seen['state'], '成功')
        delegated.assert_called_once_with(
            'Find a public article about fictional garden design.', 'fixture-request')

    def test_delegate_is_not_offered_to_claude_code_when_disabled(self):
        self.assertNotIn('delegate', {s['function']['name'] for s in self.chat.tool_schemas()})

    def test_wrong_token_is_refused(self):
        def script(server):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                post(server, '/call', {'name': 'send_message', 'arguments': {'text': 'x'}}, token='wrong')
            self.assertEqual(caught.exception.code, 403)
            post(server, '/call', {'name': 'stay_silent', 'arguments': {}})

        self.run_chat(script)
        self.assertEqual([r for r in self.store.read('test/chat.jsonl') if r['role'] == 'assistant'], [])

    def test_approval_goes_to_the_chat_and_the_answer_comes_back(self):
        self.answers = ['y', 'n']
        results = []

        def script(server):
            body = {'tool_name': 'Bash', 'input': {'command': 'git clone https://example.org/x'}}
            results.append(post(server, '/approve', body))
            results.append(post(server, '/approve', {'tool_name': 'Write', 'input': {'file_path': 'a.txt'}}))
            post(server, '/call', {'name': 'stay_silent', 'arguments': {}})

        self.run_chat(script)
        self.assertEqual(results[0]['behavior'], 'allow')
        self.assertEqual(results[0]['updatedInput']['command'], 'git clone https://example.org/x')
        self.assertEqual(results[1]['behavior'], 'deny')
        self.assertIn('Bash', self.approvals[0])
        self.assertIn('git clone', self.approvals[0])

    def test_the_same_call_over_and_over_stops_the_run(self):
        def script(server):
            for _ in range(channel.REPEAT_LIMIT + 2):
                answer = post(server, '/call', {'name': 'list_files', 'arguments': {'path': '.'}})
            self.assertIn('stopped', answer['text'])

        with self.assertRaises(Exception) as caught:
            self.run_chat(script)
        self.assertIn('重复', str(caught.exception))

    def test_an_exhausted_quota_is_said_plainly(self):
        def popen(cmd, **kwargs):
            line = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': True,
                               'result': "You've hit your session limit · resets 3am"}) + '\n'
            return FakeProcess(lambda: None, [line])

        original = channel.find_claude
        channel.find_claude = lambda provider=None, env_file=None: 'claude'
        self.addCleanup(lambda: setattr(channel, 'find_claude', original))
        self.chat._claude_agent = channel.ClaudeAgentChannel(
            self.chat, Path(self.tmp.name) / 'cc', self.chat.cfg.provider, popen=popen)
        self.addCleanup(lambda: self.chat._claude_agent.server and self.chat._claude_agent.server.close())
        with self.assertRaises(Exception) as caught:
            self.chat.send('hi')
        self.assertIn('额度', str(caught.exception))

    def test_autonomous_wake_requires_an_api_model_if_none_is_configured(self):
        with self.assertRaises(Exception) as caught:
            self.chat.send('wake', autonomy=True)
        self.assertIn('API 模型', str(caught.exception))


class PromptTests(unittest.TestCase):
    def test_history_becomes_a_transcript_and_the_last_user_message_stays_last(self):
        messages = [{'role': 'system', 'content': 'persona'}, {'role': 'system', 'content': 'facts'},
                    {'role': 'user', 'content': 'q1'}, {'role': 'assistant', 'content': 'a1'},
                    {'role': 'system', 'content': 'memory note'}, {'role': 'user', 'content': 'now'}]
        system, prompt = channel.build_prompt(messages)
        self.assertTrue(system.startswith('persona\n\nfacts'))
        self.assertIn('send_message', system)
        self.assertIn('Lan: q1\nPeter: a1', prompt)
        self.assertIn('memory note', prompt)
        self.assertTrue(prompt.rstrip().endswith('now'))

    def test_photo_messages_keep_their_text(self):
        messages = [{'role': 'system', 'content': 'p'}, {'role': 'user', 'content': [
            {'type': 'text', 'text': 'look'}, {'type': 'image_url', 'image_url': {'url': 'data:...'}}]}]
        self.assertTrue(channel.build_prompt(messages)[1].endswith('look'))


class HookTests(unittest.TestCase):
    def run_hook(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'hook.py'
            path.write_text(channel.stop_hook_source(), encoding='utf-8')
            out = subprocess.run([sys.executable, '-I', str(path)], input=json.dumps(payload), capture_output=True,
                                 text=True, check=True).stdout
        return json.loads(out)

    def test_ending_tools_stop_the_turn_and_others_only_remind(self):
        for payload in ({'tool_name': 'mcp__veil__stay_silent', 'tool_input': {}},
                        {'tool_name': 'mcp__veil__send_message', 'tool_input': {'text': 'x', 'done': True}},
                        {'tool_name': 'mcp__veil__reply_later', 'tool_input': {}}):
            self.assertIs(self.run_hook(payload)['continue'], False)
        for payload in ({'tool_name': 'mcp__veil__send_message', 'tool_input': {'text': 'x'}},
                        {'tool_name': 'Bash', 'tool_input': {'command': 'ls'}}):
            self.assertNotIn('continue', self.run_hook(payload))


class ProviderTests(unittest.TestCase):
    def test_channel_provider_needs_no_key_or_price(self):
        from veil_agent import toml_provider
        provider = toml_provider({'kind': 'claude_agent', 'model': 'claude-agent'})
        self.assertEqual(provider['api_key_env'], '')
        self.assertEqual(int(provider['output_cny_per_million']), 0)

    def test_api_providers_still_need_their_fields(self):
        from veil_agent import toml_provider
        with self.assertRaises(KeyError):
            toml_provider({'model': 'm'})

    def test_other_credentials_are_not_passed_to_claude_code(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k', 'DEEPSEEK_API_KEY': 'k', 'PATH': '/bin'}):
            env = channel.minimal_env()
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn('DEEPSEEK_API_KEY', env)



class HookAndRulesTests(unittest.TestCase):
    def test_the_stop_hook_knows_aliases(self):
        source = channel.stop_hook_source({'stay_silent': 'rest', 'send_message': 'say'})
        for name, tool_input, ends in (('mcp__veil__rest', {}, True), ('mcp__veil__say', {'done': True}, True),
                                       ('mcp__veil__say', {'done': False}, False), ('mcp__veil__stay_silent', {}, True)):
            out = subprocess.run([sys.executable, '-c', source], input=json.dumps({'tool_name': name, 'tool_input': tool_input}),
                                 capture_output=True, text=True).stdout
            self.assertEqual(json.loads(out).get('continue') is False, ends, name)

    def test_channel_is_peter_only_in_model_settings(self):
        from model_settings import ModelSettings
        with tempfile.TemporaryDirectory() as tmp:
            builtins = {'claude_agent': {'kind': 'claude_agent', 'model': 'claude-agent', 'api_key_env': '',
                                         'base_url': '', 'max_input_tokens': 200000}}
            settings = ModelSettings(Path(tmp), 'peter', builtins, 'claude_agent')
            data = settings.data
            self.assertTrue(settings._choice_ok(data, 'peter', 'builtin:claude_agent'))
            self.assertFalse(settings._choice_ok(data, 'background', 'builtin:claude_agent'))
            self.assertFalse(settings._choice_ok(data, 'glance', 'builtin:claude_agent'))

    def test_secret_files_are_denied_to_native_tools(self):
        self.assertIn('Read(**/.env)', channel.DENY_RULES)
        self.assertIn('Read(**/secrets/**)', channel.DENY_RULES)

    def test_claude_path_is_read_from_the_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'claude.exe'
            exe.write_text('x', encoding='utf-8')
            env = Path(tmp) / '.env'
            env.write_text(f'VEIL_CLAUDE_PATH={exe}\n', encoding='utf-8')
            self.assertEqual(channel.find_claude({}, env, {}), str(exe))


class FindClaudeTests(unittest.TestCase):
    def _app(self, root, package, version, digest):
        exe = Path(root) / 'Packages' / package / 'LocalCache' / 'Roaming' / 'Claude' / 'claude-code' / version / digest / 'claude.exe'
        exe.parent.mkdir(parents=True)
        exe.write_text('x', encoding='utf-8')
        return str(exe)

    def test_newest_desktop_app_copy_is_found_without_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('shutil.which', return_value=None):
            self._app(tmp, 'Claude_abc', '2.1.309', 'aaa')
            newest = self._app(tmp, 'Claude_abc', '2.1.317', 'bbb')
            self._app(tmp, 'Claude_abc', '2.1.99', 'ccc')
            self.assertEqual(channel.find_claude({}, None, {'LOCALAPPDATA': tmp}), newest)

    def test_a_wrong_configured_path_falls_back_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('shutil.which', return_value=None):
            real = self._app(tmp, 'Claude_abc', '2.1.317', 'bbb')
            env = {'LOCALAPPDATA': tmp, 'VEIL_CLAUDE_PATH': str(Path(tmp) / 'typo' / 'claude.exe')}
            self.assertEqual(channel.find_claude({}, None, env), real)

    def test_an_existing_configured_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('shutil.which', return_value=None):
            self._app(tmp, 'Claude_abc', '2.1.317', 'bbb')
            mine = Path(tmp) / 'mine.exe'
            mine.write_text('x', encoding='utf-8')
            self.assertEqual(channel.find_claude({}, None, {'LOCALAPPDATA': tmp, 'VEIL_CLAUDE_PATH': f'"{mine}"'}), str(mine))

    def test_an_empty_environment_is_used_as_given(self):
        with mock.patch('shutil.which', return_value=None), mock.patch.dict(os.environ, {'VEIL_CLAUDE_PATH': __file__}):
            self.assertIsNone(channel.find_claude({}, None, {}))

    def test_nothing_found_is_none(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('shutil.which', return_value=None):
            self.assertIsNone(channel.find_claude({}, None, {'LOCALAPPDATA': tmp}))


class ProcessLaunchTests(unittest.TestCase):
    def test_legacy_model_placeholder_uses_sonnet(self):
        claude = channel.ClaudeAgentChannel(None, Path('run'), provider={'model': 'claude-agent'})
        cmd = claude.command('claude.exe')
        self.assertEqual(cmd[cmd.index('--model') + 1], 'sonnet')

    def test_read_only_lookups_need_no_approval_but_writes_and_shell_still_ask(self):
        claude = channel.ClaudeAgentChannel(None, Path('run'), provider={'model': 'sonnet'})
        cmd = claude.command('claude.exe')
        allowed = cmd[cmd.index('--allowedTools') + 1:]
        for name in ('Read', 'Grep', 'Glob'):
            self.assertIn(name, allowed)
        for name in ('Bash', 'Write', 'Edit', 'MultiEdit', 'WebFetch', 'NotebookEdit'):
            self.assertNotIn(name, allowed)
        for tool in ('Read', 'Edit', 'Write'):  # `//**/` also covers files outside Claude Code's working folder
            for target in ('.env', '.env.*', 'secrets/**'):
                self.assertIn(f'{tool}(//**/{target})', channel.DENY_RULES)

    @unittest.skipUnless(os.name == 'nt', 'Windows process flags are only available on Windows')
    def test_windows_claude_process_uses_existing_background_flags(self):
        self.assertEqual(channel._process_group_options(), {
            'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW})

    def test_other_platforms_keep_their_process_group_behavior(self):
        with mock.patch.object(channel.os, 'name', 'posix'):
            self.assertEqual(channel._process_group_options(), {'start_new_session': True})


if __name__ == '__main__':
    unittest.main()
