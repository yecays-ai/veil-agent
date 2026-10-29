"""Offline account, transport and safety scenarios; no real cookies or paid API."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools_agent import FilesystemTools, permitted_tools, tool_policy
from xiaohongshu_tools import (MAX_DAILY, PAUSED_TEXT, Cooldown, Sidecar, TOOL, Transient, XiaohongshuTools,
                              account_lock, companion_env, private_folder, stop_process_tree)


class XiaohongshuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cfg = SimpleNamespace(id='peter', web={'enabled': True, 'xiaohongshu_readonly': True}, tools={})
        self.folder = private_folder(self.root, 'peter')
        (self.folder / 'account.json').write_text('{"user_id":"dedicated"}', encoding='utf-8')
        self.sidecar = Mock()
        self.sidecar.request.return_value = {'is_logged_in': True, 'user_id': 'dedicated', 'username': 'Peter'}
        self.now = 2_000_000_000
        self.tools = XiaohongshuTools(self.root, self.cfg, sidecar=self.sidecar, clock=lambda: self.now)

    def invoke(self, **args):
        return self.tools.invoke('xiaohongshu', args)

    def test_wrong_or_missing_account_never_reaches_feed(self):
        for status in [{'is_logged_in': False}, {'is_logged_in': True, 'user_id': 'personal'},
                       {'is_logged_in': True}]:
            self.tools.blocked = False
            self.now += 20
            self.sidecar.request.reset_mock()
            self.sidecar.request.return_value = status
            self.assertEqual(self.invoke(action='feed')[1], '出错')
            self.sidecar.request.assert_called_once_with('GET', '/api/v1/login/status', None)
            self.assertTrue(self.tools.blocked)
        (self.folder / 'account.json').unlink()
        self.sidecar.request.reset_mock()
        self.assertEqual(self.invoke(action='feed')[1], '出错')
        self.sidecar.request.assert_not_called()

    def test_schema_and_runtime_refuse_all_writes_and_custom_endpoints(self):
        self.assertEqual(tool_policy('xiaohongshu'), 'allow')
        self.assertEqual(len(permitted_tools([TOOL])), 1)
        for args in [{'action': action} for action in ['publish', 'comment', 'message', 'follow', 'like', 'login']]+[
            {'action': 'feed', 'url': 'http://127.0.0.1/publish'},
            {'action': 'search', 'query': 'x', 'cookies': 'secret'},
            {'action': 'detail', 'note_ref': 'invented'},
        ]:
            self.assertEqual(self.invoke(**args)[1], '被拒绝')
        self.sidecar.start.assert_not_called()
        real = Sidecar(self.root, 'peter')
        for method, path in [('POST', '/api/v1/publish'), ('GET', '/mcp'), ('POST', '/api/v1/feeds/comment')]:
            with self.assertRaises(ValueError):
                real.request(method, path)

    def test_budget_persists_and_failed_calls_consume_quota(self):
        self.assertEqual(self.invoke(action='status')[1], '成功')
        self.tools = XiaohongshuTools(self.root, self.cfg, sidecar=self.sidecar, clock=lambda: self.now)
        with self.assertRaises(Cooldown):
            self.tools._reserve()
        self.assertEqual(self.sidecar.request.call_count, 1)
        self.now += 11
        (self.folder / 'read-budget.json').write_text(json.dumps({
            'day': __import__('datetime').datetime.fromtimestamp(self.now).strftime('%Y-%m-%d'),
            'count': MAX_DAILY, 'last': self.now - 20}), encoding='utf-8')
        self.assertEqual(self.invoke(action='status')[1], '被拒绝')
        self.assertEqual(self.sidecar.request.call_count, 1)

    def test_corrupted_budget_fails_closed(self):
        (self.folder / 'read-budget.json').write_text('broken', encoding='utf-8')
        self.assertEqual(self.invoke(action='status')[1], '出错')
        self.sidecar.request.assert_not_called()

    def test_sequential_reads_wait_for_cooldown_without_model_retry(self):
        tools = self.tools
        class Cancelled:
            def is_set(self): return False
            def wait(self, seconds):
                self_outer.now += seconds
                return False
        self_outer = self
        tools._read('GET', '/api/v1/login/status', None, Cancelled())
        tools._read('GET', '/api/v1/login/status', None, Cancelled())
        self.assertEqual(self.sidecar.request.call_count, 2)
        self.assertGreaterEqual(json.loads((self.folder / 'read-budget.json').read_text())['last'], 2_000_000_010)

    def test_feed_to_detail_round_trip_uses_server_side_token_and_limits_comments(self):
        note = {'id': 'a'*24, 'xsecToken': 'PRIVATE_XSEC', 'noteCard': {
            'displayTitle': 'Trail notes', 'user': {'nickname': 'Explorer'}}}
        self.sidecar.request.side_effect = [
            {'is_logged_in': True, 'user_id': 'dedicated'}, {'feeds': [note]*12},
            {'is_logged_in': True, 'user_id': 'dedicated'},
            {'data': {'note': {'title': 'Trail notes', 'desc': 'Body', 'xsecToken': 'PRIVATE_XSEC'},
                      'comments': {'list': [{'content': 'A comment', 'userInfo': {}, 'subComments': []}]*30}}},
        ]
        with patch('xiaohongshu_tools.MIN_INTERVAL', 0):
            text, state, _ = self.invoke(action='search', query='trails')
            self.assertEqual(state, '成功', text)
            self.assertNotIn('PRIVATE_XSEC', text)
            data = json.loads(text.split('\n', 1)[1])
            self.assertEqual(len(data['notes']), 6)
            text, state, _ = self.invoke(action='detail', note_ref=data['notes'][0]['note_ref'])
            self.assertEqual(state, '成功', text)
            self.assertEqual(len(json.loads(text.split('\n', 1)[1])['comments']), 10)
            body = self.sidecar.request.call_args.args[2]
            self.assertFalse(body['load_all_comments'])
            self.assertFalse(body['comment_config']['click_more_replies'])
            self.assertNotIn('PRIVATE_XSEC', text)

    def test_status_and_content_fit_byte_budget(self):
        text, state, _ = self.invoke(action='status')
        self.assertEqual(state, '成功')
        self.assertIn('disabled', text)
        self.assertNotIn('dedicated"', text)
        self.assertLessEqual(len(text.encode()), 8192)
        self.cfg.tools = {'max_result_bytes': 64}
        bounded = XiaohongshuTools(self.root, self.cfg, sidecar=self.sidecar, clock=lambda: self.now)
        self.now += 20
        text, state, _ = bounded.invoke('xiaohongshu', {'action': 'status'})
        self.assertEqual(state, '成功')
        self.assertLessEqual(len(text.encode()), 64)

    def test_four_operations_per_turn_and_disabled_role(self):
        for _ in range(4):
            self.now += 20
            self.assertEqual(self.invoke(action='status')[1], '成功')
        self.now += 20
        self.assertEqual(self.invoke(action='status')[1], '被拒绝')
        self.tools.begin_request()
        self.assertEqual(self.invoke(action='status')[1], '成功')
        cfg = SimpleNamespace(id='ghost', web={}, tools={})
        ghost = XiaohongshuTools(self.root, cfg, sidecar=self.sidecar)
        self.assertFalse(ghost.tools)
        self.assertEqual(ghost.invoke('xiaohongshu', {'action': 'status'})[1], '被拒绝')

    def test_stop_cancels_inflight_read_and_blocks_retry(self):
        entered = threading.Event()
        stop = threading.Event()
        def slow(*args):
            entered.set()
            stop.wait(2)
            time.sleep(.2)
            return {'is_logged_in': True, 'user_id': 'dedicated'}
        self.sidecar.request.side_effect = slow
        def request_stop():
            entered.wait(2)
            stop.set()
        helper = threading.Thread(target=request_stop)
        helper.start()
        result = self.tools.invoke('xiaohongshu', {'action': 'status'}, should_stop=stop.is_set)
        helper.join()
        self.assertEqual(result[1], '出错')
        self.sidecar.close.assert_called()
        self.assertTrue(self.tools.blocked)
        self.assertEqual(self.sidecar.request.call_count, 1)

    def test_slow_or_unreachable_read_does_not_pause_later_reads(self):
        # Ticket 158: a timeout on Lan's PC is not a login or risk-control problem.
        self.sidecar.request.side_effect = Transient('The Xiaohongshu companion did not answer in time.')
        text, state, _ = self.invoke(action='status')
        self.assertEqual(state, '出错')
        self.assertIn('not a login or risk-control problem', text)
        self.assertFalse(self.tools.blocked)
        self.sidecar.request.side_effect = None
        self.sidecar.request.return_value = {'is_logged_in': True, 'user_id': 'dedicated', 'username': 'Peter'}
        self.now += 20
        self.assertEqual(self.invoke(action='status')[1], '成功')
        rows = [json.loads(line) for line in (self.folder / 'errors.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row['kind'] for row in rows], ['transient'])

    def test_whole_operation_deadline_stops_the_read_without_pausing(self):
        release = threading.Event()
        def slow(*args):
            release.wait(2)
            raise Transient('closed')
        self.sidecar.request.side_effect = slow
        self.sidecar.close.side_effect = release.set
        with patch('xiaohongshu_tools.OPERATION_TIMEOUT', 0.2):
            text, state, _ = self.invoke(action='status')
        self.assertEqual(state, '出错')
        self.assertIn('too slow', text)
        self.assertFalse(self.tools.blocked)

    def test_repeated_page_failures_pause_to_protect_the_account(self):
        # Ticket 159: one slow page is fine, three in a row may be risk control.
        self.sidecar.request.side_effect = Transient('Xiaohongshu page did not load (SEARCH_FEEDS_FAILED).')
        for _ in range(2):
            text, _, _ = self.invoke(action='status')
            self.assertIn('not a login or risk-control problem', text)
            self.assertFalse(self.tools.blocked)
            self.now += 20
        text, _, _ = self.invoke(action='status')
        self.assertIn('3 times in a row', text)
        self.assertTrue(self.tools.blocked)

    def test_success_resets_the_page_failure_count(self):
        for _ in range(2):
            self.sidecar.request.side_effect = Transient('slow')
            self.invoke(action='status')
            self.now += 20
            self.sidecar.request.side_effect = None
            self.sidecar.request.return_value = {'is_logged_in': True, 'user_id': 'dedicated', 'username': 'Peter'}
            self.assertEqual(self.invoke(action='status')[1], '成功')
            self.now += 20
        self.assertEqual(self.tools.transient_streak, 0)
        self.assertFalse(self.tools.blocked)

    def test_upstream_page_failure_codes_are_transient_and_others_are_labelled(self):
        import http.server
        replies = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = replies.pop(0)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        real = Sidecar(self.root, 'peter')
        real.port, real.token = server.server_address[1], 'test-token'
        replies.append((500, {'error': 'x', 'code': 'STATUS_CHECK_FAILED', 'details': 'C:/Users/secret token'}))
        with self.assertRaises(Transient) as caught:
            real.request('GET', '/api/v1/login/status')
        self.assertNotIn('secret', str(caught.exception))
        replies.append((401, {'error': 'x', 'code': 'UNAUTHORIZED', 'details': 'secret'}))
        with self.assertRaises(ValueError) as caught:
            real.request('GET', '/api/v1/login/status')
        self.assertNotIsInstance(caught.exception, Transient)
        self.assertIn('HTTP 401 UNAUTHORIZED', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    def test_login_check_page_failure_keeps_existing_binding(self):
        from xiaohongshu_setup import login_and_bind
        self.sidecar.request.side_effect = Transient('Xiaohongshu page did not load (STATUS_CHECK_FAILED).')
        with patch('builtins.input', side_effect=['']), patch('builtins.print') as printed, \
                patch('xiaohongshu_setup.binary_path', return_value=self.root / 'fake.exe'), \
                patch('xiaohongshu_setup.subprocess.Popen') as process, \
                patch('xiaohongshu_setup.Sidecar', return_value=self.sidecar):
            process.return_value.wait.return_value = 0
            login_and_bind(self.root, self.folder)
        self.assertIn('不是登录问题', ' '.join(str(c) for c in printed.call_args_list))
        self.assertEqual(json.loads((self.folder / 'account.json').read_text())['user_id'], 'dedicated')

    def test_logged_out_account_pauses_reads_with_a_clear_reason(self):
        self.sidecar.request.return_value = {'is_logged_in': False}
        text, state, _ = self.invoke(action='status')
        self.assertIn('logged out', text)
        self.assertTrue(self.tools.blocked)
        self.now += 20
        text, _, _ = self.invoke(action='status')
        self.assertEqual(PAUSED_TEXT, text)

    def test_expired_note_ref_is_rejected_without_pausing(self):
        self.tools.refs['old'] = ('a' * 24, 'token', self.now - 4000)
        text, state, _ = self.invoke(action='detail', note_ref='old')
        self.assertEqual(state, '被拒绝')
        self.assertIn('expired', text)
        self.assertFalse(self.tools.blocked)

    def test_worker_that_never_stops_pauses_and_says_so(self):
        hold = threading.Event()
        self.addCleanup(hold.set)
        self.sidecar.request.side_effect = lambda *args: hold.wait(10)
        with patch('xiaohongshu_tools.OPERATION_TIMEOUT', 0.2):
            text, state, _ = self.invoke(action='status')
        self.assertEqual(state, '出错')
        self.assertIn('paused', text)
        self.assertTrue(self.tools.blocked)

    def test_account_lock_excludes_parallel_login_and_read(self):
        with account_lock(self.folder):
            with self.assertRaises(ValueError):
                with account_lock(self.folder):
                    self.fail('second session entered')

    def test_human_relogin_keeps_account_binding_and_daily_budget(self):
        from xiaohongshu_setup import login_and_bind
        cookies = self.folder / 'cookies.json'
        cookies.write_text('fictional cookies', encoding='utf-8')
        quota = self.folder / 'read-budget.json'
        quota.write_text('{"count":60}', encoding='utf-8')
        with patch('builtins.input', side_effect=['RELOGIN', 'dedicated']), patch('builtins.print'), \
                patch('xiaohongshu_setup.binary_path', return_value=self.root / 'fake.exe'), \
                patch('xiaohongshu_setup.subprocess.Popen') as process, \
                patch('xiaohongshu_setup.Sidecar', return_value=self.sidecar):
            process.return_value.wait.return_value = 0
            login_and_bind(self.root, self.folder)
        self.assertFalse(cookies.exists())
        self.assertEqual(json.loads((self.folder / 'account.json').read_text())['user_id'], 'dedicated')
        self.assertEqual(json.loads(quota.read_text())['count'], 60)
        process.assert_called_once()

    def test_login_timeout_uses_process_tree_cleanup(self):
        from xiaohongshu_setup import login_and_bind
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired('fake-login', 600)
        with patch('builtins.print'), \
                patch('xiaohongshu_setup.binary_path', return_value=self.root / 'fake.exe'), \
                patch('xiaohongshu_setup.subprocess.Popen', return_value=process), \
                patch('xiaohongshu_setup.stop_process_tree') as cleanup:
            with self.assertRaises(subprocess.TimeoutExpired):
                login_and_bind(self.root, self.folder)
        cleanup.assert_called_once_with(process)
        self.assertEqual(json.loads((self.folder / 'account.json').read_text())['user_id'], 'dedicated')

    def test_sidecar_closes_before_account_lock_is_released_on_success_or_error(self):
        def closing():
            with self.assertRaises(ValueError):
                with account_lock(self.folder):
                    self.fail('account lock was released before sidecar close')
        self.sidecar.close.side_effect = closing
        self.tools._operation({'action': 'status'}, threading.Event())
        self.now += 20
        self.sidecar.request.side_effect = ValueError('failed read')
        with self.assertRaises(ValueError):
            self.tools._operation({'action': 'status'}, threading.Event())
        self.assertEqual(self.sidecar.close.call_count, 2)
        with account_lock(self.folder):
            pass

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree cleanup')
    def test_real_windows_cleanup_stops_owned_child_too(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        pid_file = self.root / 'owned-child.txt'
        code = ("import subprocess,sys,time; from pathlib import Path; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                "creationflags=subprocess.CREATE_NO_WINDOW); "
                "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)")
        parent = subprocess.Popen([sys.executable, '-c', code, str(pid_file)],
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        handle = None
        try:
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(pid_file.exists(), 'owned child did not start')
            handle = kernel.OpenProcess(0x100001, False, int(pid_file.read_text()))
            self.assertTrue(handle)
            stop_process_tree(parent)
            self.assertIsNotNone(parent.poll())
            self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
        finally:
            stop_process_tree(parent)
            if handle:
                if kernel.WaitForSingleObject(handle, 0) == 258:
                    kernel.TerminateProcess(handle, 1)
                kernel.CloseHandle(handle)

    def test_companion_never_inherits_model_keys_or_browser_overrides(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'GITHUB_TOKEN': 'secret',
                                    'ROD': 'url=http://personal-browser', 'HTTP_PROXY': 'secret',
                                    'AUTH_TOKEN': 'old', 'COOKIES_PATH': 'personal-cookies'}):
            env = companion_env(self.folder)
        for key in ['OPENAI_API_KEY', 'GITHUB_TOKEN', 'ROD', 'HTTP_PROXY', 'AUTH_TOKEN']:
            self.assertNotIn(key, env)
        self.assertEqual(env['COOKIES_PATH'], str(self.folder / 'cookies.json'))

    def test_private_cookie_folder_cannot_be_exposed_as_file_workspace(self):
        from agent_types import UserError
        cfg = SimpleNamespace(id='peter', web=self.cfg.web,
                              tools={'folder': 'data/peter'}, screen_pop=None,
                              windows_interaction=None, desktop_file_read=None,
                              windows_media=None, windows_observation=None)
        store = SimpleNamespace(root=self.root / 'data')
        with self.assertRaises(UserError):
            FilesystemTools(self.root, cfg, store)

    def test_unexpected_shape_and_service_error_do_not_report_success(self):
        with self.assertRaises(ValueError):
            self.tools._normalize({'data': {}}, 'detail')
        empty = {'data': {'note': {'desc': 'No comments yet'}, 'comments': {'list': None}}}
        self.assertEqual(self.tools._normalize(empty, 'detail')['comments'], [])
        empty['data']['comments']['list'] = [{'content': 'No replies', 'userInfo': {}, 'subComments': None}]
        self.assertEqual(self.tools._normalize(empty, 'detail')['comments'][0]['replies'], [])
        self.sidecar.request.side_effect = ValueError('sensitive upstream error /secret/path?token=secret')
        text, state, _ = self.invoke(action='status')
        self.assertEqual(state, '出错')
        self.assertNotIn('secret', text)
        self.assertTrue(self.tools.blocked)
        self.now += 20
        self.invoke(action='status')
        self.assertEqual(self.sidecar.request.call_count, 1)

    def test_autonomous_wake_reads_then_saves_local_draft_without_publishing(self):
        from veil_agent import ActivityState, Budget, Character, Chat, Store
        from autonomy import AutonomyController
        from web_agent import Hub
        store = Store(self.root / 'data')
        cfg = Character('peter', 'Test', 'Fictional persona', 20, 100,
                        {'model': 'fake', 'max_input_tokens': 30000,
                         'input_cny_per_million': Decimal('0'), 'cached_input_cny_per_million': Decimal('0'),
                         'output_cny_per_million': Decimal('0')}, 'fake',
                        tools={'enabled': True, 'max_steps': 8, 'max_result_bytes': 8192,
                               'folder': 'data/peter/workspace'}, web=self.cfg.web,
                        autonomy={'interval_minutes': 90, 'daily_max_runs': 12,
                                  'daily_budget_cny': Decimal('3'), 'min_alarm_gap_minutes': 15})
        with patch('tools_agent.shutil.which', return_value='fake-node'), patch(
                'tools_agent._McpWorker', return_value=SimpleNamespace(tools=[SimpleNamespace(
                    name='write_file', description='Write a file', inputSchema={'type': 'object', 'properties': {
                        'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']})],
                    close=lambda: None)):
            manager = FilesystemTools(self.root, cfg, store)
        self.addCleanup(manager.__exit__, None)
        manager.xiaohongshu_tools = self.tools
        self.assertIn('xiaohongshu', manager.capability_record)
        self.sidecar.request.side_effect = [
            {'is_logged_in': True, 'user_id': 'dedicated'},
            {'feeds': [{'id': 'a'*24, 'xsecToken': 'PRIVATE_XSEC', 'noteCard': {'displayTitle': 'Trails'}}]},
        ]
        draft = '# Local draft\nTitle: A trail idea\nBody: Original text\nImage plan: My own drawing\nNot published.'
        requests = [('xiaohongshu', {'action': 'search', 'query': 'trails'}),
                    ('write_file', {'path': 'lab/xiaohongshu-draft.md', 'content': draft}),
                    ('xiaohongshu', {'action': 'publish'})]
        seen = []
        def model(messages, **kwargs):
            index = len(seen)
            seen.append(messages)
            if index < len(requests):
                name, args = requests[index]
                call = SimpleNamespace(id=f'call-{index}', function=SimpleNamespace(
                    name=name, arguments=json.dumps(args)))
                reply = SimpleNamespace(tool_calls=[call], content=None,
                    model_dump=lambda **_: {'role': 'assistant', 'tool_calls': []})
            else:
                reply = SimpleNamespace(tool_calls=[], content='Local draft saved. Publishing is disabled.')
            return reply, 100, 20, 0
        activity = ActivityState(store.root / 'peter' / 'resident-state.json')
        activity.set(True)
        hub = Hub('Test', activity)
        chat = Chat(cfg, store, Budget(store), model, tools=manager, output=hub.output)
        with patch('xiaohongshu_tools.MIN_INTERVAL', 0):
            AutonomyController(chat, hub).run()
        self.assertEqual((manager.lab / 'xiaohongshu-draft.md').read_text(encoding='utf-8'), draft)
        rows = [r for r in store.read('peter/tools.jsonl') if r.get('state') != '进行中']
        self.assertEqual([r['state'] for r in rows], ['成功', '成功', '被拒绝'])
        self.assertEqual(self.sidecar.request.call_count, 2)
        self.assertNotIn('PRIVATE_XSEC', json.dumps(seen))


if __name__ == '__main__':
    unittest.main()
