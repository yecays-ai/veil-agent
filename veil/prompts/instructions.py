"""Tickets 400-402: the instructions page for one role, and saving an edit.

Two kinds of rows: registered text entries (prompts/registry.py) and tool descriptions, which are
managed as a class by prompts/tools.py so a new tool appears without registration. Persona,
background, talking samples and his notes are deliberately absent: each has its own page, and two
editable copies of the same text is how versions get confused.
"""
from datetime import datetime

from veil import prompts
from veil.prompts import registry

KIND_LABELS = {'text': '文本', 'marker': '每轮生成'}
TOOL_ID_PREFIX = 'tool.'


def _row(entry):
    item = entry.as_dict()
    item['kind_label'] = KIND_LABELS.get(entry.kind, entry.kind)
    item['overridden'] = False
    item['default_changed'] = False
    item['bytes'] = 0
    if entry.kind == 'text':
        status = prompts.store().status(entry.id)
        item['text'] = status['text']
        item['bytes'] = status['bytes']
        item['overridden'] = status['overridden']
        item['default_changed'] = status.get('default_changed', False)
        item['source'] = 'veil/prompts/defaults/' + entry.id + '.md'
    else:
        item['text'] = ''
        item['source'] = '程序每轮生成，没有固定原文'
    return item


def _tool_rows(chat):
    """One row per tool this role offers. Keyed by the canonical tool name, titled as he sees it."""
    rows = []
    try:
        schemas = chat.tool_schemas(full=True)
    except Exception:
        return rows
    overrides = prompts.tool_overrides()
    for schema in schemas:
        function = schema.get('function') if isinstance(schema, dict) else None
        if not isinstance(function, dict):
            continue
        label = function.get('name')
        canonical = chat.canonical_tool_name(label) if hasattr(chat, 'canonical_tool_name') else label
        if not isinstance(canonical, str) or not canonical:
            continue
        if canonical in registry.TEXT_TOOL_IDS:
            continue  # already listed as its own text entry
        key = label if isinstance(label, str) and label else canonical
        description = overrides.get(key) or function.get('description') or ''
        overridden = key in overrides
        rows.append({
            'id': TOOL_ID_PREFIX + key,
            'category': 'tools',
            'title': label if label != canonical else canonical,
            'kind': 'text',
            'kind_label': '工具说明',
            'site': '工具定义里；' + ('每轮都带' if canonical in _always_on() else '打开对应组时带'),
            'editable': True,
            'overridden': overridden,
            'default_changed': False,
            'note': '保存后，下一次请求里的工具说明就是这一版。',
            'text': description,
            'bytes': len(description.encode('utf-8')),
            'source': '工具定义（代码；可用 persona/instructions/' + TOOL_ID_PREFIX + key + '.md 覆盖）',
        })
    rows.sort(key=lambda row: row['title'].lower())
    return rows


def _always_on():
    try:
        from veil.tools.tool_groups import ALWAYS_ON
        return ALWAYS_ON
    except Exception:
        return frozenset()


def page(chat=None):
    groups = []
    for group in registry.by_category():
        rows = [_row(entry) for entry in group['entries']]
        if group['id'] == 'tools' and chat is not None:
            rows = rows + _tool_rows(chat)
        groups.append({'id': group['id'], 'title': group['title'],
                       'editable': sum(1 for row in rows if row['editable']),
                       'entries': rows})
    rows = [row for group in groups for row in group['entries']]
    return {'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'maintenance': {'overridden': sum(1 for row in rows if row.get('overridden')),
                            'default_changed': sum(1 for row in rows if row.get('default_changed')),
                            'entries': len(rows)},
            'categories': groups}


def save(entry_id, text):
    """Replace one entry's text whole. Tool rows go to the per-tool override, everything else to the registry."""
    if isinstance(entry_id, str) and entry_id.startswith(TOOL_ID_PREFIX):
        name = entry_id[len(TOOL_ID_PREFIX):]
        prompts.save_tool(name, text)
        return None
    prompts.store().save(entry_id, text)
    return _row(registry.get(entry_id))
