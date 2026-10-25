import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import email_channel
from away_bridge import AwayBridge
from email_channel import EMAIL_PREFIX, EmailChannel, reply_text
from veil_agent import send_message_schema
from web_agent import Hub


SETTINGS = {'enabled': True, 'poll_seconds': 60, 'daily_max_sent': 2, 'smtp_host': 'smtp.example',
            'smtp_port': 465, 'imap_host': 'imap.example', 'imap_port': 993}


def raw_mail(sender, body, message_id, subject='Re: hi'):
    message = EmailMessage()
    message['From'] = sender
    message['To'] = 'peter@example.com'
    message['Subject'] = subject
    message['Message-ID'] = message_id
    message.set_content(body)
    return message.as_bytes()


class FakeImap:
    mails = {}
    stored = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def login(self, *_):
        pass

    def select(self, *_):
        return 'OK', [b'1']

    def uid(self, command, *args):
        if command == 'search':
            return 'OK', [b' '.join(self.mails)]
        if command == 'fetch':
            return 'OK', [(b'1 (BODY[] {1}', self.mails[args[0]]), b')']
        if command == 'store':
            FakeImap.stored.append(args[0])
            return 'OK', []
        raise AssertionError(command)


class FakeSmtp:
    sent = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def login(self, *_):
        pass

    def send_message(self, message):
        FakeSmtp.sent.append(message)


class EmailChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.channel = EmailChannel(Path(self.tmp.name), 'peter', 'peter@example.com', 'code',
                                    'Lan@Example.com', SETTINGS)
        FakeImap.mails, FakeImap.stored, FakeSmtp.sent = {}, [], []
        patches = [patch.object(email_channel.imaplib, 'IMAP4_SSL', FakeImap),
                   patch.object(email_channel.smtplib, 'SMTP_SSL', FakeSmtp)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_reply_text_cuts_qq_quote(self):
        body = ('I am in bed now.\n\nnight\n\n------------------ 原始邮件 ------------------\n'
                '发件人: "Peter" <peter@example.com>\n> old text')
        self.assertEqual(reply_text(body), 'I am in bed now.\n\nnight')
        self.assertEqual(reply_text('ok\n\nOn Sat, Oct 24, 2026 Peter wrote:\n> hi'), 'ok')

    def test_only_lans_exact_address_is_read(self):
        FakeImap.mails = {
            b'1': raw_mail('Lan <lan@example.com>', 'hello from bed', '<a@x>'),
            b'2': raw_mail('Someone <notlan@example.com.evil>', 'hi', '<b@x>'),
        }
        found = self.channel.fetch_new()
        self.assertEqual([(uid, mid, text) for uid, mid, _, text in found], [(b'1', '<a@x>', 'hello from bed')])
        self.channel.mark_received(b'1', '<a@x>')
        self.assertEqual(self.channel.fetch_new(), [])  # logged as received, never delivered twice

    def test_send_goes_to_lan_and_respects_daily_cap(self):
        self.assertIsNone(self.channel.send('first line\nmore', in_reply_to='<a@x>'))
        self.assertIsNone(self.channel.send('second'))
        self.assertIn('上限', self.channel.send('third'))
        self.assertEqual(len(FakeSmtp.sent), 2)
        self.assertEqual(FakeSmtp.sent[0]['To'], 'lan@example.com')
        self.assertEqual(FakeSmtp.sent[0]['Subject'], 'first line')
        self.assertEqual(FakeSmtp.sent[0]['In-Reply-To'], '<a@x>')

    def test_bridge_delivers_mail_and_replies_by_email(self):
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        hub.busy = False
        bridge = AwayBridge(self.channel, hub)
        hub.away = [bridge]
        FakeImap.mails = {b'7': raw_mail('lan@example.com', 'can you hear me', '<m@x>', 'hi')}
        bridge.poll_once()
        self.assertEqual(FakeImap.stored, [b'7'])
        item = hub.input_fn()
        self.assertEqual(str(item), EMAIL_PREFIX + 'can you hear me')
        hub.output('Peter: yes')
        hub.output('Peter: I am here')
        bridge.turn_ended()
        self.assertEqual(bridge.outbox, [('yes\n\nI am here', ('<m@x>', 'hi'))])
        bridge.send_pending()
        self.assertEqual(FakeSmtp.sent[-1]['Subject'], 'Re: hi')
        self.assertEqual(FakeSmtp.sent[-1]['In-Reply-To'], '<m@x>')

    def test_busy_hub_leaves_mail_unread(self):
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        hub.busy = True
        bridge = AwayBridge(self.channel, hub)
        FakeImap.mails = {b'3': raw_mail('lan@example.com', 'later', '<l@x>')}
        bridge.poll_once()
        self.assertEqual(FakeImap.stored, [])
        self.assertEqual(bridge.replying, {})

    def test_proactive_email_outside_email_turn(self):
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        bridge = AwayBridge(self.channel, hub)
        bridge.requested('miss you')
        bridge.requested('come back soon')
        bridge.turn_ended()
        self.assertEqual(bridge.outbox, [('miss you\n\ncome back soon', None)])

    def test_send_message_schema_offers_email_only_when_configured(self):
        self.assertNotIn('email', send_message_schema()['function']['parameters']['properties'])
        properties = send_message_schema(channels=('email', 'qq'))['function']['parameters']['properties']
        self.assertIn('email', properties)
        self.assertIn('qq', properties)

    def test_email_reply_sends_the_immutable_lab_attachment_snapshot(self):
        media_id = 'a' * 32
        raw = b'<html><body>tiny game</body></html>'
        row = {'id': media_id, 'filename': media_id + '.html', 'mime': 'text/html',
               'view': 'html', 'kind': 'attachment', 'name': 'game.html', 'size': len(raw),
               'request_id': 'assistant-attachment-' + media_id}
        media_root = self.channel.log_path.parent / 'media'
        media_root.mkdir(parents=True)
        (media_root / row['filename']).write_bytes(raw)
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        bridge = AwayBridge(self.channel, hub)
        bridge.in_turn = True
        bridge.turn = ('<lan@example.com>', 'Re: game')
        bridge.assistant_message('Here it is.', [row])
        bridge.requested('Here it is.', [row])
        bridge.turn_ended()

        self.assertEqual(len(bridge.outbox), 1)
        self.assertEqual(bridge.outbox[0][2], [row])
        bridge.send_pending()
        sent = FakeSmtp.sent[-1]
        attached = list(sent.iter_attachments())
        self.assertEqual(len(attached), 1)
        self.assertEqual(attached[0].get_filename(), 'game.html')
        self.assertEqual(attached[0].get_payload(decode=True), raw)
        self.assertEqual(sent['In-Reply-To'], '<lan@example.com>')

    def test_proactive_email_attachment_uses_email_only(self):
        media_id = 'b' * 32
        raw = b'hello from Peter'
        row = {'id': media_id, 'filename': media_id + '.txt', 'mime': 'text/plain',
               'view': 'text', 'kind': 'attachment', 'name': 'note.txt', 'size': len(raw),
               'request_id': 'assistant-attachment-' + media_id}
        media_root = self.channel.log_path.parent / 'media'
        media_root.mkdir(parents=True)
        (media_root / row['filename']).write_bytes(raw)
        hub = Hub('Peter', SimpleNamespace(enabled=lambda: False))
        bridge = AwayBridge(self.channel, hub)
        bridge.requested('A note for you.', [row])
        bridge.turn_ended()
        bridge.send_pending()
        self.assertEqual(FakeSmtp.sent[-1].iter_attachments().__next__().get_payload(decode=True), raw)


if __name__ == '__main__':
    unittest.main()
