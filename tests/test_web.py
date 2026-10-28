import json
import base64
import hashlib
import socket
import ssl
import sys
import tempfile
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock, patch

from veil_agent import (ActivityState, Budget, Chat, Store, UserError, collapse_latest_photo,
                        estimate_request_tokens, load_character, run_chat)
from web_agent import Hub, clean_voice_transcript, serve_chat, start_server, voice_transport_category
from tools_agent import FilesystemTools

TOKEN = 'test-token'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class WebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'characters' / 'test').mkdir(parents=True)
        (root / 'persona').mkdir()
        (root / 'persona' / 'test.md').write_text('Fictional test persona', encoding='utf-8')
        (root / 'characters' / 'test' / 'character.toml').write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
''', encoding='utf-8')
        self.cfg = load_character(root, 'test')
        self.store = Store(root / 'data')
        self.model = Mock(return_value=('A fictional reply', 100, 20, 10))
        self.hub = Hub('Test', ActivityState(self.store.root / 'test' / 'resident-state.json'),
                       media_root=self.store.root / 'test' / 'media')
        self.request_number = 0
        self.port = free_port()
        self.server = start_server(self.hub, TOKEN, self.port)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None, token=TOKEN, host=None, origin=None):
        if path == '/api/send' and body is not None:
            self.request_number += 1
            body.setdefault('request_id', f'test-request-{self.request_number}')
        headers = {'X-Token': token, 'Content-Type': 'application/json'}
        if host:
            headers['Host'] = host
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def test_lab_attachment_survives_restart_and_is_served_as_safe_content(self):
        raw = b'<html><script>window.bad=true</script><body>game</body></html>'
        attachment = self.hub.save_attachment({'name': 'game.html', 'suffix': '.html',
            'mime': 'text/html', 'view': 'html', 'size': len(raw), 'data': raw})
        self.assertTrue(attachment)
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'reopen-state.json'),
                       media_root=self.store.root / 'test' / 'media')
        self.assertEqual(reopened.read_attachment(attachment['id']), (attachment, raw))
        self.assertEqual(self.call(f"/api/attachment?id={attachment['id']}")[1]['attachment'], attachment)
        self.assertEqual(self.call(f"/api/attachment-text?id={attachment['id']}")[0], 415)

        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/media/{attachment["id"]}?t={TOKEN}')
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
            self.assertEqual(response.headers['Content-Type'], 'application/octet-stream')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.headers['Content-Disposition'],
                             f'attachment; filename="{attachment["filename"]}"')
        self.assertEqual(body, raw)

    def test_lab_text_attachment_is_served_as_plain_text(self):
        raw = b'<script>this is only text</script>'
        attachment = self.hub.save_attachment({'name': 'notes.txt', 'suffix': '.txt',
            'mime': 'text/plain', 'view': 'text', 'size': len(raw), 'data': raw})
        self.assertTrue(attachment)
        self.assertEqual(self.call(f"/api/attachment-text?id={attachment['id']}")[1]['text'],
                         raw.decode('utf-8'))
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/media/{attachment["id"]}?t={TOKEN}')
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.headers['Content-Type'], 'text/plain; charset=utf-8')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_chat_history_replays_the_same_attachment_after_restart(self):
        raw = b'The first poem version.'
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model,
                    output=self.hub.output)
        chat.attachment_reader = lambda path: {'name': 'poem.txt', 'suffix': '.txt',
            'mime': 'text/plain', 'view': 'text', 'size': len(raw), 'data': raw}
        chat.attachment_saver = self.hub.save_attachment
        chat.attachment_discarder = self.hub._remove_unreferenced_media
        result, state, _ = chat.send_message(json.dumps({'text': 'A poem for you.',
                                                         'file': 'lab/poem.txt'}),
                                             'attachment-restart')
        self.assertEqual((result, state), ('Sent.', '成功'))
        original = self.store.read('test/chat.jsonl')[0]['attachments'][0]
        self.assertEqual((self.hub.media_root / original['filename']).read_bytes(), raw)

        reopened_hub = Hub('Test', ActivityState(self.store.root / 'test' / 'reopen-state.json'),
                           media_root=self.store.root / 'test' / 'media')
        reopened_chat = Chat(self.cfg, self.store, Budget(self.store), self.model)
        with patch('web_agent.run_chat'):
            serve_chat(reopened_chat, reopened_hub)
        replayed = next(event for event in reopened_hub.events if event['kind'] == 'assistant')

        self.assertEqual(replayed['attachments'], [original])
        self.assertEqual(reopened_hub.read_attachment(original['id']), (original, raw))

    def test_delete_and_recall_chat_actions_clean_attachment_snapshots(self):
        raw = b'Fictional attachment data.'
        deleted = self.hub.save_attachment({'name': 'deleted.txt', 'suffix': '.txt',
            'mime': 'text/plain', 'view': 'text', 'size': len(raw), 'data': raw})
        recalled = self.hub.save_attachment({'name': 'recalled.txt', 'suffix': '.txt',
            'mime': 'text/plain', 'view': 'text', 'size': len(raw), 'data': raw})
        stamp = datetime.now(timezone.utc).isoformat()
        self.store.append('test/chat.jsonl', {
            'role': 'assistant', 'content': 'Here is the deleted file.', 'time': stamp,
            'request_id': 'deleted-request', 'message_id': 'deleted-message',
            'attachments': [deleted],
        })
        self.store.append('test/chat.jsonl', {
            'role': 'user', 'content': 'Please retract this file.', 'time': stamp,
            'request_id': 'recalled-request', 'message_id': 'recalled-message',
            'attachments': [recalled],
        })
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model, output=self.hub.output)
        self.hub.busy = False
        email = SimpleNamespace(name='email', label='Email')
        with patch('web_agent.run_chat'), patch('away_bridge.AwayBridge.start'), \
                patch.object(self.hub, 'request_stop'):
            serve_chat(chat, self.hub, channels=(email,))
        email_bridge = self.hub.away[-1]
        email_bridge.in_turn = True
        email_bridge.collected_attachments = [{'id': str(index)} for index in range(3)]
        self.assertFalse(chat.email_attachment_admission())
        email_bridge.in_turn = False

        self.assertEqual(self.hub.message_action('delete', message_id='deleted-message'), '消息已删除。')
        self.assertNotIn(deleted['id'], self.hub.media_index)
        self.assertFalse((self.hub.media_root / deleted['filename']).exists())

        self.assertEqual(self.hub.message_action('recall', message_id='recalled-message'), '消息已撤回。')
        recalled_row = next(row for row in self.store.read('test/chat.jsonl')
                            if row.get('message_id') == 'recalled-message')
        self.assertEqual(recalled_row['attachments'], [])
        self.assertNotIn(recalled['id'], self.hub.media_index)
        self.assertFalse((self.hub.media_root / recalled['filename']).exists())

    def test_voice_transport_errors_keep_their_real_category(self):
        cases = (
            (urllib.error.URLError(socket.gaierror(11001, 'getaddrinfo failed')), 'dns', 'gaierror'),
            (urllib.error.URLError(ssl.SSLCertVerificationError(1, 'certificate verify failed')),
             'tls_certificate', 'SSLCertVerificationError'),
            (urllib.error.URLError(ssl.SSLError('TLS handshake failed')), 'tls', 'SSLError'),
            (urllib.error.URLError(OSError('proxy tunnel failed')), 'proxy', 'OSError'),
            (urllib.error.URLError(TimeoutError('timed out')), 'timeout', 'TimeoutError'),
            (urllib.error.URLError(ConnectionRefusedError(10061, 'refused')),
             'connection_refused', 'ConnectionRefusedError'),
            (urllib.error.URLError(OSError('socket reset')), 'socket', 'OSError'),
        )
        for exc, expected_category, expected_type in cases:
            with self.subTest(expected_category=expected_category):
                category, reason = voice_transport_category(exc)
                self.assertEqual(category, expected_category)
                self.assertEqual(type(reason).__name__, expected_type)
        with patch('web_agent.getproxies', return_value={'https': 'http://fictional-proxy.invalid:8080'}):
            category, reason = voice_transport_category(urllib.error.URLError(socket.gaierror(11001, 'lookup failed')))
        self.assertEqual(category, 'proxy')
        self.assertEqual(type(reason).__name__, 'gaierror')

    def test_dictation_hides_sensevoice_language_and_emotion_markers(self):
        self.assertEqual(clean_voice_transcript('<|zh|>你好😊<|NEUTRAL|>'), '你好')
        self.assertEqual(clean_voice_transcript('<|en|>hello there😮'), 'hello there')
        self.assertEqual(clean_voice_transcript('<|zh|>普通语音😞'), '普通语音')
        self.assertEqual(clean_voice_transcript('😀 你好😞'), '你好')
        self.assertEqual(clean_voice_transcript('Wait 😊 until the end 🐱'),
                         'Wait 😊 until the end 🐱')

    def test_sensevoice_dictation_response_does_not_append_emotion_emoji(self):
        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return '{"text":"Recognized words😞"}'.encode()
        with patch('dotenv.dotenv_values', return_value={'EMBED_API_KEY': 'fictional'}), patch(
                'web_agent.urlopen', return_value=Response()):
            transcript, error = self.hub.transcribe_voice_bytes(b'fictional audio', 'audio/wav')
        self.assertEqual((transcript, error), ('Recognized words', None))

    def test_dictation_endpoint_uses_sensevoice_without_persisting_a_voice_message(self):
        with patch.object(self.hub, 'transcribe_voice_bytes', return_value=('这是一条测试转写', None)) as transcribe:
            status, result = self.call('/api/transcribe', {
                'mime': 'audio/wav', 'name': 'dictation.wav',
                'data': base64.b64encode(b'fictional audio').decode()})
        self.assertEqual(status, 200)
        self.assertEqual(result['transcript'], '这是一条测试转写')
        self.assertEqual(result['transcription_error'], '')
        self.assertNotIn('media', result)
        transcribe.assert_called_once_with(b'fictional audio', 'audio/wav', 'dictation.wav')
        self.assertEqual(self.hub.media_index, {})

    def test_dictation_endpoint_rejects_unsupported_audio_before_transcription(self):
        with patch.object(self.hub, 'transcribe_voice_bytes') as transcribe:
            status, _ = self.call('/api/transcribe', {
                'mime': 'image/png', 'data': base64.b64encode(b'not audio').decode()})
        self.assertEqual(status, 400)
        transcribe.assert_not_called()

    def test_new_conversation_emits_reset_event(self):
        self.hub.output('新对话已开始；旧聊天保留在本机，长期记忆与实验室未改动。')
        event = self.hub.snapshot(0)['events'][-1]
        self.assertEqual(event['kind'], 'reset')
        self.assertIn('旧聊天保留', event['text'])

    def test_ombre_memory_catalog_and_read_endpoints_are_authenticated_and_read_only(self):
        calls = []

        class FixtureMemoryTools:
            def invoke(self, name, args):
                calls.append((name, args))
                if name == 'memory_catalog':
                    return json.dumps({'items': [{'memory_id': 'fixture-id', 'title': 'Fictional memory'}],
                                       'next_after': ''}), '成功', '记忆目录'
                if name == 'memory_read':
                    return json.dumps({'memory_id': args['memory_id'], 'offset': args['offset'],
                                       'text': 'Fictional exact memory text', 'next_offset': None,
                                       'version': 'a' * 64}), '成功', 'Fictional memory'
                raise AssertionError('unexpected memory tool')

        self.hub.memory_tools = FixtureMemoryTools()
        self.assertEqual(self.call('/api/memory/catalog', token='wrong')[0], 403)
        status, catalog = self.call('/api/memory/catalog?after=fixture-start')
        self.assertEqual(status, 200)
        self.assertEqual(catalog['items'][0]['memory_id'], 'fixture-id')
        status, memory = self.call('/api/memory/read?id=fixture-id&offset=0&version=' + ('a' * 64))
        self.assertEqual(status, 200)
        self.assertEqual(memory['text'], 'Fictional exact memory text')
        self.assertEqual(calls, [('memory_catalog', {'after': 'fixture-start'}),
                                 ('memory_read', {'memory_id': 'fixture-id', 'offset': 0,
                                                  'version': 'a' * 64})])

    def test_memory_page_list_edit_and_delete_endpoints(self):
        calls = []

        class FixtureMemoryTools:
            def panel_entries(self):
                return {'items': [{'memory_id': 'fixture-id', 'title': 'Fictional memory'}], 'recent_requests': 0}

            def panel_edit(self, memory_id, changes):
                calls.append(('edit', memory_id, changes))
                return True, 'saved'

            def panel_delete(self, memory_id):
                calls.append(('delete', memory_id))
                return False, 'not confirmed'

        self.hub.memory_tools = FixtureMemoryTools()
        self.assertEqual(self.call('/api/memory/panel', token='wrong')[0], 403)
        status, listing = self.call('/api/memory/panel')
        self.assertEqual((status, listing['items'][0]['memory_id']), (200, 'fixture-id'))
        self.assertEqual(self.call('/api/memory/edit', {'id': 'fixture-id', 'changes': {'title': 'New'}}, token='wrong')[0], 403)
        self.assertEqual(self.call('/api/memory/edit', {'id': 'fixture-id', 'changes': {'title': 'New'}}), (200, {'ok': True, 'message': 'saved'}))
        self.assertEqual(self.call('/api/memory/delete', {'id': 'fixture-id'})[0], 409)
        self.assertEqual(self.call('/api/memory/delete', {'nope': 1})[0], 400)
        self.assertEqual(calls, [('edit', 'fixture-id', {'title': 'New'}), ('delete', 'fixture-id')])

    def test_image_upload_is_token_bound_persisted_and_shown_as_one_chat_event(self):
        png = b'\x89PNG\r\n\x1a\nfictional'
        body = {'mime': 'image/png', 'name': 'fictional.png',
                'data': base64.b64encode(png).decode()}
        self.assertEqual(self.call('/api/media', body, token='wrong')[0], 403)

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.hub.ready and not self.hub.busy))
            status, uploaded = self.call('/api/media', body)
            self.assertEqual(status, 201)
            media = uploaded['media']
            result['media'] = media
            self.assertTrue((self.store.root / 'test' / 'media' / '.uploads' /
                             media['filename']).exists())
            status, _ = self.call('/api/send', {'text': '[图片]', 'media': {'id': media['id']}})
            self.assertEqual(status, 200)
            self.assertTrue(self.wait(lambda: any(event['kind'] == 'assistant'
                                                 for event in self.hub.snapshot(0)['events'])))

        result = self.run_loop(scenario)
        media = result['media']
        self.assertEqual((self.store.root / 'test' / 'media' / media['filename']).read_bytes(), png)
        event = self.hub.snapshot(0)['events'][-1]
        events = self.hub.snapshot(0)['events']
        user_event = next(row for row in events if row['kind'] == 'user')
        self.assertLess(events.index(user_event), next(i for i, row in enumerate(events)
                                                      if row['kind'] == 'assistant'))
        self.assertEqual(user_event['media']['id'], media['id'])
        url = f'/media/{media["filename"]}?t={TOKEN}'
        for bad_url in (f'/media/{media["filename"]}', f'/media/{media["filename"]}?t=wrong'):
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(f'http://127.0.0.1:{self.port}{bad_url}', timeout=5)
            self.assertEqual(denied.exception.code, 403)
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{url}')
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.read(), png)
        saved = next(row for row in reversed(self.store.read('test/chat.jsonl')) if row['role'] == 'user')
        self.assertEqual(saved['media']['id'], media['id'])
        self.assertEqual(saved['content'], '[图片]')
        self.assertNotIn('base64', json.dumps(saved))
        sent_user = next(item for item in self.model.call_args.args[0] if item['role'] == 'user')
        self.assertIsInstance(sent_user['content'], list)
        self.assertEqual(sent_user['content'][1], {
            'type': 'image_url', 'image_url': {
                'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}})
        reopened_chat = Chat(self.cfg, self.store, Budget(self.store), self.model)
        self.assertEqual(next(row for row in reversed(reopened_chat.history_events)
                             if row['role'] == 'user')['media']['id'], media['id'])
        reopened_user = next(row for row in reversed(reopened_chat.history) if row['role'] == 'user')
        self.assertIsInstance(reopened_user['content'], list)
        self.assertEqual(reopened_user['content'][1], sent_user['content'][1])
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'other-state.json'),
                       media_root=self.store.root / 'test' / 'media')
        self.assertEqual(reopened.media_for_request('test-request-1')['id'], media['id'])
        self.assertEqual(reopened.submit('[图片]', 'test-request-1', media['id']), 'duplicate')

        # Losing an old photo must degrade that turn to a placeholder instead
        # of making the entire conversation impossible to reopen.
        (self.store.root / 'test' / 'media' / media['filename']).unlink()
        degraded = Chat(self.cfg, self.store, Budget(self.store), self.model)
        degraded_user = next(row for row in reversed(degraded.history) if row['role'] == 'user')
        self.assertEqual(degraded_user['content'], '[Lan sent a photo]')

    def test_expired_pending_upload_is_reclaimed_before_capacity_check(self):
        media_root = self.store.root / 'test' / 'expiring-media'
        hub = Hub('Test', ActivityState(self.store.root / 'test' / 'expiring-state.json'),
                  media_root=media_root)
        png = b'\x89PNG\r\n\x1a\nfictional'
        with patch('web_agent.MAX_PENDING_MEDIA', 1), patch('web_agent.time.monotonic') as clock:
            clock.return_value = 10
            first = hub.save_media('image/png', png, 'first.png')
            self.assertIsNotNone(first)
            clock.return_value = 10 + 60 * 60 + 1
            second = hub.save_media('image/png', png, 'second.png')
        self.assertIsNotNone(second)
        self.assertIsNone(hub.pending_media_info(first['id']))
        self.assertFalse((media_root / '.uploads' / first['filename']).exists())

    def test_image_request_rejects_bad_metadata_and_commands(self):
        self.hub.busy = False
        png = base64.b64encode(b'\x89PNG\r\n\x1a\nfictional').decode()
        status, _ = self.call('/api/media', {'mime': 'image/png', 'name': 42, 'data': png})
        self.assertEqual(status, 400)
        status, _ = self.call('/api/send', {'text': 'hello', 'media': {'id': 'not-a-media-id'}})
        self.assertEqual(status, 400)
        self.assertFalse(self.hub.busy)
        status, _ = self.call('/api/send', {'text': 'hello', 'media': {'id': ''}})
        self.assertEqual(status, 400)
        self.assertFalse(self.hub.busy)
        status, _ = self.call('/api/send', {'text': 'hello', 'media': {}})
        self.assertEqual(status, 400)
        self.assertFalse(self.hub.busy)
        status, result = self.call('/api/media', {'mime': 'image/png', 'name': 'fictional.png', 'data': png})
        self.assertEqual(status, 201)
        self.hub.busy = False
        status, _ = self.call('/api/send', {'text': '/quit', 'media': {'id': result['media']['id']}})
        self.assertEqual(status, 400)
        self.assertFalse(self.hub.busy)

    def test_busy_send_commits_and_queues_a_pending_upload(self):
        png = base64.b64encode(b'\x89PNG\r\n\x1a\nfictional').decode()
        status, result = self.call('/api/media', {
            'mime': 'image/png', 'name': 'fictional.png', 'data': png})
        self.assertEqual(status, 201)
        media = result['media']
        self.hub.busy = True
        status, _ = self.call('/api/send', {'text': 'hello', 'media': {'id': media['id']}})
        self.assertEqual(status, 200)
        self.assertIsNotNone(self.hub.media_info(media['id']))
        self.assertIsNone(self.hub.pending_media_info(media['id']))

    def test_torn_final_media_index_row_keeps_completed_history(self):
        media_root = self.store.root / 'test' / 'torn-media'
        media_root.mkdir(parents=True)
        first = {'id': 'a' * 32, 'filename': ('a' * 32) + '.png', 'mime': 'image/png',
                 'name': 'first.png', 'request_id': 'first'}
        (media_root / 'index.jsonl').write_bytes(
            (json.dumps(first) + '\n' + '{"id":"torn').encode())
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'torn-state.json'),
                       media_root=media_root)
        self.assertEqual(reopened.media_info(first['id'])['request_id'], 'first')
        self.assertEqual((media_root / 'index.jsonl').read_text(), json.dumps(first) + '\n')

    def test_restart_removes_unindexed_final_media_from_interrupted_commit(self):
        media_root = self.store.root / 'test' / 'orphan-media'
        media_root.mkdir(parents=True)
        orphan = media_root / (('b' * 32) + '.jpg')
        orphan.write_bytes(b'orphan')
        Hub('Test', ActivityState(self.store.root / 'test' / 'orphan-state.json'),
            media_root=media_root)
        self.assertFalse(orphan.exists())

    def test_restart_recovers_unindexed_voice_recording_for_retry(self):
        media_root = self.store.root / 'test' / 'orphan-voice'
        media_root.mkdir(parents=True)
        media_id = 'c' * 32
        orphan = media_root / (media_id + '.webm')
        orphan.write_bytes(b'\x1a\x45\xdf\xa3fictional audio')
        (media_root / ('.' + media_id + '.voice-pending')).write_text(
            json.dumps({'id': media_id, 'mime': 'audio/webm', 'name': 'voice'}))
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'voice-recovery-state.json'),
                       media_root=media_root)
        recovered = reopened.media_info(media_id)
        self.assertEqual(recovered['request_id'], 'voice-' + media_id)
        self.assertIn('已恢复', recovered['transcription_error'])
        self.assertTrue((media_root / 'index.jsonl').exists())
        self.assertFalse((media_root / ('.' + media_id + '.voice-pending')).exists())

    def test_restart_does_not_adopt_unmarked_audio_in_character_media(self):
        media_root = self.store.root / 'test' / 'unmarked-voice'
        media_root.mkdir(parents=True)
        media_id = 'd' * 32
        audio = media_root / (media_id + '.webm')
        audio.write_bytes(b'\x1a\x45\xdf\xa3unrelated audio')
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'unmarked-state.json'),
                       media_root=media_root)
        self.assertIsNone(reopened.media_info(media_id))
        self.assertTrue(audio.exists())

    def test_voice_bytes_are_fsynced_before_index_commit(self):
        with patch('os.fsync') as sync, patch.object(
                self.hub, '_rewrite_media_index', side_effect=lambda index=None: self.assertTrue(sync.called)):
            media = self.hub.save_voice('audio/webm', b'\x1a\x45\xdf\xa3fictional audio')
        self.assertIsNotNone(media)

    def test_committed_voice_stays_recoverable_until_chat_event_is_durable(self):
        media = self.hub.save_voice('audio/webm', b'\x1a\x45\xdf\xa3fictional audio')
        request_id = 'request-voice-fixture'
        committed = self.hub.commit_media(media['id'], request_id, hashlib.sha256(b'hello').digest())
        self.assertTrue(committed['delivery_pending'])
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'voice-pending-state.json'),
                       media_root=self.store.root / 'test' / 'media')
        recovered = reopened.media_info(media['id'])
        self.assertTrue(recovered['delivery_pending'])
        reopened.finish_media_submission(True, 'hello', recovered, request_id)
        self.assertNotIn('delivery_pending', reopened.media_info(media['id']))

    def test_restart_does_not_replay_a_voice_send_outside_the_recent_history_window(self):
        media = self.hub.save_voice('audio/webm', b'\x1a\x45\xdf\xa3fictional audio')
        media_id = media['id']
        self.hub.media_index[media_id]['request_id'] = 'voice-send-' + media_id
        self.hub._rewrite_media_index()
        voice_event_media = {key: media[key] for key in ('id', 'filename', 'mime', 'name', 'kind', 'transcript')}
        self.store.append('test/chat.jsonl', {
            'role': 'user', 'content': '[voice message] fictional transcript', 'time': '2026-10-23T10:00:00+08:00',
            'request_id': 'voice-send-' + media_id, 'media': voice_event_media})
        self.store.append('test/chat.jsonl', {
            'role': 'assistant', 'content': 'Fictional reply', 'time': '2026-10-23T10:00:02+08:00'})
        for index in range(25):
            stamp = f'2026-10-23T10:{index + 1:02d}:00+08:00'
            self.store.append('test/chat.jsonl', {'role': 'user', 'content': f'fictional turn {index}', 'time': stamp})
            self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': f'fictional answer {index}', 'time': stamp})

        chat = Chat(self.cfg, self.store, Budget(self.store), self.model)
        self.assertNotIn(media_id, {row.get('media', {}).get('id') for row in chat.history_events
                                    if isinstance(row.get('media'), dict)})
        with patch('web_agent.run_chat', return_value=None):
            serve_chat(chat, self.hub)

        self.assertFalse(any(event.get('media', {}).get('id') == media_id for event in self.hub.events
                             if isinstance(event.get('media'), dict)))

    def test_snapshot_exposes_the_same_capability_record_used_by_peter(self):
        record = 'CAPABILITY RECORD\n2026-10-22T00:00:00+00:00: Capability update: added fictional_tool.'
        self.hub.capability_record = lambda: record
        self.assertEqual(self.hub.snapshot(0)['capability_record'], record)

    def test_voice_media_is_served_and_page_has_audio_with_expandable_text(self):
        media_id = 'c' * 32
        media = {'id': media_id, 'filename': media_id + '.mp3',
                 'path': 'media/' + media_id + '.mp3', 'mime': 'audio/mpeg',
                 'name': 'voice message'}
        audio = b'ID3fictional'
        (self.store.root / 'test' / 'media' / media['filename']).write_bytes(audio)
        registered = self.hub.register_voice_media(media)
        self.assertEqual(registered['id'], media['id'])
        self.assertEqual(registered['request_id'], 'assistant-voice-' + media['id'])
        self.hub._rewrite_media_index()
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'resident-state.json'),
                       media_root=self.store.root / 'test' / 'media')
        self.assertEqual(reopened.media_info(media_id)['request_id'], registered['request_id'])
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/media/{media["filename"]}?t={TOKEN}')
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual((response.headers['Content-Type'], response.read()),
                             ('audio/mpeg', audio))

    def test_photo_capability_notice_is_persistent_and_announced_once(self):
        manager = object.__new__(FilesystemTools)
        manager.cap_path = self.store.root / 'test' / 'capabilities.json'
        manager.capability_event = manager.capability_event_model = None
        manager.enabled = True
        manager.announce_photo_input()
        first = manager.capability_event
        self.assertIn('send you photos', first)
        manager.announce_photo_input()
        row = json.loads(manager.cap_path.read_text())
        matching = [item for item in row['capability_updates'] if 'send you photos' in item['text']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(row['pending_event'], first)

    def test_voice_capability_notice_is_persistent_and_announced_once(self):
        manager = object.__new__(FilesystemTools)
        manager.cap_path = self.store.root / 'test' / 'capabilities.json'
        manager.capability_event = manager.capability_event_model = None
        manager.enabled = True
        manager.announce_voice_output()
        first = manager.capability_event
        self.assertIn('send_message with voice: true', first)
        manager.announce_voice_output()
        row = json.loads(manager.cap_path.read_text())
        matching = [item for item in row['capability_updates']
                    if 'send_message with voice: true' in item['text']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(row['pending_event'], first)
        manager.suppress_voice_output_notice()
        row = json.loads(manager.cap_path.read_text())
        self.assertIsNone(row['pending_event'])
        self.assertFalse(row['voice_output_announced'])
        self.assertEqual(len(row['capability_updates']), 1)

    def test_photo_notice_save_failure_does_not_disable_chat_tools(self):
        manager = object.__new__(FilesystemTools)
        manager.cap_path = self.store.root / 'test' / 'capabilities.json'
        manager.capability_event = manager.capability_event_model = None
        manager.enabled = True
        with patch('tools_agent.save_capabilities', side_effect=OSError('fictional')):
            self.assertFalse(manager.announce_photo_input())
        self.assertTrue(manager.enabled)

    def test_voice_is_sent_before_background_transcription_and_reaches_model_as_voice_text(self):
        raw = b'\x1a\x45\xdf\xa3fictional webm audio'
        started, release = threading.Event(), threading.Event()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"text":"hello Peter"}'
        def transcribe(*_args, **_kwargs):
            started.set()
            if not release.wait(5):
                raise TimeoutError('fictional transcription gate timed out')
            return Response()

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.hub.ready and not self.hub.busy))
            body = {'mime': 'audio/webm', 'name': 'voice.webm',
                    'data': base64.b64encode(raw).decode(), 'send_first': True}
            with patch('dotenv.dotenv_values', return_value={'EMBED_API_KEY': 'fictional'}), patch('web_agent.urlopen', side_effect=transcribe):
                status, uploaded = self.call('/api/voice', body)
                self.assertEqual(status, 202)
                self.assertEqual(uploaded['media']['transcript'], '')
                self.assertNotIn('transcript', uploaded)
                self.assertTrue(started.wait(2), 'background transcription did not start')
                status, _ = self.call('/api/send', {'text': '[voice message]',
                                                    'request_id': 'voice-send-' + uploaded['media']['id'],
                                                    'media': {'id': uploaded['media']['id']}})
                self.assertEqual(status, 200)
                self.assertTrue(self.wait(lambda: self.hub.snapshot(0)['transcribing_voice']))
                release.set()
                self.assertTrue(self.wait(lambda: any(event['kind'] == 'assistant'
                                                     for event in self.hub.snapshot(0)['events'])))
            media = uploaded['media']; result['media'] = media
            self.assertTrue(self.wait(lambda: not self.hub.busy))

        result = self.run_loop(scenario)
        media = result['media']
        self.assertEqual((self.store.root / 'test' / 'media' / media['filename']).read_bytes(), raw)
        saved = next(row for row in reversed(self.store.read('test/chat.jsonl')) if row['role'] == 'user')
        self.assertEqual(saved['content'], '[voice message] hello Peter')
        self.assertEqual(saved['media']['kind'], 'voice')
        sent = next(item for item in self.model.call_args.args[0] if item['role'] == 'user')
        self.assertEqual(sent['content'], '[voice message] hello Peter')

    def test_voice_upload_keeps_the_existing_synchronous_web_response_by_default(self):
        raw = b'\x1a\x45\xdf\xa3fictional webm audio'
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"text":"web transcript"}'
        with patch('dotenv.dotenv_values', return_value={'EMBED_API_KEY': 'fictional'}), patch(
                'web_agent.urlopen', return_value=Response()):
            status, result = self.call('/api/voice', {
                'mime': 'audio/webm', 'name': 'voice.webm',
                'data': base64.b64encode(raw).decode()})
        self.assertEqual(status, 200)
        self.assertEqual(result['transcript'], 'web transcript')
        self.assertEqual(result['media']['transcript'], 'web transcript')

    def test_failed_voice_transcription_is_durable_and_retryable_without_sending(self):
        raw = b'\x1a\x45\xdf\xa3fictional webm audio'
        media = self.hub.save_voice('audio/webm', raw, 'voice.webm')
        with patch('dotenv.dotenv_values', return_value={'EMBED_API_KEY': 'fictional'}), patch(
                'web_agent.urlopen', side_effect=urllib.error.URLError(socket.gaierror(11001, 'getaddrinfo failed'))):
            transcript, error = self.hub.transcribe_voice(media['id'])
        self.assertIsNone(transcript)
        self.assertIn('DNS', error)
        self.assertIn('socket.gaierror', error)
        self.assertEqual(self.store.read('test/chat.jsonl'), [])
        reopened = Hub('Test', ActivityState(self.store.root / 'test' / 'reopened-state.json'),
                       media_root=self.store.root / 'test' / 'media')
        retained = reopened.media_info(media['id'])
        self.assertEqual(retained['transcription_error'], error)
        self.assertEqual((reopened.media_root / retained['filename']).read_bytes(), raw)

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"text":"hello after retry"}'

        with patch('dotenv.dotenv_values', return_value={'EMBED_API_KEY': 'fictional'}), patch(
                'web_agent.urlopen', return_value=Response()):
            status, result = self.call('/api/voice/retry', {'media_id': media['id']})
        self.assertEqual(status, 200)
        self.assertEqual(result['transcript'], 'hello after retry')
        self.assertEqual(result['media']['transcript'], 'hello after retry')
        self.assertEqual(result['media']['transcription_error'], '')

    def test_voice_retry_reuses_an_active_background_transcription(self):
        media = self.hub.save_voice('audio/webm', b'\x1a\x45\xdf\xa3fictional voice', 'voice.webm')
        started, release, retry_started = threading.Event(), threading.Event(), threading.Event()
        retry_result = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"text":"one transcription only"}'
        def transcribe(*_args, **_kwargs):
            started.set()
            if not release.wait(5):
                raise TimeoutError('fictional transcription gate timed out')
            return Response()
        with patch('dotenv.dotenv_values', return_value={'EMBED_API_KEY': 'fictional'}), patch(
                'web_agent.urlopen', side_effect=transcribe) as request:
            self.hub.start_voice_transcription(media['id'])
            self.assertTrue(started.wait(2), 'background transcription did not start')
            def retry():
                retry_started.set()
                retry_result.append(self.hub.retry_voice_transcription(media['id']))
            retry_thread = threading.Thread(target=retry)
            retry_thread.start()
            self.assertTrue(retry_started.wait(2))
            release.set()
            retry_thread.join(5)
            self.assertFalse(retry_thread.is_alive(), 'retry waited on a different transcription job')
            request.assert_called_once()
        self.assertEqual(retry_result, [('one transcription only', None)])

    def test_background_voice_transcription_has_a_bounded_worker_limit(self):
        medias = [self.hub.save_voice('audio/webm', b'\x1a\x45\xdf\xa3fictional voice', f'voice-{i}.webm')
                  for i in range(5)]
        all_started, release = threading.Event(), threading.Event()
        started_count, started_lock = [0], threading.Lock()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"text":"bounded worker result"}'
        def transcribe(*_args, **_kwargs):
            with started_lock:
                started_count[0] += 1
                if started_count[0] == 4:
                    all_started.set()
            if not release.wait(5):
                raise TimeoutError('fictional transcription gate timed out')
            return Response()
        with patch('dotenv.dotenv_values', return_value={'EMBED_API_KEY': 'fictional'}), patch(
                'web_agent.urlopen', side_effect=transcribe) as request:
            jobs = [self.hub.start_voice_transcription(media['id']) for media in medias[:4]]
            self.assertTrue(all_started.wait(5), 'four background workers did not start')
            rejected = self.hub.start_voice_transcription(medias[4]['id'])
            self.assertIsNone(rejected, 'excess work must be deferred for explicit retry')
            release.set()
            self.assertTrue(all(job.wait(5) for job in jobs), 'background worker did not finish')
            self.assertEqual(request.call_count, 4)
        self.assertIn('任务较多', self.hub.media_info(medias[4]['id'])['transcription_error'])

    def test_failed_chat_save_restores_voice_as_retryable_and_never_claims_delivery(self):
        raw = b'\x1a\x45\xdf\xa3fictional webm audio'
        media = self.hub.save_voice('audio/webm', raw, 'voice.webm')
        self.hub._record_voice_result(media['id'], 'hello Peter', '')
        digest = b'1' * 32
        committed = self.hub.commit_media(media['id'], 'fictional-request', digest)
        self.hub.finish_media_submission(False, '[voice message] hello Peter', committed,
                                         'fictional-request')
        retained = self.hub.media_info(media['id'])
        self.assertEqual(retained['request_id'], 'voice-' + media['id'])
        self.assertNotIn('submission_digest', retained)
        self.assertIn('没有交给 Peter', retained['delivery_error'])
        self.assertFalse(any(event['kind'] == 'user' for event in self.hub.snapshot(0)['events']))

    def test_busy_voice_send_is_queued_with_same_idempotency_key(self):
        media = self.hub.save_voice('audio/webm', b'\x1a\x45\xdf\xa3fictional audio', 'voice.webm')
        self.hub._record_voice_result(media['id'], 'hello Peter', '')
        self.hub.busy = True
        request_id = 'voice-send-' + media['id']
        status, _ = self.call('/api/send', {'text': '[voice message] hello Peter',
                                            'request_id': request_id, 'media': {'id': media['id']}})
        self.assertEqual(status, 200)
        retained = self.hub.media_info(media['id'])
        self.assertEqual(retained['transcript'], 'hello Peter')
        self.assertNotIn('delivery_error', retained)
        self.assertEqual(retained['request_id'], request_id)

        def scenario(_result):
            self.assertTrue(self.wait(lambda: self.hub.ready and not self.hub.busy))
            status, _ = self.call('/api/send', {'text': '[voice message] hello Peter',
                                                'request_id': request_id, 'media': {'id': media['id']}})
            self.assertEqual(status, 200)
            duplicate, _ = self.call('/api/send', {'text': '[voice message] hello Peter',
                                                   'request_id': request_id, 'media': {'id': media['id']}})
            self.assertEqual(duplicate, 200)
            self.assertTrue(self.wait(lambda: not self.hub.busy))

        self.run_loop(scenario)
        rows = [row for row in self.store.read('test/chat.jsonl') if row['role'] == 'user']
        self.assertEqual([row['content'] for row in rows].count('[voice message] hello Peter'), 1)
        self.assertNotIn('delivery_error', self.hub.media_info(media['id']))

    def test_voice_capability_notice_is_persistent_and_announced_once(self):
        manager = object.__new__(FilesystemTools)
        manager.cap_path = self.store.root / 'test' / 'capabilities.json'
        manager.capability_event = manager.capability_event_model = None
        manager.enabled = True
        manager.announce_voice_input()
        first = manager.capability_event
        self.assertIn('send you voice messages', first)
        manager.announce_voice_input()
        row = json.loads(manager.cap_path.read_text())
        matching = [item for item in row['capability_updates'] if 'send you voice messages' in item['text']]
        self.assertEqual(len(matching), 1)

    def test_voice_notice_flag_survives_capability_manifest_rebuild(self):
        manager = object.__new__(FilesystemTools)
        manager.cfg, manager.folder_name, manager.enabled = SimpleNamespace(memory=None), 'workspace', True
        manager.cap_path = self.store.root / 'test' / 'capabilities.json'
        manager.capability_event = manager.capability_event_model = None
        manager._record_capabilities(['read_text_file'])
        manager.announce_voice_input()
        manager._record_capabilities(['read_text_file', 'write_file'])
        row = json.loads(manager.cap_path.read_text())
        self.assertTrue(row['voice_input_announced'])

        reopened = object.__new__(FilesystemTools)
        reopened.cfg, reopened.folder_name, reopened.enabled = manager.cfg, 'workspace', True
        reopened.cap_path = manager.cap_path
        reopened.capability_event = reopened.capability_event_model = None
        reopened._record_capabilities(['read_text_file', 'write_file'])
        reopened.announce_voice_input()
        row = json.loads(reopened.cap_path.read_text())
        matching = [item for item in row['capability_updates'] if 'send you voice messages' in item['text']]
        self.assertEqual(len(matching), 1)

    def test_photo_is_budgeted_as_vision_tokens_and_collapsed_after_first_request(self):
        messages = [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'fictional caption'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + 'A' * 5_000_000}},
        ]}]
        estimated = estimate_request_tokens(messages)
        self.assertGreaterEqual(estimated, 4096)
        self.assertLess(estimated, 5000)
        self.assertGreaterEqual(estimate_request_tokens(messages, calibration=0.24), 4096)
        collapse_latest_photo(messages)
        self.assertEqual(messages[0]['content'], '[Lan sent a photo]\nfictional caption')

    def test_panel_shows_current_capabilities_without_adding_them_to_chat(self):
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model)
        def fake_loop(actual_chat, **_kwargs):
            actual_chat.send('fictional capability question')
        with patch('web_agent.run_chat', side_effect=fake_loop):
            serve_chat(chat, self.hub)
        model_messages = self.model.call_args.args[0]
        self.assertFalse(any(item['content'].startswith('CURRENT CAPABILITIES') for item in model_messages))
        self.assertEqual(self.hub.snapshot(0)['current_capabilities'], chat.current_capability_snapshot())

    def test_chat_append_failure_releases_photo_output_buffer_and_stops_service(self):
        hub = self.hub
        hub.busy = False
        media = hub.save_media('image/png', b'\x89PNG\r\n\x1a\nfictional')
        self.assertEqual(hub.submit('photo', 'failed-append', media['id']), 'accepted')
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model)
        append = self.store.append
        def fail_chat_row(path, row):
            if path == chat.path:
                hub.output('buffered diagnostic')
                raise UserError('fictional disk failure')
            return append(path, row)
        with patch.object(self.store, 'append', side_effect=fail_chat_row):
            with self.assertRaises(UserError):
                serve_chat(chat, hub)
        self.assertTrue(hub.stopping)
        self.assertFalse(hub.defer_outputs)
        self.assertEqual(hub.deferred_outputs, [])
        self.assertFalse(any(e['kind'] == 'user' for e in hub.events))
        self.assertTrue(any(e['text'] == 'buffered diagnostic' for e in hub.events))
        self.model.assert_not_called()

    def test_slow_media_commit_does_not_block_events_or_other_handlers(self):
        hub = self.hub
        hub.busy = False
        media = hub.save_media('image/png', b'\x89PNG\r\n\x1a\nfictional')
        entered, release = threading.Event(), threading.Event()
        commit = hub.commit_media
        results = []
        def slow_commit(*args):
            entered.set()
            if not release.wait(5):
                raise OSError('test timed out')
            return commit(*args)
        with patch.object(hub, 'commit_media', side_effect=slow_commit):
            worker = threading.Thread(target=lambda: results.append(
                hub.submit('photo', 'slow', media['id'])))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(hub.cond.acquire(timeout=1), 'storage held the event lock')
                try:
                    hub.push('notice', 'responsive')
                    self.assertEqual(hub.submit('other', 'other'), 'busy')
                    self.assertEqual(hub.submit('photo', 'slow', media['id']), 'busy')
                    self.assertTrue(hub.inbox.empty())
                finally:
                    hub.cond.release()
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, ['accepted'])
        self.assertFalse(hub.submission_pending)
        self.assertEqual(hub.submit('photo', 'slow', media['id']), 'duplicate')
        self.assertEqual(hub.inbox.qsize(), 1)

    def test_failed_media_commit_releases_admission_for_retry(self):
        hub = self.hub
        hub.busy = False
        media = hub.save_media('image/png', b'\x89PNG\r\n\x1a\nfictional')
        with patch.object(hub, 'commit_media', side_effect=OSError('fictional')):
            self.assertEqual(hub.submit('photo', 'retry', media['id']), 'media_storage')
        self.assertFalse(hub.submission_pending)
        self.assertFalse(hub.busy)
        self.assertNotIn('retry', hub.submissions)
        self.assertEqual(hub.submit('photo', 'retry', media['id']), 'accepted')

    def test_submission_deduplication_window_is_bounded(self):
        hub = Hub('Test', ActivityState(self.store.root / 'test' / 'other-state.json'))
        with patch('web_agent.MAX_SUBMISSIONS', 2):
            for text, request_id in (('one', 'one'), ('two', 'two')):
                hub.busy = False
                self.assertEqual(hub.submit(text, request_id), 'accepted')
            self.assertEqual(hub.submit('one', 'one'), 'duplicate')
            hub.busy = False
            self.assertEqual(hub.submit('three', 'three'), 'accepted')
            self.assertEqual(len(hub.submissions), 2)
            self.assertNotIn('one', hub.submissions)

    def wait(self, predicate, seconds=5):
        end = threading.Event()
        for _ in range(int(seconds * 50)):
            if predicate():
                return True
            end.wait(0.02)
        return False

    def run_loop(self, scenario):
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model,
                    approval=self.hub.approval, output=self.hub.output)
        result = {}

        def client():
            try:
                scenario(result)
            except BaseException as exc:  # reported in the main thread
                result['error'] = repr(exc)
            finally:
                self.hub.request_stop()

        thread = threading.Thread(target=client)
        thread.start()
        serve_chat(chat, self.hub)   # main thread: the chat loop uses signal handlers
        self.hub.stop()              # the loop and its chat writes have completed
        thread.join()
        self.assertNotIn('error', result, result.get('error'))
        return result

    def test_rejects_wrong_token_host_and_origin(self):
        self.assertEqual(self.call('/api/events', token='wrong')[0], 403)
        self.assertEqual(self.call('/api/events', host='evil.example')[0], 403)
        self.assertEqual(self.call('/api/events', origin='http://evil.example')[0], 403)
        self.assertEqual(self.call('/api/send', {'text': 'x'}, token='wrong')[0], 403)
        self.assertEqual(self.call('/api/exit', {}, token='wrong')[0], 403)
        self.assertFalse(self.hub.stopping)
        self.assertEqual(self.call('/api/events')[0], 200)
        self.model.assert_not_called()

    def test_ephemeral_port_uses_actual_port_for_host_validation(self):
        server = start_server(self.hub, TOKEN, 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/events',
                                     headers={'X-Token': TOKEN})
        with urllib.request.urlopen(req, timeout=5) as response:
            self.assertEqual(response.status, 200)

    def test_activity_toggle_is_authorized_persistent_and_task_free(self):
        status, state = self.call('/api/events')
        self.assertEqual(status, 200)
        self.assertFalse(state['activity_enabled'])
        self.assertNotIn('token', state)
        self.assertEqual(self.call('/api/activity', {'enabled': True}, token='wrong')[0], 403)
        self.assertFalse(self.hub.activity.enabled())
        self.assertEqual(self.call('/api/activity', {'enabled': True})[0], 200)
        reopened = ActivityState(self.store.root / 'test' / 'resident-state.json')
        self.assertTrue(reopened.enabled())
        self.assertEqual(self.call('/api/activity', {'enabled': False})[0], 200)
        self.assertFalse(reopened.enabled())

    def test_activity_availability_transitions_are_dated_in_capability_history(self):
        from tools_agent import FilesystemTools
        manager = FilesystemTools.__new__(FilesystemTools)
        manager.cap_path = self.store.root / 'test' / 'capabilities.json'
        toolset = SimpleNamespace(tools=[], files=manager, description='Fictional tools.',
            capability_event=None, capability_event_model=None, capability_record=None)
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model, tools=toolset)
        self.hub.activity_transition = lambda enabled: chat.set_activity_enabled(self.hub.activity, enabled)
        self.assertTrue(self.hub.set_activity(True))
        self.assertTrue(self.hub.set_activity(True))  # No event without a state transition.
        self.assertTrue(self.hub.set_activity(False))
        row = json.loads(manager.cap_path.read_text(encoding='utf-8'))
        updates = row['capability_updates']
        self.assertEqual(len(updates), 2)
        self.assertIn('was enabled', updates[0]['text'])
        self.assertIn('was paused', updates[1]['text'])
        self.assertTrue(all(datetime.fromisoformat(item['at']).utcoffset().total_seconds() == 0
                            for item in updates))

    def test_exit_is_accepted_before_shutdown_and_completes_after_saved_chat(self):
        gate = threading.Event()
        model_started = threading.Event()

        def slow_model(*_args, **_kwargs):
            model_started.set()
            gate.wait(5)
            return ('A fictional reply', 100, 20, 10)

        self.model.side_effect = slow_model
        result = {}

        def scenario(_):
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.assertEqual(self.call('/api/send', {'text': 'keep this message'})[0], 200)
            self.assertTrue(model_started.wait(5))

            result['first_exit'] = self.call('/api/exit', {})
            result['during'] = self.call('/api/events')[1]
            result['repeat_exit'] = self.call('/api/exit', {})
            result['send_while_stopping'] = self.call('/api/send', {'text': 'not sent'})
            result['activity_while_stopping'] = self.call('/api/activity', {'enabled': True})
            gate.set()
            self.assertTrue(self.wait(lambda: self.hub.stopped))

        self.run_loop(scenario)
        self.assertEqual((result['first_exit'][0], result['first_exit'][1]['state']),
                         (202, 'accepted'))
        self.assertTrue(result['during']['stopping'])
        self.assertFalse(result['during']['stopped'])
        self.assertEqual((result['repeat_exit'][0], result['repeat_exit'][1]['state']),
                         (202, 'stopping'))
        self.assertEqual(result['send_while_stopping'][0], 409)
        self.assertEqual(result['activity_while_stopping'][0], 409)
        self.assertFalse(self.hub.activity.enabled())
        self.assertTrue(self.hub.snapshot(0)['stopped'])
        self.assertEqual(self.call('/api/exit', {}), (200, {'ok': True, 'state': 'stopped'}))
        self.assertEqual([m['content'] for m in self.store.read('test/chat.jsonl')],
                         ['keep this message', 'A fictional reply'])

    def test_terminal_pause_commands_are_persisted_without_model_calls(self):
        results = {}

        def scenario(_):
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.assertEqual(self.call('/api/send', {'text': '/resume'})[0], 200)
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.assertTrue(self.hub.activity.enabled())
            self.assertEqual(self.call('/api/send', {'text': '/pause'})[0], 200)
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            results['paused'] = not self.hub.activity.enabled()
            self.call('/api/send', {'text': '/quit'})

        self.run_loop(scenario)
        self.assertTrue(results['paused'])
        self.model.assert_not_called()

    def test_message_round_trip_and_commands(self):
        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            self.assertEqual(self.call('/api/send', {'text': '你好 hello'})[0], 200)
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'assistant'
                                                  for e in self.call('/api/events')[1]['events'])))
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            self.call('/api/send', {'text': '/cost'})
            self.assertTrue(self.wait(lambda: any('今天' in e['text'] for e in self.call('/api/events')[1]['events'])))
            self.assertEqual(self.call('/api/send', {'text': '/quit'})[0], 200)   # ends the loop normally

        self.run_loop(scenario)
        kinds = [(e['kind'], e['text']) for e in self.hub.events]
        self.assertIn(('user', '你好 hello'), kinds)
        self.assertIn(('assistant', 'A fictional reply'), kinds)
        self.assertTrue(any(k == 'notice' and '正在保存并退出' in t for k, t in kinds))
        self.assertEqual(self.model.call_count, 1)
        saved = self.store.read('test/chat.jsonl')
        self.assertEqual([m['content'] for m in saved], ['你好 hello', 'A fictional reply'])

    def test_second_message_while_busy_is_queued(self):
        gate = threading.Event()
        self.model.side_effect = lambda *a, **k: (gate.wait(5), ('slow', 10, 5, 0))[1]

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['first'] = self.call('/api/send', {'text': 'one', 'request_id': 'one'})[0]
            result['duplicate'] = self.call('/api/send', {'text': 'one', 'request_id': 'one'})[0]
            result['conflict'] = self.call('/api/send', {'text': 'different', 'request_id': 'one'})[0]
            result['second'] = self.call('/api/send', {'text': 'two', 'request_id': 'two'})[0]
            gate.set()
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['late_duplicate'] = self.call('/api/send', {'text': 'one', 'request_id': 'one'})[0]
            result['retry'] = self.call('/api/send', {'text': 'two', 'request_id': 'two'})[0]
            self.assertTrue(self.wait(lambda: sum(e['kind'] == 'assistant'
                                                  for e in self.call('/api/events')[1]['events']) == 2))

        result = self.run_loop(scenario)
        self.assertEqual((result['first'], result['duplicate'], result['conflict'], result['second'],
                          result['late_duplicate'], result['retry']), (200, 200, 409, 200, 200, 200))
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([m['content'] for m in self.store.read('test/chat.jsonl')
                          if m['role'] == 'user'], ['one', 'two'])

    def test_provider_failure_does_not_stop_web_chat_or_retry(self):
        self.model.side_effect = [RuntimeError('private provider detail'),
                                  ('A fictional reply', 100, 20, 10)]

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            self.assertEqual(self.call('/api/send', {'text': 'first', 'request_id': 'first'})[0], 200)
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'error'
                                                  for e in self.call('/api/events')[1]['events'])))
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['second'] = self.call('/api/send', {'text': 'second', 'request_id': 'second'})[0]
            self.assertTrue(self.wait(lambda: any(e['text'] == 'A fictional reply'
                                                  for e in self.call('/api/events')[1]['events'])))

        self.assertEqual(self.run_loop(scenario)['second'], 200)
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([m['content'] for m in self.store.read('test/chat.jsonl')
                          if m['role'] == 'user'], ['first', 'second'])
        self.assertGreater(Budget(self.store).totals()[2], 0)

    def test_concurrent_distinct_sends_have_one_atomic_winner(self):
        gate = threading.Event()
        self.model.side_effect = lambda *a, **k: (gate.wait(5), ('slow', 10, 5, 0))[1]

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            barrier = threading.Barrier(3)
            statuses = []

            def send(text, request_id):
                barrier.wait()
                statuses.append(self.call('/api/send', {'text': text, 'request_id': request_id})[0])

            threads = [threading.Thread(target=send, args=('one', 'race-one')),
                       threading.Thread(target=send, args=('two', 'race-two'))]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(5)
            result['statuses'] = statuses
            gate.set()
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'assistant'
                                                  for e in self.call('/api/events')[1]['events'])))

        result = self.run_loop(scenario)
        self.assertEqual(sorted(result['statuses']), [200, 409])
        self.assertEqual(self.model.call_count, 1)

    def test_approval_buttons_map_to_y_and_n(self):
        answers = {}

        def ask(name, prompt):
            answers[name] = self.hub.approval(prompt)

        for name, button in (('yes', 'y'), ('no', 'n')):
            thread = threading.Thread(target=ask, args=(name, f'Peter 想把内容写到 {name}.txt（新文件）。同意吗？y/N '))
            thread.start()
            self.assertTrue(self.wait(lambda: any(e['kind'] == 'approval' and name in e['text']
                                                  for e in self.hub.events)))
            pending = self.call('/api/events')[1]['pending']
            self.assertEqual(self.call('/api/approve', {'answer': button, 'id': pending})[0], 200)
            self.assertEqual(self.call('/api/approve', {'answer': 'y', 'id': pending})[0], 409)  # once only
            thread.join(5)
        self.assertEqual(answers, {'yes': 'y', 'no': 'n'})
        shown = [e['text'] for e in self.hub.events if e['kind'] == 'approval']
        self.assertEqual(shown[0], 'Peter 想把内容写到 yes.txt（新文件）。同意吗？y/N ')
        self.assertEqual(self.call('/api/approve', {'answer': 'maybe'})[0], 400)

    def test_stale_or_unasked_approval_answers_are_refused(self):
        # No approval pending: a stray click must not be remembered for the next one.
        self.assertEqual(self.call('/api/approve', {'answer': 'y', 'id': 1})[0], 409)
        self.assertEqual(self.call('/api/approve', {'answer': 'y'})[0], 409)
        result = {}
        thread = threading.Thread(target=lambda: result.update(a=self.hub.approval('Peter 想把内容写到 b.txt（新文件）。同意吗？y/N ')))
        thread.start()
        self.assertTrue(self.wait(lambda: self.hub.pending is not None))
        current = self.hub.pending
        self.assertEqual(self.call('/api/approve', {'answer': 'y', 'id': current + 1})[0], 409)  # wrong card
        self.assertEqual(self.call('/api/approve', {'answer': 'n', 'id': current})[0], 200)
        thread.join(5)
        self.assertEqual(result['a'], 'n')

    def test_history_is_shown_on_open(self):
        self.store.append('test/chat.jsonl', {'role': 'user', 'content': 'earlier'})
        self.store.append('test/chat.jsonl', {'role': 'assistant', 'content': 'earlier reply'})

        def scenario(result):
            self.assertTrue(self.wait(lambda: self.call('/api/events')[1]['busy'] is False))
            result['events'] = self.call('/api/events')[1]['events']

        events = self.run_loop(scenario)['events']
        self.assertEqual([(e['kind'], e['text']) for e in events[:2]],
                         [('user', 'earlier'), ('assistant', 'earlier reply')])
        self.assertTrue(all(e['history'] and e['time'] for e in events[:2]))

    def test_multi_message_http_events_match_persisted_history(self):
        from tests.test_tools import ToolLoopTests
        fixture = ToolLoopTests()
        texts = ['Hello.', '你好。', 'See you，明天见。']
        calls = [fixture.tool_call('send_message', json.dumps({'text': text}), str(i))
                 for i, text in enumerate(texts)]
        self.cfg = replace(self.cfg, tools={'enabled': True, 'max_steps': 3})
        self.model.side_effect = [(fixture.message(calls), 100, 30, 0), (fixture.message(content=''), 120, 5, 0)]

        def scenario(result):
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.assertEqual(self.call('/api/send', {'text': 'hello'})[0], 200)
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            result['messages'] = [e['text'] for e in self.call('/api/events')[1]['events'] if e['kind'] == 'assistant']
            self.call('/api/send', {'text': '/cost'})
            self.assertTrue(self.wait(lambda: not self.call('/api/events')[1]['busy']))
            self.call('/api/send', {'text': '/quit'})

        self.assertEqual(self.run_loop(scenario)['messages'], texts)
        self.assertEqual([m['content'] for m in self.store.read('test/chat.jsonl')][1:], texts)
        self.assertEqual(self.model.call_count, 2)

    def test_startup_recovers_uncommitted_incoming_submissions(self):
        self.store.append('test/incoming-submissions.jsonl', {
            'role': 'user', 'content': 'saved before crash',
            'request_id': 'crash-id', 'message_id': 'crash-id',
            'state': 'received', 'time': '2026-10-28T12:00:00Z'
        })
        chat = Chat(self.cfg, self.store, Budget(self.store), self.model)
        hub = Hub('Test', ActivityState(self.store.root / 'test' / 'res-state.json'),
                  media_root=self.store.root / 'test' / 'media')
        hub.chat = chat

        # Simulate the startup recovery block
        from web_agent import serve_chat
        with patch('web_agent.run_chat'):
            serve_chat(chat, hub)

        self.assertFalse(hub.inbox.empty())
        item = hub.inbox.get_nowait()
        self.assertEqual(item[0], 'saved before crash')
        self.assertEqual(item[3], 'crash-id')
        self.assertIn('crash-id', hub.submissions)
        self.assertTrue(any(e.get('text') == 'saved before crash' for e in hub.events))


class StartupErrorTests(unittest.TestCase):
    def test_service_environment_refreshes_stale_windows_path(self):
        import web_launcher

        class RegistryKey:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        values = {
            ('machine', r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'):
                r'C:\Windows\System32;E:\Program Files',
            ('user', r'Environment'): r'C:\Users\test\AppData\Roaming\npm',
        }
        registry = SimpleNamespace(
            HKEY_LOCAL_MACHINE='machine', HKEY_CURRENT_USER='user',
            OpenKey=lambda hive, subkey: RegistryKey(values[(hive, subkey)]),
            QueryValueEx=lambda key, name: (key.path, 1),
        )
        with patch.dict(sys.modules, {'winreg': registry}):
            result = web_launcher._service_environment(
                {'PATH': r'C:\stale;E:\Program Files'}, platform_name='nt')

        self.assertEqual(result['PATH'],
                         r'C:\Windows\System32;E:\Program Files;C:\Users\test\AppData\Roaming\npm;C:\stale')

    def test_launcher_waits_until_chat_loop_is_ready(self):
        import web_launcher
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub('Test', ActivityState(Path(tmp) / 'state.json'))
            port = free_port()
            server = start_server(hub, TOKEN, port)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            endpoint = Path(tmp) / 'web_service.json'
            endpoint.write_text(json.dumps({'port': port, 'token': TOKEN}), encoding='utf-8')
            with patch.object(web_launcher, 'ENDPOINT', endpoint):
                self.assertIsNone(web_launcher.read_endpoint(require_ready=True))
                self.assertIsNotNone(web_launcher.read_endpoint())
                waiter = threading.Thread(target=lambda: self.assertRaises(EOFError, hub.input_fn))
                waiter.start()
                self.assertTrue(self.wait_for(lambda: hub.snapshot(0)['ready']))
                self.assertIsNotNone(web_launcher.read_endpoint(require_ready=True))
                hub.stop()
                waiter.join(5)

    def wait_for(self, condition):
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.05)
        return False

    def test_detached_service_leaves_startup_reason_for_launcher(self):
        import web_agent
        import web_launcher
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(web_agent, 'ROOT', root):
                self.assertEqual(web_agent.main(['--character', 'missing', '--no-browser']), 1)
            error = root / 'data' / 'web_service_error.txt'
            self.assertTrue(error.read_text(encoding='utf-8').strip())
            with patch.object(web_launcher, 'STARTUP_ERROR', error):
                message = web_launcher.startup_error()
            self.assertIn(error.read_text(encoding='utf-8').strip(), message)
            self.assertTrue(message.startswith(web_launcher.FAILED))


if __name__ == '__main__':
    unittest.main()

