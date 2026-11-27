"""Ticket 407: one event, one memory.

A save is a *stage* of an event, not a memory of its own. Stages commit atomically (pending row ->
event row -> committed row); the event has exactly one Ombre memory whose body is the complete
searchable story, while every stage/evidence also stays in the local ledger and is readable with
`memory_timeline`. Chat injection remains bounded separately by the request token budget.

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
from decimal import Decimal

from veil import prompts
from veil.brain.storage import brain_events
from veil.brain.storage.brain_authority import ProjectionConflict, ProjectionLocked

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_DREAM_UNREADABLE = object()


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
    'summary_chars': 0,          # 0 indexes the whole story; recall_budget still limits chat context
    'stage_chars': 0,            # ticket 421: 0 keeps the whole stage; the real request budget is the limit
    'story_batch_chars': 12000,  # ticket 421: stages are merged into the story in batches this size
    'search_query_chars': 2000,  # the embedding query may be shorter than the stage; grouping sees it all
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


_NATURAL_API_KEY = re.compile(
    r'(?i)(api[_ -]?key)(\s+is\s+)(?:bearer\s+)?[^\s,;]{4,}'
    r'(?:\s+(?!password\b|passwd\b|secret\b|access[_ -]?token\b|authorization\b|'
    r'bearer\b|api[_ -]?key\b)[^\s,;]{4,})*')
_SENSITIVE_KV = re.compile(
    r'(?i)(api[_ -]?key|password|passwd|secret|access[_ -]?token|authorization|bearer|密码|密钥)'
    r'(\s*[:=：]\s*(?:bearer\s+)?|(?<=\bbearer)\s+)'
    r'[^\s,;]{4,}')
_OPAQUE_SECRET = re.compile(
    r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9_-]{12,})\b')


def _redact_evidence_text(text):
    """Mask API keys/tokens on model-visible evidence output boundaries without mutating chat logs."""
    if not text:
        return ''
    text = _NATURAL_API_KEY.sub(lambda m: m.group(1) + m.group(2) + '[已隐藏]', text)
    text = _SENSITIVE_KV.sub(lambda m: m.group(1) + m.group(2) + '[已隐藏]', text)
    return _OPAQUE_SECRET.sub('[已隐藏的凭据]', text)


def _extract_date_parts(text):
    """(month, day, year_or_None, span) for every date expression in text.

    Digit boundaries keep a longer number such as ``11-250`` from being read as November 25
    (P6-S2 review); a year written with the date is returned so it can participate in matching.
    """
    text = text or ''
    found = []
    pattern = re.compile(r'(?:(?P<year>\d{4})\s*[-/年]\s*)?'
                         r'(?P<month>\d{1,2})\s*[-/月]\s*(?P<day>\d{1,2})\s*日?')
    for match in pattern.finditer(text):
        start, end = match.span()
        if start and text[start - 1].isdigit():
            continue
        if end < len(text) and text[end].isdigit():
            continue
        month, day = int(match.group('month')), int(match.group('day'))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        year = int(match.group('year')) if match.group('year') else None
        found.append((month, day, year, (start, end)))
    months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
              'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
    for match in re.finditer(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b',
                             text, re.I):
        month = months.get(match.group(1).lower()[:3])
        day = int(match.group(2))
        if month and 1 <= day <= 31:
            found.append((month, day, None, match.span()))
    return found


def facts(text):
    text = text or ''
    dates = [text[start:end].strip() for _m, _d, _y, (start, end) in _extract_date_parts(text)]
    return {'dates': sorted(set(d for d in dates if d)),
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
    def __init__(self, memory, settings=None, model=None, sidecar=None, budget=None,
                 cost_category=None, daily_cap=None):
        self.memory = memory
        self.cfg = memory.cfg
        self.store = memory.store
        self.model = model if callable(model) else None
        self.sidecar = sidecar
        self.budget = budget if budget is not None else getattr(memory, 'budget', None)
        self.cost_category = cost_category
        self.daily_cap = Decimal(str(daily_cap)) if daily_cap is not None else None
        self._model_budget_blocked = False
        self._last_projection_pending = False
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
        return [row for row in self._stage_rows().values()
                if row.get('state') not in ('committed', 'superseded')]

    def _supersede_pending(self, source_key, keep_stage_id):
        """Ticket 421 review: one source entry has one journal row. Older duplicate pending rows from a
        reworded retry are retired so recovery cannot commit the same batch twice."""
        if not source_key:
            return
        for row in self._stage_rows().values():
            if (row.get('state') == 'pending' and row.get('source_key') == source_key
                    and row.get('stage_id') != keep_stage_id):
                clean = {key: value for key, value in row.items() if key != 'event_row'}
                self.store.append(self.stages_path, {**clean, 'state': 'superseded'})

    def stages(self, event_id=None):
        rows = [row for row in self._stage_rows().values()
                if row.get('state') == 'committed' and (event_id is None or row.get('event_id') == event_id)]
        rows.sort(key=lambda row: str(row.get('at') or row.get('saved_at') or ''))
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
        return [{'kind': row.get('kind'), 'at': row.get('at'), 'saved_at': row.get('saved_at'),
                 'message_id': row.get('message_id'), 'text': row.get('text')}
                for row in rows if isinstance(row, dict) and row.get('stage_id') == stage_id]

    def _infer_at(self, now):
        """The latest real chat/tool/autonomy row near the save, or None: an unknown date stays unknown."""
        cutoff = now - timedelta(minutes=max(1, self.settings['evidence_minutes']))
        stamps = []
        for path in (f'{self.cfg.id}/chat.jsonl', f'{self.cfg.id}/tools.jsonl',
                     f'{self.cfg.id}/autonomy.jsonl'):
            try:
                rows = self.store.read_tail(path, 20)
            except Exception:
                continue
            for row in rows:
                stamp = _when(row.get('time'))
                if stamp is not None and cutoff <= stamp <= now:
                    stamps.append(stamp)
        return max(stamps) if stamps else None

    @staticmethod
    def _normalise_source(source):
        """A compact, traceable source range: what it came from and when that material was said or done."""
        if not isinstance(source, dict):
            return None
        kinds = source.get('kinds')
        if not isinstance(kinds, list) or not kinds:
            kind = source.get('kind')
            kinds = [kind] if isinstance(kind, str) and kind else []
        kinds = [str(item) for item in kinds if isinstance(item, str) and item]
        out = {'kind': '+'.join(dict.fromkeys(kinds)) or 'unknown'}
        for key in ('from', 'to'):
            value = source.get(key)
            if isinstance(value, str) and value:
                out[key] = value
        count = source.get('count')
        if type(count) is int and count > 0:
            out['count'] = count
        ids = source.get('message_ids')
        if isinstance(ids, list):
            ids = [item for item in ids if isinstance(item, str) and item]
            if ids:
                out['message_ids'] = ids
        return out

    @classmethod
    def _evidence_source(cls, rows):
        rows = [row for row in rows or [] if isinstance(row, dict)]
        if not rows:
            return None
        stamps = sorted(str(row.get('at')) for row in rows if row.get('at'))
        ids = [str(row.get('message_id')) for row in rows if row.get('message_id')]
        return {'kinds': sorted({str(row.get('kind')) for row in rows if row.get('kind')}),
                'from': stamps[0] if stamps else None, 'to': stamps[-1] if stamps else None,
                'count': len(rows),
                **({'message_ids': list(dict.fromkeys(ids))[:40]} if ids else {})}

    def visible_events(self):
        rows = [event for event in self.events().values()
                if event.get('status') not in ('merged', 'closed') and self._visible(event)]
        rows.sort(key=lambda event: str(event.get('last_at') or event.get('last_saved_at') or ''), reverse=True)
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
                'story_through': event.get('story_through'),
                'story_coverage': event.get('story_coverage') or [],
                'story_pending': bool(event.get('story_pending')),
                'dirty': bool(event.get('dirty')),
                'first_at_known': event.get('first_at_known', True),
                'last_at_known': event.get('last_at_known', True),
                'pending_review': bool(event.get('pending_review')),
                'pending_stages': len([row for row in self.pending_stages()
                                       if row.get('event_id') == event_id]),
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
        if event is None or event.get('status') in ('merged', 'closed') or not self._visible(event):
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
                 f"{event.get('status')} · {str(event.get('first_at') or '')[:16]} → {str(event.get('last_at') or '')[:16]}"
                 + ('' if event.get('first_at_known', True) and event.get('last_at_known', True)
                    else '（有日期未知的段）'),
                 f"stages {offset + 1}-{offset + len(chunk)} of {len(stages)}", '']
        if event.get('story_pending'):
            lines += ['（故事还在整理；阶段已经完整保存，稍后会重试。）', '']
        if offset == 0:
            lines += [story, '', '— stages —']
        else:
            lines += ['— stages —']
        for stage in chunk:
            when = stage.get('at')
            stamp = (str(when)[:16] if stage.get('at_known', True)
                     else f"日期未知（记于 {str(stage.get('saved_at') or '')[:16]}）")
            lines.append(f"- [{stage.get('stage_id')}] {stamp} {stage.get('text')}")
            source = stage.get('source') if isinstance(stage.get('source'), dict) else {}
            if source:
                span = ' → '.join(str(source[key])[:16] for key in ('from', 'to') if source.get(key))
                lines.append(f"  source: {source.get('kind') or 'unknown'}"
                             + (f" {span}" if span else '')
                             + (f" ({source['count']} 条)" if source.get('count') else ''))
            if stage.get('interpretation'):
                lines.append(f"  his reading: {stage['interpretation']}")
            extra = stage.get('facts') if isinstance(stage.get('facts'), dict) else {}
            if extra.get('files') or extra.get('numbers'):
                lines.append(f"  facts: {', '.join((extra.get('files') or []) + (extra.get('numbers') or []))[:300]}")
            if include_evidence:
                for proof in self._evidence_for(stage.get('stage_id')):
                    safe_text = _redact_evidence_text(str(proof.get('text') or ''))
                    lines.append(f"  evidence ({proof.get('kind')}): {safe_text[:300]}")
        next_offset = offset + len(chunk) if offset + len(chunk) < len(stages) else None
        lines.append('' if next_offset is None else f"… 下一段从 offset={next_offset} 继续")
        return '\n'.join(lines), next_offset, len(stages)

    # --- write side ------------------------------------------------------------------------
    @staticmethod
    def _locked_story_view(event, reviewed, old_story):
        """Reviewed-story view committed when the target is or just became locked (ticket 453)."""
        view = dict(event)
        view.update({
            'story': old_story or event.get('story') or '',
            'story_source': reviewed['story_source'],
            'story_through': reviewed['story_through'],
            'story_through_id': reviewed['story_through_id'],
            'story_coverage': reviewed['story_coverage'] or [],
            'last_story_at': reviewed['last_story_at'],
            'story_pending': True, 'dirty': True,
            'pending_review': True,
        })
        return view

    @_serialized
    def record(self, text, title, *, why='', at=None, importance=None, pinned=None,
               interpretation='', event_id=None, source=None, source_key=''):
        saved_at = datetime.now().astimezone()
        # When the caller knows when it happened (the tidy batch), use that. Peter's own save during a
        # conversation happened around the latest message, not at whatever time the tidy job runs.
        occurred = _when(at) or self._infer_at(saved_at)
        order_at = occurred or saved_at
        text = ' '.join((text or '').split())
        if not text:
            raise ValueError('empty stage')
        key = source_key or order_at.strftime('%Y-%m-%d')
        stage_id = 's-' + hashlib.sha1(
            f'{title or ""}|{text}|{key}'.encode('utf-8')).hexdigest()[:16]
        stage_rows = self._stage_rows()
        existing = stage_rows.get(stage_id)
        pending_reuse = None
        if source_key:
            committed = next((row for row in stage_rows.values()
                              if row.get('state') == 'committed' and row.get('source_key') == source_key),
                             None)
            if committed is not None:
                existing = committed
            else:
                pending_rows = [row for row in stage_rows.values()
                                if row.get('state') == 'pending' and row.get('source_key') == source_key
                                and isinstance(row.get('event_id'), str)]
                if pending_rows:
                    # Ticket 421 review: a crash after the journal write leaves the stage pending. A retry
                    # of the same source batch must resume that row, not create a second stage that may
                    # later be grouped elsewhere. The newest wording wins, the identity does not change.
                    pending_reuse = max(pending_rows, key=lambda row: str(row.get('saved_at') or row.get('at') or ''))
                    stage_id = str(pending_reuse.get('stage_id') or stage_id)
        if pending_reuse is None and existing is not None and existing.get('state') == 'pending':
            pending_reuse = existing
            stage_id = str(existing.get('stage_id') or stage_id)
        if existing is not None and existing.get('state') == 'committed':
            if event_id and existing.get('event_id') != event_id:
                # The same source batch was already recorded in another experience; never silently
                # pretend the explicit target was used (ticket 421).
                raise ValueError('stage already belongs to another event')
            event = self.events().get(existing.get('event_id')) or {}
            if interpretation and existing.get('interpretation') != interpretation:
                self.store.append(self.stages_path, {**existing, 'state': 'committed',
                                                     'interpretation': ' '.join(interpretation.split())[:300]})
                if event:
                    stages = self.stages(event['event_id'])
                    story, source_name, through, pending = self._write_story(
                        stages, event.get('story') or self._story(event['event_id']), event)
                    if pending:
                        story, source_name = self._story(event['event_id']), 'composed'
                        through = stages[-1].get('at') if stages else event.get('story_through')
                        pending = False
                    event.update({'story': story, 'story_source': source_name,
                                  'story_through': through or event.get('story_through'),
                                  'story_through_id': (stages[-1].get('stage_id') if stages
                                                       else event.get('story_through_id')),
                                  'story_pending': pending,
                                  'belief_history': self._belief_history(event['event_id']),
                                  'dirty': pending})
                    self.store.append(self.events_path, event)
                    self.retry_dirty()
            return {'event_id': existing.get('event_id'), 'title': event.get('title') or title,
                    'memory_id': event.get('memory_id'), 'updated': True, 'changed': True,
                    'locked': False, 'confidence': event.get('confidence'), 'duplicate': True}
        seen_revision = None
        if pending_reuse is not None:
            reused_event_id = str(pending_reuse.get('event_id') or '')
            if event_id and event_id != reused_event_id:
                self._review(reused_event_id, 'retry target differed from the journaled pending stage; '
                                              'kept the journaled experience to avoid a duplicate')
            event_id = reused_event_id
            event = self.events().get(event_id) if event_id else None
            if event is not None:
                if (event.get('status') in ('merged', 'closed') or not self._visible(event)):
                    raise ValueError('unknown event')
                seen_revision = int(event.get('revision') or 0)
                event = {**event, 'status': 'open'}
                if occurred is not None:
                    event['last_at'] = occurred.isoformat()
            else:
                snapshot = pending_reuse.get('event_row')
                if not isinstance(snapshot, dict) or not snapshot.get('event_id'):
                    raise ValueError('unknown event')
                if snapshot.get('status') in ('merged', 'closed'):
                    raise ValueError('unknown event')
                event = dict(snapshot)
            confidence = event.get('confidence') or 0.5
            updated = True
        elif event_id:
            event = self.events().get(event_id)
            # An explicit target must still be a real, visible experience; a stale or hidden one is
            # refused instead of silently overwriting it (ticket 421).
            if (event is None or event.get('status') in ('merged', 'closed')
                    or not self._visible(event)):
                raise ValueError('unknown event')
            seen_revision = int(event.get('revision') or 0)
            event = {**event, 'status': 'open'}
            if occurred is not None:
                event['last_at'] = occurred.isoformat()
            confidence = event.get('confidence') or 0.5
            updated = True
        else:
            probe = {'at': occurred.isoformat() if occurred else None,
                     'saved_at': saved_at.isoformat(), 'entities': sorted(entities(text))[:80],
                     'stage_id': stage_id, 'text': text}
            event, updated, confidence = self._attach(probe, title)
            seen_revision = int(event.get('revision') or 0) if event.get('event_id') in self.events() else None
        limit = self.settings.get('stage_chars') or 0
        stage = {
            'stage_id': stage_id, 'at': occurred.isoformat() if occurred else None,
            'occurred_at': occurred.isoformat() if occurred else None,
            'at_known': bool(occurred), 'saved_at': saved_at.isoformat(),
            'text': text[:limit] if limit > 0 else text,
            'entities': sorted(entities(text))[:80], 'facts': facts(text), 'why': why,
        }
        if source_key:
            stage['source_key'] = source_key
        summary = self._normalise_source(source)
        if summary:
            stage['source'] = summary
        if interpretation:
            stage['interpretation'] = ' '.join(interpretation.split())[:300]
        stage['event_id'] = event['event_id']
        # Journal first: a pending row carries the event snapshot, so a later write failure can be resumed
        # with the same stage_id instead of creating a duplicate.
        self.store.append(self.stages_path, {**stage, 'state': 'pending', 'event_row': event})
        if pending_reuse is not None and source_key:
            # Retire older duplicate journal rows only after the replacement journal row is safely written;
            # a refused or failed retry must leave the original pending stage recoverable.
            self._supersede_pending(source_key, stage_id)
        old_story = event.get('story')
        memory_id = event.get('memory_id')
        # P6-S2 review: capture the committed projection baseline before calling the story model so
        # any panel edit committed while the model runs is detected inside the write transaction.
        old_projection = self.memory.content_for(memory_id, refresh=True) if memory_id else None
        story, story_source, through, story_pending = self._write_story(
            self.stages(event['event_id']) + [stage], self._story(event['event_id'], extra=stage), event)
        if story_pending:
            if self._model_budget_blocked:
                # A paid rewrite that cannot start must remain pending and must not replace a good draft.
                story = old_story or self._story(event['event_id'], extra=stage)
                story_source = event.get('story_source') or 'composed'
                through = event.get('story_through')
            else:
                # A broken/unavailable story model degrades to the complete stages, never a partial answer.
                story, story_source = self._story(event['event_id'], extra=stage), 'composed'
                through = stage.get('at') or stage.get('saved_at')
                story_pending = False
        projection = self._projection(story)
        fresh = self.events().get(event['event_id'])
        if seen_revision is not None and fresh is not None and int(fresh.get('revision') or 0) != seen_revision:
            # The model composed against an older version. Stop before touching the index: the stage is
            # journaled for recovery, nothing newer is overwritten (ticket 421).
            return {'event_id': event['event_id'], 'title': event.get('title') or title,
                    'memory_id': event.get('memory_id'), 'updated': updated, 'changed': False,
                    'locked': False, 'confidence': confidence, 'pending': True, 'version_conflict': True}
        authority = getattr(self.memory, 'brain_authority', None)
        changed, locked = False, False
        memory_id = event.get('memory_id')
        if authority is None:
            try:
                memory_id, changed, locked = self._write_projection(
                    event, projection, title or event.get('title') or 'Event',
                    importance=importance, pinned=pinned)
            except Exception:
                changed, locked = False, False
        elif memory_id:
            try:
                locked = bool(callable(self.locked) and self.locked(memory_id))
                note = self.sidecar.all().get(memory_id) if self.sidecar is not None else {}
                edited = (note or {}).get('lan_edited')
                if edited and str(edited) > str(event.get('last_story_at') or ''):
                    locked = True
                if callable(getattr(self.memory, 'refresh_visibility', None)):
                    locked = locked or not self.memory.refresh_visibility(memory_id)
            except Exception:
                return {'event_id': event['event_id'], 'title': event.get('title') or title,
                        'memory_id': memory_id, 'updated': updated, 'changed': False,
                        'locked': False, 'confidence': confidence, 'pending': True}
            changed = not locked
        else:
            changed = True

        try:
            collected = self._capture_evidence(stage, persist=False)
        except Exception:
            collected = []
        explicit_quotes = self._explicit_user_quotes(stage)
        source_seen = self._normalise_source(self._evidence_source(collected))
        if source_seen and not stage.get('source'):
            stage['source'] = source_seen

        reviewed = {key: event.get(key) for key in
                    ('story_source', 'story_through', 'story_through_id', 'story_coverage', 'last_story_at')}
        event.update({
            'memory_id': memory_id or event.get('memory_id'),
            'last_at': occurred.isoformat() if occurred else event.get('last_at'),
            'last_saved_at': saved_at.isoformat(),
            'stage_count': len(self.stages(event['event_id'])) + 1,
            'story': story,
            'story_source': story_source,
            'story_through': through or event.get('story_through'),
            'story_through_id': (event.get('story_through_id') if story_pending else stage['stage_id']),
            'story_coverage': ((event.get('story_coverage') or []) if story_pending and old_story else
                               [row.get('stage_id') for row in self.stages(event['event_id']) + [stage]
                                if row.get('stage_id')]),
            'story_pending': story_pending,
            'belief_history': self._belief_history(event['event_id'], extra=stage),
            'related_to': sorted(set(event.get('related_to') or []) | set(self._related_events(event['event_id']))),
            'confidence': confidence,
            'dirty': story_pending or (not changed and not locked),
            'last_story_at': (reviewed['last_story_at'] if locked
                              else datetime.now().astimezone().isoformat()),
            'revision': int(event.get('revision') or 0) + 1,
        })
        if 'first_at_known' not in event:
            event['first_at_known'] = stage['at_known'] if not updated else event.get('first_at_known', True)
        event['last_at_known'] = stage['at_known']
        ledger_rows = []
        locked_review_row = ('event-review', {
            'at': saved_at.isoformat(), 'event_id': event['event_id'],
            'reason': 'locked event got a new stage; reviewed story waits for unlock'})
        locked_event = None
        locked_ledger_rows = []
        if locked:
            event = self._locked_story_view(event, reviewed, old_story)
            story_pending = True
            ledger_rows.append(locked_review_row)
        elif memory_id and authority is not None:
            # Ticket 453: the panel can take the lock between the check above and the bundle's write
            # transaction. Keep a reviewed-story view ready so the bundle re-check inside its
            # transaction commits it instead of the candidate story, never touching the locked body.
            locked_event = self._locked_story_view(dict(event), reviewed, old_story)
            locked_ledger_rows = [locked_review_row]
        if event.get('status') == 'uncertain':
            ledger_rows.append(('event-review', {
                'at': saved_at.isoformat(), 'event_id': event['event_id'],
                'reason': 'low confidence grouping; candidate links kept'}))
        evidence_rows, quote_rows = self._stage_evidence_ledger(
            stage, event['event_id'], collected, explicit_quotes, saved_at)
        ledger_rows.extend(evidence_rows)

        if authority is not None:
            try:
                result = authority.commit_event_bundle(
                    event=event, stage={**stage, 'state': 'committed'},
                    projection=None if locked else projection,
                    title=title or event.get('title') or 'Event',
                    importance=importance, pinned=pinned,
                    embedder=self.memory.embedder(), old_story=old_story or '',
                    operation_reason='事件故事更新', ledger_rows=ledger_rows,
                    quotes=quote_rows,
                    seen_projection=old_projection,
                    locked_event=locked_event, locked_ledger_rows=locked_ledger_rows)
                memory_id = result['memory_id']
                event['memory_id'] = memory_id
                if result.get('locked'):
                    # Ticket 453: the lock was taken after the check above; the bundle committed the
                    # reviewed-story view instead of the candidate projection.
                    locked = True
                    story_pending = True
                    if locked_event is not None:
                        event = dict(locked_event, memory_id=memory_id)
                changed = result['changed'] or locked
                self._last_projection_pending = result['index_pending']
                self.memory.bucket_details_cache.pop(memory_id, None)
                try:
                    if old_projection is None and callable(getattr(self.memory, 'record_native', None)):
                        self.memory.record_native(
                            {'content': projection, 'title': title or event.get('title') or 'Event'},
                            f'新建→{memory_id}')
                    elif callable(getattr(self.memory, 'record_native_update', None)):
                        # P6-S2 review: a locked event keeps its committed body. The candidate story
                        # must never become the readable cached body before Lan confirms it.
                        confirmed = old_projection if locked else projection
                        if confirmed:
                            self.memory.record_native_update(memory_id, confirmed)
                except Exception:
                    pass
            except ProjectionConflict:
                # The committed body changed between the read above and the transaction; nothing new
                # was overwritten and the stage stays journaled for recovery.
                return {'event_id': event['event_id'], 'title': event.get('title') or title,
                        'memory_id': event.get('memory_id'), 'updated': updated, 'changed': False,
                        'locked': locked, 'confidence': confidence, 'pending': True,
                        'version_conflict': True}
            except Exception:
                return {'event_id': event['event_id'], 'title': event.get('title') or title,
                        'memory_id': event.get('memory_id'), 'updated': updated, 'changed': False,
                        'locked': locked, 'confidence': confidence, 'pending': True}
        else:
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
            try:
                self.store.append(self.events_path, event)
                self.store.append(self.stages_path, {**stage, 'state': 'committed'})
                ledger_paths = {kind: f'{self.cfg.id}/{name}'
                                for name, kind in brain_events.EVENT_LEDGER_FILES.items()}
                for kind, row in ledger_rows:
                    if kind in ledger_paths:
                        self.store.append(ledger_paths[kind], row)
            except Exception:
                return {'event_id': event['event_id'], 'title': event.get('title') or title,
                        'memory_id': event.get('memory_id'), 'updated': updated, 'changed': False,
                        'locked': locked, 'confidence': confidence, 'pending': True}
        return {'event_id': event['event_id'], 'title': event.get('title') or title,
                'memory_id': event.get('memory_id'), 'updated': updated, 'changed': changed,
                'locked': locked, 'confidence': confidence, 'story_pending': story_pending,
                'index_pending': self._last_projection_pending, 'story_source': story_source,
                'at': stage['at'], 'at_known': stage['at_known'], 'saved_at': stage['saved_at'],
                'source': stage.get('source'), 'stage_id': stage_id}


    @_serialized
    def revise(self, event_id, text, title='', seen=None, why='整理'):
        """Replace the event's narrative without appending it as a new stage (tidy revisions)."""
        self.last_revise_status = ''
        event = self.events().get(event_id)
        if event is None or event.get('status') in ('merged', 'closed') or not self._visible(event):
            self.last_revise_status = 'closed'
            return False
        memory_id = event.get('memory_id')
        old_story = event.get('story') or ''
        old_projection = None
        if memory_id:
            if callable(self.locked) and self.locked(memory_id):
                self.last_revise_status = 'locked'
                return False
            old_projection = self.memory.content_for(memory_id, refresh=True)
            if seen is not None and old_projection is not None and old_projection.strip() != str(seen).strip():
                self.last_revise_status = 'conflict'
                return False
        text = ' '.join((text or '').split())
        if not text:
            self.last_revise_status = 'empty'
            return False
        missing = self._missing_facts(self.stages(event_id), text)
        if missing:
            self._review(event_id, 'tidy revision dropped facts: ' + ', '.join(missing[:10]))
            self.last_revise_status = 'fact_mismatch'
            return False
        revision_id = 'r-' + uuid.uuid4().hex[:12]
        stamp = datetime.now().astimezone().isoformat()
        projection = self._projection(text)
        explicit_title = title.strip()[:100] if isinstance(title, str) and title.strip() else ''
        authority = getattr(self.memory, 'brain_authority', None)
        if authority is not None:
            updated_event = dict(event)
            updated_event.update({
                'story': text, 'dirty': False, 'last_story_at': stamp,
                'story_source': 'tidy', 'revision_id': revision_id,
                'title': (explicit_title or event.get('title') or '')[:100],
            })
            embedder = None
            try:
                embedder = self.memory.embedder()
            except Exception:
                embedder = None
            try:
                authority.commit_event_bundle(
                    event=updated_event,
                    projection=projection if memory_id else None,
                    title=explicit_title,
                    embedder=embedder,
                    old_story=old_story,
                    operation_reason='事件故事修订',
                    # P6-S2 review: the body checked above may have changed since; re-check inside the
                    # transaction so a draft based on the old text can never overwrite a newer save.
                    seen_projection=old_projection if memory_id else None,
                    ledger_rows=(
                        ('event-revision', {'state': 'done', 'event_id': event_id,
                                            'revision_id': revision_id, 'story': text, 'at': stamp}),
                        ('event-op', {'at': stamp, 'op': 'revise', 'event_id': event_id,
                                      'why': why, 'before': old_story[:500], 'after': text[:500]}),
                    ),
                )
            except ProjectionLocked:
                # Ticket 453: the panel took the lock after the check above; refuse the whole revision.
                self.last_revise_status = 'locked'
                return False
            except ProjectionConflict:
                self.last_revise_status = 'conflict'
                return False
            except Exception:
                self.last_revise_status = 'write_failed'
                return False
            if memory_id:
                try:
                    self.memory.bucket_details_cache.pop(memory_id, None)
                except Exception:
                    pass
                if callable(getattr(self.memory, 'record_native_update', None)):
                    try:
                        self.memory.record_native_update(memory_id, projection)
                    except Exception:
                        pass
            self.last_revise_status = 'ok'
            return True
        if old_story and old_story != text:
            self.store.append(self.versions_path, {'event_id': event_id, 'memory_id': memory_id,
                                                   'old_story': old_story, 'at': stamp, 'reason': why})
        if memory_id and old_projection:
            self.store.append(f'{self.cfg.id}/memory-versions.jsonl', {
                'memory_id': memory_id, 'fields': ['content'], 'operation_reason': '事件故事修订',
                'why_remembered': '', 'old_content': old_projection})
        self.store.append(self.revisions_path, {'state': 'begin', 'event_id': event_id,
                                                'revision_id': revision_id, 'story': text, 'at': stamp})
        if memory_id:
            try:
                self.memory.call('trace', {'bucket_id': memory_id, 'content': projection})
            except Exception:
                self.last_revise_status = 'write_failed'
                return False
            try:
                self.memory.bucket_details_cache.pop(memory_id, None)
            except Exception:
                pass
            written = self.memory.content_for(memory_id, refresh=True)
            if written is not None and written.strip() != projection.strip():
                self.last_revise_status = 'write_failed'
                return False
        event.update({'story': text, 'dirty': False, 'last_story_at': stamp,
                      'story_source': 'tidy', 'revision_id': revision_id,
                      'title': (title or event.get('title'))[:100]})
        self.store.append(self.events_path, event)
        self.store.append(self.revisions_path, {'state': 'done', 'event_id': event_id,
                                                'revision_id': revision_id, 'at': stamp})
        self.store.append(self.ops_path, {'at': stamp, 'op': 'revise', 'event_id': event_id,
                                          'why': why, 'before': old_story[:500], 'after': text[:500]})
        self.last_revise_status = 'ok'
        return True

    @_serialized
    def retry_pending(self, limit=10):
        fixed = 0
        attempted = 0
        made = {}
        try:
            for row in self.store.read(self.projections_path):
                if isinstance(row, dict) and row.get('stage_id') and row.get('memory_id'):
                    made[row['stage_id']] = row['memory_id']
        except Exception:
            made = {}
        pending = sorted(self.pending_stages(),
                         key=lambda row: str(row.get('saved_at') or row.get('at') or ''))
        chosen, older = {}, []
        for stage in pending:
            source = stage.get('source_key')
            key = ('source', source) if source else ('stage', stage.get('stage_id'))
            if key in chosen:
                older.append(chosen[key])
            chosen[key] = stage
        for stage in older:
            # Ticket 421 review: a reworded retry can leave two journal rows for one source entry.
            # Keep the newest wording and retire the old row instead of committing both.
            clean = {key: value for key, value in stage.items() if key != 'event_row'}
            self.store.append(self.stages_path, {**clean, 'state': 'superseded'})
        for stage in chosen.values():
            if attempted >= limit:
                break
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
            if event.get('memory_id') and callable(self.locked):
                try:
                    if self.locked(event['memory_id']):
                        # Ticket 456: keep the stage pending for the unlock, but skip it
                        # without spending a batch slot so later unlocked stages are not starved.
                        continue
                except Exception:
                    continue
            attempted += 1
            # P6-S2 review: a stage whose bundle failed had its evidence and quotes rolled back with
            # that transaction. Recovering it must go through the same bundle path and commit the
            # stage together with its evidence; marking it committed alone would lose the chain.
            try:
                collected = self._capture_evidence(clean, persist=False)
            except Exception:
                collected = []
            explicit_quotes = self._explicit_user_quotes(clean)
            stamp = _when(clean.get('saved_at')) or _when(clean.get('at')) or datetime.now().astimezone()
            ledger_rows, quote_rows = self._stage_evidence_ledger(
                clean, event_id, collected, explicit_quotes, stamp)
            authority = getattr(self.memory, 'brain_authority', None)
            if authority is not None:
                need_projection = not event.get('memory_id')
                embedder = None
                if need_projection:
                    try:
                        embedder = self.memory.embedder()
                    except Exception:
                        embedder = None
                try:
                    bundle_event = {**event, 'dirty': False} if need_projection else event
                    authority.commit_event_bundle(
                        event=bundle_event, stage={**clean, 'state': 'committed'},
                        projection=(self._projection(event['story']) if need_projection else None),
                        title=(event.get('title') or 'Event') if need_projection else '',
                        importance=event.get('importance'), pinned=event.get('pinned'),
                        embedder=embedder, old_story='', operation_reason='待提交阶段恢复',
                        ledger_rows=ledger_rows, quotes=quote_rows, abort_on_locked=True)
                    fixed += 1
                except Exception:
                    continue
                continue
            try:
                self.store.append(self.events_path, event)
                self.store.append(self.stages_path, {**clean, 'state': 'committed'})
                ledger_paths = {kind: f'{self.cfg.id}/{name}'
                                for name, kind in brain_events.EVENT_LEDGER_FILES.items()}
                for kind, row in ledger_rows:
                    if kind in ledger_paths:
                        self.store.append(ledger_paths[kind], row)
                fixed += 1
            except Exception:
                continue
        return fixed

    def _candidate_scores(self, stage, title, evidence):
        scores = {}
        now = _when(stage.get('at')) or _when(stage.get('saved_at')) or datetime.now().astimezone()
        stage_entities = set(stage['entities'])

        def add(event_id, points, reason):
            row = scores.setdefault(event_id, {'score': 0.0, 'reasons': []})
            row['score'] += points
            row['reasons'].append(reason)

        ids = []
        try:
            # Ticket 421: the whole stage is the query up to the embedding budget; a long development
            # must not be grouped from its opening only. The grouping model still sees the full text.
            query = f'{title}\n{stage["text"]}'
            cap = int(self.settings.get('search_query_chars') or 0)
            if cap > 0:
                query = query[:cap]
            answer = self.memory.call('breath_search', {'query': query,
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

    def _reserve_model(self, request_cfg, accounting):
        if self.budget is None or not callable(getattr(self.budget, 'reserve', None)):
            return None, None
        category = self.cost_category or accounting
        try:
            return self.budget.reserve(request_cfg, accounting=accounting, category=category,
                                       category_daily=self.daily_cap)
        except Exception:
            self._model_budget_blocked = True
            raise

    def _model_choose(self, stage, scores, title):
        """One structured decision over the candidate events and the raw evidence. None on any failure."""
        if self.model is None or not scores:
            return None
        ranked = sorted(scores.items(), key=lambda item: item[1]['score'], reverse=True)[:5]
        events = self.events()
        candidates = []
        for event_id, info in ranked:
            event = events.get(event_id) or {}
            recent = [f"{str(row.get('at'))[:16]} {str(row.get('text') or '')[:800]}"
                      for row in self.stages(event_id)[-2:]]
            # Ticket 421: the existing story is part of the comparison. Deciding from two recent stages
            # alone made different discussions look alike.
            story = str(event.get('story') or self._story(event_id) or '')[:800]
            candidates.append({'event_id': event_id, 'title': event.get('title'),
                               'span': [event.get('first_at'), event.get('last_at')],
                               'story': story, 'recent': recent, 'signals': info['reasons']})
        new_text = str(stage.get('text') or '')
        grouping_text = new_text
        if len(new_text) > 4000:
            # Ticket 421 review: a long stage's ending can be the part that says what it continues.
            # Send head and tail instead of silently dropping everything after the opening.
            head, tail = 2600, 1400
            grouping_text = new_text[:head] + ' …[middle omitted]… ' + new_text[-tail:]
        payload = {'new_stage': {'title': title, 'text': grouping_text,
                                 **({'text_truncated': True, 'full_chars': len(new_text)}
                                    if len(new_text) > 4000 else {}),
                                 'at': stage['at']},
                   'evidence': self._evidence_preview(_when(stage.get('at')) or _when(stage.get('saved_at'))
                                                      or datetime.now().astimezone()),
                   'candidates': candidates}
        content = json.dumps(payload, ensure_ascii=False)
        while len(content) > 24000 and candidates:
            # Keep the request bounded without cutting the JSON itself.
            candidates = candidates[:-1]
            payload['candidates'] = candidates
            content = json.dumps(payload, ensure_ascii=False)
        messages = [
            {'role': 'system', 'content': prompts.text('memory.event.group')},
            {'role': 'user', 'content': content},
        ]
        try:
            request_cfg = replace(self.cfg, max_output_tokens=400)
            reservation, amount = self._reserve_model(request_cfg, 'event-group')
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
                                       cached_tokens, accounting='event-group',
                                       category=self.cost_category or 'event-group')
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
        now = _when(stage.get('at')) or _when(stage.get('saved_at')) or datetime.now().astimezone()
        scores = self._candidate_scores(stage, title, evidence or [])
        if not scores:
            return self._new_event(stage, title, [], 'open'), False, 0.4
        ranked = sorted(scores.items(), key=lambda item: item[1]['score'], reverse=True)
        # Similar words/day only produce candidates. The merge itself must be an event-relationship decision;
        # a high score alone never merges (review of #407).
        decision = self._model_choose(stage, scores, title) if self.model is not None else None
        if decision and decision['event_id'] and decision['confidence'] >= MIN_MERGE_CONFIDENCE:
            event = self.events().get(decision['event_id'])
            if event is None or event.get('status') in ('merged', 'closed') or not self._visible(event):
                return self._new_event(stage, title, [], 'uncertain'), False, decision['confidence']
            event = {**event, 'status': 'open', 'last_at': stage['at'],
                     'entities': sorted(set(event.get('entities') or []) | set(stage['entities']))[:80]}
            return event, True, decision['confidence']
        links = [event_id for event_id, _ in ranked[:3]]
        confidence = decision['confidence'] if decision else round(min(0.9, ranked[0][1]['score'] / 8), 2)
        return self._new_event(stage, title, links, 'uncertain'), False, confidence

    def _new_event(self, stage, title, candidate_ids, status):
        when = _when(stage.get('at')) or _when(stage.get('saved_at')) or datetime.now().astimezone()
        event_id = 'e-' + when.strftime('%Y%m%d') + '-' + hashlib.sha1(
            f'{stage["stage_id"]}|{title}'.encode('utf-8')).hexdigest()[:6]
        return {'event_id': event_id, 'title': (title or _first_sentence(stage['text'], 60) or 'Event')[:100],
                'status': status, 'first_at': stage['at'], 'last_at': stage['at'],
                'first_saved_at': stage.get('saved_at'), 'last_saved_at': stage.get('saved_at'),
                'entities': stage['entities'], 'stage_count': 0, 'dirty': False,
                'candidate_links': candidate_ids}

    # --- story and projection --------------------------------------------------------------
    def _story(self, event_id, extra=None):
        lines = []
        for stage in self.stages(event_id) + ([extra] if extra else []):
            when = _when(stage.get('at'))
            if stage.get('at_known') is False:
                stamp = '（日期未知）'
            else:
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
        Ticket 421: the material is sent in batches instead of slicing one JSON string, so a long event
        cannot lose its second half. Returns (story, source, through, pending): `pending` means the
        narrative is not finished and must be rebuilt later; the stages are already safe on disk.
        """
        self._model_budget_blocked = False
        if self.model is None or not stages:
            return fallback, 'composed', None, False
        existing = str((event or {}).get('story') or '')
        through = str((event or {}).get('story_through') or '')
        through_id = str((event or {}).get('story_through_id') or '')
        ids = [str(stage.get('stage_id') or '') for stage in stages]
        if through_id and through_id in ids:
            # Ticket 421 review: the watermark follows stage identity, so two stages saved in the same
            # minute still advance it (timestamps can be equal).
            fresh = stages[ids.index(through_id) + 1:]
        else:
            fresh = [stage for stage in stages if not through or str(stage.get('at') or '') > through]
        if not fresh:
            fresh = stages
        full_rederive = bool((event or {}).get('story_pending')) or not existing or len(stages) % 8 == 0
        merge = bool(existing) and not full_rederive
        try:
            max_input = int((self.cfg.provider or {}).get('max_input_tokens') or 0)
        except (TypeError, ValueError):
            max_input = 0
        if merge and max_input:
            # The existing story is part of the request; when it leaves no room for a batch, re-derive
            # from the stages instead of sending an oversized request (ticket 421).
            if max_input - len(existing) - 4000 < 2000:
                merge = False
        budget = self._story_budget()
        if merge:
            merged, pending, last_through = self._merge_many(fresh, existing, budget)
            if merged and not pending:
                return merged, 'model+merge', last_through, False
            if merged:
                # Keep the successfully merged prefix for this pass; retry_dirty rebuilds from scratch.
                return merged, 'model+merge+partial', last_through, True
        merged, pending, last_through = self._merge_many(stages, '', budget)
        if merged and not pending:
            return merged, 'model', last_through, False
        if merged:
            return merged, 'model+partial', last_through, True
        return fallback, 'composed', None, True

    def _story_budget(self, existing_chars=0):
        budget = int(self.settings.get('story_batch_chars') or 12000)
        try:
            max_input = int((self.cfg.provider or {}).get('max_input_tokens') or 0)
        except (TypeError, ValueError):
            max_input = 0
        if max_input:
            budget = min(budget, max(1200, max_input - existing_chars - 4000))
        return max(1000, budget)

    def _merge_many(self, stages, previous, budget=None):
        """Weave incremental batches into `previous` when present, or build from scratch in batches."""
        story, through = previous, None
        progressed = pending = False
        for chunk in self._story_batches(stages, budget):
            if not chunk:
                continue
            # P6-S2 review: every accepted merge must still carry the facts already accepted in the
            # story so far, not just this batch's facts. Otherwise a later batch can replace the
            # earlier story while the caller treats the result as the complete narrative.
            carried = [{'text': story, 'facts': facts(story)}] if story else []
            merged = self._merge_story_chunk(story, chunk, carried=carried)
            if merged is None:
                pending = True
                break
            story = merged
            progressed = True
            through = chunk[-1].get('at') or chunk[-1].get('saved_at') or through
        return (story if progressed else ''), pending, through

    def _story_batches(self, stages, budget=None):
        """Stage groups small enough for one request. An oversized stage is split at paragraph/sentence
        boundaries and marked with its part number; nothing is dropped and no JSON is ever cut."""
        budget = int(budget or self.settings.get('story_batch_chars') or 12000)
        budget = max(1000, budget)
        pieces = []
        for stage in stages:
            text = str(stage.get('text') or '')
            if len(text) <= budget:
                pieces.append(dict(stage))
                continue
            parts = self._split_text(text, budget)
            for index, part in enumerate(parts):
                piece = {**stage, 'text': part, 'facts': facts(part),
                         'part': f'{index + 1}/{len(parts)}'}
                if index:
                    piece.pop('interpretation', None)
                pieces.append(piece)
        batches, current, used = [], [], 0
        for piece in pieces:
            size = len(str(piece.get('text') or ''))
            if current and used + size > budget:
                batches.append(current)
                current, used = [], 0
            current.append(piece)
            used += size
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _split_text(text, budget):
        """Whole paragraphs first, then whole sentences; only a single sentence over budget is cut."""
        parts, current = [], ''
        for block in (text or '').split('\n'):
            block = block.strip()
            if not block:
                continue
            if len(current) + len(block) + 1 <= budget:
                current = f'{current}\n{block}'.strip()
                continue
            if current:
                parts.append(current)
                current = ''
            if len(block) <= budget:
                current = block
                continue
            for sentence in re.split(r'(?<=[。！？.!?])\s*', block):
                if not sentence:
                    continue
                if len(current) + len(sentence) <= budget:
                    current += sentence
                else:
                    if current:
                        parts.append(current)
                    while len(sentence) > budget:
                        parts.append(sentence[:budget])
                        sentence = sentence[budget:]
                    current = sentence
        if current:
            parts.append(current)
        return parts or [text[:budget]]

    def _story_tokens(self, chunk):
        material = sum(len(str(stage.get('text') or '')) for stage in chunk)
        return max(1200, min(8000, material // 2))

    def _merge_story_chunk(self, previous, chunk, carried=None):
        """One model call over one batch. None when the answer is unusable or drops a fact.

        `carried` holds pseudo-stages for facts already accepted in `previous`, so a later merge may
        not silently rewrite the earlier narrative without them (P6-S2 review).
        """
        prompt_id = 'memory.event.story.merge' if previous else 'memory.event.story'
        payload = {
            'existing_story': previous,
            'stages': [{'at': stage.get('at'), 'text': stage.get('text'),
                        'his_reading': stage.get('interpretation'),
                        **({'part': stage['part']} if stage.get('part') else {})} for stage in chunk],
            'beliefs': [{'at': stage.get('at'), 'belief': stage.get('interpretation'),
                         'valid_from': stage.get('at'), 'invalid_at': stage.get('invalid_at')}
                        for stage in chunk if stage.get('interpretation')],
        }
        check_stages = list(chunk) + list(carried or [])
        persona = str(getattr(self.cfg, 'persona', '') or '')
        messages = [{'role': 'system', 'content': prompts.text(prompt_id)}]
        if persona:
            messages.append({'role': 'system', 'content': persona})
        messages.append({'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)})
        max_tokens = self._story_tokens(chunk)
        for _ in range(2):
            story, finish = self._call_story_model(messages, max_tokens)
            if not story or finish == 'length':
                continue
            missing = self._missing_facts(check_stages, story)
            if not missing:
                return story
            messages.append({'role': 'user', 'content': 'Keep every one of these exactly: ' + ', '.join(missing[:20])})
        return None

    def _call_story_model(self, messages, max_tokens):
        try:
            request_cfg = replace(self.cfg, max_output_tokens=max_tokens)
            reservation = amount = None
            reservation, amount = self._reserve_model(request_cfg, 'event-story')
            result = self.model(messages, max_tokens=max_tokens, detail=True)
            output = result[0] if isinstance(result, tuple) else result
            finish = result[4] if isinstance(result, tuple) and len(result) > 4 else None
            if reservation is not None:
                try:
                    prompt_tokens = int(result[1]) if isinstance(result, tuple) and len(result) > 1 else 0
                    completion_tokens = int(result[2]) if isinstance(result, tuple) and len(result) > 2 else 0
                    cached_tokens = int(result[3]) if isinstance(result, tuple) and len(result) > 3 else 0
                    self.budget.settle(reservation, request_cfg, prompt_tokens, completion_tokens,
                                       cached_tokens, accounting='event-story',
                                       category=self.cost_category or 'event-story')
                except Exception:
                    pass
            return ((output or '').strip() if isinstance(output, str) else ''), finish
        except Exception:
            return '', None

    @staticmethod
    def _month_day_set(text):
        """Normalize date expressions like 11-25, 11/25, 11月25日, 2026-11-25, Nov 25 into (month, day)."""
        return {(month, day) for month, day, _year, _span in EventMemory._date_parts(text)}

    @staticmethod
    def _date_parts(text):
        return _extract_date_parts(text)

    @classmethod
    def _independent_numbers(cls, text):
        """Numbers that stand on their own, excluding the digits inside a date expression.

        ``On 11-25 we ordered 25 tickets`` keeps an independent 25: a draft that converts the date
        format but drops the ticket count must not pass (P6-S2 review).
        """
        text = text or ''
        if not text:
            return set()
        blanked = list(text)
        for _month, _day, _year, (start, end) in cls._date_parts(text):
            blanked[start:end] = ' ' * (end - start)
        return set(_NUMBER.findall(''.join(blanked)))

    @classmethod
    def _missing_facts(cls, stages, story):
        missing = []
        story_text = story or ''
        story_numbers = set(_NUMBER.findall(story_text))
        story_free_numbers = cls._independent_numbers(story_text)
        story_dates = cls._date_parts(story_text)
        for stage in stages:
            extra = stage.get('facts') if isinstance(stage.get('facts'), dict) else {}
            independent = cls._independent_numbers(str(stage.get('text') or ''))
            matched_date_tokens = set()
            missing_date_numbers = set()
            for value in (extra.get('dates') or []):
                sval = str(value)
                if not sval:
                    continue
                if cls._contains_number_token(story_text, sval):
                    for num in _NUMBER.findall(sval):
                        matched_date_tokens.add(num)
                    continue
                parts = cls._date_parts(sval)
                matched = [part for part in parts
                           if any(part[0] == smonth and part[1] == sday
                                  and (part[2] is None or syear == part[2])
                                  for smonth, sday, syear, _span in story_dates)]
                if not parts or len(matched) != len(parts):
                    missing.append(sval)
                    missing_date_numbers.update(_NUMBER.findall(sval))
                    continue
                for month, day, year, _span in matched:
                    matched_date_tokens.add(str(month))
                    matched_date_tokens.add(str(day))
                    # A known year must survive a format change: '2026-11-25' may become
                    # '2026年11月25日', but never '2025-11-25' or a yearless date that drops it.
                    if year is not None and any(
                            syear == year for smonth, sday, syear, _span in story_dates
                            if smonth == month and sday == day):
                        matched_date_tokens.add(str(year))
            # Only the digits that belong to a date may be exempted; the same value written as an
            # independent quantity stays required.
            matched_date_tokens -= independent
            for value in (extra.get('files') or []):
                if str(value) and str(value) not in story_text:
                    missing.append(str(value))
            for value in extra.get('numbers') or []:
                sval = str(value)
                if sval in matched_date_tokens or sval in missing_date_numbers:
                    continue
                if sval in story_numbers:
                    # A value that only occurs inside a date in the story cannot satisfy an
                    # independent quantity from the stage (P6-S2 review).
                    if sval in independent and sval not in story_free_numbers:
                        missing.append(sval)
                    continue
                missing.append(sval)
        return list(dict.fromkeys(missing))

    @staticmethod
    def _contains_number_token(text, value):
        """True when `value` occurs in `text` as a whole token, not as part of a longer number."""
        if not value:
            return False
        if not text:
            return False
        return re.search(r'(?<!\d)' + re.escape(value) + r'(?!\d)', text) is not None

    def _belief_history(self, event_id, extra=None):
        history = []
        for stage in self.stages(event_id) + ([extra] if extra else []):
            belief = stage.get('interpretation')
            if not belief:
                continue
            if history and history[-1]['belief'] != belief:
                history[-1]['invalid_at'] = stage.get('at') or stage.get('saved_at')
            history.append({'at': stage.get('at'), 'valid_from': stage.get('at') or stage.get('saved_at'),
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
        limit = int(self.settings.get('summary_chars') or 0)
        if limit <= 0:
            return text
        limit = max(120, limit)
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
            self._last_projection_pending = '检索索引待重试' in str(result)
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
            if getattr(self.memory, 'brain_authority', None) is None:
                # Legacy fake-backend path only; BrainAuthority.trace writes a single versions row.
                self.store.append(f'{self.cfg.id}/memory-versions.jsonl', {
                    'memory_id': memory_id, 'fields': ['content'],
                    'operation_reason': '事件故事重写', 'why_remembered': '', 'old_content': old})
        try:
            result = self.memory.call('trace', {'bucket_id': memory_id, 'content': text})
            self._last_projection_pending = '检索索引待重试' in str(result)
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
            projection = self._projection(story)
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
            memory_id = event.get('memory_id')
            if memory_id and callable(self.locked):
                try:
                    if self.locked(memory_id):
                        # Ticket 453: keep the reviewed story and its pending flags for the unlock.
                        # Rebuilding here would pay for a story that must not be persisted and would
                        # clear `dirty`, so the stage could never be projected after Lan unlocks.
                        continue
                except Exception:
                    continue
            try:
                story = event.get('story') or self._story(event['event_id'])
                if event.get('story_pending'):
                    # Ticket 421: finish the narrative before projecting it; a pending rebuild keeps the
                    # previous good story and waits for the next pass instead of writing a partial one.
                    rebuilt, source_name, through, pending = self._write_story(
                        self.stages(event['event_id']), story, event)
                    if pending:
                        if self._model_budget_blocked:
                            continue
                        rebuilt, source_name = self._story(event['event_id']), 'composed'
                        through = (self.stages(event['event_id'])[-1].get('at')
                                   or self.stages(event['event_id'])[-1].get('saved_at'))
                    story, event = rebuilt, dict(event)
                    event.update({'story': story, 'story_source': source_name,
                                  'story_through': through, 'story_pending': False,
                                  'story_coverage': [row.get('stage_id') for row in self.stages(event['event_id'])
                                                     if row.get('stage_id')],
                                  'belief_history': self._belief_history(event['event_id'])})
                memory_id, changed, locked = self._write_projection(
                    event, self._projection(story), event.get('title') or 'Event')
                if locked and memory_id and callable(self.locked) and self.locked(memory_id):
                    # Ticket 453: a lock taken between the check above and the write must keep the
                    # reviewed story and pending flags; retry this event after the unlock.
                    continue
                if changed or locked:
                    event['memory_id'] = memory_id or event.get('memory_id')
                    event['dirty'] = bool(event.get('story_pending'))
                    if not locked:
                        event.pop('pending_review', None)
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
                    self.memory.call('trace', {'bucket_id': memory_id, 'archive': True})
                except Exception:
                    pass
            self.store.append(self.events_path, event)
            return event
        event.update({'story': self._story(event_id), 'stage_count': len(stages),
                      'story_coverage': [stage.get('stage_id') for stage in stages if stage.get('stage_id')],
                      'belief_history': self._belief_history(event_id), 'dirty': True,
                      'last_story_at': datetime.now().astimezone().isoformat()})
        self.store.append(self.events_path, event)
        return event

    @_serialized
    def move_stage(self, stage_id, to_event_id, why='纠正归组'):
        stage = self._stage_rows().get(stage_id)
        target = self.events().get(to_event_id)
        if (not stage or stage.get('state') != 'committed' or not target
                or target.get('status') == 'merged' or not self._visible(target)):
            return False
        source = self.events().get(stage.get('event_id')) or {}
        if not source or not self._visible(source):
            return False
        source_id = stage.get('event_id')
        if source_id == to_event_id:
            return False
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
        if not event or not stage_ids or not self._visible(event) or self._lan_edited(event):
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
        state = self._dream_state(strict=True)
        if state is _DREAM_UNREADABLE:
            return 0  # A failed read cannot prove there is no lease; skip this tick.
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

    def _dream_state(self, strict=False):
        path = f'{self.cfg.id}/event-dream.json'
        reader = getattr(self.store, 'read_state', None)
        if callable(reader):
            try:
                state = reader(path)
                if state is None:
                    return {}
                return state if isinstance(state, dict) else (
                    _DREAM_UNREADABLE if strict else {})
            except Exception:
                return _DREAM_UNREADABLE if strict else {}
        try:
            return json.loads((self.store.root / path).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return _DREAM_UNREADABLE if strict else {}

    def dream_state(self):
        state = self._dream_state()
        return {} if state is _DREAM_UNREADABLE else state

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
    def _explicit_user_quotes(self, stage):
        source = stage.get('source') if isinstance(stage.get('source'), dict) else {}
        wanted = {item for item in (source.get('message_ids') or [])
                  if isinstance(item, str) and item}
        if not wanted:
            return []
        try:
            rows = self.store.read(f'{self.cfg.id}/chat.jsonl')
        except Exception:
            return []
        quotes = []
        for row in rows:
            message_id = row.get('message_id') or row.get('id')
            text = row.get('content') or row.get('text')
            if (message_id in wanted and row.get('role') == 'user'
                    and isinstance(text, str) and text.strip()):
                quotes.append({'message_id': message_id, 'speaker': 'user',
                               'text': ' '.join(text.split())})
        return quotes

    def _stage_evidence_ledger(self, stage, event_id, collected, explicit_quotes, saved_at):
        """Ledger rows and user quotes for one stage; shared by record() and retry_pending()."""
        stage_id = stage.get('stage_id')
        at = stage.get('at') or (saved_at.isoformat() if saved_at is not None else '')
        rows = [('stage-evidence', {**item, 'stage_id': stage_id, 'event_id': event_id})
                for item in collected]
        explicit_ids = ((stage.get('source') or {}).get('message_ids')
                        if isinstance(stage.get('source'), dict) else [])
        if explicit_ids:
            resolved_ids = {item['message_id'] for item in explicit_quotes}
            rows.append(('stage-evidence', {
                'at': at, 'stage_id': stage_id, 'event_id': event_id, 'kind': 'chat-message-ids',
                'message_ids': list(dict.fromkeys(explicit_ids)),
                'unresolved_message_ids': [mid for mid in dict.fromkeys(explicit_ids)
                                           if mid not in resolved_ids],
                'text': ''}))
            for quote in explicit_quotes:
                rows.append(('stage-evidence', {
                    'at': at, 'stage_id': stage_id, 'event_id': event_id, 'kind': 'chat',
                    'role': 'user', 'message_id': quote['message_id'], 'text': quote['text']}))
        quotes = []
        quote_seen = set()
        for quote in ([{'speaker': 'user', 'text': item.get('text')}
                       for item in collected
                       if item.get('kind') == 'chat' and item.get('role') == 'user' and item.get('text')]
                      + [{'speaker': 'user', 'text': item['text']} for item in explicit_quotes]):
            key = (quote['speaker'], quote['text'])
            if key not in quote_seen:
                quote_seen.add(key)
                quotes.append(quote)
        return rows, quotes

    def _capture_evidence(self, stage, *, persist=True):
        now = _when(stage.get('at')) or _when(stage.get('saved_at')) or datetime.now().astimezone()
        cutoff = now - timedelta(minutes=max(1, self.settings['evidence_minutes']))
        by_kind = {'chat': [], 'tool': [], 'autonomy': []}
        for kind, path in (('chat', f'{self.cfg.id}/chat.jsonl'),
                           ('tool', f'{self.cfg.id}/tools.jsonl'),
                           ('autonomy', f'{self.cfg.id}/autonomy.jsonl')):
            try:
                rows = self.store.read_tail(path, 40)
            except Exception:
                continue
            for row in rows:
                stamp = _when(row.get('time'))
                # P6-S2 review: nothing that happened after the stage can be its evidence. Keep the
                # earlier cutoff and also refuse a row newer than the stage's own time.
                if stamp is None or stamp < cutoff or stamp > now:
                    continue
                text = row.get('content') if kind == 'chat' else (
                    row.get('detail') or row.get('path') or row.get('text') or row.get('summary'))
                if not isinstance(text, str) or not text.strip():
                    continue
                item = {'kind': kind, 'at': stamp.isoformat(),
                        'saved_at': datetime.now().astimezone().isoformat(),
                        'message_id': row.get('message_id') or row.get('call_id') or row.get('id'),
                        'text': ' '.join(text.split())[:self.settings['evidence_chars']]}
                if kind == 'chat' and isinstance(row.get('role'), str):
                    item['role'] = row['role']
                by_kind[kind].append(item)
        # Guarantee chat evidence is never squeezed out by high-frequency tool calls:
        # reserve per-kind quotas (prioritizing user chat messages) then fill remaining slots chronologically.
        limit = max(1, self.settings['evidence_items'])
        user_chat = [item for item in by_kind['chat'] if item.get('role') == 'user']
        chat_pick = (user_chat[-3:] if user_chat else by_kind['chat'][-3:])
        # Deduplicate while preserving order
        seen_keys = set()
        picked = []
        for pool in (chat_pick, by_kind['tool'][-2:], by_kind['autonomy'][-1:]):
            for item in pool:
                key = (item['kind'], item.get('message_id'), item['at'])
                if key not in seen_keys and len(picked) < limit:
                    seen_keys.add(key)
                    picked.append(item)
        rest = sorted(by_kind['chat'] + by_kind['tool'] + by_kind['autonomy'], key=lambda x: x['at'])
        for item in reversed(rest):
            if len(picked) >= limit:
                break
            key = (item['kind'], item.get('message_id'), item['at'])
            if key not in seen_keys:
                seen_keys.add(key)
                picked.append(item)
        # Also record explicit chat message_ids from stage.source (e.g., day_end running hours after chat).
        src = stage.get('source') if isinstance(stage.get('source'), dict) else {}
        for mid in (src.get('message_ids') or []):
            if len(picked) >= limit:
                break
            if not isinstance(mid, str) or not mid:
                continue
            if any(item.get('kind') == 'chat' and item.get('message_id') == mid for item in picked):
                continue
            picked.append({'kind': 'chat', 'at': stage.get('at') or now.isoformat(),
                           'saved_at': datetime.now().astimezone().isoformat(),
                           'message_id': mid, 'text': ''})
        picked.sort(key=lambda x: x['at'])
        if persist:
            for item in picked:
                row = {**item, 'stage_id': stage['stage_id'], 'event_id': stage['event_id']}
                self.store.append(self.evidence_path, row)
        return picked
