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
import os
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
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from veil_agent import (ROOT, AUTONOMY_WAKE, MAX_PHOTO_BYTES, ActivityState, Budget, Chat, Store, UserError,
                        load_character, now, make_model, run_chat)
from autonomy import AutonomyController

PAGE = Path(__file__).resolve().parent / 'web' / 'index.html'
MAX_BODY = 200_000
MAX_MEDIA_BYTES = MAX_PHOTO_BYTES
MAX_VOICE_BYTES = 10 * 1024 * 1024
MAX_PENDING_MEDIA = 32
PENDING_MEDIA_TTL = 60 * 60
MAX_SUBMISSIONS = 1024
MEDIA_ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')
MEDIA_TYPES = {
    'image/png': ('.png', b'\x89PNG\r\n\x1a\n'),
    'image/jpeg': ('.jpg', b'\xff\xd8\xff'),
    'image/gif': ('.gif', b'GIF8'),
    'image/webp': ('.webp', b'RIFF'),
}
VOICE_TYPES = {
    'audio/webm': '.webm',
    'audio/ogg': '.ogg',
    'audio/mp4': '.m4a',
    'audio/wav': '.wav',
}


def valid_media_row(row):
    if not isinstance(row, dict):
        return False
    media_id, filename, mime = row.get('id'), row.get('filename'), row.get('mime')
    suffix = MEDIA_TYPES[mime][0] if mime in MEDIA_TYPES else VOICE_TYPES.get(mime)
    return (isinstance(media_id, str) and MEDIA_ID_PATTERN.fullmatch(media_id) is not None
            and isinstance(mime, str) and suffix is not None
            and filename == media_id + suffix
            and isinstance(row.get('name'), str) and isinstance(row.get('request_id'), str)
            and (row.get('submission_digest') is None or
                 (isinstance(row.get('submission_digest'), str)
                  and re.fullmatch(r'[0-9a-f]{64}', row['submission_digest']) is not None)))


class ChatInput(str):
    def __new__(cls, text, media, complete=None):
        value = super().__new__(cls, text)
        value.media = media
        value.complete = complete or (lambda _committed: None)
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
        self.submission_pending = False  # admission reserved while media storage runs
        self.autonomy = None
        self.capability_record = lambda: None
        self.defer_outputs = False
        self.deferred_outputs = []
        self.current_capabilities = lambda: 'Current capabilities are not available.'
        self.activity_transition = lambda enabled: self.activity.set(enabled)
        self.autonomy_busy = False
        self.autonomy_interrupted = threading.Event()
        self.media_root = Path(media_root) if media_root else None
        self.media_index = {}
        self.pending_media = OrderedDict()
        self.pending_media_times = {}
        self.media_lock = threading.RLock()
        if self.media_root:
            self.media_root.mkdir(parents=True, exist_ok=True)
            self.upload_root = self.media_root / '.uploads'
            self.upload_root.mkdir(exist_ok=True)
            for upload in self.upload_root.iterdir():
                if upload.is_file():
                    upload.unlink(missing_ok=True)
            index = self.media_root / 'index.jsonl'
            try:
                raw_index = index.read_bytes()
                lines = raw_index.splitlines(keepends=True)
                if lines and not lines[-1].endswith((b'\n', b'\r')):
                    lines.pop()  # an interrupted final append was never committed
                    repaired = index.with_suffix('.repair')
                    repaired.write_bytes(b''.join(lines))
                    repaired.replace(index)
                for raw_line in lines:
                    if not raw_line.strip():
                        continue
                    row = json.loads(raw_line.decode('utf-8'))
                    if not valid_media_row(row):
                        raise ValueError('invalid media index row')
                    self.media_index[row['id']] = row
                    digest = row.get('submission_digest')
                    if digest:
                        self.submissions[row['request_id']] = bytes.fromhex(digest)
                        if len(self.submissions) > MAX_SUBMISSIONS:
                            self.submissions.popitem(last=False)
            except FileNotFoundError:
                pass
            except (OSError, ValueError, UnicodeError) as exc:
                raise UserError('媒体索引无法读取，已停止；请保留 data 文件夹。') from exc
            recovered_voice = False
            voice_mimes = {suffix: mime for mime, suffix in VOICE_TYPES.items()}
            indexed = {row['filename'] for row in self.media_index.values()}
            for candidate in self.media_root.iterdir():
                if not candidate.is_file() or candidate.name in indexed:
                    continue
                match = re.fullmatch(r'([0-9a-f]{32})\.(png|jpg|gif|webp|webm|ogg|m4a|wav)', candidate.name)
                if not match:
                    continue
                media_id, suffix = match.groups()
                mime = voice_mimes.get('.' + suffix)
                if mime:
                    if candidate.is_symlink():
                        continue
                    self.media_index[media_id] = {
                        'id': media_id, 'filename': candidate.name, 'mime': mime,
                        'kind': 'voice', 'name': 'voice', 'request_id': 'voice-' + media_id,
                        'transcript': '',
                        'transcription_error': '上次保存中断，录音已恢复；可以重试识别。'}
                    recovered_voice = True
                else:
                    candidate.unlink(missing_ok=True)
            if recovered_voice:
                self._rewrite_media_index(index)

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

    def pending_media_info(self, media_id):
        with self.media_lock:
            row = self.pending_media.get(media_id)
            return dict(row) if row else None

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
            cutoff = time.monotonic() - PENDING_MEDIA_TTL
            expired = [media_id for media_id, created in self.pending_media_times.items()
                       if created <= cutoff]
            for expired_id in expired:
                expired_row = self.pending_media.pop(expired_id, None)
                self.pending_media_times.pop(expired_id, None)
                if expired_row:
                    (self.upload_root / expired_row['filename']).unlink(missing_ok=True)
            if len(self.pending_media) >= MAX_PENDING_MEDIA:
                return None
            pending_path = self.upload_root / filename
            try:
                pending_path.write_bytes(raw)
            except OSError:
                pending_path.unlink(missing_ok=True)
                raise
            self.pending_media[media_id] = row
            self.pending_media_times[media_id] = time.monotonic()
        return dict(row)

    def save_voice(self, mime, raw, original_name='voice'):
        """Durably save a recording before transcription so failures can be retried."""
        if (self.media_root is None or mime not in VOICE_TYPES or not isinstance(original_name, str)
                or not raw or len(raw) > MAX_VOICE_BYTES):
            return None
        valid_container = ((mime == 'audio/webm' and raw.startswith(b'\x1a\x45\xdf\xa3'))
                           or (mime == 'audio/ogg' and raw.startswith(b'OggS'))
                           or (mime == 'audio/mp4' and len(raw) >= 12 and raw[4:8] == b'ftyp')
                           or (mime == 'audio/wav' and raw.startswith(b'RIFF') and raw[8:12] == b'WAVE'))
        if not valid_container:
            return None
        media_id = secrets.token_hex(16)
        filename = media_id + VOICE_TYPES[mime]
        row = {'id': media_id, 'filename': filename, 'mime': mime, 'kind': 'voice',
               'name': Path(original_name).name[:120] or 'voice', 'request_id': 'voice-' + media_id,
               'transcript': '', 'transcription_error': ''}
        target = self.media_root / filename
        index = self.media_root / 'index.jsonl'
        with self.media_lock:
            try:
                import os
                with target.open('wb', buffering=0) as handle:
                    if handle.write(raw) != len(raw):
                        raise OSError('short voice write')
                    handle.flush()
                    os.fsync(handle.fileno())
                self.media_index[media_id] = row
                self._rewrite_media_index(index)
            except OSError:
                self.media_index.pop(media_id, None)
                target.unlink(missing_ok=True)
                raise
        return dict(row)

    def _rewrite_media_index(self, index=None):
        index = index or self.media_root / 'index.jsonl'
        replacement = index.with_suffix('.tmp')
        data = ''.join(json.dumps(row, ensure_ascii=False) + '\n'
                       for row in self.media_index.values()).encode('utf-8')
        try:
            with replacement.open('wb', buffering=0) as handle:
                if handle.write(data) != len(data):
                    raise OSError('short media index write')
                handle.flush()
                os.fsync(handle.fileno())
            replacement.replace(index)
        except OSError:
            replacement.unlink(missing_ok=True)
            raise

    def transcribe_voice(self, media_id):
        with self.media_lock:
            row = self.media_index.get(media_id)
            if not row or row.get('kind') != 'voice':
                return None, '录音不存在或已损坏。'
            path = self.media_root / row['filename']
            mime, name = row['mime'], row['filename']
        from dotenv import dotenv_values
        key = (dotenv_values(Path(ROOT) / '.env', interpolate=False, encoding='utf-8-sig')
               .get('EMBED_API_KEY') or '').strip()
        if not key:
            error = '语音识别密钥未配置。'
            self._record_voice_result(media_id, '', error)
            return None, error
        try:
            raw = path.read_bytes()
            boundary = '----veil-' + secrets.token_hex(12)
            body = bytearray()
            def field(label, value):
                body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{label}"\r\n\r\n{value}\r\n'.encode())
            field('model', 'FunAudioLLM/SenseVoiceSmall')
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            body.extend(raw)
            body.extend(f'\r\n--{boundary}--\r\n'.encode())
            request = Request('https://api.siliconflow.cn/v1/audio/transcriptions', data=bytes(body),
                              headers={'Authorization': 'Bearer ' + key,
                                       'Content-Type': 'multipart/form-data; boundary=' + boundary})
            with urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode('utf-8'))
            transcript = result.get('text', '').strip() if isinstance(result, dict) else ''
            if not transcript:
                raise ValueError('empty transcript')
        except HTTPError as exc:
            error = '语音识别服务拒绝了请求，请检查密钥后重试。' if exc.code in (401, 403) else '语音识别服务暂时不可用，请稍后重试。'
            self._record_voice_result(media_id, '', error)
            return None, error
        except (URLError, TimeoutError):
            error = '网络连接失败，录音已保留；联网后可以重试识别。'
            self._record_voice_result(media_id, '', error)
            return None, error
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            error = '没有识别出文字，录音已保留；可以重试识别。'
            self._record_voice_result(media_id, '', error)
            return None, error
        self._record_voice_result(media_id, transcript, '')
        return transcript, None

    def _record_voice_result(self, media_id, transcript, error):
        with self.media_lock:
            row = self.media_index.get(media_id)
            if not row:
                return
            row['transcript'] = transcript
            row['transcription_error'] = error
            self._rewrite_media_index()

    def mark_voice_unsent(self, media, error='语音没有交给 Peter；录音已保留，可以重试识别。'):
        if not isinstance(media, dict) or media.get('kind') != 'voice':
            return
        with self.media_lock:
            row = self.media_index.get(media.get('id'))
            if not row:
                return
            row['request_id'] = 'voice-' + row['id']
            row.pop('submission_digest', None)
            row['delivery_error'] = error
            self.submissions.pop(media.get('request_id'), None)
            self._rewrite_media_index()

    def commit_media(self, media_id, request_id, submission_digest):
        """Move one uploaded image into durable history, or return an existing commit."""
        if media_id is None:
            return None
        with self.media_lock:
            existing = self.media_index.get(media_id)
            if existing:
                if existing.get('request_id') == request_id:
                    return dict(existing)
                if existing.get('kind') == 'voice' and existing.get('request_id', '').startswith('voice-'):
                    existing['request_id'] = request_id
                    existing['submission_digest'] = submission_digest.hex()
                    existing.pop('delivery_error', None)
                    self._rewrite_media_index()
                    return dict(existing)
                return None
            pending = self.pending_media.get(media_id)
            if not pending:
                return None
            row = {**pending, 'request_id': request_id,
                   'submission_digest': submission_digest.hex()}
            source = self.upload_root / row['filename']
            target = self.media_root / row['filename']
            index = self.media_root / 'index.jsonl'
            replacement = index.with_suffix('.tmp')
            moved = False
            try:
                import os
                with source.open('rb+') as image_handle:
                    os.fsync(image_handle.fileno())
                source.replace(target)
                moved = True
                data = (json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8')
                previous = index.read_bytes() if index.exists() else b''
                with replacement.open('wb', buffering=0) as handle:
                    combined = previous + data
                    if handle.write(combined) != len(combined):
                        raise OSError('short media index write')
                    handle.flush()
                    os.fsync(handle.fileno())
                replacement.replace(index)
            except OSError:
                try:
                    replacement.unlink(missing_ok=True)
                except OSError:
                    pass
                if moved:
                    try:
                        target.replace(source)
                    except OSError:
                        pass
                raise
            self.pending_media.pop(media_id, None)
            self.pending_media_times.pop(media_id, None)
            self.media_index[media_id] = row
            return dict(row)

    def push(self, kind, text, **metadata):
        with self.cond:
            event_id = len(self.events) + 1
            self.events.append({'id': event_id, 'kind': kind, 'text': text,
                                'time': now(), **metadata})
            return event_id

    def _publish_output(self, text):
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

    def output(self, text):
        with self.cond:
            if self.defer_outputs:
                self.deferred_outputs.append(str(text))
                return
        self._publish_output(text)

    def finish_media_submission(self, committed, text, media, request_id):
        if not committed:
            self.mark_voice_unsent(media)
        with self.cond:
            if committed:
                self.push('user', text, media=media, request_id=request_id)
            buffered = self.deferred_outputs
            self.deferred_outputs = []
            self.defer_outputs = False
        for output in buffered:
            self._publish_output(output)

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
                    with self.cond:
                        self.defer_outputs = True
                        self.deferred_outputs = []
                    return ChatInput(text[0], text[1], text[2] if len(text) > 2 else None)
                return text
            if self.stopping or self.stopped:
                raise EOFError
            if self.autonomy is not None and self.autonomy.due():
                with self.cond:
                    if (not self.stopping and not self.stopped and self.activity.enabled()
                            and self.inbox.empty() and not self.submission_pending):
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
            if isinstance(text, tuple):
                with self.cond:
                    self.defer_outputs = True
                    self.deferred_outputs = []
                return ChatInput(text[0], text[1], text[2] if len(text) > 2 else None)
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
        digest_input = text.encode('utf-8') + b'\0' + (media_id.encode() if isinstance(media_id, str) else b'')
        text_digest = hashlib.sha256(digest_input).digest()
        if media_id is not None and (
                not isinstance(media_id, str) or not MEDIA_ID_PATTERN.fullmatch(media_id)):
            return 'media'
        if media_id is not None and text.lstrip().startswith('/'):
            return 'media_command'
        # Check busy and register the accepted request atomically for all handlers.
        with self.cond:
            if request_id in self.submissions:
                return 'duplicate' if self.submissions[request_id] == text_digest else 'conflict'
            if self.stopping or self.stopped:
                return 'stopped'
            if self.submission_pending:
                return 'busy'
            if self.autonomy_busy:
                if not self.inbox.empty():
                    return 'busy'
            elif self.busy:
                return 'busy'
            self.submission_pending = True
        # Disk writes must not hold the event/approval condition. The reservation
        # excludes concurrent submissions and new autonomy work until publication.
        try:
            try:
                media = self.commit_media(media_id, request_id, text_digest)
            except OSError:
                return 'media_storage'
            if media_id is not None and not media:
                return 'media'
            with self.cond:
                if self.stopping or self.stopped:
                    return 'stopped'
                def complete(committed):
                    self.finish_media_submission(committed, text, media, request_id)
                if not media:
                    self.push('user', text, request_id=request_id)
                self.submissions[request_id] = text_digest
                if len(self.submissions) > MAX_SUBMISSIONS:
                    self.submissions.popitem(last=False)
                self.inbox.put((text, media, complete) if media else text)
                if self.autonomy_busy:
                    self.autonomy_interrupted.set()
                else:
                    self.busy = True
                return 'accepted'
        finally:
            with self.cond:
                self.submission_pending = False
                self.cond.notify_all()

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
                if not self._guard((query.get('t') or [''])[0]):
                    return
                media_id = url.path.rsplit('/', 1)[-1].split('.', 1)[0]
                media = hub.media_info(media_id)
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
                except OSError:
                    self._send(500, {'error': 'image storage failed'})
                else:
                    self._send(201, {'ok': True, 'media': media})
                return
            if url.path == '/api/voice':
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if not 0 < length <= MAX_VOICE_BYTES * 2:
                        raise ValueError
                    body = json.loads(self.rfile.read(length))
                    mime, encoded = body.get('mime'), body.get('data')
                    if not isinstance(mime, str) or not isinstance(encoded, str):
                        raise ValueError
                    raw = base64.b64decode(encoded, validate=True)
                    media = hub.save_voice(mime, raw, body.get('name', 'voice'))
                    if not media:
                        raise ValueError
                except (ValueError, TypeError, base64.binascii.Error):
                    self._send(400, {'error': 'invalid voice'})
                    return
                except OSError:
                    self._send(500, {'error': 'voice storage failed'})
                    return
                transcript, error = hub.transcribe_voice(media['id'])
                media = hub.media_info(media['id'])
                self._send(200, {'ok': True, 'media': media, 'transcript': transcript,
                                 'transcription_error': error})
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
                    supplied_media = body.get('media') if 'media' in body else None
                    if (supplied_media is not None and
                            (not isinstance(supplied_media, dict) or supplied_media.get('id') is None)):
                        result = 'media'
                    else:
                        media_id = supplied_media.get('id') if supplied_media is not None else None
                        result = hub.submit(text, request_id, media_id)
                    if result in ('accepted', 'duplicate'):
                        if result == 'accepted' and text == '/quit':
                            hub.push('notice', '正在保存并退出，稍等几秒，然后可以关掉这个页面。')
                        self._send(200, {'ok': True, 'duplicate': result == 'duplicate'})
                    elif result == 'media':
                        self._send(400, {'error': 'invalid media'})
                    elif result == 'media_command':
                        self._send(400, {'error': 'media cannot be sent with a command'})
                    elif result == 'media_storage':
                        response = {'error': 'media_storage'}
                        if (isinstance(supplied_media, dict) and isinstance(media_id, str)
                                and hub.media_info(media_id)
                                and hub.media_info(media_id).get('kind') == 'voice'):
                            response['media'] = hub.media_info(media_id)
                        self._send(500, response)
                    else:
                        response = {'error': result}
                        if (isinstance(supplied_media, dict) and isinstance(media_id, str)
                                and hub.media_info(media_id)
                                and hub.media_info(media_id).get('kind') == 'voice'):
                            hub.mark_voice_unsent(
                                hub.media_info(media_id),
                                'Peter 正在回复或暂时不可用；录音和识别文字已保留，可以重试发送。')
                            response['media'] = hub.media_info(media_id)
                        self._send(409, response)
            elif url.path == '/api/voice/retry':
                media_id = body.get('media_id')
                if not isinstance(media_id, str) or not MEDIA_ID_PATTERN.fullmatch(media_id):
                    self._send(400, {'error': 'invalid voice'})
                else:
                    transcript, error = hub.transcribe_voice(media_id)
                    media = hub.media_info(media_id)
                    if not media:
                        self._send(404, {'error': 'invalid voice'})
                    else:
                        self._send(200, {'ok': True, 'media': media, 'transcript': transcript,
                                         'transcription_error': error})
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
    files = getattr(chat.tools, 'files', None) if chat.tools else None
    files = files or chat.tools
    announce_photo = getattr(files, 'announce_photo_input', None)
    if callable(announce_photo):
        if announce_photo() is False:
            hub.output('图片能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
        chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
        chat.pending_capability_event_model = chat.pending_capability_event
    announce_voice = getattr(files, 'announce_voice_input', None)
    if callable(announce_voice):
        if announce_voice() is False:
            hub.output('语音能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
        chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
        chat.pending_capability_event_model = getattr(chat.tools, 'capability_event_model', None)
    for message in chat.history_events:
        media = message.get('media') or (hub.media_for_request(message.get('request_id'))
                                         if message['role'] == 'user' else None)
        hub.push(message['role'], message['content'], time=message['time'], history=True,
                 **({'media': media} if media else {}))
    completed_voice_ids = {message.get('media', {}).get('id') for message in chat.history_events
                           if isinstance(message.get('media'), dict)}
    for media in hub.media_index.values():
        if (media.get('kind') == 'voice' and media['id'] not in completed_voice_ids
                and media.get('request_id', '').startswith('voice-')):
            hub.push('user', media.get('transcript') or '', time=now(), history=True, media=dict(media))
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


