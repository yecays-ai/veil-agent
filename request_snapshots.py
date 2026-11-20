"""Ticket 403: one file per turn with the exact blocks that went to the model.

The snapshot is written from the same list that is about to be sent, before dispatch. It never feeds
back into a request, and a failure here must not stop Peter from answering. Images are replaced by a
short placeholder; text is capped so a day of snapshots stays small. The newest 40 are kept.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

KEEP = 40
BLOCK_CHARS = 12000
TOTAL_CHARS = 400000
DIR = 'request-snapshots'

TITLES = {
    'identity.persona': 'Peter 的人设',
    'identity.user': '你（Lan）的设定',
    'identity.user_frame': '「关于你」的开头',
    'voice.frame': '说话样例的说明',
    'voice.samples': '说话样例',
    'memory.learned': '他记的你',
    'system.facts': '系统事实',
    'memory.experience': '最近几天的经历笔记',
    'history': '聊天历史',
    'user': '你这条消息',
    'memory.recalled': '自动想起的记忆',
    'memory.opening': '开场记忆',
    'memory.ongoing': '正在进行的事',
    'memory.short_notes': '短期便签',
    'work_checkpoint': '工作断点',
    'investigation_checkpoint': '调查断点',
    'background': '背景资料（本轮命中）',
    'recent_experiences': '上次瞄一眼',
    'extra': '本轮附加说明',
    'screen_pop': '弹幕与屏幕消息记录',
    'return_activity': '你回来期间他做了什么',
    'capability_event': '能力更新',
    'mcp_problem': 'MCP 故障说明',
    'time_ledger': '时间与花费',
    'recall_candidates': '撤回候选',
    'virtual_cards': '虚拟卡片',
}


def title_of(label):
    if label in TITLES:
        return TITLES[label]
    try:
        import prompts
        return prompts.entry(label).title
    except Exception:
        return label or '未标注'


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'text':
                parts.append(str(part.get('text') or ''))
            elif isinstance(part, dict) and part.get('type') in ('image_url', 'input_image'):
                parts.append('[图片]')
        return '\n'.join(parts)
    return str(content or '')


def _safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build(messages, labels, *, request_id, kind, model, message_ids=(), autonomy_run_id=None,
          estimated_tokens=0, cache_prefix=0, trim_note='', memory_ids=(), created_at=None):
    blocks = []
    total = 0
    for index, message in enumerate(messages):
        text = _text(message.get('content'))
        if len(text) > BLOCK_CHARS:
            text = text[:BLOCK_CHARS] + '\n…（以下省略）'
        total += len(text)
        if total > TOTAL_CHARS:
            text = text[:2000] + '\n…（以下省略）'
        label = labels[index] if index < len(labels) else ''
        blocks.append({'role': _safe(message.get('role')), 'label': _safe(label),
                       'title': title_of(label), 'chars': len(text), 'text': text})
    return {
        'version': 1,
        'request_id': str(request_id),
        'kind': _safe(kind),
        'model': _safe(model),
        'created_at': created_at or datetime.now().astimezone().isoformat(timespec='seconds'),
        'message_ids': [str(item) for item in message_ids if item],
        'autonomy_run_id': _safe(autonomy_run_id),
        'estimated_tokens': int(estimated_tokens or 0),
        'cache_prefix': int(cache_prefix or 0),
        'trim_note': _safe(trim_note or ''),
        'memory_ids': [str(item) for item in memory_ids if item],
        'blocks': blocks,
    }


def folder(root, role):
    return Path(root) / str(role) / DIR


def save(root, role, record):
    """Write one snapshot; never raises into the request path."""
    try:
        directory = folder(root, role)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (record['request_id'] + '.json')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        data = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        with temporary.open('w', encoding='utf-8', newline='\n') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _prune(directory)
        return str(path)
    except Exception:
        return ''


def _prune(directory):
    try:
        files = sorted(directory.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in files[KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def get(root, role, request_id):
    if not request_id or '/' in str(request_id) or '\\' in str(request_id):
        return None
    return _read(folder(root, role) / (str(request_id) + '.json'))


def recent(root, role, limit=12):
    rows = []
    try:
        files = sorted(folder(root, role).glob('*.json'),
                       key=lambda item: item.stat().st_mtime, reverse=True)[:max(1, limit)]
    except OSError:
        return rows
    for path in files:
        record = _read(path)
        if not record:
            continue
        rows.append({'request_id': record.get('request_id'), 'kind': record.get('kind'),
                     'model': record.get('model'), 'created_at': record.get('created_at'),
                     'estimated_tokens': record.get('estimated_tokens'),
                     'blocks': len(record.get('blocks') or [])})
    return rows


def for_message(root, role, message_id, limit=KEEP):
    if not message_id:
        return None
    try:
        files = sorted(folder(root, role).glob('*.json'),
                       key=lambda item: item.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return None
    for path in files:
        record = _read(path)
        if record and str(message_id) in (record.get('message_ids') or []):
            return record
    return None
