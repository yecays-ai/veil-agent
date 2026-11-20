"""Ticket 400: the instructions page payload for one role.

Read-only. Every row comes from prompts.registry; the text is either the live entry text, the local
file the entry points at, or a short explanation for a runtime block. Nothing here changes a request.
"""
from datetime import datetime
from pathlib import Path

import prompts
from prompts import registry

KIND_LABELS = {'text': '文本', 'file': '文件', 'marker': '每轮生成'}
READ_LIMIT = 60000


def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8-sig')[:READ_LIMIT]
    except (OSError, TypeError):
        return ''


def _file_target(entry, cfg, profile=None):
    persona_path = getattr(cfg, 'persona_path', None)
    if entry.id == 'identity.persona':
        return persona_path
    if entry.id == 'identity.user':
        settings = getattr(cfg, 'user_persona', None) or {}
        if persona_path and settings.get('file'):
            return Path(persona_path).parent / settings['file']
        return None
    if entry.id == 'background.library':
        return getattr(cfg, 'background', None)
    if entry.id == 'voice.samples':
        return getattr(cfg, 'voice_style', None)
    if entry.id == 'memory.notes':
        return getattr(profile, 'path', None)
    return None


def _row(entry, cfg, profile):
    item = entry.as_dict()
    item['kind_label'] = KIND_LABELS.get(entry.kind, entry.kind)
    item['overridden'] = False
    item['source'] = ''
    if entry.kind == 'text':
        status = prompts.store().status(entry.id)
        item['text'] = status['text']
        item['bytes'] = status['bytes']
        item['overridden'] = status['overridden']
        item['source'] = 'prompts/defaults/' + entry.id + '.md'
    elif entry.kind == 'file':
        path = _file_target(entry, cfg, profile)
        item['text'] = _read(path)
        item['bytes'] = len(item['text'].encode('utf-8'))
        item['source'] = str(path) if path else '这个角色没有这个文件'
    else:
        item['text'] = ''
        item['bytes'] = 0
        item['source'] = '程序每轮生成，没有固定原文'
    return item


def page(cfg, profile=None):
    groups = []
    for group in registry.by_category():
        rows = [_row(entry, cfg, profile) for entry in group['entries']]
        groups.append({'id': group['id'], 'title': group['title'],
                       'editable': sum(1 for row in rows if row['editable'] and row['kind'] == 'text'),
                       'entries': rows})
    return {'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'categories': groups}
