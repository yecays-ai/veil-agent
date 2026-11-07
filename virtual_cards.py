"""Virtual transfers and gifts, ported from the VEIL phone chat.

A card is a chat gesture. It never moves money, property, or inventory. Either person can
send one. Only the recipient can receive, decline, or return it, and a transfer that was
already received can still be returned by that same recipient.
"""

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class VirtualCardError(ValueError):
    def __init__(self, zh, en):
        super().__init__(en)
        self.zh = zh
        self.en = en


AMOUNT_LIMIT = Decimal('1000000000000')
NOTE_LIMIT = 240
ICON_LIMIT = 24
GIFT_LIMIT = 500
CLIENT_ACTION_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
MESSAGE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
_CONTROL = dict.fromkeys(map(ord, '\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069'), None)

TRANSFER_STATUS = {
    'pending': '待接收',
    'received': '已接收',
    'declined': '已拒绝',
    'returned': '已退回',
}
GIFT_STATUS = {
    'pending': '待收下',
    'accepted': '已收下',
    'declined': '已退回',
}
TRANSFER_ACTIONS = frozenset({'receive', 'decline', 'return'})
GIFT_ACTIONS = frozenset({'accept', 'decline'})


def _clean(value, limit):
    if not isinstance(value, str):
        return ''
    text = value.translate(_CONTROL)
    text = ''.join(ch if unicodedata.category(ch)[0] != 'C' else ' ' for ch in text)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def normalize_amount(value):
    if isinstance(value, bool) or value is None:
        raise VirtualCardError('金额无效。', 'The amount is invalid.')
    try:
        if isinstance(value, str):
            raw = value.strip()
            if not re.fullmatch(r'(?:0|[1-9]\d{0,12})(?:\.\d{1,2})?', raw):
                raise InvalidOperation
            number = Decimal(raw)
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if number_is_bad(value):
                raise InvalidOperation
            number = Decimal(str(value))
        else:
            raise InvalidOperation
        number = number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise VirtualCardError('金额无效。', 'The amount is invalid.') from None
    if number <= 0 or number > AMOUNT_LIMIT:
        raise VirtualCardError('金额需要大于 0，且不超过 1,000,000,000,000。',
                                'Enter an amount greater than 0 and no more than 1,000,000,000,000.')
    return f'{number:.2f}'


def number_is_bad(value):
    return value != value or value in (float('inf'), float('-inf'))


def normalize_note(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise VirtualCardError('留言必须是文字。', 'The transfer note must be text.')
    note = _clean(value, NOTE_LIMIT + 1)
    if len(note) > NOTE_LIMIT:
        raise VirtualCardError('留言请控制在 240 个字以内。', 'The transfer note must be at most 240 characters.')
    return note


def normalize_icon(value):
    if value is None or value == '':
        value = '🎁'
    if not isinstance(value, str):
        raise VirtualCardError('请选一个礼物图标。', 'Choose a gift icon.')
    icon = _clean(value, ICON_LIMIT + 1)
    if not icon or len(icon) > ICON_LIMIT:
        raise VirtualCardError('礼物图标请控制在 1 到 24 个字。', 'A gift icon must contain 1–24 characters.')
    return icon


def normalize_gift(value):
    if not isinstance(value, str):
        raise VirtualCardError('礼物内容请写成文字。', 'Gift content must be text.')
    content = _clean(value, GIFT_LIMIT + 1)
    if not content or len(content) > GIFT_LIMIT:
        raise VirtualCardError('礼物内容请填写 1 至 500 个字。', 'Gift content must contain 1–500 characters.')
    return content


def client_action_id(value):
    if not isinstance(value, str) or not CLIENT_ACTION_RE.fullmatch(value):
        return None
    return value


def _person(actor, user_name, character_name):
    return user_name if actor == 'user' else character_name


def build_card(kind, fields, sender):
    if sender not in ('user', 'assistant'):
        raise VirtualCardError('发送方无效。', 'The sender is invalid.')
    if not isinstance(fields, dict):
        raise VirtualCardError('卡片内容无效。', 'The card payload is invalid.')
    recipient = 'assistant' if sender == 'user' else 'user'
    if kind == 'transfer':
        return {
            'type': 'transfer',
            'amount': normalize_amount(fields.get('amount')),
            'note': normalize_note(fields.get('note', '')),
            'sender': sender,
            'recipient': recipient,
            'status': 'pending',
        }
    if kind == 'gift':
        return {
            'type': 'gift',
            'icon': normalize_icon(fields.get('icon')),
            'content': normalize_gift(fields.get('content')),
            'sender': sender,
            'recipient': recipient,
            'status': 'pending',
        }
    raise VirtualCardError('卡片类型无效。', 'The card type is invalid.')


def can_apply(card, actor, action):
    if not isinstance(card, dict) or actor not in ('user', 'assistant') or actor != card.get('recipient'):
        return False
    if card.get('type') == 'transfer':
        if card.get('status') == 'pending':
            return action in ('receive', 'decline')
        return card.get('status') == 'received' and action == 'return'
    if card.get('type') == 'gift':
        return card.get('status') == 'pending' and action in GIFT_ACTIONS
    return False


def next_status(card, action):
    if not can_apply(card, card.get('recipient'), action):
        raise VirtualCardError('现在不能这样处理这张卡片。', 'This card action is not available.')
    if card['type'] == 'transfer':
        return {'receive': 'received', 'decline': 'declined', 'return': 'returned'}[action]
    return 'accepted' if action == 'accept' else 'declined'


def card_text(card, message_id, user_name, character_name):
    sender = _person(card['sender'], user_name, character_name)
    recipient = _person(card['recipient'], user_name, character_name)
    if card['type'] == 'transfer':
        status = TRANSFER_STATUS[card['status']]
        note = card.get('note') or '无留言'
        return (f'[VEIL虚拟转账 {message_id}] {sender} 发给 {recipient} ¥{card["amount"]}；'
                f'状态：{status}；留言：{note}。这是聊天里的虚拟互动，不是真实转账、余额或支付。')
    status = GIFT_STATUS[card['status']]
    quoted = json.dumps(card['content'], ensure_ascii=False)
    return (f'[VEIL虚拟礼物 {message_id}] {sender} 发给 {recipient} {card["icon"]} {quoted}；'
            f'状态：{status}。这是聊天里的虚拟礼物，不是真实购买、送达或物品。')


def event_text(card, message_id, action, actor, user_name, character_name):
    name = _person(actor, user_name, character_name)
    if card['type'] == 'transfer':
        verb = {'receive': '收下了', 'decline': '拒绝了', 'return': '退回了'}[action]
        note = card.get('note') or '无留言'
        return (f'[VEIL转账结果 {message_id}] {name}{verb} ¥{card["amount"]}；留言：{note}。'
                '只改变这张虚拟转账的状态，没有真实资金变动。')
    verb = '收下了' if action == 'accept' else '退回了'
    quoted = json.dumps(card['content'], ensure_ascii=False)
    return (f'[VEIL礼物结果 {message_id}] {name}{verb} {card["icon"]} {quoted}。'
            '只改变这张虚拟礼物的状态，没有真实物品。')


def event_payload(card, message_id, action, actor):
    payload = {
        'card_id': message_id,
        'type': card['type'],
        'action': action,
        'actor': actor,
    }
    for key in ('amount', 'note', 'icon', 'content'):
        if key in card:
            payload[key] = card[key]
    return payload


def card_context(events, character_name):
    """Tell Peter which cards he can act on, with the ids card_action requires."""
    lines = []
    for row in events:
        card = row.get('virtual_card')
        message_id = row.get('message_id')
        if not isinstance(card, dict) or not isinstance(message_id, str) or card.get('recipient') != 'assistant':
            continue
        if card.get('type') == 'transfer' and card.get('status') == 'pending':
            lines.append(f'- message_id={message_id} transfer ¥{card.get("amount")} from Lan, '
                         f'note: {card.get("note") or "none"}. You may card_action receive or decline.')
        elif card.get('type') == 'transfer' and card.get('status') == 'received':
            lines.append(f'- message_id={message_id} transfer ¥{card.get("amount")} already received. '
                         'You may card_action return.')
        elif card.get('type') == 'gift' and card.get('status') == 'pending':
            lines.append(f'- message_id={message_id} gift {card.get("icon") or ""} '
                         f'{card.get("content") or ""} from Lan. You may card_action accept or decline.')
    if not lines:
        return None
    return ('VEIL virtual transfers and gifts are in-chat gestures. They are not real money, purchases, '
            f'deliveries, or inventory. Cards addressed to you ({character_name}):\n'
            + '\n'.join(lines)
            + '\nUse card_action with the exact message_id. Do not only narrate the click. '
            'To send one of your own, call send_message with transfer: {"amount": number, "note": string} '
            'or gift: {"icon": string, "content": string}. A card is its own bubble.')


CARD_ACTION = {'type': 'function', 'function': {
    'name': 'card_action',
    'description': ('Receive, decline, or return a VEIL virtual transfer, or accept or decline a VEIL virtual gift, '
                    'when it is addressed to you. Pass the exact message_id from the conversation. '
                    'This changes only the in-chat card. It is not a real payment or delivery, and it does not '
                    'require Lan to approve it.'),
    'parameters': {'type': 'object', 'properties': {
        'message_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
        'action': {'type': 'string', 'enum': ['receive', 'decline', 'return', 'accept']},
    }, 'required': ['message_id', 'action'], 'additionalProperties': False},
}}


ANNOUNCE_TEXT = (
    'Capability update: you and Lan can exchange VEIL virtual transfers and gifts in chat. '
    'They are gestures, not real money, purchases, deliveries, or inventory. '
    'Send one with send_message transfer: {"amount": number, "note": string} or '
    'gift: {"icon": string, "content": string}; it is its own bubble, and any words go out as the next message. '
    'When a card is addressed to you, the conversation lists its message_id. Use card_action with that id to '
    'receive or decline a pending transfer, return one you already received, or accept or decline a gift. '
    'Do not only narrate the click. You never have to send or answer one.'
)
