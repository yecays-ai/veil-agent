"""Local browser front end for the same chat loop the terminal uses.

The chat loop itself (run_chat) is unchanged and runs on the main thread; this
file only supplies its input/output/approval functions and a small HTTP server
bound to 127.0.0.1.
"""
import argparse
import hashlib
import hmac
import json
import queue
import secrets
import sys
import threading
import time
import webbrowser
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from veil_agent import (ROOT, ActivityState, Budget, Chat, Store, UserError,
                        load_character, now, make_model, run_chat)

PAGE = Path(__file__).resolve().parent / 'web' / 'index.html'
MAX_BODY = 200_000
MAX_SUBMISSIONS = 1024


class Hub:
    """Message list shared by the chat loop (main thread) and HTTP handlers."""

    def __init__(self, name, activity):
        self.name = name
        self.activity = activity
        self.events = []
        self.busy = True          # true until the loop is waiting for input
        self.stopped = False
        self.cond = threading.Condition()
        self.inbox = queue.Queue()
        self.answers = queue.Queue()
        self.pending = None       # event id of the approval being asked, else None
        self.submissions = OrderedDict()  # recent request ids -> normalized text digest

    def push(self, kind, text, **metadata):
        with self.cond:
            event_id = len(self.events) + 1
            self.events.append({'id': event_id, 'kind': kind, 'text': text,
                                'time': now(), **metadata})
            return event_id

    def output(self, text):
        text = str(text)
        prefix = f'{self.name}: '
        if text.startswith(prefix):
            self.push('assistant', text[len(prefix):])
        elif text.startswith('请求失败，未自动重试'):
            self.push('error', text)
        elif text.startswith('接上了'):
            self.push('notice', text.split('。')[0] + '。')   # drop the terminal-only command hints
        else:
            self.push('notice', text)

    def input_fn(self, _prompt=''):
        with self.cond:
            self.busy = False
        while True:
            try:
                text = self.inbox.get(timeout=0.5)
            except queue.Empty:
                if self.stopped:
                    raise EOFError
                continue
            with self.cond:
                self.busy = True
            return text

    def stop(self):
        with self.cond:
            self.stopped = True
            self.cond.notify_all()

    def submit(self, text, request_id):
        text_digest = hashlib.sha256(text.encode('utf-8')).digest()
        # Check busy and register the accepted request atomically for all handlers.
        with self.cond:
            if request_id in self.submissions:
                return 'duplicate' if self.submissions[request_id] == text_digest else 'conflict'
            if self.stopped:
                return 'stopped'
            if self.busy:
                return 'busy'
            self.busy = True
            self.push('user', text)
            self.submissions[request_id] = text_digest
            if len(self.submissions) > MAX_SUBMISSIONS:
                self.submissions.popitem(last=False)
            self.inbox.put(text)
            return 'accepted'
    def approval(self, prompt):
        # Same sentence the terminal shows; the page offers two buttons for y/N.
        with self.cond:
            while not self.answers.empty():
                self.answers.get_nowait()
            self.pending = self.push('approval', prompt)
        while True:
            try:
                answer = self.answers.get(timeout=0.5)
            except queue.Empty:
                if self.stopped:
                    with self.cond:
                        self.pending = None
                    return 'n'
                continue
            with self.cond:
                self.pending = None
                self.push('notice', '已同意。' if answer == 'y' else '已拒绝。')
            return answer

    def answer(self, approval_id, answer):
        # Only the approval currently being asked can be answered, once.
        with self.cond:
            if approval_id is None or approval_id != self.pending:
                return False
            self.pending = None
            self.answers.put(answer)
            return True

    def snapshot(self, after):
        with self.cond:
            return {'events': self.events[after:], 'busy': self.busy, 'stopped': self.stopped,
                    'name': self.name, 'pending': self.pending,
                    'activity_enabled': self.activity.enabled()}


def make_handler(hub, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # never log requests: URLs carry the token

        def _send(self, code, body, ctype='application/json; charset=utf-8'):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, supplied):
            port = self.server.server_address[1]
            allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            allowed_origins = {f'http://{h}' for h in allowed_hosts}
            if self.headers.get('Host') not in allowed_hosts:
                self._send(403, {'error': 'host'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin not in allowed_origins:
                self._send(403, {'error': 'origin'})
                return False
            if not hmac.compare_digest((supplied or '').encode(), token.encode()):
                self._send(403, {'error': 'token'})
                return False
            return True

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == '/':
                if self._guard((query.get('t') or [''])[0]):
                    self._send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
            elif url.path == '/api/events':
                if self._guard(self.headers.get('X-Token')):
                    try:
                        after = max(0, int((query.get('after') or ['0'])[0]))
                    except ValueError:
                        after = 0
                    self._send(200, hub.snapshot(after))
            else:
                self._send(404, {'error': 'not found'})

        def do_POST(self):
            url = urlparse(self.path)
            if not self._guard(self.headers.get('X-Token')):
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= MAX_BODY:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._send(400, {'error': 'bad request'})
                return
            if url.path == '/api/send':
                text = body.get('text')
                request_id = body.get('request_id')
                if not isinstance(text, str) or not text.strip():
                    self._send(400, {'error': 'empty'})
                elif not isinstance(request_id, str) or not request_id or len(request_id) > 100:
                    self._send(400, {'error': 'request_id'})
                else:
                    text = text.strip()
                    result = hub.submit(text, request_id)
                    if result in ('accepted', 'duplicate'):
                        if result == 'accepted' and text == '/quit':
                            hub.push('notice', '正在保存并退出，稍等几秒，然后可以关掉这个页面。')
                        self._send(200, {'ok': True, 'duplicate': result == 'duplicate'})
                    else:
                        self._send(409, {'error': result})
            elif url.path == '/api/approve':
                answer = body.get('answer')
                if answer not in ('y', 'n'):
                    self._send(400, {'error': 'answer'})
                elif not hub.answer(body.get('id'), answer):
                    self._send(409, {'error': 'no such pending approval'})
                else:
                    self._send(200, {'ok': True})
            elif url.path == '/api/activity':
                enabled = body.get('enabled')
                if not isinstance(enabled, bool):
                    self._send(400, {'error': 'enabled'})
                else:
                    hub.activity.set(enabled)
                    self._send(200, {'ok': True, 'enabled': enabled})
            elif url.path == '/api/exit':
                try:
                    self._send(200, {'ok': True})
                finally:
                    hub.stop()
            else:
                self._send(404, {'error': 'not found'})

    return Handler


def start_server(hub, token, port):
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(hub, token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def serve_chat(chat, hub):
    for message in chat.history_events:
        hub.push(message['role'], message['content'], time=message['time'], history=True)
    run_chat(chat, input_fn=hub.input_fn, output=hub.output, drop=lambda: False,
             activity=hub.activity)


def save_endpoint(path, port, token):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'port': port, 'token': token}), encoding='utf-8')
    tmp.replace(path)


def clear_endpoint(path, token):
    try:
        current = json.loads(path.read_text(encoding='utf-8'))
        if hmac.compare_digest(str(current.get('token', '')).encode(), token.encode()):
            path.unlink(missing_ok=True)
    except (OSError, ValueError, UnicodeError, AttributeError):
        pass


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='VEIL 本机网页聊天')
    parser.add_argument('--character', default='peter')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    hub = None
    token = None
    server = None
    endpoint_path = ROOT / 'data' / 'web_service.json'
    try:
        store = Store(ROOT / 'data')
        with store.lock():
            budget = Budget(store)
            cfg = load_character(ROOT, args.character)
            client, model = make_model(ROOT, cfg)
            activity = ActivityState(store.root / cfg.id / 'resident-state.json')
            hub = Hub(cfg.name, activity)
            token = secrets.token_urlsafe(24)
            try:
                server = start_server(hub, token, args.port)
            except OSError:
                raise UserError('网页服务未能启动；请稍后重试。')
            port = server.server_address[1]
            save_endpoint(endpoint_path, port, token)
            url = f'http://127.0.0.1:{port}/?t={token}'
            if not args.no_browser:
                webbrowser.open(url)
            failed = False
            try:
                with client:
                    from tools_agent import FilesystemTools
                    with FilesystemTools(ROOT, cfg, store) as tools:
                        if cfg.memory and cfg.memory.get('enabled'):
                            from memory_agent import Memory, open_ombre
                            with open_ombre(ROOT, cfg) as backend:
                                with Memory(cfg, store, budget, model, backend) as memory:
                                    if memory.native:
                                        from memory_native import NativeMemoryTools, ToolSet
                                        tools = ToolSet(tools, NativeMemoryTools(
                                            memory, cfg.tools['max_result_bytes']))
                                    serve_chat(Chat(cfg, store, budget, model, memory, tools,
                                                    approval=hub.approval, output=hub.output), hub)
                                for notice in memory.notices():
                                    hub.output(notice)
                        else:
                            serve_chat(Chat(cfg, store, budget, model, tools=tools,
                                            approval=hub.approval, output=hub.output), hub)
            except UserError as exc:
                hub.push('error', str(exc))
                failed = True
            finally:
                hub.stop()
                time.sleep(1.5)   # let an open page read the final state
        return 1 if failed else 0
    except UserError as exc:
        if hub:
            hub.push('error', str(exc))
            time.sleep(1.5)
        if token:
            clear_endpoint(endpoint_path, token)
    except ImportError:
        pass
    except (OSError, UnicodeError):
        pass
    except (KeyboardInterrupt, EOFError):
        if hub:
            hub.stop()
        return 0
    finally:
        if server:
            if hub:
                hub.stop()
            server.shutdown()
            server.server_close()
        if token:
            clear_endpoint(endpoint_path, token)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

