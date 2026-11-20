"""Tickets 400/401: the instructions page for one role, and saving an edit.

Read-only listing plus one write path (save). Persona, background, talking samples and his notes are
deliberately absent: each has its own page, and two editable copies of the same text is how versions
get confused.
"""
from datetime import datetime

import prompts
from prompts import registry

KIND_LABELS = {'text': '文本', 'marker': '每轮生成'}


def _row(entry):
    item = entry.as_dict()
    item['kind_label'] = KIND_LABELS.get(entry.kind, entry.kind)
    item['overridden'] = False
    item['bytes'] = 0
    if entry.kind == 'text':
        status = prompts.store().status(entry.id)
        item['text'] = status['text']
        item['bytes'] = status['bytes']
        item['overridden'] = status['overridden']
        item['source'] = 'prompts/defaults/' + entry.id + '.md'
    else:
        item['text'] = ''
        item['source'] = '程序每轮生成，没有固定原文'
    return item


def page():
    groups = []
    for group in registry.by_category():
        rows = [_row(entry) for entry in group['entries']]
        groups.append({'id': group['id'], 'title': group['title'],
                       'editable': sum(1 for row in rows if row['editable']),
                       'entries': rows})
    return {'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'categories': groups}


def save(entry_id, text):
    """Replace one entry's text whole. Returns the refreshed row."""
    prompts.store().save(entry_id, text)
    return _row(registry.get(entry_id))
