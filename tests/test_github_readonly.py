import base64
import copy
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

from github_readonly import GitHubReadTools, MCP_URL, TOKEN_ENV, official_code_search, public_get
from tools_agent import FilesystemTools, permitted_tools, tool_policy
from veil_agent import ActivityState, Budget, Character, Chat, Store, load_character
from autonomy import AutonomyController
from desktop_files import DesktopFileTools
from web_agent import Hub

SHA = 'a' * 40
TREE = 'b' * 40
SOURCE = 'def wake():\n    return "Explore"\n'


def fixture_get(path, params=None):
    if path == '/repos/example/companion':
        return {'private': False, 'visibility': 'public', 'default_branch': 'main'}
    if '/commits/' in path:
        return {'sha': SHA, 'commit': {'tree': {'sha': TREE}}}
    if '/git/trees/' in path:
        return {'truncated': False, 'tree': [
            {'path': 'wake.py', 'type': 'blob', 'mode': '100644', 'size': len(SOURCE)}]}
    if '/contents/' in path:
        return {'type': 'file', 'encoding': 'base64', 'size': len(SOURCE),
                'content': base64.b64encode(SOURCE.encode()).decode()}
    return []


def config(enabled=True, max_bytes=8192):
    return SimpleNamespace(tools={'max_result_bytes': max_bytes}, web={
        'github_readonly': enabled, 'github_repository': 'example/companion'})


class GitHubBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.get = Mock(side_effect=fixture_get)
        self.mcp = Mock(return_value='indexed code results')
        self.tools = GitHubReadTools(config(), get=self.get, mcp_search=self.mcp)
        self.args = {'repository': 'example/companion', 'resource': 'file', 'path': 'wake.py'}

    def test_source_has_commit_permalink_and_actual_line_numbers(self):
        result, state, _ = self.tools.invoke('github_read', self.args)
        self.assertEqual(state, '成功', result)
        self.assertIn('/blob/' + SHA + '/wake.py', result)
        self.assertIn('2:     return "Explore"', result)
        self.assertIn('Untrusted', result)
        self.assertEqual(self.get.call_args.args[1], {'ref': SHA})

    def test_core_source_larger_than_100kb_can_be_read_in_slices(self):
        large = ('# source\n' * 23_000).encode()
        def get(path, params=None):
            if '/contents/' in path:
                return {'type': 'file', 'encoding': 'base64', 'size': len(large),
                        'content': base64.b64encode(large).decode()}
            return fixture_get(path, params)
        tools = GitHubReadTools(config(), get=get)
        result, state, _ = tools.invoke('github_read', self.args)
        self.assertEqual(state, '成功', result)
        self.assertIn('1: # source', result)
        self.assertIn('next_offset=', result)
        self.assertLessEqual(len(result.encode()), 8192)

    def test_unknown_writes_and_parameter_injection_never_contact_network(self):
        cases = [('create_issue', self.args), ('push_files', self.args)]
        cases += [('github_read', {**self.args, 'resource': r}) for r in (
            'create_issue', 'create_pull_request', 'push', 'merge', 'delete')]
        cases += [('github_read', {**self.args, k: v}) for k, v in (
            ('method', 'POST'), ('url', 'http://localhost'), ('body', 'payload'),
            ('tool', 'create_pull_request'))]
        cases += [('github_read', {**self.args, k: v}) for k, v in (
            ('repository', '../private'), ('repository', 'https://github.com/x/y'),
            ('path', '../secret'), ('path', '%2e%2e/secret'), ('path', 'x?method=POST'),
            ('offset', True), ('page', -1))]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                self.assertEqual(self.tools.invoke(name, args)[1], '被拒绝')
        self.get.assert_not_called()
        self.mcp.assert_not_called()
        for name in ('create_issue', 'create_pull_request', 'push_files', 'merge_pull_request'):
            self.assertEqual(tool_policy(name), 'deny')
        self.assertEqual(permitted_tools([SimpleNamespace(name='create_issue')]), [])

    def test_role_opt_out_and_private_repositories_are_refused(self):
        disabled = GitHubReadTools(config(False), get=self.get)
        self.assertEqual(disabled.tools, [])
        self.assertEqual(disabled.invoke('github_read', self.args)[1], '被拒绝')
        self.get.assert_not_called()
        self.get.side_effect = None
        self.get.return_value = {'private': True, 'default_branch': 'main'}
        with patch.dict(os.environ, {TOKEN_ENV: 'fictional-readonly-secret'}):
            self.assertEqual(self.tools.invoke('github_read', {
                **self.args, 'resource': 'search_code', 'query': 'wake'})[1], '被拒绝')
        self.mcp.assert_not_called()
        self.assertEqual(self.get.call_count, 1)

    def test_anonymous_literal_search_returns_evidence_and_scan_coverage(self):
        with patch.dict(os.environ, {}, clear=True):
            result, state, _ = self.tools.invoke('github_read', {
                **self.args, 'path': '', 'resource': 'search_code', 'query': 'Explore'})
        self.assertEqual(state, '成功', result)
        self.assertIn('wake.py#L2', result)
        self.assertIn('not a whole-repository negative result', result)
        self.assertIn('scanned', result)
        self.mcp.assert_not_called()

    def test_official_search_receives_only_scoped_literal_query_and_dedicated_token(self):
        with patch.dict(os.environ, {TOKEN_ENV: 'fictional-readonly-secret', 'GITHUB_TOKEN': 'developer-secret'}):
            result, state, _ = self.tools.invoke('github_read', {
                'repository': 'example/companion', 'resource': 'search_code', 'query': 'wake'})
        self.assertEqual(state, '成功', result)
        self.mcp.assert_called_once_with('"wake" repo:example/companion', 1, 'fictional-readonly-secret')
        self.assertNotIn('secret', result)
        for query in ('wake repo:private/secret', 'wake OR secret', 'wake" repo:elsewhere/repo', 'wake\\', 'wake\tsecret'):
            self.assertEqual(self.tools.invoke('github_read', {
                **self.args, 'resource': 'search_code', 'query': query})[1], '被拒绝')
        self.assertEqual(MCP_URL, 'https://api.githubcopilot.com/mcp/readonly')

    def test_pinned_search_uses_anonymous_scan_even_if_token_exists(self):
        with patch.dict(os.environ, {TOKEN_ENV: 'fictional-readonly-secret'}):
            result, state, _ = self.tools.invoke('github_read', {
                **self.args, 'path': '', 'ref': SHA, 'resource': 'search_code', 'query': 'wake'})
        self.assertEqual(state, '成功', result)
        self.mcp.assert_not_called()
        self.assertIn(SHA, result)

    def test_paginated_utf8_output_can_be_reassembled_without_losing_characters(self):
        tools = GitHubReadTools(config(max_bytes=700), get=self.get)
        value = '源代码😀' * 200
        offset, collected = 0, ''
        for _ in range(30):
            result = tools._page(value, 'https://github.com/example/companion', offset)
            self.assertLessEqual(len(result.encode('utf-8')), 700)
            body = result.split('\n', 2)[2]
            content = body.rsplit('\n[', 1)[0]
            collected += content
            if '[End of this result.]' in result:
                break
            offset = int(result.split('next_offset=')[1].split(';')[0])
        self.assertEqual(collected, value)

    def test_comments_pr_files_commits_and_search_use_fixed_get_endpoints(self):
        for resource, endpoint in (
            ('issue_comments', '/issues/7/comments'), ('pull_files', '/pulls/7/files'),
            ('pull_comments', '/issues/7/comments'), ('commits', '/commits')):
            result, state, _ = self.tools.invoke('github_read', {
                **self.args, 'resource': resource, 'number': 7, 'page': 2})
            self.assertEqual(state, '成功', result)
            self.assertEqual(self.get.call_args.args[0], '/repos/example/companion' + endpoint)
            self.assertEqual(self.get.call_args.args[1]['page'], 2)
        self.assertEqual(self.tools.invoke('github_read', {
            'repository': 'example/companion', 'resource': 'issue'})[1], '被拒绝')

    def test_rate_limit_network_errors_and_mcp_errors_do_not_leak_secrets(self):
        for error in (HTTPError('https://api.github.com', 403, 'limited', {}, None),
                      TimeoutError('secret should not be printed')):
            self.get.side_effect = error
            result, state, _ = self.tools.invoke('github_read', self.args)
            self.assertEqual(state, '出错')
            self.assertNotIn('secret', result)
        self.get.side_effect = fixture_get
        self.mcp.side_effect = RuntimeError('Bearer fictional-readonly-secret')
        with patch.dict(os.environ, {TOKEN_ENV: 'fictional-readonly-secret'}):
            result, state, _ = self.tools.invoke('github_read', {
                'repository': 'example/companion', 'resource': 'search_code', 'query': 'wake'})
        self.assertEqual(state, '出错')
        self.assertNotIn('fictional-readonly-secret', result)

    def test_transport_is_get_only_without_authorization_and_refuses_redirects(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = b'{"private": false}'
        opener = Mock()
        opener.open.return_value = response
        with patch('github_readonly.build_opener', return_value=opener), patch.dict(
                os.environ, {'GITHUB_TOKEN': 'developer-secret', TOKEN_ENV: 'read-only-secret'}):
            public_get('/repos/example/companion')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertFalse(request.has_header('Authorization'))
        self.assertIsNone(request.data)
        from github_readonly import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_real_mcp_adapter_only_calls_search_code_with_readonly_transport(self):
        calls = []
        session = SimpleNamespace(initialize=AsyncMock(), call_tool=AsyncMock(return_value=
            SimpleNamespace(isError=False, content=[SimpleNamespace(text='code results')])) )
        @asynccontextmanager
        async def transport(url, **kwargs):
            calls.append((url, kwargs))
            yield ('read', 'write', None)
        @asynccontextmanager
        async def session_context(*args):
            self.assertEqual(args, ('read', 'write'))
            yield session
        with patch('mcp.client.streamable_http.streamablehttp_client', transport), \
                patch('mcp.ClientSession', session_context):
            self.assertEqual(official_code_search('"wake" repo:example/companion', 2, 'fake-token'), 'code results')
        self.assertEqual(calls[0][0], MCP_URL)
        self.assertEqual(calls[0][1]['headers'], {'Authorization': 'Bearer fake-token',
            'X-MCP-Readonly': 'true', 'X-MCP-Toolsets': 'repos'})
        session.call_tool.assert_awaited_once_with('search_code', {
            'query': '"wake" repo:example/companion', 'page': 2, 'perPage': 5})


class GitHubWakeIntegrationTests(unittest.TestCase):
    def test_autonomous_wake_researches_source_then_creates_proposal_using_existing_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            desktop = root / 'Desktop'
            desktop.mkdir()
            store = Store(root / 'data')
            cfg = Character('test', 'Test', 'Fictional persona', 20, 100,
                            {'model': 'fake', 'max_input_tokens': 30000,
                             'input_cny_per_million': Decimal('0'), 'cached_input_cny_per_million': Decimal('0'),
                             'output_cny_per_million': Decimal('0')}, 'fake',
                            tools={'enabled': True, 'max_steps': 8, 'max_result_bytes': 8192,
                                   'folder': 'data/test/workspace', 'lab': False},
                            web={'github_readonly': True, 'github_repository': 'example/companion'},
                            autonomy={'interval_minutes': 90, 'daily_max_runs': 12,
                                      'daily_budget_cny': Decimal('3'), 'min_alarm_gap_minutes': 15})
            with patch('tools_agent.shutil.which', return_value=None):
                manager = FilesystemTools(root, cfg, store)
            get = Mock(side_effect=fixture_get)
            manager.github_tools = GitHubReadTools(cfg, get=get)
            manager.desktop_files = DesktopFileTools(True, folder_for=lambda _name: desktop)
            manager.tools.extend(manager.desktop_files.tools)
            self.assertNotIn('github_read', [t['function']['name'] for t in permitted_tools(manager.tools)])
            source_url = 'https://github.com/example/companion/blob/' + SHA + '/wake.py#L2'
            proposal = '# 改进申请\n目标：提供自由探索入口。\n源码证据：' + source_url + '\n收益：方便探索。\n风险：需保留暂停。\n验收：自主醒来可探索，等待 Lan 审批实现。'
            requests = [
                ('mcp__github__github_read', {'repository': 'example/companion', 'resource': 'tree'}),
                ('mcp__github__github_read', {'repository': 'example/companion', 'resource': 'file', 'ref': SHA, 'path': 'wake.py'}),
                ('desktop_create_file', {'folder': 'desktop', 'path': '改进申请.md', 'content': proposal}),
            ]
            seen = []
            def model(messages, **kwargs):
                seen.append(copy.deepcopy((messages, kwargs)))
                index = len(seen) - 1
                if index < len(requests):
                    name, args = requests[index]
                    call = SimpleNamespace(id=f'call-{index}', function=SimpleNamespace(
                        name=name, arguments=json.dumps(args, ensure_ascii=False)))
                    reply = SimpleNamespace(tool_calls=[call], content=None,
                        model_dump=lambda **_: {'role': 'assistant', 'tool_calls': []})
                else:
                    reply = SimpleNamespace(tool_calls=[], content='Decision: stop\nReason: proposal saved\nFocus: source research\nNext step: wait for Lan')
                return reply, 100, 20, 0
            activity = ActivityState(store.root / 'test' / 'resident-state.json')
            activity.set(True)
            hub = Hub('Test', activity)
            chat = Chat(cfg, store, Budget(store), model, tools=manager, output=hub.output)
            controller = AutonomyController(chat, hub)
            controller.run()
            self.assertEqual((desktop / '改进申请.md').read_text(encoding='utf-8'), proposal)
            rows = [r for r in store.read('test/tools.jsonl') if r.get('state') != '进行中']
            self.assertEqual([r['state'] for r in rows], ['成功'] * 3)
            self.assertEqual([r['tool'] for r in rows], [r[0] for r in requests])
            self.assertIn('2:     return', json.dumps(seen[2][0]))
            self.assertEqual(get.call_count, 6)
            self.assertEqual(chat.mcp_services.permission_for('mcp__github__github_read'), 'allow')
            self.assertIn('mcp__github__github_read', chat.current_capability_snapshot())
            self.assertEqual(tool_policy('desktop_create_file'), 'desktop_create')
            # Existing file protection remains active, including on repeated wakes.
            self.assertEqual(manager.invoke('desktop_create_file', requests[-1][1])[1], '被拒绝')
            disabled = replace(cfg, tools={**cfg.tools, 'enabled': False})
            with patch('tools_agent.shutil.which', return_value=None):
                closed = FilesystemTools(root, disabled, store)
            self.assertNotIn('github_read', [t.name for t in closed.tools])
            self.assertEqual(closed.invoke('github_read', requests[0][1])[1], '被拒绝')


if __name__ == '__main__':
    unittest.main()
