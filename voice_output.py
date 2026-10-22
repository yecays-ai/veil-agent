"""MiniMax voice output. Secrets are read locally and never logged or persisted."""
from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid


MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_RESPONSE_BYTES = MAX_AUDIO_BYTES * 2 + 64 * 1024
REGION_HOSTS = {'cn': 'https://api.minimaxi.com', 'global': 'https://api.minimax.io'}
CJK = re.compile(r'[\u3400-\u9fff]')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


DEFAULT_OPEN = urllib.request.build_opener(_NoRedirect()).open


@dataclass(frozen=True)
class VoiceConfig:
    api_key: str
    voice_id: str
    model: str
    region: str
    cny_per_10k_chars: Decimal


class VoiceError(Exception):
    def __init__(self, user_message, *, outcome_unknown=False):
        super().__init__(user_message)
        self.user_message = user_message
        self.outcome_unknown = outcome_unknown


def load_voice_config(root):
    """Return a complete valid local config, otherwise keep the feature hidden."""
    from dotenv import dotenv_values
    values = dotenv_values(Path(root) / '.env', interpolate=False, encoding='utf-8-sig')
    required = {name: str(values.get(name) or '').strip() for name in (
        'MINIMAX_API_KEY', 'MINIMAX_VOICE_ID', 'MINIMAX_MODEL', 'MINIMAX_REGION')}
    if not all(required.values()) or required['MINIMAX_REGION'].lower() not in REGION_HOSTS:
        return None
    try:
        rate = Decimal(str(values.get('MINIMAX_CNY_PER_10K_CHARS') or '3.5').strip())
        if not rate.is_finite() or rate <= 0:
            return None
    except Exception:
        return None
    return VoiceConfig(required['MINIMAX_API_KEY'], required['MINIMAX_VOICE_ID'],
                       required['MINIMAX_MODEL'], required['MINIMAX_REGION'].lower(), rate)


def billed_characters(text):
    return sum(2 if CJK.fullmatch(char) else 1 for char in text)


def _read_bounded(response):
    declared = response.headers.get('Content-Length')
    if declared:
        try:
            if int(declared) > MAX_RESPONSE_BYTES:
                raise VoiceError('MiniMax 返回的音频过大。')
        except ValueError:
            pass
    chunks, total = [], 0
    while True:
        chunk = response.read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise VoiceError('MiniMax 返回的音频过大。')
        chunks.append(chunk)


class MiniMaxVoice:
    def __init__(self, config, store, budget, character, *, opener=DEFAULT_OPEN):
        self.config, self.store, self.budget, self.character = config, store, budget, character
        self.opener = opener
        self.media_root = store.root / character / 'media'

    def _request(self, text):
        cfg = self.config
        request = urllib.request.Request(
            REGION_HOSTS[cfg.region] + '/v1/t2a_v2', method='POST',
            headers={'Authorization': f'Bearer {cfg.api_key}', 'Content-Type': 'application/json'},
            data=json.dumps({
                'model': cfg.model, 'text': text, 'stream': False, 'output_format': 'hex',
                'voice_setting': {'voice_id': cfg.voice_id},
                'audio_setting': {'format': 'mp3', 'sample_rate': 32000,
                                  'bitrate': 128000, 'channel': 1},
            }).encode('utf-8'))
        try:
            with self.opener(request, timeout=60) as response:
                raw = _read_bounded(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise VoiceError('MiniMax 鉴权失败，请检查 API Key 和账号区域。') from exc
            if exc.code == 429:
                raise VoiceError('MiniMax 额度不足或请求过多。') from exc
            raise VoiceError(f'MiniMax 请求失败（HTTP {exc.code}）。') from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise VoiceError('无法连接 MiniMax，请检查网络和账号区域。', outcome_unknown=True) from exc
        try:
            body = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise VoiceError('MiniMax 返回了无法读取的音频。') from exc
        code = (body.get('base_resp') or {}).get('status_code') if isinstance(body, dict) else None
        if type(code) is int and code != 0:
            if code == 1004:
                raise VoiceError('MiniMax 鉴权失败，请检查 API Key 和账号区域。')
            if code in (1002, 1039):
                raise VoiceError('MiniMax 额度不足或请求过多。')
            raise VoiceError(f'MiniMax 生成失败（错误码 {code}）。')
        value = (body.get('data') or {}).get('audio') if isinstance(body, dict) else None
        if (not isinstance(value, str) or not value or len(value) % 2
                or len(value) > MAX_AUDIO_BYTES * 2 or re.fullmatch(r'[0-9a-fA-F]+', value) is None):
            raise VoiceError('MiniMax 返回了无效音频。')
        audio = bytes.fromhex(value)
        if not audio or not (audio.startswith(b'ID3') or (len(audio) >= 2 and audio[0] == 0xff
                                                           and audio[1] & 0xe0 == 0xe0)):
            raise VoiceError('MiniMax 返回了无效 MP3。')
        return audio

    def synthesize(self, text):
        chars = billed_characters(text)
        amount = Decimal(chars) * self.config.cny_per_10k_chars / Decimal(10_000)
        reservation = self.budget.reserve_amount(
            self.character, self.config.model, amount, 'minimax-voice', billed_characters=chars)
        try:
            audio = self._request(text)
        except VoiceError as exc:
            self.budget.settle_estimate(
                reservation, 'minimax-voice-failed',
                settled_amount=reservation[1] if exc.outcome_unknown else Decimal(0))
            raise
        self.budget.settle_estimate(reservation, 'minimax-voice', settled_amount=amount)
        media_id = uuid.uuid4().hex
        filename = media_id + '.mp3'
        self.media_root.mkdir(parents=True, exist_ok=True)
        target = self.media_root / filename
        temp = target.with_suffix('.tmp')
        try:
            try:
                with temp.open('wb', buffering=0) as handle:
                    if handle.write(audio) != len(audio):
                        raise OSError('short audio write')
                    handle.flush()
                    os.fsync(handle.fileno())
                temp.replace(target)
            except OSError as exc:
                raise VoiceError('语音已经生成，但无法保存到本机。') from exc
        finally:
            temp.unlink(missing_ok=True)
        return {'id': media_id, 'filename': filename, 'path': f'media/{filename}',
                'mime': 'audio/mpeg', 'name': 'voice message'}

    def discard(self, media):
        try:
            (self.media_root / media['filename']).unlink(missing_ok=True)
        except (OSError, KeyError, TypeError):
            pass
