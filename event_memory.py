"""Ticket 407: one event, one memory.

A save is a *stage* of an event, not a memory of its own. Stages and their evidence are kept in the
role's data folder; the event has exactly one Ombre memory whose body is a short, dated summary of
the whole event. The full story is persisted on the event and readable with `memory_timeline`.

Grouping is deliberately conservative: memory search, shared names/objects, the same day, an open
event, and a bounded look at the raw chat/autonomy/tool ledgers. Anything uncertain becomes a
provisional event (still recallable) and goes to the review ledger; it is never forced into an
unrelated event.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

DEFAULTS = {
    'enabled': False,
    'evidence_minutes': 30,
    'evidence_items': 8,
    'evidence_chars': 400,
    'summary_chars': 400,
    'stage_chars': 600,
    'investigate_rows': 400,
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
    found = set()
    for raw in _TOKEN.findall(text or ''):
        token = raw.strip('.-_').lower()
        if len(token) < 2 or token in _STOP or token.isdigit() or token.startswith('http'):
            continue
        found.add(token)
    return found


def facts(text):
    text = text or ''
    return {'dates': sorted(set(_DATE.findall(text))),
            'numbers': sorted(set(_NUMBER.findall(text))),
            'files': sorted(set(match.group(0).strip() for match in _FILE.finditer(text)))}


def _first_sentence(text, limit=140):
    text = ' '.join((text or '').split())
    if not text:
        return ''
    for index, char in enumerate(text):
        if char in '。！？.!?' and index >= 4:
            return text[:index + 1]
    return text[:limit]


def _ids_from_search(text):
    """Ombre's breath_search answer carries ids in a result-ids block or [bucket_id:...] markers."""
    found = []
    match = re.search(r'=== ombre:result-ids ===\s*```json\s*(\{.*?\})\s*```', text or '', re.S)
    if match:
        try:
            payload = json.loads(match.group(1))
            found += [item for item in payload.get('bucket_ids') or [] if isinstance(item, str)]
        except ValueError:
            pass
    found += re.findall(r'\[bucket_id:([\w-]+)\]', text or '')
    return list(dict.fromkeys(found))


class EventMemory:
    def __init__(self, memory, settings=None, model=None):
        self.memory = memory
        self.cfg = memory.cfg
        self.store = memory.store
        self.model = model if callable(model) else None
        raw = settings if isinstance(settings, dict) else {}
        self.settings = dict(DEFAULTS)
        for key, default in DEFAULTS.items():
            value = raw.get(key, default)
            if type(value) is type(default):
                self.settings[key] = value
        self.settings['enabled'] = bool(raw.get('enabled', True))
        self.events_path = f'{self.cfg.id}/events.jsonl'
        self.stages_path = f'{self.cfg.id}/event-stages.jsonl'
        self.evidence_path = f'{self.cfg.id}/stage-evidence.jsonl'
        self.versions_path = f'{self.cfg.id}/event-versions.jsonl'
        self.review_path = f'{self.cfg.id}/event-review.jsonl'
        self.ops_path = f'{self.cfg.id}/event-ops.jsonl'
        self.locked = None  # set by NativeMemoryTools to respect Lan's lock

    # --- read side -------------------------------------------------------------------------
    def events(self):
        latest = {}
        for row in self.store.read(self.events_path):
            if isinstance(row, dict) and isinstance(row.get('event_id'), str):
                latest[row['event_id']] = row
        return latest

    def event_for_memory(self, memory_id):
        for event in self.events().values():
            if event.get('memory_id') == memory_id:
                return event['event_id']
        return None

    def event_map(self):
        return {event['memory_id']: event['event_id'] for event in self.events().values()
                if isinstance(event.get('memory_id'), str)}

    def stages(self, event_id=None):
        rows = [row for row in self.store.read(self.stages_path)
                if isinstance(row, dict) and isinstance(row.get('stage_id'), str)
                and (event_id is None or row.get('event_id') == event_id)]
        rows.sort(key=lambda row: str(row.get('at') or ''))
        return rows

    def _visible(self, event):
        memory_id = event.get('memory_id')
        if not memory_id or not callable(getattr(self.memory, 'refresh_visibility', None)):
            return True
        raw = self.memory.raw_content_for(memory_id) if callable(
            getattr(self.memory, 'raw_content_for', None)) else None
        if raw is None:
            return True   # a test/fake backend does not render files; nothing proves it hidden
        return bool(self.memory.refresh_visibility(memory_id))

    def visible_events(self):
        rows = [event for event in self.events().values() if event.get('status') != 'merged' and self._visible(event)]
        rows.sort(key=lambda event: str(event.get('last_at') or ''), reverse=True)
        return rows

    def detail(self, event_id):
        event = self.events().get(event_id)
        if event is None or not self._visible(event):
            return None
        stages = self.stages(event_id)
        evidence = {}
        try:
            rows = self.store.read(self.evidence_path)
        except Exception:
            rows = []
        for row in rows:
            if isinstance(row, dict) and row.get('stage_id'):
                evidence.setdefault(row['stage_id'], []).append({
                    'kind': row.get('kind'), 'at': row.get('at'), 'text': row.get('text')})
        versions = []
        try:
            versions = [row for row in self.store.read(self.versions_path)
                        if isinstance(row, dict) and row.get('event_id') == event_id][-20:]
        except Exception:
            pass
        return {
            'event_id': event_id, 'title': event.get('title'), 'status': event.get('status'),
            'first_at': event.get('first_at'), 'last_at': event.get('last_at'),
            'story': event.get('story') or self._story(event_id),
            'confidence': event.get('confidence'),
            'stages': [{**stage, 'evidence': evidence.get(stage.get('stage_id'), [])} for stage in stages],
            'versions': [{'at': row.get('at'), 'old_content': row.get('old_content')} for row in versions],
        }

    def timeline(self, event_id, limit=60):
        event = self.events().get(event_id)
        if event is None or not self._visible(event):
            return None
        stages = self.stages(event_id)[-limit:]
        story = event.get('story') or self._story(event_id)
        lines = [f"{event.get('title') or event_id}",
                 f"{event.get('status')} · {str(event.get('first_at') or '')[:16]} → {str(event.get('last_at') or '')[:16]}",
                 '', story, '', '— stages —']
        for stage in stages:
            lines.append(f"- {str(stage.get('at'))[:16]} {stage.get('text')}")
            if stage.get('interpretation'):
                lines.append(f"  his reading: {stage['interpretation']}")
            extra = stage.get('facts') if isinstance(stage.get('facts'), dict) else {}
            if extra.get('files') or extra.get('numbers'):
                bits = ', '.join((extra.get('files') or []) + (extra.get('numbers') or []))
                lines.append(f"  evidence: {bits[:300]}")
        return '\n'.join(lines)

    # --- write side ------------------------------------------------------------------------
    def record(self, text, title, *, why='', at=None, importance=None, pinned=None, interpretation=''):
        when = _when(at) or datetime.now().astimezone()
        text = ' '.join((text or '').split())
        if not text:
            raise ValueError('empty stage')
        stage = {
            'stage_id': 's-' + hashlib.sha1(f'{when.isoformat()}|{text}'.encode('utf-8')).hexdigest()[:16],
            'at': when.isoformat(), 'text': text[:self.settings['stage_chars']],
            'entities': sorted(entities(text))[:40], 'facts': facts(text), 'why': why,
        }
        if interpretation:
            stage['interpretation'] = ' '.join(interpretation.split())[:300]
        event, updated, confidence = self._attach(stage, title)
        stage['event_id'] = event['event_id']
        self.store.append(self.stages_path, stage)
        self._capture_evidence(stage)
        story = self._story(event['event_id'])
        projection = self._projection(event['event_id'], story)
        changed, locked = False, False
        try:
            memory_id, changed, locked = self._write_projection(
                event, projection, title or event['title'], importance=importance, pinned=pinned)
        except Exception:
            memory_id = event.get('memory_id')
        event.update({
            'memory_id': memory_id or event.get('memory_id'),
            'last_at': when.isoformat(),
            'stage_count': len(self.stages(event['event_id'])),
            'story': story,
            'story_source': 'composed',
            'confidence': confidence,
            'dirty': not changed and not locked,
        })
        if event.get('status') == 'uncertain':
            self._review(event['event_id'], 'low confidence grouping; candidate links kept')
        self.store.append(self.events_path, event)
        return {'event_id': event['event_id'], 'title': event.get('title') or title,
                'memory_id': event.get('memory_id'), 'updated': updated, 'changed': changed,
                'locked': locked, 'confidence': confidence}

    def _candidate_scores(self, stage, title):
        scores = {}
        now = _when(stage['at']) or datetime.now().astimezone()
        stage_entities = set(stage['entities'])

        def add(event_id, points, reason):
            if points:
                row = scores.setdefault(event_id, {'score': 0.0, 'reasons': []})
                row['score'] += points
                row['reasons'].append(reason)

        # 1. semantic-ish: Ombre's own search, mapped back to the event owning the hit.
        ids = []
        try:
            answer = self.memory.call('breath_search', {'query': f'{title}\n{stage["text"]}'[:1200],
                                                       'mode': 'automatic', 'with_ids': True, 'max_results': 8})
            ids = _ids_from_search(answer)
        except Exception:
            ids = []
        owners = self.event_map()
        for memory_id in ids:
            if memory_id in owners:
                add(owners[memory_id], 4, 'memory search hit')

        # 2. names/objects, same day, open event.
        for event in self.events().values():
            shared = len(stage_entities & set(event.get('entities') or []))
            last = _when(event.get('last_at'))
            if shared:
                add(event['event_id'], min(shared, 3) * 2, 'shared names/objects')
            if last and last.date() == now.date():
                add(event['event_id'], 1, 'same day')
            if event.get('status') == 'open':
                add(event['event_id'], 1, 'open')

        # 3. raw ledgers: look for these names in chat/autonomy/tool rows near the stage.
        if max([row['score'] for row in scores.values()] or [0]) < 4:
            for event in self.events().values():
                if self._raw_mentions(event, stage_entities, now):
                    add(event['event_id'], 3, 'raw ledger mention')
        return scores

    def _raw_mentions(self, event, stage_entities, now):
        names = set(event.get('entities') or []) & stage_entities
        if len(names) < 2:
            return False
        cutoff = now - timedelta(days=3)
        for path in (f'{self.cfg.id}/chat.jsonl', f'{self.cfg.id}/autonomy.jsonl',
                     f'{self.cfg.id}/tools.jsonl'):
            try:
                rows = self.store.read_tail(path, self.settings['investigate_rows'])
            except Exception:
                continue
            for row in rows:
                stamp = _when(row.get('time'))
                if stamp is None or stamp < cutoff:
                    continue
                text = ' '.join(str(row.get(key) or '') for key in ('content', 'detail', 'path', 'text'))
                tokens = entities(text)
                if len(names & tokens) >= 2:
                    return True
        return False

    def _attach(self, stage, title):
        scores = self._candidate_scores(stage, title)
        if not scores:
            return self._new_event(stage, title, [], 'open'), False, 0.4
        ranked = sorted(scores.items(), key=lambda item: item[1]['score'], reverse=True)
        best_id, best = ranked[0]
        events = self.events()
        if best['score'] < 4 or (len(ranked) > 1 and ranked[1][1]['score'] == best['score']):
            links = [event_id for event_id, _ in ranked[:3]]
            return self._new_event(stage, title, links, 'uncertain'), False, round(min(0.9, best['score'] / 8), 2)
        event = events[best_id]
        merged = sorted(set(event.get('entities') or []) | set(stage['entities']))[:60]
        event = {**event, 'entities': merged, 'status': 'open', 'last_at': stage['at']}
        return event, True, round(min(1.0, best['score'] / 8), 2)

    def _new_event(self, stage, title, candidate_ids, status):
        when = _when(stage['at']) or datetime.now().astimezone()
        event_id = 'e-' + when.strftime('%Y%m%d') + '-' + hashlib.sha1(
            f'{stage["stage_id"]}|{title}'.encode('utf-8')).hexdigest()[:6]
        return {
            'event_id': event_id, 'title': (title or _first_sentence(stage['text'], 60) or 'Event')[:100],
            'status': status, 'first_at': stage['at'], 'last_at': stage['at'],
            'entities': stage['entities'], 'stage_count': 0, 'dirty': False,
            'candidate_links': candidate_ids,
        }

    # --- story and projection --------------------------------------------------------------
    def _story(self, event_id):
        lines = []
        for stage in self.stages(event_id):
            when = _when(stage.get('at'))
            stamp = when.strftime('%m-%d %H:%M') if when else ''
            line = f'{stamp} {stage.get("text")}'.strip()
            if stage.get('interpretation'):
                line += f'（他当时理解：{stage["interpretation"]}）'
            lines.append(line)
        return '\n\n'.join(lines)

    def _projection(self, event_id, story):
        pieces = []
        for stage in self.stages(event_id):
            text = ' '.join((stage.get('text') or '').split())
            if text:
                pieces.append(text[:180].rstrip() + ('…' if len(text) > 180 else ''))
        text = ' / '.join(pieces) or story
        limit = max(120, self.settings['summary_chars'])
        if len(text) > limit:
            head = int(limit * 0.65)
            text = text[:head].rstrip() + ' … ' + text[-(limit - head - 3):].lstrip()
        return text[:limit]

    def _write_projection(self, event, text, title, *, importance=None, pinned=None):
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
            if type(importance) is int and 1 <= importance <= 10:
                args['importance'] = importance
            if pinned is True:
                args['pinned'] = True
            result = self.memory.call('hold', args)
            new_id = self.memory.created_id(result)
            try:
                self.memory.record_native(args, result)
            except Exception:
                pass
            return new_id, bool(new_id), False
        old = self.memory.content_for(memory_id, refresh=True)
        if old is None and callable(getattr(self.memory, 'raw_content_for', None)):
            old = self.memory.raw_content_for(memory_id)
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
            return memory_id, True, False
        return memory_id, now.strip() == text.strip(), False

    # --- background repair -----------------------------------------------------------------
    def retry_dirty(self, limit=10):
        fixed = 0
        for event in list(self.events().values()):
            if not event.get('dirty') or fixed >= limit:
                continue
            try:
                story = event.get('story') or self._story(event['event_id'])
                memory_id, changed, locked = self._write_projection(
                    event, self._projection(event['event_id'], story), event.get('title') or 'Event')
                if changed or locked:
                    event['dirty'] = False
                    self.store.append(self.events_path, event)
                    fixed += 1
            except Exception:
                continue
        return fixed

    def auto_correct(self):
        """Merge only events that provably share the same Ombre memory; never guess from topics."""
        by_memory = {}
        fixed = 0
        events = self.events()
        for event in events.values():
            memory_id = event.get('memory_id')
            if not memory_id:
                continue
            by_memory.setdefault(memory_id, []).append(event)
        for memory_id, group in by_memory.items():
            if len(group) < 2:
                continue
            keep = max(group, key=lambda event: int(event.get('stage_count') or 0))
            for other in group:
                if other['event_id'] == keep['event_id']:
                    continue
                for stage in self.stages(other['event_id']):
                    self.store.append(self.stages_path, {**stage, 'event_id': keep['event_id']})
                    fixed += 1
                other['status'] = 'merged'
                other['merged_from'] = keep['event_id']
                self.store.append(self.events_path, other)
            self.store.append(self.ops_path, {'at': datetime.now().astimezone().isoformat(),
                                              'op': 'merge', 'into': keep['event_id'],
                                              'memory_id': memory_id, 'stages': fixed})
        return fixed

    def _review(self, event_id, reason):
        self.store.append(self.review_path, {'at': datetime.now().astimezone().isoformat(),
                                             'event_id': event_id, 'reason': reason})

    # --- evidence --------------------------------------------------------------------------
    def _capture_evidence(self, stage):
        now = _when(stage['at']) or datetime.now().astimezone()
        cutoff = now - timedelta(minutes=max(1, self.settings['evidence_minutes']))
        collected = []
        for kind, path in (('chat', f'{self.cfg.id}/chat.jsonl'),
                           ('tool', f'{self.cfg.id}/tools.jsonl'),
                           ('autonomy', f'{self.cfg.id}/autonomy.jsonl')):
            try:
                rows = self.store.read_tail(path, 40)
            except Exception:
                continue
            for row in rows:
                stamp = _when(row.get('time'))
                if stamp is None or stamp < cutoff:
                    continue
                text = row.get('content') if kind == 'chat' else (
                    row.get('detail') or row.get('path') or row.get('text') or row.get('summary'))
                if not isinstance(text, str) or not text.strip():
                    continue
                collected.append({'kind': kind, 'at': stamp.isoformat(),
                                  'text': ' '.join(text.split())[:self.settings['evidence_chars']]})
        for item in collected[-max(1, self.settings['evidence_items']):]:
            item.update({'stage_id': stage['stage_id'], 'event_id': stage['event_id']})
            self.store.append(self.evidence_path, item)
        return collected
