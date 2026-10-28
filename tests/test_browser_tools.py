"""Permission checks plus opt-in real Chromium scenarios; no paid model or user profile."""
import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from browser_tools import BrowserTools, PublicProxy, public_addresses, public_url, MAX_BYTES
from veil_agent import model_tool_result


CFG = SimpleNamespace(web={'enabled': True, 'browser_interaction': True})


class Idle:
    def snapshot(self): return 1
    def changed_since(self, snapshot): return False
    def require_idle(self, minimum): pass


class BrowserSafetyTests(unittest.TestCase):
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
