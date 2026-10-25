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
import logging
import os
import queue
import re
import secrets
import socket
import ssl
import stat
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, getproxies, urlopen
from urllib.error import HTTPError, URLError

from attachment_utils import (ATTACHMENT_EXTENSIONS, ATTACHMENT_TYPES, MAX_ATTACHMENT_BYTES,
                              inspect_attachment, redirected_path, valid_attachment_row)
from veil_agent import (ROOT, AUTONOMY_WAKE, MAX_PHOTO_BYTES, ActivityState, Budget, Chat, Store, UserError,
                        load_character, now, make_model, make_voice, run_chat,
                        safe_tool_activity_detail)
from autonomy import AutonomyController

MAX_BODY = 200_000
MAX_MEDIA_BYTES = MAX_PHOTO_BYTES
MAX_VOICE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ACTIVE_VOICE_TRANSCRIPTIONS = 4
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
SENSEVOICE_METADATA_TAG = re.compile(
    r'<\|(?:zh|en|yue|ja|ko|nospeech|HAPPY|SAD|ANGRY|NEUTRAL|FEARFUL|DISGUSTED|SURPRISED|'
    r'EMO_UNKNOWN|BGM|Speech|Applause|Laughter|Cry|Sneeze|Breath|Cough|Sing|Speech_Noise|'
    r'withitn|woitn|GBG|Event_UNK)\|>', re.IGNORECASE)
SENSEVOICE_EVENT_EMOJI = '🎼👏😀😭🤧😷'
SENSEVOICE_EMOTION_EMOJI = '😊😔😞😡😰🤢😮'
SENSEVOICE_EVENT_EMOJI_PREFIX = re.compile(rf'^(?:[{re.escape(SENSEVOICE_EVENT_EMOJI)}]\s*)+')
SENSEVOICE_EMOTION_EMOJI_SUFFIX = re.compile(rf'\s*[{re.escape(SENSEVOICE_EMOTION_EMOJI)}]+$')


def clean_voice_transcript(text):
    """Remove SenseVoice rich emotion/event markers from dictation text."""
    text = SENSEVOICE_METADATA_TAG.sub('', text).strip()
    text = SENSEVOICE_EVENT_EMOJI_PREFIX.sub('', text)
    return SENSEVOICE_EMOTION_EMOJI_SUFFIX.sub('', text).strip()
VOICE_MIME = 'audio/mpeg'
MAX_GENERATED_VOICE_BYTES = 25 * 1024 * 1024
VOICE_TRANSPORT_ERRORS = {
    'dns': '语音识别 DNS 解析失败（socket.gaierror），请检查本机网络或 DNS。',
    'tls_certificate': '语音识别 TLS 证书验证失败，请检查系统证书或 HTTPS 检查代理。',
    'tls': '语音识别 TLS 握手失败，请检查系统时间、证书或 HTTPS 代理。',
    'proxy': '语音识别代理连接失败，请检查代理设置。',
    'timeout': '语音识别请求超时，录音已保留；可以稍后重试。',
    'connection_refused': '语音识别连接被拒绝，请检查网络或代理。',
    'socket': '语音识别 socket 连接失败，请检查本机网络。',
    'transport': '语音识别传输失败，请查看 Agent 日志中的异常类型。',
}
logger = logging.getLogger(__name__)


def voice_transport_category(exc):
    """Classify urllib's wrapped transport exception without exposing credentials."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    detail = str(reason).lower()
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'tls_certificate', reason
    if isinstance(reason, ssl.SSLError):
        return 'tls', reason
    if 'proxy' in detail or 'tunnel' in detail or (getproxies().get('https') and isinstance(reason, OSError)):
        return 'proxy', reason
    if isinstance(reason, socket.gaierror):
        return 'dns', reason
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return 'timeout', reason
    if isinstance(reason, ConnectionRefusedError) or getattr(reason, 'winerror', None) == 10061:
        return 'connection_refused', reason
    if isinstance(reason, OSError):
        return 'socket', reason
    return 'transport', reason


def valid_media_row(row):
    if not isinstance(row, dict):
        return False
    if row.get('kind') == 'attachment':
        return valid_attachment_row(row)
    media_id, filename, mime = row.get('id'), row.get('filename'), row.get('mime')
    suffix = (MEDIA_TYPES[mime][0] if mime in MEDIA_TYPES else
              ('.mp3' if mime == VOICE_MIME else VOICE_TYPES.get(mime)))
    return (isinstance(media_id, str) and MEDIA_ID_PATTERN.fullmatch(media_id) is not None
            and isinstance(mime, str) and suffix is not None
            and filename == media_id + suffix
            and isinstance(row.get('name'), str) and isinstance(row.get('request_id'), str)
            and (row.get('submission_digest') is None or
                 (isinstance(row.get('submission_digest'), str)
                  and re.fullmatch(r'[0-9a-f]{64}', row['submission_digest']) is not None)))


class ChatInput(str):
    def __new__(cls, text, media, complete=None, message_id=None):
        value = super().__new__(cls, text)
        value.media = media
        value.complete = complete or (lambda _committed: None)
        value.message_id = message_id
        return value


class ChatAction:
    def __init__(self, action, payload):
        self.action = action
        self.payload = payload


class Hub:
    """Message list shared by the chat loop (main thread) and HTTP handlers."""

    def __init__(self, name, activity, memory_port=None, media_root=None):
        self.name = name
        self.chat = None
        self.activity = activity
        self.memory_url = f'http://127.0.0.1:{memory_port}/dashboard' if memory_port else None
        self.memory_tools = None
        self.events = []
        self.busy = True          # true until the loop is waiting for input
        self.stopping = False
        self.stopped = False
        self.ready = False        # true once the chat loop first waits for input
        self.cond = threading.Condition()
        self.inbox = queue.Queue()
        self.answers = queue.Queue()
        self.pending = None       # event id of the approval being asked, else None
        self.voice_jobs = {}
        self.voice_jobs_lock = threading.Lock()
        self.voice_job_slots = threading.BoundedSemaphore(MAX_ACTIVE_VOICE_TRANSCRIPTIONS)
        self.transcribing_voice = False
        self.submissions = OrderedDict()  # recent request ids -> normalized text digest
        self.submission_pending = False  # admission reserved while media storage runs
        self.autonomy = None
        self.capability_record = lambda: None
        self.defer_outputs = False
        self.deferred_outputs = []
        self.current_capabilities = lambda: 'Current capabilities are not available.'
        self.runtime_status = lambda: {
            'state': 'healthy', 'classification': 'healthy', 'message': '运行正常。'}
        self.activity_transition = lambda enabled: self.activity.set(enabled)
        self.autonomy_busy = False
        self.autonomy_interrupted = threading.Event()
        self.away = []   # tickets 098/102: an AwayBridge per configured channel (email, QQ)
        self.media_root = Path(media_root) if media_root else None
        self.media_index = {}
        self.pending_media = OrderedDict()
        self.pending_media_times = {}
        self.media_lock = threading.RLock()
        if self.media_root:
            self.media_root.mkdir(parents=True, exist_ok=True)
            if redirected_path(self.media_root) or not self.media_root.is_dir():
                raise UserError('附件目录被重定向，已停止；请保留 data 文件夹。')
            self.upload_root = self.media_root / '.uploads'
            self.upload_root.mkdir(exist_ok=True)
            if redirected_path(self.upload_root) or not self.upload_root.is_dir():
                raise UserError('附件临时目录被重定向，已停止；请保留 data 文件夹。')
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
                attachment_match = re.fullmatch(r'([0-9a-f]{32})(\.[a-z0-9]{1,12})', candidate.name)
                if attachment_match and attachment_match.group(2) in ATTACHMENT_EXTENSIONS:
                    candidate.unlink(missing_ok=True)
                    continue
                match = re.fullmatch(r'([0-9a-f]{32})\.(png|jpg|gif|webp|webm|ogg|m4a|wav)', candidate.name)
                if not match:
                    continue
                media_id, suffix = match.groups()
                mime = voice_mimes.get('.' + suffix)
                marker = self.media_root / ('.' + media_id + '.voice-pending')
                if mime and marker.is_file() and not marker.is_symlink():
                    if candidate.is_symlink():
                        continue
                    self.media_index[media_id] = {
                        'id': media_id, 'filename': candidate.name, 'mime': mime,
                        'kind': 'voice', 'name': 'voice', 'request_id': 'voice-' + media_id,
                        'transcript': '',
                        'transcription_error': '上次保存中断，录音已恢复；可以重试识别。'}
                    recovered_voice = True
                elif not mime:
                    candidate.unlink(missing_ok=True)
            if recovered_voice:
                self._rewrite_media_index(index)
            for marker in self.media_root.glob('.' + ('[0-9a-f]' * 32) + '.voice-pending'):
                marker.unlink(missing_ok=True)

    def tool_activity(self, limit=240):
        """Project the append-only tool ledger into one current row per real call."""
        chat = self.chat
        if chat is None:
            return []
        # The desktop polls every 600 ms per window; only re-read the ledger when it changed.
        try:
            stat = (chat.store.root / f'{chat.cfg.id}/tools.jsonl').stat()
            key = (chat.cfg.id, stat.st_size, stat.st_mtime_ns, limit)
        except OSError:
            key = None
        cached = getattr(self, '_tool_activity_cache', None)
        if key is not None and cached and cached[0] == key:
            return cached[1]
        rows = chat.store.read_tail(f'{chat.cfg.id}/tools.jsonl', 5000)
        projected = {}
        order = []
        memory_names = {'hold', 'breath_search', 'trace', 'dream', 'letter_write', 'letter_read',
                        'letter_lock_update', 'memory_history', 'memory_catalog', 'memory_read'}
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get('tool'), str):
                continue
            call_id = row.get('call_id')
            if not isinstance(call_id, str) or not call_id:
                call_id = 'legacy:' + ':'.join(str(row.get(key, '')) for key in
                                               ('request_id', 'time', 'tool', 'path'))
                if call_id == 'legacy::::':
                    call_id += str(index)
            if call_id not in projected:
                projected[call_id] = {'call_id': call_id, 'started_at': row.get('time'),
                                      'request_id': row.get('request_id')}
                order.append(call_id)
            item = projected[call_id]
            item.update({key: row[key] for key in ('tool', 'path', 'state') if key in row})
            if 'detail' in row:
                item['detail'] = safe_tool_activity_detail(row['detail'])
            item['source'] = ('自主活动' if row.get('autonomy_run_id') else
                              '长期任务' if row['tool'].startswith('long_task_') else '聊天')
            item['category'] = ('memory' if row['tool'] in memory_names else
                                'long_task' if row['tool'].startswith('long_task_') else 'tool')
            if row.get('autonomy_run_id'):
                item['autonomy_run_id'] = row['autonomy_run_id']
            if row.get('state') != '进行中':
                item['finished_at'] = row.get('time')
        result = [projected[call] for call in order[-limit:]]
        if key is not None:
            self._tool_activity_cache = (key, result)
        return result

    def media_info(self, media_id):
        with self.media_lock:
            row = self.media_index.get(media_id)
            return dict(row) if row else None

    def attachment_info(self, media_id):
        row = self.media_info(media_id)
        return row if valid_attachment_row(row) else None

    def save_attachment(self, snapshot):
        if self.media_root is None or not isinstance(snapshot, dict):
            return None
        raw = snapshot.get('data')
        try:
            metadata = inspect_attachment(snapshot.get('name'), raw)
        except ValueError:
            return None
        if any(snapshot.get(key) != metadata[key] for key in ('name', 'mime', 'view', 'size')):
            return None
        media_id = secrets.token_hex(16)
        filename = media_id + metadata['suffix']
        row = {'id': media_id, 'filename': filename, 'mime': metadata['mime'],
               'view': metadata['view'], 'kind': 'attachment', 'name': metadata['name'],
               'size': metadata['size'], 'request_id': 'assistant-attachment-' + media_id}
        target = self.media_root / filename
        with self.media_lock:
            if redirected_path(self.media_root) or not self.media_root.is_dir():
                raise OSError('attachment directory redirected')
            if media_id in self.media_index or target.exists() or target.is_symlink():
                return None
            created = False
            try:
                with target.open('xb', buffering=0) as handle:
                    created = True
                    if handle.write(raw) != len(raw):
                        raise OSError('short attachment write')
                    handle.flush()
                    os.fsync(handle.fileno())
                self.media_index[media_id] = row
                created = True
                self._rewrite_media_index()
            except OSError:
                if created:
                    self.media_index.pop(media_id, None)
                    target.unlink(missing_ok=True)
                raise
        return dict(row)

    def read_attachment(self, media_id):
        row = self.attachment_info(media_id)
        if not row:
            return None
        path = self.media_root / row['filename']
        try:
            if (redirected_path(self.media_root) or redirected_path(path)
                    or not path.is_file()):
                return None
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != row['size']:
                return None
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as handle:
                opened = os.fstat(handle.fileno())
                identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
                    return None
                raw = handle.read(MAX_ATTACHMENT_BYTES + 1)
                after_read = os.fstat(handle.fileno())
            after = path.stat(follow_symlinks=False)
            if (len(raw) != row['size'] or len(raw) > MAX_ATTACHMENT_BYTES or
                    any((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) != identity
                        for item in (after_read, after))):
                return None
            try:
                metadata = inspect_attachment(row['name'], raw)
            except ValueError:
                return None
            if any(row.get(key) != metadata[key] for key in ('name', 'mime', 'view', 'size')):
                return None
            return row, raw
        except OSError:
            return None

    def register_voice_media(self, media):
        if not isinstance(media, dict) or media.get('mime') != VOICE_MIME:
            return None
        media_id, filename = media.get('id'), media.get('filename')
        if (not isinstance(media_id, str) or MEDIA_ID_PATTERN.fullmatch(media_id) is None
                or filename != media_id + '.mp3'):
            return None
        row = {key: media[key] for key in ('id', 'filename', 'path', 'mime', 'name') if key in media}
        row['request_id'] = 'assistant-voice-' + media_id
        with self.media_lock:
            self.media_index[media_id] = row
        return dict(row)

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
                or not raw or len(raw) > MAX_VOICE_UPLOAD_BYTES):
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
        marker = self.media_root / ('.' + media_id + '.voice-pending')
        with self.media_lock:
            try:
                import os
                with marker.open('xb', buffering=0) as handle:
                    payload = json.dumps({'id': media_id, 'mime': mime, 'name': row['name']}).encode('utf-8')
                    if handle.write(payload) != len(payload):
                        raise OSError('short voice recovery marker write')
                    handle.flush()
                    os.fsync(handle.fileno())
                with target.open('wb', buffering=0) as handle:
                    if handle.write(raw) != len(raw):
                        raise OSError('short voice write')
                    handle.flush()
                    os.fsync(handle.fileno())
                self.media_index[media_id] = row
                self._rewrite_media_index(index)
                marker.unlink(missing_ok=True)
            except OSError:
                self.media_index.pop(media_id, None)
                target.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
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
        try:
            raw = path.read_bytes()
        except OSError:
            error = '录音文件无法读取，录音已保留；可以重试识别。'
            self._record_voice_result(media_id, '', error)
            return None, error
        transcript, error = self._transcribe_voice_bytes(raw, mime, name)
        self._record_voice_result(media_id, transcript or '', error or '')
        return transcript, error

    def transcribe_voice_bytes(self, raw, mime, name='dictation'):
        """Transcribe an in-memory dictation without retaining it as a voice message."""
        return self._transcribe_voice_bytes(raw, mime, name)

    def _transcribe_voice_bytes(self, raw, mime, name):
        from dotenv import dotenv_values
        key = (dotenv_values(Path(ROOT) / '.env', interpolate=False, encoding='utf-8-sig')
               .get('EMBED_API_KEY') or '').strip()
        if not key:
            return None, '语音识别密钥未配置。'
        try:
            started = time.monotonic()
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
                trace_id = getattr(response, 'headers', {}).get('x-siliconcloud-trace-id', '')
                result = json.loads(response.read().decode('utf-8'))
            transcript = result.get('text', '').strip() if isinstance(result, dict) else ''
            transcript = clean_voice_transcript(transcript)
            if not transcript:
                raise ValueError('empty transcript')
            logger.info('SenseVoice transcription complete: elapsed_seconds=%.2f audio_bytes=%d trace_id=%s',
                        time.monotonic() - started, len(raw), trace_id or 'unavailable')
        except HTTPError as exc:
            logger.warning('SenseVoice HTTP failure: category=%s status=%s',
                           'proxy' if exc.code == 407 else 'http', exc.code)
            error = ('语音识别代理认证失败（HTTP 407），请检查代理设置。' if exc.code == 407 else
                     f'语音识别服务返回 HTTP {exc.code}，请检查密钥或服务权限。'
                     if exc.code in (401, 403) else f'语音识别服务返回 HTTP {exc.code}，请稍后重试。')
            return None, error
        except (URLError, TimeoutError, socket.timeout) as exc:
            category, reason = voice_transport_category(exc)
            logger.warning('SenseVoice transport failure: category=%s exception=%s errno=%s winerror=%s',
                           category, type(reason).__name__, getattr(reason, 'errno', None),
                           getattr(reason, 'winerror', None))
            error = VOICE_TRANSPORT_ERRORS[category]
            return None, error
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            logger.warning('SenseVoice response failure: exception=%s', type(exc).__name__)
            error = 'SenseVoice 返回内容无法解析或没有文字，录音已保留；可以重试识别。'
            return None, error
        return transcript, None

    def start_voice_transcription(self, media_id):
        """Start one background transcription and return its completion event."""
        with self.voice_jobs_lock:
            current = self.voice_jobs.get(media_id)
            if current:
                return current
            media = self.media_info(media_id)
            if media and media.get('transcript'):
                finished = threading.Event()
                finished.set()
                return finished
            if not self.voice_job_slots.acquire(blocking=False):
                self._record_voice_result(media_id, '', '语音识别任务较多，录音已保留；可以稍后重试识别。')
                return None
            finished = threading.Event()
            self.voice_jobs[media_id] = finished
        def run():
            try:
                self.transcribe_voice(media_id)
            finally:
                finished.set()
                with self.voice_jobs_lock:
                    if self.voice_jobs.get(media_id) is finished:
                        self.voice_jobs.pop(media_id, None)
                self.voice_job_slots.release()
        threading.Thread(target=run, name='voice-transcription', daemon=True).start()
        return finished

    def retry_voice_transcription(self, media_id, timeout=50):
        """Reuse an active job, or start one retry, and return its durable result."""
        finished = self.start_voice_transcription(media_id)
        if not finished:
            media = self.media_info(media_id)
            return None, (media or {}).get('transcription_error') or '语音识别任务较多，请稍后重试。'
        if not finished.wait(timeout):
            return None, '语音识别仍在进行；录音已保留，请稍后再看。'
        media = self.media_info(media_id)
        if not media:
            return None, '录音不存在或已损坏。'
        transcript = (media.get('transcript') or '').strip()
        return transcript or None, (None if transcript else media.get('transcription_error') or '没有识别出文字，录音已保留；可以重试识别。')

    def _chat_input(self, queued):
        if not isinstance(queued, tuple):
            return queued
        text, media, complete = queued[0], queued[1], queued[2] if len(queued) > 2 else None
        message_id = queued[3] if len(queued) > 3 else None
        if isinstance(media, dict) and media.get('kind') == 'voice':
            transcript = (media.get('transcript') or '').strip()
            if not transcript:
                with self.voice_jobs_lock:
                    finished = self.voice_jobs.get(media.get('id'))
                if not finished:
                    finished = self.start_voice_transcription(media.get('id'))
                if finished:
                    with self.cond:
                        self.transcribing_voice = True
                    deadline = time.monotonic() + 55
                    while not finished.wait(min(0.2, max(0, deadline - time.monotonic()))):
                        if self.stopping or self.stopped:
                            with self.cond:
                                self.transcribing_voice = False
                            if callable(complete):
                                complete(False)
                            raise EOFError
                        if time.monotonic() >= deadline:
                            with self.cond:
                                self.transcribing_voice = False
                            media = self.media_info(media.get('id')) or media
                            media['transcription_error'] = '语音识别耗时较长，录音已保留；可以重试识别。'
                            self.push('voice_status', '', media=media)
                            if callable(complete):
                                complete(False)
                            with self.cond:
                                self.busy = False
                            return None
                    with self.cond:
                        self.transcribing_voice = False
                media = self.media_info(media.get('id')) or media
                transcript = (media.get('transcript') or '').strip()
            if not transcript:
                if callable(complete):
                    complete(False)
                with self.media_lock:
                    row = self.media_index.get(media.get('id'))
                    if row:
                        row.pop('delivery_error', None)
                        media = dict(row)
                self.push('voice_status', '', media=media)
                with self.cond:
                    self.busy = False
                return None
            text = '[voice message] ' + transcript
            self.push('voice_status', '', media=media)
        if media:
            with self.cond:
                self.defer_outputs = True
                self.deferred_outputs = []
        return ChatInput(text, media, complete, message_id)

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
            row.pop('delivery_pending', None)
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
                    existing['delivery_pending'] = True
                    existing.pop('delivery_error', None)
                    self._rewrite_media_index()
                    return dict(existing)
                return None
            pending = self.pending_media.get(media_id)
            if not pending:
                return None
            row = {**pending, 'request_id': request_id,
                   'submission_digest': submission_digest.hex()}
            if row.get('kind') == 'voice':
                row['delivery_pending'] = True
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
        media = getattr(text, 'media', None)
        attachments = getattr(text, 'attachments', None)
        message_id = getattr(text, 'message_id', None)
        request_id = getattr(text, 'request_id', None)
        memory_refs = getattr(text, 'memory_refs', None)
        text = str(text)
        prefix = f'{self.name}: '
        if text.startswith('新对话已开始；'):
            self.push('reset', text)
        elif text.startswith(prefix):
            voice = self.register_voice_media(media)
            safe_attachments = []
            for item in attachments if isinstance(attachments, list) else []:
                attachment = self.attachment_info(item.get('id')) if isinstance(item, dict) else None
                if attachment:
                    safe_attachments.append(attachment)
            for bridge in self.away:
                bridge.assistant_message(text[len(prefix):], safe_attachments)
            self.push('assistant', text[len(prefix):],
                      **({'media': voice} if voice else {}),
                      **({'attachments': safe_attachments} if safe_attachments else {}),
                      **({'message_id': message_id} if isinstance(message_id, str) else {}),
                      **({'request_id': request_id} if isinstance(request_id, str) else {}),
                      **({'memory_refs': memory_refs} if isinstance(memory_refs, list) and memory_refs else {}))
        elif text.startswith('请求失败，未自动重试'):
            self.push('error', text)
        elif text.startswith('接上了'):
            self.push('notice', text.split('。')[0] + '。')   # drop the terminal-only command hints
        else:
            self.push('notice', text)

    def output(self, text):
        with self.cond:
            if self.defer_outputs:
                self.deferred_outputs.append(text)
                return
        self._publish_output(text)

    def finish_media_submission(self, committed, text, media, request_id):
        if not committed:
            self.mark_voice_unsent(media)
        elif isinstance(media, dict) and media.get('kind') == 'voice':
            with self.media_lock:
                row = self.media_index.get(media.get('id'))
                if row and row.get('delivery_pending'):
                    row.pop('delivery_pending', None)
                    try:
                        self._rewrite_media_index()
                    except OSError:
                        row['delivery_pending'] = True
                if row:
                    media = dict(row)
        with self.cond:
            if committed and media:
                self.push('user', text, media=media, request_id=request_id, message_id=request_id)
            buffered = self.deferred_outputs
            self.deferred_outputs = []
            self.defer_outputs = False
        for output in buffered:
            self._publish_output(output)

    def input_fn(self, _prompt=''):
        for bridge in self.away:
            bridge.turn_ended()
        item = self._input_fn()
        for bridge in self.away:
            bridge.turn_started(item)
        return item

    def _input_fn(self):
        with self.cond:
            self.busy = False
            self.ready = True
        while True:
            try:
                text = self.inbox.get_nowait()
            except queue.Empty:
                text = None
            if text is not None:
                if isinstance(text, ChatAction):
                    with self.cond:
                        self.busy = True
                    return text
                with self.cond:
                    self.busy = True
                item = self._chat_input(text)
                if item is not None:
                    return item
                continue
            if self.stopping or self.stopped:
                raise EOFError
            if self.autonomy is not None and self.autonomy.due():
                with self.cond:
                    if (not self.stopping and not self.stopped and self.activity.enabled()
                            and self.inbox.empty() and not self.submission_pending
                            and self.autonomy.claim_due()):
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
            if isinstance(text, ChatAction):
                return text
            item = self._chat_input(text)
            if item is not None:
                return item

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
                    self.push('user', text, request_id=request_id, message_id=request_id)
                self.submissions[request_id] = text_digest
                if len(self.submissions) > MAX_SUBMISSIONS:
                    self.submissions.popitem(last=False)
                self.inbox.put((text, media, complete, request_id))
                if self.autonomy_busy:
                    self.autonomy_interrupted.set()
                else:
                    self.busy = True
                return 'accepted'
        finally:
            with self.cond:
                self.submission_pending = False
                self.cond.notify_all()

    def message_action(self, action, message_id=None, *, message_ids=None, content=None, favorite=None):
        allowed = ('delete', 'recall', 'regenerate', 'edit', 'favorite')
        if action not in allowed or self.chat is None:
            return '无效的消息操作。'
        ids = message_ids if action == 'delete' and message_ids is not None else [message_id]
        if (not isinstance(ids, list) or not ids or len(ids) > 100
                or any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', item) for item in ids)):
            return '无效的消息操作。'
        with self.cond:
            if self.stopping or self.stopped:
                return 'Agent 服务已退出。'
            if self.busy or self.submission_pending or self.autonomy_busy:
                return 'Peter 正在处理消息，请稍后再试。'
            self.busy = True
        queued = False
        try:
            if action == 'regenerate':
                user, removed = self.chat.prepare_regeneration(message_id)
                with self.cond:
                    self.inbox.put(ChatAction('regenerate', {'user': user, 'remove_ids': removed}))
                    queued = True
                return '已开始重新生成。'
            if action == 'delete':
                rows = self.chat.delete_messages(ids)
                publish_removed = getattr(self.chat, 'message_removed', None)
                if callable(publish_removed):
                    publish_removed(rows)
                else:
                    for row in rows:
                        self.push('message_removed', '', message_id=row['message_id'])
                        try:
                            self._remove_unreferenced_media([row.get('media'), *(row.get('attachments') or [])])
                        except (OSError, UserError):
                            pass
                return '已删除所选消息。' if len(rows) > 1 else '消息已删除。'
            if action == 'edit':
                row = self.chat.edit_message(message_id, content)
                self.push('message_updated', '', message_id=message_id, text=row['content'], favorite=row.get('favorite', False))
                return '消息已更新。'
            if action == 'favorite':
                row = self.chat.favorite_message(message_id, favorite)
                self.push('message_updated', '', message_id=message_id, favorite=row['favorite'])
                return '已收藏消息。' if favorite else '已取消收藏。'
            row = self.chat.recall_message(message_id)
            publish_recalled = getattr(self.chat, 'message_recalled', None)
            if callable(publish_recalled):
                publish_recalled(row)
            else:
                self.push('message_recalled', '[Message recalled]', message_id=message_id)
                try:
                    self._remove_unreferenced_media([row.get('media'), *(row.get('attachments') or [])])
                except (OSError, UserError):
                    pass
            return '消息已撤回。'
        except UserError as exc:
            return str(exc)
        except OSError:
            return '消息记录未能安全更新，请保留数据后重试。'
        finally:
            if not queued:
                with self.cond:
                    self.busy = False
                    self.cond.notify_all()

    def message_memory(self, message_id):
        if self.chat is None or not isinstance(message_id, str):
            raise UserError('无效的消息。')
        _, row = self.chat.message_record(message_id)
        if row.get('role') != 'assistant':
            raise UserError('只有 Peter 的回复有记忆引用。')
        refs = row.get('memory_refs', [])
        if not isinstance(refs, list):
            refs = []
        return list(dict.fromkeys(ref for ref in refs
                                  if isinstance(ref, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ref)))[:6]

    def _remove_unreferenced_media(self, media):
        candidates = media if isinstance(media, list) else [media]
        media_ids = {item.get('id') for item in candidates
                     if isinstance(item, dict) and isinstance(item.get('id'), str)}
        for media_id in media_ids:
            if any(media_id in ({(row.get('media') or {}).get('id')} |
                                {item.get('id') for item in (row.get('attachments') or [])
                                 if isinstance(item, dict)})
                   for row in self.chat.store.read(self.chat.path)):
                continue
            with self.media_lock:
                row = self.media_index.pop(media_id, None)
                if not row:
                    continue
                try:
                    self._rewrite_media_index()
                except OSError:
                    self.media_index[media_id] = row
                    raise
            try:
                (self.media_root / row['filename']).unlink(missing_ok=True)
            except OSError:
                # The chat ledger and media index no longer reference it. A locked
                # file is harmless and can be reclaimed on a later startup.
                pass

    def approval(self, prompt):
        # Same sentence the terminal shows; the page offers two buttons for y/N.
        with self.cond:
            while not self.answers.empty():
                self.answers.get_nowait()
            self.pending = self.push('approval', prompt)
        for bridge in self.away:
            # Ticket 114: a task Lan gave on QQ can be approved from QQ too.
            bridge.approval_asked(prompt)
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
        tool_activity = self.tool_activity()
        with self.cond:
            autonomy = self.autonomy.status() if self.autonomy else {}
            return {'events': self.events[after:], 'busy': self.busy and not self.autonomy_busy,
                    'transcribing_voice': self.transcribing_voice,
                    'stopping': self.stopping and not self.stopped, 'stopped': self.stopped,
                    'name': self.name, 'pending': self.pending, 'ready': self.ready,
                    'activity_enabled': self.activity.enabled(),
                    'current_capabilities': self.current_capabilities(),
                    'capability_record': self.capability_record(),
                    'runtime_status': self.runtime_status(),
                    'tool_activity': tool_activity,
                    'memory_panel_url': self.memory_url,
                    'memory_available': self.memory_tools is not None, **autonomy}


def make_handler(hub, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # never log requests: URLs carry the token

        def _send(self, code, body, ctype='application/json; charset=utf-8', extra_headers=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
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
            if url.path in ('/api/memory/catalog', '/api/memory/read'):
                if not self._guard(self.headers.get('X-Token')):
                    return
                if hub.memory_tools is None:
                    self._send(404, {'error': 'Ombre memory is not available.'})
                    return
                if url.path == '/api/memory/catalog':
                    cursor = query.get('after', [''])[0]
                    rendered, state, _ = hub.memory_tools.invoke('memory_catalog', {'after': cursor})
                    if state != '成功':
                        self._send(400 if state == '被拒绝' else 503, {'error': rendered})
                    else:
                        try:
                            self._send(200, json.loads(rendered))
                        except (TypeError, ValueError):
                            self._send(503, {'error': 'Ombre memory catalog is temporarily unavailable.'})
                else:
                    memory_id = query.get('id', [''])[0]
                    try:
                        offset = int(query.get('offset', ['0'])[0])
                    except (TypeError, ValueError):
                        self._send(400, {'error': 'Invalid memory offset.'})
                        return
                    args = {'memory_id': memory_id, 'offset': offset}
                    version = query.get('version', [None])[0]
                    if version is not None:
                        args['version'] = version
                    rendered, state, _ = hub.memory_tools.invoke('memory_read', args)
                    if state != '成功':
                        self._send(404 if state == '被拒绝' else 503, {'error': rendered})
                    else:
                        try:
                            self._send(200, json.loads(rendered))
                        except (TypeError, ValueError):
                            self._send(503, {'error': 'Ombre memory could not be read right now.'})
            elif url.path in ('/api/attachment', '/api/attachment-text'):
                if not self._guard(self.headers.get('X-Token')):
                    return
                media_ids = query.get('id', [])
                if len(query) != 1 or len(media_ids) != 1 or not MEDIA_ID_PATTERN.fullmatch(media_ids[0]):
                    self._send(400, {'error': 'invalid attachment'})
                    return
                saved = hub.read_attachment(media_ids[0])
                if not saved:
                    self._send(404, {'error': 'attachment not found'})
                    return
                media, raw = saved
                if url.path == '/api/attachment':
                    self._send(200, {'attachment': media})
                elif media.get('view') != 'text':
                    self._send(415, {'error': 'attachment is not plain text'})
                else:
                    try:
                        self._send(200, {'text': raw.decode('utf-8')})
                    except UnicodeDecodeError:
                        self._send(415, {'error': 'attachment is not UTF-8 text'})
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
                    path = hub.media_root / media['filename']
                    if media.get('kind') == 'attachment':
                        saved = hub.read_attachment(media_id)
                        if not saved:
                            raise OSError('attachment unavailable')
                        _, attachment_data = saved
                        size = len(attachment_data)
                    else:
                        attachment_data = None
                    limit = (MAX_GENERATED_VOICE_BYTES if media.get('mime') == VOICE_MIME else
                             (MAX_VOICE_UPLOAD_BYTES if media.get('kind') == 'voice' else
                              (MAX_ATTACHMENT_BYTES if media.get('kind') == 'attachment' else MAX_MEDIA_BYTES)))
                    if attachment_data is None:
                        size = path.stat().st_size
                    if size > limit:
                        raise OSError('media too large')
                    status = 200
                    start, end = 0, size - 1
                    range_header = self.headers.get('Range', '')
                    range_match = (re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
                                   if len(range_header) <= 128 else None)
                    if range_match:
                        first, last = range_match.groups()
                        if first:
                            start = int(first)
                            end = int(last) if last else size - 1
                        elif last:
                            suffix_length = int(last)
                            start = max(0, size - suffix_length)
                            end = size - 1
                        if start >= size or start > end or end < 0 or not first and not last:
                            self._send(416, b'', media['mime'], {
                                'Accept-Ranges': 'bytes', 'Content-Range': f'bytes */{size}'})
                            return
                        end = min(end, size - 1)
                        status = 206
                    length = max(0, end - start + 1)
                    if attachment_data is not None:
                        data = attachment_data[start:start + length]
                    else:
                        with path.open('rb') as handle:
                            handle.seek(start)
                            data = handle.read(length)
                    if len(data) != length:
                        raise OSError('media too large')
                except OSError:
                    self._send(404, {'error': 'not found'})
                else:
                    headers = {'Accept-Ranges': 'bytes'}
                    content_type = media['mime']
                    if media.get('kind') == 'attachment':
                        if media.get('view') == 'html':
                            content_type = 'application/octet-stream'
                            headers['Content-Disposition'] = f'attachment; filename="{media["filename"]}"'
                        elif media.get('view') == 'text':
                            content_type = 'text/plain; charset=utf-8'
                    if status == 206:
                        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
                    self._send(status, data, content_type, headers)
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
            if url.path == '/api/transcribe':
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if not 0 < length <= MAX_VOICE_UPLOAD_BYTES * 2:
                        raise ValueError
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError
                    mime, encoded = body.get('mime'), body.get('data')
                    if not isinstance(mime, str) or mime not in VOICE_TYPES or not isinstance(encoded, str):
                        raise ValueError
                    raw = base64.b64decode(encoded, validate=True)
                    if not raw or len(raw) > MAX_VOICE_UPLOAD_BYTES:
                        raise ValueError
                except (ValueError, TypeError, base64.binascii.Error):
                    self._send(400, {'error': 'invalid voice'})
                    return
                transcript, error = hub.transcribe_voice_bytes(raw, mime, 'dictation' + VOICE_TYPES[mime])
                self._send(200, {'ok': True, 'transcript': transcript or '',
                                 'transcription_error': error or ''})
                return
            if url.path == '/api/voice':
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if not 0 < length <= MAX_VOICE_UPLOAD_BYTES * 2:
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
                if body.get('send_first') is True:
                    hub.start_voice_transcription(media['id'])
                    media = hub.media_info(media['id'])
                    self._send(202, {'ok': True, 'media': media})
                else:
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
            if url.path == '/api/message/action':
                action, message_id = body.get('action'), body.get('message_id')
                if action == 'memory':
                    try:
                        items = hub.message_memory(message_id)
                    except UserError as exc:
                        self._send(409, {'ok': False, 'message': str(exc)})
                    else:
                        self._send(200, {'ok': True, 'items': items})
                    return
                result = hub.message_action(action, message_id, message_ids=body.get('message_ids'),
                                            content=body.get('content'), favorite=body.get('favorite'))
                accepted = result in ('已开始重新生成。', '消息已删除。', '已删除所选消息。', '消息已撤回。',
                                      '消息已更新。', '已收藏消息。', '已取消收藏。')
                self._send(200 if accepted else 409,
                           {'ok': accepted, 'message': result, 'action': action if accepted else None})
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
                    transcript, error = hub.retry_voice_transcription(media_id)
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


def serve_chat(chat, hub, channels=()):
    hub.chat = chat
    files = getattr(chat.tools, 'files', None) if chat.tools else None
    files = files or chat.tools
    if (getattr(files, 'lab', None) is not None and
            callable(getattr(files, 'read_lab_attachment', None))):
        chat.attachment_reader = files.read_lab_attachment
        chat.attachment_saver = hub.save_attachment
        chat.attachment_discarder = hub._remove_unreferenced_media
        announce_attachments = getattr(files, 'announce_file_attachments', None)
        if callable(announce_attachments):
            if announce_attachments() is False:
                hub.output('附件能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
            chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
            chat.pending_capability_event_model = getattr(chat.tools, 'capability_event_model', None)
    def publish_recalled(row):
        hub.push('message_recalled', '[Message recalled]', message_id=row['message_id'])
        try:
            hub._remove_unreferenced_media([row.get('media'), *(row.get('attachments') or [])])
        except (OSError, UserError):
            pass
    chat.message_recalled = publish_recalled
    def publish_removed(rows):
        for row in rows:
            hub.push('message_removed', '', message_id=row['message_id'])
            try:
                hub._remove_unreferenced_media([row.get('media'), *(row.get('attachments') or [])])
            except (OSError, UserError):
                pass
    chat.message_removed = publish_removed
    announce_desktop = getattr(files, 'announce_desktop_freedom', None)
    if callable(announce_desktop):
        if announce_desktop() is False:
            hub.output('桌面能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
        chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
        chat.pending_capability_event_model = getattr(chat.tools, 'capability_event_model', None)
    announce_photo = getattr(files, 'announce_photo_input', None)
    if callable(announce_photo):
        if announce_photo() is False:
            hub.output('图片能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
        chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
        chat.pending_capability_event_model = chat.pending_capability_event
    announce_voice_input = getattr(files, 'announce_voice_input', None)
    if callable(announce_voice_input):
        if announce_voice_input() is False:
            hub.output('语音能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
        chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
        chat.pending_capability_event_model = getattr(chat.tools, 'capability_event_model', None)
    announce_voice = getattr(files, 'announce_voice_output', None)
    if chat.voice is not None and callable(announce_voice):
        if announce_voice() is False:
            hub.output('语音能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
        chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
        chat.pending_capability_event_model = chat.pending_capability_event
    elif chat.voice is None:
        suppress_voice = getattr(files, 'suppress_voice_output_notice', None)
        if callable(suppress_voice):
            suppress_voice()
            chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
            chat.pending_capability_event_model = getattr(chat.tools, 'capability_event_model', None)
    for message in chat.history_events:
        media = message.get('media') or (hub.media_for_request(message.get('request_id'))
                                         if message['role'] == 'user' else None)
        if message['role'] == 'assistant' and isinstance(media, dict):
            media = hub.register_voice_media(media)
        attachments = [hub.attachment_info(item.get('id')) for item in message.get('attachments', [])
                       if isinstance(item, dict)]
        attachments = [item for item in attachments if item]
        hub.push(message['role'], message['content'], time=message['time'], history=True,
                 message_id=message['message_id'], request_id=message.get('request_id'),
                 favorite=message.get('favorite') is True,
                 memory_refs=message.get('memory_refs', []),
                 **({'attachments': attachments} if attachments else {}),
                 **({'media': media} if media else {}))
    # The UI history is a bounded window; replay decisions must use the durable
    # full chat log or an older successful voice send looks pending on restart.
    completed_voice_ids = {
        message.get('media', {}).get('id') for message in chat.store.read(chat.path)
        if isinstance(message.get('media'), dict) and message['media'].get('kind') == 'voice'
    }
    for media in hub.media_index.values():
        if (media.get('kind') == 'voice' and media['id'] not in completed_voice_ids
                and (media.get('request_id') == 'voice-' + media['id'] or media.get('delivery_pending'))):
            hub.push('user', media.get('transcript') or '', time=now(), history=True, media=dict(media))
    if channels:
        from away_bridge import AwayBridge
    for channel in channels:
        bridge = AwayBridge(channel, hub)
        hub.away.append(bridge)
        chat.away_channels[channel.name] = bridge.requested
        if channel.name == 'email':
            chat.email_attachment_admission = bridge.can_add_reply_attachment
        announce = getattr(files, 'announce_' + channel.name, None)
        if callable(announce):
            if announce() is False:
                hub.output(f'{channel.label}能力通知暂时无法保存；聊天仍可使用，下次启动会重试。')
            chat.pending_capability_event = getattr(chat.tools, 'capability_event', None)
            chat.pending_capability_event_model = getattr(chat.tools, 'capability_event_model', None)
    hub.autonomy = AutonomyController(chat, hub)
    hub.capability_record = lambda: chat.capability_history_record
    hub.activity_transition = lambda enabled: chat.set_activity_enabled(hub.activity, enabled)
    chat.activity_enabled = hub.activity.enabled
    hub.current_capabilities = chat.current_capability_snapshot
    if chat.runtime_monitor:
        hub.runtime_status = lambda: chat.runtime_monitor.snapshot(chat.long_tasks, chat.tools)
    for bridge in hub.away:
        bridge.start()
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
    parser = argparse.ArgumentParser(description='VEIL 本机聊天服务（桌面版后端）')
    parser.add_argument('--character', default='peter')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true', help=argparse.SUPPRESS)  # kept for older desktop builds
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
            voice = make_voice(ROOT, cfg, store, budget)
            from email_channel import EmailChannel
            from qq_channel import QQChannel
            channels = [channel for channel in (EmailChannel.from_env(ROOT, cfg, store.root),
                                                QQChannel.from_env(ROOT, cfg, store.root)) if channel]
            activity = ActivityState(store.root / cfg.id / 'resident-state.json')
            from runtime_monitor import RuntimeMonitor
            runtime_monitor = RuntimeMonitor(store, cfg.id, activity)
            memory_port = (cfg.memory or {}).get('ombre_port') if (cfg.memory or {}).get('enabled') else None
            hub = Hub(cfg.name, activity, memory_port, store.root / cfg.id / 'media')
            token = secrets.token_urlsafe(24)
            try:
                server = start_server(hub, token, args.port)
            except OSError:
                raise UserError('网页服务未能启动；请稍后重试。')
            port = server.server_address[1]
            save_endpoint(endpoint_path, port, token)
            failed = False
            try:
                with client:
                    from tools_agent import FilesystemTools
                    from web_tools import WebTools
                    with FilesystemTools(ROOT, cfg, store, web_factory=WebTools) as tools:
                        if cfg.memory and cfg.memory.get('enabled'):
                            from memory_agent import Memory, open_ombre
                            with open_ombre(ROOT, cfg) as backend:
                                with Memory(cfg, store, budget, model, backend, runtime_monitor) as memory:
                                    from memory_native import NativeMemoryTools, ToolSet
                                    tools = ToolSet(tools, NativeMemoryTools(
                                        memory, cfg.tools['max_result_bytes']))
                                    hub.memory_tools = tools.native
                                    serve_chat(Chat(cfg, store, budget, model, memory, tools,
                                                    approval=hub.approval, output=hub.output,
                                                    voice=voice, runtime_monitor=runtime_monitor), hub, channels)
                                for notice in memory.notices():
                                    hub.output(notice)
                        else:
                            serve_chat(Chat(cfg, store, budget, model, tools=tools,
                                            approval=hub.approval, output=hub.output,
                                            voice=voice, runtime_monitor=runtime_monitor), hub, channels)
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


