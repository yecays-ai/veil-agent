"""Permission checks plus opt-in real Chromium scenarios; no paid model or user profile."""
import asyncio
import json
import os
import time
import urllib.request
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from browser_tools import (BrowserTools, BrowserWorkbenchGateway, PublicProxy, public_addresses, public_url,
                           MAX_BYTES, MAX_MCP_REQUEST_BYTES)
from veil_agent import model_tool_result


CFG = SimpleNamespace(web={'enabled': True, 'browser_interaction': True})


class Idle:
    def snapshot(self): return 1
    def changed_since(self, snapshot): return False
    def require_idle(self, minimum): pass


class BrowserWorkbenchGatewayTests(unittest.TestCase):
    def request(self, gateway, body, token=True):
        request = urllib.request.Request(
            gateway.url.replace('host.docker.internal', '127.0.0.1'), method='POST',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                **({'Authorization': 'Bearer ' + gateway.token} if token else {})})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.URLError as exc:
            if getattr(exc.reason, 'winerror', None) == 10013:
                self.skipTest('the restricted test environment blocks loopback client connections')
            raise

    def test_gateway_only_exposes_the_task_local_public_browser_tool(self):
        calls = []
        def invoke(*args, **kwargs):
            calls.append((args, kwargs))
            return 'snapshot', '成功', 'browser'
        browser = SimpleNamespace(begin_request=lambda: calls.append('begin'), invoke=invoke, monitor=Idle())
        with BrowserWorkbenchGateway(browser, lambda: False) as gateway:
            status, initialized = self.request(gateway, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                                           'params': {'protocolVersion': '2025-03-26'}})
            self.assertEqual((status, initialized['result']['serverInfo']['name']), (200, 'veil-browser'))
            _status, tools = self.request(gateway, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
            self.assertEqual([tool['name'] for tool in tools['result']['tools']], ['public_browser'])
            _status, result = self.request(gateway, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'public_browser', 'arguments': {'action': 'open', 'url': 'https://example.com/'}}})
            self.assertEqual(result['result']['content'][0]['text'], 'snapshot')
            self.assertEqual(calls[0], 'begin')
            self.assertEqual(calls[1][0], ('browser', {'action': 'open', 'url': 'https://example.com/'}))
            self.assertFalse(calls[1][1]['should_stop']())
        self.assertTrue(gateway.stopped(), 'closing the gateway must revoke later MCP calls')

    def test_gateway_rejects_missing_task_token(self):
        browser = SimpleNamespace(begin_request=lambda: None, invoke=lambda *_args, **_kwargs: ('', '成功', ''), monitor=Idle())
        with BrowserWorkbenchGateway(browser, lambda: False) as gateway:
            request = urllib.request.Request(gateway.url.replace('host.docker.internal', '127.0.0.1'), method='POST',
                data=b'{}', headers={'Content-Type': 'application/json'})
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request)
            except urllib.error.URLError as exc:
                if getattr(exc.reason, 'winerror', None) == 10013:
                    self.skipTest('the restricted test environment blocks loopback client connections')
                raise
        self.assertEqual(denied.exception.code, 401)

    def test_gateway_rejects_oversized_request_before_parsing_it(self):
        browser = SimpleNamespace(begin_request=lambda: None, invoke=lambda *_args, **_kwargs: ('', '成功', ''), monitor=Idle())
        with BrowserWorkbenchGateway(browser, lambda: False) as gateway:
            request = urllib.request.Request(gateway.url.replace('host.docker.internal', '127.0.0.1'), method='POST',
                data=b'x' * (MAX_MCP_REQUEST_BYTES + 1), headers={'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + gateway.token})
            try:
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request)
            except urllib.error.URLError as exc:
                if getattr(exc.reason, 'winerror', None) == 10013:
                    self.skipTest('the restricted test environment blocks loopback client connections')
                raise
        self.assertEqual(rejected.exception.code, 413)

    def test_human_takeover_revokes_the_whole_gateway_and_request_size_is_bounded(self):
        changed = [False]
        monitor = SimpleNamespace(snapshot=lambda: 'before', changed_since=lambda _snapshot: changed[0])
        browser = SimpleNamespace(begin_request=lambda: None, invoke=lambda *_args, **_kwargs: ('', '成功', ''), monitor=monitor)
        with BrowserWorkbenchGateway(browser, lambda: False) as gateway:
            self.assertFalse(gateway.stopped())
            changed[0] = True
            self.assertTrue(gateway.stopped())
            changed[0] = False
            self.assertTrue(gateway.stopped(), 'human takeover must not be cleared between MCP calls')
        self.assertEqual(BrowserWorkbenchGateway.request_size(str(MAX_MCP_REQUEST_BYTES)), MAX_MCP_REQUEST_BYTES)
        with self.assertRaises(OverflowError):
            BrowserWorkbenchGateway.request_size(str(MAX_MCP_REQUEST_BYTES + 1))
        with self.assertRaises(ValueError):
            BrowserWorkbenchGateway.request_size('not-a-size')


class BrowserSafetyTests(unittest.TestCase):
    def test_failed_and_cancelled_operations_release_their_stop_callback(self):
        async def scenario():
            browser = BrowserTools(CFG, platform_enabled=True, monitor=Idle())
            self.assertFalse(browser.stopped())
            with self.assertRaises(ValueError):
                await browser._operate({'action': 'observe'}, lambda: True)
            self.assertFalse(browser.stopped())
            browser.page = SimpleNamespace(is_closed=lambda: False)
            entered = asyncio.Event()
            async def slow_snapshot():
                entered.set()
                await asyncio.Future()
            browser._snapshot = slow_snapshot
            task = asyncio.create_task(browser._operate({'action': 'observe'}, lambda: False))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(browser.stopped())
        asyncio.run(scenario())

    def test_snapshot_size_limit_terminates_and_keeps_usable_controls(self):
        async def scenario():
            browser = BrowserTools(CFG, platform_enabled=True, monitor=Idle())
            controls = [{'index': index, 'visible': True, 'tag': 'input', 'type': 'text',
                         'name': f'field{index}', 'label': 'L' * 100,
                         'formAction': 'https://example.com/search', 'formMethod': 'get'} for index in range(40)]
            links = [{'text': 'T' * 100, 'url': 'https://example.com/' + 'p' * 150 + str(index)}
                     for index in range(60)]
            browser.page = SimpleNamespace(url='https://example.com/', evaluate=AsyncMock(return_value={
                'text': 'X' * 12000, 'links': links, 'controls': controls, 'title': 't', 'videos': []}))
            result = await browser._snapshot()
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode('utf-8')), MAX_BYTES)
            self.assertTrue(result['controls'])
            for control in result['controls']:
                self.assertIn(control['control_id'], browser.controls)
        asyncio.run(scenario())

    def test_unsafe_urls_and_write_parameters_are_refused(self):
        for url in ['file:///secret', 'http://example.com', 'https://127.0.0.1',
                    'https://user:pw@example.com', 'https://example.com:8443',
                    'https://passport.bilibili.com/login', 'https://example.com/%64ownload']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)
        browser = BrowserTools(CFG, platform_enabled=True, monitor=Idle())
        for args in [{'action': 'evaluate', 'script': 'secret'},
                     {'action': 'open', 'url': 'https://example.com', 'cookies': []},
                     {'action': 'click', 'selector': 'button'},
                     {'action': 'scroll', 'direction': 'sideways'}]:
            self.assertEqual(browser.invoke('browser', args)[1], '被拒绝')
        self.assertIsNone(browser.loop)

    def test_mixed_dns_cannot_reach_loopback(self):
        async def scenario():
            loop = asyncio.get_running_loop()
            with patch.object(loop, 'getaddrinfo', AsyncMock(return_value=[
                (2, 1, 6, '', ('93.184.216.34', 443)),
                (2, 1, 6, '', ('127.0.0.1', 443))])):
                with self.assertRaises(ValueError):
                    await public_addresses('example.com')
        asyncio.run(scenario())

    def test_proxy_dials_validated_ip_instead_of_resolving_host_again(self):
        async def scenario():
            reader = SimpleNamespace(readuntil=AsyncMock(return_value=
                b'CONNECT example.com:443 HTTP/1.1\r\n\r\n'))
            writer = SimpleNamespace(close=lambda: None)
            # Identity must be hashable like a real StreamWriter.
            class Writer:
                def close(self): pass
            with patch('browser_tools.public_addresses', AsyncMock(return_value=['93.184.216.34'])), \
                    patch('browser_tools.asyncio.open_connection', AsyncMock(side_effect=OSError)) as dial:
                await PublicProxy().handle(reader, Writer())
                dial.assert_awaited_once_with('93.184.216.34', 443)
        asyncio.run(scenario())

    def test_role_opt_in_budget_human_priority_and_no_queued_action(self):
        disabled = BrowserTools(SimpleNamespace(web={'enabled': True}), platform_enabled=True)
        self.assertEqual(disabled.tools, [])
        self.assertEqual(disabled.invoke('browser', {'action': 'observe'})[1], '被拒绝')
        browser = BrowserTools(CFG, platform_enabled=True, monitor=Idle())
        browser.begin_request()
        browser.steps = 20
        self.assertIn('limit', browser.invoke('browser', {'action': 'observe'})[0])
        browser.begin_request()
        browser.deadline = time.monotonic() - 1
        self.assertIn('limit', browser.invoke('browser', {'action': 'observe'})[0])
        browser.gate.acquire()
        try:
            self.assertIn('queued', browser.invoke('browser', {'action': 'observe'})[0])
        finally:
            browser.gate.release()
        browser.begin_request()
        browser.monitor.require_idle = lambda _: (_ for _ in ()).throw(ValueError('human priority'))
        self.assertIn('human priority', browser.invoke('browser', {'action': 'observe'})[0])
        self.assertIsNone(browser.loop)

    def test_cjk_evidence_is_kept_and_wrapped_as_untrusted(self):
        result = model_tool_result('browser', '视频页面已准备，播放未确认', '成功')
        self.assertIn('视频页面', result)
        self.assertIn('untrusted_browser', result)


class BrowserLoopIntegrationTests(unittest.TestCase):
    def test_chat_and_wake_offer_browser_keep_evidence_and_forward_stop(self):
        from dataclasses import replace
        from tests.test_tools import ToolLoopTests
        from tools_agent import FilesystemTools, permitted_tools
        from veil_agent import Chat
        from browser_tools import TOOL
        for autonomous in (False, True):
            with self.subTest(autonomous=autonomous):
                helper = ToolLoopTests()
                helper.setUp()
                try:
                    cfg = replace(helper.cfg, web=CFG.web,
                                  tools={**helper.cfg.tools, 'max_result_bytes': 8192})
                    with patch('tools_agent.shutil.which', return_value=None):
                        manager = FilesystemTools(helper.root, cfg, helper.store)
                    manager.browser_tools = BrowserTools(CFG, platform_enabled=True, monitor=Idle())
                    manager.tools = [TOOL]
                    seen = []
                    def model(messages, **kwargs):
                        seen.append(messages)
                        if len(seen) == 1:
                            return helper.message([helper.tool_call('browser', json.dumps({
                                'action': 'open', 'url': 'https://example.com/video'}), 'b')]), 100, 20, 0
                        return helper.message(content='Video page ready; playback unconfirmed.'), 100, 20, 0
                    def invoke(name, args, approval=None, *, should_stop=None):
                        self.assertTrue(callable(should_stop))
                        self.assertFalse(should_stop())
                        return '{"title":"公开视频", "videos":[{"paused":true}]}', '成功', 'isolated public browser'
                    with patch.object(manager.browser_tools, 'invoke', side_effect=invoke):
                        chat = Chat(cfg, helper.store, helper.budget, model, tools=manager, output=lambda _: None)
                        chat.autonomy_should_yield = lambda: False
                        chat.send('Find an interesting public video', autonomy=autonomous)
                    self.assertIn('browser', [item['function']['name'] for item in permitted_tools(manager.tools)])
                    evidence = [item for item in seen[-1] if item.get('role') == 'tool']
                    self.assertIn('untrusted_browser', str(evidence))
                    self.assertIn('公开视频', str(evidence))
                finally:
                    helper.doCleanups()


@unittest.skipUnless(os.environ.get('VEIL_BROWSER_TEST') == '1', 'opt-in isolated Chromium')
class BrowserChromiumTests(unittest.TestCase):
    def setUp(self):
        self.browser = BrowserTools(CFG, platform_enabled=True, headless=True, monitor=Idle())
        self.browser.begin_request()

    def tearDown(self):
        self.browser.close()

    def test_handoff_page_keeps_loading_after_old_stop_condition_changes(self):
        async def scenario():
            browser = self.browser
            await browser._launch()
            async def fixture(route):
                async def continue_read():
                    await route.fulfill(status=200, content_type='text/html',
                                        body='<title>Public page</title><p>Ready</p>')
                await browser._route(SimpleNamespace(request=route.request,
                                                     continue_=continue_read, abort=route.abort))
            await browser.context.route('https://example.com/**', fixture)
            old_stop = [False]
            await browser._operate({'action': 'open', 'url': 'https://example.com/'},
                                   lambda: old_stop[0])
            # Simulates human takeover or expiry AFTER Peter handed the page over.
            old_stop[0] = True
            self.assertTrue(await browser.page.evaluate("async () => (await fetch('/lazy')).ok"))
            await browser.page.goto('https://example.com/manual-navigation')
            self.assertEqual(await browser.page.title(), 'Public page')
            with self.assertRaises(ValueError):
                await browser._operate({'action': 'observe'}, lambda: True)
            self.assertTrue(await browser.page.evaluate("async () => (await fetch('/after-failure')).ok"))
            # Clearing operation state must not weaken the permanent read-only policy.
            self.assertFalse(await browser.page.evaluate(
                "async () => {try {await fetch('/write', {method:'POST'});return true;} catch {return false;}}"))
            with self.assertRaises(Exception):
                await browser.page.goto('https://127.0.0.1/')
        self.browser._start_loop()
        asyncio.run_coroutine_threadsafe(scenario(), self.browser.loop).result(timeout=20)

    def test_search_scroll_page_turn_video_and_isolation(self):
        async def scenario():
            browser = self.browser
            await browser._launch()
            async def fixture(route):
                if route.request.url.endswith('/video'):
                    html = '<title>Chosen video</title><h1>Science video</h1><video controls></video>'
                elif route.request.url.endswith('/page2'):
                    html = '<title>Results page two</title><a href="/video">Interesting science video</a>'
                else:
                    html = ('<title>Public search</title><h1>Search results</h1>'
                            '<a href="/login">登录</a><button>Like</button>'
                            '<div style="height:1000px">Results</div><a href="/page2">Next page</a>')
                await route.fulfill(status=200, content_type='text/html', body=html)
            await browser.context.route('https://example.com/**', fixture)
            first = await browser._operate({'action': 'open', 'url': 'https://example.com/search'}, lambda: False)
            self.assertEqual(first['links'], [])
            scrolled = await browser._operate({'action': 'scroll', 'direction': 'down'}, lambda: False)
            second = await browser._operate({'action': 'click', 'link_id': scrolled['links'][0]['link_id']}, lambda: False)
            with self.assertRaises(ValueError):
                await browser._operate({'action': 'click', 'link_id': scrolled['links'][0]['link_id']}, lambda: False)
            second = await browser._operate({'action': 'observe'}, lambda: False)
            video = await browser._operate({'action': 'click', 'link_id': second['links'][0]['link_id']}, lambda: False)
            self.assertEqual(video['title'], 'Chosen video')
            self.assertEqual(video['videos'][0]['readyState'], 0)
            self.assertIn('unconfirmed', video['evidence'])
            self.assertLessEqual(len(json.dumps(video, ensure_ascii=False).encode()), MAX_BYTES)
            await browser.context.add_cookies([{'name': 'fixture', 'value': 'private', 'url': 'https://example.com'}])
            await browser._release()
            await browser._launch()
            self.assertEqual(await browser.context.cookies(), [])
        self.browser._start_loop()
        asyncio.run_coroutine_threadsafe(scenario(), self.browser.loop).result(timeout=30)

    def test_hidden_field_before_search_input_keeps_the_control_reference_aligned(self):
        async def scenario():
            browser = self.browser
            await browser._launch()
            async def fixture(route):
                await route.fulfill(status=200, content_type='text/html', body=(
                    '<form action="/results" method="get"><input type="hidden" name="token" value="x">'
                    '<input type="search" name="q" aria-label="Search"><button>Search</button></form>'))
            await browser.context.route('https://example.com/**', fixture)
            page = await browser._operate({'action': 'open', 'url': 'https://example.com/search'}, lambda: False)
            field = next(item for item in page['controls'] if item['name'] == 'q')
            await browser._operate({'action': 'fill', 'control_id': field['control_id'], 'text': 'VEIL'}, lambda: False)
            self.assertEqual(await browser.page.locator('input[name=q]').input_value(), 'VEIL')
        self.browser._start_loop()
        asyncio.run_coroutine_threadsafe(scenario(), self.browser.loop).result(timeout=20)

    def test_fill_and_submit_a_public_search_form_and_read_the_result_page(self):
        async def scenario():
            browser = self.browser
            await browser._launch()
            async def fixture(route):
                if '/results' in route.request.url:
                    html = '<title>Results</title><h1>Science results</h1><p>Result body</p>'
                else:
                    html = ('<title>Public search</title>'
                            '<form action="/results" method="get"><input type="hidden" name="token" value="x">'
                            '<input type="search" name="q" aria-label="Search">'
                            '<input type="submit" value="Search"></form>')
                await route.fulfill(status=200, content_type='text/html', body=html)
            await browser.context.route('https://example.com/**', fixture)
            page = await browser._operate({'action': 'open', 'url': 'https://example.com/search'}, lambda: False)
            field = next(item for item in page['controls'] if item['name'] == 'q')
            page = await browser._operate({'action': 'fill', 'control_id': field['control_id'],
                                           'text': 'VEIL'}, lambda: False)
            submit = next(item for item in page['controls'] if item['kind'] == 'input' and item['type'] == 'submit')
            result = await browser._operate({'action': 'submit', 'control_id': submit['control_id']}, lambda: False)
            self.assertIn('q=VEIL', browser.page.url)
            self.assertEqual(result['title'], 'Results')
            self.assertIn('Result body', result['text'])
        self.browser._start_loop()
        asyncio.run_coroutine_threadsafe(scenario(), self.browser.loop).result(timeout=20)

    def test_stop_cancels_inflight_action_and_closes_browser(self):
        async def slow(args, stopped):
            await asyncio.sleep(30)
        stopped = [False]
        timer = __import__('threading').Timer(0.5, lambda: stopped.__setitem__(0, True))
        with patch.object(self.browser, '_operate', slow):
            timer.start()
            started = time.monotonic()
            result = self.browser.invoke('browser', {'action': 'observe'}, should_stop=lambda: stopped[0])
            self.assertIn('Interrupted', result[0])
            self.assertLess(time.monotonic() - started, 5)
        timer.join()
