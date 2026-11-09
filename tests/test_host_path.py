"""Ticket 329: host execution path (VEIL checkpoint + MCP gateway), with fakes only."""
from concurrent.futures import Future
import json
import tempfile
import threading
import unittest
import urllib.request
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import executor_opencode as ex
import host_executor
import veil_checkpoint as vc
from veil_mcp_gateway import VeilMcpGateway


def make_checkpoint(root, *, answers=(), autonomy=False, foreground=None, protect=()):
    work = root / 'veil' / 'data' / 'peter' / 'workspace'
    work.mkdir(parents=True)
    (work / '.env').write_text('KEY=1', encoding='utf-8')
    (work / 'src').mkdir()
    (work / 'src' / 'a.py').write_text('x = 1', encoding='utf-8')
    (root / 'veil' / 'data' / 'peter' / 'memory').mkdir(parents=True)
    asked = []
    queue = list(answers)

    def ask(prompt):
        asked.append(prompt)
        return queue.pop(0) if queue else 'n'
    checkpoint = vc.Checkpoint(work, ask=ask, autonomy=autonomy, log_path=root / 'log.jsonl',
                               forbidden=vc.forbidden_roots(root / 'veil', home=root / 'home'),
                               foreground=foreground or (lambda: ('notepad.exe', 'Untitled - Notepad')),
                               protect=protect)
    return checkpoint, work, asked


class CheckpointShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cp, self.work, self.asked = make_checkpoint(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def bash(self, command):
        return self.cp.opencode_permission('bash', [command], {'command': command})

    def test_recursive_read_over_secret_files_is_refused(self):
        for command in ('grep -r KEY .', 'rg KEY', 'cat *', 'grep -R KEY', 'tar czf out.tgz .'):
            self.assertEqual(self.bash(command)[0], 'reject', command)
        self.assertEqual(self.bash('grep -r x src')[0], 'once')
        self.assertEqual(self.bash('cat src/a.py')[0], 'once')

    def test_secret_veil_data_and_elevation_are_refused(self):
        self.assertEqual(self.bash('cat .env')[0], 'reject')
        self.assertEqual(self.bash('cat ../memory/notes.jsonl')[0], 'reject')
        self.assertEqual(self.bash('Start-Process notepad -Verb RunAs')[0], 'reject')
        self.assertEqual(self.cp.opencode_permission(
            'external_directory', [str(self.root / 'home' / '.ssh' / '*')], {})[0], 'reject')
        self.assertEqual(self.cp.opencode_permission(
            'read', [str(self.root / 'veil' / 'data' / 'peter' / 'memory' / 'x')], {})[0], 'reject')
        self.assertEqual(self.bash('cat .aws/credentials')[0], 'reject')
        self.assertEqual(self.bash('cat .git-credentials')[0], 'reject')
        self.assertEqual(self.bash('cat credentials*')[0], 'reject')
        self.assertEqual(self.bash('cat src/credentials_example.py')[0], 'once')
        local = self.root / 'local'
        with patch.dict('os.environ', {'LOCALAPPDATA': str(local)}):
            cp, _, _ = make_checkpoint(self.root / 'again')
            for target in (local / 'Microsoft' / 'Edge' / 'User*' / 'Default' / 'Login*',
                           local / 'Microsoft' / '*' / 'User Data'):
                command = f'Get-Content "{target}"'
                self.assertEqual(cp.opencode_permission('bash', [command], {'command': command})[0], 'reject',
                                 command)
        self.assertEqual(self.asked, [])

    def test_unknown_permissions_are_refused_and_every_decision_is_logged(self):
        self.assertEqual(self.cp.opencode_permission('websearch', ['x'], {})[0], 'reject')
        self.assertEqual(self.bash('cat .env')[0], 'reject')
        lines = [json.loads(line) for line in (self.root / 'log.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([(item['kind'], item['decision']) for item in lines], [('decision', 'deny')] * 2)
        self.assertTrue(all(item['cls'] in vc.CLASSES for item in lines))
        self.assertIn('websearch', lines[0]['operation'])

    def test_windows_paths_and_environment_variables_keep_their_meaning(self):
        self.assertEqual(self.bash('type ..\\memory\\notes.jsonl')[0], 'reject')
        self.assertEqual(self.bash('type src\\a.py')[0], 'once')
        with patch.dict('os.environ', {'VEILHOME': str(self.root / 'home')}):
            self.assertEqual(self.bash('cat $env:VEILHOME/.ssh/id_x')[0], 'reject')
            self.assertEqual(self.bash('type %VEILHOME%\\.ssh\\x')[0], 'reject')
        self.assertEqual(self.bash('cat $env:NOT_SET_ANYWHERE/x')[0], 'reject')

    def test_links_out_of_the_project_or_veil_root_refuse_the_host_run(self):
        self.assertEqual(self.cp.refusal, '')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        veil = Path(tmp.name) / 'veil'
        (veil / 'data').mkdir(parents=True)
        cp = vc.Checkpoint(veil, ask=lambda _p: 'n', autonomy=False, log_path=Path(tmp.name) / 'l.jsonl',
                           forbidden=vc.forbidden_roots(veil, home=Path(tmp.name) / 'h'))
        self.assertIn("VEIL's own data", cp.refusal)
        try:
            (self.work / 'out').symlink_to(self.root / 'home', target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        (self.root / 'home').mkdir()
        cp = vc.Checkpoint(self.work, ask=lambda _p: 'n', autonomy=False, log_path=self.root / 'l2.jsonl')
        self.assertTrue(cp.refusal)

    def test_protected_journal_cannot_be_written(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cp, work, _ = make_checkpoint(Path(tmp.name), protect=['growth.md'])
        self.assertEqual(cp.opencode_permission('edit', ['growth.md'], {})[0], 'reject')
        self.assertEqual(cp.opencode_permission('bash', ['echo x >> growth.md'],
                                                {'command': 'echo x >> growth.md'})[0], 'reject')


class CheckpointToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_computer_table(self):
        cp, _, asked = make_checkpoint(self.root, answers=['y', 'n'])
        call = cp.tool_call
        self.assertTrue(call(vc.COMPUTER_SERVER, 'Snapshot', {})[0])
        self.assertTrue(call(vc.COMPUTER_SERVER, 'App', {'mode': 'launch', 'name': 'Notepad'})[0])
        self.assertTrue(call(vc.COMPUTER_SERVER, 'Type', {'text': 'hello', 'loc': [1, 2]})[0])
        self.assertTrue(call(vc.COMPUTER_SERVER, 'Shortcut', {'shortcut': 'ctrl+s'})[0])  # asked: y
        self.assertFalse(call(vc.COMPUTER_SERVER, 'Type', {'text': 'hi', 'loc': [1, 2], 'press_enter': True})[0])
        self.assertEqual(len(asked), 2)
        self.assertFalse(call(vc.COMPUTER_SERVER, 'Shortcut', {'shortcut': 'ctrl+shift+enter'})[0])
        self.assertEqual(len(asked), 2)
        self.assertFalse(call(vc.COMPUTER_SERVER, 'PowerShell', {'command': 'dir'})[0])
        self.assertFalse(call(vc.COMPUTER_SERVER, 'App', {'mode': 'launch_executable',
                                                           'executable': 'C:/Windows/System32/cmd.exe'})[0])
        self.assertFalse(call(vc.COMPUTER_SERVER, 'App', {'mode': 'launch', 'name': 'regedit'})[0])

    def test_no_typing_into_her_own_browser_and_typed_text_is_not_logged(self):
        cp, _, _ = make_checkpoint(self.root, foreground=lambda: ('chrome.exe', 'Sign in - Google'))
        self.assertFalse(cp.tool_call(vc.COMPUTER_SERVER, 'Type', {'text': 'hunter2', 'loc': [1, 2]})[0])
        self.assertTrue(cp.tool_call(vc.COMPUTER_SERVER, 'Click', {'loc': [1, 2]})[0])
        self.assertNotIn('hunter2', (self.root / 'log.jsonl').read_text(encoding='utf-8'))

    def test_no_input_into_password_or_uac_windows(self):
        cp, _, _ = make_checkpoint(self.root, foreground=lambda: ('consent.exe', 'User Account Control'))
        self.assertFalse(cp.tool_call(vc.COMPUTER_SERVER, 'Click', {'loc': [1, 2]})[0])
        self.assertTrue(cp.tool_call(vc.COMPUTER_SERVER, 'Snapshot', {})[0])

    def test_autonomy_never_asks(self):
        cp, _, asked = make_checkpoint(self.root, autonomy=True, answers=['y'])
        self.assertFalse(cp.tool_call(vc.COMPUTER_SERVER, 'Process', {'mode': 'kill', 'name': 'x.exe'})[0])
        self.assertEqual(asked, [])

    def test_browser_table(self):
        cp, _, _ = make_checkpoint(self.root)
        call = cp.tool_call
        self.assertTrue(call(vc.BROWSER_SERVER, 'browser_navigate', {'url': 'https://example.com/a'})[0])
        for url in ('http://127.0.0.1:8080', 'file:///C:/x', 'http://192.168.1.1', 'javascript:alert(1)'):
            self.assertFalse(call(vc.BROWSER_SERVER, 'browser_navigate', {'url': url})[0], url)
        self.assertFalse(call(vc.BROWSER_SERVER, 'browser_evaluate', {'function': '() => 1'})[0])
        self.assertFalse(call(vc.BROWSER_SERVER, 'browser_type', {'element': 'Password', 'text': 'x'})[0])
        self.assertTrue(call(vc.BROWSER_SERVER, 'browser_type', {'element': 'Search', 'text': 'x', 'submit': True})[0])
        self.assertFalse(call(vc.BROWSER_SERVER, 'browser_tabs', {'action': 'new', 'url': 'http://10.0.0.1'})[0])
        self.assertTrue(call(vc.BROWSER_SERVER, 'browser_tabs', {'action': 'new', 'url': 'https://example.com'})[0])
        self.assertFalse(call(vc.BROWSER_SERVER, 'browser_take_screenshot', {'filename': '../../x.png'})[0])


class FakeWorker:
    def __init__(self, service, environment=None):
        self.thread = SimpleNamespace(is_alive=lambda: True)
        self.closing = threading.Event()
        self.ready = Future()
        self.ready.set_result(True)
        self.calls = []
        tool = lambda name: SimpleNamespace(name=name, description=name, inputSchema={'type': 'object'})
        self.tools = [tool('Snapshot'), tool('Click'), tool('PowerShell')]

    def call(self, name, args, should_stop=None):
        self.calls.append((name, args))
        if name == 'Click':
            raise RuntimeError('MCP 工具返回错误：element 7 not found')
        return 'desktop tree'

    def close(self):
        self.closing.set()


class GatewayTests(unittest.TestCase):
    def rpc(self, gateway, method, params=None, token=None):
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode()
        request = urllib.request.Request(gateway.url, body, {
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (token or gateway.token)})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_gateway_offers_only_listed_tools_rules_calls_and_logs_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, _, _ = make_checkpoint(Path(tmp))
            with VeilMcpGateway(vc.COMPUTER_SERVER, {'command': 'x', 'args': []}, cp, vc.COMPUTER_TOOLS,
                                should_stop=lambda: False, worker_factory=FakeWorker) as gateway:
                names = [tool['name'] for tool in self.rpc(gateway, 'tools/list')['result']['tools']]
                self.assertEqual(names, ['Snapshot', 'Click'])
                ok = self.rpc(gateway, 'tools/call', {'name': 'Snapshot', 'arguments': {}})['result']
                self.assertEqual((ok['isError'], ok['content'][0]['text']), (False, 'desktop tree'))
                refused = self.rpc(gateway, 'tools/call', {'name': 'PowerShell', 'arguments': {'command': 'dir'}})
                self.assertTrue(refused['result']['isError'])
                failed = self.rpc(gateway, 'tools/call', {'name': 'Click', 'arguments': {'loc': [1, 1]}})['result']
                self.assertIn('element 7 not found', failed['content'][0]['text'])
                self.assertEqual([name for name, _ in gateway.worker.calls], ['Snapshot', 'Click'])
                with self.assertRaises(urllib.error.HTTPError):
                    self.rpc(gateway, 'tools/list', token='wrong')
                gateway.stop()
                stopped = self.rpc(gateway, 'tools/call', {'name': 'Snapshot', 'arguments': {}})['result']
                self.assertTrue(stopped['isError'])
            log = (Path(tmp) / 'log.jsonl').read_text(encoding='utf-8')
            self.assertIn('element 7 not found', log)


class HostRunTests(unittest.TestCase):
    def test_host_config_turns_off_grep_question_and_project_tooling(self):
        config = ex.build_config({'model': 'm'}, 't', 'http://x', host_mcp={'veil_browser': {'type': 'remote'}})
        self.assertEqual((config['permission']['grep'], config['permission']['question']), ('deny', 'deny'))
        self.assertEqual(config['permission']['veil_browser_*'], 'allow')
        self.assertEqual(config['mcp'], {'veil_browser': {'type': 'remote'}})
        self.assertIs(config['formatter'], False)
        old = ex.build_config({'model': 'm'}, 't', 'http://x')
        self.assertNotIn('grep', old['permission'])

    def test_checkpoint_never_runs_with_container_or_old_gateways(self):
        for extra in ({'container': {'image': 'x'}}, {'browser_gateway': object()}, {'windows_gateway': object()}):
            costs = []
            result = ex.run('t', command='docker', workdir=tempfile.mkdtemp(), home=tempfile.mkdtemp(),
                            provider={'model': 'm', 'base_url': 'http://x'}, key='k', limits={}, autonomy=False,
                            ask=lambda _p: 'n', stopped=lambda: False, record_cost=costs.append,
                            checkpoint=object(), **extra)
            self.assertEqual((result.state, result.reason, costs), ('被拒绝', 'invalid_host_run', [Decimal(0)]))

    def test_host_opencode_never_loads_project_plugins_or_config(self):
        seen = {}

        def popen(command, **kwargs):
            seen.update(kwargs['env'])
            raise OSError('stop here')
        with tempfile.TemporaryDirectory() as tmp:
            cp, work, _ = make_checkpoint(Path(tmp))
            proxy = SimpleNamespace(token='t', port=1, local_base=lambda host: 'http://x', close=lambda: None,
                                    allow_hosts=lambda hosts: None, local_health=lambda host: '')
            ex.run('t', command='opencode', workdir=work, home=Path(tmp) / 'home',
                   provider={'model': 'm', 'base_url': 'http://x'}, key='k', limits={}, autonomy=False,
                   ask=lambda _p: 'n', stopped=lambda: False, record_cost=lambda _c: None,
                   proxy_factory=lambda *a, **k: proxy, popen=popen, checkpoint=cp)
        for flag in ('OPENCODE_PURE', 'OPENCODE_DISABLE_PROJECT_CONFIG', 'OPENCODE_DISABLE_CLAUDE_CODE',
                     'OPENCODE_DISABLE_EXTERNAL_SKILLS'):
            self.assertEqual(seen.get(flag), '1', flag)
        config = json.loads(seen['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(config['permission']['read']['*.pem'], 'deny')
        self.assertTrue(config['experimental']['continue_loop_on_deny'])
        self.assertEqual(config['provider']['relay']['options']['apiKey'], 't')

    def test_gateway_start_failure_is_reported_not_raised(self):
        class Broken:
            server_id = 'veil_browser'

            def __enter__(self):
                raise RuntimeError('npx not found')

            def __exit__(self, *_):
                pass
        costs = []
        with tempfile.TemporaryDirectory() as tmp:
            cp, work, _ = make_checkpoint(Path(tmp))
            proxy = SimpleNamespace(token='t', port=1, local_base=lambda host: 'http://x', close=lambda: None,
                                    allow_hosts=lambda hosts: None, local_health=lambda host: '')
            result = ex.run('t', command='opencode', workdir=work, home=Path(tmp) / 'home',
                            provider={'model': 'm', 'base_url': 'http://x'}, key='k', limits={}, autonomy=False,
                            ask=lambda _p: 'n', stopped=lambda: False, record_cost=costs.append,
                            proxy_factory=lambda *a, **k: proxy, checkpoint=cp, host_gateways=[Broken()])
        self.assertEqual((result.state, result.reason), ('出错', 'gateway_failed'))
        self.assertIn('npx not found', result.text)

    def test_upstream_services_are_pinned_and_desktop_is_windows_only(self):
        which = lambda name: '/bin/' + name
        self.assertNotIn(vc.BROWSER_SERVER, host_executor.upstream_services(tempfile.mkdtemp(), which=which))
        services = host_executor.upstream_services(tempfile.mkdtemp(), which=which, proxy_port=4321)
        browser = services[vc.BROWSER_SERVER][0]['args']
        self.assertEqual(browser[browser.index('--proxy-server') + 1], 'http://127.0.0.1:4321')
        # No `<`/`>` on the command line: cmd.exe (npx is a .cmd) would read them as redirections.
        self.assertFalse(any(ch in arg for arg in browser for ch in '<>|&^'), browser)
        settings = json.loads(Path(browser[browser.index('--config') + 1]).read_text(encoding='utf-8'))
        self.assertIn('--webrtc-ip-handling-policy=disable_non_proxied_udp', settings['browser']['launchOptions']['args'])
        self.assertIn('@playwright/mcp@' + host_executor.PINNED['playwright-mcp'], browser)
        self.assertIn('--headless', browser)
        self.assertIn('--isolated', browser)
        with patch.object(host_executor.os, 'name', 'nt'):
            only_uvx = lambda name: '/bin/uvx' if name == 'uvx' else None
            desktop = host_executor.upstream_services('h', which=only_uvx)[vc.COMPUTER_SERVER][0]['args']
        self.assertEqual(desktop[:4], ['--python', '3.14', '--from', 'windows-mcp==0.8.8'])
        self.assertEqual(desktop[-1].split(','), sorted(vc.COMPUTER_TOOLS))
        self.assertNotIn('PowerShell', desktop[-1])
        self.assertEqual(host_executor.upstream_services(tempfile.mkdtemp(), which=lambda name: None), {})


class BrowserNetworkTests(unittest.TestCase):
    def proxy_answer(self, port, request):
        import socket
        with socket.create_connection(('127.0.0.1', port), timeout=10) as sock:
            sock.sendall(request)
            sock.settimeout(10)
            try:
                return sock.recv(200)
            except OSError:
                return b''

    def test_browser_cannot_reach_local_network_through_its_proxy(self):
        network = host_executor.BrowserNetwork()
        try:
            for target in (b'127.0.0.1:443', b'192.168.1.1:443', b'localhost:443', b'10.0.0.1:443',
                           b'example.com:80'):
                answer = self.proxy_answer(network.port, b'CONNECT ' + target + b' HTTP/1.1\r\nHost: x\r\n\r\n')
                self.assertNotIn(b'200', answer, target)
            plain = self.proxy_answer(network.port, b'GET http://192.168.1.1/ HTTP/1.1\r\nHost: 192.168.1.1\r\n\r\n')
            self.assertEqual(plain, b'')
        finally:
            network.close()
        self.assertFalse(network.thread.is_alive())

    def test_gateway_closes_the_browser_network_even_when_never_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, _, _ = make_checkpoint(Path(tmp))
            closed = []
            gateway = VeilMcpGateway(vc.BROWSER_SERVER, {'command': 'x'}, cp, vc.BROWSER_TOOLS,
                                     should_stop=lambda: False, worker_factory=FakeWorker,
                                     companions=[SimpleNamespace(close=lambda: closed.append(1))])
            gateway.__exit__(None, None, None)
            gateway.__exit__(None, None, None)
        self.assertEqual(closed, [1])

    def test_run_closes_host_gateways_on_every_early_return(self):
        closed = []
        gateway = SimpleNamespace(__exit__=lambda *a: closed.append(1), server_id='veil_browser')
        result = ex.run('t', command='opencode', workdir=Path('.'), home=Path('.'), provider={}, key='k', limits={},
                        autonomy=False, ask=lambda _p: 'n', stopped=lambda: False, record_cost=lambda _c: None,
                        container=object(), checkpoint=object(), host_gateways=[gateway])
        self.assertEqual(result.reason, 'invalid_host_run')
        self.assertTrue(closed)


if __name__ == '__main__':
    unittest.main()
