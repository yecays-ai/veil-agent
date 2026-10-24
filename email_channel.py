"""Ticket 098: Peter and Lan talk by email (QQ Mail over IMAP/SMTP) when she is away from the computer.

Standard library only: smtplib sends, imaplib receives. Only mail from Lan's own address is read;
the recipient is always Lan, never chosen by the model.
"""
import hashlib
import html
import imaplib
import json
import re
import smtplib
import ssl
import threading
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid, parseaddr
from html.parser import HTMLParser
from pathlib import Path


EMAIL_PREFIX = '[Lan sent this by email; she is away from the computer.]\n'
ENV_KEYS = ('PETER_EMAIL_ADDRESS', 'PETER_EMAIL_AUTH_CODE', 'LAN_EMAIL_ADDRESS')
MAX_BODY_CHARS = 4000

# Lines that start the quoted original in a reply. QQ Mail writes the first two;
# the others cover iOS Mail, Gmail and Outlook replies.
QUOTE_MARKERS = (
    re.compile(r'^-{4,}\s*原始邮件\s*-{4,}'),
    re.compile(r'^-{4,}\s*Original\s*-{4,}', re.IGNORECASE),
    re.compile(r'^-{3,}\s*Original Message\s*-{3,}', re.IGNORECASE),
    re.compile(r'^(发件人|From)\s*[:：]'),
    re.compile(r'^在.{0,80}写道[:：]\s*$'),
    re.compile(r'^On .{0,200}wrote:\s*$'),
)


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, _attrs):
        if tag in ('br', 'p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(raw):
    parser = _TextOnly()
    parser.feed(raw)
    return html.unescape(''.join(parser.parts))


def reply_text(body):
    """The new part of a reply: everything above the quoted original."""
    lines = body.replace('\r\n', '\n').replace('\xa0', ' ').split('\n')
    kept = []
    for line in lines:
        stripped = line.strip()
        if any(marker.match(stripped) for marker in QUOTE_MARKERS):
            break
        if stripped.startswith('>'):
            continue
        kept.append(line.rstrip())
    return '\n'.join(kept).strip()


def message_body(message):
    plain = message.get_body(preferencelist=('plain',))
    if plain is not None:
        return plain.get_content()
    rich = message.get_body(preferencelist=('html',))
    if rich is not None:
        return html_to_text(rich.get_content())
    return ''


class EmailChannel:
    def __init__(self, store_root, character_id, address, auth_code, lan_address, settings, sender_name='Peter'):
        self.address, self.auth_code, self.sender_name = address, auth_code, sender_name
        self.lan_address = lan_address.strip().lower()
        self.poll_seconds = settings['poll_seconds']
        self.daily_max_sent = settings['daily_max_sent']
        self.smtp_host, self.smtp_port = settings['smtp_host'], settings['smtp_port']
        self.imap_host, self.imap_port = settings['imap_host'], settings['imap_port']
        self.log_path = Path(store_root) / character_id / 'email-log.jsonl'
        self.lock = threading.Lock()

    @classmethod
    def from_env(cls, root, cfg, store_root):
        settings = getattr(cfg, 'email', None)
        if not settings or not settings.get('enabled'):
            return None
        from dotenv import dotenv_values
        values = dotenv_values(Path(root) / '.env', interpolate=False, encoding='utf-8-sig')
        found = [(values.get(key) or '').strip() for key in ENV_KEYS]
        if not all(found):
            return None
        return cls(store_root, cfg.id, *found, settings, cfg.name)

    # --- log -------------------------------------------------------------
    def _log_rows(self):
        try:
            text = self.log_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return []
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def _log(self, row):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': datetime.now().astimezone().isoformat(), **row},
                                    ensure_ascii=False) + '\n')

    def sent_today(self):
        today = datetime.now().astimezone().date().isoformat()
        return sum(1 for row in self._log_rows() if row.get('kind') == 'sent' and row.get('time', '')[:10] == today)

    def received_ids(self):
        return {row.get('message_id') for row in self._log_rows() if row.get('kind') == 'received'}

    # --- sending -----------------------------------------------------------
    def send(self, text, subject=None, in_reply_to=None):
        """Send one email to Lan. Returns None on success or a Chinese reason."""
        text = text.strip()
        if not text:
            return None
        with self.lock:
            if self.sent_today() >= self.daily_max_sent:
                return f'今天的邮件已达到 {self.daily_max_sent} 封上限，这封没有发出。'
            message = EmailMessage()
            message['From'] = f'{self.sender_name} <{self.address}>'
            message['To'] = self.lan_address
            if not subject:
                first = text.splitlines()[0].strip()
                subject = first[:40] + ('…' if len(first) > 40 else '')
            message['Subject'] = subject
            message_id = make_msgid(domain=self.address.split('@')[-1])
            message['Message-ID'] = message_id
            if in_reply_to:
                message['In-Reply-To'] = in_reply_to
                message['References'] = in_reply_to
            message.set_content(text)
            try:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30,
                                      context=ssl.create_default_context()) as smtp:
                    smtp.login(self.address, self.auth_code)
                    smtp.send_message(message)
            except smtplib.SMTPAuthenticationError:
                return '邮箱登录失败：请检查 .env 里的邮箱地址和授权码。'
            except (smtplib.SMTPException, OSError):
                return '邮件发送失败（网络或邮箱服务暂时不可用），这封没有发出。'
            self._log({'kind': 'sent', 'message_id': message_id, 'subject': subject})
            return None

    # --- receiving ---------------------------------------------------------
    def fetch_new(self):
        """Unread mail from Lan as (uid, message_id, subject, text); other senders are left untouched."""
        found = []
        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=ssl.create_default_context(),
                               timeout=30) as imap:
            imap.login(self.address, self.auth_code)
            imap.select('INBOX')
            status, data = imap.uid('search', None, 'UNSEEN', 'FROM', f'"{self.lan_address}"')
            if status != 'OK' or not data or not data[0]:
                return found
            seen = self.received_ids()
            for uid in data[0].split()[:10]:
                status, parts = imap.uid('fetch', uid, '(BODY.PEEK[])')
                raw = next((part[1] for part in parts or [] if isinstance(part, tuple)), None)
                if status != 'OK' or raw is None:
                    continue
                message = BytesParser(policy=policy.default).parsebytes(raw)
                sender = parseaddr(str(message.get('From', '')))[1].strip().lower()
                if sender != self.lan_address:
                    continue  # search FROM is a substring match; only her exact address counts
                message_id = str(message.get('Message-ID', '')).strip() or (
                    '<' + hashlib.sha256(raw).hexdigest() + '@local>')
                if message_id in seen:
                    imap.uid('store', uid, '+FLAGS', '(\\Seen)')
                    continue
                text = reply_text(message_body(message))[:MAX_BODY_CHARS]
                found.append((uid, message_id, str(message.get('Subject', '')), text))
        return found

    def mark_received(self, uid, message_id):
        self._log({'kind': 'received', 'message_id': message_id})
        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=ssl.create_default_context(),
                                   timeout=30) as imap:
                imap.login(self.address, self.auth_code)
                imap.select('INBOX')
                imap.uid('store', uid, '+FLAGS', '(\\Seen)')
        except (imaplib.IMAP4.error, OSError):
            pass  # the log already prevents a second delivery

    # away-bridge interface (away_bridge.py) ------------------------------------
    name, label, prefix = 'email', '邮件', EMAIL_PREFIX

    def ready_to_send(self):
        return True

    def poll(self):
        from away_bridge import ChannelError, Incoming
        try:
            mails = self.fetch_new()
        except imaplib.IMAP4.error as exc:
            raise ChannelError('收邮件失败：邮箱登录被拒绝，请检查 .env 里的邮箱地址和授权码。') from exc
        except OSError as exc:
            raise ChannelError('收邮件失败（网络或邮箱服务暂时不可用），稍后会再试。') from exc
        return [Incoming(message_id, text, (message_id, subject or self.sender_name), uid)
                for uid, message_id, subject, text in mails]

    def ack(self, incoming):
        self.mark_received(incoming.handle, incoming.key)

    def reply(self, text, reply_ref):
        if reply_ref:
            message_id, subject = reply_ref
            subject = subject if subject.lower().startswith('re:') else 'Re: ' + subject
            return self.send(text, subject, message_id)
        return self.send(text)
