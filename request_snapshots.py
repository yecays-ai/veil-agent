"""Ticket 403: one file per model call with the exact blocks that went to the model.

A turn with tools makes several model requests. Every one is stored as <request_id>.<step>.json, so a
reply can be traced to the request that produced it and the earlier request plus tool results can be
inspected too. Snapshots are written before dispatch and never feed back into a request; a failure
here must not stop Peter from answering.

Text is capped per block, tool definitions are stored with the request, obvious secrets are replaced
before writing, and each record says whether anything was cut. The page shows that range instead of
claiming a byte-for-byte copy.
"""
import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

KEEP_REQUESTS = 40
BLOCK_CHARS = 12000
TOTAL_CHARS = 300000
TOOLS_CHARS = 120000
DIR = 'request-snapshots'

SECRET = re.compile(
    r'sk-[A-Za-z0-9_-]{12,}'
    r'|gh[pousr]_[A-Za-z0-9]{20,}'
    r'|github_pat_[A-Za-z0-9_]{20,}'
    r'|Bearer\s+[A-Za-z0-9._~+/=-]{8,}'
    r'|(?<![A-Za-z0-9])(?:api[_-]?key|access[_-]?token|password|passwd|secret|token|密码|密钥)'
    r'\s*[:=：]\s*\S+',
    re.I,
)

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
    'assistant': '他的上一步（工具调用）',
    'tool_result': '工具返回',
    'runtime': '运行时追加',
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


def _redact(text):
    found = 0

    def replace(match):
        nonlocal found
        found += 1
        return '[已隐藏]'
    return SECRET.sub(replace, text), found


def _tools(schemas):
    """The tool definitions as sent, with a size ceiling. Returns (tools, complete, bytes)."""
    tools = []
    for schema in schemas or []:
        function = schema.get('function') if isinstance(schema, dict) else None
        if not isinstance(function, dict):
            continue
        tools.append({'name': function.get('name'), 'description': function.get('description'),
                      'parameters': function.get('parameters')})
    size = len(json.dumps(tools, ensure_ascii=False))
    if size <= TOOLS_CHARS:
        return tools, True, size
    for item in tools:
        item['parameters'] = {'note': '参数结构过大，已省略'}
    size = len(json.dumps(tools, ensure_ascii=False))
    if size <= TOOLS_CHARS:
        return tools, False, size
    return tools[:40], False, len(json.dumps(tools[:40], ensure_ascii=False))


def build(messages, labels, *, request_id, step=1, kind, model, message_ids=(), autonomy_run_id=None,
          estimated_tokens=0, cache_prefix=0, trim_note='', memory_ids=(), tools=None,
          created_at=None):
    blocks = []
    total = 0
    redacted = 0
    truncated = 0
    for index, message in enumerate(messages):
        text = _text(message.get('content'))
        text, hidden = _redact(text)
        redacted += hidden
        was_cut = False
        if len(text) > BLOCK_CHARS:
            text = text[:BLOCK_CHARS] + '\n…（以下省略）'
            was_cut = True
        total += len(text)
        if total > TOTAL_CHARS:
            text = text[:2000] + '\n…（以下省略）'
            was_cut = True
        if was_cut:
            truncated += 1
        label = labels[index] if index < len(labels) else ''
        blocks.append({'role': _safe(message.get('role')), 'label': _safe(label),
                       'title': title_of(label), 'chars': len(text), 'text': text,
                       'truncated': was_cut})
    tool_list, tools_complete, tools_bytes = _tools(tools)
    record = {
        'version': 2,
        'request_id': str(request_id),
        'step': int(step),
        'kind': _safe(kind),
        'model': _safe(model),
        'created_at': created_at or datetime.now().astimezone().isoformat(timespec='seconds'),
        'message_ids': [str(item) for item in message_ids if item],
        'autonomy_run_id': _safe(autonomy_run_id),
        'estimated_tokens': int(estimated_tokens or 0),
        'cache_prefix': int(cache_prefix or 0),
        'trim_note': _safe(trim_note or ''),
        'memory_ids': [str(item) for item in memory_ids if item],
        'tools': tool_list,
        'tools_complete': bool(tools_complete),
        'tools_bytes': int(tools_bytes),
        'truncated_blocks': truncated,
        'redacted': redacted,
        'complete': truncated == 0 and tools_complete,
        'blocks': blocks,
    }
    return record


def folder(root, role):
    return Path(root) / str(role) / DIR


def _name(request_id, step):
    return str(request_id) + '.' + ('%02d' % int(step)) + '.json'


def save(root, role, record):
    """Write one step; never raises into the request path."""
    try:
        directory = folder(root, role)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _name(record['request_id'], record['step'])
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


def _request_of(path):
    return path.name.rsplit('.', 2)[0]


def _prune(directory):
    try:
        files = sorted(directory.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return
    keep = set()
    order = []
    for path in files:
        request_id = _request_of(path)
        if request_id not in order:
            order.append(request_id)
        if len(order) <= KEEP_REQUESTS:
            keep.add(path.name)
    for path in files:
        if path.name not in keep:
            try:
                path.unlink()
            except OSError:
                pass


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _steps(root, role, request_id):
    directory = folder(root, role)
    if not directory.is_dir() or not request_id:
        return []
    safe = str(request_id)
    if '/' in safe or '\\' in safe or '.' in safe:
        return []
    rows = []
    for path in sorted(directory.glob(safe + '.*.json')):
        record = _read(path)
        if record:
            rows.append(record)
    rows.sort(key=lambda item: item.get('step') or 0)
    return rows


def for_request(root, role, request_id):
    steps = _steps(root, role, request_id)
    return {'request_id': str(request_id), 'steps': steps} if steps else None


def get(root, role, request_id, step=None):
    steps = _steps(root, role, request_id)
    if not steps:
        return None
    if step is None:
        return steps[-1]
    return next((row for row in steps if row.get('step') == int(step)), None)


def recent(root, role, limit=12):
    try:
        files = sorted(folder(root, role).glob('*.json'),
                       key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return []
    grouped = {}
    for path in files:
        request_id = _request_of(path)
        if request_id not in grouped:
            grouped[request_id] = []
        grouped[request_id].append(path)
        if len(grouped) >= max(1, limit):
            break
    rows = []
    for request_id, paths in grouped.items():
        records = [record for record in (_read(path) for path in paths) if record]
        if not records:
            continue
        records.sort(key=lambda item: item.get('step') or 0)
        latest = records[-1]
        rows.append({'request_id': request_id, 'kind': latest.get('kind'),
                     'model': latest.get('model'), 'created_at': latest.get('created_at'),
                     'estimated_tokens': max(int(item.get('estimated_tokens') or 0) for item in records),
                     'steps': len(records), 'blocks': len(latest.get('blocks') or []),
                     'truncated_blocks': sum(int(item.get('truncated_blocks') or 0) for item in records),
                     'redacted': sum(int(item.get('redacted') or 0) for item in records),
                     'complete': all(item.get('complete') for item in records)})
    return rows


def digest(record):
    return hashlib.sha1(json.dumps(record, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:12]
