"""Ticket 329: VEIL's MCP gateway for the host execution path.

OpenCode never gets Windows-MCP or Playwright MCP directly. It gets one local, token-protected MCP endpoint per
server; each call is ruled by the run's `veil_checkpoint.Checkpoint` with its real arguments, then forwarded to
the upstream server (a stdio process VEIL starts and stops). Only the tools listed in the checkpoint are offered.
Errors are returned and logged with their own text.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time

import executor_opencode as ex
import veil_checkpoint as vc

from mcp_services import START_TIMEOUT, _Worker

MAX_RESTARTS = 2
QUEUE_SECONDS = 90


def server_is_computer(server_id):
    return server_id == vc.COMPUTER_SERVER


def _squash(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


class VeilMcpGateway:
    def __init__(self, server_id, service, checkpoint, offered, *, should_stop, environment=None,
                 call_timeout=60, worker_factory=_Worker, companions=()):
        self.server_id = server_id
        self.service = {'id': server_id, 'cwd': None, **service}
        self.checkpoint = checkpoint
        self.offered = frozenset(offered)
        self.should_stop = should_stop
        self.environment = environment
        self.call_timeout = call_timeout
        self.worker_factory = worker_factory
        self.companions = list(companions)  # closed with the gateway (the browser's network guard)
        self.token = secrets.token_urlsafe(24)
        self.server = self.thread = self.worker = None
        self.restarts = 0
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.tools = []

    # ---- upstream ---------------------------------------------------------------------------------------
    def _start_worker(self):
        worker = self.worker_factory(self.service, self.environment)
        worker.call_timeout = self.call_timeout
        try:
            deadline = time.monotonic() + START_TIMEOUT + 5
            while True:  # a stop while the upstream server starts ends the wait at once
                if self.stopped():
                    raise RuntimeError('The task is stopped; ' + self.server_id + ' was not started.')
                try:
                    worker.ready.result(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
                    break
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        raise
        except BaseException:
            worker.close()
            raise
        self.worker = worker
        self.tools = [tool for tool in worker.tools if tool.name in self.offered]

    def _live_worker(self):
        worker = self.worker
        if worker is not None and worker.thread.is_alive() and not worker.closing.is_set():
            return worker
        if worker is not None:
            worker.close()  # the old upstream process is gone before a new one starts
            if worker.thread.is_alive():
                raise RuntimeError(f'{self.server_id} did not stop; not starting a second copy.')
            self.worker = None
        if self.restarts >= MAX_RESTARTS:
            raise RuntimeError(f'{self.server_id} stopped and was already restarted {MAX_RESTARTS} times.')
        self.restarts += 1
        self.checkpoint.log(kind='restart', server=self.server_id, count=self.restarts)
        self._start_worker()
        return self.worker

    def stop(self):
        self.cancelled.set()

    def stopped(self):
        return self.cancelled.is_set() or bool(self.should_stop())

    def invoke(self, name, arguments):
        """(text, is_error)."""
        if self.stopped():
            return 'The task is stopped; nothing was done.', True
        if name not in self.offered:
            return f'{name} is not offered.', True
        # One action at a time per server: parallel calls queue (the desktop and the browser page are shared).
        # The wait is bounded, and the ruling happens after the wait, right before the action, so it sees the
        # current foreground window and a stop that came while queued.
        deadline = time.monotonic() + QUEUE_SECONDS
        while not self.lock.acquire(timeout=0.25):
            if self.stopped():
                return 'The task is stopped; nothing was done.', True
            if time.monotonic() >= deadline:
                return 'Another action on this server took too long; nothing was done.', True
        try:
            if self.stopped():
                return 'The task is stopped; nothing was done.', True
            allowed, why = self.checkpoint.tool_call(self.server_id, name, arguments)
            if not allowed:
                return f'VEIL did not allow this ({why}).', True
            before = self._look('before') if server_is_computer(self.server_id) and name == 'Type' else (None, None)
            if self.stopped():
                return 'The task is stopped; nothing was done.', True
            try:
                text = self._live_worker().call(name, arguments, should_stop=self.stopped)
            except Exception as exc:
                detail = str(exc) or type(exc).__name__
                for key, value in arguments.items():  # an upstream error may echo what Peter typed
                    if key in vc.SENSITIVE_ARGUMENTS and len(str(value)) >= 3:
                        detail = detail.replace(str(value), f'[{len(str(value))} chars]')
                detail = ex._safe_display(detail)
                self.checkpoint.log(kind='tool_error', server=self.server_id, tool=name, error=detail[:2000])
                return detail[:2000], True
            if server_is_computer(self.server_id) and name == 'Type':
                text, failed = self._verify_typed(arguments, text, before)
                if failed:
                    return text, True
        finally:
            self.lock.release()
        return text, False

    def _look(self, name):
        """What the desktop shows now: (control text or None, snapshot text or None); only when Snapshot is offered."""
        seen = self.checkpoint.read_focus() if self.checkpoint.read_focus else None
        if seen is not None or 'Snapshot' not in self.offered:
            return seen, None
        try:
            return None, str(self._live_worker().call('Snapshot', {}, should_stop=self.stopped))
        except Exception as exc:
            self.checkpoint.log(kind='readback_error', server=self.server_id, error=(str(exc) or type(exc).__name__)[:300])
            return None, None

    def _verify_typed(self, arguments, text, before):
        """Read the field back after Type (never from the model's say-so). (text for the model, failed).
        Success needs the typed text to appear more often than before this Type, so text that was already
        there cannot count."""
        raw = str(arguments.get('text') or '')
        typed = _squash(raw)
        unsure = ' VEIL could not confirm it, so it is NOT confirmed. Take a Screenshot or Snapshot and check the ' \
                 'field before saying it worked; if it is not there, report the failure.'
        if not raw:
            return text, False
        if not typed or str(arguments.get('press_enter', '')).casefold() in {'true', '1'}:
            return text + unsure, False
        seen, snapshot = self._look('after')
        old_seen, old_snapshot = before
        if seen is not None and old_seen is not None:
            ok = _squash(seen).count(typed) > _squash(old_seen).count(typed)
            method = 'control'
        elif snapshot is not None and old_snapshot is not None:
            ok = _squash(snapshot).count(typed) > _squash(old_snapshot).count(typed)
            method = 'snapshot'
            if not ok:
                self.checkpoint.log(kind='readback', server=self.server_id, method=method, typed_chars=len(typed), ok=None)
                return text + unsure, False
        else:
            self.checkpoint.log(kind='readback', server=self.server_id, method='none', typed_chars=len(typed), ok=None)
            return text + unsure, False
        self.checkpoint.log(kind='readback', server=self.server_id, method=method, typed_chars=len(typed), ok=ok)
        if ok:
            return text, False
        return ('Input did not take effect: after typing, the field in front does not show the new text. '
                'Nothing was confirmed; do not report it as typed. Check the window with a Snapshot, '
                'click the right field, and try again, or report the failure.'), True

    # ---- MCP endpoint for OpenCode ----------------------------------------------------------------------
    def __enter__(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            timeout = 10

            def log_message(self, *_args):
                pass

            def send_json(self, body, status=200, session=None):
                data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                if status >= 400:
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
                    return self.send_json({'error': {'code': -32001, 'message': 'unauthorized'}}, 401)
                try:
                    size = int(self.headers.get('Content-Length') or 0)
                    if size < 0 or size > 262144:
                        raise ValueError('invalid request size')
                    request = json.loads(self.rfile.read(size))
                    if not isinstance(request, dict) or not isinstance(request.get('method'), str):
                        raise ValueError('invalid request')
                    params, method, request_id = request.get('params') or {}, request['method'], request.get('id')
                    if not isinstance(params, dict):
                        raise ValueError('invalid params')
                    if method == 'initialize':
                        return self.send_json({'jsonrpc': '2.0', 'id': request_id, 'result': {
                            'protocolVersion': params.get('protocolVersion', '2025-03-26'),
                            'capabilities': {'tools': {}},
                            'serverInfo': {'name': gateway.server_id, 'version': '1'}}}, session=gateway.token)
                    if method.startswith('notifications/'):
                        self.send_response(202)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return None
                    if method == 'ping':
                        return self.send_json({'jsonrpc': '2.0', 'id': request_id, 'result': {}})
                    if method == 'tools/list':
                        return self.send_json({'jsonrpc': '2.0', 'id': request_id, 'result': {'tools': [
                            {'name': tool.name, 'description': tool.description or '',
                             'inputSchema': getattr(tool, 'inputSchema', None) or getattr(tool, 'input_schema', None)
                             or {'type': 'object'}} for tool in gateway.tools]}})
                    if method == 'tools/call':
                        name, arguments = params.get('name'), params.get('arguments') or {}
                        if not isinstance(name, str) or not isinstance(arguments, dict):
                            raise ValueError('invalid tool call')
                        text, is_error = gateway.invoke(name, arguments)
                        return self.send_json({'jsonrpc': '2.0', 'id': request_id, 'result': {
                            'content': [{'type': 'text', 'text': str(text)}], 'isError': bool(is_error)}})
                    return self.send_json({'jsonrpc': '2.0', 'id': request_id,
                                           'error': {'code': -32601, 'message': 'method not found'}})
                except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    return self.send_json({'jsonrpc': '2.0', 'id': None,
                                           'error': {'code': -32602, 'message': str(exc)[:200]}}, 400)

        try:
            self._start_worker()
            self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                           name='veil-gateway-' + self.server_id)
            self.thread.start()
            return self
        except BaseException as exc:
            self.checkpoint.log(kind='start_error', server=self.server_id, error=ex._safe_display(ex.error_text(exc), 2000))
            self.__exit__(None, None, None)
            raise

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}/mcp'

    def config(self):
        """OpenCode's MCP entry for this gateway."""
        return {'type': 'remote', 'url': self.url, 'oauth': False, 'timeout': 30000,
                'headers': {'Authorization': 'Bearer ' + self.token}}

    def __exit__(self, *_args):
        self.stop()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None
        if self.worker is not None:
            try:
                self.worker.close()
            except Exception:
                pass
            self.worker = None
        while self.companions:
            try:
                self.companions.pop().close()
            except Exception as exc:
                self.checkpoint.log(kind='cleanup_error', server=self.server_id,
                                    error=(str(exc) or type(exc).__name__)[:300])
