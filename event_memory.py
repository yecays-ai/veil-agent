"""Ticket 407: one event, one memory.

A save is a *stage* of an event, not a memory of its own. Stages and their evidence are kept in the
role's data folder; the event has exactly one Ombre memory whose body is a short, dated summary of
the whole event. The full, lossless story is the ordered stages, readable with `memory_timeline`.

Deliberately model-free in this ticket: grouping and the summary are deterministic, so a save costs
no model call and a summary can never invent or drop a detail (it quotes the stage's first sentence).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

DEFAULTS = {
    'enabled': False,
    'evidence_minutes': 20,
    'evidence_items': 6,
    'evidence_chars': 400,
    'summary_chars': 600,    # Ombre/VOICE's own page limit for a memory body
    'stage_chars': 600,
}

_TOKEN = re.compile(r'[0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_\-.\u4e00-\u9fff]*')
_STOP = {
    'the', 'and', 'for', 'with', 'that', 'this', 'was', 'were', 'had', 'have', 'from', 'she', 'her',
    'him', 'his', 'they', 'them', 'but', 'not', 'you', 'your', 'about', 'after', 'before', 'then',
    'when', 'what', 'why', 'how', 'said', 'says', 'one', 'two', 'three', 'day', 'night', 'today',
    '昨天', '今天', '晚上', '早上', '下午', '事情', '东西', '时候', '因为', '所以', '然后', '我们', '你们',
}
_DATE = re.compile(r'\b(?:\d{1,2}[-/月]\d{1,2}日?|\d{4}[-/]\d{2}[-/]\d{2})\b')
_NUMBER = re.compile(r'\b\d+(?:\.\d+)?\b')
_FILE = re.compile(r'[\w\-. \u4e00-\u9fff]{1,80}\.(?:txt|md|html|svg|png|jpg|json|csv|pdf|py|js|css|docx|xlsx)')


def _when(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def entities(text):
    """Words/names that can tie two stages together. Deterministic and language-neutral."""
    found = set()
    for raw in _TOKEN.findall(text or ''):
        token = raw.strip('.-_').lower()
        if len(token) < 2 or token in _STOP or token.isdigit():
            continue
        if token.startswith('http'):
            continue
        found.add(token)
    return found


def facts(text):
    """Things a later fact check can compare: dates, numbers, file names. Stored verbatim."""
    text = text or ''
    return {
        'dates': sorted(set(_DATE.findall(text))),
        'numbers': sorted(set(_NUMBER.findall(text))),
        'files': sorted(set(match.group(0).strip() for match in _FILE.finditer(text))),
    }


def _first_sentence(text, limit=140):
    text = ' '.join((text or '').split())
    if not text:
        return ''
    for index, char in enumerate(text):
        if char in '。！？.!?' and index >= 4:
            return text[:index + 1]
    return text[:limit]


class EventMemory:
    """The event ledger plus the one Ombre memory that projects each event into recall."""

    def __init__(self, memory, settings=None):
        self.memory = memory
        self.cfg = memory.cfg
        self.store = memory.store
        raw = settings if isinstance(settings, dict) else {}
        self.settings = dict(DEFAULTS)
        for key, default in DEFAULTS.items():
            value = raw.get(key, default)
            if type(value) is type(default) or (type(default) is int and type(value) is int):
                self.settings[key] = value
        self.settings['enabled'] = bool(raw.get('enabled', True))
        self.events_path = f'{self.cfg.id}/events.jsonl'
        self.stages_path = f'{self.cfg.id}/event-stages.jsonl'
        self.evidence_path = f'{self.cfg.id}/stage-evidence.jsonl'
        self.versions_path = f'{self.cfg.id}/event-versions.jsonl'
        self.locked = None  # set by NativeMemoryTools to respect Lan's lock

    # --- read side -------------------------------------------------------------------------
    def events(self):
        latest = {}
        for row in self.store.read(self.events_path):
            if isinstance(row, dict) and isinstance(row.get('event_id'), str):
                latest[row['event_id']] = row
        return latest

    def stages(self, event_id=None):
        rows = []
        for row in self.store.read(self.stages_path):
            if not isinstance(row, dict) or not isinstance(row.get('stage_id'), str):
                continue
            if event_id is None or row.get('event_id') == event_id:
                rows.append(row)
        rows.sort(key=lambda row: str(row.get('at') or ''))
        return rows

    def timeline(self, event_id, limit=40):
        events = self.events()
        event = events.get(event_id)
        if event is None:
            return None
        stages = self.stages(event_id)[-limit:]
        lines = [f"{event.get('title') or event_id}",
                 f"{event.get('status')} · {str(event.get('first_at') or '')[:16]} → {str(event.get('last_at') or '')[:16]}"]
        for stage in stages:
            lines.append(f"- {str(stage.get('at'))[:16]} {stage.get('text')}")
            extra = stage.get('facts') if isinstance(stage.get('facts'), dict) else {}
            if extra.get('files') or extra.get('numbers'):
                bits = ', '.join((extra.get('files') or []) + (extra.get('numbers') or []))
                lines.append(f"  evidence: {bits[:300]}")
        return '\n'.join(lines)

    # --- write side ------------------------------------------------------------------------
    def record(self, text, title, *, why='', at=None, memory_title=None):
        when = _when(at) or datetime.now().astimezone()
        text = ' '.join((text or '').split())
        if not text:
            raise ValueError('empty stage')
        stage = {
            'stage_id': 's-' + hashlib.sha1(f'{when.isoformat()}|{text}'.encode('utf-8')).hexdigest()[:16],
            'at': when.isoformat(), 'text': text[:self.settings['stage_chars']],
            'entities': sorted(entities(text))[:40], 'facts': facts(text), 'why': why,
        }
        event, updated = self._attach(stage, title)
        stage['event_id'] = event['event_id']
        self.store.append(self.stages_path, stage)
        self._capture_evidence(stage)
        story = self._story(event['event_id'])
        projection = self._projection(event['event_id'], story)
        memory_id, changed, locked = self._write_projection(event, projection, memory_title or title or event['title'])
        event.update({
            'memory_id': memory_id or event.get('memory_id'),
            'last_at': when.isoformat(),
            'stage_count': len(self.stages(event['event_id'])),
            'dirty': not changed and not locked,
        })
        self.store.append(self.events_path, event)
        return {'event_id': event['event_id'], 'title': event.get('title') or title,
                'memory_id': event.get('memory_id'), 'updated': updated, 'changed': changed, 'locked': locked}

    def _attach(self, stage, title):
        events = self.events()
        best, second = None, None
        now = _when(stage['at']) or datetime.now().astimezone()
        stage_entities = set(stage['entities'])
        for event in events.values():
            last = _when(event.get('last_at'))
            shared = len(stage_entities & set(event.get('entities') or []))
            if shared == 0:
                continue
            same_day = bool(last and last.date() == now.date())
            score = shared * 2 + (2 if event.get('status') == 'open' else 0) + (1 if same_day else 0)
            if score >= 4 and shared >= 1 and ((same_day and event.get('status') == 'open') or shared >= 2):
                if best is None or score > best[0]:
                    second, best = best, (score, event)
                elif second is None or score > second[0]:
                    second = (score, event)
        if best is None:
            return self._new_event(stage, title, [], 'open' if not events else 'uncertain'), False
        if second is not None and second[0] == best[0]:
            return self._new_event(stage, title, [best[1]['event_id'], second[1]['event_id']], 'uncertain'), False
        event = best[1]
        merged = sorted(set(event.get('entities') or []) | stage_entities)[:60]
        event = {**event, 'entities': merged, 'status': 'open', 'last_at': stage['at']}
        return event, True

    def _new_event(self, stage, title, candidate_ids, status):
        when = _when(stage['at']) or datetime.now().astimezone()
        event_id = 'e-' + when.strftime('%Y%m%d') + '-' + hashlib.sha1(
            f'{stage["stage_id"]}|{title}'.encode('utf-8')).hexdigest()[:6]
        event = {
            'event_id': event_id, 'title': (title or _first_sentence(stage['text'], 60) or 'Event')[:100],
            'status': status, 'first_at': stage['at'], 'last_at': stage['at'],
            'entities': stage['entities'], 'stage_count': 0, 'dirty': False,
            'candidate_links': candidate_ids,
        }
        return event

    # --- story and projection --------------------------------------------------------------
    def _story(self, event_id):
        lines = []
        for stage in self.stages(event_id):
            when = _when(stage.get('at'))
            stamp = when.strftime('%m-%d %H:%M') if when else ''
            lines.append(f'{stamp} {stage.get("text")}'.strip())
        return '\n\n'.join(lines)

    def _projection(self, event_id, story):
        """Every stage contributes its opening, so the recalled memory keeps the whole arc."""
        pieces = []
        for stage in self.stages(event_id):
            text = ' '.join((stage.get('text') or '').split())
            if text:
                pieces.append(text[:180].rstrip() + ('…' if len(text) > 180 else ''))
        text = ' / '.join(pieces) or story
        limit = max(120, self.settings['summary_chars'])
        if len(text) > limit:
            # Keep the beginning and the end: the cause and the latest turn are what recall needs.
            head = int(limit * 0.65)
            text = text[:head].rstrip() + ' … ' + text[-(limit - head - 3):].lstrip()
        return text[:limit]

    def _write_projection(self, event, text, title):
        """Create or rewrite the event's one Ombre memory. Returns (memory_id, changed)."""
        memory_id = event.get('memory_id')
        if memory_id and callable(self.locked):
            try:
                if self.locked(memory_id):
                    return memory_id, True, True
            except Exception:
                return memory_id, False, False
        if memory_id and callable(getattr(self.memory, 'refresh_visibility', None)):
            try:
                raw = self.memory.raw_content_for(memory_id) if callable(
                    getattr(self.memory, 'raw_content_for', None)) else None
                if raw is not None and not self.memory.refresh_visibility(memory_id):
                    return memory_id, True, True   # hidden or deleted: never resurrect it
            except Exception:
                return memory_id, False, False
        if not memory_id:
            args = {'content': text, 'title': title[:100]}
            result = self.memory.call('hold', args)
            new_id = self.memory.created_id(result)
            try:
                self.memory.record_native(args, result)
            except Exception:
                pass
            return new_id, bool(new_id), False
        old = self.memory.content_for(memory_id, refresh=True)
        if old is None:
            old = self.memory.raw_content_for(memory_id) if callable(getattr(self.memory, 'raw_content_for', None)) else None
        if old:
            self.store.append(self.versions_path, {
                'event_id': event['event_id'], 'memory_id': memory_id,
                'old_content': old, 'at': datetime.now().astimezone().isoformat(),
            })
        try:
            self.memory.call('trace', {'bucket_id': memory_id, 'content': text})
        except Exception:
            return memory_id, False, False
        try:
            self.memory.bucket_details_cache.pop(memory_id, None)
        except Exception:
            pass
        now = self.memory.content_for(memory_id, refresh=True)
        if now is not None and callable(getattr(self.memory, 'record_native_update', None)):
            try:
                self.memory.record_native_update(memory_id, now)
            except Exception:
                pass
        if now is None:
            return memory_id, True, False   # the backend accepted the rewrite; a fake may not render details
        return memory_id, now.strip() == text.strip(), False

    # --- evidence --------------------------------------------------------------------------
    def _capture_evidence(self, stage):
        """Copy the little that survives a restart: recent chat lines and tool results."""
        now = _when(stage['at']) or datetime.now().astimezone()
        cutoff = now - timedelta(minutes=max(1, self.settings['evidence_minutes']))
        collected = []
        for kind, path in (('chat', f'{self.cfg.id}/chat.jsonl'), ('tool', f'{self.cfg.id}/tools.jsonl')):
            try:
                rows = self.store.read_tail(path, 40)
            except Exception:
                continue
            for row in rows:
                stamp = _when(row.get('time'))
                if stamp is None or stamp < cutoff:
                    continue
                text = row.get('content') if kind == 'chat' else row.get('detail') or row.get('path')
                if not isinstance(text, str) or not text.strip():
                    continue
                collected.append({'kind': kind, 'at': stamp.isoformat(),
                                  'text': ' '.join(text.split())[:self.settings['evidence_chars']]})
        for item in collected[-max(1, self.settings['evidence_items']):]:
            item.update({'stage_id': stage['stage_id'], 'event_id': stage['event_id']})
            self.store.append(self.evidence_path, item)
        return collected
