"""Ticket 430 / P1: retrieval from VEIL Brain's own store.

Only the candidate source changes in this ticket. The recall judge, dedup, cooldown, budget and the
authoritative read/write path stay exactly as they are; Ombre remains the default backend.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
import time
from pathlib import Path

DEFAULTS = {
    'backend': 'ombre',
    'weight_vector': 0.65,
    'weight_keyword': 0.35,
}
VISIBLE_BUCKETS = ('dynamic', 'permanent')
HIDDEN_TYPES = ('archived', 'letter', 'plan', 'i', 'feel')
ID_BLOCK = '=== ombre:result-ids ==='
DRIFT_MARK = '=== 忽然想起来（非检索命中） ==='
WORD = re.compile(r'[\u4e00-\u9fffA-Za-z0-9_]')


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
    return settings


def tokenize(text):
    """jieba words, lower-cased; punctuation and whitespace tokens are dropped."""
    import jieba
    jieba.setLogLevel(60)
    tokens = []
    for piece in jieba.lcut(text or ''):
        piece = piece.strip().lower()
        if piece and WORD.search(piece):
            tokens.append(piece)
    return tokens


class BrainIndex:
    """Read-only index over brain.db. Rebuilt when the file changes; never writes to it."""

    def __init__(self, db_path, *, weight_vector=0.65, weight_keyword=0.35):
        self.db_path = Path(db_path)
        self.weight_vector = weight_vector
        self.weight_keyword = weight_keyword
        self.lock = threading.RLock()
        self.signature = None
        self.rows = []
        self.vectors = {}
        self.bm25 = None

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
            rows = connection.execute(
                'SELECT id, title, body, created, importance, bucket, type, is_tombstone, '
                'is_deleted, is_hidden, dont_surface FROM memories '
                'WHERE bucket IN (?,?) AND is_tombstone=0 AND is_deleted=0 AND is_hidden=0 '
                'AND (dont_surface IS NULL OR dont_surface=0)',
                VISIBLE_BUCKETS).fetchall()
            visible = [row for row in rows if (row[6] or '') not in HIDDEN_TYPES]
            vectors = {}
            for memory_id, dim, blob in connection.execute(
                    'SELECT memory_id, dim, vector FROM vectors'):
                if dim == 1024 and blob:
                    import array
                    values = array.array('f')
                    values.frombytes(blob)
                    vectors[memory_id] = values.tolist()
            from rank_bm25 import BM25Okapi
            tokenized = [tokenize((row[1] or '') + '\n' + (row[2] or '')) for row in visible]
            bm25 = BM25Okapi(tokenized) if tokenized else None
        finally:
            connection.close()
        # All fields are replaced together so a concurrent reader sees one consistent version.
        self.rows, self.vectors, self.bm25 = visible, vectors, bm25

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
        from recall_relevance import cosine
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
        tokens = tokenize(query)
        if not tokens:
            return {}
        raw = bm25.get_scores(tokens)
        return {rows[index][0]: float(score) for index, score in enumerate(raw)}

    def search(self, query, limit=8, embedder=None):
        """Rank visible memories: vector cosine plus BM25, normalized and fused."""
        self.refresh()
        with self.lock:
            rows = list(self.rows)
            if not rows:
                # An empty shadow must not silently turn automatic recall into "nothing found";
                # the caller falls back to Ombre and the diagnostic records why.
                raise RuntimeError('brain.db has no visible memories')
            bm25 = self.bm25
            visible_ids = [row[0] for row in rows]
        query_vector = None
        if embedder is not None:
            query_vector = embedder.embed(query)
        vector_scores = self._vector_scores(query_vector, visible_ids)
        keyword_scores = self._keyword_scores(bm25, rows, query)
        vector_top = max((vector_scores.get(memory_id, 0.0) for memory_id in visible_ids), default=0.0)
        vector_top = vector_top or 1.0
        keyword_top = max(keyword_scores.values(), default=0.0) or 1.0
        scored = []
        for row in rows:
            memory_id = row[0]
            score = (self.weight_vector * vector_scores.get(memory_id, 0.0) / vector_top
                     + self.weight_keyword * keyword_scores.get(memory_id, 0.0) / keyword_top)
            if score <= 0:
                continue
            try:
                importance = int(row[4] or 5)
            except (TypeError, ValueError):
                importance = 5
            score += min(0.05, max(0.0, (importance - 5) * 0.01))
            scored.append((score, memory_id, row))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{'id': memory_id, 'title': row[1] or '', 'body': row[2] or '',
                 'created': row[3] or '', 'importance': row[4], 'score': round(score, 6)}
                for score, memory_id, row in scored[:limit]]

    def drift(self, taken):
        """One random visible memory for the '忽然想起来' section, or None."""
        with self.lock:
            rows = [row for row in self.rows if row[0] not in taken]
        if not rows:
            return None
        row = random.choice(rows)
        return {'id': row[0], 'title': row[1] or '', 'body': row[2] or ''}


_INDICES = {}
_INDEX_LOCK = threading.Lock()


def get_index(db_path, settings):
    key = str(Path(db_path).resolve()) + '|' + str(settings['weight_vector']) + '|' + str(settings['weight_keyword'])
    with _INDEX_LOCK:
        index = _INDICES.get(key)
        if index is None:
            index = BrainIndex(db_path, weight_vector=settings['weight_vector'],
                               weight_keyword=settings['weight_keyword'])
            _INDICES[key] = index
    return index


def render(results, drift=None):
    """Same text contract as Ombre's breath_search output, so ordinary_hits() keeps working."""
    lines = []
    for item in results:
        lines.append('[bucket_id:%s] %s\n%s' % (item['id'], item['title'], item['body']))
    if drift is not None:
        lines.append('%s\n[bucket_id:%s] %s\n%s'
                     % (DRIFT_MARK, drift['id'], drift['title'], drift['body']))
    block = {'bucket_ids': [item['id'] for item in results]}
    return ('\n\n'.join(lines) + '\n' + ID_BLOCK + '\n' + '```json\n'
            + json.dumps(block, ensure_ascii=False) + '\n```')


def search_text(db_path, query, limit=8, embedder=None, settings=None, drift=True):
    """One call used by Memory: Ombre-shaped text, or an exception for the caller to fall back."""
    settings = settings or DEFAULTS
    index = get_index(db_path, settings)
    started = time.monotonic()
    results = index.search(query, limit=limit, embedder=embedder)
    picked = index.drift({item['id'] for item in results}) if (drift and len(results) < 3) else None
    elapsed = (time.monotonic() - started) * 1000
    return render(results, picked), {'elapsed_ms': round(elapsed, 1), 'count': len(results),
                                     'ids': [item['id'] for item in results],
                                     'drift': picked['id'] if picked else ''}
