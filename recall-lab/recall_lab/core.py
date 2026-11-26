"""Core of recall-lab: data snapshots, the append-only budget ledger, embedding cache and metrics.

recall-lab is an isolated ablation harness for P6-S4. It never writes to the production
instance: every database is opened read-only, every paid call goes through BudgetLedger,
and real sample text is only read from paths the operator passes in (never from the repo).

Nothing in this module holds production secrets or requires network access; the synthetic
tests feed it in-memory fakes.
"""
from __future__ import annotations

import array
import contextlib
import hashlib
import json
import math
import os
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HARNESS_VERSION = 'recall-lab-0.1'
CATEGORIES = ('explicit_recall', 'empathy', 'cross_event', 'noise', 'trap', 'autonomy')
CATEGORY_LABELS = {
    'explicit_recall': 'S1 显式回忆与具体细节追问',
    'empathy': 'S2 隐含共情与关系情境',
    'cross_event': 'S3 跨事件串联与自然联想',
    'noise': 'S4 应空召回的噪声/表情包/短消息',
    'trap': 'S5 同词陷阱/工作讨论/报错抑制',
    'autonomy': 'S6 自主唤醒任务态与闲暇态',
}
# Brain visibility mirrors brain_retrieval.py: these rows are what automatic recall can offer.
VISIBLE_BUCKETS = ('dynamic', 'permanent')
HIDDEN_TYPES = ('archived', 'letter', 'plan', 'i', 'feel')

# Provider prices. The judge rates come from character.toml; the embedding rate is NOT hard-coded
# here: the operator must declare it per run (--embedding-rate-cny-per-million, 0.0 only when the
# endpoint is declared free), because a wrong embedding price would make the spend ledger lie.
PRICES = {
    'embedding': {'model': 'BAAI/bge-m3', 'input_cny_per_million': None,
                  'price_source': 'not configured yet (set via --embedding-rate-cny-per-million)'},
    'judge': {'model': 'deepseek-flash', 'input_cny_per_million': 2.0,
              'cached_input_cny_per_million': 0.04, 'output_cny_per_million': 8.0,
              'price_source': 'character.toml [providers.deepseek] peak rates'},
}
TIER_CAPS_CNY = {0: 0.0, 1: 6.0, 2: 10.0}
TOTAL_CAP_CNY = 20.0


def configure_embedding_price(rate_cny_per_million, *, source='operator declared'):
    """Declare the embedding price for this run; refuse unknown, negative or non-finite rates."""
    if rate_cny_per_million is None:
        raise ValueError('the embedding price per million tokens must be declared (0.0 only when '
                         'the endpoint is free/covered); unknown rates are refused')
    rate = float(rate_cny_per_million)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError('the embedding price must be a finite non-negative number')
    PRICES['embedding']['input_cny_per_million'] = rate
    PRICES['embedding']['price_source'] = source
    return dict(PRICES['embedding'])


def embedding_price():
    rate = PRICES['embedding'].get('input_cny_per_million')
    if rate is None:
        raise ValueError('embedding price is not configured; call configure_embedding_price() '
                         'before any embedding request')
    return float(rate)


def utc_now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def decode_vector(raw):
    """float32 blob -> list[float]; same contract as recall_relevance.decode_vector."""
    data = bytes(raw)
    if not data or len(data) % 4:
        raise ValueError('bad embedding blob')
    values = array.array('f')
    values.frombytes(data)
    if array.array('H', [1]).tobytes() != b'\x01\x00':
        values.byteswap()
    return values.tolist()


def percentile(values, fraction):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int(math.ceil(fraction * len(ordered))) - 1))
    return ordered[index]


def summarize_latency(values):
    return {'n': len(values), 'p50_ms': round(percentile(values, 0.5), 2),
            'p95_ms': round(percentile(values, 0.95), 2),
            'mean_ms': round(sum(values) / len(values), 2) if values else 0.0}


# ---------------------------------------------------------------------------------------
# Budget: append-only local ledger with reserve -> settle. Local only (gitignored path by
# default); the committed report only quotes its aggregates.
# ---------------------------------------------------------------------------------------
class BudgetBlocked(RuntimeError):
    """A paid call was refused before dispatch: the tier or total cap could be exceeded."""


class BudgetLedger:
    def __init__(self, path, *, tier_caps=None, total_cap=TOTAL_CAP_CNY, run_id=''):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tier_caps = dict(TIER_CAPS_CNY if tier_caps is None else tier_caps)
        self.total_cap = float(total_cap)
        self.run_id = run_id or datetime.now().strftime('run-%Y%m%d-%H%M%S')
        self._seq = 0
        self._tickets = {}
        self._mutex = threading.RLock()
        self._file_lock_depth = 0
        self._lock_path = self.path.with_name(self.path.name + '.lock')
        for row in self._read():
            self._seq = max(self._seq, int(row.get('seq') or 0))

    @contextlib.contextmanager
    def _file_lock(self):
        """Cross-process lock over one ledger file (shared across runs and instances).

        The in-process RLock only serialises threads. Two runs sharing one ledger file could both
        read the same headroom and each approve a reserve that together cross the cap, so the
        read-headroom-and-append section is also serialised through an exclusive lock file. Nested
        acquisitions reuse the outer lock (reserve() appends its own row inside the same section).
        """
        if self._file_lock_depth:
            self._file_lock_depth += 1
            try:
                yield
            finally:
                self._file_lock_depth -= 1
            return
        deadline = time.monotonic() + 20.0
        handle = None
        while handle is None:
            try:
                handle = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self._lock_path.stat().st_mtime
                    if age > 60:   # a crashed run left the lock file behind
                        self._lock_path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError('budget ledger lock timeout: %s' % self._lock_path)
                time.sleep(0.02)
        self._file_lock_depth = 1
        try:
            os.write(handle, ('%d %s' % (os.getpid(), self.run_id)).encode('utf-8'))
            yield
        finally:
            self._file_lock_depth = 0
            try:
                os.close(handle)
            except OSError:
                pass
            try:
                self._lock_path.unlink()
            except OSError:
                pass

    def _read(self):
        if not self.path.is_file():
            return []
        rows = []
        with self.path.open('r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows

    def _append(self, row):
        with self._mutex:
            return self._append_locked(row)

    def _append_locked(self, row):
        with self._file_lock():
            # Re-read the highest seq under the cross-process lock so two runs cannot emit the
            # same seq for different rows.
            for existing in self._read():
                self._seq = max(self._seq, int(existing.get('seq') or 0))
            self._seq += 1
            row['seq'] = self._seq
            row['at'] = utc_now()
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        return row

    def totals(self):
        """{'tier': Decimal-ish float spent+dispatched-unsettled, 'total': ..., 'calls': n}"""
        with self._mutex:
            return self._totals_locked()

    def _totals_locked(self):
        spent = {tier: 0.0 for tier in self.tier_caps}
        total = 0.0
        calls = 0
        open_tickets = {}
        for row in self._read():
            if row.get('type') != 'reserve':
                continue
            calls += 1
            open_tickets[row['ticket']] = row
        settled = {}
        for row in self._read():
            if row.get('type') == 'settle':
                settled[row['ticket']] = row
        for ticket, row in open_tickets.items():
            amount = float((settled.get(ticket) or {}).get('amount_cny', row.get('reserve_cny', 0)))
            spent[row.get('tier', 0)] = spent.get(row.get('tier', 0), 0.0) + amount
            total += amount
        return {'by_tier': {str(k): round(v, 6) for k, v in sorted(spent.items())},
                'total_cny': round(total, 6), 'calls': calls}

    def headroom(self, tier):
        totals = self.totals()
        tier_spent = totals['by_tier'].get(str(tier), 0.0)
        return {'tier_room': round(self.tier_caps.get(tier, 0.0) - tier_spent, 6),
                'total_room': round(self.total_cap - totals['total_cny'], 6)}

    def reserve(self, tier, experiment, *, provider, model, kind, reserve_cny, detail=None):
        reserve_cny = round(float(reserve_cny), 8)
        with self._mutex:
            return self._reserve_locked(tier, experiment, provider=provider, model=model,
                                        kind=kind, reserve_cny=reserve_cny, detail=detail)

    def _reserve_locked(self, tier, experiment, *, provider, model, kind, reserve_cny, detail):
        if tier not in self.tier_caps:
            raise BudgetBlocked('tier %r has no cap' % (tier,))
        with self._file_lock():
            return self._reserve_under_lock(tier, experiment, provider=provider, model=model,
                                            kind=kind, reserve_cny=reserve_cny, detail=detail)

    def _reserve_under_lock(self, tier, experiment, *, provider, model, kind, reserve_cny, detail):
        # headroom and append are inside the same cross-process lock: same-file instances serialise.
        headroom = self.headroom(tier)
        if reserve_cny > headroom['tier_room'] + 1e-9:
            self._append({'type': 'blocked', 'tier': tier, 'experiment': experiment, 'kind': kind,
                          'reason': 'tier_cap', 'reserve_cny': reserve_cny, 'headroom': headroom,
                          'run_id': self.run_id})
            raise BudgetBlocked('tier cap %s would be exceeded: reserve %.6f > room %.6f'
                                % (tier, reserve_cny, headroom['tier_room']))
        if reserve_cny > headroom['total_room'] + 1e-9:
            self._append({'type': 'blocked', 'tier': tier, 'experiment': experiment, 'kind': kind,
                          'reason': 'total_cap', 'reserve_cny': reserve_cny, 'headroom': headroom,
                          'run_id': self.run_id})
            raise BudgetBlocked('total cap would be exceeded')
        ticket = secrets.token_hex(8)
        self._append({'type': 'reserve', 'ticket': ticket, 'tier': tier, 'experiment': experiment,
                      'provider': provider, 'model': model, 'kind': kind,
                      'reserve_cny': reserve_cny, 'detail': detail or {}, 'run_id': self.run_id})
        self._tickets[ticket] = tier
        return ticket

    def settle(self, ticket, amount_cny, *, usage=None, accounting='usage', note=''):
        with self._mutex:
            return self._settle_locked(ticket, amount_cny, usage=usage, accounting=accounting, note=note)

    def _settle_locked(self, ticket, amount_cny, *, usage=None, accounting='usage', note=''):
        tier = self._tickets.pop(ticket, None)
        self._append({'type': 'settle', 'ticket': ticket, 'tier': tier,
                      'amount_cny': round(float(amount_cny), 8),
                      'usage': usage or {}, 'accounting': accounting, 'note': note,
                      'run_id': self.run_id})
        return amount_cny

    def block(self, tier, experiment, reason, detail=None):
        self._append({'type': 'blocked', 'tier': tier, 'experiment': experiment,
                      'kind': 'offline', 'reason': reason, 'detail': detail or {},
                      'run_id': self.run_id})


def paid_upper_bound(tokens_in, tokens_out, model='judge'):
    price = PRICES[model]
    return (tokens_in * price['input_cny_per_million']
            + tokens_out * price['output_cny_per_million']) / 1_000_000


# ---------------------------------------------------------------------------------------
# Evaluation set
# ---------------------------------------------------------------------------------------
@dataclass
class Sample:
    sample_id: str
    category: str
    query: str
    expected: tuple
    allow_empty: bool
    key_facts: tuple
    confidence: str
    ambiguity: str
    context: dict
    source: dict
    labeler: str
    human_confirmed: bool

    @property
    def light(self):
        text = self.query.strip()
        return bool(text) and len(text) <= 40


def load_eval(path, *, validate_ids=None):
    """Load and validate an eval-v2 JSONL file. Returns (samples, report dict).

    Validation is structural only: it never rewrites the annotation. Unknown/duplicate sample ids,
    categories outside the six P6-4 classes, allow_empty inconsistencies and expected ids missing
    from the data snapshot are reported as errors instead of being silently fixed.
    """
    path = Path(path)
    samples, errors, warnings = [], [], []
    seen = set()
    with path.open('r', encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError as exc:
                errors.append('line %d: bad json: %s' % (number, exc))
                continue
            sample_id = raw.get('sample_id')
            if not isinstance(sample_id, str) or not sample_id:
                errors.append('line %d: missing sample_id' % number)
                continue
            if sample_id in seen:
                errors.append('%s: duplicate sample id' % sample_id)
                continue
            seen.add(sample_id)
            category = raw.get('category')
            if category not in CATEGORIES:
                errors.append('%s: unknown category %r' % (sample_id, category))
                continue
            query = raw.get('query')
            if not isinstance(query, str) or not query.strip():
                errors.append('%s: empty query' % sample_id)
                continue
            expected = raw.get('expected_relevant_ids') or []
            if not isinstance(expected, list) or any(not isinstance(item, str) or not item for item in expected):
                errors.append('%s: bad expected_relevant_ids' % sample_id)
                continue
            allow_empty = bool(raw.get('allow_empty'))
            if allow_empty and expected:
                errors.append('%s: allow_empty=true with expected ids' % sample_id)
                continue
            if not allow_empty and not expected:
                errors.append('%s: allow_empty=false without expected ids' % sample_id)
                continue
            if category in ('noise', 'trap') and not allow_empty:
                errors.append('%s: %s must be allow_empty' % (sample_id, category))
                continue
            if validate_ids is not None:
                missing = [item for item in expected if item not in validate_ids]
                if missing:
                    errors.append('%s: expected ids not present/alive in snapshot: %s'
                                  % (sample_id, ', '.join(missing)))
            if raw.get('labeler') != 'ai-assisted':
                warnings.append('%s: labeler=%r' % (sample_id, raw.get('labeler')))
            if raw.get('human_confirmed') is not False:
                warnings.append('%s: human_confirmed=%r' % (sample_id, raw.get('human_confirmed')))
            samples.append(Sample(
                sample_id=sample_id, category=category, query=query,
                expected=tuple(expected), allow_empty=allow_empty,
                key_facts=tuple(raw.get('key_facts') or []), confidence=str(raw.get('confidence') or ''),
                ambiguity=str(raw.get('ambiguity') or ''), context=raw.get('context') or {},
                source=raw.get('source') or {}, labeler=str(raw.get('labeler') or ''),
                human_confirmed=bool(raw.get('human_confirmed'))))
    if not samples and not errors:
        errors.append('no samples loaded')
    report = {'path_name': path.name, 'sha256': sha256_file(path), 'samples': len(samples),
              'errors': errors, 'warnings': warnings[:20], 'warning_count': len(warnings)}
    if errors:
        raise EvalSetError(report)
    return samples, report


class EvalSetError(ValueError):
    def __init__(self, report):
        super().__init__('; '.join(report.get('errors') or ['invalid eval set'])[:2000])
        self.report = report


def category_counts(samples):
    counts = {category: 0 for category in CATEGORIES}
    for sample in samples:
        counts[sample.category] = counts.get(sample.category, 0) + 1
    return counts


# ---------------------------------------------------------------------------------------
# Data snapshot: read-only brain.db + optional chat / learned-context files
# ---------------------------------------------------------------------------------------
@dataclass
class Memory:
    id: str
    title: str
    body: str
    raw: str
    created: str
    last_active: str
    importance: int
    valence: float | None
    arousal: float | None
    why_remembered: str
    bucket: str
    type: str
    tags: tuple
    category: str


@dataclass
class Event:
    id: str
    memory_id: str
    title: str
    story: str
    status: str
    approved: int
    first_at: str
    last_at: str
    story_source: str
    related: tuple
    previous: tuple


@dataclass
class Snapshot:
    db_path: Path
    memories: dict = field(default_factory=dict)
    vectors: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    relations: list = field(default_factory=list)
    event_sources: list = field(default_factory=list)
    stage_evidence: list = field(default_factory=list)
    quotes: list = field(default_factory=list)
    embedding_model: str = ''
    stats: dict = field(default_factory=dict)

    @property
    def visible_ids(self):
        """Ids automatic recall can offer (brain_retrieval.py visibility contract)."""
        return [mid for mid, memory in self.memories.items()
                if memory.bucket in VISIBLE_BUCKETS and memory.type not in HIDDEN_TYPES]

    @property
    def vector_ids(self):
        return [mid for mid in self.visible_ids if mid in self.vectors]

    def event_memory_ids(self):
        """event id -> memory row id it projects to (V-series events use their own id)."""
        mapping = {}
        for event_id, event in self.events.items():
            mapping[event_id] = event.memory_id or event_id
        return mapping

    def identity(self):
        return {'brain_db_sha256': sha256_file(self.db_path),
                'memories': len(self.memories), 'visible_memories': len(self.visible_ids),
                'vectors': len(self.vectors), 'events': len(self.events),
                'relations': len(self.relations), 'stage_evidence': len(self.stage_evidence),
                'quotes': len(self.quotes), 'embedding_model': self.embedding_model}


def load_snapshot(db_path):
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError('brain.db not found: %s' % db_path.name)
    connection = sqlite3.connect('file:%s?mode=ro' % db_path.as_posix(), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        memories = {}
        vectors = {}
        rows = connection.execute(
            'SELECT id,title,body,raw_markdown,created,last_active,importance,valence,arousal,'
            'why_remembered,bucket,type,tags_json,category,is_tombstone,is_deleted FROM memories'
        ).fetchall()
        for row in rows:
            if row['is_tombstone'] or row['is_deleted']:
                continue
            tags = ()
            try:
                parsed = json.loads(row['tags_json'] or '[]')
                if isinstance(parsed, list):
                    tags = tuple(str(tag) for tag in parsed)
            except (TypeError, ValueError):
                tags = ()
            memories[row['id']] = Memory(
                id=row['id'], title=row['title'] or '', body=row['body'] or '',
                raw=row['raw_markdown'] or '', created=str(row['created'] or ''),
                last_active=str(row['last_active'] or ''), importance=int(row['importance'] or 5),
                valence=row['valence'], arousal=row['arousal'],
                why_remembered=row['why_remembered'] or '', bucket=row['bucket'] or '',
                type=row['type'] or '', tags=tags, category=row['category'] or '')
        for row in connection.execute('SELECT memory_id,dim,vector FROM vectors'):
            if not row['vector']:
                continue
            try:
                vectors[row['memory_id']] = decode_vector(row['vector'])
            except (ValueError, TypeError):
                continue
        events = {}
        for row in connection.execute(
                'SELECT event_id,memory_id,title,story,status,approved,first_at,last_at,'
                'story_source,related_events_json,previous_events_json FROM events').fetchall():
            def _list(value):
                try:
                    parsed = json.loads(value or '[]')
                except (TypeError, ValueError):
                    return ()
                return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()
            events[row['event_id']] = Event(
                id=row['event_id'], memory_id=row['memory_id'] or '', title=row['title'] or '',
                story=row['story'] or '', status=row['status'] or '', approved=int(row['approved'] or 0),
                first_at=str(row['first_at'] or ''), last_at=str(row['last_at'] or ''),
                story_source=row['story_source'] or '',
                related=_list(row['related_events_json']), previous=_list(row['previous_events_json']))
        relations = [dict(row) for row in connection.execute(
            'SELECT memory_id,target_id,type,score,status FROM relations')]
        event_sources = [dict(row) for row in connection.execute(
            'SELECT event_id,ord,memory_id,part FROM event_sources')]
        stage_evidence = []
        try:
            for row in connection.execute(
                    "SELECT event_id,stage_id,payload_json FROM event_ledger WHERE kind='stage-evidence'"):
                try:
                    payload = json.loads(row['payload_json'] or '{}')
                except (TypeError, ValueError):
                    payload = {}
                stage_evidence.append({'event_id': row['event_id'], 'stage_id': row['stage_id'],
                                       'payload': payload if isinstance(payload, dict) else {}})
        except sqlite3.OperationalError:
            stage_evidence = []
        quotes = []
        try:
            for row in connection.execute('SELECT memory_id,speaker,text FROM quotes'):
                quotes.append({'memory_id': row['memory_id'], 'speaker': row['speaker'] or '',
                               'text': row['text'] or ''})
        except sqlite3.OperationalError:
            quotes = []
        try:
            row = connection.execute("SELECT value FROM meta WHERE key='embedding_model'").fetchone()
            embedding_model = str((row[0] if row else '') or '').strip()
        except sqlite3.OperationalError:
            embedding_model = ''
    finally:
        connection.close()
    snapshot = Snapshot(db_path=db_path, memories=memories, vectors=vectors, events=events,
                        relations=relations, event_sources=event_sources,
                        stage_evidence=stage_evidence, quotes=quotes,
                        embedding_model=embedding_model)
    snapshot.stats = snapshot.identity()
    return snapshot


def load_chat_user_messages(chat_path):
    """(message_id set of role=user rows, list of rows needed by experiments).

    Streams the file; a bad line is skipped, never raised. Returns ids and a thin index.
    """
    path = Path(chat_path)
    message_ids, rows = set(), []
    with path.open('r', encoding='utf-8', errors='replace') as handle:
        for number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get('role') == 'user' and isinstance(row.get('message_id'), str) and row['message_id']:
                message_ids.add(row['message_id'])
            rows.append({'line': number, 'role': row.get('role'),
                         'content': row.get('content') if isinstance(row.get('content'), str) else '',
                         'message_id': row.get('message_id'), 'request_id': row.get('request_id'),
                         'memory_refs': row.get('memory_refs') or [],
                         'time': row.get('time') or ''})
    return message_ids, rows


def read_jsonl(path):
    rows = []
    with Path(path).open('r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_learned_context(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)


def learned_context_sizes(data):
    """UTF-8 byte sizes of the four learned-context zones, using the production counting rule."""
    def _bytes(text):
        return len(str(text or '').encode('utf-8'))
    facts = data.get('facts') or {}
    growth = data.get('growth') or {}
    relationship = (data.get('relationship') or {}).get('items') or []
    ongoing = (data.get('ongoing') or {}).get('items') or []
    sizes = {
        'facts': _bytes(facts.get('text') if isinstance(facts, dict) else facts),
        'growth': _bytes(growth.get('text') if isinstance(growth, dict) else growth),
        'relationship': sum(_bytes(item.get('text')) for item in relationship
                            if isinstance(item, dict) and item.get('status') in ('confirmed', 'uncertain')),
        'ongoing': sum(_bytes(item.get('text')) for item in ongoing
                       if isinstance(item, dict) and item.get('status') in ('active', 'uncertain')),
    }
    sizes['total'] = sum(sizes.values())
    return sizes


# ---------------------------------------------------------------------------------------
# Embedding cache: one embedding request per unique text, persisted locally as JSONL.
# ---------------------------------------------------------------------------------------
class EmbeddingError(RuntimeError):
    pass


class EmbeddingCache:
    """Caches provider vectors by (model, sha256(text)); local file only.

    The operator supplies the provider (base_url/model/key) through environment variables or a
    read-only env file; the cache never stores the key. A cache hit does not call the provider.
    """

    def __init__(self, cache_path, *, model, base_url, api_key, timeout=30,
                 budget=None, tier=1, experiment='PREP-embedding', allow_provider=True):
        self.path = Path(cache_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.base_url = base_url
        self._api_key = api_key
        self.timeout = timeout
        self._client = None
        self._cache = {}
        self.calls = 0
        self.usage_tokens = 0
        self.used_cache = 0
        self.budget = budget
        self.tier = tier
        self.experiment = experiment
        self.allow_provider = bool(allow_provider)
        self.cost_cny = 0.0
        self.blocked_calls = 0
        if self.path.is_file():
            with self.path.open('r', encoding='utf-8') as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get('model') == self.model and isinstance(row.get('vector'), list):
                        self._cache[(row.get('model'), row.get('key'))] = row['vector']

    def _key(self, text):
        return sha256_text('%s\x00%s' % (self.model, text))

    def embed(self, text):
        key = self._key(text)
        cached = self._cache.get((self.model, key))
        if cached is not None:
            self.used_cache += 1
            return list(cached)
        if not self.allow_provider:
            self.blocked_calls += 1
            raise EmbeddingError('paid embedding call blocked (cache-only mode): cache miss for %s'
                                 % key[:12])
        if not self._api_key:
            raise EmbeddingError('no embedding key supplied')
        rate = embedding_price()
        # Reserve a conservative upper bound BEFORE dispatch: an embedding endpoint with a non-zero
        # declared rate must never be called without cap enforcement, and the ledger must reflect it.
        tokens_upper = max(512, len(str(text).encode('utf-8')))
        reserve_cny = round(tokens_upper * rate / 1_000_000, 8)
        ticket = None
        if self.budget is not None:
            ticket = self.budget.reserve(self.tier, self.experiment, provider='embedding',
                                         model=self.model, kind='embedding',
                                         reserve_cny=reserve_cny,
                                         detail={'estimated_input_tokens': tokens_upper,
                                                 'input_cny_per_million': rate})
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, base_url=self.base_url,
                                  timeout=self.timeout, max_retries=0)
        try:
            response = self._client.embeddings.create(model=self.model, input=str(text)[:4000])
        except Exception:
            if ticket is not None:
                self.budget.settle(ticket, reserve_cny, accounting='embedding-failed-conservative',
                                   note='provider request failed')
                self.cost_cny += reserve_cny
            raise
        usage = getattr(response, 'usage', None)
        prompt_tokens = getattr(usage, 'prompt_tokens', None)
        used_tokens = prompt_tokens if type(prompt_tokens) is int and prompt_tokens > 0 else None
        amount = (used_tokens * rate / 1_000_000) if used_tokens is not None else reserve_cny
        if ticket is not None:
            self.budget.settle(ticket, amount,
                               usage={'prompt_tokens': used_tokens or 0},
                               accounting='embedding-usage' if used_tokens is not None
                               else 'embedding-usage-missing-conservative')
        self.cost_cny += amount
        if used_tokens is not None:
            self.usage_tokens += used_tokens
        vector = list(response.data[0].embedding)
        if not vector:
            raise EmbeddingError('empty embedding')
        self.calls += 1
        self._cache[(self.model, key)] = vector
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'model': self.model, 'key': key, 'dim': len(vector),
                                     'vector': [round(value, 6) for value in vector]},
                                    ensure_ascii=False, sort_keys=True) + '\n')
        return vector

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

    def stats(self):
        price = embedding_price()
        return {'model': self.model, 'provider_calls': self.calls, 'cache_hits': self.used_cache,
                'usage_tokens': self.usage_tokens, 'cost_cny': round(self.cost_cny, 8),
                'input_cny_per_million': price, 'blocked_calls': self.blocked_calls}


class FakeEmbedder:
    """Deterministic local embedder for tests: hashes character n-grams into a fixed vector."""

    def __init__(self, dim=64):
        self.dim = dim
        self.calls = 0
        self.usage_tokens = 0

    def embed(self, text):
        self.calls += 1
        vector = [0.0] * self.dim
        for index, char in enumerate(str(text)):
            bucket = (ord(char) * 131 + index * 17) % self.dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


# ---------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------
def recall_at_k(ranked_ids, expected, k):
    if not expected:
        return None
    top = set(ranked_ids[:k])
    return len(top.intersection(expected)) / len(expected)


def precision_at_k(ranked_ids, expected, k):
    if not expected:
        return None
    top = ranked_ids[:k]
    if not top:
        return 0.0
    return len(set(top).intersection(expected)) / len(top)


def ndcg_at_k(ranked_ids, expected, k):
    if not expected:
        return None
    expected = set(expected)
    dcg = 0.0
    for index, item in enumerate(ranked_ids[:k]):
        if item in expected:
            dcg += 1.0 / math.log2(index + 2)
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected), k)))
    return dcg / ideal if ideal else 0.0


def empty_outcome(ranked_ids, k):
    """For allow_empty samples: True when the pipeline injected nothing (a correct empty recall)."""
    return not ranked_ids[:k]


def cross_event_covered(ranked_ids, expected, k, minimum=2):
    hits = set(ranked_ids[:k]).intersection(expected)
    return len(hits) >= minimum


def evaluate_rankings(samples, rankings, k, *, ndcg_k=8, precision_k=5):
    """Aggregate metrics over (sample -> list of ranked ids). Returns per-category + overall."""
    per_category = {}
    for category in CATEGORIES:
        rows = [sample for sample in samples if sample.category == category]
        per_category[category] = _aggregate_category(rows, rankings, k, ndcg_k, precision_k)
    return {'k': k, 'overall': _aggregate_category(list(samples), rankings, k, ndcg_k, precision_k),
            'by_category': per_category}


def _aggregate_category(samples, rankings, k, ndcg_k, precision_k):
    recalls, precisions, ndcgs = [], [], []
    empty_hits, empty_n = 0, 0
    injected_on_empty = 0
    cross_hits, cross_n = 0, 0
    for sample in samples:
        ranked = list(rankings.get(sample.sample_id) or [])
        if sample.allow_empty:
            empty_n += 1
            if not ranked[:k]:
                empty_hits += 1
            else:
                injected_on_empty += 1
        else:
            value = recall_at_k(ranked, sample.expected, k)
            if value is not None:
                recalls.append(value)
            precision = precision_at_k(ranked, sample.expected, precision_k)
            if precision is not None:
                precisions.append(precision)
            ndcg = ndcg_at_k(ranked, sample.expected, ndcg_k)
            if ndcg is not None:
                ndcgs.append(ndcg)
            if sample.category == 'cross_event':
                cross_n += 1
                if cross_event_covered(ranked, sample.expected, k):
                    cross_hits += 1
    return {'samples': len(samples),
            'recall_at_k': round(sum(recalls) / len(recalls), 4) if recalls else None,
            'precision_at_5': round(sum(precisions) / len(precisions), 4) if precisions else None,
            'ndcg_at_8': round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else None,
            'miss_rate': round(1 - sum(recalls) / len(recalls), 4) if recalls else None,
            'empty_expected_n': empty_n,
            'empty_correct': empty_hits,
            'false_positive_rate': round(injected_on_empty / empty_n, 4) if empty_n else None,
            'empty_injected_n': injected_on_empty,
            'cross_event_coverage': round(cross_hits / cross_n, 4) if cross_n else None}


def compare(metric_a, metric_b, key, higher_is_better=True):
    """Signed delta b-a with an explicit direction; None-safe."""
    first, second = metric_a.get(key), metric_b.get(key)
    if first is None or second is None:
        return {'metric': key, 'a': first, 'b': second, 'delta': None, 'direction': None}
    delta = second - first
    direction = 'better' if ((delta > 0) == higher_is_better) and delta != 0 else (
        'worse' if delta != 0 else 'same')
    return {'metric': key, 'a': round(first, 4), 'b': round(second, 4),
            'delta': round(delta, 4), 'direction': direction}


def result_stub(experiment_id, tier, *, title, variable, fixed):
    return {'experiment_id': experiment_id, 'tier': tier, 'title': title,
            'fixed_baseline': fixed, 'variable': variable,
            'harness_version': HARNESS_VERSION, 'status': 'not_run',
            'arms': [], 'comparison': {}, 'verdict': {}, 'evidence': {},
            'budget': {}, 'notes': [], 'evidence_gaps': [],
            'started_at': utc_now(), 'finished_at': None}