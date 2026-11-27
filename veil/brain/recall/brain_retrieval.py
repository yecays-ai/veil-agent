"""Ticket 430 / P1: retrieval from VEIL Brain's own store.

Brain is the only retrieval backend (P3-2); the recall judge, dedup, cooldown, budget and the
authoritative read/write path are unchanged.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

DEFAULTS = {
    'backend': 'brain',
    'weight_vector': 0.65,
    'weight_keyword': 0.35,
    # P6-S5: auditable Chinese stopword filtering for BM25 only (off by default until verified).
    'stopwords_enabled': False,
    # P6-S5: explicit hybrid retrieval candidate count K (also bounds judge's hybrid pool).
    'candidate_k': 8,
}
VISIBLE_BUCKETS = ('dynamic', 'permanent')
HIDDEN_TYPES = ('archived', 'letter', 'plan', 'i', 'feel')
ID_BLOCK = '=== ombre:result-ids ==='
WORD = re.compile(r'[\u4e00-\u9fffA-Za-z0-9_]')
DATE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')

# P6-S5 auditable Chinese stopword list (BM25 lexical channel only; never alters raw query or
# embedding input). Deliberately excludes dates/time anchors (今天/昨天/明天/现在/刚才/以前/上次),
# negations (不/没/没有/别/不是), promises/agreements (约定/说好/答应), people/nicknames/pronouns
# carrying relational subject, and meaningful Chinese/English colloquialisms.
CHINESE_STOPWORDS = frozenset("""
的 了 是 在 这 那 这个 那个 这些 那些 一个 一种 什么 怎么 怎样 为什么
吗 呢 啊 吧 呀 哦 嗯 哈 嘛 啦 就 都 也 还 又 很 太 更 最 有 会 能 可以 要 想 觉得 知道
已经 一直 还是 但是 因为 所以 如果 然后 而且 只是 就是 真的 有点 一些 事情 时候 感觉 自己 一样
""".split())

# Protected terms that must never be added to CHINESE_STOPWORDS (checked by unit tests).
PROTECTED_TERMS = frozenset("""
今天 昨天 明天 现在 刚才 以前 上次 那天 那晚 去年 前年
不 没 没有 别 不要 不能 不是
约定 说好 答应 保证 记得 纪念日
我 你 她 他 我们 你们 他们 peter lan
""".split())


def created_date(value):
    """YYYY-MM-DD of a stored created timestamp, or '' when it cannot be read as a valid date."""
    match = DATE.search(str(value or ''))
    if not match:
        return ''
    text = '%s-%s-%s' % match.groups()
    try:
        from datetime import date
        date.fromisoformat(text)
    except ValueError:
        return ''
    return text


def load_settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULTS)
    backend = raw.get('backend')
    if backend in ('ombre', 'brain'):
        settings['backend'] = backend
    for key in ('weight_vector', 'weight_keyword'):
        value = raw.get(key)
        if type(value) in (int, float) and 0 <= float(value) <= 1:
            settings[key] = float(value)
    total = settings['weight_vector'] + settings['weight_keyword']
    if total <= 0:
        settings['weight_vector'], settings['weight_keyword'] = 0.65, 0.35
    else:
        settings['weight_vector'] /= total
        settings['weight_keyword'] /= total
    if type(raw.get('stopwords_enabled')) is bool:
        settings['stopwords_enabled'] = raw['stopwords_enabled']
    candidate_k = raw.get('candidate_k')
    if type(candidate_k) is int and 1 <= candidate_k <= 32:
        settings['candidate_k'] = candidate_k
    return settings


def tokenize(text, *, stopwords_enabled=False):
    """jieba words, lower-cased; punctuation and whitespace tokens are dropped.

    When ``stopwords_enabled`` is True, removes only auditable function words in
    ``CHINESE_STOPWORDS`` for BM25 scoring. Raw query and embedding input are untouched.
    """
    import jieba
    jieba.setLogLevel(60)
    stopwords = CHINESE_STOPWORDS if stopwords_enabled else None
    tokens = []
    for piece in jieba.lcut(text or ''):
        piece = piece.strip().lower()
        if not piece or not WORD.search(piece):
            continue
        if stopwords is not None and piece in stopwords:
            continue
        tokens.append(piece)
    return tokens


class BrainIndex:
    """Read-only index over brain.db. Rebuilt when the file changes; never writes to it."""

    def __init__(self, db_path, *, weight_vector=0.65, weight_keyword=0.35, stopwords_enabled=False):
        self.db_path = Path(db_path)
        self.weight_vector = weight_vector
        self.weight_keyword = weight_keyword
        self.stopwords_enabled = bool(stopwords_enabled)
        self.lock = threading.RLock()
        self.signature = None
        self.rows = []
        self.vectors = {}
        self.bm25 = None
        self.embedding_model = ''

    def _signature(self):
        try:
            stat = self.db_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def available(self):
        return self.db_path.is_file()

    def _connect(self):
        return sqlite3.connect('file:%s?mode=ro' % self.db_path, uri=True)

    def _load(self):
        connection = self._connect()
        try:
            query = ('SELECT id, title, body, created, importance, bucket, type, is_tombstone, '
                     'is_deleted FROM memories '
                     'WHERE bucket IN (?,?) AND is_tombstone=0 AND is_deleted=0')
            try:
                rows = connection.execute(
                    query + " AND id NOT IN (SELECT memory_id FROM delete_requests "
                            "WHERE status='pending')", VISIBLE_BUCKETS).fetchall()
            except sqlite3.OperationalError as exc:
                if 'no such table: delete_requests' not in str(exc).casefold():
                    raise
                rows = connection.execute(query, VISIBLE_BUCKETS).fetchall()
            visible = [row for row in rows if (row[6] or '') not in HIDDEN_TYPES]
            vectors = {}
            # P6-S1 G-A14: no hard-coded dimension. The stored length is the vector's real size;
            # compatibility with the current query vector is checked per row in _vector_scores/filter.
            for memory_id, dim, blob in connection.execute(
                    'SELECT memory_id, dim, vector FROM vectors'):
                if blob:
                    import array
                    values = array.array('f')
                    values.frombytes(blob)
                    vectors[memory_id] = values.tolist()
            try:
                row = connection.execute(
                    "SELECT value FROM meta WHERE key='embedding_model'").fetchone()
                embedding_model = str((row[0] if row else '') or '').strip()
            except sqlite3.OperationalError:
                embedding_model = ''
            from rank_bm25 import BM25Okapi
            tokenized = [tokenize((row[1] or '') + '\n' + (row[2] or ''),
                                  stopwords_enabled=self.stopwords_enabled) for row in visible]
            bm25 = BM25Okapi(tokenized) if tokenized else None
        finally:
            connection.close()
        # All fields are replaced together so a concurrent reader sees one consistent version.
        self.rows, self.vectors, self.bm25 = visible, vectors, bm25
        self.embedding_model = embedding_model

    def refresh(self, force=False):
        signature = self._signature()
        if signature is None:
            raise FileNotFoundError('brain.db is missing')
        with self.lock:
            if force or signature != self.signature or not self.rows:
                self._load()
                self.signature = signature
        return self

    def _vector_scores(self, query_vector, visible_ids):
        from veil.brain.recall.recall_relevance import cosine
        scores = {}
        if not query_vector:
            return scores
        for memory_id in visible_ids:
            vector = self.vectors.get(memory_id)
            if not vector:
                continue
            try:
                scores[memory_id] = max(0.0, cosine(query_vector, vector))
            except (TypeError, ValueError):
                continue
        return scores

    def _keyword_scores(self, bm25, rows, query):
        if bm25 is None:
            return {}
        tokens = tokenize(query, stopwords_enabled=self.stopwords_enabled)
        if not tokens:
            return {}
        raw = bm25.get_scores(tokens)
        return {rows[index][0]: float(score) for index, score in enumerate(raw)}

    def vector_similarities(self, query_vector, memory_ids, *, model=''):
        """Reuse loaded vectors to compute cosine similarity for a bounded set of candidate IDs."""
        if not query_vector or not memory_ids:
            return {}
        self.refresh()
        with self.lock:
            embedding_model = self.embedding_model
            if model and embedding_model and str(model).strip() != embedding_model:
                return {}
            expected = len(query_vector)
            usable = [mid for mid in memory_ids
                      if mid in self.vectors and len(self.vectors[mid]) == expected]
            return self._vector_scores(query_vector, usable)

    def search(self, query, limit=8, embedder=None, *, query_vector=None,
               model='', date_from='', date_to='', diagnostics=None):
        """Rank visible memories: vector cosine plus BM25, normalized and fused.

        P6-S1: (a) the caller may pass one shared query vector, so one turn pays for one embedding;
        (b) only vectors written by the configured embedding model, with the same dimension as the
        query vector, enter the vector channel — a mismatched model/size degrades to the keyword
        channel with a diagnostic instead of mixing incompatible vectors; (c) date_from/date_to
        filter by created day, inclusive, and report how many rows were filtered.
        """
        start_date = created_date(date_from) if date_from else ''
        end_date = created_date(date_to) if date_to else ''
        if date_from and not start_date:
            raise ValueError('invalid date_from')
        if date_to and not end_date:
            raise ValueError('invalid date_to')
        if start_date and end_date and start_date > end_date:
            raise ValueError('date_from is later than date_to')
        self.refresh()
        with self.lock:
            all_rows = list(self.rows)
            embedding_model = self.embedding_model
            if not all_rows:
                # An empty shadow must not silently turn automatic recall into "nothing found";
                # the caller falls back to Ombre and the diagnostic records why.
                raise RuntimeError('brain.db has no visible memories')
            bm25 = self.bm25
        # BM25 scores are indexed by the full visible list; compute them before any date filter
        # narrows the rows, then filter the row list without breaking that alignment.
        keyword_scores = self._keyword_scores(bm25, all_rows, query)
        rows = all_rows
        diag = diagnostics if isinstance(diagnostics, dict) else {}
        diag.update({'model_mismatch': False, 'skipped_dim': 0, 'skipped_created': 0,
                     'date_filtered': 0, 'embedding_model': embedding_model,
                     'keyword_only': False})
        if start_date or end_date:
            kept = []
            for row in rows:
                when = created_date(row[3])
                if not when:
                    diag['skipped_created'] += 1
                    continue
                if (start_date and when < start_date) or (end_date and when > end_date):
                    diag['date_filtered'] += 1
                    continue
                kept.append(row)
            rows = kept
        if query_vector is None and embedder is not None:
            # Accept either the provider object or a plain embed function: the authority writer
            # needs a callable while this index needs .embed(). Let a provider failure reach
            # Memory.search_candidates so it can fall back and record the diagnostic instead of
            # silently degrading to BM25.
            embed = getattr(embedder, 'embed', None)
            query_vector = (embed or embedder)(query)
        if model and embedding_model and str(model).strip() != embedding_model:
            # Vectors written by a different embedding model must never be compared with this
            # query vector; the keyword channel stays usable and the diagnostic records the cause.
            diag['model_mismatch'] = True
            query_vector = None
        vector_scores = {}
        visible_ids = [row[0] for row in rows]
        if query_vector:
            expected = len(query_vector)
            usable = {}
            for memory_id in visible_ids:
                vector = self.vectors.get(memory_id)
                if not vector:
                    continue
                if len(vector) == expected:
                    usable[memory_id] = vector
                else:
                    diag['skipped_dim'] += 1
            vector_scores = self._vector_scores(query_vector, list(usable))
        vector_top = max((vector_scores.get(memory_id, 0.0) for memory_id in visible_ids), default=0.0)
        vector_top = vector_top or 1.0
        keyword_top = max(keyword_scores.values(), default=0.0) or 1.0
        # A keyword-only call has no query vector. If the operator set weight_keyword=0 for the
        # normal vector search, still give the only available channel a real weight; otherwise
        # every BM25 hit would score 0 and the documented local fallback would return nothing.
        vector_weight = self.weight_vector if query_vector else 0.0
        keyword_weight = self.weight_keyword if (query_vector or self.weight_keyword) else 1.0
        scored = []
        for row in rows:
            memory_id = row[0]
            score = (vector_weight * vector_scores.get(memory_id, 0.0) / vector_top
                     + keyword_weight * keyword_scores.get(memory_id, 0.0) / keyword_top)
            if score <= 0:
                continue
            try:
                importance = int(row[4] or 5)
            except (TypeError, ValueError):
                importance = 5
            score += min(0.05, max(0.0, (importance - 5) * 0.01))
            scored.append((score, memory_id, row))
        scored.sort(key=lambda item: (-item[0], item[1]))
        result = [{'id': memory_id, 'title': row[1] or '', 'body': row[2] or '',
                   'created': row[3] or '', 'importance': row[4], 'score': round(score, 6)}
                  for score, memory_id, row in scored[:limit]]
        diag.update({'count': len(result), 'keyword_only': bool(query_vector is None),
                     'stopwords_enabled': self.stopwords_enabled, 'candidate_k': int(limit)})
        return result


_INDICES = {}
_INDEX_LOCK = threading.Lock()


def get_index(db_path, settings):
    stopwords = bool(settings.get('stopwords_enabled', False))
    key = (str(Path(db_path).resolve()) + '|' + str(settings['weight_vector']) + '|'
           + str(settings['weight_keyword']) + '|' + str(int(stopwords)))
    with _INDEX_LOCK:
        index = _INDICES.get(key)
        if index is None:
            index = BrainIndex(db_path, weight_vector=settings['weight_vector'],
                               weight_keyword=settings['weight_keyword'],
                               stopwords_enabled=stopwords)
            _INDICES[key] = index
    return index


def render(results):
    """Same text contract as Ombre's breath_search output, so ordinary_hits() keeps working."""
    lines = []
    for item in results:
        lines.append('[bucket_id:%s] %s\n%s' % (item['id'], item['title'], item['body']))
    block = {'bucket_ids': [item['id'] for item in results]}
    return ('\n\n'.join(lines) + '\n' + ID_BLOCK + '\n' + '```json\n'
            + json.dumps(block, ensure_ascii=False) + '\n```')


def search_text(db_path, query, limit=8, embedder=None, settings=None, *, query_vector=None,
                model='', date_from='', date_to=''):
    """One call used by Memory: Ombre-shaped text, or an exception for the caller to fall back."""
    settings = settings or DEFAULTS
    index = get_index(db_path, settings)
    started = time.monotonic()
    diagnostics = {}
    results = index.search(query, limit=limit, embedder=embedder, query_vector=query_vector,
                           model=model, date_from=date_from, date_to=date_to,
                           diagnostics=diagnostics)
    elapsed = (time.monotonic() - started) * 1000
    details = {'elapsed_ms': round(elapsed, 1), 'count': len(results),
               'ids': [item['id'] for item in results], **diagnostics}
    return render(results), details
