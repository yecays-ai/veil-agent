"""Ticket 407: one event, one memory.

A save is a *stage* of an event, not a memory of its own. Stages commit atomically (pending row ->
event row -> committed row); the event has exactly one Ombre memory whose body is a 400-character
summary, while the full story and every stage/evidence stay in the local ledger and are readable
with `memory_timeline`.

Grouping pairs deterministic signals with a model decision over the candidate events and the raw
chat/autonomy/tool evidence; anything uncertain becomes a provisional, recallable event and is
written to the review ledger. It is never forced into an unrelated event.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta

import prompts

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(role_id):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(role_id, threading.RLock())


def _serialized(method):
    """One event write at a time per role: foreground appends, tidy revisions and recovery share it."""
    def wrapper(self, *args, **kwargs):
        with _lock_for(self.cfg.id):
            return method(self, *args, **kwargs)
    return wrapper

DEFAULTS = {
    'enabled': False,
    'evidence_minutes': 30,
    'evidence_items': 8,
    'evidence_chars': 400,
    'summary_chars': 400,
    'stage_chars': 600,
    'investigate_rows': 400,
    'timeline_page': 20,
}
MIN_MERGE_CONFIDENCE = 0.6

_TOKEN = re.compile(r'[0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_\-.\u4e00-\u9fff]*')
_CJK = re.compile(r'[\u4e00-\u9fff]+')
_STOP = {
    'the', 'and', 'for', 'with', 'that', 'this', 'was', 'were', 'had', 'have', 'from', 'she', 'her',
    'him', 'his', 'they', 'them', 'but', 'not', 'you', 'your', 'about', 'after', 'before', 'then',
    'when', 'what', 'why', 'how', 'said', 'says', 'one', 'two', 'three', 'day', 'night', 'today',
    '昨天', '今天', '晚上', '早上', '下午', '事情', '东西', '时候', '因为', '所以', '然后', '我们', '你们',
    '这个', '那个', '一个', '没有', '还是', '就是', '什么', '自己', '可以',
}
_DATE = re.compile(r'\b(?:\d{1,2}[-/月]\d{1,2}日?|\d{4}[-/]\d{2}[-/]\d{2})\b')
_NUMBER = re.compile(r'(?<![\w.])\d+(?:\.\d+)?(?![\w])(?!\.\d)')
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
    """Latin words plus CJK bigrams/trigrams, so a Chinese sentence without spaces still shares terms."""
    found = set()
    text = text or ''
    for raw in _TOKEN.findall(text):
        token = raw.strip('.-_').lower()
        if len(token) < 2 or token in _STOP or token.isdigit() or token.startswith('http'):
            continue
        found.add(token)
    for run in _CJK.findall(text):
        for size in (2, 3):
            for index in range(len(run) - size + 1):
                piece = run[index:index + size]
                if piece not in _STOP:
                    found.add(piece)
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


def _parse_json(text):
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.S)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


class EventMemory:
    def __init__(self, memory, settings=None, model=None, sidecar=None, budget=None):
        self.memory = memory
        self.cfg = memory.cfg
        self.store = memory.store
        self.model = model if callable(model) else None
        self.sidecar = sidecar
        self.budget = budget if budget is not None else getattr(memory, 'budget', None)
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
        self.projections_path = f'{self.cfg.id}/event-projections.jsonl'
        self.revisions_path = f'{self.cfg.id}/event-revisions.jsonl'
        self.locked = None

    # --- read side -------------------------------------------------------------------------
    def events(self):
        latest = {}
        for row in self.store.read(self.events_path):
            if isinstance(row, dict) and isinstance(row.get('event_id'), str):
                latest[row['event_id']] = row
        return latest

    def event_for_memory(self, memory_id):
        active = [event for event in self.events().values()
                  if event.get('memory_id') == memory_id and event.get('status') != 'merged']
        if not active:
            return None
        active.sort(key=lambda event: str(event.get('last_at') or ''), reverse=True)
        return active[0]['event_id']

    def event_map(self):
        return {event['memory_id']: event['event_id'] for event in self.events().values()
                if isinstance(event.get('memory_id'), str) and event.get('status') != 'merged'}

    def _stage_rows(self):
        latest = {}
        for row in self.store.read(self.stages_path):
            if isinstance(row, dict) and isinstance(row.get('stage_id'), str):
                latest[row['stage_id']] = row
        return latest

    def pending_stages(self):
        return [row for row in self._stage_rows().values() if row.get('state') != 'committed']

    def stages(self, event_id=None):
        rows = [row for row in self._stage_rows().values()
                if row.get('state') == 'committed' and (event_id is None or row.get('event_id') == event_id)]
        rows.sort(key=lambda row: str(row.get('at') or ''))
        return rows

    def _visible(self, event):
        memory_id = event.get('memory_id')
        if not memory_id or not callable(getattr(self.memory, 'refresh_visibility', None)):
            return True
        try:
            return bool(self.memory.refresh_visibility(memory_id))
        except Exception:
            return False

    def _evidence_for(self, stage_id):
        try:
            rows = self.store.read(self.evidence_path)
        except Exception:
            return []
        return [{'kind': row.get('kind'), 'at': row.get('at'), 'text': row.get('text')}
                for row in rows if isinstance(row, dict) and row.get('stage_id') == stage_id]

    def visible_events(self):
        rows = [event for event in self.events().values()
                if event.get('status') not in ('merged', 'closed') and self._visible(event)]
        rows.sort(key=lambda event: str(event.get('last_at') or ''), reverse=True)
        return rows

    def detail(self, event_id):
        event = self.events().get(event_id)
        if event is None or not self._visible(event):
            return None
        stages = [{**stage, 'evidence': self._evidence_for(stage.get('stage_id'))}
                  for stage in self.stages(event_id)]
        versions = []
        try:
            versions = [row for row in self.store.read(self.versions_path)
                        if isinstance(row, dict) and row.get('event_id') == event_id]
        except Exception:
            pass
        return {'event_id': event_id, 'title': event.get('title'), 'status': event.get('status'),
                'first_at': event.get('first_at'), 'last_at': event.get('last_at'),
                'story': event.get('story') or self._story(event_id), 'confidence': event.get('confidence'),
                'story_source': event.get('story_source'),
                'belief_history': event.get('belief_history') or self._belief_history(event_id),
                'related': [{'event_id': key, 'title': (self.events().get(key) or {}).get('title'),
                             'last_at': (self.events().get(key) or {}).get('last_at')}
                            for key in self._related_events(event_id)],
                'stages': stages,
                'versions': [{'at': row.get('at'), 'old_story': row.get('old_story'),
                              'old_content': row.get('old_content')} for row in versions]}

    def timeline(self, event_id, offset=0, limit=None, include_evidence=True):
        """The whole story plus one page of stages. offset is a stage index; next_offset is a cursor."""
        event = self.events().get(event_id)
        if event is None or not self._visible(event):
            return None
        stages = self.stages(event_id)
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        page = max(1, int(limit or self.settings['timeline_page']))
        chunk = stages[offset:offset + page]
        story = event.get('story') or self._story(event_id)
        lines = [f"{event.get('title') or event_id}",
                 f"{event.get('status')} · {str(event.get('first_at') or '')[:16]} → {str(event.get('last_at') or '')[:16]}",
                 f"stages {offset + 1}-{offset + len(chunk)} of {len(stages)}", '']
        if offset == 0:
            lines += [story, '', '— stages —']
        else:
            lines += ['— stages —']
        for stage in chunk:
            lines.append(f"- [{stage.get('stage_id')}] {str(stage.get('at'))[:16]} {stage.get('text')}")
            if stage.get('interpretation'):
                lines.append(f"  his reading: {stage['interpretation']}")
            extra = stage.get('facts') if isinstance(stage.get('facts'), dict) else {}
            if extra.get('files') or extra.get('numbers'):
                lines.append(f"  facts: {', '.join((extra.get('files') or []) + (extra.get('numbers') or []))[:300]}")
            if include_evidence:
                for proof in self._evidence_for(stage.get('stage_id')):
                    lines.append(f"  evidence ({proof.get('kind')}): {str(proof.get('text') or '')[:300]}")
        next_offset = offset + len(chunk) if offset + len(chunk) < len(stages) else None
        lines.append('' if next_offset is None else f"… 下一段从 offset={next_offset} 继续")
        return '\n'.join(lines), next_offset, len(stages)

    # --- write side ------------------------------------------------------------------------
    @_serialized
    def record(self, text, title, *, why='', at=None, importance=None, pinned=None,
               interpretation='', event_id=None):
        when = _when(at) or datetime.now().astimezone()
        text = ' '.join((text or '').split())
        if not text:
            raise ValueError('empty stage')
        stage_id = 's-' + hashlib.sha1(
            f'{title or ""}|{text}|{when.strftime("%Y-%m-%d")}'.encode('utf-8')).hexdigest()[:16]
        existing = self._stage_rows().get(stage_id)
        if existing is not None and existing.get('state') == 'committed':
            event = self.events().get(existing.get('event_id')) or {}
            if interpretation and existing.get('interpretation') != interpretation:
                self.store.append(self.stages_path, {**existing, 'state': 'committed',
                                                     'interpretation': ' '.join(interpretation.split())[:300]})
                if event:
                    stages = self.stages(event['event_id'])
                    story, source = self._write_story(stages, event.get('story') or self._story(event['event_id']),
                                                      event)
                    event.update({'story': story, 'story_source': source,
                                  'story_through': stages[-1].get('at') if stages else event.get('story_through'),
                                  'belief_history': self._belief_history(event['event_id']),
                                  'dirty': True})
                    self.store.append(self.events_path, event)
                    self.retry_dirty()
            return {'event_id': existing.get('event_id'), 'title': event.get('title') or title,
                    'memory_id': event.get('memory_id'), 'updated': True, 'changed': True,
                    'locked': False, 'confidence': event.get('confidence'), 'duplicate': True}
        stage = {
            'stage_id': stage_id, 'at': when.isoformat(), 'text': text[:self.settings['stage_chars']],
            'entities': sorted(entities(text))[:80], 'facts': facts(text), 'why': why,
        }
        if interpretation:
            stage['interpretation'] = ' '.join(interpretation.split())[:300]
        if event_id:
            event = self.events().get(event_id)
            if event is None:
                raise ValueError('unknown event')
            event = {**event, 'status': 'open', 'last_at': stage['at']}
            confidence = event.get('confidence') or 0.5
            updated = True
        else:
            event, updated, confidence = self._attach(stage, title)
        stage['event_id'] = event['event_id']
        # Journal first: a pending row carries the event snapshot, so a later write failure can be resumed
        # with the same stage_id instead of creating a duplicate.
        self.store.append(self.stages_path, {**stage, 'state': 'pending', 'event_row': event})
        story = self._story(event['event_id'], extra=stage)
        story, story_source = self._write_story(self.stages(event['event_id']) + [stage], story, event)
        projection = self._projection(story)
        changed, locked = False, False
        memory_id = event.get('memory_id')
        old_story = event.get('story')
        try:
            memory_id, changed, locked = self._write_projection(
                event, projection, title or event.get('title') or 'Event',
                importance=importance, pinned=pinned)
        except Exception:
            changed, locked = False, False
        if memory_id:
            try:
                self.store.append(self.projections_path,
                                  {'stage_id': stage_id, 'event_id': event['event_id'], 'memory_id': memory_id})
            except Exception:
                pass
        if old_story and old_story != story:
            self.store.append(self.versions_path, {
                'event_id': event['event_id'], 'memory_id': memory_id,
                'old_story': old_story, 'old_content': None,
                'at': datetime.now().astimezone().isoformat()})
        event.update({
            'memory_id': memory_id or event.get('memory_id'),
            'last_at': when.isoformat(),
            'stage_count': len(self.stages(event['event_id'])) + 1,
            'story': story,
            'story_source': story_source,
            'story_through': stage['at'],
            'belief_history': self._belief_history(event['event_id'], extra=stage),
            'related_to': sorted(set(event.get('related_to') or []) | set(self._related_events(event['event_id']))),
            'confidence': confidence,
            'dirty': not changed and not locked,
            'last_story_at': datetime.now().astimezone().isoformat(),
        })
        try:
            self.store.append(self.events_path, event)
            self.store.append(self.stages_path, {**stage, 'state': 'committed'})
        except Exception:
            return {'event_id': event['event_id'], 'title': event.get('title') or title,
                    'memory_id': event.get('memory_id'), 'updated': updated, 'changed': False,
                    'locked': False, 'confidence': confidence, 'pending': True}
        if event.get('status') == 'uncertain':
            self._review(event['event_id'], 'low confidence grouping; candidate links kept')
        try:
            self._capture_evidence(stage)
        except Exception:
            pass
        return {'event_id': event['event_id'], 'title': event.get('title') or title,
                'memory_id': event.get('memory_id'), 'updated': updated, 'changed': changed,
                'locked': locked, 'confidence': confidence}

    @_serialized
    def revise(self, event_id, text, title='', seen=None, why='整理'):
        """Replace the event's narrative without appending it as a new stage (tidy revisions)."""
        event = self.events().get(event_id)
        if event is None or not self._visible(event):
            return False
        memory_id = event.get('memory_id')
        old_story = event.get('story') or ''
        old_projection = None
        if memory_id:
            if callable(self.locked) and self.locked(memory_id):
                return False
            old_projection = self.memory.content_for(memory_id, refresh=True)
            if seen is not None and old_projection is not None and old_projection.strip() != str(seen).strip():
                return False
        text = ' '.join((text or '').split())
        if not text:
            return False
        missing = self._missing_facts(self.stages(event_id), text)
        if missing:
            self._review(event_id, 'tidy revision dropped facts: ' + ', '.join(missing[:10]))
            return False
        revision_id = 'r-' + uuid.uuid4().hex[:12]
        stamp = datetime.now().astimezone().isoformat()
        if old_story and old_story != text:
            self.store.append(self.versions_path, {'event_id': event_id, 'memory_id': memory_id,
                                                   'old_story': old_story, 'at': stamp, 'reason': why})
        if memory_id and old_projection:
            self.store.append(f'{self.cfg.id}/memory-versions.jsonl', {
                'memory_id': memory_id, 'fields': ['content'], 'operation_reason': '事件故事修订',
                'why_remembered': '', 'old_content': old_projection})
        projection = text[:self.settings['summary_chars']]
        self.store.append(self.revisions_path, {'state': 'begin', 'event_id': event_id,
                                                'revision_id': revision_id, 'story': text, 'at': stamp})
        if memory_id:
            try:
                self.memory.call('trace', {'bucket_id': memory_id, 'content': projection})
            except Exception:
                return False
            try:
                self.memory.bucket_details_cache.pop(memory_id, None)
            except Exception:
                pass
            written = self.memory.content_for(memory_id, refresh=True)
            if written is not None and written.strip() != projection.strip():
                return False
        event.update({'story': text, 'dirty': False, 'last_story_at': stamp,
                      'story_source': 'tidy', 'revision_id': revision_id,
                      'title': (title or event.get('title'))[:100]})
        self.store.append(self.events_path, event)
        self.store.append(self.revisions_path, {'state': 'done', 'event_id': event_id,
                                                'revision_id': revision_id, 'at': stamp})
        self.store.append(self.ops_path, {'at': stamp, 'op': 'revise', 'event_id': event_id,
                                          'why': why, 'before': old_story[:500], 'after': text[:500]})
        return True

    @_serialized
    def retry_pending(self, limit=10):
        fixed = 0
        made = {}
        try:
            for row in self.store.read(self.projections_path):
                if isinstance(row, dict) and row.get('stage_id') and row.get('memory_id'):
                    made[row['stage_id']] = row['memory_id']
        except Exception:
            made = {}
        for stage in self.pending_stages()[:limit]:
            snapshot = stage.get('event_row')
            if not isinstance(snapshot, dict):
                continue
            clean = {key: value for key, value in stage.items() if key != 'event_row'}
            event_id = clean.get('event_id')
            projected = made.get(clean.get('stage_id'))
            current = self.events().get(event_id)
            if current:
                # Never overwrite a newer index with the pending snapshot: keep its memory_id and latest
                # state, fold this stage into the story, and leave the projection for retry_dirty().
                story = self._story(event_id, extra=clean)
                event = {**current, 'story': story,
                         'stage_count': len(self.stages(event_id)) + 1,
                         'last_at': max(str(current.get('last_at') or ''), str(clean.get('at') or '')),
                         'dirty': True}
                if not event.get('memory_id') and isinstance(snapshot.get('memory_id'), str):
                    event['memory_id'] = snapshot['memory_id']
                if not event.get('memory_id') and projected:
                    event['memory_id'] = projected
            else:
                event = {**snapshot, 'story': self._story(event_id, extra=clean), 'dirty': True}
                if not event.get('memory_id') and projected:
                    event['memory_id'] = projected
            try:
                self.store.append(self.events_path, event)
                self.store.append(self.stages_path, {**clean, 'state': 'committed'})
                fixed += 1
            except Exception:
                continue
        return fixed

    def _candidate_scores(self, stage, title, evidence):
        scores = {}
        now = _when(stage['at']) or datetime.now().astimezone()
        stage_entities = set(stage['entities'])

        def add(event_id, points, reason):
            row = scores.setdefault(event_id, {'score': 0.0, 'reasons': []})
            row['score'] += points
            row['reasons'].append(reason)

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

        for event in self.events().values():
            if not self._visible(event):
                continue
            shared = len(stage_entities & set(event.get('entities') or []))
            last = _when(event.get('last_at'))
            if shared:
                add(event['event_id'], min(shared, 3) * 2, 'shared names/objects')
            if last and last.date() == now.date():
                add(event['event_id'], 1, 'same day')
            if event.get('status') == 'open':
                add(event['event_id'], 1, 'open')

        if max([row['score'] for row in scores.values()] or [0]) < 4:
            for event in self.events().values():
                if not self._visible(event):
                    continue
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
                if len(names & entities(text)) >= 2:
                    return True
        return False

    def _evidence_preview(self, now):
        cutoff = now - timedelta(minutes=max(1, self.settings['evidence_minutes']))
        out = []
        for path in (f'{self.cfg.id}/chat.jsonl', f'{self.cfg.id}/autonomy.jsonl',
                     f'{self.cfg.id}/tools.jsonl'):
            try:
                rows = self.store.read_tail(path, 20)
            except Exception:
                continue
            for row in rows:
                stamp = _when(row.get('time'))
                if stamp is None or stamp < cutoff:
                    continue
                text = row.get('content') or row.get('detail') or row.get('path') or row.get('text')
                if isinstance(text, str) and text.strip():
                    out.append(' '.join(text.split())[:300])
        return out[-self.settings['evidence_items']:]

    def _model_choose(self, stage, scores, title):
        """One structured decision over the candidate events and the raw evidence. None on any failure."""
        if self.model is None or not scores:
            return None
        ranked = sorted(scores.items(), key=lambda item: item[1]['score'], reverse=True)[:5]
        events = self.events()
        candidates = []
        for event_id, info in ranked:
            event = events.get(event_id) or {}
            recent = [f"{str(row.get('at'))[:16]} {row.get('text')}" for row in self.stages(event_id)[-2:]]
            candidates.append({'event_id': event_id, 'title': event.get('title'),
                               'span': [event.get('first_at'), event.get('last_at')],
                               'recent': recent, 'signals': info['reasons']})
        messages = [
            {'role': 'system', 'content': prompts.text('memory.event.group')},
            {'role': 'user', 'content': json.dumps({'new_stage': {'title': title, 'text': stage['text'],
                                                                   'at': stage['at']},
                                                     'evidence': self._evidence_preview(_when(stage['at'])),
                                                     'candidates': candidates}, ensure_ascii=False)[:12000]},
        ]
        try:
            request_cfg = replace(self.cfg, max_output_tokens=400)
            reservation, amount = (None, None)
            if self.budget is not None and callable(getattr(self.budget, 'reserve', None)):
                reservation, amount = self.budget.reserve(request_cfg, accounting='event-group',
                                                          category='event-group')
        except Exception:
            return None   # no budget: keep the provisional-event path
        try:
            result = self.model(messages, max_tokens=400, detail=True)
            output = result[0] if isinstance(result, tuple) else result
            payload = _parse_json(output if isinstance(output, str) else '')
            if reservation is not None:
                try:
                    prompt_tokens = int(result[1]) if isinstance(result, tuple) and len(result) > 1 else 0
                    completion_tokens = int(result[2]) if isinstance(result, tuple) and len(result) > 2 else 0
                    cached_tokens = int(result[3]) if isinstance(result, tuple) and len(result) > 3 else 0
                    self.budget.settle(reservation, request_cfg, prompt_tokens, completion_tokens,
                                       cached_tokens, accounting='event-group', category='event-group')
                except Exception:
                    pass
        except Exception:
            if reservation is not None and callable(getattr(self.budget, 'settle_estimate', None)):
                try:
                    self.budget.settle_estimate((reservation, amount), accounting='event-group-failed')
                except Exception:
                    pass
            return None
        if not isinstance(payload, dict):
            return None
        chosen = payload.get('event_id')
        if chosen is not None and chosen not in {event_id for event_id, _ in ranked}:
            return None
        try:
            confidence = max(0.0, min(1.0, float(payload.get('confidence'))))
        except (TypeError, ValueError):
            confidence = 0.5
        return {'event_id': chosen, 'confidence': confidence, 'reason': str(payload.get('reason') or '')[:200]}

    def _attach(self, stage, title, evidence=None):
        now = _when(stage['at']) or datetime.now().astimezone()
        scores = self._candidate_scores(stage, title, evidence or [])
        if not scores:
            return self._new_event(stage, title, [], 'open'), False, 0.4
        ranked = sorted(scores.items(), key=lambda item: item[1]['score'], reverse=True)
        # Similar words/day only produce candidates. The merge itself must be an event-relationship decision;
        # a high score alone never merges (review of #407).
        decision = self._model_choose(stage, scores, title) if self.model is not None else None
        if decision and decision['event_id'] and decision['confidence'] >= MIN_MERGE_CONFIDENCE:
            event = self.events()[decision['event_id']]
            event = {**event, 'status': 'open', 'last_at': stage['at'],
                     'entities': sorted(set(event.get('entities') or []) | set(stage['entities']))[:80]}
            return event, True, decision['confidence']
        links = [event_id for event_id, _ in ranked[:3]]
        confidence = decision['confidence'] if decision else round(min(0.9, ranked[0][1]['score'] / 8), 2)
        return self._new_event(stage, title, links, 'uncertain'), False, confidence

    def _new_event(self, stage, title, candidate_ids, status):
        when = _when(stage['at']) or datetime.now().astimezone()
        event_id = 'e-' + when.strftime('%Y%m%d') + '-' + hashlib.sha1(
            f'{stage["stage_id"]}|{title}'.encode('utf-8')).hexdigest()[:6]
        return {'event_id': event_id, 'title': (title or _first_sentence(stage['text'], 60) or 'Event')[:100],
                'status': status, 'first_at': stage['at'], 'last_at': stage['at'],
                'entities': stage['entities'], 'stage_count': 0, 'dirty': False,
                'candidate_links': candidate_ids}

    # --- story and projection --------------------------------------------------------------
    def _story(self, event_id, extra=None):
        lines = []
        for stage in self.stages(event_id) + ([extra] if extra else []):
            when = _when(stage.get('at'))
            stamp = when.strftime('%m-%d %H:%M') if when else ''
            line = f'{stamp} {stage.get("text")}'.strip()
            if stage.get('interpretation'):
                line += f'（他当时理解：{stage["interpretation"]}）'
            lines.append(line)
        return '\n\n'.join(lines)

    def _write_story(self, stages, fallback, event=None):
        """A natural narrative from the stages, checked against every stage's dates/numbers/files.

        Normally this merges only the stages after `story_through` into the existing story (Graphiti's
        saga watermark). Every eighth stage it re-derives from all stages so an old omission can return.
        """
        if self.model is None or not stages:
            return fallback, 'composed'
        existing = str((event or {}).get('story') or '')
        through = str((event or {}).get('story_through') or '')
        fresh = [stage for stage in stages if not through or str(stage.get('at') or '') > through]
        if not fresh:
            fresh = stages
        full_rederive = not existing or len(stages) % 8 == 0
        attempts = []
        if existing and not full_rederive:
            attempts.append(('merge', fresh, existing))
        attempts.append(('full', stages, ''))
        story = ''
        for mode, source, previous in attempts:
            prompt_id = 'memory.event.story.merge' if mode == 'merge' else 'memory.event.story'
            messages = [
                {'role': 'system', 'content': prompts.text(prompt_id)},
                {'role': 'user', 'content': json.dumps({
                    'existing_story': previous,
                    'stages': [{'at': stage.get('at'), 'text': stage.get('text'),
                                 'his_reading': stage.get('interpretation')} for stage in source],
                    'beliefs': [{'at': stage.get('at'), 'belief': stage.get('interpretation'),
                                 'valid_from': stage.get('at'), 'invalid_at': stage.get('invalid_at')}
                                for stage in stages if stage.get('interpretation')]},
                    ensure_ascii=False)[:16000]},
            ]
            for _ in range(2):
                try:
                    request_cfg = replace(self.cfg, max_output_tokens=1200)
                    reservation = amount = None
                    if self.budget is not None and callable(getattr(self.budget, 'reserve', None)):
                        reservation, amount = self.budget.reserve(request_cfg, accounting='event-story',
                                                                  category='event-story')
                    result = self.model(messages, max_tokens=1200, detail=True)
                    output = result[0] if isinstance(result, tuple) else result
                    if reservation is not None:
                        try:
                            prompt_tokens = int(result[1]) if isinstance(result, tuple) and len(result) > 1 else 0
                            completion_tokens = int(result[2]) if isinstance(result, tuple) and len(result) > 2 else 0
                            cached_tokens = int(result[3]) if isinstance(result, tuple) and len(result) > 3 else 0
                            self.budget.settle(reservation, request_cfg, prompt_tokens, completion_tokens,
                                               cached_tokens, accounting='event-story', category='event-story')
                        except Exception:
                            pass
                    story = (output or '').strip() if isinstance(output, str) else ''
                except Exception:
                    story = ''
                if not story:
                    break
                candidate = story[:6000]
                missing = self._missing_facts(stages, candidate)
                if not missing:
                    return candidate, ('model+merge' if mode == 'merge' else 'model')
                messages.append({'role': 'user', 'content': 'Keep every one of these exactly: ' + ', '.join(missing[:20])})
        return fallback, 'composed'

    @staticmethod
    def _missing_facts(stages, story):
        missing = []
        story_numbers = set(_NUMBER.findall(story or ''))
        for stage in stages:
            extra = stage.get('facts') if isinstance(stage.get('facts'), dict) else {}
            for value in (extra.get('dates') or []) + (extra.get('files') or []):
                if str(value) and str(value) not in story:
                    missing.append(str(value))
            for value in extra.get('numbers') or []:
                if str(value) not in story_numbers:
                    missing.append(str(value))
        return list(dict.fromkeys(missing))

    def _belief_history(self, event_id, extra=None):
        history = []
        for stage in self.stages(event_id) + ([extra] if extra else []):
            belief = stage.get('interpretation')
            if not belief:
                continue
            if history and history[-1]['belief'] != belief:
                history[-1]['invalid_at'] = stage.get('at')
            history.append({'at': stage.get('at'), 'valid_from': stage.get('at'),
                            'invalid_at': None, 'belief': belief, 'stage_id': stage.get('stage_id')})
        return history

    def _related_events(self, event_id):
        event = self.events().get(event_id) or {}
        mine = set(event.get('entities') or [])
        scored = []
        for other in self.events().values():
            if other.get('event_id') == event_id or other.get('status') == 'merged' or not self._visible(other):
                continue
            shared = len(mine & set(other.get('entities') or []))
            if shared:
                scored.append((shared, other['event_id']))
        scored.sort(reverse=True)
        return [key for _, key in scored[:3]]

    def _projection(self, story):
        text = ' '.join((story or '').split())
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
        if memory_id and self.sidecar is not None:
            note = self.sidecar.all().get(memory_id) or {}
            edited = note.get('lan_edited')
            if edited and str(edited) > str(event.get('last_story_at') or ''):
                self._review(event['event_id'], 'Lan edited this memory; the automatic story was not written')
                return memory_id, True, True
        if memory_id and callable(getattr(self.memory, 'refresh_visibility', None)):
            try:
                if not self.memory.refresh_visibility(memory_id):
                    return memory_id, True, True   # hidden or deleted: never resurrect it
            except Exception:
                return memory_id, False, False
        if not memory_id:
            args = {'content': text, 'title': title[:100]}
            if type(importance) is int and 1 <= importance <= 10:
                args['importance'] = importance
            if pinned is True:
                args['pinned'] = True
            operation = duplicate = None
            if callable(getattr(self.memory, 'begin_write', None)):
                operation, duplicate = self.memory.begin_write(args)
                if duplicate is not None:
                    # The earlier hold may already exist; never send a second one with unknown result.
                    return None, False, False
            result = self.memory.call('hold', args, write_operation=operation) if operation else \
                self.memory.call('hold', args)
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
            stamp = datetime.now().astimezone().isoformat()
            self.store.append(self.versions_path, {'event_id': event['event_id'], 'memory_id': memory_id,
                                                   'old_content': old, 'at': stamp})
            # The existing memory page restores from memory-versions.jsonl; write the same record so an
            # event rewrite is recoverable through the original entry point too.
            self.store.append(f'{self.cfg.id}/memory-versions.jsonl', {
                'memory_id': memory_id, 'fields': ['content'],
                'operation_reason': '事件故事重写', 'why_remembered': '', 'old_content': old})
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
    def _recover_revisions(self):
        """A revise whose trace succeeded but whose event row failed: finish it once, never re-apply blindly."""
        try:
            rows = self.store.read(self.revisions_path)
        except Exception:
            return 0
        done = {row.get('revision_id') for row in rows if row.get('state') == 'done'}
        begins = [row for row in rows if row.get('state') == 'begin'
                  and row.get('revision_id') not in done]
        begins.sort(key=lambda row: str(row.get('at') or ''))
        fixed = 0
        for row in begins:
            event = self.events().get(row.get('event_id'))
            if not event:
                continue
            memory_id = event.get('memory_id')
            if memory_id and callable(self.locked) and self.locked(memory_id):
                continue
            story = str(row.get('story') or '')
            projection = story[:self.settings['summary_chars']]
            written = self.memory.content_for(memory_id, refresh=True) if memory_id else None
            if written is None or written.strip() != projection.strip():
                if memory_id:
                    try:
                        self.memory.call('trace', {'bucket_id': memory_id, 'content': projection})
                    except Exception:
                        continue
            event.update({'story': story, 'dirty': False,
                          'last_story_at': datetime.now().astimezone().isoformat()})
            self.store.append(self.events_path, event)
            self.store.append(self.revisions_path, {'state': 'done', 'event_id': event['event_id'],
                                                    'revision_id': row.get('revision_id'),
                                                    'at': datetime.now().astimezone().isoformat()})
            fixed += 1
        return fixed

    @_serialized
    def retry_dirty(self, limit=10):
        fixed = 0
        self._recover_revisions()
        self.retry_pending(limit)
        for event in list(self.events().values()):
            if not event.get('dirty') or fixed >= limit:
                continue
            try:
                story = event.get('story') or self._story(event['event_id'])
                memory_id, changed, locked = self._write_projection(
                    event, self._projection(story), event.get('title') or 'Event')
                if changed or locked:
                    event['memory_id'] = memory_id or event.get('memory_id')
                    event['dirty'] = False
                    self.store.append(self.events_path, event)
                    fixed += 1
            except Exception:
                continue
        return fixed

    def _lan_edited(self, event):
        memory_id = event.get('memory_id')
        if not memory_id or self.sidecar is None:
            return False
        note = self.sidecar.all().get(memory_id) or {}
        edited = note.get('lan_edited')
        return bool(edited and str(edited) > str(event.get('last_story_at') or ''))

    def _refresh_event(self, event_id, *, close_empty=False):
        event = self.events().get(event_id)
        if not event:
            return None
        stages = self.stages(event_id)
        if not stages and close_empty:
            event.update({'story': '', 'stage_count': 0, 'status': 'closed',
                          'belief_history': [], 'dirty': False,
                          'last_story_at': datetime.now().astimezone().isoformat()})
            memory_id = event.get('memory_id')
            if memory_id:
                try:
                    self.memory.call('trace', {'bucket_id': memory_id,
                                               'content': '这段经历已全部移到别处，不再从这里想起。'})
                    self.memory.call('trace', {'bucket_id': memory_id, 'dont_surface': True})
                except Exception:
                    pass
            self.store.append(self.events_path, event)
            return event
        event.update({'story': self._story(event_id), 'stage_count': len(stages),
                      'belief_history': self._belief_history(event_id), 'dirty': True,
                      'last_story_at': datetime.now().astimezone().isoformat()})
        self.store.append(self.events_path, event)
        return event

    @_serialized
    def move_stage(self, stage_id, to_event_id, why='纠正归组'):
        stage = self._stage_rows().get(stage_id)
        target = self.events().get(to_event_id)
        if not stage or stage.get('state') != 'committed' or not target or target.get('status') == 'merged':
            return False
        source_id = stage.get('event_id')
        if source_id == to_event_id:
            return False
        source = self.events().get(source_id) or {}
        if self._lan_edited(source) or self._lan_edited(target):
            self._review(source_id, 'Lan edited this event; a stage move was blocked')
            return False
        self.store.append(self.stages_path, {**stage, 'event_id': to_event_id, 'state': 'committed'})
        self._refresh_event(source_id, close_empty=True)
        self._refresh_event(to_event_id)
        self.store.append(self.ops_path, {'at': datetime.now().astimezone().isoformat(), 'op': 'move',
                                          'stage': stage_id, 'from': source_id, 'to': to_event_id, 'why': why})
        self.retry_dirty()
        return True

    @_serialized
    def split_event(self, event_id, stage_ids, title='', why='纠正归组'):
        event = self.events().get(event_id)
        if not event or not stage_ids or self._lan_edited(event):
            return None
        chosen = [self._stage_rows().get(stage_id) for stage_id in stage_ids]
        chosen = [stage for stage in chosen if stage and stage.get('event_id') == event_id]
        if not chosen or len(chosen) == len(self.stages(event_id)):
            return None
        when = _when(chosen[0].get('at')) or datetime.now().astimezone()
        new_id = 'e-' + when.strftime('%Y%m%d') + '-' + hashlib.sha1(
            f'{event_id}|{",".join(stage_ids)}'.encode('utf-8')).hexdigest()[:6]
        new_event = {'event_id': new_id, 'title': (title or event.get('title') or 'Event')[:100],
                     'status': 'open', 'first_at': chosen[0].get('at'), 'last_at': chosen[-1].get('at'),
                     'entities': sorted({word for stage in chosen for word in stage.get('entities') or []})[:80],
                     'stage_count': len(chosen), 'dirty': True, 'story': '',
                     'belief_history': [], 'related_to': [event_id]}
        self.store.append(self.events_path, new_event)
        for stage in chosen:
            self.store.append(self.stages_path, {**stage, 'event_id': new_id, 'state': 'committed'})
        self._refresh_event(event_id)
        self._refresh_event(new_id)
        self.store.append(self.ops_path, {'at': datetime.now().astimezone().isoformat(), 'op': 'split',
                                          'from': event_id, 'to': new_id,
                                          'stages': list(stage_ids), 'why': why})
        self.retry_dirty()
        return new_id

    @_serialized
    def dream_pass(self, why='tick'):
        """agent-memory style claim/lease/watermark around recovery, so two ticks never run together."""
        state_path = f'{self.cfg.id}/event-dream.json'
        report_path = f'{self.cfg.id}/event-dream-report.jsonl'
        now = datetime.now().astimezone()
        try:
            state = json.loads((self.store.root / state_path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        lease = _when(state.get('lease_until'))
        if state.get('status') == 'running' and lease and now < lease:
            return 0
        state.update({'status': 'running', 'lease_until': (now + timedelta(minutes=5)).isoformat(),
                      'attempt': int(state.get('attempt') or 0) + 1, 'started_at': now.isoformat()})
        self.store.replace(state_path, state)
        try:
            fixed = self.retry_pending() + self.retry_dirty() + self.auto_correct()
            outcome = 'succeeded'
            error = ''
        except Exception as exc:
            fixed, outcome, error = 0, 'failed', type(exc).__name__
        end = datetime.now().astimezone()
        state.update({'status': 'idle', 'lease_until': None, 'last_run_at': end.isoformat(),
                      'last_outcome': outcome, 'last_fixed': fixed, 'last_error': error,
                      'covered_through': str((max((event.get('last_at') or '' for event in self.events().values()),
                                                   default='')))})
        self.store.replace(state_path, state)
        self.store.append(report_path, {'at': end.isoformat(), 'why': why, 'outcome': outcome,
                                        'fixed': fixed, 'error': error})
        return fixed

    def dream_state(self):
        try:
            return json.loads((self.store.root / f'{self.cfg.id}/event-dream.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    @_serialized
    def auto_correct(self):
        """Merge only events that provably share the same Ombre memory; never guess from topics."""
        by_memory, fixed = {}, 0
        for event in self.events().values():
            if event.get('status') == 'merged':
                continue
            if isinstance(event.get('memory_id'), str):
                by_memory.setdefault(event['memory_id'], []).append(event)
        for memory_id, group in by_memory.items():
            if len(group) < 2:
                continue
            keep = max(group, key=lambda item: int(item.get('stage_count') or 0))
            before = {'story': keep.get('story'), 'stage_count': keep.get('stage_count')}
            moved = 0
            for other in group:
                if other['event_id'] == keep['event_id']:
                    continue
                for stage in self.stages(other['event_id']):
                    self.store.append(self.stages_path, {**stage, 'event_id': keep['event_id'],
                                                         'state': 'committed'})
                    moved += 1
                other['status'] = 'merged'
                other['merged_from'] = keep['event_id']
                self.store.append(self.events_path, other)
            story = self._story(keep['event_id'])
            keep.update({'story': story, 'stage_count': len(self.stages(keep['event_id'])),
                         'dirty': True})
            self.store.append(self.events_path, keep)
            self.store.append(self.ops_path, {
                'at': datetime.now().astimezone().isoformat(), 'op': 'merge',
                'into': keep['event_id'], 'memory_id': memory_id, 'stages': moved,
                'before': before, 'after': {'story': story, 'stage_count': keep['stage_count']}})
            fixed += moved
        if fixed:
            self.retry_dirty()
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
