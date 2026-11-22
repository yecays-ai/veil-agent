"""Ticket 354: the program's fallback for Peter's memory of what he and Lan went through.

When enough new visible chat has piled up and the two of them have gone quiet, the chat model (with
Peter's persona) reads only the messages they actually sent each other and writes down, in his own
casual first person, what he would remember: what happened and the attitude he showed at the time.
He may also decide that nothing is worth keeping. Entries are saved into Ombre as ordinary memories.
When the chat continues something he already remembers (an entry written here, or any memory the library
search finds), that memory is rewritten whole instead of a new one being added (tickets 363 and 364); the
old version stays in the version record for Lan.

Borrowed, not copied: the first-person recollection prompt and "the model decides create / update /
nothing" from the old veil phone (lib/memory/summary-input.ts, maintenance), the cursor / attempt /
checkpoint ledger and budget handling from short_term_notes.py. SillyTavern's MessageSummarize warns that
a summary the model keeps regenerating drifts; Lan chose rewriting anyway (2026-11-15), so every rewrite
keeps the old version and she can lock a memory.
"""
import json
import hashlib
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import prompts

import prompts

from agent_types import RequestFailed, UserError
from short_term_notes import _parse_json_object
from memory_agent import SEARCH_SPARE_HITS, ordinary_hits
from memory_sidecar import Sidecar

COST_CATEGORY = 'experience_memory'
COST_ACCOUNTING = 'experience-memory'
TICK_SECONDS = 300
MAX_ENTRIES = 2           # 2026-11-15 Lan: small things may simply be forgotten
MAX_UPDATES = 3
TITLE_CHARS = 60
ENTRY_CHARS = 0           # ticket 421: no fixed cap; the model's output budget is the real limit
UPDATE_CHARS = 0          # ticket 421: an update rewrites the whole memory, however long it is
MESSAGE_CHARS = 1500      # one chunk of a pasted wall; long messages are split, never clipped
REQUEST_CHARS = 12000     # visible chat text per model call; the rest waits for the next tick
MAX_OUTPUT_TOKENS = 8000  # ticket 386: a thinking model's reasoning counts here too; at 3000 deepseek-flash often
                          # thought until the limit and never wrote the entries (7 failures in a row on 11-16)
FAILURES_PATH_SUFFIX = 'experience-memory-failures.jsonl'
FAILURES_KEPT = 20        # ticket 386: what the model actually sent back when a run failed, newest kept
FAILURE_CHARS = 4000      # of each reply kept there
BACKOFF = (timedelta(minutes=30), timedelta(hours=2))  # ticket 386: after the 1st and 2nd failure in a row; from
                          # the 3rd on, the next try waits for the next day. The chat is never skipped.
CANDIDATES = 12           # his latest entries offered for an update
LIBRARY_QUERIES = 4       # 2026-11-15: the chat is also searched against the whole memory library, in this many
LIBRARY_QUERY_CHARS = 800 # pieces of this size, so a memory Peter saved himself can be updated too
LIBRARY_HITS = 6          # at most this many library memories are added to the candidates
LOOKS = 2                # ticket 368: at most this many more asks after the first answer (the 2nd shows the exact problem again)
STAGE_HITS = 4            # ticket 368: and this many more, the other stages of an experience a hit belongs to
OWN_SAVES = 3             # ticket 368: and the memories Peter saved or rewrote himself most recently
CANDIDATE_CHARS = 6000    # ticket 421: the model must see the whole old memory before rewriting it; longer
                          # ones are marked "too long to rewrite" (it may still continue them as a stage)
FIX_MARGIN = 100          # ticket 386: an overlong draft is asked for about this much under the cap
VERBATIM_RUN = 24         # an entry repeating this many characters of a message in a row is a quote, not a memory
BACKFILL = timedelta(days=3)
DEFAULTS = {'enabled': False, 'min_chars': 3000, 'floor_chars': 200, 'idle_minutes': 30,
            'day_end': '23:30', 'day_end_idle_minutes': 10, 'daily_budget_cny': Decimal('1'),
            'recent_days': 3, 'recent_chars': 1500}


# Conservative local filter (same idea as the old veil phone's sensitiveMemoryText), not a promise to catch every secret.
SECRET = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+\S+|'
                    r'(?:api[_ -]?key|password|access[_ -]?token|密码|密钥)\s*[:=：]\s*\S+)', re.I)

# Import-time copy for tests only; context() reads the live text.
CONTEXT_HEADING = prompts.text('memory.experience.heading')


def _when(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def load_settings(raw):
    """Validated [memory.experience] table; unknown keys are refused so a typo cannot silently do nothing."""
    settings = dict(DEFAULTS)
    if raw is None:
        return settings
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError('memory.experience has unknown keys')
    for key, value in raw.items():
        if key == 'enabled':
            if type(value) is not bool:
                raise ValueError('memory.experience.enabled must be boolean')
        elif key == 'day_end':
            datetime.strptime(value, '%H:%M')
        elif key == 'daily_budget_cny':
            value = Decimal(str(value))
            if not value.is_finite() or value < 0:
                raise ValueError('memory.experience.daily_budget_cny must not be negative')
        elif type(value) is not int or value < 0:
            raise ValueError(f'memory.experience.{key} must be a whole number')
        settings[key] = value
    return settings


class ExperienceMemory:
    def __init__(self, cfg, store, budget, model, chat_path, memory, settings):
        self.cfg, self.store, self.budget, self.model = cfg, store, budget, model
        self.chat_path, self.memory, self.settings = chat_path, memory, settings
        self.path = f'{cfg.id}/experience-memory.jsonl'
        self.switch_path = f'{cfg.id}/experience-memory.json'
        self.failures_path = f'{cfg.id}/{FAILURES_PATH_SUFFIX}'
        self._worst = None      # the most one call may cost, set by _digest; the cut-off retry checks it
        self._retried = 0       # cut-off answers asked again without thinking during this run
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._context_cache = None
        self.sidecar = Sidecar(store, cfg.id, budget.clock)
        # Ticket 407: automatic tidy writes go through the same event layer as Peter's own saves.
        self.events = None
        event_settings = (getattr(cfg, 'memory', None) or {}).get('events')
        if isinstance(event_settings, dict) and event_settings.get('enabled'):
            from event_memory import EventMemory
            self.events = EventMemory(memory, event_settings, model=model, sidecar=self.sidecar,
                                      budget=budget, cost_category=COST_CATEGORY,
                                      daily_cap=self.settings.get('daily_budget_cny', DEFAULTS['daily_budget_cny']))
        if getattr(self.budget, 'reconcile_unsettled', None):
            self.budget.reconcile_unsettled({COST_ACCOUNTING})

    # --- Lan's switch -------------------------------------------------------------------------
    def enabled(self):
        try:
            state = json.loads((self.store.root / self.switch_path).read_text(encoding='utf-8'))
            if isinstance(state, dict) and type(state.get('enabled')) is bool:
                return state['enabled']
        except (OSError, ValueError):
            pass
        return bool(self.settings['enabled'])

    def set_enabled(self, enabled):
        self.store.replace(self.switch_path, {'enabled': bool(enabled)})
        self._context_cache = None
        return self.enabled()

    # --- scheduling ---------------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f'{self.cfg.id}-experience-memory', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def _run(self):
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.run_if_due()
            except Exception:
                # A fallback memory writer must never take chat down with it; the ledger keeps the reason.
                pass

    def _event_maintenance(self):
        """Ticket 407: the existing tidy also repairs event projections and merges provable duplicates."""
        settings = (getattr(self.cfg, 'memory', None) or {}).get('events')
        if not isinstance(settings, dict) or not settings.get('enabled'):
            return 0
        try:
            from event_memory import EventMemory
            events = EventMemory(self.memory, settings, model=self.model, sidecar=self.sidecar,
                                 budget=self.budget, cost_category=COST_CATEGORY,
                                 daily_cap=self.settings.get('daily_budget_cny', DEFAULTS['daily_budget_cny']))
            events.locked = self.sidecar.locked
            return events.dream_pass('tidy')
        except Exception:
            return 0

    def _now(self):
        return _when(self.budget.clock()) or datetime.now().astimezone()

    def _events(self):
        return self.store.read(self.path)

    def _visible(self, rows):
        """Only what the two of them sent each other: no recalled messages, cards, tool rows or empty text."""
        out = []
        for index, row in enumerate(rows):
            content = row.get('content') if isinstance(row, dict) else None
            if (row.get('role') not in ('user', 'assistant') or not isinstance(content, str) or not content.strip()
                    or row.get('recalled') or row.get('virtual_card') or row.get('virtual_card_event')):
                continue
            stamp = _when(row.get('time'))
            if stamp is None:
                continue
            item = {'position': index, 'id': row.get('message_id'), 'time': stamp,
                    'role': row['role'], 'text': ' '.join(content.split())}
            if row.get('autonomy_run_id'):
                # Ticket 360: sent on his own during an autonomous wake, not part of a conversation.
                item['own'] = (str(row['autonomy_origin']) if isinstance(row.get('autonomy_origin'), str)
                               and row['autonomy_origin'] else True)
            out.append(item)
        return out

    def _cursor(self, rows, events):
        """Index of the first chat row not yet looked at."""
        for event in reversed(events):
            if event.get('kind') in ('checkpoint', 'start'):
                message_id, position = event.get('through_message_id'), event.get('through_position')
                if isinstance(message_id, str):
                    for index in range(len(rows) - 1, -1, -1):
                        if rows[index].get('message_id') == message_id:
                            return index + 1
                return min(position, len(rows)) if type(position) is int and position >= 0 else len(rows)
        # First run: only the last few days, never the whole history.
        cutoff = self._now() - BACKFILL
        start = len(rows)
        for index in range(len(rows) - 1, -1, -1):
            stamp = _when(rows[index].get('time'))
            if stamp is not None and stamp < cutoff:
                break
            start = index
        self.store.append(self.path, {'kind': 'start', 'through_position': start,
                                      'through_message_id': rows[start - 1].get('message_id') if start else None,
                                      'time': self.budget.clock()})
        return start

    def pending(self):
        rows = self.store.read(self.chat_path)
        events = self._events()
        start = self._cursor(rows, events)
        progress = self._chunk_progress(events)
        out = []
        for item in self._visible(rows):
            if item['position'] < start:
                continue
            chunks = self._message_chunks(item)
            skip = 0
            if (progress and progress.get('message_id') == item.get('id')
                    and progress.get('position') == item.get('position')):
                skip = max(0, int(progress.get('next_chunk') or 0))
            out.extend(chunks[skip:])
        return out

    def _chunk_progress(self, events):
        """A partially processed long message, only when no later checkpoint has passed it."""
        for event in reversed(events):
            if event.get('kind') == 'chunk-progress':
                return event
            if event.get('kind') in ('checkpoint', 'start'):
                return None
        return None

    @staticmethod
    def _split_message(text, limit):
        """Split at paragraphs/sentences; hard-cut only when there is no boundary. Never drops text."""
        text = str(text or '')
        if len(text) <= limit:
            return [text]
        pieces, rest = [], text
        while len(rest) > limit:
            cut = rest.rfind('\n', 0, limit + 1)
            for mark in '。！？.!?':
                pos = rest.rfind(mark, 0, limit + 1)
                if pos + 1 > cut:
                    cut = pos + 1
            if cut < limit // 3:
                cut = limit
            pieces.append(rest[:cut].rstrip() or rest[:cut])
            rest = rest[cut:].lstrip('\n')
        if rest:
            pieces.append(rest)
        return pieces or [text]

    def _message_chunks(self, item):
        limit = max(200, MESSAGE_CHARS)
        parts = self._split_message(item['text'], limit)
        if len(parts) == 1 and len(item['text']) <= limit:
            return [dict(item, chunk=0, chunks=1)]
        return [{**item, 'text': part, 'chunk': index, 'chunks': len(parts)}
                for index, part in enumerate(parts)]

    def due(self, pending=None):
        """Volume first, then time. Returns a reason or ''."""
        pending = self.pending() if pending is None else pending
        if not pending:
            return ''
        now = self._now()
        chars = sum(len(item['text']) for item in pending)
        idle = now - pending[-1]['time']
        if chars >= self.settings['min_chars'] and idle >= timedelta(minutes=self.settings['idle_minutes']):
            return 'volume'
        if chars < self.settings['floor_chars']:
            return ''
        end = datetime.strptime(self.settings['day_end'], '%H:%M').time()
        local_now, last = now.astimezone(pending[-1]['time'].tzinfo), pending[-1]['time']
        day_closing = local_now.date() > last.date() or local_now.time() >= end
        if day_closing and idle >= timedelta(minutes=self.settings['day_end_idle_minutes']):
            return 'day_end'
        return ''

    def retry_at(self, events=None):
        """Ticket 386: after failures in a row, the earliest time the same chat is tried again, or None."""
        failures = []
        for event in reversed(self._events() if events is None else events):
            if event.get('kind') in ('checkpoint', 'start'):
                break
            if event.get('kind') == 'error':
                failures.append(event)
        last = _when(failures[0].get('time')) if failures else None
        if last is None:
            return None
        if len(failures) <= len(BACKOFF):
            return last + BACKOFF[len(failures) - 1]
        local = last.astimezone(self._now().tzinfo)
        return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=local.tzinfo)

    def _record_failure(self, stage, what, **fields):
        """Ticket 386: keep what the model actually answered when a run fails, on Lan's computer only (the newest
        FAILURES_KEPT). Never lets a logging problem take the run down."""
        try:
            for key in ('output', 'draft'):
                if isinstance(fields.get(key), str):
                    fields[key] = fields[key][:FAILURE_CHARS]
            self.store.append(self.failures_path, {
                'time': self.budget.clock(), 'stage': stage, 'what': what,
                'model': str((self.cfg.provider or {}).get('model') or ''), **fields})
            rows = self.store.read(self.failures_path)
            if len(rows) > FAILURES_KEPT:
                self.store.remove_records(self.failures_path, range(len(rows) - FAILURES_KEPT))
        except Exception:
            pass

    def failures(self):
        try:
            return self.store.read(self.failures_path)
        except Exception:
            return []

    def spent_today(self):
        day, waiting = self.budget.category_totals(self.cfg.id, None, COST_CATEGORY)
        return day + waiting

    # --- one run ------------------------------------------------------------------------------
    def run_if_due(self, force=False):
        """Returns 'off', 'not_due', 'waiting', 'budget', 'skipped', or 'written'."""
        if self.memory is None:
            return 'off'
        # Ticket 407: event recovery is independent of the chat-tidy switch; a closed tidy must not
        # freeze a dirty or pending event forever.
        self._event_maintenance()
        if not self.enabled():
            return 'off'
        try:
            self._apply_deferred()
        except Exception:
            pass
        with self._lock:
            pending = self.pending()
            reason = 'forced' if force and pending else self.due(pending)
            if not reason:
                return 'not_due'
            retry_at = self.retry_at()
            if not force and retry_at is not None and self._now() < retry_at:
                # Ticket 386: a failed run is not asked again every 5 minutes (each try costs money).
                return 'waiting'
            batch, used = [], 0
            for item in pending:
                text = item['text']          # already a bounded chunk; never clip a message
                if batch and used + len(text) > REQUEST_CHARS:
                    break
                batch.append({**item, 'text': text})
                used += len(text)
            return self._digest(batch, reason)

    def _alive(self, memory_id):
        """Still stored and not hidden, archived or deleted (a memory Lan deleted is simply gone)."""
        return self.memory.raw_content_for(memory_id) is not None and self.memory.refresh_visibility(memory_id)

    def _event_status(self, memory_id):
        """Ticket 421 review: a closed or merged experience must not be offered for a rewrite."""
        events = getattr(self, 'events', None)
        if events is None:
            return ''
        try:
            event_id = events.event_for_memory(memory_id)
            event = (events.events() or {}).get(event_id) if event_id else None
            return str((event or {}).get('status') or '')
        except Exception:
            return ''

    def _offer(self, memory_id):
        """One candidate as the model sees it, read fresh from the library: its text, how often it was rewritten and
        the other stages it is linked to. None for what is gone, hidden, archived or locked by Lan. A memory too long
        to rewrite whole is shown clipped and marked: it can be continued but not rewritten."""
        try:
            if not self._alive(memory_id) or self.sidecar.locked(memory_id):
                return None
            if self._event_status(memory_id) in ('merged', 'closed'):
                return None
            body = self.memory.content_for(memory_id, refresh=True)
            if not body:
                return None
            versions = sum(1 for row in self.store.read(f'{self.cfg.id}/memory-versions.jsonl')
                           if row.get('memory_id') == memory_id)
        except Exception:
            return None
        titled = getattr(self.memory, 'title_for', None)
        stages = getattr(self.memory, 'experience_stages', None)
        item = {'id': memory_id, 'title': (titled(memory_id) if titled else '') or '', 'written': '',
                'text': body[:CANDIDATE_CHARS], 'whole': len(body) <= CANDIDATE_CHARS, 'versions': versions,
                'full_chars': len(body),
                **({'text_truncated': True} if len(body) > CANDIDATE_CHARS else {}),
                'stages': [{'id': other, 'title': (titled(other) if titled else '') or ''}
                           for other in (stages(memory_id) if stages else [])][:4]}
        return item

    def _own_saves(self):
        """Memories Peter saved or rewrote himself in the last few days, newest first: the sidecar notes them, and the
        vault's own creation times find the ones saved before the sidecar began noting (ticket 368)."""
        try:
            since = datetime.fromisoformat(self.budget.clock()) - BACKFILL
        except ValueError:
            return []
        found = []
        try:
            rows = []
            for memory_id, note in self.sidecar.all().items():
                if note.get('merged_into'):
                    continue
                times = [source['time'] for source in note.get('sources', [])
                         if source.get('via') in ('hold', 'rewrite') and source.get('time')
                         and datetime.fromisoformat(source['time']) >= since]
                if times:
                    rows.append((max(times), memory_id))
            found = [memory_id for _, memory_id in sorted(rows, reverse=True)]
        except Exception:
            pass
        recent = getattr(self.memory, 'recently_created', None)
        for memory_id in (recent(since) if recent else []):
            if memory_id not in found:
                found.append(memory_id)
        return found

    def _search_ids(self, query):
        # Ticket 384: ask for spare hits and drop archived, hidden or deleted ones before keeping three (as in 381).
        try:
            hits = ordinary_hits(self.memory.call('breath_search', {
                'query': query, 'mode': 'automatic', 'with_ids': True, 'max_results': 3 + SEARCH_SPARE_HITS}),
                limit=3 + SEARCH_SPARE_HITS)
        except Exception:
            return []  # a failed search only means fewer candidates
        found = []
        for memory_id, _ in hits:
            try:
                if self._alive(memory_id):
                    found.append(memory_id)
            except Exception:
                continue  # an unreadable one is simply not offered
            if len(found) == 3:
                break
        return found

    def _library(self, batch, skip):
        """2026-11-15 (Lan): memories anywhere in the library about what these messages talk about, so the same
        matter is rewritten instead of saved again. Ticket 368 adds, without another model call: the other stages of
        an experience a hit belongs to, and what Peter himself saved lately. Locked memories are never offered."""
        text = '\n'.join(item['text'] for item in batch)
        step = max(LIBRARY_QUERY_CHARS, -(-len(text) // LIBRARY_QUERIES))
        found = []
        for start in range(0, len(text), step):
            query = text[start:start + LIBRARY_QUERY_CHARS].strip()
            if query:
                found += [memory_id for memory_id in self._search_ids(query)
                          if memory_id not in skip and memory_id not in found]
        out, taken = [], set(skip)

        def add(memory_id, limit):
            if memory_id in taken or len(out) >= limit:
                return
            item = self._offer(memory_id)
            taken.add(memory_id)
            if item:
                out.append(item)
        for memory_id in found:
            add(memory_id, LIBRARY_HITS)
        hits = len(out)
        for item in list(out):
            for stage in item['stages']:
                add(stage['id'], hits + STAGE_HITS)
        own = 0
        for memory_id in self._own_saves():
            before = len(out)
            add(memory_id, len(out) + 1)
            own += len(out) - before
            if own >= OWN_SAVES:
                break
        return out

    def _fit_candidates(self, candidates):
        """Keep the whole old text when it fits the real request budget; clip only with an explicit marker,
        never silently (ticket 421)."""
        try:
            total = int((self.cfg.provider or {}).get('max_input_tokens') or 0)
        except (TypeError, ValueError):
            total = 0
        room = max(2000, (total or 30000) - REQUEST_CHARS - 4000)
        used = 0
        for item in candidates:
            left = max(0, room - used)
            text = str(item.get('text') or '')
            if len(text) > left:
                item['text'] = text[:left]
                item['whole'] = False
                item['text_truncated'] = True
            used += len(item.get('text') or '')
        return [item for item in candidates if item.get('text')]

    def _candidates(self, events):
        """His latest entries that still exist and are not hidden, newest first."""
        seen, out = set(), []
        for event in reversed(events):
            memory_id = event.get('memory_id')
            if event.get('kind') != 'entry' or not isinstance(memory_id, str) or memory_id in seen:
                continue
            seen.add(memory_id)
            item = self._offer(memory_id)
            if item is None:
                continue
            item['title'], item['written'] = event.get('title') or item['title'], str(event.get('time', ''))[:10]
            out.append(item)
            if len(out) >= CANDIDATES:
                break
        return out

    def _request_cfg(self, messages):
        """Reserve for this request's real size, not the chat model's whole window."""
        provider = dict(self.cfg.provider)
        size = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
        provider['max_input_tokens'] = min(provider['max_input_tokens'], size + 512)
        return replace(self.cfg, provider=provider, max_output_tokens=MAX_OUTPUT_TOKENS)

    def _request(self, messages, stage, thinking=None):
        """One model call: (text, finish_reason, completion_tokens)."""
        if self.spent_today() + self._worst_for(messages) > Decimal(str(self.settings['daily_budget_cny'])):
            raise UserError('经历整理今天的花费上限不够再发一次请求；聊天记录不受影响。')
        request_cfg = self._request_cfg(messages)
        reservation, amount = self.budget.reserve(request_cfg, accounting=COST_ACCOUNTING, category=COST_CATEGORY)
        try:
            # Called like a chat turn (no JSON mode): whichever chat model Lan picks, relays included, accepts
            # it. The reply is parsed leniently instead. Thinking is only switched off for the retry of a reply
            # that was cut off (ticket 386).
            result = self.model(messages, max_tokens=MAX_OUTPUT_TOKENS, detail=True,
                                **({'thinking': thinking} if thinking else {}))
            output, prompt_tokens, completion_tokens, cached_tokens = result[:4]
            finish = result[4] if len(result) > 4 else None
        except Exception as exc:
            self.budget.settle_estimate((reservation, amount), accounting=f'{COST_ACCOUNTING}-failed')
            self._record_failure(stage, 'request_failed', thinking=thinking or 'as_set', error=type(exc).__name__,
                                 detail=str(exc)[:300])
            if isinstance(exc, UserError):
                raise
            raise RequestFailed('经历整理请求失败；聊天记录不受影响，稍后再试。') from exc
        try:
            self.budget.settle(reservation, request_cfg, prompt_tokens, completion_tokens, cached_tokens,
                               accounting=COST_ACCOUNTING, category=COST_CATEGORY)
        except UserError:
            self.budget.settle_estimate((reservation, amount), accounting=f'{COST_ACCOUNTING}-usage-invalid')
        return output, finish, completion_tokens

    def _worst_for(self, messages):
        """The most one call with these messages may cost (as reserved), for this feature's own daily ceiling."""
        provider = self._request_cfg(messages).provider
        return provider.get('cny_per_call') or (
            provider['max_input_tokens'] * Decimal(str(provider['input_cny_per_million']))
            + MAX_OUTPUT_TOKENS * Decimal(str(provider['output_cny_per_million']))) / Decimal(1_000_000)

    def _call_model(self, messages, stage='first', expect=('entries', 'updates')):
        """The reply text. Ticket 386: a reply cut off before it holds a JSON object (a thinking model that used the
        whole output limit on its reasoning) is asked once more at once with thinking off; both are kept in the
        failure record, and one still unusable after that says plainly that it was cut off."""
        def usable(text):
            # Only the whole answer counts: a reply cut off inside its outer object can still hold a complete inner
            # one ({"title":..,"text":..}), which must not pass for an answer with nothing to keep (review of 386).
            payload = _parse_json_object(text)
            return isinstance(payload, dict) and any(key in payload for key in expect)

        if self._retried:
            # Already cut off once in this run: the fix asks go without thinking straight away (11-18 real run:
            # each fix was cut off again and needed its own retry).
            output, finish, used = self._request(messages, stage, thinking='disabled')
            if usable(output):
                return output
            self._record_failure(stage, 'cut_off' if finish == 'length' else 'unreadable', thinking='disabled',
                                 finish_reason=finish, completion_tokens=used,
                                 output=output if isinstance(output, str) else '')
            raise UserError('经历整理的修改没有返回可用的结果。')
        output, finish, used = self._request(messages, stage)
        if usable(output):
            return output
        cut = finish == 'length'
        self._record_failure(stage, 'cut_off' if cut else 'unreadable', finish_reason=finish,
                             completion_tokens=used, output=output if isinstance(output, str) else '')
        if not cut:
            return output
        if self.spent_today() + self._worst_for(messages) > Decimal(str(self.settings['daily_budget_cny'])):
            raise UserError('经历整理被截断：思考用完了字数，今天的花费上限不够再试一次；聊天记录不受影响。')
        self._retried += 1
        output, finish, used = self._request(messages, stage, thinking='disabled')
        if usable(output):
            return output
        self._record_failure(stage, 'cut_off' if finish == 'length' else 'unreadable', thinking='disabled',
                             finish_reason=finish, completion_tokens=used,
                             output=output if isinstance(output, str) else '')
        raise UserError('经历整理被截断：思考用完了字数，关掉思考重试也没写出结果；聊天记录不受影响。')

    @staticmethod
    def _quotes(text, batch):
        """True when the text repeats a long run of a message word for word."""
        squeezed = ''.join(text.split())
        sources = [''.join(item['text'].split()) for item in batch]
        for start in range(0, max(0, len(squeezed) - VERBATIM_RUN + 1)):
            piece = squeezed[start:start + VERBATIM_RUN]
            if any(piece in source for source in sources):
                return True
        return False

    def _problem(self, text, limit, batch):
        """What is wrong with a text that cannot be written as it is, or None. Never shows a secret back."""
        if SECRET.search(text):
            return None
        if limit and len(text) > limit:
            # Ticket 386: "shorten it" alone left 4 of 6 drafts still 8 to 99 characters over after two tries
            # (11-17 to 11-18); aiming well under the cap leaves room for miscounting. Ticket 421 removes the
            # fixed cap entirely; this branch only runs when a caller still passes a real budget.
            return (f'{len(text)} characters; the most is {limit}. Rewrite it to about {limit - FIX_MARGIN} characters '
                    '(well under the limit, since counting is easy to get wrong), keeping the facts and the attitude; '
                    'what belongs to a later stage is not part of this one.')
        if self._quotes(text, batch):
            return 'repeats a stretch of a message word for word; say it in your own words.'
        return None

    def _fix(self, pending):
        """Ticket 368: the flawed drafts only, shown back with their problem. The model returns corrected text and
        nothing else: which memory it is about, whether it continues one and what the entry is called are kept by the
        program from the first answer."""
        data = {'drafts': [{'ref': item['ref'], 'title': item.get('title') or '', 'text': item['text'],
                            'problem': item['problem']} for item in pending]}
        messages = [{'role': 'system', 'content': self.cfg.persona},
                    {'role': 'system', 'content': prompts.text('memory.experience.fix')},
                    {'role': 'user', 'content': json.dumps(data, ensure_ascii=False, separators=(',', ':'))}]
        payload = _parse_json_object(self._call_model(messages, stage='fix', expect=('fixes',)))
        fixes = payload.get('fixes') if isinstance(payload, dict) else None
        if not isinstance(fixes, list):
            raise UserError('经历整理的修改没有返回可用的结果。')
        return {str(item.get('ref')): str(item.get('text') or '').strip() for item in fixes if isinstance(item, dict)}

    def _validated(self, output, batch, candidates, final=True):
        """(entries, updates, dropped, flagged). A text that is too long or copies the chat is not silently lost:
        when `final` is false it comes back in `flagged` (with its text and its `continues`) so the model can be
        shown it once and fix it, instead of starting over and losing what was right in it (ticket 368)."""
        payload = _parse_json_object(output)
        if not isinstance(payload, dict) or not ({'entries', 'updates'} & set(payload)):
            raise UserError('经历整理没有返回可用的结果；聊天记录不受影响，稍后重试。')
        entries, updates, dropped, flagged = [], [], 0, []
        raw_entries, raw_updates = payload.get('entries') or [], payload.get('updates') or []
        if not isinstance(raw_entries, list) or not isinstance(raw_updates, list):
            raise UserError('经历整理结果格式无效；聊天记录不受影响，稍后重试。')
        dropped += max(0, len(raw_entries) - MAX_ENTRIES) + max(0, len(raw_updates) - MAX_UPDATES)
        known = {candidate['id'] for candidate in candidates}
        rewritable = {candidate['id'] for candidate in candidates if candidate.get('whole', True)}

        for item in raw_entries[:MAX_ENTRIES]:
            title = ' '.join(str((item or {}).get('title') or '').split())[:TITLE_CHARS] if isinstance(item, dict) else ''
            text = str(item.get('text') or '').strip() if isinstance(item, dict) else ''
            continues = item.get('continues') if isinstance(item, dict) else None
            continues = continues if isinstance(continues, str) and continues in known else None
            fault = self._problem(text, ENTRY_CHARS, batch) if title and text else None
            if fault and not final:
                flagged.append({'kind': 'entry', 'title': title, 'text': text, 'continues': continues, 'problem': fault})
                continue
            if not title or not text or fault or SECRET.search(text):
                dropped += 1
                continue
            entries.append({'title': title, 'text': text, **({'continues': continues} if continues else {})})
        for item in raw_updates[:MAX_UPDATES]:
            memory_id = item.get('id') if isinstance(item, dict) else None
            text = str(item.get('text') or '').strip() if isinstance(item, dict) else ''
            fault = self._problem(text, UPDATE_CHARS, batch) if memory_id in rewritable and text else None
            if fault and not final and not any(update['id'] == memory_id for update in updates):
                flagged.append({'kind': 'update', 'id': memory_id, 'text': text, 'problem': fault})
                continue
            if (memory_id not in rewritable or not text or fault
                    or any(update['id'] == memory_id for update in updates)):
                dropped += 1
                continue
            updates.append({'id': memory_id, 'text': text})
        return entries, updates, dropped, flagged

    def _apply_deferred(self):
        """Ticket 421 review: apply updates that were kept while the memory was locked, once it is free.
        The draft is only applied if the memory still holds the text the model rewrote from."""
        applied = 0
        rows = self._events()
        finished = {str(row.get('deferred_key')) for row in rows
                    if row.get('kind') in ('deferred-applied', 'deferred-conflict')
                    and row.get('deferred_key')}
        seen_keys = set(finished)
        for row in reversed(rows):
            if row.get('kind') != 'deferred-update':
                continue
            key = str(row.get('deferred_key') or row.get('memory_id') or '')
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            memory_id = row.get('memory_id')
            if (not isinstance(memory_id, str) or self.sidecar.locked(memory_id)
                    or not self._alive(memory_id)):
                continue
            if not isinstance(row.get('seen'), str):
                self.store.append(self.path, {'kind': 'deferred-conflict', 'memory_id': memory_id,
                                              'deferred_key': key, 'reason': 'missing source version',
                                              'time': self.budget.clock()})
                continue
            try:
                current = self.memory.content_for(memory_id, refresh=True)
                if current is not None and current.strip() == str(row.get('text') or '').strip():
                    saved = True
                else:
                    saved = self._save_update({'id': memory_id, 'text': str(row.get('text') or '')},
                                              row.get('seen'), source_key=key)
                if saved == 'closed':
                    self.store.append(self.path, {'kind': 'deferred-conflict', 'memory_id': memory_id,
                                                  'deferred_key': key, 'reason': 'target event closed',
                                                  'time': self.budget.clock()})
                    continue
                if saved:
                    self.store.append(self.path, {'kind': 'deferred-applied', 'memory_id': memory_id,
                                                  'deferred_key': key, 'time': self.budget.clock()})
                    applied += 1
                else:
                    self.store.append(self.path, {'kind': 'deferred-conflict', 'memory_id': memory_id,
                                                  'deferred_key': key, 'reason': 'source version changed',
                                                  'time': self.budget.clock()})
            except Exception as exc:
                self._record_failure('deferred', 'apply_failed', memory_id=memory_id,
                                     error=type(exc).__name__)
        return applied

    def _save_entry(self, entry, span, source=None, source_key='', index=0):
        continues = entry.get('continues')
        args = {'content': entry['text'], 'title': entry['title']}
        if source_key:
            # Ticket 421: a retry of the same source batch must not add the stage twice, even if the model
            # worded it (or its title) differently this time. The position in the batch is the stable key.
            for row in self._events():
                if (row.get('kind') == 'entry' and row.get('source_key') == source_key
                        and row.get('entry_index', 0) == index):
                    return row.get('memory_id')
        if self.events is not None:
            target_event = self.events.event_for_memory(continues) if continues else None
            event_source_key = f'{source_key}:entry:{index}' if source_key else ''
            try:
                # Ticket 421: the stage carries when the conversation happened and which messages it came
                # from; the same batch is idempotent even when a retry crosses midnight.
                saved = self.events.record(entry['text'], entry['title'], why='经历整理',
                                           event_id=target_event, at=(source or {}).get('to'),
                                           source=source, source_key=event_source_key)
            except Exception as exc:
                raise UserError('经历整理的事件没有保存成功；这批消息会重试，不推进游标。') from exc
            if not saved or saved.get('pending'):
                raise UserError('经历整理的事件还在等待恢复，没有推进游标。')
            memory_id = saved.get('memory_id')
            if continues and not target_event and memory_id:
                try:
                    self.sidecar.link(memory_id, continues)
                except Exception:
                    pass
            stored = next((row for row in self.events.stages(saved.get('event_id'))
                           if row.get('source_key') == event_source_key), None)
            self.store.append(self.path, {'kind': 'entry', 'memory_id': memory_id,
                                          'title': (saved.get('title') if saved.get('duplicate') else entry['title']),
                                          'text': (((stored or {}).get('text') or entry['text'])
                                                   if saved.get('duplicate') else entry['text']),
                                          'merged': False, 'covers': span,
                                          'continues': continues, 'event_id': saved.get('event_id'),
                                          'source_key': source_key, 'entry_index': index,
                                          'source': source, 'time': self.budget.clock()})
            return memory_id
        operation, duplicate = self.memory.begin_write(args)
        if duplicate is not None:
            return None
        text = self.memory.call('hold', args, write_operation=operation)
        kind = self.memory.record_native(args, text)
        memory_id = self.memory.created_id(text)
        try:
            if memory_id:
                self.sidecar.source(memory_id, 'experience')
                if continues and continues != memory_id:
                    self.sidecar.link(memory_id, continues)   # a later stage: the same link Peter's link_to makes
        except Exception:
            pass
        self.store.append(self.path, {'kind': 'entry', 'memory_id': memory_id, 'title': entry['title'],
                                      'text': entry['text'], 'merged': kind == '合并', 'covers': span,
                                      'continues': continues, 'source_key': source_key,
                                      'entry_index': index, 'source': source,
                                      'time': self.budget.clock()})
        return memory_id

    def _preserve_rejected_update(self, update, seen, source_key, reason):
        """Keep a draft that can never be applied (for example a closed experience) where Lan can see it,
        without putting the batch back into an endless retry."""
        key = source_key or f'rejected:{update["id"]}:{hashlib.sha1(str(update.get("text") or "").encode("utf-8")).hexdigest()[:10]}'
        for row in self._events():
            if (row.get('kind') == 'update-rejected' and row.get('source_key') == key
                    and row.get('memory_id') == update['id']):
                return
        self.store.append(self.path, {'kind': 'update-rejected', 'memory_id': update['id'],
                                      'text': str(update.get('text') or ''),
                                      'seen': seen if isinstance(seen, str) else None,
                                      'source_key': key, 'reason': reason,
                                      'time': self.budget.clock()})

    def _save_update(self, update, seen=None, source_key=''):
        """Ticket 363: the entry is rewritten whole; the old version goes to the version record. `seen` is the
        text the model rewrote from: if Lan changed the entry meanwhile, her text wins and this rewrite is dropped."""
        memory_id = update['id']
        if source_key and any(row.get('kind') == 'update' and row.get('source_key') == source_key
                              and row.get('memory_id') == memory_id for row in self._events()):
            return True
        if not self._alive(memory_id) or self.sidecar.locked(memory_id):
            return False
        status = self._event_status(memory_id)
        if status in ('merged', 'closed'):
            # Ticket 421 review: revise() refuses a closed experience. Do not raise (that would keep the
            # whole batch uncommitted and buy the same model answer again); keep the draft and move on.
            self._preserve_rejected_update(update, seen, source_key, f'event {status}')
            return 'closed'
        previous = self.memory.content_for(memory_id, refresh=True)
        if not previous or (seen is not None and previous.strip() != seen.strip()):
            return False
        if self.events is not None:
            event_id = self.events.event_for_memory(memory_id)
            if event_id:
                if not self.events.revise(event_id, update['text'].strip(),
                                          self.memory.title_for(memory_id) or 'Event',
                                          seen=previous, why='经历整理：按后来的变化重写'):
                    return False
                self.store.append(self.path, {'kind': 'update', 'memory_id': memory_id, 'text': update['text'],
                                              'time': self.budget.clock(), 'event_id': event_id,
                                              **({'source_key': source_key} if source_key else {})})
                return True
        text = update['text'].strip()
        if text == previous.strip():
            return True
        document = self.memory.raw_content_for(memory_id)
        if document is None:
            return False
        # Same archive Peter's own edits use, so Lan can see and restore the earlier wording.
        self.store.append(f'{self.cfg.id}/memory-versions.jsonl', {
            'memory_id': memory_id, 'fields': ['content'], 'operation_reason': '经历整理：按后来的变化重写',
            'why_remembered': '', 'old_content': document})
        self.memory.call('trace', {'bucket_id': memory_id, 'content': text})
        getattr(self.memory, 'bucket_details_cache', {}).pop(memory_id, None)
        stored = self.memory.content_for(memory_id, refresh=True)
        if stored is None or stored.strip() != text:
            raise UserError('经历整理的重写没有得到确认；原记忆的旧版本已存档。')
        self.memory.record_native_update(memory_id, stored)
        try:
            self.sidecar.source(memory_id, 'experience-rewrite')
            self.sidecar.peter_changed(memory_id)
        except Exception:
            pass
        self.store.append(self.path, {'kind': 'update', 'memory_id': memory_id, 'text': text,
                                      'time': self.budget.clock(),
                                      **({'source_key': source_key} if source_key else {})})
        return True

    def _digest(self, batch, reason):
        events = self._events()
        last = batch[-1]
        candidates = self._candidates(events)
        if Decimal(str(self.settings['daily_budget_cny'])) > self.spent_today():
            # Searching costs a little embedding money: not once the day's ceiling is reached.
            candidates += self._library(batch, {candidate['id'] for candidate in candidates})
        candidates = self._fit_candidates(candidates)
        zone = last['time'].tzinfo

        def shown(c):
            item = {'id': c['id'], 'title': c['title'], 'written': c['written'], 'text': c['text']}
            if c.get('versions'):
                item['rewritten_times'] = c['versions']
            if c.get('stages'):
                item['stages'] = c['stages']
            if c.get('full_chars') is not None:
                item['full_chars'] = c['full_chars']
            if c.get('text_truncated'):
                item['text_truncated'] = True
            if not c.get('whole', True):
                item['too_long_to_rewrite'] = True   # can be continued, not rewritten
            return item

        def build(candidates):
            data = {
                'local_time_now': self._now().astimezone(zone).strftime('%Y-%m-%d %H:%M'),
                'earlier_entries': [shown(c) for c in candidates],
                'messages': [{'time': item['time'].strftime('%Y-%m-%d %H:%M'),
                          'from': 'Lan' if item['role'] == 'user' else self.cfg.name, 'text': item['text'],
                          **({'sent_on_your_own': ('after: ' + item['own']) if isinstance(item['own'], str)
                              else 'during one of your autonomous wakes'} if item.get('own') else {})}
                             for item in batch],
            }
            return [{'role': 'system', 'content': self.cfg.persona},
                    {'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': json.dumps(data, ensure_ascii=False, separators=(',', ':'))}]
        first_day = batch[0]['time']
        prompt = prompts.text('memory.experience.prompt').format(entry_chars=ENTRY_CHARS, update_chars=UPDATE_CHARS, max_entries=MAX_ENTRIES,
                               example_date=f'{first_day.strftime("%b")} {first_day.day} or {first_day.month}月{first_day.day}日')
        messages = build(candidates)
        cap = Decimal(str(self.settings['daily_budget_cny']))
        worst = self._worst_for(messages)
        self._worst, self._retried = worst, 0
        if self.spent_today() + worst > cap:
            # Lan's daily ceiling for this feature: nothing is sent, the chat waits for tomorrow, one line per day.
            today = self.budget.clock()[:10]
            if not any(e.get('kind') == 'budget' and str(e.get('time', ''))[:10] == today for e in events):
                self.store.append(self.path, {'kind': 'budget', 'time': self.budget.clock(), 'cap': str(cap)})
            return 'budget'
        complete = [item for item in batch
                    if int(item.get('chunk') or 0) == int(item.get('chunks') or 1) - 1]
        if complete:
            mark = {'through_message_id': complete[-1]['id'],
                    'through_position': complete[-1]['position'] + 1}
        else:
            # Only part of one long message fits: keep the cursor at that message and remember the chunk.
            mark = {'through_message_id': None, 'through_position': batch[0]['position']}
        mark.update({'messages': len(batch), 'chars': sum(len(item['text']) for item in batch),
                     'reason': reason,
                     'covers': {'from': batch[0]['time'].isoformat(), 'to': last['time'].isoformat()}})
        tail = batch[-1]
        progress = ({'kind': 'chunk-progress', 'message_id': tail['id'], 'position': tail['position'],
                     'next_chunk': int(tail.get('chunk') or 0) + 1, 'chunks': int(tail.get('chunks') or 1)}
                    if int(tail.get('chunks') or 1) > 1
                    and int(tail.get('chunk') or 0) < int(tail.get('chunks') or 1) - 1 else None)
        self.store.append(self.path, {'kind': 'attempt', **mark, 'time': self.budget.clock()})
        span = [batch[0]['time'].isoformat(timespec='minutes'), last['time'].isoformat(timespec='minutes')]
        deferred = []
        message_ids = list(dict.fromkeys(item['id'] for item in batch
                                         if isinstance(item.get('id'), str) and item['id']))
        source = {'kind': 'chat', 'from': batch[0]['time'].isoformat(), 'to': last['time'].isoformat(),
                  'message_ids': message_ids, 'count': len(batch)}
        bits = [item['id'] if int(item.get('chunks') or 1) == 1
                else f"{item['id']}#{int(item.get('chunk') or 0)}"
                for item in batch if isinstance(item.get('id'), str) and item['id']]
        source_key = ('chat:' + '|'.join(bits)) if bits else ('chat:' + ':'.join(span))
        written = updated = 0
        failed = []
        rejected = []
        try:
            entries, updates, dropped, flagged = self._validated(
                self._call_model(messages), batch, candidates, final=False)
            # Ticket 368: what the first answer decided (update / continue / new, and which memory) is kept as it is.
            # A draft that is fine is saved at once. One that is too long or copies the chat is shown back with its
            # problem, and only its text may change (at most LOOKS tries, within the daily ceiling); one that cannot
            # be fixed is not saved and is reported in the ledger.
            for number, item in enumerate(flagged):
                item['ref'] = f'd{number}'
            pending, tries = flagged, 0
            while pending and tries < LOOKS and self.spent_today() + worst <= cap:
                tries += 1
                try:
                    fixed = self._fix(pending)
                except Exception:
                    break
                still = []
                for item in pending:
                    text = fixed.get(item['ref'])
                    fault = self._problem(text, ENTRY_CHARS if item['kind'] == 'entry' else UPDATE_CHARS, batch) \
                        if text else 'came back empty'
                    if text and not fault and item['kind'] == 'entry':
                        entries.append({'title': item['title'], 'text': text,
                                        **({'continues': item['continues']} if item['continues'] else {})})
                    elif text and not fault and not any(u['id'] == item['id'] for u in updates):
                        updates.append({'id': item['id'], 'text': text})
                    else:
                        still.append({**item, 'text': text or item['text'], 'problem': fault or 'already rewritten'})
                pending = still
            failed = [{**({'title': item['title']} if item['kind'] == 'entry' else {'id': item['id']}),
                       'problem': item['problem']} for item in pending]
            dropped += len(pending)
            for item in pending:
                # Ticket 386: a draft that could not be fixed is not saved, but its text is not lost either.
                self._record_failure('fix', 'unfixed_draft', kind=item['kind'], problem=item['problem'],
                                     draft=item['text'], **({'title': item['title']} if item['kind'] == 'entry'
                                                             else {'id': item['id']}))
            for update in updates:
                if self.sidecar.locked(update['id']):
                    # Ticket 421: Lan locked the reviewed story; the new development is not dropped.
                    # It is written down once per batch and applied on a later run after she unlocks it.
                    seen = next((c['text'] for c in candidates if c['id'] == update['id']), None)
                    key = f'{source_key}:{update["id"]}'
                    known = {row.get('deferred_key') for row in self._events()
                             if row.get('kind') == 'deferred-update'}
                    if key not in known:
                        deferred.append({'memory_id': update['id'], 'text': update['text'],
                                         'reason': 'locked; kept for review'})
                        self.store.append(self.path, {'kind': 'deferred-update', 'memory_id': update['id'],
                                                      'text': update['text'], 'seen': seen,
                                                      'deferred_key': key, 'covers': span,
                                                      'time': self.budget.clock()})
                    continue
                update_key = f'{source_key}:{update["id"]}'
                saved = self._save_update(update, next(
                    (c['text'] for c in candidates if c['id'] == update['id']), None),
                    source_key=update_key)
                if saved == 'closed':
                    # Ticket 421 review: a closed experience is a terminal rejection, not a transient
                    # failure. The draft is preserved by _save_update; advance the cursor and keep going.
                    rejected.append({'memory_id': update['id'], 'reason': 'event closed'})
                    continue
                if not saved:
                    self.store.append(self.path, {'kind': 'update-conflict', 'memory_id': update['id'],
                                                  'source_key': update_key,
                                                  'reason': 'target changed, disappeared, locked, or rejected',
                                                  'time': self.budget.clock()})
                    raise UserError('经历整理的更新没有保存；保留当前记忆并等待重试，没有推进游标。')
                updated += 1
            for number, entry in enumerate(entries):
                written += self._save_entry(entry, span, source, source_key, number) is not None
        except Exception as exc:
            self.store.append(self.path, {'kind': 'error', 'reason': type(exc).__name__,
                                          'detail': str(exc)[:300] if isinstance(exc, UserError) else '',
                                          'written': written, 'updated': updated, 'time': self.budget.clock(),
                                          **({'retried_without_thinking': self._retried} if self._retried else {})})
            if not isinstance(exc, UserError):
                self._record_failure('save', 'error', error=type(exc).__name__, detail=str(exc)[:300])
            # Ticket 421: the cursor must not advance unless every draft of this batch is safely stored.
            # A retry is idempotent because stages are keyed by the source batch.
            raise
        self.store.append(self.path, {'kind': 'checkpoint', **mark, 'written': written, 'updated': updated,
                                      'dropped': dropped, 'time': self.budget.clock(),
                                      **({'unfixed': failed} if failed else {}),
                                      **({'rejected': rejected} if rejected else {}),
                                      **({'deferred': deferred} if deferred else {}),
                                      **({'retried_without_thinking': self._retried} if self._retried else {})})
        if progress:
            # The last message in this batch was only partly handled; the next run resumes inside it.
            self.store.append(self.path, {**progress, 'time': self.budget.clock()})
        self._context_cache = None
        return 'written' if written or updated else 'skipped'

    # --- what the chat and Lan see ------------------------------------------------------------
    def entry_ids(self):
        return {event['memory_id'] for event in self._events()
                if event.get('kind') == 'entry' and isinstance(event.get('memory_id'), str)}

    def context(self):
        """Recent entries for the block between the cached prefix and the history; None when off or empty.

        Rebuilt only when the ledger grows or the local day changes, so the provider's prefix cache holds
        between runs. A deleted or hidden entry drops out because its text is read from Ombre each rebuild.
        """
        if not self.enabled() or self.memory is None or not self.settings['recent_days']:
            return None
        try:
            events = self._events()
            now = self._now()
            key = (len(events), now.date().isoformat(), tuple(sorted(self.memory.hidden_ids())))
            if self._context_cache and self._context_cache[0] == key:
                return self._context_cache[1]
            cutoff = now - timedelta(days=self.settings['recent_days'])
            lines, used, seen = [], 0, set()
            titles = {e.get('memory_id'): e for e in events if e.get('kind') == 'entry'}
            for event in reversed(events):
                # An entry counts as recent from when it was written or last added to.
                memory_id = event.get('memory_id')
                stamp = _when(event.get('time'))
                origin = titles.get(memory_id)
                if (event.get('kind') not in ('entry', 'update') or memory_id in seen or origin is None
                        or stamp is None or stamp < cutoff or origin.get('merged')):
                    continue
                seen.add(memory_id)
                if not self._alive(memory_id):
                    continue
                text = self.memory.content_for(memory_id, refresh=True)
                if not text:
                    continue
                line = f'- {origin.get("title", "")}: {" ".join(text.split())}'
                if used + len(line) > self.settings['recent_chars']:
                    break
                lines.append(line)
                used += len(line)
            message = ({'role': 'system', 'content': prompts.text('memory.experience.heading') + '\n' + '\n'.join(reversed(lines))}
                       if lines else None)
            self._context_cache = (key, message)
            return message
        except Exception:
            return None

    def status(self):
        """For the memory page: is it on, what happened last, what it cost today."""
        events = self._events()
        last = next((e for e in reversed(events) if e.get('kind') in ('checkpoint', 'error', 'budget')), None)
        try:
            pending = self.pending()
        except Exception:
            pending = []
        try:
            spent = str(self.spent_today().quantize(Decimal('0.0001')))
        except Exception:
            spent = None
        return {'enabled': self.enabled(), 'available': self.memory is not None,
                'last': ({'kind': last['kind'], 'time': last.get('time'), 'written': last.get('written', 0),
                          'updated': last.get('updated', 0), 'detail': last.get('detail', ''),
                          'covers': last.get('covers'),
                          'retried_without_thinking': last.get('retried_without_thinking', 0),
                          'unfixed': len(last.get('unfixed') or []),
                          'rejected': len(last.get('rejected') or []),
                          'deferred': len(last.get('deferred') or [])} if last else None),
                'retry_at': (lambda at: at.isoformat(timespec='minutes') if at else None)(self.retry_at(events)),
                'failures_kept': len(self.failures()),
                'pending_chars': sum(len(item['text']) for item in pending),
                'min_chars': self.settings['min_chars'], 'spent_today': spent,
                'daily_budget_cny': str(self.settings['daily_budget_cny']),
                'entries': sum(1 for e in events if e.get('kind') == 'entry')}
