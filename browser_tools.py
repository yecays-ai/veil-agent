"""Public browsing in a fresh Playwright context, never a user's browser profile."""
import asyncio
import concurrent.futures
import contextlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit, unquote

from desktop_access import desktop_access
from web_tools import public_http_url
from windows_interaction import DesktopInputMonitor

BROWSER_TOOL_NAMES = frozenset({'browser'})
MAX_STEPS = 20
MAX_SECONDS = 300
MAX_BYTES = 8192
TEXT_BYTES = 4500
MAX_MCP_REQUEST_BYTES = 64 * 1024
FORBIDDEN = re.compile(
    r'login|signin|signout|logout|passport|oauth|account|checkout|purchase|delete|'
    r'download|upload|publish|comment|reply|like|favorite|coin|subscribe|message|send|'
    r'登录|登陆|退出|账号|账户|支付|购买|删除|下载|上传|发布|投稿|评论|回复|点赞|收藏|投币|关注|消息|发送', re.I)
CREDENTIAL = re.compile(r'password|passwd|pwd|passcode|密码|口令', re.I)
SELF_TARGETS = frozenset({'', '_self', '_top'})

TOOL = SimpleNamespace(
    name='browser',
    description=(
        'Browse public HTTPS pages in your own isolated browser, also during autonomous wakes. It runs in the '
        'background: no window on Lan\'s screen, and her using the computer does not interrupt it. '
        'Only when Lan wants to see a page herself, open it with show: true; that browser window appears on her '
        'screen and her keyboard or mouse input then takes priority over your next step. '
        'Use web_search for discovery, then open a URL, click a fresh link_id, or scroll to see more. '
        'open, click and scroll each already return the page text and links, so do not call observe right after them; '
        'observe only re-reads the current page. '
        'click navigates an anchor URL; no buttons/forms, login, accounts, social actions or downloads. '
        'Only page/link text and measured video state are evidence: page ready does NOT mean video playing. '
        'Stop/pause, 20 steps and 5 minutes take priority. close releases your browser.'),
    inputSchema={'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['open', 'observe', 'click', 'scroll', 'close']},
        'url': {'type': 'string'}, 'link_id': {'type': 'string'},
        'direction': {'type': 'string', 'enum': ['up', 'down']},
        'show': {'type': 'boolean', 'description': 'open only: put the browser on Lan\'s screen for her to see.'},
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
   text: (document.body?.innerText || '').slice(0,3000),
   links: [...document.querySelectorAll('a[href]')].filter(visible).slice(0,60)
     .map(e=>({text:(e.innerText || e.getAttribute('title') || e.getAttribute('aria-label') || '').trim().slice(0,100), url:e.href})),
   controls: [...document.querySelectorAll('input,textarea,button')]
     .map((e,index)=>({index, visible:visible(e), tag:e.tagName.toLowerCase(), type:(e.type || '').toLowerCase(),
       name:(e.name || '').slice(0,80), label:(e.getAttribute('aria-label') || e.placeholder || e.value || e.innerText || '').trim().slice(0,100),
       formAction:e.form?.action || '', formMethod:(e.form?.method || 'get').toLowerCase(),
       formTarget:(e.form?.target || '').toLowerCase()})).filter(e=>e.visible).slice(0,40),
   videos:[...document.querySelectorAll('video')].slice(0,3).map(v=>({
     readyState:v.readyState, paused:v.paused, currentTime:v.currentTime,
     error:v.error?.code || null, visible:visible(v)}))};
}"""


LIVE_CONTROL = """(element) => ({tag: element.tagName.toLowerCase(),
 type: (element.type || '').toLowerCase(), name: element.name || '',
 action: element.form?.action || '', method: (element.form?.method || 'get').toLowerCase(),
 target: (element.form?.target || '').toLowerCase()})"""

SUBMIT_FORM = """(element, expected) => { const form = element.form;
 if (!form || form.action !== expected || (form.method || 'get').toLowerCase() !== 'get') return false;
 form.submit(); return true; }"""


def failure_reason(exc):
    """Class name plus the first line of the library's message, so a failure can be diagnosed from the log
    (Playwright's own exception class is just `Error`). Local paths and URL credentials/queries are hidden."""
    text = (str(exc).strip().splitlines() or [''])[0]
    text = re.sub(r'(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@', r'\1', text)
    text = re.sub(r'(?i)(https?://[^\s?#]+)[?#]\S*', r'\1', text)
    # A Windows path may contain spaces (a profile like "John Doe"), so it is hidden to the end of the line.
    text = re.sub(r'(?i)\b[a-z]:[\\/][^\'"\r\n]*', '<path>', text)
    text = re.sub(r'(?i)\bfile:/{1,3}[^\s\'"]*', '<path>', text)
    text = re.sub(r'(?<![\w:/])/(?:home|users|root|opt|usr|var|tmp|mnt)/[^\s\'"]*', '<path>', text, flags=re.I)
    name = type(exc).__name__
    return f'{name}: {text[:200]}' if text else name


class BrowserTools:
    def __init__(self, cfg, *, platform_enabled=None, headless=None, monitor=None):
        self.enabled = bool((getattr(cfg, 'web', None) or {}).get('enabled') is True
                            and (getattr(cfg, 'web', None) or {}).get('browser_interaction') is True
                            and (os.name == 'nt' if platform_enabled is None else platform_enabled))
        self.tools = [TOOL] if self.enabled else []
        self.monitor = monitor or DesktopInputMonitor()
        # None: background (headless) unless an open asks for show; True forces headless (tests).
        self.headless = headless
        # Whether the current browser window is on Lan's screen. Only then does her input take priority:
        # a background browser never touches her mouse, keyboard or foreground window.
        self.visible = False
        self.loop = None
        self.thread = None
        self.gate = threading.Lock()
        self.steps = 0
        self.deadline = 0
        self.links = {}
        self.controls = {}
        self.page = self.context = self.engine = self.runtime = self.proxy = None
        self.serial = 0
        self.launched_visible = None
        self.quarantined = False
        self.stopped = lambda: False

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
        self.controls = {}
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
        self.launched_visible = self.visible
        self.engine = await self.runtime.chromium.launch(
            headless=True if self.headless else not self.visible, env=environment, chromium_sandbox=True,
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
        # Keep room for links: long (e.g. Chinese) page text is cut by bytes, not only characters.
        data['text'] = data['text'].encode('utf-8', 'ignore')[:TEXT_BYTES].decode('utf-8', 'ignore')
        self.serial += 1
        self.links = {}
        self.controls = {}
        # Remove controls before sizing links: otherwise the raw control list can fill the trial
        # budget and the first candidate would drop every navigation link.
        raw_controls = data.pop('controls')
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
        result_controls = []
        for item in raw_controls:
            # Search input only: no passwords, file fields, hidden state or form submits other than GET.
            if (item['tag'] not in {'input', 'textarea', 'button'} or item['type'] in {'password', 'file', 'hidden'}
                    or FORBIDDEN.search(item['name'] + ' ' + item['label'])
                    or CREDENTIAL.search(item['name'] + ' ' + item['label'])):
                continue
            if item.get('formTarget', '') not in SELF_TARGETS:
                continue  # a _blank/named-target form would navigate a page that _launch closes
            if item['tag'] == 'button' and (item['formMethod'] != 'get' or not item['formAction']):
                continue
            try:
                if item['formAction']:
                    public_url(item['formAction'])
            except ValueError:
                continue
            control_id = f'{self.serial}-c{len(result_controls)}'
            result_controls.append({'control_id': control_id, 'kind': item['tag'], 'type': item['type'],
                                    'name': item['name'], 'label': item['label']})
            self.controls[control_id] = (self.page.url, item)
        result = {**data, 'url': self.page.url, 'links': result_links, 'controls': result_controls,
                'evidence': 'Page ready is document state only; video playing is unconfirmed. '
                'Video fields are point-in-time observations, not proof playback advanced.'}
        while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
            if result['text']:
                result['text'] = result['text'][:len(result['text']) // 2]
            elif result_links:
                # Controls stay usable longest: a long query string can make both lists exceed the
                # budget, and dropping the search box would silently remove the only next action.
                dropped = result_links.pop()
                self.links.pop(dropped['link_id'], None)
            elif result_controls:
                dropped = result_controls.pop()
                self.controls.pop(dropped['control_id'], None)
            else:
                break
        return result

    async def _operate(self, args, stopped):
        self.stopped = stopped
        try:
            with desktop_access() if self.visible else contextlib.nullcontext():
                if stopped():
                    raise ValueError('Stopped: human input or stop/pause has priority.')
                action = args['action']
                if action == 'close':
                    await self._release()
                    return {'closed': True}
                if self.page is None or self.page.is_closed() or (
                        action == 'open' and self.launched_visible != self.visible):
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
                elif action in {'fill', 'submit'}:
                    source, control = self.controls.get(args['control_id'], (None, None))
                    if source != self.page.url or control is None:
                        raise ValueError('Stale or unknown control_id. Observe again.')
                    locator = self.page.locator('input,textarea,button').nth(control['index'])
                    # nth() resolves against the live DOM; re-read it so a mutation between snapshot
                    # and action cannot turn a search box into another field or another form.
                    live = await locator.evaluate(LIVE_CONTROL)
                    if (live['tag'] != control['tag'] or live['type'] != control['type']
                            or live['name'] != control['name']):
                        raise ValueError('Page changed; observe again.')
                    if action == 'fill':
                        if live['tag'] not in {'input', 'textarea'} or live['type'] not in {
                                '', 'text', 'search', 'textarea'}:
                            raise ValueError('Only observed public text/search fields can be filled.')
                        if CREDENTIAL.search(live['name']) or FORBIDDEN.search(live['name']):
                            raise ValueError('Credential-like fields are not available.')
                        await locator.fill(args['text'])
                    else:
                        if live['tag'] not in {'button', 'input'}:
                            raise ValueError('Only observed public GET search forms can be submitted.')
                        if live['tag'] == 'button' and live['type'] != 'submit':
                            raise ValueError('Only observed submit controls can be submitted.')
                        if live['tag'] == 'input' and live['type'] not in {'submit', 'image'}:
                            raise ValueError('Only observed submit controls can be submitted.')
                        if live['method'] != 'get' or not live['action']:
                            raise ValueError('Only observed public GET search forms can be submitted.')
                        if live['action'] != control['formAction'] or live['target'] not in SELF_TARGETS:
                            raise ValueError('Page changed; observe again.')
                        if urlsplit(live['action']).netloc != urlsplit(self.page.url).netloc:
                            raise ValueError('Only same-site public GET search forms can be submitted.')
                        # Native submit bypasses page handlers; routing still blocks every non-GET request.
                        async with self.page.expect_navigation(wait_until='domcontentloaded', timeout=12000):
                            submitted = await locator.evaluate(SUBMIT_FORM, live['action'])
                            if not submitted:
                                raise ValueError('Page changed; observe again.')
                if stopped():
                    raise ValueError('Interrupted; navigation may have occurred. Observe again when idle.')
                return await self._snapshot()
        except BaseException:
            self.links = {}
            raise
        finally:
            # Human input/deadlines stop Peter's operation, not the page left for Lan.
            self.stopped = lambda: False

    def invoke(self, name, args, approval=None, *, should_stop=None, background=False):
        """`background` (the workbench): never show a window, never wait on Lan's input."""
        summary = 'isolated public browser'
        if not self.enabled or name not in BROWSER_TOOL_NAMES:
            return 'Isolated browsing is unavailable for this role/platform.', '被拒绝', summary
        if not self.gate.acquire(blocking=False):
            return 'Browser is busy; no additional action was queued.', '被拒绝', summary
        previous_visible = self.visible
        try:
            if not isinstance(args, dict):
                raise ValueError('Expected browser action arguments.')
            action = args.get('action')
            if self.quarantined and action != 'close':
                raise ValueError('Browser cleanup is incomplete; restart VEIL before browsing again.')
            required = {'open': {'url'}, 'observe': set(), 'click': {'link_id'},
                        'scroll': {'direction'}, 'fill': {'control_id', 'text'},
                        'submit': {'control_id'}, 'close': set()}.get(action)
            if action == 'open' and 'show' in args:
                if not isinstance(args['show'], bool):
                    raise ValueError('show must be true or false.')
                args = dict(args)
                show = args.pop('show')
            else:
                show = None
            if required is None or set(args) != required | {'action'}:
                raise ValueError('Unsupported action or arguments.')
            if background and self.visible:
                # The chat is showing a page on Lan's screen; background work must not drive or close it.
                raise ValueError("The browser is showing a page on Lan's screen; background browsing is "
                                 'unavailable until that page is closed.')
            if action == 'open':
                public_url(args['url'])
                # A plain open keeps the current mode; only an explicit show changes it.
                if background:
                    self.visible = False
                elif show is not None:
                    self.visible = show
            if action == 'click' and not isinstance(args['link_id'], str):
                raise ValueError('link_id must come from the latest observation.')
            if action in {'fill', 'submit'} and not isinstance(args['control_id'], str):
                raise ValueError('control_id must come from the latest observation.')
            if action == 'fill' and (not isinstance(args['text'], str) or len(args['text']) > 300):
                raise ValueError('Search text must be at most 300 characters.')
            if action == 'scroll' and args['direction'] not in ('up', 'down'):
                raise ValueError('direction must be up or down.')
            if not self.deadline:
                self.begin_request()
            if action != 'close' and (self.steps >= MAX_STEPS or time.monotonic() >= self.deadline):
                raise ValueError('Browser request step/time limit reached.')
            if action == 'close':
                # Showing a page ends with its window; the next open is in the background again.
                self.visible = False
            human_priority = self.visible and action != 'close'
            snapshot = self.monitor.snapshot() if human_priority else None
            if human_priority:
                self.monitor.require_idle(3)
            def stopped():
                return (bool(should_stop and should_stop())
                        or (action != 'close' and time.monotonic() >= self.deadline)
                        or (human_priority and self.monitor.changed_since(snapshot)))
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
            if args.get('action') == 'open' if isinstance(args, dict) else False:
                # A failed open does not change the mode; the next open relaunches if they differ.
                self.visible = previous_visible
            reason = str(exc) if isinstance(exc, ValueError) else failure_reason(exc)
            return 'Browser action failed or was interrupted: ' + reason[:300], '被拒绝', summary
        finally:
            self.gate.release()

    def close(self):
        loop, thread = self.loop, self.thread
        try:
            if loop and thread and thread.is_alive():
                try:
                    asyncio.run_coroutine_threadsafe(self._release(), loop).result(timeout=8)
                finally:
                    loop.call_soon_threadsafe(loop.stop)
                    thread.join(timeout=2)
        finally:
            # A closed instance must not look like a running worker: the next invoke starts a fresh loop.
            self.loop = None
            self.thread = None

    def workbench_gateway(self, should_stop, container=False):
        """A one-run MCP bridge.  It deliberately exposes no selectors, scripts or host paths."""
        return BrowserWorkbenchGateway(self, should_stop, container=container)


class BrowserWorkbenchGateway:
    """Task-local Streamable HTTP MCP service for the already-isolated public browser."""
    def __init__(self, browser, should_stop, *, container=False):
        self.browser = browser
        self.should_stop = should_stop
        self.token = secrets.token_urlsafe(24)
        self.host = '0.0.0.0' if container else '127.0.0.1'
        self.server = self.thread = None
        self.started = False
        self.cancelled = threading.Event()

    def stop(self):
        self.cancelled.set()

    def stopped(self):
        # The workbench browses in the background, so Lan using her computer never stops it; only the
        # stop button, pause, limits or the end of the run do.
        return self.cancelled.is_set() or self.should_stop()

    @staticmethod
    def request_size(value):
        try:
            size = int(value or 0)
        except (TypeError, ValueError):
            raise ValueError('invalid Content-Length') from None
        if size < 0:
            raise ValueError('invalid Content-Length')
        if size > MAX_MCP_REQUEST_BYTES:
            raise OverflowError('MCP request body is too large')
        return size

    def __enter__(self):
        gateway = self
        gate = getattr(self.browser, 'gate', None)
        if gate is not None and not gate.acquire(blocking=False):
            raise RuntimeError('Browser is busy; the workbench browser gateway was not started.')

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            timeout = 10  # idle or slow unauthenticated clients must not hold a worker thread

            def log_message(self, *_args):
                pass

            def _send(self, body, status=200, session=None):
                data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                if status >= 400:
                    # The request body may be unread (401/413); never parse it as the next request.
                    self.send_header('Connection', 'close')
                    self.close_connection = True
                if session:
                    self.send_header('Mcp-Session-Id', session)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path != '/mcp' or not secrets.compare_digest(
                        self.headers.get('Authorization', '').encode('utf-8', 'ignore'),
                        ('Bearer ' + gateway.token).encode()):
                    return self._send({'error': {'code': -32001, 'message': 'unauthorized'}}, 401)
                try:
                    size = gateway.request_size(self.headers.get('Content-Length'))
                    request = json.loads(self.rfile.read(size))
                    if not isinstance(request, dict) or not isinstance(request.get('method'), str):
                        raise ValueError('invalid request')
                    params = request.get('params') or {}
                    if not isinstance(params, dict):
                        raise ValueError('invalid params')
                    method, request_id = request['method'], request.get('id')
                    if method == 'initialize':
                        return self._send({'jsonrpc': '2.0', 'id': request_id, 'result': {
                            'protocolVersion': params.get('protocolVersion', '2025-03-26'),
                            'capabilities': {'tools': {}}, 'serverInfo': {'name': 'veil-browser', 'version': '1'}}},
                            session=gateway.token)
                    if method == 'notifications/initialized':
                        return self._send({}, 202)
                    if method == 'tools/list':
                        return self._send({'jsonrpc': '2.0', 'id': request_id, 'result': {'tools': [
                            {'name': 'public_browser', 'description': (
                                'Use the isolated public browser to open HTTPS pages, observe, follow a fresh '
                                'public link, scroll, fill an observed non-sensitive search field, or submit an '
                                'observed GET search form. No login, upload, download, JavaScript, files or private '
                                'network access is available.'),
                             'inputSchema': {'type': 'object', 'properties': {
                                 'action': {'type': 'string', 'enum': ['open', 'observe', 'click', 'scroll', 'fill', 'submit']},
                                 'url': {'type': 'string'}, 'link_id': {'type': 'string'},
                                 'control_id': {'type': 'string'}, 'text': {'type': 'string'},
                                 'direction': {'type': 'string', 'enum': ['up', 'down']}},
                                 'required': ['action'], 'additionalProperties': False}}
                        ]}})
                    if method == 'tools/call':
                        arguments = params.get('arguments', {})
                        if params.get('name') != 'public_browser':
                            raise ValueError('unknown tool')
                        if (not isinstance(arguments, dict)
                                or arguments.get('action') not in {'open', 'observe', 'click', 'scroll',
                                                                  'fill', 'submit'}):
                            raise ValueError('unsupported browser action')
                        # The workbench browses in the background only; it never puts a window on Lan's screen.
                        arguments = {key: value for key, value in arguments.items() if key != 'show'}
                        text, state, _summary = gateway.browser.invoke(
                            'browser', arguments, should_stop=gateway.stopped, background=True)
                        result = {'content': [{'type': 'text', 'text': text}],
                                  'isError': state != '成功'}
                        return self._send({'jsonrpc': '2.0', 'id': request_id, 'result': result})
                    return self._send({'jsonrpc': '2.0', 'id': request_id,
                                       'error': {'code': -32601, 'message': 'method not found'}})
                except OverflowError:
                    return self._send({'jsonrpc': '2.0', 'id': None,
                                       'error': {'code': -32600, 'message': 'request body too large'}}, 413)
                except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    return self._send({'jsonrpc': '2.0', 'id': None,
                                       'error': {'code': -32602, 'message': str(exc)[:200]}}, 400)

        try:
            if getattr(self.browser, 'page', None) is not None and hasattr(self.browser, 'close'):
                # Every workbench task starts from a fresh isolated context: an idle page left by
                # the previous task must not be observable by the next one.
                self.browser.close()
            self.server = ThreadingHTTPServer((self.host, 0), Handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='veil-browser-mcp')
            self.thread.start()
            self.started = True
            self.browser.begin_request()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise
        finally:
            if gate is not None:
                gate.release()

    @property
    def url(self):
        host = 'host.docker.internal' if self.host == '0.0.0.0' else '127.0.0.1'
        return f'http://{host}:{self.server.server_address[1]}/mcp'

    def __exit__(self, *_args):
        started = self.started
        self.stop()
        if self.server is not None:
            if started:
                # shutdown() waits for serve_forever(); a failed thread start never runs it.
                self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            if started:
                self.thread.join(timeout=2)
            self.thread = None
        self.started = False
