"""Tickets 098 / 102: channels Lan uses when she is away from the computer (email, QQ).

A channel's messages from Lan enter the same chat as desktop messages (same history, same memory);
everything Peter says in that turn goes back through the channel she used. Outside such a turn he can
choose a channel himself with send_message(<channel>=true).

A channel provides: name, label, prefix, poll_seconds, poll() -> [Incoming], ack(incoming),
reply(text, reply_ref) -> None or a Chinese error, and ready_to_send() -> bool.
"""
import hashlib
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


class AwayBridge:
    def __init__(self, channel, hub):
        self.channel, self.hub = channel, hub
        self.replying = {}       # request_id -> reply_ref for messages waiting to be answered
        self.turn = None         # reply_ref while answering a message from this channel
        self.in_turn = False
        self.collected = []      # Peter's messages in the current channel turn
        self.proactive = []      # messages he marked for this channel outside such a turn
        self.last_error = None
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
                self._queue('\n\n'.join(self.collected), self.turn)
            elif self.proactive:
                self._queue('\n\n'.join(self.proactive), None)
            self.turn, self.in_turn, self.collected, self.proactive = None, False, [], []

    def turn_started(self, item):
        request_id = getattr(item, 'message_id', None)
        with self.state_lock:
            if isinstance(request_id, str) and request_id in self.replying:
                self.turn = self.replying.pop(request_id)
                self.in_turn = True

    def assistant_message(self, text):
        with self.state_lock:
            if self.in_turn:
                self.collected.append(text)

    def requested(self, text):
        """send_message(<channel>=true). Inside this channel's turn every message goes out anyway."""
        with self.state_lock:
            if not self.in_turn:
                self.proactive.append(text)
        return True

    # sending worker ---------------------------------------------------------
    def _queue(self, text, reply_ref):
        with self.outbox_ready:
            self.outbox.append((text, reply_ref))
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
                text, reply_ref = self.outbox.pop(0)
            error = self.channel.reply(text, reply_ref)
            self._report(error)
            if not error:
                self.hub.push('notice', f'已通过{self.channel.label}发给 Lan。')

    def _send_worker(self):
        while not self.hub.stopped:
            with self.outbox_ready:
                self.outbox_ready.wait(2)
            self.send_pending()

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
            request_id = self.request_id(incoming.key)
            with self.state_lock:
                self.replying[request_id] = incoming.reply_ref
            result = self.hub.submit(self.channel.prefix + incoming.text, request_id)
            if result in ('accepted', 'duplicate'):
                self.channel.ack(incoming)
            else:
                with self.state_lock:
                    self.replying.pop(request_id, None)
                return  # busy: the channel keeps it and offers it again on the next poll

    def _poll_worker(self):
        while not self.hub.stopped:
            self.poll_once()
            with self.hub.cond:
                self.hub.cond.wait_for(lambda: self.hub.stopped, timeout=self.channel.poll_seconds)

    def start(self):
        for target in (self._send_worker, self._poll_worker):
            threading.Thread(target=target, daemon=True,
                             name=f'{self.channel.name}-{target.__name__}').start()
