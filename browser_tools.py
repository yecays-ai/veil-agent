"""Public browsing in a fresh Playwright context, never a user's browser profile."""
import asyncio
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit, unquote

from desktop_access import desktop_access
from web_tools import public_http_url
from windows_interaction import DesktopInputMonitor

BROWSER_TOOL_NAMES = frozenset({'browser'})
MAX_STEPS = 20
MAX_SECONDS = 300
MAX_BYTES = 8192
FORBIDDEN = re.compile(
    r'login|signin|signout|logout|passport|oauth|account|checkout|purchase|delete|'
    r'download|upload|publish|comment|reply|like|favorite|coin|subscribe|message|send|'
    r'登录|登陆|退出|账号|账户|支付|购买|删除|下载|上传|发布|投稿|评论|回复|点赞|收藏|投币|关注|消息|发送', re.I)

TOOL = SimpleNamespace(
    name='browser',
    description=(
        'Browse public HTTPS pages in your own isolated visible browser, also during autonomous wakes. '
        'Use web_search for discovery, open a URL, observe, click a fresh link_id, or scroll to see more links. '
        'click navigates an anchor URL; no buttons/forms, login, accounts, social actions or downloads. '
        'Only page/link text and measured video state are evidence: page ready does NOT mean video playing. '
        'Human input, stop/pause, 20 steps and 5 minutes take priority. close releases your browser.'),
    inputSchema={'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['open', 'observe', 'click', 'scroll', 'close']},
        'url': {'type': 'string'}, 'link_id': {'type': 'string'},
        'direction': {'type': 'string', 'enum': ['up', 'down']},
    }, 'required': ['action'], 'additionalProperties': False})


def public_url(value):
    value = public_http_url(value)
    if not value or urlsplit(value).scheme != 'https' or urlsplit(value).port not in (None, 443):
        raise ValueError('Only public HTTPS URLs on port 443 are supported.')
    if FORBIDDEN.search(unquote(value)):
        raise ValueError('Account, social, purchase and download URLs are not available.')
    return value


async def public_addresses(host):
    """Resolve once; the proxy connects to this exact public IP, preventing DNS rebinding."""
    public_url('https://' + host + '/')
    infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM), 4)
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Private, local or mixed DNS destinations are refused.')
    return addresses


class PublicProxy:
    """A loopback-only CONNECT proxy: public pinned IPs, no local network or proxy credentials."""
    def __init__(self):
        self.connections = set()
        self.total = 0

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, limit=8192)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        peer = None
        self.connections.add(writer)
        try:
            if len(self.connections) > 64:
                return
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 4)
            first = header.split(b'\r\n', 1)[0].decode('ascii').split()
            if len(first) != 3 or first[0] != 'CONNECT':
                return
            target = urlsplit('https://' + first[1])
            if target.port != 443 or not target.hostname or target.username or target.path:
                return
            addresses = await public_addresses(target.hostname)
            upstream, peer = await asyncio.wait_for(asyncio.open_connection(addresses[0], 443), 5)
            self.connections.add(peer)
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()

            async def relay(source, destination):
                while True:
                    data = await asyncio.wait_for(source.read(65536), 20)
                    if not data:
                        break
                    self.total += len(data)
                    if self.total > 100 * 1024 * 1024:
                        raise ValueError('Browser session network limit reached (100 MB).')
                    destination.write(data)
                    await destination.drain()
            tasks = [asyncio.create_task(relay(reader, peer)),
                     asyncio.create_task(relay(upstream, writer))]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (ValueError, OSError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            for stream in (writer, peer):
                if stream:
                    self.connections.discard(stream)
                    stream.close()

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.connections):
            writer.close()


# Fixed program scripts only. No model-supplied JavaScript, selector or text input.
SNAPSHOT = """() => {
 const visible = e => {const r=e.getBoundingClientRect(); return r.width>0 && r.height>0 &&
   r.bottom>0 && r.top<innerHeight && getComputedStyle(e).visibility!=='hidden';};
 return {title: document.title.slice(0,300), ready: document.readyState,
   text: (document.body?.innerText || '').slice(0,1200),
   links: [...document.querySelectorAll('a[href]')].filter(visible).slice(0,60)
     .map(e=>({text:(e.innerText || e.getAttribute('title') || e.getAttribute('aria-label') || '').trim().slice(0,100), url:e.href})),
   videos:[...document.querySelectorAll('video')].slice(0,3).map(v=>({
     readyState:v.readyState, paused:v.paused, currentTime:v.currentTime,
     error:v.error?.code || null, visible:visible(v)}))};
}"""


class BrowserTools:
    def __init__(self, cfg, *, platform_enabled=None, headless=False, monitor=None):
        self.enabled = bool((getattr(cfg, 'web', None) or {}).get('enabled') is True
                            and (getattr(cfg, 'web', None) or {}).get('browser_interaction') is True
                            and (os.name == 'nt' if platform_enabled is None else platform_enabled))
        self.tools = [TOOL] if self.enabled else []
        self.monitor = monitor or DesktopInputMonitor()
        self.headless = headless
        self.loop = None
        self.thread = None
        self.gate = threading.Lock()
        self.steps = 0
        self.deadline = 0
        self.links = {}
        self.page = self.context = self.engine = self.runtime = self.proxy = None
        self.serial = 0
        self.quarantined = False

    def begin_request(self):
        # Chat/wake already enforces its model budget and total step count.
        self.steps = 0
        self.deadline = time.monotonic() + MAX_SECONDS

    def _start_loop(self):
        ready = threading.Event()
        def serve():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            ready.set()
            self.loop.run_forever()
            self.loop.close()
        self.thread = threading.Thread(target=serve, daemon=True, name='public-browser')
        self.thread.start()
        if not ready.wait(3):
            raise RuntimeError('Browser worker did not start.')

    async def _release(self):
        self.links = {}
        try:
            if self.engine:
                await self.engine.close()
        finally:
            if self.runtime:
                await self.runtime.stop()
            if self.proxy:
                await self.proxy.close()
            self.page = self.context = self.engine = self.runtime = self.proxy = None

    async def _launch(self):
        from playwright.async_api import async_playwright
        self.proxy = PublicProxy()
        port = await self.proxy.start()
        self.runtime = await async_playwright().start()
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        self.engine = await self.runtime.chromium.launch(
            headless=self.headless, env=environment, chromium_sandbox=True,
            proxy={'server': f'http://127.0.0.1:{port}', 'bypass': '<-loopback>'},
            args=['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                  '--disable-background-networking'])
        self.context = await self.engine.new_context(
            accept_downloads=False, service_workers='block', permissions=[],
            viewport={'width': 1280, 'height': 800})
        await self.context.route('**/*', self._route)
        await self.context.route_web_socket('**/*', lambda ws: ws.close())
        self.page = await self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.set_default_navigation_timeout(12000)
        self.context.on('page', lambda page: asyncio.create_task(page.close()) if page != self.page else None)
        self.page.on('dialog', lambda dialog: asyncio.create_task(dialog.dismiss()))
        self.page.on('download', lambda download: asyncio.create_task(download.cancel()))

    async def _route(self, route):
        try:
            public_url(route.request.url)
            if route.request.method not in {'GET', 'HEAD'} or self.stopped():
                raise ValueError('Read-only browsing stopped.')
            await route.continue_()
        except ValueError:
            await route.abort()

    async def _snapshot(self):
        public_url(self.page.url)
        data = await self.page.evaluate(SNAPSHOT)
        self.serial += 1
        self.links = {}
        result_links = []
        for item in data.pop('links'):
            try:
                url = public_url(item['url'])
                if not item['text'] or FORBIDDEN.search(item['text']):
                    continue
            except ValueError:
                continue
            link_id = f'{self.serial}-{len(result_links)}'
            candidate = {'link_id': link_id, 'text': item['text'], 'url': url}
            trial = {**data, 'url': self.page.url, 'links': result_links + [candidate]}
            if len(json.dumps(trial, ensure_ascii=False).encode('utf-8')) > MAX_BYTES - 700:
                break
            result_links.append(candidate)
            self.links[link_id] = (self.page.url, url)
        result = {**data, 'url': self.page.url, 'links': result_links,
                'evidence': 'Page ready is document state only; video playing is unconfirmed. '
                'Video fields are point-in-time observations, not proof playback advanced.'}
        while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
            result['text'] = result['text'][:len(result['text']) // 2]
        return result

    async def _operate(self, args, stopped):
        self.stopped = stopped
        try:
            with desktop_access():
                if stopped():
                    raise ValueError('Stopped: human input or stop/pause has priority.')
                action = args['action']
                if action == 'close':
                    await self._release()
                    return {'closed': True}
                if self.page is None or self.page.is_closed():
                    if action != 'open':
                        raise ValueError('Open a public page first.')
                    await self._release()
                    await self._launch()
                if stopped():
                    raise ValueError('Stopped before navigation.')
                if action == 'open':
                    self.links = {}
                    await self.page.goto(public_url(args['url']), wait_until='domcontentloaded')
                elif action == 'click':
                    source, url = self.links.get(args['link_id'], (None, None))
                    self.links = {}
                    if source != self.page.url or url is None:
                        raise ValueError('Stale or unknown link_id. Observe again.')
                    # Navigate the observed href without executing untrusted click handlers.
                    await self.page.goto(url, wait_until='domcontentloaded')
                elif action == 'scroll':
                    await self.page.evaluate('(dy) => window.scrollBy(0, dy)',
                                             -600 if args['direction'] == 'up' else 600)
                if stopped():
                    raise ValueError('Interrupted; navigation may have occurred. Observe again when idle.')
                return await self._snapshot()
        except BaseException:
            self.links = {}
            raise

    def invoke(self, name, args, approval=None, *, should_stop=None):
        summary = 'isolated public browser'
        if not self.enabled or name not in BROWSER_TOOL_NAMES:
            return 'Isolated browsing is unavailable for this role/platform.', '被拒绝', summary
        if not self.gate.acquire(blocking=False):
            return 'Browser is busy; no additional action was queued.', '被拒绝', summary
        try:
            if not isinstance(args, dict):
                raise ValueError('Expected browser action arguments.')
            action = args.get('action')
            if self.quarantined and action != 'close':
                raise ValueError('Browser cleanup is incomplete; restart VEIL before browsing again.')
            required = {'open': {'url'}, 'observe': set(), 'click': {'link_id'},
                        'scroll': {'direction'}, 'close': set()}.get(action)
            if required is None or set(args) != required | {'action'}:
                raise ValueError('Unsupported action or arguments.')
            if action == 'open':
                public_url(args['url'])
            if action == 'click' and not isinstance(args['link_id'], str):
                raise ValueError('link_id must come from the latest observation.')
            if action == 'scroll' and args['direction'] not in ('up', 'down'):
                raise ValueError('direction must be up or down.')
            if not self.deadline:
                self.begin_request()
            if action != 'close' and (self.steps >= MAX_STEPS or time.monotonic() >= self.deadline):
                raise ValueError('Browser request step/time limit reached.')
            snapshot = self.monitor.snapshot()
            if action != 'close':
                self.monitor.require_idle(3)
            def stopped():
                return (bool(should_stop and should_stop())
                        or (action != 'close' and (self.monitor.changed_since(snapshot)
                                                  or time.monotonic() >= self.deadline)))
            if not self.loop:
                self._start_loop()
            self.steps += 1
            future = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(self._operate(args, stopped), 22), self.loop)
            while True:
                try:
                    data = future.result(timeout=0.1)
                    return json.dumps(data, ensure_ascii=False), '成功', summary
                except concurrent.futures.TimeoutError:
                    if future.done():
                        self.quarantined = True
                        asyncio.run_coroutine_threadsafe(self._release(), self.loop).result(timeout=8)
                        self.quarantined = False
                        raise ValueError('Browser action timed out; isolated browser closed.')
                    if stopped():
                        self.quarantined = True
                        future.cancel()
                        # Async cancellation aborts navigation; discard the entire context before reuse.
                        cleanup = asyncio.run_coroutine_threadsafe(self._release(), self.loop)
                        cleanup.result(timeout=8)
                        self.quarantined = False
                        raise ValueError('Interrupted; isolated browser closed. No next action was queued.')
        except Exception as exc:
            # Do not expose arbitrary browser/network exception text or local installation paths.
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return 'Browser action failed or was interrupted: ' + reason[:300], '被拒绝', summary
        finally:
            self.gate.release()

    def close(self):
        if self.loop and self.thread and self.thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(self._release(), self.loop).result(timeout=8)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(timeout=2)
