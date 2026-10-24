import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools_agent import FilesystemTools
from veil_agent import Store
from web_tools import WebTools, public_http_url


class PublicUrlTests(unittest.TestCase):
    def test_only_public_http_urls_are_accepted(self):
        for url in (
            'file:///C:/Users/lan/private.txt',
            'ftp://example.com/file',
            'http://localhost/private',
            'http://service.local/',
            'http://router.lan/',
            'http://intranet/',
            'http://127.0.0.1/',
            'http://127.1/',
            'http://10.0.0.8/',
            'http://169.254.169.254/latest/meta-data/',
            'http://192.168.1.1/',
            'http://[::1]/',
            'http://user:password@example.com/',
            'http://@example.com/',
            '//example.com/path',
            'https://example.com/a\\@127.0.0.1/',
        ):
            with self.subTest(url=url):
                self.assertIsNone(public_http_url(url))

    def test_public_url_is_normalized_and_fragment_is_removed(self):
        self.assertEqual(public_http_url('HTTPS://Example.com/news#section'),
                         'https://example.com/news')
        self.assertEqual(public_http_url('https://example.com'), 'https://example.com/')


class WebToolsTests(unittest.TestCase):
    def make_tools(self, *, worker=None, max_bytes=8192, enabled=True):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(root, ignore_errors=True))
        cfg = SimpleNamespace(id='test', tools={'enabled': True, 'max_result_bytes': max_bytes},
                              web={'enabled': enabled})
        factory = Mock(return_value=worker) if worker is not None else Mock(side_effect=OSError())
        with patch('web_tools._fetch_python', return_value=Path('fetch-python.exe') if worker else None), \
                patch('web_tools.DDGS', Mock()):
            return root, cfg, WebTools(root, cfg, worker_factory=factory), factory

    def test_search_returns_bounded_sources_as_untrusted_data_without_logging_query(self):
        worker = Mock()
        _, _, tools, _ = self.make_tools(worker=worker, max_bytes=4096)
        ddgs = Mock()
        ddgs.text.return_value = [
            {'title': 'Weather', 'href': 'https://weather.example/forecast', 'body': 'Sunny'},
            {'title': 'Private result', 'href': 'http://192.168.1.1/', 'body': 'hidden'},
        ]
        with patch('web_tools.DDGS', return_value=ddgs):
            result, state, summary = tools.invoke('web_search', {'query': 'Shanghai weather'})
        self.assertEqual(state, '成功', result)
        self.assertIn('Untrusted web search results', result)
        self.assertIn('https://weather.example/forecast', result)
        self.assertNotIn('192.168.1.1', result)
        self.assertNotIn('Shanghai weather', summary)
        self.assertLessEqual(len(result.encode('utf-8')), 4096)
        ddgs.text.assert_called_once_with(
            'Shanghai weather', backend='duckduckgo', max_results=5)

    def test_query_validation_and_network_failure_are_bounded(self):
        _, _, tools, _ = self.make_tools(worker=Mock())
        with patch('web_tools.DDGS') as ddgs:
            result, state, _ = tools.invoke('web_search', {'query': '   '})
        ddgs.assert_not_called()
        self.assertEqual(state, '被拒绝')
        self.assertIn('1 to 500 characters', result)
        with patch('web_tools.DDGS', side_effect=TimeoutError()):
            result, state, summary = tools.invoke('web_search', {'query': 'a useful topic'})
        self.assertEqual(state, '出错')
        self.assertNotIn('a useful topic', result)
        self.assertEqual(summary, 'web_search')

    def test_small_result_limit_applies_to_fixed_messages(self):
        _, _, tools, _ = self.make_tools(worker=Mock(), max_bytes=32)
        for name, arguments in (('web_search', {'query': '  '}),
                                ('web_fetch', {'url': 'http://127.0.0.1/'})):
            with self.subTest(name=name):
                result, _, _ = tools.invoke(name, arguments)
                self.assertLessEqual(len(result.encode('utf-8')), 32)

    def test_fetch_refuses_private_urls_before_contacting_mcp(self):
        worker = Mock()
        _, _, tools, _ = self.make_tools(worker=worker)
        for url in ('http://localhost/', 'http://127.1/', 'http://192.168.0.1/',
                    'http://[::1]/', 'file:///private.txt'):
            with self.subTest(url=url):
                result, state, _ = tools.invoke('web_fetch', {'url': url})
                self.assertEqual(state, '被拒绝')
                self.assertIn('Only public HTTP(S)', result)
        worker.call.assert_not_called()

    def test_fetch_uses_official_pagination_and_marks_page_as_untrusted(self):
        worker = Mock()
        worker.call.return_value = 'Contents of https://example.com/:\nA page body.'
        _, _, tools, _ = self.make_tools(worker=worker)
        result, state, summary = tools.invoke(
            'web_fetch', {'url': 'https://example.com/#top', 'start_index': 512})
        self.assertEqual(state, '成功')
        self.assertIn('Untrusted webpage contents', result)
        self.assertIn('A page body.', result)
        self.assertEqual(summary, 'web_fetch (example.com)')
        name, arguments = worker.call.call_args.args
        self.assertEqual(name, 'fetch')
        self.assertEqual(arguments['url'], 'https://example.com/')
        self.assertEqual(arguments['start_index'], 512)
        self.assertGreater(arguments['max_length'], 0)

    def test_fetch_error_does_not_expose_private_address_or_server_details(self):
        worker = Mock()
        worker.call.side_effect = RuntimeError('Refused: host resolves to 10.0.0.5 (robots.txt)')
        _, _, tools, _ = self.make_tools(worker=worker)
        result, state, _ = tools.invoke('web_fetch', {'url': 'https://example.com/'})
        self.assertEqual(state, '出错')
        self.assertIn('does not allow', result)
        self.assertNotIn('10.0.0.5', result)

    def test_web_tools_follow_role_toggle_and_allow_search_without_fetch_install(self):
        _, _, disabled, _ = self.make_tools(worker=Mock(), enabled=False)
        self.assertEqual(disabled.tools, [])
        self.assertIsNone(disabled.startup_notice)
        _, _, degraded, _ = self.make_tools(worker=None)
        self.assertEqual([tool.name for tool in degraded.tools], ['web_search'])
        self.assertIn('run setup.bat', degraded.startup_notice)


class FilesystemWebToolIntegrationTests(unittest.TestCase):
    def test_web_tools_are_directly_available_and_announced_in_capabilities(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / 'data')
            web = SimpleNamespace(
                tools=[SimpleNamespace(name='web_search'), SimpleNamespace(name='web_fetch')],
                startup_notice=None,
                invoke=Mock(return_value=('result', '成功', 'web_search (1 results)')),
                preview_summary=Mock(return_value='web_search'),
                close=Mock())
            cfg = SimpleNamespace(id='test', tools={'enabled': True,
                'folder': 'data/test/workspace', 'max_steps': 6, 'max_result_bytes': 8192,
                'lab': False}, memory={'enabled': False})
            with patch('tools_agent.shutil.which', return_value=None):
                manager = FilesystemTools(root, cfg, store, web_factory=lambda *_: web)
            self.assertIn('web_search', [tool.name for tool in manager.tools])
            self.assertIn('web_fetch', [tool.name for tool in manager.tools])
            self.assertIn('web_search', manager.capability_event)
            record = manager.capability_record
            self.assertIn('web_fetch', record)
            self.assertEqual(manager.invoke('web_search', {'query': 'fixture'})[0], 'result')
            manager.preview_summary('web_fetch', {'url': 'https://example.com/'})
            manager.__exit__(None, None, None)
            web.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
