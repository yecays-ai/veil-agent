"""Tickets 098 / 102: channels Lan uses when she is away from the computer (email, QQ).

A channel's messages from Lan enter the same chat as desktop messages (same history, same memory);
everything Peter says in that turn goes back through the channel she used. Outside such a turn he can
choose a channel himself with send_message(<channel>=true).

A channel provides: name, label, prefix, poll_seconds, poll() -> [Incoming], ack(incoming),
reply(text, reply_ref) -> None or a Chinese error, and ready_to_send() -> bool.

Ticket 114: while Peter waits for Lan's approval in a turn that came from a channel, the question is sent
there too, and a plain 同意 / 不同意 reply on that channel answers it.
"""
import hashlib
import re
import threading
from dataclasses import dataclass


class ChannelError(Exception):
    """A receive failure worth one Chinese line on the desktop."""


@dataclass
class Incoming:
    key: str            # stable identity, used for de-duplication
    text: str
    reply_ref: object = None
    handle: object = None


APPROVE_WORDS = frozenset({'同意', '好', '好的', '可以', '行', 'y', 'yes', 'ok'})
REFUSE_WORDS = frozenset({'不同意', '不', '不要', '不行', '拒绝', 'n', 'no'})


def approval_answer(text):
    """'y' or 'n' when Lan's last line is a plain yes/no, else None."""
    lines = [line for line in str(text or '').splitlines() if line.strip()]
    if not lines:
        return None
    word = re.sub(r'[\s。．.!！~～,，、]+', '', lines[-1]).casefold()
    if word in APPROVE_WORDS:
        return 'y'
    if word in REFUSE_WORDS:
        return 'n'
    return None


class AwayBridge:
    def __init__(self, channel, hub):
        self.channel, self.hub = channel, hub
        self.replying = {}       # request_id -> reply_ref for messages waiting to be answered
        self.turn = None         # reply_ref while answering a message from this channel
        self.in_turn = False
        self.collected = []      # Peter's messages in the current channel turn
        self.collected_attachments = []
        self.proactive = []      # messages he marked for this channel outside such a turn
        self.last_error = None
        self.asking = False      # an approval question was sent on this channel in the current turn
        self.state_lock = threading.Lock()
        self.outbox = []
        self.outbox_ready = threading.Condition()

    def request_id(self, key):
        return f'{self.channel.name}-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:40]

    # chat thread hooks ------------------------------------------------------
    def turn_ended(self):
        """The chat loop is about to take its next input: send what the last turn produced."""
        with self.state_lock:
            if self.in_turn and self.collected:
                self._queue('\n\n'.join(self.collected), self.turn, self.collected_attachments)
            elif self.proactive:
                self._queue('\n\n'.join(item[0] for item in self.proactive), None,
                            [attachment for _, items in self.proactive for attachment in items])
            self.turn, self.in_turn, self.collected, self.collected_attachments, self.proactive = (
                None, False, [], [], [])
            self.asking = False

    def turn_started(self, item):
        request_id = getattr(item, 'message_id', None)
        with self.state_lock:
            if isinstance(request_id, str) and request_id in self.replying:
                self.turn = self.replying.pop(request_id)
                self.in_turn = True

    def approval_asked(self, prompt):
        """VEIL is asking Lan to approve one action. If she gave this task on this channel, ask her here
        too (after what Peter already said in this turn) so she can answer without coming to the computer."""
        with self.state_lock:
            if not self.in_turn:
                return False
            if self.collected:
                self._queue('\n\n'.join(self.collected), self.turn, self.collected_attachments)
                self.collected, self.collected_attachments = [], []
            question = re.sub(r'\s*y/N\s*$', '', str(prompt).strip())
            question = re.sub(r'\s*\n\s*', ' ', question)   # one QQ message, not split on blank lines
            self._queue(f'{question} 回复「同意」或「不同意」。', self.turn)
            self.asking = True
        return True

    def assistant_message(self, text, attachments=None):
        with self.state_lock:
            if self.in_turn:
                self.collected.append(text)
                if self.channel.name == 'email' and isinstance(attachments, list):
                    self.collected_attachments.extend(item for item in attachments if isinstance(item, dict))

    def can_add_reply_attachment(self):
        with self.state_lock:
            return (self.channel.name != 'email' or not self.in_turn
                    or len(self.collected_attachments) < 3)

    def requested(self, text, attachments=None):
        """send_message(<channel>=true). Inside this channel's turn every message goes out anyway."""
        with self.state_lock:
            if not self.in_turn:
                files = (list(attachments) if self.channel.name == 'email'
                         and isinstance(attachments, list) else [])
                self.proactive.append((text, files))
        return True

    # sending worker ---------------------------------------------------------
    def _queue(self, text, reply_ref, attachments=()):
        with self.outbox_ready:
            files = list(attachments) if self.channel.name == 'email' else []
            self.outbox.append((text, reply_ref, files) if files else (text, reply_ref))
            self.outbox_ready.notify()

    def _report(self, error):
        if error and error != self.last_error:
            self.hub.push('notice', error)
        self.last_error = error

    def send_pending(self):
        """Send queued messages while the channel allows it. Returns when the outbox is empty or blocked."""
        while True:
            with self.outbox_ready:
                if not self.outbox or self.hub.stopped:
                    return
                if not self.channel.ready_to_send():
                    return
                item = self.outbox.pop(0)
                text, reply_ref = item[:2]
                attachments = item[2] if len(item) > 2 else []
            error = (self.channel.reply(text, reply_ref, attachments=attachments)
                     if attachments and self.channel.name == 'email'
                     else self.channel.reply(text, reply_ref))
            self._report(error)
            if not error:
                self.hub.push('notice', f'已通过{self.channel.label}发给 Lan。')

    def _send_worker(self):
        while not self.hub.stopped:
            with self.outbox_ready:
                self.outbox_ready.wait(2)
            try:
                self.send_pending()
            except Exception as exc:   # never let the thread die silently
                self._report(f'{self.channel.label}发送出错：{type(exc).__name__}: {exc}')

    # receiving loop ---------------------------------------------------------
    def poll_once(self):
        try:
            found = self.channel.poll()
        except ChannelError as exc:
            self._report(str(exc))
            return
        self.last_error = None
        for incoming in found:
            if not incoming.text:
                self.channel.ack(incoming)
                continue
            answer = approval_answer(incoming.text)
            with self.state_lock:
                asking = self.asking
            if asking and answer is not None:
                with self.hub.cond:
                    pending = self.hub.pending
                if pending is not None and self.hub.answer(pending, answer):
                    self.channel.ack(incoming)
                    continue
            request_id = self.request_id(incoming.key)
            with self.state_lock:
                self.replying[request_id] = incoming.reply_ref
            result = self.hub.submit(self.channel.prefix + incoming.text, request_id,
                                     allow_while_busy=False)
            if result in ('accepted', 'duplicate'):
                self.channel.ack(incoming)
            else:
                with self.state_lock:
                    self.replying.pop(request_id, None)
                return  # busy: the channel keeps it and offers it again on the next poll

    def _poll_worker(self):
        while not self.hub.stopped:
            try:
                self.poll_once()
            except Exception as exc:   # never let the thread die silently
                self._report(f'{self.channel.label}接收出错：{type(exc).__name__}: {exc}')
            with self.hub.cond:
                self.hub.cond.wait_for(lambda: self.hub.stopped, timeout=self.channel.poll_seconds)

    def start(self):
        for target in (self._send_worker, self._poll_worker):
            threading.Thread(target=target, daemon=True,
                             name=f'{self.channel.name}-{target.__name__}').start()
