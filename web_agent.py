"""Local browser front end for the same chat loop the terminal uses.

The chat loop itself (run_chat) is unchanged and runs on the main thread; this
file only supplies its input/output/approval functions and a small HTTP server
bound to 127.0.0.1.
"""
import argparse
import base64
import hashlib
import hmac
import json
import queue
import re
import secrets
import sys
import threading
import time
import webbrowser
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from veil_agent import (ROOT, AUTONOMY_WAKE, ActivityState, Budget, Chat, Store, UserError,
                        load_character, now, make_model, run_chat)
from autonomy import AutonomyController

PAGE = Path(__file__).resolve().parent / 'web' / 'index.html'
MAX_BODY = 200_000
MAX_MEDIA_BYTES = 8 * 1024 * 1024
MAX_SUBMISSIONS = 1024
MEDIA_ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')
MEDIA_TYPES = {
    'image/png': ('.png', b'\x89PNG\r\n\x1a\n'),
    'image/jpeg': ('.jpg', b'\xff\xd8\xff'),
    'image/gif': ('.gif', b'GIF8'),
    'image/webp': ('.webp', b'RIFF'),
}


class ChatInput(str):
    def __new__(cls, text, media):
        value = super().__new__(cls, text)
        value.media = media
        return value


class Hub:
    """Message list shared by the chat loop (main thread) and HTTP handlers."""

    def __init__(self, name, activity, memory_port=None, media_root=None):
        self.name = name
        self.activity = activity
        self.memory_url = f'http://127.0.0.1:{memory_port}/dashboard' if memory_port else None
        self.events = []
        self.busy = True          # true until the loop is waiting for input
        self.stopping = False
        self.stopped = False
        self.ready = False        # true once the chat loop first waits for input
        self.cond = threading.Condition()
        self.inbox = queue.Queue()
        self.answers = queue.Queue()
        self.pending = None       # event id of the approval being asked, else None
        self.submissions = OrderedDict()  # recent request ids -> normalized text digest
        self.autonomy = None
        self.capability_record = lambda: None
        self.current_capabilities = lambda: 'Current capabilities are not available.'
        self.activity_transition = lambda enabled: self.activity.set(enabled)
        self.autonomy_busy = False
        self.autonomy_interrupted = threading.Event()
        self.media_root = Path(media_root) if media_root else None
        self.media_index = {}
        self.media_lock = threading.RLock()
        if self.media_root:
            self.media_root.mkdir(parents=True, exist_ok=True)
            index = self.media_root / 'index.jsonl'
            try:
                for line in index.read_text(encoding='utf-8').splitlines():
                    row = json.loads(line)
                    if isinstance(row, dict) and isinstance(row.get('id'), str):
                        self.media_index[row['id']] = row
            except FileNotFoundError:
                pass
            except (OSError, ValueError, UnicodeError) as exc:
                raise UserError('图片索引无法读取，已停止；请保留 data 文件夹。') from exc

    def media_info(self, media_id):
        with self.media_lock:
            row = self.media_index.get(media_id)
            return dict(row) if row else None

    def media_for_request(self, request_id):
        with self.media_lock:
            for row in reversed(list(self.media_index.values())):
                if row.get('request_id') == request_id:
                    return dict(row)
        return None

    def save_media(self, mime, raw, original_name='image'):
        if (self.media_root is None or mime not in MEDIA_TYPES or not isinstance(original_name, str)
                or not raw or len(raw) > MAX_MEDIA_BYTES):
            return None
        suffix, signature = MEDIA_TYPES[mime]
        if not raw.startswith(signature) or (mime == 'image/webp' and raw[8:12] != b'WEBP'):
            return None
        media_id = secrets.token_hex(16)
        filename = media_id + suffix
        row = {'id': media_id, 'filename': filename, 'mime': mime,
               'name': Path(original_name).name[:120] or 'image'}
        with self.media_lock:
            (self.media_root / filename).write_bytes(raw)
            self.media_index[media_id] = row
        return row

    def push(self, kind, text, **metadata):
        with self.cond:
            event_id = len(self.events) + 1
            self.events.append({'id': event_id, 'kind': kind, 'text': text,
                                'time': now(), **metadata})
            return event_id

    def output(self, text):
        text = str(text)
        prefix = f'{self.name}: '
        if text.startswith('新对话已开始；'):
            self.push('reset', text)
        elif text.startswith(prefix):
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
            self.ready = True
        while True:
            try:
                text = self.inbox.get_nowait()
            except queue.Empty:
                text = None
            if text is not None:
                with self.cond:
                    self.busy = True
                if isinstance(text, tuple):
                    return ChatInput(text[0], text[1])
                return text
            if self.stopping or self.stopped:
                raise EOFError
            if self.autonomy is not None and self.autonomy.due():
                with self.cond:
                    if (not self.stopping and not self.stopped and self.activity.enabled()
                            and self.inbox.empty()):
                        self.autonomy_busy = True
                        self.busy = True
                        self.autonomy_interrupted.clear()
                        return AUTONOMY_WAKE
            try:
                text = self.inbox.get(timeout=0.5)
            except queue.Empty:
                if self.stopping or self.stopped:
                    raise EOFError
                continue
            with self.cond:
                self.busy = True
            return text

    def request_stop(self):
        """Reject new work and ask the chat loop to finish its current step."""
        with self.cond:
            if self.stopped:
                return 'stopped'
            if self.stopping:
                return 'stopping'
            self.stopping = True
            self.pending = None
            self.autonomy_interrupted.set()
            self.cond.notify_all()
            return 'accepted'

    def stop(self):
        """Publish completed service shutdown after chat resources have closed."""
        with self.cond:
            self.stopping = True
            self.stopped = True
            self.pending = None
            self.autonomy_interrupted.set()
            self.cond.notify_all()

    def set_activity(self, enabled):
        with self.cond:
            if self.stopping or self.stopped:
                return False
            previous = self.activity.enabled()
            if previous == enabled:
                return True
            if enabled and self.autonomy is not None:
                self.autonomy.announce_autonomy()
            self.activity_transition(enabled)
            return True

    def submit(self, text, request_id, media_id=None):
        text_digest = hashlib.sha256(text.encode('utf-8')).digest()
        if media_id and (not isinstance(media_id, str) or not MEDIA_ID_PATTERN.fullmatch(media_id)):
            return 'media'
        if media_id and text.lstrip().startswith('/'):
            return 'media_command'
        # Check busy and register the accepted request atomically for all handlers.
        with self.cond:
            if request_id in self.submissions:
                return 'duplicate' if self.submissions[request_id] == text_digest else 'conflict'
            if self.stopping or self.stopped:
                return 'stopped'
            if self.autonomy_busy:
                if not self.inbox.empty():
                    return 'busy'
                media = self.media_info(media_id) if media_id else None
                if media_id and not media:
                    return 'media'
                self.push('user', text, **({'media': media} if media else {}))
                if media:
                    self._record_media_request(media, request_id)
                self.submissions[request_id] = text_digest
                if len(self.submissions) > MAX_SUBMISSIONS:
                    self.submissions.popitem(last=False)
                self.inbox.put((text, media) if media else text)
                self.autonomy_interrupted.set()
                return 'accepted'
            if self.busy:
                return 'busy'
            self.busy = True
            media = self.media_info(media_id) if media_id else None
            if media_id and not media:
                return 'media'
            self.push('user', text, **({'media': media} if media else {}))
            if media:
                self._record_media_request(media, request_id)
            self.submissions[request_id] = text_digest
            if len(self.submissions) > MAX_SUBMISSIONS:
                self.submissions.popitem(last=False)
            self.inbox.put((text, media) if media else text)
            return 'accepted'

    def _record_media_request(self, media, request_id):
        with self.media_lock:
            row = {**media, 'request_id': request_id}
            self.media_index[media['id']] = row
            index = self.media_root / 'index.jsonl'
            data = (json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8')
            with index.open('ab', buffering=0) as handle:
                handle.write(data)
                handle.flush()
                import os
                os.fsync(handle.fileno())
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
                if self.stopping or self.stopped:
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
            if self.stopping or self.stopped or approval_id is None or approval_id != self.pending:
                return False
            self.pending = None
            self.answers.put(answer)
            return True

    def snapshot(self, after):
        with self.cond:
            autonomy = self.autonomy.status() if self.autonomy else {}
            return {'events': self.events[after:], 'busy': self.busy and not self.autonomy_busy,
                    'stopping': self.stopping and not self.stopped, 'stopped': self.stopped,
                    'name': self.name, 'pending': self.pending, 'ready': self.ready,
                    'activity_enabled': self.activity.enabled(),
                    'current_capabilities': self.current_capabilities(),
                    'capability_record': self.capability_record(),
                    'memory_panel_url': self.memory_url, **autonomy}


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
            elif url.path.startswith('/media/'):
                media_id = url.path.rsplit('/', 1)[-1].split('.', 1)[0]
                media = hub.media_info(media_id)
                if not self._guard((query.get('t') or [''])[0]):
                    return
                if not media:
                    self._send(404, {'error': 'not found'})
                    return
                try:
                    data = (hub.media_root / media['filename']).read_bytes()
                except OSError:
                    self._send(404, {'error': 'not found'})
                else:
                    self._send(200, data, media['mime'])
            else:
                self._send(404, {'error': 'not found'})

        def do_POST(self):
            url = urlparse(self.path)
            if not self._guard(self.headers.get('X-Token')):
                return
            if url.path == '/api/media':
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if not 0 < length <= MAX_MEDIA_BYTES * 2:
                        raise ValueError
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError
                    mime = body.get('mime')
                    encoded = body.get('data')
                    if not isinstance(mime, str) or not isinstance(encoded, str):
                        raise ValueError
                    raw = base64.b64decode(encoded, validate=True)
                    media = hub.save_media(mime, raw, body.get('name', 'image'))
                    if not media:
                        raise ValueError
                except (ValueError, base64.binascii.Error):
                    self._send(400, {'error': 'invalid image'})
                else:
                    self._send(201, {'ok': True, 'media': media})
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
                    media = body.get('media')
                    media_id = media.get('id') if isinstance(media, dict) else None
                    result = hub.submit(text, request_id, media_id)
                    if result in ('accepted', 'duplicate'):
                        if result == 'accepted' and text == '/quit':
                            hub.push('notice', '正在保存并退出，稍等几秒，然后可以关掉这个页面。')
                        self._send(200, {'ok': True, 'duplicate': result == 'duplicate'})
                    elif result == 'media':
                        self._send(400, {'error': 'invalid media'})
                    elif result == 'media_command':
                        self._send(400, {'error': 'image cannot be sent with a command'})
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
                    try:
                        if not hub.set_activity(enabled):
                            self._send(409, {'error': 'stopping'})
                        else:
                            self._send(200, {'ok': True, 'enabled': enabled})
                    except UserError as exc:
                        self._send(500, {'error': str(exc)})
            elif url.path == '/api/exit':
                state = hub.request_stop()
                if state == 'accepted':
                    self._send(202, {'ok': True, 'state': 'accepted'})
                elif state == 'stopping':
                    self._send(202, {'ok': True, 'state': 'stopping'})
                else:
                    self._send(200, {'ok': True, 'state': 'stopped'})
            else:
                self._send(404, {'error': 'not found'})

    return Handler


def start_server(hub, token, port):
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(hub, token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def serve_chat(chat, hub):
    for message in chat.history_events:
        media = message.get('media') or hub.media_for_request(message.get('request_id'))
        hub.push(message['role'], message['content'], time=message['time'], history=True,
                 **({'media': media} if media else {}))
    hub.autonomy = AutonomyController(chat, hub)
    hub.capability_record = lambda: chat.capability_history_record
    hub.activity_transition = lambda enabled: chat.set_activity_enabled(hub.activity, enabled)
    chat.activity_enabled = hub.activity.enabled
    hub.current_capabilities = chat.current_capability_snapshot
    try:
        run_chat(chat, input_fn=hub.input_fn, output=hub.output, drop=lambda: False,
                 activity=hub.activity, autonomy=hub.autonomy)
    finally:
        hub.request_stop()


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


def save_startup_error(path, message):
    # The detached service has no console; the launcher shows this text instead.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message, encoding='utf-8')
    except OSError:
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
    error_path = ROOT / 'data' / 'web_service_error.txt'
    try:
        store = Store(ROOT / 'data')
        with store.lock():
            budget = Budget(store)
            cfg = load_character(ROOT, args.character)
            budget.autonomy_daily = cfg.autonomy['daily_budget_cny']
            client, model = make_model(ROOT, cfg)
            activity = ActivityState(store.root / cfg.id / 'resident-state.json')
            memory_port = (cfg.memory or {}).get('ombre_port') if (cfg.memory or {}).get('enabled') else None
            hub = Hub(cfg.name, activity, memory_port, store.root / cfg.id / 'media')
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
                save_startup_error(error_path, str(exc))
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
        print(str(exc))
        save_startup_error(error_path, str(exc))
    except ImportError:
        print('缺少运行依赖，请先按 README 完成安装。')
        save_startup_error(error_path, '缺少运行依赖，请先按 README 完成安装。')
    except (OSError, UnicodeError):
        print('本机文件无法读写，已停止；请检查磁盘空间和文件权限。')
        save_startup_error(error_path, '本机文件无法读写，已停止；请检查磁盘空间和文件权限。')
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


