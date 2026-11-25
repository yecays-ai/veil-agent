"""Ticket 432: read-only developer diagnostics for VEIL.

Everything in this module is read-only: it never writes to Peter's chat, memory,
tool ledger, autonomy records or configuration. Every reader degrades to
"unknown" instead of raising, because a broken diagnostic page must never break
chat, autonomous activity or memory.

The one file this module may write is its own small snapshot
(`<role>/diagnostics-snapshot.json`), so the command-line report still works
when the service is not running. The snapshot holds only the numbers this page
computed; it never copies chat or memory text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Only obvious secrets. Real protection is that this module never reads chat or
# memory bodies; this is the belt-and-braces pass for text that was already logged.
SECRET = re.compile(r'sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}'
                    r'|github_pat_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,}')
# Ticket 432 review: also key=value credentials and credentials inside URLs.
CREDENTIAL = re.compile(
    r'(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|token|secret|'
    r'password|passwd|pwd)\b(\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s,;&]+)')
URL_CREDENTIAL = re.compile(r'(https?://)[^/\s:@]+:[^/\s@]+@')

# Wake ids are ISO timestamps (2026-11-23T23:36:01.806219+08:00) and call ids
# may carry uppercase letters, so the plus sign belongs to a real id.
ID_PATTERN = re.compile(r'[A-Za-z0-9_:.+\-]{1,120}\Z')

SNAPSHOT_NAME = 'diagnostics-snapshot.json'
SNAPSHOT_MAX_AGE = 300
ISSUE_LIMIT = 200
TAIL_ROWS = 4000
STATE_LABELS = {
    'running': '运行中', 'paused': '已暂停', 'error': '出错',
    'attention': '需要关注', 'unknown': '未知',
}
MODULE_LABELS = {
    'chat': '聊天服务', 'model': '模型接口', 'memory': '记忆',
    'memory_tidy': '记忆后台整理', 'autonomy': '自主活动', 'glance': '瞄一眼',
    'codex': 'Codex 观察', 'tools': '工具执行', 'mcp': 'MCP 服务',
    'long_task': '长期任务', 'budget': '后台预算', 'desktop': '桌面连接',
    'runtime': '运行安全摘要',
}
GROUP_LABELS = {
    'web': '上网', 'github': 'GitHub', 'desktop_control': '操控桌面',
    'memory_admin': '整理记忆', 'build': '干活', 'desktop_files': '桌面文件',
    'garden_forum': '花园论坛', 'garden_play': '花园游戏', 'garden_more': '花园其他',
    'nowhere': '乌有乡', 'xiaohongshu': '小红书', 'social': '社交',
    'other': '未分组的新工具',
}
MEMORY_TOOLS = {
    'hold', 'breath_search', 'trace', 'memory_read', 'memory_catalog',
    'memory_history', 'memory_timeline',
}
DENIAL_SUBCLASS = {
    'waiting': '等你批准', 'expired': '批准已过期（你没有回复）',
    'explicit': '你明确拒绝', 'rule': '权限规则或路径规则阻止',
    'unavailable': '这个能力当前不可用', 'state': '数据状态冲突（版本或内容已变）',
    'limit': '次数或时间限制', 'arguments': '工具参数写错',
    'replay': '副作用结果未确认，程序拒绝重放', 'loop': '同一调用重复过多',
}


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _now():
    return datetime.now().astimezone()


def _rows(path, limit=TAIL_ROWS):
    """Last rows of an append-only ledger; tolerant of damage."""
    path = Path(path)
    try:
        if not path.is_file():
            return []
        size = path.stat().st_size
    except OSError:
        return []
    block = 64 * 1024
    data = b''
    position = size
    newlines = 0
    try:
        with path.open('rb') as handle:
            while position > 0 and newlines <= limit and len(data) < 8 * 1024 * 1024:
                step = min(block, position)
                position -= step
                handle.seek(position)
                data = handle.read(step) + data
                newlines = data.count(b'\n')
    except OSError:
        return []
    rows = []
    for raw in data.split(b'\n'):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows[-limit:]


def _json(path, default=None):
    try:
        with Path(path).open(encoding='utf-8') as handle:
            value = json.load(handle)
    except (OSError, ValueError, UnicodeError):
        return default
    return value if isinstance(value, (dict, list)) else default


def _guard(function):
    """Never let a reader raise into the page, the ledger or the report."""
    try:
        return function()
    except Exception:
        return None


def _text(value):
    return str(value) if value is not None else ''


def valid_id(value):
    return bool(ID_PATTERN.match(_text(value)))


def _redact(value):
    """Hide obvious secrets in text that was already written to a ledger."""
    text = SECRET.sub('[已隐藏]', _text(value))
    text = URL_CREDENTIAL.sub(r'\1[已隐藏]@', text)
    return CREDENTIAL.sub(lambda match: match.group(1) + match.group(2) + '[已隐藏]', text)


def _time_text(value):
    parsed = _parse_time(value)
    return parsed.isoformat(timespec='seconds') if parsed else ''


def _role_dir(root, role):
    return Path(root) / str(role)


def chat_path(root, role):
    return _role_dir(root, role) / 'chat.jsonl'


def tools_path(root, role):
    return _role_dir(root, role) / 'tools.jsonl'


def context_path(root, role):
    return _role_dir(root, role) / 'context-log.jsonl'


def provider_path(root, role):
    return _role_dir(root, role) / 'provider-errors.jsonl'


def autonomy_path(root, role):
    return _role_dir(root, role) / 'autonomy.jsonl'


def recall_path(root, role):
    return _role_dir(root, role) / 'recall-diagnostics.jsonl'


def memory_diag_path(root, role):
    return _role_dir(root, role) / 'memory' / '_agent' / 'diagnostics.jsonl'


def experience_path(root, role):
    return _role_dir(root, role) / 'experience-memory.jsonl'


def experience_failures_path(root, role):
    return _role_dir(root, role) / 'experience-memory-failures.jsonl'


def approvals_path(root, role):
    return _role_dir(root, role) / 'pending-approvals.json'


def checkpoint_path(root, role):
    return _role_dir(root, role) / 'investigation-checkpoint.json'


def runtime_status_path(root, role):
    return _role_dir(root, role) / 'runtime-status.json'


def snapshot_path(root, role):
    return _role_dir(root, role) / SNAPSHOT_NAME


def service_error_path(root):
    return Path(root) / 'web_service_error.txt'


def mcp_config_path(root, role):
    return _role_dir(root, role) / 'mcp-services.json'


# --------------------------------------------------------------- denials

def _approvals(root, role):
    data = _json(approvals_path(root, role), {}) or {}
    items = data.get('items') if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _match_approval(approvals, row):
    """The wake id is the only exact link; time alone never decides."""
    run_id = _text(row.get('autonomy_run_id'))
    if not run_id:
        return None
    for item in approvals:
        if _text(item.get('run_id')) == run_id:
            return item
    return None


def _denial_row(detail, approval):
    text = _text(detail)
    lowered = text.casefold()

    if approval is not None:
        status = _text(approval.get('status'))
        # The approvals file only rewrites "expired" when it is next used, so the
        # deadline is checked here too: an old pending row must not read as waiting.
        expiry = _parse_time(approval.get('expires_at'))
        if status == 'pending' and expiry is not None and expiry <= _now():
            status = 'expired'
        if status == 'pending':
            return ('pending', 'waiting', '等你批准',
                    '待批准记录里同一次唤醒（run_id 相同）仍在等你', 'exact')
        if status == 'expired':
            return ('expired', 'expired', '批准已过期',
                    '待批准记录里同一次唤醒（run_id 相同）已过截止时间', 'exact')
        if status == 'denied':
            return ('denied', 'explicit', '你已拒绝',
                    '待批准记录里同一次唤醒（run_id 相同）被你拒绝', 'exact')

    if lowered.startswith('lan is not asked during your own autonomous activity'):
        return ('pending', 'waiting', '等你批准',
                '文案说明"自主活动里不会问你"，且没有找到待批准记录（启发式）', 'heuristic')
    for phrase in ('lan did not approve', 'did not approve', 'not approved', 'lan rejected'):
        if phrase in lowered:
            return ('denied', 'explicit', '你已拒绝', '文案：' + phrase, 'heuristic')
    if 'do not replay it automatically' in lowered or 'already completed, rejected, stopped' in lowered:
        return ('safety', 'replay', '安全护栏',
                '文案：副作用结果未确认时禁止重放', 'heuristic')
    if 'tool call history exceeded' in lowered:
        return ('safety', 'loop', '安全护栏', '文案：重复调用上限', 'heuristic')
    if 'data, not instructions' in lowered:
        return ('safety', 'rule', '安全护栏', '文案：数据不作为指令', 'heuristic')
    if 'first use a read-only tool' in lowered:
        return ('safety', 'rule', '安全护栏', '文案：必须先只读确认再动手', 'heuristic')
    if 'the previous side-effect operation' in lowered:
        return ('safety', 'replay', '安全护栏', '文案：上一次副作用结果未确认', 'heuristic')
    if 'isolated public browser step' in lowered:
        return ('unknown', '', '未分类',
                '这条记录的详情被活动摘要覆盖，真实原因没有保留（旧记录）', 'none')
    # State conflicts come before generic validation: "memory does not exist" is
    # about data that moved, not about arguments the model typed wrong.
    for phrase in ('does not exist', 'changed or its version', 'old_text appears',
                   'memory changed', 'version does not match', 'stale'):
        if phrase in lowered:
            return ('policy', 'state', '规则阻止', '文案：' + phrase, 'heuristic')
    for phrase in ('arguments are not a valid json object', 'invalid tool arguments',
                   'arguments are invalid', 'does not accept arguments', 'parameters are invalid',
                   '参数无效', 'nothing was sent', 'nothing was changed', 'no sticker',
                   'requires non-empty', 'must be ', 'must match', 'must contain',
                   'provide ', 'cannot be empty', 'is not a valid', 'invalid ',
                   'too long', 'too short', 'is required', 'must include',
                   'use 1-200 characters', 'no such pack',
                   'importance and pinned apply only', 'apply only to'):
        if phrase in lowered:
            return ('args', 'arguments', '参数错误', '文案：' + phrase, 'heuristic')
    for phrase in ('repository-relative path', 'without traversal', 'not allowed',
                   'permission', 'protected', 'sandbox', 'outside',
                   'may move only between'):
        if phrase in lowered:
            return ('policy', 'rule', '规则阻止', '文案：' + phrase, 'heuristic')
    for phrase in ('scheduled time must be', 'may send at', 'daily limit',
                   'too many', 'exceeded', 'preview limit', '两分钟内',
                   'character limit', 'evidence limit', 'exceeds the'):
        if phrase in lowered:
            return ('policy', 'limit', '规则阻止', '文案：' + phrase, 'heuristic')
    for phrase in ('is unavailable', 'are unavailable', 'is disabled', 'not available',
                   'no safe lab folder', 'managed through the mcp service',
                   'is not available here', 'service is not running'):
        if phrase in lowered:
            return ('policy', 'unavailable', '规则阻止', '文案：' + phrase, 'heuristic')
    # Generic shapes, not one-off wording: these are instruction-style sentences
    # that tell the model how an argument must look, or which step must come first.
    if lowered.startswith('autonomous activity is paused'):
        return ('policy', 'unavailable', '规则阻止', '文案：自主活动当前是暂停的', 'heuristic')
    if '未授权' in text or 'unauthorized' in lowered:
        return ('policy', 'rule', '规则阻止', '文案：未授权', 'heuristic')
    if 'is data only' in lowered or 'is data, not' in lowered:
        return ('safety', 'rule', '安全护栏', '文案：数据不作为指令', 'heuristic')
    if lowered.startswith('first '):
        return ('safety', 'rule', '安全护栏', '文案：必须先做前置步骤', 'heuristic')
    if lowered.startswith(('use ', 'read ', 'provide ')):
        return ('args', 'arguments', '参数错误', '文案：以 Use/Read/Provide 开头的参数要求', 'heuristic')
    if not text.strip():
        return ('unknown', '', '未分类', '这条记录没有原因文本，无法分类', 'none')
    return ('unknown', '', '未分类', '没有匹配任何已知规则', 'none')


def classify_denial(row, approvals=None):
    """Classify one tool denial; None when the row is not a denial."""
    if not isinstance(row, dict) or row.get('state') != '被拒绝':
        return None
    code, subclass, label, evidence, confidence = _denial_row(
        row.get('detail'), _match_approval(approvals or [], row))
    return {'code': code, 'subclass': subclass, 'label': label,
            'subclass_label': DENIAL_SUBCLASS.get(subclass, subclass),
            'evidence': evidence, 'confidence': confidence}


# --------------------------------------------------------------- config

def _kb(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return '0'
    return f'{number / 1024:.1f} KB' if number >= 1024 else f'{int(number)} B'


def config_findings(inventory, chat=None, mcp_errors=None):
    """Configuration checks that never make a model call."""
    findings = []
    if isinstance(inventory, dict):
        rows = inventory.get('rows') if isinstance(inventory.get('rows'), list) else []
        by_tool = {row.get('tool'): row for row in rows if isinstance(row, dict)}
        budget = inventory.get('budget') if isinstance(inventory.get('budget'), dict) else {}
        over = inventory.get('over') if isinstance(inventory.get('over'), list) else []
        for item in over:
            if not isinstance(item, dict):
                continue
            if item.get('kind') == 'always_on':
                findings.append({
                    'id': 'budget-always-on', 'level': 'warning',
                    'title': '一直带着的工具超出总预算',
                    'detail': f"{_kb(item.get('bytes'))} / 上限 {_kb(item.get('limit'))}",
                    'evidence': 'Hub 工具清单的 over 列表（每轮真实请求的组装结果）',
                    'hint': '把不常用的工具移进组；预算数字只有 Lan 同意才能改。',
                })
                continue
            group = _text(item.get('group'))
            members = [name for name, row in by_tool.items()
                       if isinstance(row, dict) and row.get('group') == group]
            size = int(item.get('bytes') or 0)
            limit = int(item.get('limit') or 0)
            findings.append({
                'id': 'budget-group-' + group, 'level': 'warning',
                'title': f"「{GROUP_LABELS.get(group, group)}」这一组工具说明超出预算",
                'detail': f"{_kb(size)} / 上限 {_kb(limit)}"
                          + (f"，超出 {_kb(size - limit)}" if size > limit else ''),
                'evidence': 'Hub 工具清单的 over 列表（每轮真实请求的组装结果）',
                'hint': (f"这一组里有 {len(members)} 个工具；可以拆组或缩短说明"
                         '（预算数字只有 Lan 同意才能改）。'),
                'group': group, 'group_label': GROUP_LABELS.get(group, group),
                'tools': sorted(members)[:20], 'bytes': size, 'limit': limit,
            })
        always_on = inventory.get('always_on_bytes')
        limit = budget.get('always_on')
        if (isinstance(always_on, int) and isinstance(limit, int) and limit > 0
                and not any(isinstance(item, dict) and item.get('kind') == 'always_on' for item in over)):
            ratio = always_on / limit
            if ratio >= 0.9:
                findings.append({
                    'id': 'budget-always-on-near', 'level': 'notice',
                    'title': '一直带着的工具已经接近总预算',
                    'detail': f'{_kb(always_on)} / 上限 {_kb(limit)}（{ratio * 100:.1f}%）',
                    'evidence': 'Hub 工具清单的 always_on_bytes 与 budget',
                    'hint': '再加常驻工具就会超；先考虑放进组。',
                })
    else:
        findings.append({
            'id': 'budget-unknown', 'level': 'unknown',
            'title': '工具预算还没算过',
            'detail': '本次启动后还没有发生过一次真实请求，工具清单尚未生成。',
            'evidence': 'Chat.tool_inventory_snapshot 为空',
            'hint': '发一句话（或让自主活动跑一次）后再看。',
        })

    cfg = getattr(chat, 'cfg', None) if chat is not None else None
    provider = getattr(cfg, 'provider', None) if cfg is not None else None
    if isinstance(provider, dict):
        env_name = provider.get('api_key_env')
        if isinstance(env_name, str) and env_name and not os.environ.get(env_name):
            findings.append({
                'id': 'model-key-missing', 'level': 'error',
                'title': '模型接口的钥匙没有配好',
                'detail': f"环境变量 {env_name} 是空的（模型 {provider.get('model')}）",
                'evidence': '进程环境变量（不读取、不显示钥匙内容）',
                'hint': '在 .env 里补上后重启服务。',
            })
        if not provider.get('model') or not provider.get('base_url'):
            findings.append({
                'id': 'model-config-invalid', 'level': 'error',
                'title': '模型配置不完整', 'detail': '缺少 model 或 base_url。',
                'evidence': 'character.toml 的 [providers.*]',
                'hint': '在设置页重新选择模型。',
            })

    if isinstance(mcp_errors, dict):
        for service_id, message in sorted(mcp_errors.items()):
            findings.append({
                'id': 'mcp-' + _text(service_id), 'level': 'error',
                'title': f'MCP 服务 {service_id} 出错',
                'detail': _text(message)[:300],
                'evidence': 'MCP 运行期错误表（只存在于本次运行，重启后丢失）',
                'hint': '到 MCP 页点停用再启用重试。',
            })
    return findings


# --------------------------------------------------------------- modules

def _module(module_id, state, evidence, reason='', last_ok=None, last_fail=None, extra=None):
    row = {'id': module_id, 'name': MODULE_LABELS.get(module_id, module_id),
           'state': state, 'state_label': STATE_LABELS.get(state, state),
           'last_ok_at': _time_text(last_ok), 'last_fail_at': _time_text(last_fail),
           'evidence': evidence, 'reason': reason}
    if extra:
        row.update(extra)
    return row


def _latest(rows, predicate, key='time'):
    found = None
    for row in rows:
        if not isinstance(row, dict) or not predicate(row):
            continue
        when = _parse_time(row.get(key))
        if when and (found is None or when > found[0]):
            found = (when, row)
    return found


def module_states(root, role, hub=None, now=None):
    now = now or _now()
    modules = []
    tools = _rows(tools_path(root, role), 1200)
    providers = _rows(provider_path(root, role), 400)
    contexts = _rows(context_path(root, role), 400)
    autonomy = _rows(autonomy_path(root, role), 100)
    memory_diag = _rows(memory_diag_path(root, role), 200)
    experience = _rows(experience_path(root, role), 80)

    ready = bool(getattr(hub, 'ready', False)) if hub is not None else None
    stopped = bool(getattr(hub, 'stopped', False)) if hub is not None else None
    started = getattr(hub, 'started_at', None) if hub is not None else None
    if hub is None:
        modules.append(_module('chat', 'unknown', '命令行模式：没有运行中的服务可读'))
    elif stopped:
        modules.append(_module('chat', 'paused', 'Hub.stopped 为真'))
    elif ready:
        modules.append(_module('chat', 'running', 'Hub.ready 为真',
                               extra={'started_at': _time_text(started)}))
    else:
        modules.append(_module('chat', 'attention', 'Hub 已启动但还没进入等待输入状态',
                               extra={'started_at': _time_text(started)}))

    last_ok = _latest(contexts, lambda row: bool(row.get('sent')) and
                      row.get('usage_status') not in ('preflight', 'untrusted'))
    last_fail = _latest(providers, lambda row: True)
    if last_fail and (last_ok is None or last_fail[0] > last_ok[0]):
        modules.append(_module('model', 'error', 'provider-errors.jsonl 的最新一条晚于最近一次成功',
                               reason=(_text(last_fail[1].get('status')) or
                                       _text(last_fail[1].get('type')))[:80],
                               last_ok=last_ok[0] if last_ok else None, last_fail=last_fail[0]))
    elif last_ok:
        modules.append(_module('model', 'running', 'context-log.jsonl 有已发送并计量的请求',
                               last_ok=last_ok[0]))
    else:
        modules.append(_module('model', 'unknown', '没有可用的请求记录'))

    memory_ok = _latest(tools, lambda row: row.get('tool') in MEMORY_TOOLS and row.get('state') == '成功')
    memory_fail = _latest(memory_diag, lambda row: _text(row.get('code')).startswith('retrieval') or
                          row.get('code') in ('memory_tool_failed', 'provider_input_context_limit'))
    if memory_fail and (memory_ok is None or memory_fail[0] > memory_ok[0]):
        modules.append(_module('memory', 'error',
                               'memory/_agent/diagnostics.jsonl 的最新失败晚于最近一次成功',
                               reason=(f"{memory_fail[1].get('code')} · "
                                       f"{_text(memory_fail[1].get('stage'))} · "
                                       f"{_text(memory_fail[1].get('error_type'))}")[:120],
                               last_ok=memory_ok[0] if memory_ok else None, last_fail=memory_fail[0]))
    elif memory_ok:
        modules.append(_module('memory', 'running', 'tools.jsonl 里有成功的记忆工具调用',
                               last_ok=memory_ok[0]))
    else:
        modules.append(_module('memory', 'unknown', '没有记忆调用记录'))

    tidy = _latest(experience, lambda row: True)
    if tidy and tidy[1].get('kind') == 'error':
        modules.append(_module('memory_tidy', 'error', 'experience-memory.jsonl 最新一条是 error',
                               reason=_text(tidy[1].get('reason'))[:120], last_fail=tidy[0]))
    elif tidy and tidy[1].get('kind') == 'checkpoint':
        modules.append(_module('memory_tidy', 'running',
                               'experience-memory.jsonl 最新一条是 checkpoint', last_ok=tidy[0]))
    elif tidy and tidy[1].get('kind') == 'budget':
        modules.append(_module('memory_tidy', 'paused', '今天的整理预算已用完', last_fail=tidy[0]))
    else:
        modules.append(_module('memory_tidy', 'unknown', '没有整理记录'))

    autonomy_status = _guard(lambda: hub.autonomy.status() if getattr(hub, 'autonomy', None) else {}) or {}
    enabled = _guard(lambda: hub.activity.enabled()) if getattr(hub, 'activity', None) else None
    last_run = autonomy[-1] if autonomy else None
    next_at = _parse_time(autonomy_status.get('next_wake_at'))
    overdue = bool(next_at and now > next_at + timedelta(minutes=10))
    if enabled is False:
        modules.append(_module('autonomy', 'paused', '自主活动开关是关的',
                               last_ok=last_run.get('finished_at') if last_run else None))
    elif overdue:
        modules.append(_module('autonomy', 'attention',
                               '下次唤醒时间已经过了，但没有新的活动记录（推断，未经确认）',
                               reason=f"下次唤醒 {next_at.isoformat(timespec='minutes')}，没有新记录",
                               last_ok=last_run.get('finished_at') if last_run else None))
    elif last_run:
        modules.append(_module('autonomy', 'running', 'autonomy.jsonl 有活动记录',
                               reason=f"上次 {last_run.get('result')}："
                                      f"{_text(last_run.get('result_reason'))[:80]}",
                               last_ok=last_run.get('finished_at'),
                               extra={'next_wake_at': _time_text(next_at)}))
    else:
        modules.append(_module('autonomy', 'unknown', '没有活动记录',
                               extra={'next_wake_at': _time_text(next_at)}))

    glance_status = _guard(lambda: hub.glance.status() if getattr(hub, 'glance', None) else {}) or {}
    glance_rows = [row for row in tools if _text(row.get('autonomy_run_id')).startswith('glance:')]
    if glance_status.get('glance_enabled') is False:
        modules.append(_module('glance', 'paused', '瞄一眼开关是关的'))
    elif glance_rows:
        modules.append(_module('glance', 'running', 'tools.jsonl 有瞄一眼调用',
                               last_ok=glance_rows[-1].get('time')))
    else:
        modules.append(_module('glance', 'unknown', '没有瞄一眼记录'))

    codex_status = _guard(lambda: hub.codex_watch.status() if getattr(hub, 'codex_watch', None) else {}) or {}
    codex_on = codex_status.get('codex_watch_enabled')
    modules.append(_module('codex', 'paused' if codex_on is False else
                           'running' if codex_on else 'unknown',
                           '来自 Codex 观察控制器' if codex_on is not None else '没有状态'))

    tool_fail = _latest(tools, lambda row: row.get('state') in ('出错', '失败'))
    tool_ok = _latest(tools, lambda row: row.get('state') == '成功')
    if tool_fail and (tool_ok is None or tool_fail[0] > tool_ok[0]):
        modules.append(_module('tools', 'error', 'tools.jsonl 的最新失败晚于最近一次成功',
                               reason=f"{tool_fail[1].get('tool')}："
                                      f"{_text(tool_fail[1].get('detail'))[:80]}",
                               last_ok=tool_ok[0] if tool_ok else None, last_fail=tool_fail[0]))
    elif tool_ok:
        modules.append(_module('tools', 'running', 'tools.jsonl 有成功的调用', last_ok=tool_ok[0]))
    else:
        modules.append(_module('tools', 'unknown', '没有工具调用记录'))

    mcp_config = _json(mcp_config_path(root, role), {}) or {}
    services = mcp_config.get('services') if isinstance(mcp_config, dict) else None
    configured = [item for item in services
                  if isinstance(item, dict) and item.get('enabled')] if isinstance(services, list) else []
    mcp_errors = {}
    if hub is not None:
        mcp_errors = _guard(lambda: dict(getattr(hub.chat.mcp_services, 'errors', {}))) or {}
    if configured and mcp_errors:
        modules.append(_module('mcp', 'error', 'MCP 运行期错误表里有记录（只在本次运行内）',
                               reason='；'.join(f'{key}: {_text(value)[:60]}'
                                               for key, value in list(mcp_errors.items())[:3])))
    elif configured:
        modules.append(_module('mcp', 'running',
                               f'配置了 {len(configured)} 个启用的服务；没有运行期错误',
                               extra={'services': [item.get('id') for item in configured][:10]}))
    elif mcp_config:
        modules.append(_module('mcp', 'paused', 'MCP 配置里没有启用的服务'))
    else:
        modules.append(_module('mcp', 'unknown', '没有 MCP 配置'))

    task = _json(_role_dir(root, role) / 'long-task.json', None)
    if isinstance(task, dict) and task.get('status') and task.get('status') != 'completed':
        modules.append(_module('long_task', 'attention', 'long-task.json 里还有未完成的任务',
                               reason=f"{task.get('status')}：{_text(task.get('title'))[:80]}"))
    elif isinstance(task, dict):
        modules.append(_module('long_task', 'running', '长期任务已完成或空闲',
                               last_ok=task.get('updated_at')))
    else:
        modules.append(_module('long_task', 'unknown', '没有长期任务记录'))

    budget_row = None
    daily = None
    if hub is not None and getattr(hub, 'chat', None) is not None:
        budget_row = _guard(lambda: hub.chat.budget.category_totals(hub.chat.cfg.id))
        cfg = getattr(hub.chat, 'cfg', None)
        daily = (getattr(cfg, 'autonomy', None) or {}).get('daily_budget_cny') if cfg is not None else None
    if isinstance(budget_row, tuple) and len(budget_row) == 2:
        spent, pending = budget_row
        modules.append(_module('budget', 'running', '来自预算账本的分类合计',
                               reason=f'今天自主活动 ¥{spent}（预留 ¥{pending}）'
                                      + (f' / 上限 ¥{daily}' if daily else '')))
    else:
        modules.append(_module('budget', 'unknown', '命令行模式：没有预算合计'))

    modules.append(_module('desktop', 'unknown', '只有本页能判断；服务端不记录桌面连接'))
    return modules


# --------------------------------------------------------------- issues

def _issue_id(*parts):
    raw = '|'.join(_text(part) for part in parts)
    return hashlib.sha1(raw.encode('utf-8', 'ignore')).hexdigest()[:12]


def _issue(time_value, module, kind, severity, title, detail='', request_id='', run_id='',
           call_id='', confirmed=True, extra=None, stable_key=''):
    detail = _redact(detail)[:1200]
    request_id, run_id, call_id = _text(request_id), _text(run_id), _text(call_id)
    # Ticket 432 review: a row with no structured id can only mean "happened
    # nearby", never "this exact turn"; the caller cannot override that.
    if not (request_id or run_id or call_id):
        confirmed = False
    # Some sources (MCP) have no timestamp of their own; hashing "now" would give
    # a new id on every refresh, so a copied record could not be looked up again.
    row_id = (hashlib.sha1(('stable|' + _text(stable_key)).encode('utf-8', 'ignore')).hexdigest()[:12]
              if stable_key else _issue_id(time_value, module, kind, title, request_id, run_id,
                                           call_id, detail[:120]))
    row = {'id': row_id,
           'time': _time_text(time_value), 'module': module,
           'module_label': MODULE_LABELS.get(module, module), 'kind': kind,
           'severity': severity, 'title': title, 'detail': detail,
           'request_id': request_id, 'run_id': run_id,
           'call_id': call_id, 'confirmed': bool(confirmed), 'recovery': 'unknown'}
    if extra:
        row.update(extra)
    return row


def collect_issues(root, role, hours=72, mcp_errors=None, now=None, limit=ISSUE_LIMIT):
    now = now or _now()
    try:
        hours = max(1, min(int(hours or 72), 24 * 30))
    except (TypeError, ValueError):
        hours = 72
    cutoff = now - timedelta(hours=hours)
    approvals = _approvals(root, role)
    issues = []

    for row in _rows(provider_path(root, role), 400):
        when = _parse_time(row.get('time'))
        if when is None or when < cutoff:
            continue
        request_id = _text(row.get('request_id'))
        run_id = _text(row.get('run_id'))
        status = row.get('status')
        title = ('接口返回 HTTP ' + str(status)) if isinstance(status, int) else _text(row.get('type') or '请求失败')
        if row.get('input_context_limit'):
            title += '（命中模型输入上限）'
        issues.append(_issue(when, 'model', 'model_error', 'error', title,
                             detail=row.get('detail'), request_id=request_id, run_id=run_id,
                             confirmed=bool(request_id or run_id),
                             extra={'model': row.get('model'), 'source': _text(row.get('kind')) or 'unknown',
                                    'note': '' if (request_id or run_id) else
                                            '旧记录没有关联 id，无法确认属于哪一轮'}))

    for row in _rows(tools_path(root, role), TAIL_ROWS):
        when = _parse_time(row.get('time'))
        if when is None or when < cutoff:
            continue
        state = row.get('state')
        request_id = _text(row.get('request_id'))
        run_id = _text(row.get('autonomy_run_id'))
        call_id = _text(row.get('call_id'))
        if state in ('出错', '失败'):
            issues.append(_issue(when, 'tools', 'tool_error', 'error',
                                 f"{row.get('tool')} 执行失败", detail=row.get('detail'),
                                 request_id=request_id, run_id=run_id, call_id=call_id))
        elif state == '被拒绝':
            denial = classify_denial(row, approvals)
            severity = {'pending': 'notice', 'denied': 'info', 'expired': 'info',
                        'unknown': 'notice'}.get(
                denial['code'], 'info')
            issues.append(_issue(when, 'tools', 'denial', severity,
                                 f"{row.get('tool')} 被拒绝：{denial['label']}",
                                 detail=row.get('detail'), request_id=request_id,
                                 run_id=run_id, call_id=call_id, extra={'denial': denial}))

    for row in _rows(autonomy_path(root, role), 200):
        when = _parse_time(row.get('finished_at') or row.get('started_at'))
        if when is None or when < cutoff:
            continue
        result = row.get('result')
        if result not in ('失败', '部分完成', '搁置'):
            continue
        severity = 'error' if result == '失败' else ('notice' if result == '部分完成' else 'info')
        issues.append(_issue(when, 'autonomy', 'autonomy_' + _text(result), severity,
                             f'自主活动{result}', detail=row.get('result_reason'),
                             run_id=_text(row.get('started_at')),
                             extra={'tools': (row.get('tools') or [])[:8]}))

    for row in _rows(memory_diag_path(root, role), 300):
        when = _parse_time(row.get('time'))
        if when is None or when < cutoff:
            continue
        code = _text(row.get('code'))
        if code == 'retrieval_failed':
            issues.append(_issue(when, 'memory', 'memory_retrieval', 'error',
                                 f"记忆检索在 {row.get('stage')} 阶段失败",
                                 detail=row.get('error_type'), confirmed=False))
        elif code == 'memory_tool_failed':
            issues.append(_issue(when, 'memory', 'memory_tool', 'error',
                                 f"记忆工具 {row.get('tool')} 失败",
                                 detail=row.get('error_type'), confirmed=False))
        elif code == 'provider_input_context_limit':
            issues.append(_issue(when, 'memory', 'memory_limit', 'notice',
                                 '模型输入超过上限（记忆侧记录）',
                                 detail=row.get('model'), confirmed=False))

    for row in _rows(experience_failures_path(root, role), 40):
        when = _parse_time(row.get('time'))
        if when is None or when < cutoff:
            continue
        # The failure file can hold the model's reply, the rejected draft and the
        # memory title. Only the technical labels below are allowed into a report.
        safe = ' · '.join(_text(row.get(key)) for key in
                          ('stage', 'problem', 'finish_reason', 'model') if row.get(key))
        issues.append(_issue(when, 'memory_tidy', 'experience_failed', 'error',
                             '后台记忆整理失败', detail=safe, confirmed=False))

    status = _json(runtime_status_path(root, role), {}) or {}
    if isinstance(status, dict) and status.get('state') in ('attention', 'paused'):
        when = _parse_time(status.get('time'))
        if when and when >= cutoff:
            issues.append(_issue(when, 'runtime', 'runtime_attention',
                                 'error' if status.get('state') == 'paused' else 'notice',
                                 f"运行安全摘要：{status.get('module')} {status.get('code')}",
                                 detail=status.get('action'), confirmed=False,
                                 extra={'classification': status.get('classification')}))

    checkpoint = _json(checkpoint_path(root, role), {}) or {}
    for item in (checkpoint.get('evidence') or []) if isinstance(checkpoint, dict) else []:
        if not isinstance(item, dict) or item.get('state') != '结果未知':
            continue
        when = _parse_time(item.get('time'))
        if when is None or when < cutoff:
            continue
        issues.append(_issue(when, 'tools', 'unknown_side_effect', 'attention',
                             f"{item.get('tool')} 的结果没有确认",
                             detail='副作用可能已经发生；程序已阻止自动重试。', confirmed=False,
                             extra={'note': '需要先核对目标，再决定是否重试'}))

    crash = _guard(lambda: service_error_path(root).read_text(encoding='utf-8').strip())
    if crash:
        when = _guard(lambda: datetime.fromtimestamp(
            service_error_path(root).stat().st_mtime).astimezone())
        if when and when >= cutoff:
            issues.append(_issue(when, 'chat', 'crash', 'error', '服务上一次异常退出',
                                 detail=crash[:600], confirmed=False,
                                 extra={'note': '只有一行摘要；完整 traceback 没有保存'}))

    if isinstance(mcp_errors, dict):
        for service_id, message in mcp_errors.items():
            # MCP errors carry no timestamp of their own. The refresh time is not
            # when the error happened, so it is kept separate and never shown as
            # the event time.
            issues.append(_issue('', 'mcp', 'mcp_error', 'error',
                                 f'MCP 服务 {service_id} 出错', detail=message, confirmed=False,
                                 stable_key=f'mcp|{service_id}|{_text(message)}',
                                 extra={'note': '只在本次运行内有效，重启后这条就没有了',
                                        'observed_at': now.isoformat(timespec='seconds')}))

    # Rows without their own time (MCP) still belong at the top: sort by when we
    # observed them, while the row keeps saying "time unknown".
    issues.sort(key=lambda row: row.get('time') or row.get('observed_at') or '', reverse=True)
    return issues[:limit]


def _recovery(issue, contexts, tools):
    """Only a later success on the same id counts as recovered; time is not enough."""
    when = _parse_time(issue.get('time'))
    if when is None:
        return 'unknown'
    request_id = issue.get('request_id')
    run_id = issue.get('run_id')
    if issue.get('module') == 'model' and request_id:
        for row in contexts:
            if _text(row.get('request_id')) != request_id:
                continue
            row_time = _parse_time(row.get('time'))
            if row_time and row_time > when and row.get('sent') and not row.get('error'):
                return 'confirmed'
    if issue.get('module') == 'autonomy' and run_id:
        for row in tools:
            if _text(row.get('autonomy_run_id')) != run_id:
                continue
            row_time = _parse_time(row.get('time'))
            if row_time and row_time > when and row.get('state') == '成功':
                return 'confirmed'
    return 'unknown'


def recent_issues(root, role, hours=72, mcp_errors=None, limit=ISSUE_LIMIT):
    """One timeline with the recovery verdict already attached."""
    rows = collect_issues(root, role, hours=hours, mcp_errors=mcp_errors, limit=limit)
    contexts = _rows(context_path(root, role), 200)
    tools = _rows(tools_path(root, role), 600)
    for row in rows:
        row['recovery'] = _recovery(row, contexts, tools)
    return rows


# ----------------------------------------------------------------- trace

def trace(root, role, request_id='', run_id='', call_id=''):
    request_id, run_id, call_id = _text(request_id), _text(run_id), _text(call_id)
    if not (request_id or run_id or call_id):
        return None
    chats = _rows(chat_path(root, role), 2000)
    tools = _rows(tools_path(root, role), TAIL_ROWS)
    contexts = _rows(context_path(root, role), 400)
    recalls = _rows(recall_path(root, role), 400)
    autonomy = _rows(autonomy_path(root, role), 200)

    if call_id:
        matched = next((row for row in reversed(tools)
                        if _text(row.get('call_id')) == call_id), None)
        if matched:
            request_id = request_id or _text(matched.get('request_id'))
            run_id = run_id or _text(matched.get('autonomy_run_id'))

    request_ids = []

    def remember(value):
        value = _text(value)
        if value and value not in request_ids:
            request_ids.append(value)

    if request_id:
        remember(request_id)
    if run_id:
        # A wake can make several requests (one per tool step); collect all of them
        # so the chain is the whole wake, not just its last step.
        for row in tools:
            if _text(row.get('autonomy_run_id')) == run_id:
                remember(row.get('request_id'))
        for row in contexts:
            if _text(row.get('autonomy_run_id')) == run_id:
                remember(row.get('request_id'))
    if not request_id and request_ids:
        request_id = request_ids[-1]

    wake = None
    if run_id:
        wake = next((row for row in autonomy if _text(row.get('started_at')) == run_id), None)

    chain = {'request_id': request_id, 'request_ids': request_ids, 'run_id': run_id,
             'call_id': call_id, 'confirmed': True, 'note': '',
             'messages': [], 'context': [], 'recall': None, 'tools': [],
             'model_calls': [], 'autonomy': None}
    if not request_ids and not call_id and wake is None:
        chain.update({'confirmed': False,
                      'note': '这段记录没有可用的关联 id，无法确认属于哪一轮'})
        return chain
    if run_id and not request_ids and wake is None:
        chain.update({'confirmed': False,
                      'note': '这次唤醒在所有账本里都没有记录（可能没有发出请求）'})
        return chain

    ids = set(request_ids)
    # A bare call id is not a turn: show only that call, never every ledger row
    # whose (missing) run id happens to equal the empty string.
    only_call = bool(call_id) and not ids and not run_id
    # Never attach chat messages when only a wake id is known: that would mix in
    # unrelated turns that merely happened nearby.
    if ids:
        for row in chats:
            if _text(row.get('request_id')) not in ids:
                continue
            # Deliberately not a "role/content" dict: this module must never look
            # like it builds a model request (tests/test_prompt_registry.py scans
            # every root module for that shape).
            chain['messages'].append({'time': _time_text(row.get('time')),
                                      'user': row.get('role') == 'user',
                                      'chars': len(_text(row.get('content'))),
                                      'message_id': row.get('message_id') or ''})
    for row in contexts:
        if only_call:
            continue
        if ids:
            if _text(row.get('request_id')) not in ids:
                continue
        elif _text(row.get('autonomy_run_id')) != run_id:
            continue
        chain['context'].append({key: row.get(key) for key in
                                 ('time', 'kind', 'step', 'sent', 'usage_status',
                                  'estimated_input_tokens', 'actual_input_tokens',
                                  'completion_tokens', 'latency_seconds', 'finish_reason',
                                  'history_rounds')})
        chain['context'][-1]['trim_note'] = _redact(row.get('trim_note'))
    for row in recalls:
        if ids and _text(row.get('request_id')) in ids:
            chain['recall'] = {key: row.get(key) for key in
                               ('ombre', 'hidden', 'judge', 'dropped', 'injected',
                                'budget_dropped', 'failure', 'query_chars')}
            break
    for row in tools:
        if only_call:
            if _text(row.get('call_id')) != call_id:
                continue
        elif ids:
            if _text(row.get('request_id')) not in ids:
                continue
        elif _text(row.get('autonomy_run_id')) != run_id:
            continue
        chain['tools'].append({'time': _time_text(row.get('time')), 'tool': row.get('tool'),
                               'state': row.get('state'), 'call_id': row.get('call_id') or '',
                               'detail': _redact(row.get('detail'))[:400],
                               'arguments_hash': row.get('arguments_hash') or '',
                               'result_hash': row.get('result_hash') or ''})
    if wake is not None:
        chain['autonomy'] = {key: wake.get(key) for key in
                             ('started_at', 'finished_at', 'decision', 'reason', 'result',
                              'result_reason', 'current_focus', 'next_step', 'cost_cny')}
        for key in ('decision', 'reason', 'result_reason', 'current_focus', 'next_step'):
            chain['autonomy'][key] = _redact(chain['autonomy'].get(key))
    chain['model_calls'] = [row for row in chain['context']
                            if row.get('kind') in ('tool_step_context', 'chat', 'autonomy',
                                                   'glance', 'qq', 'codex')]
    return chain


def snapshots(root, role, request_id, limit=8):
    if not request_id:
        return []
    folder = _role_dir(root, role) / 'request-snapshots'
    out = []
    try:
        for path in sorted(folder.glob(str(request_id) + '.*.json'))[:limit]:
            record = _json(path, {}) or {}
            out.append({'step': record.get('step'), 'kind': record.get('kind'),
                        'model': record.get('model'), 'created_at': record.get('created_at'),
                        'estimated_tokens': record.get('estimated_tokens'),
                        'blocks': len(record.get('blocks') or []),
                        'complete': record.get('complete')})
    except OSError:
        return []
    return out


# -------------------------------------------------------------- overview

def _branch(git_dir):
    try:
        head = (Path(git_dir) / 'HEAD').read_text(encoding='utf-8').strip()
        return head.split('refs/heads/', 1)[1] if 'refs/heads/' in head else 'detached'
    except (OSError, ValueError):
        return ''


def _remote_commit(git_dir):
    git_dir = Path(git_dir)
    loose = git_dir / 'refs' / 'remotes' / 'origin' / 'main'
    try:
        return loose.read_text(encoding='utf-8').strip()[:12]
    except (OSError, ValueError):
        pass
    try:
        for line in (git_dir / 'packed-refs').read_text(encoding='utf-8').splitlines():
            if line.endswith('refs/remotes/origin/main'):
                return line.split(' ', 1)[0][:12]
    except (OSError, ValueError):
        pass
    return ''


def _memory_bytes():
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {'total': int(status.ullTotalPhys), 'free': int(status.ullAvailPhys),
                    'percent': int(status.dwMemoryLoad)}
    except Exception:
        pass
    try:
        return {'load1': round(os.getloadavg()[0], 2)}
    except (AttributeError, OSError):
        return {}


_DIR_CACHE = {}


def _dir_size(root, limit=4000):
    """(bytes, complete) for the data folder, cached for five minutes.

    Ticket 432 review: stopping at the scan limit must be visible. A partial sum
    shown as the total would understate how much disk the agent uses.
    """
    key = str(root)
    cached = _DIR_CACHE.get(key)
    if cached and time.time() - cached[0] < 300:
        return cached[1], cached[2]
    total = 0
    seen = 0
    complete = True
    try:
        for base, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += (Path(base) / name).stat().st_size
                except OSError:
                    continue
                seen += 1
                if seen >= limit:
                    complete = False
                    break
            if not complete:
                break
    except OSError:
        complete = False
    _DIR_CACHE[key] = (time.time(), total, complete)
    return total, complete


def service_info(root, role, hub=None):
    git_dir = Path(__file__).resolve().parent / '.git'
    head = ''
    try:
        head = (git_dir / 'HEAD').read_text(encoding='utf-8').strip()
        if head.startswith('ref:'):
            ref_path = git_dir / head.split(' ', 1)[1].strip()
            head = ref_path.read_text(encoding='utf-8').strip() if ref_path.is_file() else ''
        head = head[:12]
    except (OSError, ValueError):
        head = ''
    data_bytes, data_complete = _dir_size(root)
    news = {'commit': head, 'branch': _branch(git_dir), 'remote_commit': _remote_commit(git_dir),
            'python': platform.python_version(),
            'platform': f'{platform.system()} {platform.release()} {platform.machine()}',
            'cpu_count': os.cpu_count() or 0, 'memory': _memory_bytes(),
            'data_bytes': data_bytes, 'data_bytes_complete': data_complete, 'character': role,
            'error_file': _guard(lambda: service_error_path(root).read_text(encoding='utf-8').strip()[:400]) or ''}
    if hub is not None:
        started = getattr(hub, 'started_at', None)
        news['started_at'] = _time_text(started)
        news['uptime_seconds'] = int(time.time() - started) if isinstance(started, (int, float)) else None
        news['ready'] = bool(getattr(hub, 'ready', False))
        news['stopped'] = bool(getattr(hub, 'stopped', False))
        cfg = getattr(getattr(hub, 'chat', None), 'cfg', None)
        provider = getattr(cfg, 'provider', None) if cfg is not None else None
        if isinstance(provider, dict):
            news['model'] = provider.get('label') or provider.get('model') or ''
            news['model_host'] = provider.get('base_url') or ''
        news['name'] = getattr(cfg, 'name', '') if cfg is not None else ''
    return news


def overview(root, role, hub=None, inventory=None, hours=72):
    mcp_errors = {}
    if hub is not None:
        mcp_errors = _guard(lambda: dict(getattr(hub.chat.mcp_services, 'errors', {}))) or {}
    config = config_findings(inventory, chat=getattr(hub, 'chat', None),
                             mcp_errors=mcp_errors) if hub is not None else []
    issues = recent_issues(root, role, hours=hours, mcp_errors=mcp_errors)
    denials = {}
    for issue in issues:
        if issue.get('kind') == 'denial' and issue.get('denial'):
            key = issue['denial']['code']
            denials[key] = denials.get(key, 0) + 1
    attention = [issue for issue in issues
                 if issue.get('severity') in ('error', 'attention') and issue.get('recovery') != 'confirmed']
    try:
        hours = max(1, min(int(hours or 72), 24 * 30))
    except (TypeError, ValueError):
        hours = 72
    return {'generated_at': _now().isoformat(timespec='seconds'), 'character': role,
            'service': service_info(root, role, hub),
            'modules': module_states(root, role, hub=hub),
            'config': config, 'issues': issues[:60],
            'counts': {'issues': len(issues), 'attention': len(attention), 'denials': denials,
                       'errors': sum(1 for issue in issues if issue.get('severity') == 'error')},
            'attention': attention[:10], 'hours': hours}


def links(root, role, cfg=None):
    """Where maintenance lives. Memory owns its own page; this only links to it."""
    memory_cfg = getattr(cfg, 'memory', None) if cfg is not None else None
    url = ''
    if isinstance(memory_cfg, dict):
        url = _text(memory_cfg.get('maintenance_url'))
    usable = url.startswith(('http://127.0.0.1', 'http://localhost'))
    return [
        {'id': 'memory', 'name': '记忆维护（VEIL Brain）',
         'state': 'ready' if usable else 'not_ready', 'url': url if usable else '',
         'hint': '' if usable else '记忆组提供维护页后自动出现在这里；现在可以用 '
                                  'veil_brain.py inspect --db <brain.db> --id <记忆 id> 查。'},
        {'id': 'mcp', 'name': 'MCP 服务', 'state': 'page', 'page': 'mcp',
         'hint': '启停服务、看重试状态。'},
        {'id': 'data_repair', 'name': '数据修复诊断', 'state': 'command',
         'hint': 'python data_repair.py diagnose --file <账本文件>（只读）'},
        {'id': 'update', 'name': '更新代码', 'state': 'command',
         'hint': 'update-main.cmd（诊断页不会自己更新或重启）'},
    ]


# --------------------------------------------------------------- report

def _gb(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return '?'
    return f'{number / 1024 ** 3:.2f} GB'


def _duration(seconds):
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return '?'
    hours, rest = divmod(seconds, 3600)
    return f'{hours} 小时 {rest // 60} 分' if hours else f'{rest // 60} 分'


def _report_header(service, now):
    memory = service.get('memory') or {}
    mem = (f"{memory.get('percent')}%（可用 {_gb(memory.get('free'))} / 共 {_gb(memory.get('total'))}）"
           if memory.get('percent') is not None else '未知')
    uptime = service.get('uptime_seconds')
    state = '已停止' if service.get('stopped') else '运行中' if service.get('ready') else '启动中'
    crash = (service.get('error_file') or '').splitlines()
    lines = [
        'VEIL 诊断报告',
        '=' * 32,
        f"时间：{now.isoformat(timespec='seconds')}",
        # Old offline snapshots only have "role"; keep them readable.
        f"角色：{service.get('character') or service.get('role') or '?'}"
        f"　｜　代码：{service.get('commit') or '未知'}"
        f"（{service.get('branch') or '?'}）",
        f"服务：{state}" + (f"　｜　已运行 {_duration(uptime)}" if uptime else ''),
        f"系统：{service.get('platform') or '?'}　｜　{service.get('cpu_count') or '?'} 核　｜　内存 {mem}",
        f"数据：{_gb(service.get('data_bytes'))}"
        + ('（至少；只统计到扫描上限）' if service.get('data_bytes_complete') is False else '')
        + f"　｜　模型：{service.get('model') or '未配置'}",
        f"上一次崩溃：{crash[0][:160] if crash else '无记录'}",
    ]
    return '\n'.join(lines)


def _module_line(module):
    parts = [module.get('name', '?'), module.get('state_label', '')]
    if module.get('last_ok_at'):
        parts.append('正常 ' + module['last_ok_at'].replace('T', ' ')[:19])
    if module.get('last_fail_at'):
        parts.append('失败 ' + module['last_fail_at'].replace('T', ' ')[:19])
    if module.get('reason'):
        parts.append(_text(module['reason'])[:120])
    parts.append('依据：' + _text(module.get('evidence'))[:80])
    return '  ｜  '.join(part for part in parts if part)


def _issue_line(issue):
    when = _text(issue.get('time')).replace('T', ' ')[:19]
    if not when:
        observed = _text(issue.get('observed_at')).replace('T', ' ')[:19]
        when = ('时间未知 · 本次运行内 ' + observed[11:] + ' 看到') if observed else '时间未知'
    badge = ('未确认关联' if not issue.get('confirmed')
             else '已恢复' if issue.get('recovery') == 'confirmed' else '恢复未知')
    ids = ' '.join(part for part in (
        ('request=' + issue['request_id']) if issue.get('request_id') else '',
        ('run=' + issue['run_id']) if issue.get('run_id') else '',
        ('call=' + issue['call_id']) if issue.get('call_id') else '') if part)
    line = f"[{when}] {issue.get('module_label')} · {issue.get('title')} · {badge}"
    if ids:
        line += ' · ' + ids
    detail = ' '.join(_text(issue.get('detail')).split())
    if detail:
        line += '\n      ' + detail[:300]
    return line


def report_text(root, role, hub=None, inventory=None, hours=72, issue_id=''):
    """Plain text for the clipboard: no chat text, no memory text, no API key."""
    now = _now()
    if hub is not None:
        data = overview(root, role, hub=hub, inventory=inventory, hours=hours)
        service = data['service']
    else:
        snapshot = _json(snapshot_path(root, role), {})
        data = snapshot if isinstance(snapshot, dict) else {}
        # The command line is also used when the service died: never trust an old
        # snapshot for "what went wrong", always re-read the ledgers for issues.
        data['issues'] = recent_issues(root, role, hours=hours)
        if not data.get('modules'):
            data['modules'] = module_states(root, role)
        if not data.get('config'):
            data['config'] = [{'id': 'config-unknown', 'level': 'unknown',
                               'title': '配置校验需要服务运行',
                               'detail': '命令行模式下读不到工具清单和模型配置。',
                               'evidence': '没有运行中的服务', 'hint': '服务起来后打开诊断页，或在服务内生成报告。'}]
        service = data.get('service') if isinstance(data.get('service'), dict) else service_info(root, role)

    blocks = [_report_header(service, now)]
    if hub is None and data.get('generated_at'):
        blocks.append(f"（模块与配置来自服务运行时的最后一次快照：{data['generated_at']}）")

    if issue_id:
        issue = next((item for item in data.get('issues') or []
                      if isinstance(item, dict) and item.get('id') == issue_id), None)
        if issue:
            blocks.append('\n[问题详情]\n  ' + _issue_line(issue))
            denial = issue.get('denial')
            if isinstance(denial, dict):
                blocks.append('  分类：' + _text(denial.get('label')) + ' / '
                              + _text(denial.get('subclass_label')) + '（'
                              + _text(denial.get('confidence')) + '）　证据：'
                              + _text(denial.get('evidence')))

    blocks.append('\n[1] 版本与系统\n' + '\n'.join([
        f"  本地提交：{service.get('commit') or '未知'}（{service.get('branch') or '?'}）",
        f"  远端提交（上次已知）：{service.get('remote_commit') or '未知'}",
        f"  上一次崩溃：{(service.get('error_file') or '无记录')[:160]}",
    ]))

    modules = data.get('modules') or []
    blocks.append('\n[2] 模块状态\n' + ('\n'.join('  - ' + _module_line(module) for module in modules)
                                       if modules else '  无记录'))

    config = data.get('config') or []
    blocks.append('\n[3] 配置校验异常\n' + ('\n'.join(
        f"  [{item.get('level')}] {item.get('title')}：{item.get('detail')}"
        f"（{item.get('evidence')}）" for item in config if isinstance(item, dict))
        if config else '  没有发现配置问题（或还没算过，见 [2] 的未知项）'))

    issues = [item for item in (data.get('issues') or []) if isinstance(item, dict)]
    background = [item for item in issues if item.get('module') in
                  ('autonomy', 'memory_tidy', 'mcp', 'memory') and item.get('kind') != 'denial']
    blocks.append('\n[4] 后台活动失败\n' + ('\n'.join('  ' + _issue_line(item)
                                                  for item in background[:12])
                                          if background else f'  最近 {hours} 小时没有记录'))

    denials = [item for item in issues if item.get('kind') == 'denial']
    if denials:
        counts = {}
        for item in denials:
            key = (item.get('denial') or {}).get('label') or '未分类'
            counts[key] = counts.get(key, 0) + 1
        summary = '　'.join(f'{key} {value}' for key, value
                            in sorted(counts.items(), key=lambda kv: -kv[1]))
        unknown = [item for item in denials
                   if (item.get('denial') or {}).get('code') == 'unknown'][:3]
        blocks.append('\n[5] 授权与拒绝分类\n  ' + summary +
                      ('\n  未分类样例：\n' + '\n'.join('    ' + _issue_line(item)
                                                     for item in unknown) if unknown else ''))
    else:
        blocks.append(f'\n[5] 授权与拒绝分类\n  最近 {hours} 小时没有被拒绝的记录')

    shown = {id(item) for item in background} | {id(item) for item in denials}
    others = [item for item in issues if id(item) not in shown][:20]
    blocks.append('\n[6] 最近异常明细\n' + ('\n'.join('  ' + _issue_line(item) for item in others)
                                          if others else '  没有记录'))

    cfg = getattr(getattr(hub, 'chat', None), 'cfg', None) if hub is not None else None
    blocks.append('\n[7] 维护入口\n' + '\n'.join(
        f"  {item['name']}：{item.get('url') or item.get('hint') or ''}"
        for item in links(root, role, cfg)))

    blocks.append('\n—— 这段可直接发给维护 agent；不含聊天正文、记忆正文和 API Key ——')
    return _redact('\n'.join(blocks))


def write_snapshot(root, role, data):
    try:
        path = snapshot_path(root, role)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def snapshot_if_stale(root, role, data):
    try:
        path = snapshot_path(root, role)
        if path.is_file() and time.time() - path.stat().st_mtime < SNAPSHOT_MAX_AGE:
            return False
    except OSError:
        pass
    return write_snapshot(root, role, data)


def main(argv=None):
    parser = argparse.ArgumentParser(description='VEIL 诊断报告（只读，可在服务停止后运行）')
    parser.add_argument('--data-root', default='data')
    parser.add_argument('--role', default='peter')
    parser.add_argument('--hours', type=int, default=72)
    parser.add_argument('--issue', default='', help='只输出这一条问题的详情')
    parser.add_argument('--json', action='store_true', help='输出 JSON 而不是文本')
    args = parser.parse_args(argv)
    if args.json:
        snapshot = _json(snapshot_path(args.data_root, args.role), {})
        data = snapshot if isinstance(snapshot, dict) else {}
        data['issues'] = collect_issues(args.data_root, args.role, hours=args.hours)
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    print(report_text(args.data_root, args.role, hours=args.hours, issue_id=args.issue))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
