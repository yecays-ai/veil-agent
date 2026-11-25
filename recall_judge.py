"""Ticket 382: a small relevance judge for automatic recall.

Ombre's automatic search is a keyword search with a vector side channel that almost never opens (docs/tickets/382). Lan's
everyday sentences rarely repeat the words a memory was written with, so memories that fit are missed while weakly related
ones fill the three slots. The judge looks at a short list (Ombre's visible hits plus the nearest memories by vector) and says
which of them bear directly on what she just said, best first, or none. It only chooses memories; it never writes, edits or
hides one, and it says nothing about how Peter should answer.

Ticket 419: the judge is no longer capped at two (three for autonomous wakes). It may name every
candidate that directly bears on the message; the whole-round memory budget in ``recall_budget``
decides how many of them actually fit in the request.

Failure rule (Lan, 2026-11-17): the judge answering "none" means no memory this turn, nothing is filled back from Ombre.
Only a technical failure (timeout, request error, broken JSON, no embedding, budget) falls back to today's recall (ticket 381).
Three failures in a row switch the judge off for a few minutes so a dead provider cannot slow every message.
"""
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import replace
from decimal import Decimal

import prompts
from recall_relevance import all_vectors, cosine

COST_CATEGORY = 'recall_judge'
COST_ACCOUNTING = 'recall-judge'
MAX_OUTPUT_TOKENS = 200
QUERY_CHARS = 600
CARD_CHARS = 300
SKIPPED_TYPES = ('feel', 'plan', 'letter')

DEFAULTS = {'enabled': True, 'timeout_seconds': 4.0, 'ombre_candidates': 8, 'vector_candidates': 8,
            'breaker_failures': 3, 'breaker_seconds': 300}




def load_settings(raw):
    """[memory.recall_judge]; a bad value falls back to its default and never stops Peter from starting."""
    settings = dict(DEFAULTS)
    raw = raw if isinstance(raw, dict) else {}
    if type(raw.get('enabled')) is bool:
        settings['enabled'] = raw['enabled']
    value = raw.get('timeout_seconds')
    if type(value) in (int, float) and 0.5 <= value <= 10:
        settings['timeout_seconds'] = float(value)
    for key, low, high in (('ombre_candidates', 0, 16), ('vector_candidates', 0, 16), ('breaker_failures', 1, 20),
                           ('breaker_seconds', 5, 3600)):
        value = raw.get(key)
        if type(value) is int and low <= value <= high:
            settings[key] = value
    return settings


class JudgeFailed(Exception):
    """A technical failure: the caller keeps today's recall."""

    def __init__(self, reason, detail=''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason, self.detail = reason, detail


def card_text(raw):
    """Title, keywords and both ends of one memory file: all the judge reads of it.

    Ticket 419: an ending-only question ("what was the last thing that happened?") used to be
    invisible to the judge because the card was only the opening. The card now also carries a short
    closing line, so the judge can recognise a memory whose relevant detail comes at its end without
    reading the whole body."""
    if not isinstance(raw, str):
        return ''
    head, _, body = raw.partition('\n---\n')
    title = re.search(r'^title:\s*(.*)$', head, re.M) or re.search(r'^name:\s*(.*)$', head, re.M)
    tags, inside = [], False
    for line in head.splitlines():
        if line.startswith('tags:'):
            inside = True
            continue
        if inside:
            if line.startswith('- '):
                tags.append(line[2:].strip().strip('\'"'))
            else:
                inside = False
    keywords = re.search(r'(?:关键词|检索词)[：:](.*)', body)
    plain = ' '.join(body.split())
    opening = plain[:150]
    closing = ''
    if plain:
        tail = plain[-130:]
        if len(tail) > 24 and tail not in opening:
            closing = '…ending: ' + tail
    parts = [(title.group(1).strip().strip('\'"')[:60] if title else ''),
             (keywords.group(1).strip()[:60] if keywords else ''),
             ('…opening: ' + opening[:90]) if opening else '',
             closing[:130],
             ' '.join(tags)[:30]]
    return ' | '.join(part for part in parts if part)[:CARD_CHARS]


def labels_for(count):
    """A..Z, then A2..Z2 etc. — always parseable, so 32 candidates never become punctuation."""
    result = []
    for index in range(max(0, int(count))):
        if index < 26:
            result.append(chr(65 + index))
        else:
            result.append(f'{chr(65 + (index - 26) % 26)}{(index - 26) // 26 + 2}')
    return result


def parse_choice(text, letters, max_picks=None):
    """The picked ids, or JudgeFailed when the answer is not what was asked for. [] is a valid answer.

    Ticket 419: no default cap. The judge may name every memory it was offered; the request budget
    decides how many of them are actually injected."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise JudgeFailed('bad_json') from exc
    ids = data.get('ids') if isinstance(data, dict) else None
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise JudgeFailed('bad_format')
    picked = [letters[item] for item in dict.fromkeys(ids) if item in letters]
    if max_picks is not None:
        picked = picked[:max_picks]
    if ids and not picked:
        raise JudgeFailed('bad_format', 'none of the letters was offered')
    return picked


class RecallJudge:
    def __init__(self, memory, cfg, model, settings):
        self.memory, self.cfg, self.model, self.settings = memory, cfg, model, settings
        self.switch_path = f'{memory.cfg.id}/recall-judge.json'
        self.clock = time.monotonic
        self.failures = 0
        self.open_until = 0.0
        self.last = None
        # Ticket 391: IDs the judge was shown and picked, for the read-only recall panel (never text).
        self.last_choice = {'status': 'skipped', 'candidates': 0, 'picked': [], 'vector': []}
        self._embedder = None
        self._files = {}
        self._brain_rows = None
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='recall-judge')

    # --- the switch (Lan's): a file read every turn, so turning it off acts at once, nothing else changes ---
    def enabled(self):
        try:
            state = json.loads((self.memory.store.root / self.switch_path).read_text(encoding='utf-8'))
            if isinstance(state, dict) and type(state.get('enabled')) is bool:
                return state['enabled']
        except (OSError, ValueError):
            pass
        return bool(self.settings['enabled'])

    def set_enabled(self, enabled):
        self.memory.store.replace(self.switch_path, {'enabled': bool(enabled)})
        if enabled:
            with self._lock:
                self.failures, self.open_until = 0, 0.0
        return self.enabled()

    def breaker_open(self):
        return self.clock() < self.open_until

    def active(self):
        return bool(self.enabled() and self.model is not None and not self.breaker_open())

    def close(self):
        self._pool.shutdown(wait=False)

    # --- one turn ---------------------------------------------------------------------------------------
    def make_embedder(self):
        from dotenv import dotenv_values
        from pathlib import Path
        from recall_relevance import QueryEmbedder
        from runtime_env import env_file
        # P2-B: same instance-scoped key as Memory.embedder(); never the repository .env in a test instance.
        key = dotenv_values(env_file(self.memory.cfg), interpolate=False,
                            encoding='utf-8-sig').get('EMBED_API_KEY')
        if not key or not key.strip():
            raise JudgeFailed('no_embedding_key')
        memory_cfg = self.memory.cfg.memory
        return QueryEmbedder(memory_cfg['base_url'], memory_cfg['model'], key.strip(),
                             timeout=self.settings['timeout_seconds'])

    def _embed(self, text):
        """One embedding request, booked like every other vector request (estimate first, settled with the reported usage), so
        a paid embedding model could not slip past the daily ceiling. A refusal by the budget is a failure of the judge only."""
        if self._embedder is None:
            self._embedder = self.make_embedder()   # kept: its connection is reused on the next turn
        memory_cfg = self.memory.cfg.memory
        rate = Decimal(str(memory_cfg['input_cny_per_million']))
        text = text[:QUERY_CHARS]
        tokens = max(512, len(text.encode('utf-8')))
        budget = self.memory.budget
        reservation = budget.estimate(self.memory.cfg.id, memory_cfg['model'], tokens, rate)
        settled, usage, accounting = Decimal(tokens) * rate / Decimal(1_000_000), None, 'embedding-estimate'
        try:
            vector = self._embedder.embed(text)
            reported = getattr(self._embedder, 'usage_tokens', None)
            if type(reported) is int and reported > 0:
                amount = Decimal(reported) * rate / Decimal(1_000_000)
                if amount <= reservation[1]:
                    settled, usage, accounting = amount, reported, 'embedding-usage'
            return vector
        finally:
            budget.settle_estimate(reservation, accounting=accounting, settled_amount=settled, usage_tokens=usage)

    def warm(self):
        """Build the embedder and open its connection in the background right after start, so the first message does not pay."""
        def build():
            try:
                self._embed('hello')
            except Exception:
                pass
        self._pool.submit(build)

    def prefetch(self, query):
        """Start embedding her sentence now, so it overlaps with Ombre's own search instead of adding to it."""
        return self._pool.submit(self._embed, query)

    def _scan(self):
        """One pass over the library per turn: {memory id: file} for what is not in the archive folder, so the judge does
        not re-scan the whole folder for every candidate. Under Brain authority the rows come from brain.db."""
        authority = getattr(self.memory, 'brain_authority', None)
        if authority is not None:
            from brain_read import BrainRead
            index = {}
            self._brain_rows = {}
            with BrainRead(authority.db_path) as reader:
                rows = reader.connection.execute(
                    'SELECT id,bucket,type,body,title,is_deleted,is_tombstone,'
                    'source_tool FROM memories WHERE is_deleted=0 AND is_tombstone=0')
                for row in rows:
                    item = dict(row)
                    self._brain_rows = getattr(self, '_brain_rows', None) or {}
                    self._brain_rows[item['id']] = item
                    index[item['id']] = item['id']
            return index
        self._brain_rows = None
        from memory_agent import vault_path
        vault = vault_path(self.memory.store.root, self.memory.cfg.id)
        index = {}
        for path in vault.rglob('*.md'):
            relative = path.relative_to(vault).parts
            if relative[0] in ('archive', '_agent', '_sources', '_ledger', '_media') or path.is_symlink():
                continue
            index[path.stem] = path
            match = re.search(r'_([0-9A-Za-z-]+)$', path.stem)
            if match:
                index[match.group(1)] = path
        return index

    def _raw(self, key):
        if getattr(self.memory, 'brain_authority', None) is not None:
            return self.memory.raw_content_for(key)
        path = self._files.get(key)
        if path is None:
            return None
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            return None

    def _eligible(self, key):
        """The memory's front matter when Peter may be shown it, else None. A cheap read of the file first; the program's own
        visibility check (sources, archived, deleted, tombstone) has the last word."""
        if getattr(self.memory, 'brain_authority', None) is not None:
            self._brain_rows = getattr(self, '_brain_rows', None) or {}
            row = self._brain_rows.get(key)
            if row is None:
                return None
            if (row.get('type') in SKIPPED_TYPES + ('archived',) or row.get('bucket') not in ('dynamic', 'permanent')
                    or row.get('is_deleted') or row.get('is_tombstone')
                    or (row.get('source_tool') or '').casefold() == 'letter'):
                return None
            return {'body': row.get('body') or '', 'title': row.get('title') or ''}
        from recall_rotation import front_matter
        raw = self._raw(key)
        if raw is None:
            return None
        meta = front_matter(raw)
        head = raw.partition('\n---\n')[0]
        if (meta.get('type') in SKIPPED_TYPES + ('archived',) or re.search(r'^tombstone:\s*true', head, re.M)
                or re.search(r'^deleted_at:', head, re.M) or re.search(r'^source_tool:\s*letter', head, re.M)):
            return None   # what Ombre's automatic search never offers either
        if not self.memory.refresh_visibility(key) or not self.memory.visible([(key, '')]):
            return None
        return meta

    def _vector_candidates(self, query_vector, taken):
        from pathlib import Path
        from memory_agent import vault_path
        if getattr(self.memory, 'brain_authority', None) is not None:
            from brain_read import BrainRead
            with BrainRead(self.memory.brain_authority.db_path) as reader:
                vectors = reader.all_vectors()
        else:
            vectors = all_vectors(vault_path(self.memory.store.root, self.memory.cfg.id) / 'embeddings.db')
        ranked = sorted(((cosine(query_vector, vector), key) for key, vector in vectors.items()), reverse=True)
        found, checked = [], 0
        for _, key in ranked:
            if len(found) >= self.settings['vector_candidates'] or checked >= 30:
                break
            if key in taken or key in found:
                continue
            checked += 1
            meta = self._eligible(key)
            if meta is not None:
                found.append((key, f'[bucket_id:{key}]\n{(meta.get("body") or "").strip()}'))
        return found

    def _ask(self, messages):
        """Runs in a worker: reserve, one call, settle. A late answer after the caller gave up is still settled."""
        provider = dict(self.cfg.provider)
        size = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
        provider['max_input_tokens'] = min(provider['max_input_tokens'], size + 512)
        request_cfg = replace(self.cfg, provider=provider, max_output_tokens=MAX_OUTPUT_TOKENS)
        budget = self.memory.budget
        reservation, amount = budget.reserve(request_cfg, accounting=COST_ACCOUNTING, category=COST_CATEGORY)
        # The chat turn that asked for this already holds the model-settings locks, so a request from this worker thread
        # must not wait for them again (it would always time out). Like Chat._model_call: the lock-free request function with a
        # provider snapshot; a model without one (a test's fake) is called as it is.
        call = getattr(self.model, '__dict__', {}).get('raw') or self.model
        extra = {'_provider': dict(self.cfg.provider)} if call is not self.model else {}
        budget.mark_dispatched(reservation)   # from here a restart keeps the reserved cost: the provider may have been asked
        try:
            output, prompt_tokens, completion_tokens, cached_tokens = call(
                messages, json_output=True, max_tokens=MAX_OUTPUT_TOKENS, thinking='disabled',
                request_timeout=self.settings['timeout_seconds'], **extra)
        except Exception:
            budget.settle_estimate((reservation, amount), accounting=f'{COST_ACCOUNTING}-failed')
            raise
        try:
            budget.settle(reservation, request_cfg, prompt_tokens, completion_tokens, cached_tokens,
                          accounting=COST_ACCOUNTING, category=COST_CATEGORY)
        except Exception:
            budget.settle_estimate((reservation, amount), accounting=f'{COST_ACCOUNTING}-usage-invalid')
        return output

    def choose(self, query, pool, prefetched, *, wake=False, max_picks=None, candidate_limit=None):
        """(key, body) pairs the judge picked, best first; [] when it says none. Raises JudgeFailed on a technical failure."""
        started = self.clock()
        deadline = started + self.settings['timeout_seconds'] + 0.5
        candidates, picked = [], []
        self.last_choice = {'status': 'running', 'candidates': 0, 'picked': [], 'vector': []}
        try:
            self._files = self._scan()
            pool = list(pool)[:self.settings['ombre_candidates'] if candidate_limit is None else candidate_limit]
            try:
                query_vector = prefetched.result(timeout=max(0.05, deadline - self.clock()))
            except FutureTimeout as exc:
                raise JudgeFailed('timeout', 'embedding') from exc
            except JudgeFailed:
                raise
            except Exception as exc:
                raise JudgeFailed('embedding_error', type(exc).__name__) from exc
            try:
                extra = self._vector_candidates(query_vector, {key for key, _ in pool})
            except JudgeFailed:
                raise
            except Exception as exc:
                raise JudgeFailed('vectors_unreadable', type(exc).__name__) from exc
            candidates = pool + extra
            vector_keys = [key for key, _ in extra]
            self.last_choice.update({'candidates': len(candidates), 'vector': vector_keys})
            if not candidates:
                self.last_choice.update({'status': 'empty'})
                self._record('empty', started, candidates=0, picked=0)
                return []
            order = sorted(candidates, key=lambda item: hashlib.md5((query + item[0]).encode()).hexdigest())
            letters = {label: key for label, (key, _) in zip(labels_for(len(order)), order)}
            lines = []
            for letter, key in letters.items():
                lines.append(f'{letter}) {card_text(self._raw(key) or self.memory.raw_content_for(key))}')
            prompt = prompts.text('memory.judge.wake') if wake else prompts.text('memory.judge.prompt')
            context_label = 'Autonomous activity context' if wake else 'Lan just said'
            messages = [{'role': 'system', 'content': prompt},
                        {'role': 'user', 'content': '%s: "%s"\n\nSaved memories:\n%s' % (
                            context_label, query[:QUERY_CHARS], '\n'.join(lines))}]
            future = self._pool.submit(self._ask, messages)
            try:
                text = future.result(timeout=max(0.05, deadline - self.clock()))
            except FutureTimeout as exc:
                raise JudgeFailed('timeout', 'model') from exc
            except JudgeFailed:
                raise
            except Exception as exc:
                raise JudgeFailed('request_error', type(exc).__name__) from exc
            keys = parse_choice(text, letters, max_picks=max_picks)
            bodies = dict(candidates)
            picked = [(key, bodies[key]) for key in keys]
            self.last_choice.update({'status': 'chosen' if picked else 'empty',
                                     'candidates': len(candidates), 'picked': list(keys), 'vector': vector_keys})
        except JudgeFailed as failure:
            self._failed(failure.reason, started, len(candidates), failure.detail)
            raise
        with self._lock:
            self.failures = 0
        self._record('ok' if picked else 'empty', started, candidates=len(candidates), picked=len(picked))
        return picked

    # --- bookkeeping --------------------------------------------------------------------------------------
    def _failed(self, reason, started, candidates, detail=''):
        self.last_choice.update({'status': 'failed', 'candidates': candidates, 'picked': []})
        with self._lock:
            self.failures += 1
            if self.failures >= self.settings['breaker_failures']:
                self.open_until = self.clock() + self.settings['breaker_seconds']
        self._record('failed', started, reason=reason, detail=detail, candidates=candidates, picked=0)

    def _record(self, kind, started, **detail):
        seconds = round(self.clock() - started, 3)
        self.last = {'time': self.memory.budget.clock(), 'kind': kind, 'seconds': seconds, **detail}
        try:
            self.memory.store.append(self.memory.diagnostics, {'time': self.last['time'], 'code': 'recall_judge',
                                                                'kind': kind, 'seconds': seconds, **detail})
        except Exception:
            pass

    def status(self):
        try:
            day, waiting = self.memory.budget.category_totals(self.memory.cfg.id, None, COST_CATEGORY)
            spent = str((day + waiting).quantize(Decimal('0.0001')))
        except Exception:
            spent = None
        return {'enabled': self.enabled(), 'available': True, 'breaker_open': self.breaker_open(),
                'timeout_seconds': self.settings['timeout_seconds'], 'last': dict(self.last) if self.last else None,
                'spent_today': spent}
