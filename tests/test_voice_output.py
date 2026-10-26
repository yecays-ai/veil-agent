import json
import tempfile
import unittest
import urllib.error
import http.client
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from veil_agent import Budget, Character, Chat, Store, make_voice, self_facts
from voice_output import (MiniMaxVoice, VoiceConfig, VoiceError, billed_characters,
                          load_voice_config)


class FakeResponse:
    def __init__(self, body):
        self.body = body
        self.offset = 0
        self.headers = {'Content-Length': str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class IncompleteResponse(FakeResponse):
    def read(self, _size=-1):
        raise http.client.IncompleteRead(b'partial')


class VoiceOutputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store)
        provider = {'model': 'fake', 'max_input_tokens': 6000,
                    'input_cny_per_million': Decimal('1'),
                    'cached_input_cny_per_million': Decimal('0.5'),
                    'output_cny_per_million': Decimal('2')}
        self.cfg = Character('peter', 'Peter', 'fictional persona', 20, 50, provider, 'fake',
                             tools={'enabled': True, 'max_steps': 2, 'max_output_tokens': 50})
        self.voice_cfg = VoiceConfig('secret', 'peter-voice', 'speech-2.8-hd', 'cn', Decimal('3.5'))

    def test_incomplete_config_hides_voice_and_complete_config_uses_default_price(self):
        (self.root / '.env').write_text('MINIMAX_API_KEY=secret\nMINIMAX_VOICE_ID=id\n', encoding='utf-8')
        self.assertIsNone(load_voice_config(self.root))
        (self.root / '.env').write_text(
            'MINIMAX_API_KEY=secret\nMINIMAX_VOICE_ID=id\nMINIMAX_MODEL=speech-2.8-hd\n'
            'MINIMAX_REGION=global\n', encoding='utf-8')
        config = load_voice_config(self.root)
        self.assertEqual((config.region, config.cny_per_10k_chars), ('global', Decimal('3.5')))
        self.assertIsNone(make_voice(self.root, self.cfg, self.store, self.budget))
        self.cfg.voice = {'provider': 'minimax'}
        self.assertIsInstance(make_voice(self.root, self.cfg, self.store, self.budget), MiniMaxVoice)

    def test_billing_counts_cjk_as_two_and_saves_relative_mp3_path(self):
        audio = b'ID3fictional-mp3'
        payload = json.dumps({'base_resp': {'status_code': 0},
                              'data': {'audio': audio.hex()}}).encode()
        captured = {}

        def opener(request, timeout):
            captured['url'] = request.full_url
            captured['headers'] = dict(request.header_items())
            captured['body'] = json.loads(request.data)
            captured['timeout'] = timeout
            return FakeResponse(payload)

        voice = MiniMaxVoice(self.voice_cfg, self.store, self.budget, 'peter', opener=opener)
        media = voice.synthesize('你a')
        self.assertEqual(billed_characters('你a'), 3)
        self.assertEqual(captured['url'], 'https://api.minimaxi.com/v1/t2a_v2')
        self.assertEqual(captured['body']['voice_setting']['voice_id'], 'peter-voice')
        self.assertEqual(captured['body']['audio_setting']['format'], 'mp3')
        self.assertEqual(media['path'], f"media/{media['filename']}")
        self.assertEqual((self.store.root / 'peter' / media['path']).read_bytes(), audio)

    def test_supplementary_cjk_is_billed_as_two_characters(self):
        self.assertEqual(billed_characters('\U00020000A'), 3)

    def test_incomplete_response_becomes_unknown_outcome_voice_error(self):
        voice = MiniMaxVoice(
            self.voice_cfg, self.store, self.budget, 'peter',
            opener=lambda *_args, **_kwargs: IncompleteResponse(b''))
        with self.assertRaises(VoiceError) as caught:
            voice.synthesize('hello')
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(self.budget.totals()[0], Decimal('0.00175'))

    def test_voice_schema_identity_and_history_only_change_when_configured(self):
        disabled = Chat(self.cfg, self.store, self.budget, Mock())
        disabled_schema = next(s for s in disabled.tool_schemas()
                               if s['function']['name'] == 'send_message')
        self.assertNotIn('voice', disabled_schema['function']['parameters']['properties'])
        self.assertIn('You have no voice.', self_facts(self.cfg, self.budget)[0])

        voice = Mock()
        enabled = Chat(self.cfg, self.store, self.budget, Mock(), voice=voice)
        schema = next(s for s in enabled.tool_schemas() if s['function']['name'] == 'send_message')
        self.assertEqual(schema['function']['parameters']['properties']['voice']['description'],
                         'true sends this message as a voice message in your own voice; the text is also kept.')
        capabilities = SimpleNamespace(description='Messaging is available.')
        self.assertIn('send_message voice: true', self_facts(
            self.cfg, self.budget, capabilities=capabilities, voice=True)[0])
        self.assertIn('You have no voice.', self_facts(self.cfg, self.budget, voice=True)[0])

        media = {'id': 'a' * 32, 'filename': 'a' * 32 + '.mp3',
                 'path': 'media/' + 'a' * 32 + '.mp3', 'mime': 'audio/mpeg', 'name': 'voice message'}
        self.store.append('peter/chat.jsonl', {'role': 'assistant', 'content': 'hello', 'media': media})
        reopened = Chat(self.cfg, self.store, self.budget, Mock(), voice=voice)
        self.assertEqual(reopened.history[-1]['content'], '[voice message] hello')
        self.assertEqual(reopened.history_events[-1]['content'], 'hello')

    def test_wrong_key_is_not_retried_and_releases_the_voice_reservation(self):
        opener = Mock(side_effect=urllib.error.HTTPError(
            'https://api.minimaxi.com/v1/t2a_v2', 401, 'unauthorized', {}, None))
        voice = MiniMaxVoice(self.voice_cfg, self.store, self.budget, 'peter', opener=opener)
        with self.assertRaisesRegex(VoiceError, '鉴权失败'):
            voice.synthesize('hello')
        opener.assert_called_once()
        self.assertEqual(self.budget.totals(), (Decimal(0), Decimal(0), Decimal(0)))
        rows = self.store.read('costs.jsonl')
        self.assertEqual([row['accounting'] for row in rows],
                         ['minimax-voice', 'minimax-voice-failed'])

    def test_success_persists_original_and_failure_sends_marked_text_without_retry(self):
        media = {'id': 'b' * 32, 'filename': 'b' * 32 + '.mp3',
                 'path': 'media/' + 'b' * 32 + '.mp3', 'mime': 'audio/mpeg', 'name': 'voice message'}
        voice = Mock()
        voice.synthesize.return_value = media
        output = []
        chat = Chat(self.cfg, self.store, self.budget, Mock(), output=output.append, voice=voice)
        result = chat.send_message('{"text":"hello","voice":true}', 'request-1', 'call-1')
        self.assertEqual(result[0], 'Sent as a voice message.')
        saved = self.store.read('peter/chat.jsonl')[-1]
        self.assertEqual((saved['content'], saved['media']['path']), ('hello', media['path']))
        self.assertEqual(getattr(output[-1], 'media'), media)

        voice.synthesize.side_effect = VoiceError('MiniMax 鉴权失败，请检查 API Key 和账号区域。')
        failed = chat.send_message('{"text":"still here","voice":true}', 'request-2', 'call-2')
        self.assertIn('sent as text instead', failed[0])
        fallback = self.store.read('peter/chat.jsonl')[-1]
        self.assertIn('语音生成失败：MiniMax 鉴权失败', fallback['content'])
        self.assertNotIn('media', fallback)
        self.assertEqual(voice.synthesize.call_count, 2)

    def test_failed_voice_tool_turn_creates_only_the_marked_text_bubble(self):
        call = SimpleNamespace(id='voice-call', function=SimpleNamespace(
            name='send_message', arguments='{"text":"still here","voice":true}'))
        message = SimpleNamespace(
            content=None, tool_calls=[call], reasoning_content=None,
            model_dump=lambda exclude_none=True: {'role': 'assistant', 'tool_calls': [{
                'id': call.id, 'type': 'function', 'function': {
                    'name': call.function.name, 'arguments': call.function.arguments}}]})
        model = Mock(side_effect=[(message, 100, 20, 0),
                                  (SimpleNamespace(content='', tool_calls=[]), 120, 5, 0)])
        voice = Mock()
        voice.synthesize.side_effect = VoiceError('MiniMax 鉴权失败，请检查 API Key 和账号区域。')
        output = []
        chat = Chat(self.cfg, self.store, self.budget, model, output=output.append, voice=voice)
        reply = chat.send('send me a voice message')
        self.assertIn('语音生成失败：MiniMax 鉴权失败', reply)
        rows = self.store.read('peter/chat.jsonl')
        self.assertEqual([row['role'] for row in rows], ['user', 'assistant'])
        self.assertEqual(sum(str(item).startswith('Peter: ') for item in output), 1)
        voice.synthesize.assert_called_once_with('still here')


if __name__ == '__main__':
    unittest.main()
