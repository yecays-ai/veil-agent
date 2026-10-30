import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_types import RequestFailed, TooLong, UserError
from veil_agent import (Budget, Chat, ChatOutput, Store, conservative_token_ratio,
                        estimate_request_tokens, load_character, run_chat)
from web_agent import ChatInput, Hub


class ImageRecoveryAndContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'characters' / 'peter').mkdir(parents=True)
        (self.root / 'persona').mkdir()
        (self.root / 'persona' / 'peter.md').write_text('Peter is a test companion.', encoding='utf-8')
        (self.root / 'characters' / 'peter' / 'character.toml').write_text('''
name = "Peter"
provider = "fake"
persona = "persona/peter.md"
history_rounds = 20
max_output_tokens = 1024

[providers.fake]
model = "fake-model"
base_url = "https://fake.local/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 32768
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"

[tools]
enabled = true
max_steps = 2
''', encoding='utf-8')
        self.cfg = load_character(self.root, 'peter')
        self.store = Store(self.root / 'data')
        self.budget = Budget(self.store)
        self.response_message = SimpleNamespace(content='Fictional reply from Peter', tool_calls=[])
        self.model = Mock(return_value=(self.response_message, 100, 20, 0))
        self.chat = Chat(self.cfg, self.store, self.budget, self.model, output=[].append)

        # Create media directory and a valid dummy image file
        self.media_dir = self.store.root / 'peter' / 'media'
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.test_img = self.media_dir / 'valid.png'
        self.test_img.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + b'\x00' * 50)
        self.valid_media = {'id': 'media-1', 'filename': 'valid.png', 'mime': 'image/png'}

    def test_failed_image_not_recovered_after_abandon_and_restart(self):
        """Lan sends image that fails -> abandons image -> chats text -> restarts -> old image must not auto-resend."""
        # 1. Image submission is persisted as received
        req_id = 'req-img-failed-001'
        self.chat.persist_incoming('[图片]', req_id, media=self.valid_media)

        # Verify it is initially pending before processing
        pending = self.chat.recover_uncommitted_incoming()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['request_id'], req_id)

        # 2. Simulate send triggering TooLong
        with patch.object(self.chat, 'current_request_tool_schemas', return_value=[]):
            # Force limit to 1 token so preflight fails
            self.chat.cfg.provider['max_input_tokens'] = 1
            with self.assertRaises(TooLong):
                self.chat.send('[图片]', media=self.valid_media, message_id=req_id)

        # 3. Verify incoming submission was marked failed
        submissions = self.store.read('peter/incoming-submissions.jsonl')
        states = [r.get('state') for r in submissions if r.get('message_id') == req_id]
        self.assertIn('failed', states)

        # 4. Verify recover_uncommitted_incoming does NOT return the failed image
        uncommitted = self.chat.recover_uncommitted_incoming()
        self.assertEqual(uncommitted, [])

        # 5. Lan abandons the image and continues chatting text
        self.chat.cfg.provider['max_input_tokens'] = 32768
        self.chat.send('Let us talk about something else', message_id='req-text-002')
        self.assertEqual(self.store.read('peter/chat.jsonl')[-2]['content'], 'Let us talk about something else')
        self.assertEqual(self.store.read('peter/chat.jsonl')[-1]['content'], 'Fictional reply from Peter')

        # 6. Simulate server restart: create a new Chat instance
        reopened_chat = Chat(self.cfg, self.store, Budget(self.store), self.model, output=[].append)
        recovered_on_restart = reopened_chat.recover_uncommitted_incoming()

        # The failed image MUST NOT be recovered!
        self.assertEqual(recovered_on_restart, [])

    def test_interrupted_processing_marked_uncertain_and_not_auto_resent(self):
        """Simulate crash while processing: request outcome is uncertain and must not auto-produce chat."""
        req_id = 'req-crash-mid-process'
        self.chat.persist_incoming('important message', req_id)
        self.chat.mark_incoming_processing([req_id])

        # Server crashes here without committing or failing.
        # Now restart VEIL:
        reopened_chat = Chat(self.cfg, self.store, Budget(self.store), self.model, output=[].append)
        recovered = reopened_chat.recover_uncommitted_incoming()

        # Must NOT re-enter queue automatically
        self.assertEqual(recovered, [])

        # Must record durable audit state as uncertain
        rows = self.store.read('peter/incoming-submissions.jsonl')
        latest_state = [r for r in rows if r.get('message_id') == req_id][-1]
        self.assertEqual(latest_state.get('state'), 'uncertain')
        self.assertEqual(latest_state.get('reason'), 'interrupted_during_processing')

    def test_new_conversation_cancels_previous_uncommitted_messages(self):
        """New conversation must not inherit failed or uncommitted requests from prior session."""
        req_id = 'req-old-session'
        self.chat.persist_incoming('uncommitted old message', req_id)

        # Start a new conversation
        self.chat.start_new_conversation()

        # Must be marked cancelled
        rows = self.store.read('peter/incoming-submissions.jsonl')
        latest = [r for r in rows if r.get('message_id') == req_id][-1]
        self.assertEqual(latest.get('state'), 'cancelled')
        self.assertEqual(latest.get('reason'), 'new_conversation')

        # Reopen check
        self.assertEqual(self.chat.recover_uncommitted_incoming(), [])

    def test_normal_single_image_fits_by_pruning_history_and_memories(self):
        """Sending a single normal image with long history and memories must fit by pruning croppable context."""
        # Prepopulate history with 15 rounds of text and an older image
        for i in range(15):
            self.store.append(self.chat.path, {'role': 'user', 'content': f'User message {i}' * 50, 'message_id': f'u{i}'})
            self.store.append(self.chat.path, {'role': 'assistant', 'content': f'Assistant reply {i}' * 50, 'message_id': f'a{i}'})
        self.chat.history, self.chat.rounds = self.chat.recent()

        # Mock large tool schemas (e.g. 25KB of tools)
        large_tool = SimpleNamespace(
            name='heavy_tool', description='x' * 2000,
            inputSchema={'type': 'object', 'properties': {'field': {'type': 'string', 'description': 'y' * 1000}}}
        )
        tools = SimpleNamespace(tools=[large_tool] * 8, description='Large tools', capability_event=None)
        self.chat.tools = tools

        # Mock memory with several recalled memories
        self.chat.memory = SimpleNamespace(
            context=Mock(return_value=[{'role': 'system', 'content': f'Recalled memory {j}' * 40} for j in range(5)]),
            last_context_refs=['mem1', 'mem2'],
            refresh_opening=Mock()
        )

        # Now send a normal photo with caption
        self.chat.cfg.provider['max_input_tokens'] = 32768
        reply = self.chat.send('Look at this photo', media=self.valid_media, message_id='req-photo-fit')

        # Must succeed and reach the model!
        self.assertEqual(reply, 'Fictional reply from Peter')
        self.model.assert_called_once()
        sent_messages = self.model.call_args.args[0]
        # User message with photo was sent
        user_msg = sent_messages[-1]
        self.assertEqual(user_msg['role'], 'user')
        self.assertIsInstance(user_msg['content'], list)
        self.assertEqual(user_msg['content'][0]['text'], 'Look at this photo')

    def test_unavoidable_overflow_provides_exact_breakdown(self):
        """When input genuinely exceeds limit after all cropping, TooLong must provide detailed token breakdown."""
        # Set limit lower than just the image reserve (4096 tokens) + system persona
        self.chat.cfg.provider['max_input_tokens'] = 2000
        with self.assertRaises(TooLong) as caught:
            self.chat.send('Photo overflow test', media=self.valid_media, message_id='req-overflow')

        err_msg = str(caught.exception)
        # Check transparent breakdown
        self.assertIn('太长', err_msg)
        self.assertIn('上限 2000 tokens', err_msg)
        self.assertIn('系统人设与身份', err_msg)
        self.assertIn('当前消息（含图片）', err_msg)
        self.assertIn('已裁剪全部历史与记忆', err_msg)

    def test_hub_error_deduplication(self):
        """Hub must not push duplicate error events for the same request_id."""
        activity = SimpleNamespace(set=Mock(), enabled=Mock(return_value=True))
        hub = Hub('Peter', activity)
        hub.chat = self.chat

        # Publish first error output for req-123
        err_output1 = ChatOutput('请求失败，未自动重试：500 Internal Error', request_id='req-123', is_error=True)
        hub.output(err_output1)

        # Publish second identical error output for req-123
        err_output2 = ChatOutput('请求失败，未自动重试：500 Internal Error', request_id='req-123', is_error=True)
        hub.output(err_output2)

        error_events = [e for e in hub.events if e.get('kind') == 'error' and e.get('request_id') == 'req-123']
        self.assertEqual(len(error_events), 1)

    def test_finish_media_submission_fails_incoming_on_uncommitted(self):
        """Hub.finish_media_submission(committed=False) must mark incoming-submissions as failed."""
        activity = SimpleNamespace(set=Mock(), enabled=Mock(return_value=True))
        hub = Hub('Peter', activity)
        hub.chat = self.chat

        req_id = 'req-uncommitted-001'
        self.chat.persist_incoming('test text', req_id, media=self.valid_media)
        hub.submissions[req_id] = b'dummy'

        hub.finish_media_submission(committed=False, text='test text', media=self.valid_media, request_id=req_id)

        rows = self.store.read('peter/incoming-submissions.jsonl')
        latest = [r for r in rows if r.get('message_id') == req_id][-1]
        self.assertEqual(latest.get('state'), 'failed')
        self.assertEqual(latest.get('reason'), 'submission_uncommitted')
        self.assertNotIn(req_id, hub.submissions)

    def test_missing_media_file_fails_recovery(self):
        """A received message whose media file is deleted/missing must be marked failed and not recovered."""
        req_id = 'req-missing-file'
        missing_media = {'id': 'media-ghost', 'filename': 'ghost.png', 'mime': 'image/png'}
        self.chat.persist_incoming('ghost image', req_id, media=missing_media)

        recovered = self.chat.recover_uncommitted_incoming()
        self.assertEqual(recovered, [])

        rows = self.store.read('peter/incoming-submissions.jsonl')
        latest = [r for r in rows if r.get('message_id') == req_id][-1]
        self.assertEqual(latest.get('state'), 'failed')
        self.assertEqual(latest.get('reason'), 'media_missing')


if __name__ == '__main__':
    unittest.main()
