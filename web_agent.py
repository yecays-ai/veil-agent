Warning: truncated output (original token count: 24808)
Total output lines: 1968

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
from veil_agent import (ROOT, AUTONOMY_WAKE, GLANCE_WAKE, MAX_PHOTO_BYTES, ActivityState, Budget, Chat, Store, UserError,
                        load_character, now, make_model, make_voice, run_chat,
                        safe_tool_activity_detail)
from autonomy import AutonomyController, GlanceController

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
        self.screen_pop_cond = threading.Condition()
        self.screen_pop_shell_seen = 0.0
        self.screen_pop_requests = OrderedDict()
        self.voice_jobs = {}
        self.voice_jobs_lock = threading.Lock()
        self.voice_job_slots = threading.BoundedSemaphore(MAX_ACTIVE_VOICE_TRANSCRIPTIONS)
        self.transcribing_voice = False
        self.submissions = OrderedDict()  # recent request ids -> normalized text digest
        self.submission_pending = False  # admission reserved while media storage runs
        self.autonomy = None
        self.glance = None
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
            item['source'] = ('瞄一眼' if str(row.get('autonomy_run_id') or '').startswith('glance:') else
                              '自主活动' if row.get('autonomy_run_id') else
                              '长期任务' if row['tool'].startswith('long_task_') else '聊天')
            item['category'] = ('glance' if str(row.get('autonomy_run_id') or '').startswith('glance:') else
                                'memory' if row['tool'] in memory_names else
                                'long_task' if row['tool'].startswith('long_task_') else 'tool')
            if row.get('autonomy_run_id'):
                item['autonomy_run_id'] = row['autonomy_run_id']
            lifecycle = row.get('screen_pop_event')
            if (row.get('tool') == 'screen_pop' and
                    lifecycle in ('shown', 'dismissed', 'finished')):
                item['screen_pop_style'] = row.get('screen_pop_style')
                item['screen_pop_total'] = row.get('screen_pop_total', 1)
                item.setdefault('screen_pop_events', []).append({
                    'event': lifecycle, 'time': row.get('time'),
                    'item': row.get('screen_pop_item'),
                })
            if row.get('state') != '进行中':
                item['finished_at'] = row.get('time')
        for item in projected.values():
            if item.get('tool') != 'screen_pop' or not item.get('screen_pop_events'):
                continue
            total = item.get('screen_pop_total', 1)
            event_name = 'dismissed' if item.get('screen_pop_style') == 'popup' else 'finished'
            completed = {entry.get('item') for entry in item['screen_pop_events']
                         if entry.get('event') == event_name and isinstance(entry.get('item'), int)}
            item['screen_pop_state'] = ('还开着' if item.get('screen_pop_style') == 'popup' else '弹幕播放中')
            if len(completed) >= total:
                item['screen_pop_state'] = ('已关闭' if item.get('screen_pop_style') == 'popup' else '弹幕结束')
            labels = {'shown': 'shown', 'dismissed': 'dismissed', 'finished': 'finished'}
            item['detail'] = '\n'.join(
                f"{labels[event['event']]} at {event['time']}" +
                (f" (item {event['item']})" if isinstance(event.get('item'), int) else '')
                for event in item['screen_pop_events'] if event.get('time'))
        result = [projected[call] for call in order[-limit:]]
        if key is not None:
            self._tool_activity_cache = (key, result)
        return result

    def request_screen_pop(self, command, *, request_id=None, call_id=None, autonomy_run_id=None):
        """Wait for the desktop shell to confirm the requested message was shown."""
        start = time.monotonic()
        shell_deadline = start + 2.0
        with self.screen_pop_cond:
            while time.monotonic() < shell_deadline:
                if self.stopping or self.stopped:
                    return {'state': 'failed', 'reason': 'unavailable'}
                if time.monotonic() - self.screen_pop_shell_seen <= 2.5:
                    break
                self.screen_pop_cond.wait(min(0.25, shell_deadline - time.monotonic()))
            if time.monotonic() - self.screen_pop_shell_seen > 2.5:
                return {'state': 'failed', 'reason': 'unavailable'}
            pop_id = secrets.token_hex(16)
            entry = {
                'id': pop_id, 'command': {**command, 'id': pop_id, 'name': self.name},
                'request_id': request_id, 'call_id': call_id,
                'autonomy_run_id': autonomy_run_id, 'style': command['style'],
                'expected': command['count'] if command['style'] == 'popup' else None,
                'created': time.monotonic(), 'shown': False, 'failure': None,
                'cancelled': False, 'events': set(), 'completed': set(),
            }
            self.screen_pop_requests[pop_id] = entry
            deadline = time.monotonic() + 8.0
            while True:
                if entry['failure']:
                    return {'state': 'failed', 'reason': entry['failure']}
                if entry['shown']:
                    return {'state': 'shown', 'style': entry['style']}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    entry['cancelled'] = True
                    self.screen_pop_cond.notify_all()
                    return {'state': 'failed', 'reason': 'unavailable'}
                if time.monotonic() - self.screen_pop_shell_seen > 3.0:
                    entry['cancelled'] = True
                    return {'state': 'failed', 'reason': 'unavailable'}
                self.screen_pop_cond.wait(min(0.25, remaining))

    def next_screen_pop(self):
        with self.screen_pop_cond:
            current = time.monotonic()
            self.screen_pop_shell_seen = current
            for pop_id, entry in list(self.screen_pop_requests.items()):
                if entry.get('terminal_at') is not None and current - entry['terminal_at'] > 60:
                    self.screen_pop_requests.pop(pop_id, None)
                elif entry['cancelled'] and current - entry['created'] > 60:
                    self.screen_pop_requests.pop(pop_id, None)
            pending = [entry for entry in self.screen_pop_requests.values()
                       if not entry['shown'] and not entry['cancelled'] and not entry['failure']]
            if pending:
                try:
                    from screen_pop import screen_state
                    safe, _, reason = screen_state()
                except Exception:
                    safe, reason = False, 'locked'
                if reason == 'locked' or not safe:
                    for entry in pending:
                        entry['failure'] = 'locked'
                    self.screen_pop_cond.notify_all()
                    pending = []
            self.screen_pop_cond.notify_all()
            return [{'id': entry['id']} for entry in pending[:16]]

    def claim_screen_pop(self, pop_id):
        with self.screen_pop_cond:
            entry = self.screen_pop_requests.get(pop_id)
            if (not entry or entry['shown'] or entry['cancelled'] or entry['failure'] or
                    time.monotonic() - entry['created'] > 8.0 or self.stopping or self.stopped):
                return None
            try:
                from screen_pop import screen_state
                safe, fullscreen, reason = screen_state()
            except Exception:
                safe, fullscreen, reason = False, False, 'locked'
            if reason == 'locked' or not safe:
                entry['failure'] = 'locked'
                self.screen_pop_cond.notify_all()
                return None
            if fullscreen and entry['style'] == 'popup':
                entry['style'] = 'danmaku'
                entry['expected'] = None
                entry['command']['style'] = 'danmaku'
                entry['command']['count'] = 1
            return dict(entry['command'])

    def record_screen_pop_event(self, body):
        pop_id, event = body.get('id'), body.get('event')
        if not isinstance(pop_id, str) or not re.fullmatch(r'[0-9a-f]{32}', pop_id):
            return False
        if event not in ('shown', 'dismissed', 'finished'):
            return False
        with self.screen_pop_cond:
            entry = self.screen_pop_requests.get(pop_id)
            if not entry or entry['cancelled'] or entry['failure']:
                return False
            if event == 'shown':
                style = body.get('style')
                total = body.get('total')
                if style != entry['style'] or isinstance(total, bool) or not isinstance(total, int):
                    return False
                maximum = entry['command']['count'] if style == 'popup' else 32
                if not 1 <= total <= maximum or (style == 'popup' and total != entry['command']['count']):
                    return False
                if entry['shown']:
                    return entry['expected'] == total
                if time.monotonic() - entry['created'] > 8.0:
                    return False
                event_total = total
            else:
                if not entry['shown'] or event != ('dismissed' if entry['style'] == 'popup' else 'finished'):
                    return False
                item = body.get('item')
                if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= entry['expected']:
                    return False
                dedupe = (event, item)
                if dedupe in entry['events']:
                    return True
                event_total = entry['expected']
            item = body.get('item') if event != 'shown' else None
            row = {
                'request_id': entry['request_id'], 'call_id': entry['call_id'],
                'tool': 'screen_pop', 'path': entry['style'], 'state': '成功', 'time': now(),
                'screen_pop_event': event, 'screen_pop_style': entry['style'],
                'screen_pop_total': event_total,
            }
            if item is not None:
                row['screen_pop_item'] = item
            if entry['autonomy_run_id']:
                row['autonomy_run_id'] = entry['autonomy_run_id']
            if not self.chat:
                return False
            # Persist the lifecycle event before changing the in-memory state
            # that acknowledges it to the desktop shell or waiting tool call.
            self.chat.store.append(f'{self.chat.cfg.id}/tools.jsonl', row)
            self._tool_activity_cache = None
            if event == 'shown':
                entry['shown'] = True
                entry['expected'] = event_total
            else:
                entry['events'].add((event, item))
            if event != 'shown':
                entry['completed'].add(item)
                if len(entry['completed']) >= entry['expected']:
                    entry['terminal_at'] = time.monotonic()
            self.screen_pop_cond.notify_all()
  …12808 tokens truncated…read(length))
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
            elif url.path == '/api/glance':
                enabled = body.get('enabled')
                if not isinstance(enabled, bool):
                    self._send(400, {'error': 'enabled'})
                else:
                    try:
                        if not hub.set_glance(enabled):
                            self._send(409, {'error': 'unavailable'})
                        else:
                            self._send(200, {'ok': True, 'enabled': hub.glance.enabled()})
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
    chat.screen_pop_handler = hub.request_screen_pop
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
    hub.glance = GlanceController(chat, hub)
    hub.autonomy.glance = hub.glance
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
            if cfg.glance:
                budget.glance_daily = cfg.glance['daily_budget_cny']
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


