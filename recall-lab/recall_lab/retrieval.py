"""Retrieval variants for the P6-S4 ablations.

The baseline re-implements brain_retrieval.BrainIndex semantics (BM25 + cosine, min-max fusion
0.65/0.35, importance bonus capped at 0.05) so single-variable variants can be compared on the
same snapshot without touching the production modules. Every variant is a strict single-variable
change over RetrievalConfig.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime

WORD = re.compile(r'[\u4e00-\u9fffA-Za-z0-9_]')
DATE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')

# A small curated Chinese stopword set for AB-R1b. It is intentionally short and auditable:
# function words that carry no retrieval signal plus the three examples named by P6-4
# ("那个/今天/测试"-style filler). It never removes content words a memory could be keyed on.
STOPWORDS = frozenset("""
的 了 是 在 我 你 她 他 它 我们 你们 他们 这 那 这个 那个 这些 那些 一个 一种 什么 怎么 怎样 为什么
吗 呢 啊 吧 呀 哦 嗯 哈 嘛 啦 就 都 也 还 又 很 太 更 最 不 没 没有 有 会 能 可以 要 想 觉得 知道
今天 昨天 明天 现在 刚才 已经 一直 还是 但是 因为 所以 如果 然后 而且 只是 就是 真的 有点 一些
测试 那个 事情 时候 感觉 自己 一样
""".split())

HIDDEN_TYPES = ('archived', 'letter', 'plan', 'i', 'feel')
VISIBLE_BUCKETS = ('dynamic', 'permanent')


def _jieba():
    import jieba
    jieba.setLogLevel(60)
    return jieba


def tokenize(text, *, stopwords=None):
    """jieba words, lower-cased, punctuation dropped; identical to brain_retrieval.tokenize
    when stopwords is None (fidelity checked by the synthetic unit test)."""
    jieba = _jieba()
    tokens = []
    for piece in jieba.lcut(text or ''):
        piece = piece.strip().lower()
        if not piece or not WORD.search(piece):
            continue
        if stopwords is not None and piece in stopwords:
            continue
        tokens.append(piece)
    return tokens


def created_date(value):
    match = DATE.search(str(value or ''))
    if not match:
        return ''
    text = '%s-%s-%s' % match.groups()
    try:
        datetime.strptime(text, '%Y-%m-%d')
    except ValueError:
        return ''
    return text


def days_between(earlier, later):
    first, second = created_date(earlier), created_date(later)
    if not first or not second:
        return None
    return (datetime.strptime(second, '%Y-%m-%d') - datetime.strptime(first, '%Y-%m-%d')).days


class LexicalIndex:
    """BM25 over the visible memory titles+bodies. Optionally drops stopword tokens."""

    def __init__(self, snapshot, *, stopwords=None):
        self.stopwords = stopwords
        self.ids = []
        corpus = []
        for memory_id in snapshot.visible_ids:
            memory = snapshot.memories[memory_id]
            text = (memory.title or '') + '\n' + (memory.body or '')
            self.ids.append(memory_id)
            corpus.append(tokenize(text, stopwords=stopwords))
        from rank_bm25 import BM25Okapi
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def scores(self, query):
        if self.bm25 is None:
            return {}
        tokens = tokenize(query, stopwords=self.stopwords)
        if not tokens:
            return {}
        raw = self.bm25.get_scores(tokens)
        return {self.ids[index]: float(value) for index, value in enumerate(raw)}


class AhoCorasick:
    """Minimal Aho-Corasick matcher: enough for a deterministic alias dictionary, no dependency."""

    def __init__(self, patterns):
        self.goto = [{}]
        self.fail = [0]
        self.out = [set()]
        for pattern, values in patterns.items():
            if not pattern:
                continue
            node = 0
            for char in pattern:
                nxt = self.goto[node].get(char)
                if nxt is None:
                    nxt = len(self.goto)
                    self.goto[node][char] = nxt
                    self.goto.append({})
                    self.fail.append(0)
                    self.out.append(set())
                node = nxt
            self.out[node].update(values)
        queue = []
        for char, node in self.goto[0].items():
            self.fail[node] = 0
            queue.append(node)
        while queue:
            current = queue.pop(0)
            for char, node in self.goto[current].items():
                queue.append(node)
                fallback = self.fail[current]
                while fallback and char not in self.goto[fallback]:
                    fallback = self.fail[fallback]
                target = self.goto[fallback].get(char, 0)
                self.fail[node] = target
                self.out[node].update(self.out[target])

    def find(self, text):
        found = set()
        node = 0
        for char in str(text or '').lower():
            while node and char not in self.goto[node]:
                node = self.fail[node]
            node = self.goto[node].get(char, 0)
            if self.out[node]:
                found.update(self.out[node])
        return found


def build_alias_dictionary(snapshot, *, max_ids_per_alias=3, min_length=2):
    """Deterministic alias dictionary from real memory metadata (titles, tags, 关键词 lines).

    The design's "豆辞典" data source does not exist in this repository; this builds an
    equivalent auto-extracted dictionary from the snapshot itself and records that gap.
    """
    aliases = {}
    interjections = frozenset('嗯嗯 哈哈 哈哈哈 啊啊 晚安 早安 拜拜 好耶 笑死 抱抱 摸摸 亲亲 谢谢 再见 好的'.split())
    english_stop = frozenset("""
        the this that with what when where which while about after before because been being
        from have here into just like make many more most much must need only other over
        some such than then them they this very want were will with would your yours
        says said tell told know think feel feels felt look looks good bad okay sure
        time times day days night today yesterday tomorrow right left well still even
        peter assistant recent chat message messages user note notes memory memories
        tool tools test tests project projects file files work works thing things
        first last next another other same different new old little big small
    """.split())

    def add(alias, memory_id):
        alias = str(alias or '').strip().lower()
        if len(alias) < min_length or not WORD.search(alias):
            return
        # Only real entity-ish strings: at least two usable letters/characters, not a digit run,
        # not a function word and not a pure interjection (those created noise false touches).
        usable = sum(1 for char in alias if re.match(r'[一-鿿A-Za-z0-9]', char))
        if usable < 2 or alias.isdigit() or alias in STOPWORDS or alias in interjections:
            return
        aliases.setdefault(alias, set()).add(memory_id)
    jieba = _jieba()
    for memory_id, memory in snapshot.memories.items():
        if memory_id not in snapshot.visible_ids:
            continue
        add(memory.title, memory_id)
        for tag in memory.tags:
            add(tag, memory_id)
        for token in tokenize(memory.title):
            if len(token) >= 2:
                add(token, memory_id)
        for match in re.finditer(r'(?:关键词|检索词)[：:]\s*([^\r\n]+)', memory.raw or ''):
            for piece in re.split(r'[、,，;；/\s]+', match.group(1)):
                for token in tokenize(piece):
                    if len(token) >= 2:
                        add(token, memory_id)
        for match in re.finditer(r'[「“"]([^」”"]{2,12})[」”"]', memory.title or ''):
            add(match.group(1), memory_id)
    dictionary = {alias: tuple(sorted(ids)) for alias, ids in aliases.items()
                  if len(ids) <= max_ids_per_alias}
    return dictionary


@dataclass
class RetrievalConfig:
    k: int = 8                # production brain_retrieval default search limit
    fusion: str = 'minmax'              # minmax | rrf | vector
    stopwords: bool = False
    weight_vector: float = 0.65
    weight_keyword: float = 0.35
    importance_weight: float = 0.05     # additive cap; AB-R5a arms: 0 / .05 / .10 / .15
    last_active_boost: float = 0.0      # AB-R5b: 0.02 when last_active within 7d, .01 within 30d
    affect_boost: float = 0.0           # AB-R5c: 0.02 for matching valence/arousal
    related_expand: bool = False        # AB-R4b: one-hop related_to / related_events expansion
    related_seed_k: int = 8
    related_limit: int = 32
    alias_expand: bool = False          # AB-R4c: Aho-Corasick alias补召回
    story_index: bool = False           # AB-R4a: event story vectors join the vector channel


@dataclass
class Ranked:
    id: str
    score: float
    # vector is the channel value used for fused ranking (story-index arms may raise it).
    # stored_vector is always the memory's own stored-vector cosine; it is the common relevance
    # gate so all arms stay comparable.
    vector: float = 0.0
    stored_vector: float = 0.0
    keyword: float = 0.0
    story: float = 0.0
    bonus: float = 0.0


SENTIMENT_POSITIVE = ('开心', '高兴', '喜欢', '爱', '甜', '温暖', '幸福', '期待', '兴奋', '笑', '好耶', '棒')
SENTIMENT_NEGATIVE = ('难过', '伤心', '累', '痛', '哭', '害怕', '焦虑', '烦', '生气', '失望', '孤单', '委屈', '困')


def query_sentiment(query):
    text = str(query or '')
    positive = sum(1 for word in SENTIMENT_POSITIVE if word in text)
    negative = sum(1 for word in SENTIMENT_NEGATIVE if word in text)
    if positive == negative == 0:
        return None
    return 1 if positive > negative else (-1 if negative > positive else None)


class Retriever:
    def __init__(self, snapshot, *, alias_dictionary=None, story_vectors=None):
        self.snapshot = snapshot
        self.alias_dictionary = alias_dictionary or {}
        self.alias_matcher = AhoCorasick(self.alias_dictionary)
        self.story_vectors = dict(story_vectors or {})
        self._lexical = {}
        self._vector_matrix = None
        self._vector_ids = []
        self._vector_norms = None
        self._relevant_neighbours = self._build_related_neighbours()

    # -- preparation ------------------------------------------------------------------
    def _build_related_neighbours(self):
        neighbours = {}
        snapshot = self.snapshot
        for row in snapshot.relations:
            if (row.get('status') or '') != 'active':
                continue
            if row.get('type') not in ('related_to',):
                continue
            first, second = row.get('memory_id'), row.get('target_id')
            if not first or not second:
                continue
            neighbours.setdefault(first, set()).add(second)
            neighbours.setdefault(second, set()).add(first)
        event_memory = snapshot.event_memory_ids()
        for event in snapshot.events.values():
            source = event.memory_id or event.id
            for related in event.related:
                target = event_memory.get(related, related)
                if source and target and source != target:
                    neighbours.setdefault(source, set()).add(target)
        return {key: tuple(sorted(values)) for key, values in neighbours.items()}

    def _lexical_index(self, stopwords):
        key = bool(stopwords)
        if key not in self._lexical:
            self._lexical[key] = LexicalIndex(self.snapshot, stopwords=STOPWORDS if stopwords else None)
        return self._lexical[key]

    def _vectors(self):
        if self._vector_matrix is None:
            import numpy
            ids, rows = [], []
            for memory_id in self.snapshot.visible_ids:
                vector = self.snapshot.vectors.get(memory_id)
                if not vector:
                    continue
                ids.append(memory_id)
                rows.append(vector)
            matrix = numpy.asarray(rows, dtype=numpy.float32)
            if len(matrix):
                norms = numpy.linalg.norm(matrix, axis=1)
                norms[norms == 0] = 1.0
                matrix = matrix / norms[:, None]
            else:
                matrix = numpy.zeros((0, 0), dtype=numpy.float32)
                norms = numpy.zeros((0,), dtype=numpy.float32)
            self._vector_ids, self._vector_matrix = ids, matrix
        return self._vector_ids, self._vector_matrix

    def prepare(self):
        """Materialise the heavy indexes up front so per-query latency excludes one-off setup."""
        self._vectors()
        self._lexical_index(False)
        if self.story_vectors:
            self._story_vectors_matrix()

    def _story_vectors_matrix(self):
        if getattr(self, '_story_matrix', None) is None:
            import numpy
            event_memory = self.snapshot.event_memory_ids()
            ids, rows = [], []
            for event_id, vector in self.story_vectors.items():
                if not vector:
                    continue
                target = event_memory.get(event_id, event_id)
                if target not in self.snapshot.memories:
                    continue
                ids.append(target)
                rows.append(vector)
            matrix = numpy.asarray(rows, dtype=numpy.float32)
            if len(matrix):
                norms = numpy.linalg.norm(matrix, axis=1)
                norms[norms == 0] = 1.0
                matrix = matrix / norms[:, None]
            self._story_ids, self._story_matrix = ids, matrix
        return self._story_ids, self._story_matrix

    # -- channels ---------------------------------------------------------------------
    def stored_vector_scores(self, query_vector):
        """Memory-only stored-vector cosine per visible memory; the common relevance gate."""
        import numpy
        ids, matrix = self._vectors()
        scores = {}
        if not ids or not query_vector:
            return scores
        query = numpy.asarray(query_vector, dtype=numpy.float32)
        norm = float(numpy.linalg.norm(query)) or 1.0
        raw = matrix @ (query / norm)
        for index, memory_id in enumerate(ids):
            scores[memory_id] = max(0.0, float(raw[index]))
        return scores

    def vector_scores(self, query_vector, *, story_index=False):
        if story_index and self.story_vectors:
            import numpy
            scores = self.stored_vector_scores(query_vector)
            story_ids, story_matrix = self._story_vectors_matrix()
            if story_ids and query_vector:
                query = numpy.asarray(query_vector, dtype=numpy.float32)
                norm = float(numpy.linalg.norm(query)) or 1.0
                story_raw = story_matrix @ (query / norm)
                for index, memory_id in enumerate(story_ids):
                    value = max(0.0, float(story_raw[index]))
                    if value > scores.get(memory_id, 0.0):
                        scores[memory_id] = value
                        self._last_story_hit = getattr(self, '_last_story_hit', {})
                        self._last_story_hit[memory_id] = value
            return scores
        return self.stored_vector_scores(query_vector)

    def keyword_scores(self, query, *, stopwords=False, alias_ids=None):
        index = self._lexical_index(stopwords)
        scores = index.scores(query)
        if alias_ids:
            top = max(scores.values(), default=0.0) or 1.0
            for memory_id in alias_ids:
                if memory_id in self.snapshot.visible_ids:
                    scores[memory_id] = max(scores.get(memory_id, 0.0), top * 0.9)
        return scores

    def alias_ids(self, query):
        return tuple(sorted(self.alias_matcher.find(query))) if self.alias_dictionary else ()

    # -- ranking ----------------------------------------------------------------------
    def rank(self, query, query_vector, config, *, query_time=''):
        config = config if isinstance(config, RetrievalConfig) else RetrievalConfig(**config)
        started = time.perf_counter()
        story_hits = {}
        stored_vector = self.stored_vector_scores(query_vector)
        vector = dict(stored_vector)
        if config.story_index and self.story_vectors:
            self._last_story_hit = {}
            vector = self.vector_scores(query_vector, story_index=True)
            story_hits = dict(getattr(self, '_last_story_hit', {}) or {})
            self._last_story_hit = {}
        alias_ids = self.alias_ids(query) if config.alias_expand else ()
        keyword = self.keyword_scores(query, stopwords=config.stopwords,
                                      alias_ids=alias_ids if config.alias_expand else None)
        sentiment = query_sentiment(query) if config.affect_boost else None
        vector_order = sorted(vector, key=lambda key: (-vector[key], key))
        keyword_order = sorted(keyword, key=lambda key: (-keyword[key], key))
        vector_position = {memory_id: index for index, memory_id in enumerate(vector_order)}
        keyword_position = {memory_id: index for index, memory_id in enumerate(keyword_order)}
        vector_top = max(vector.values(), default=0.0) or 1.0
        keyword_top = max(keyword.values(), default=0.0) or 1.0
        ranked = []
        for memory_id in self.snapshot.visible_ids:
            memory = self.snapshot.memories[memory_id]
            vec = vector.get(memory_id, 0.0)
            kw = keyword.get(memory_id, 0.0)
            if config.fusion == 'minmax':
                score = (config.weight_vector * vec / vector_top
                         + config.weight_keyword * kw / keyword_top)
            elif config.fusion == 'rrf':
                # Standard RRF k=60, then scaled by 60 so the fusion part sits on the same 0..1
                # scale as min-max and the fixed importance bonus keeps the same relative weight.
                score = 0.0
                if memory_id in vector_position:
                    score += config.weight_vector / (60.0 + vector_position[memory_id])
                if memory_id in keyword_position:
                    score += config.weight_keyword / (60.0 + keyword_position[memory_id])
                score *= 60.0
            elif config.fusion == 'vector':
                score = config.weight_vector * vec / vector_top
            else:
                raise ValueError('unknown fusion %r' % config.fusion)
            if score <= 0:
                continue
            bonus = 0.0
            if config.importance_weight:
                bonus += min(config.importance_weight, max(0.0, (memory.importance - 5) * 0.01))
            if config.last_active_boost and memory.last_active and query_time:
                age = days_between(memory.last_active, query_time[:10])
                if age is not None and age >= 0:
                    if age <= 7:
                        bonus += config.last_active_boost
                    elif age <= 30:
                        bonus += config.last_active_boost / 2.0
            if sentiment and config.affect_boost and memory.valence is not None:
                valence = float(memory.valence)
                if (sentiment > 0 and valence >= 0.55) or (sentiment < 0 and valence <= 0.45):
                    bonus += config.affect_boost
            score += bonus
            ranked.append(Ranked(id=memory_id, score=score, vector=round(vec, 6),
                                 stored_vector=round(stored_vector.get(memory_id, 0.0), 6),
                                 keyword=round(kw, 6), story=round(story_hits.get(memory_id, 0.0), 6),
                                 bonus=round(bonus, 6)))
        ranked.sort(key=lambda item: (-item.score, item.id))
        diagnostics = {'alias_matched': list(alias_ids), 'sentiment': sentiment,
                       'story_hits': len(story_hits)}
        expanded_added = []
        if config.related_expand:
            seeds = [item.id for item in ranked[:config.related_seed_k]]
            # Only the delivered top-K counts as taken: the importance bonus gives every
            # important memory a positive score, so "all scored rows" would swallow every neighbour.
            taken = set(seeds) | set(item.id for item in ranked[:max(config.k, 0)])
            for seed in seeds:
                if len(expanded_added) >= config.related_limit:
                    break
                for neighbour in self._relevant_neighbours.get(seed, ()):
                    if len(expanded_added) >= config.related_limit:
                        break
                    if neighbour in taken or neighbour not in self.snapshot.visible_ids:
                        continue
                    taken.add(neighbour)
                    expanded_added.append(neighbour)
                    ranked.append(Ranked(id=neighbour, score=0.0))
            diagnostics['related_expanded'] = list(expanded_added)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        ranked = ranked[:max(config.k, 0)]
        diagnostics.update({'count': len(ranked), 'elapsed_ms': round(elapsed_ms, 3),
                            'pool_ids': [item.id for item in ranked]})
        return ranked, diagnostics


def rrf_positions(ranked_ids):
    return {memory_id: index for index, memory_id in enumerate(ranked_ids)}


def judge_candidate_pool(ranker, query, query_vector, *, fused_limit=8, vector_limit=8, config=None):
    """Production recall_judge layout: fused top-N plus nearest stored vectors not already taken.

    Returns [(memory_id, body)] in the randomized order the judge sees (md5(query+id)).
    """
    import hashlib
    ranking_config = replace(config, k=max(fused_limit, 8)) if config is not None else RetrievalConfig(k=max(fused_limit, 8))
    ranked, _ = ranker.rank(query, query_vector, ranking_config)
    fused = [item.id for item in ranked[:fused_limit]]
    taken = set(fused)
    vector = ranker.vector_scores(query_vector)
    extra = []
    for memory_id in sorted(vector, key=lambda key: (-vector[key], key)):
        if len(extra) >= vector_limit:
            break
        if memory_id in taken:
            continue
        extra.append(memory_id)
        taken.add(memory_id)
    candidates = fused + extra
    candidates.sort(key=lambda mid: hashlib.md5((query + mid).encode()).hexdigest())
    return [(mid, ranker.snapshot.memories[mid].body if mid in ranker.snapshot.memories else '')
            for mid in candidates]


def production_breath_pool(snapshot, *, limit=8):
    """Exact replica of brain_authority.BrainAuthority.breath(): static top-N ordered by
    (pinned DESC, importance DESC, created DESC) across visible memories."""
    visible = [snapshot.memories[mid] for mid in snapshot.visible_ids if mid in snapshot.memories]
    visible.sort(key=lambda m: (int(getattr(m, 'pinned', 0) or 0), int(m.importance or 0), str(m.created or '')),
                 reverse=True)
    return visible[:limit]


def stratified_long_tail_pool(snapshot, *, size=24, importance_min=7, rotation=0,
                              exclude_work=True, stride=None):
    """AB-A1a: library-wide stratified rotation pool of high-importance memories.

    Strata are (category, created year-month); memories are interleaved across strata into a
    full-library ring, and `rotation` slides a window of `size` across the entire ring (not just
    reordering the same 24 items), so long-tail imp>=7/8 memories genuinely surface across turns.
    """
    from veil.brain.recall.natural_association import _NOT_LIGHT
    visible_set = set(snapshot.visible_ids)
    candidates = [memory for memory in snapshot.memories.values()
                  if memory.id in visible_set and memory.importance >= importance_min
                  and (not exclude_work or not _NOT_LIGHT.search((memory.body or '')[:600]))]
    strata = {}
    for memory in candidates:
        strata.setdefault((memory.category, (memory.created or '')[:7]), []).append(memory)
    ordered = []
    for key in sorted(strata):
        ordered.append(sorted(strata[key], key=lambda item: (-int(getattr(item, 'pinned', 0) or 0), item.id)))
    ring, index = [], 0
    while any(index < len(group) for group in ordered):
        for group in ordered:
            if index < len(group):
                ring.append(group[index])
        index += 1
    if not ring:
        return []
    if len(ring) <= size:
        offset = int(rotation) % len(ring)
        return ring[offset:] + ring[:offset]
    step = max(1, int(stride) if stride is not None else max(1, size // 3))
    start = (int(rotation) * step) % len(ring)
    return [ring[(start + idx) % len(ring)] for idx in range(size)]


def association_choice(snapshot, query, candidates, *, turn=0, already=(), recall_cfg=None,
                       last_shown=None, previous=None):
    """Run the production natural-association scoring over an explicit candidate pool."""
    from veil.brain.recall.natural_association import choose
    metas = {}
    for memory in candidates:
        metas[memory.id if hasattr(memory, 'id') else memory] = {
            'importance': getattr(memory, 'importance', 0),
            'pinned': 'true' if getattr(memory, 'pinned', 0) else 'false'}
    pairs = [(memory.id, memory.body) for memory in candidates]
    return choose(query, pairs, metas, dict(last_shown or {}), int(turn), set(already),
                  recall_cfg=recall_cfg, previous=previous)


def wake_association_choice(snapshot, wake_context, candidates, *, turn=0, already=(),
                            last_shown=None, previous=None, is_idle=True,
                            min_importance=4, cooldown_turns=8):
    """AB-A1b: independent wake-side candidate gate for autonomous idle wakes.

    Unlike production `natural_association.choose()`, idle wakes have long system/activity prompts
    (>40 chars), so the 40-char `light_chat` gate is replaced by an explicit `is_idle` gate:
    - task wakes (`is_idle=False`) are strictly blocked (0 task interference);
    - idle wakes filter out work-topic candidate bodies, respect cooldown and `already`, and score
      candidates by importance + silence + light lexical affinity to the idle wake context.
    """
    from veil.brain.recall.natural_association import _NOT_LIGHT
    if not is_idle:
        return {'key': '', 'body': '', 'score': 0, 'reason': 'task_wake', 'dropped': []}
    if not candidates:
        return {'key': '', 'body': '', 'score': 0, 'reason': 'no_candidates', 'dropped': []}
    already_set = set(already or ())
    last_map = dict(last_shown or {})
    wake_tokens = set(tokenize(wake_context, stopwords=STOPWORDS))
    scored = []
    dropped = []
    for memory in candidates:
        key = memory.id
        body = memory.body or ''
        if key in already_set:
            dropped.append({'id': key, 'reason': 'already_in_context'})
            continue
        imp = int(getattr(memory, 'importance', 0) or 0)
        if getattr(memory, 'pinned', 0):
            imp = max(imp, 9)
        if imp < min_importance:
            dropped.append({'id': key, 'reason': 'not_meaningful'})
            continue
        if _NOT_LIGHT.search(body[:600]):
            dropped.append({'id': key, 'reason': 'work_topic'})
            continue
        last = last_map.get(key)
        gap = cooldown_turns
        if isinstance(previous, dict) and previous.get('key') == key:
            streak = previous.get('repeat')
            streak = streak if type(streak) is int and 0 <= streak <= 8 else 0
            gap = gap * (2 ** min(streak, 3))
        if type(last) is int and gap and turn - last <= gap:
            dropped.append({'id': key, 'reason': 'association_cooldown'})
            continue
        silence = 50 if type(last) is not int else min(max(0, turn - last), 50)
        overlap = 0
        if wake_tokens:
            mem_tokens = set(tokenize((memory.title or '') + ' ' + body[:300], stopwords=STOPWORDS))
            overlap = min(15, len(wake_tokens & mem_tokens) * 5)
        weight = imp * 10 + silence + overlap
        scored.append((weight, key, body))
    if not scored:
        return {'key': '', 'body': '', 'score': 0, 'reason': 'nothing_suitable', 'dropped': dropped}
    scored.sort(key=lambda row: (-row[0], row[1]))
    score, key, body = scored[0]
    return {'key': key, 'body': body, 'score': score, 'reason': 'wake_idle_selected', 'dropped': dropped}