"""Ticket 422: weak natural association, without a fixed frequency or a random draw.

The old path took Ombre's "忽然想起来（非检索命中）" hits and, with probability `random_chance`,
showed one. That is a fixed chance per turn, and it can put a work emergency into a dinner
conversation just because the dice fell that way.

This module keeps the same source (Ombre's own non-search recollections) and decides with what the
ticket asks for, deterministically:

- does this moment have room for a memory at all (short, light, not a work/log topic),
- how much this memory matters to Peter personally (stored importance; a pinned memory counts high),
- has it been shown recently (cooldown),
- how long since it last surfaced,
- is it already in this request.

Only the best one may surface, and "none" is a normal answer: a turn with nothing suitable carries
no association at all. There is no "every N turns" rule and no probability. Two turns with the same
state always pick the same memory, which is what makes the behaviour explainable.

No model call is made here. It is arithmetic over candidates the normal recall already fetched.
"""
import re

DEFAULTS = {
    'enabled': True,
    'min_importance': 4,
    'cooldown_turns': 8,
    'light_chars': 40,
    # P6-S5: independent candidate switch for context-aware natural association (default off).
    'contextual_enabled': False,
    'min_similarity': 0.52,
    'pool_size': 32,
}
# Name at most this many passed-over candidates in diagnostics; the rest are counted only, so a big
# candidate pool cannot make every request record a thousand reasons.
DROP_LIST_LIMIT = 40
# How much a candidate loses for coming only from Ombre's random "忽然想起来" draw.
DRIFT_PENALTY = 20

# A short light sentence, or one about work and technical trouble, is not the moment to bring up an
# old personal memory. This is a coarse gate on purpose: it can only suppress an association, never
# invent one, and it makes the "CI failure walks into dinner" case impossible.
_NOT_LIGHT = re.compile(
    r'报错|错误|日志|崩溃|卡死|部署|提交|合并|测试|代码|接口|工单|仓库|编译|安装|配置|权限|加班|故障|'
    r'开会|上班|下班|加班|值班|客户|线上|紧急|上线|需求|排期|会议|bug|crash|deadline|meeting|shift|'
    r'\bci\b|deploy|commit|merge|build|error|stack|traceback|log|test|repo|ticket|install|config',
    re.I)


def settings(recall_cfg):
    """`[memory.recall]` association keys with defaults; bad values fall back, never raise."""
    raw = recall_cfg if isinstance(recall_cfg, dict) else {}
    result = dict(DEFAULTS)
    if raw.get('association_enabled') is False or raw.get('enabled') is False:
        result['enabled'] = False
    # Legacy opt-out: an install that switched the old random recall off with 0 keeps associations
    # off after upgrading. The value is still not used as a probability.
    if raw.get('random_chance') == 0:
        result['enabled'] = False
    value = raw.get('association_min_importance')
    if type(value) is int and 0 <= value <= 10:
        result['min_importance'] = value
    value = raw.get('association_cooldown_turns')
    if type(value) is int and 0 <= value <= 200:
        result['cooldown_turns'] = value
    value = raw.get('association_light_chars')
    if type(value) is int and 8 <= value <= 400:
        result['light_chars'] = value
    if type(raw.get('association_contextual_enabled')) is bool:
        result['contextual_enabled'] = raw['association_contextual_enabled']
    sim = raw.get('association_min_similarity')
    if type(sim) in (int, float) and 0.05 <= float(sim) <= 0.95:
        result['min_similarity'] = float(sim)
    pool_size = raw.get('association_pool_size')
    if type(pool_size) is int and 8 <= pool_size <= 128:
        result['pool_size'] = pool_size
    return result


def light_chat(query, *, light_chars=40):
    """Is this sentence the kind of easy talk a personal memory can surface in?"""
    text = query if isinstance(query, str) else ''
    text = text.strip()
    if not text or len(text) > light_chars:
        return False
    return not _NOT_LIGHT.search(text)


def _importance(item, metas_key):
    value = (metas_key or {}).get('importance')
    importance = value if type(value) is int else 0
    if str((metas_key or {}).get('pinned', '')).lower() == 'true':
        importance = max(importance, 9)
    return importance


def choose(query, candidates, metas, last_shown, turn, already, recall_cfg=None, origin=None,
           previous=None, *, similarities=None, recent=None, context_signal=None):
    """Pick at most one natural association.

    When ``association_contextual_enabled`` is False (default), runs the deterministic Ticket 422
    baseline unchanged. When enabled, uses situational gating (suppressing pure work/error tasks
    and bare acknowledgements while allowing work-induced emotional sharing) plus a mandatory
    semantic similarity threshold over the shared turn embedding — importance and silence rank
    eligible candidates but can NEVER bypass the similarity gate.
    """
    options = settings(recall_cfg)
    contextual = bool(options.get('contextual_enabled'))
    result = {'key': '', 'body': '', 'score': 0, 'dropped': [], 'reason': '',
              'mode': 'contextual' if contextual else 'legacy', 'similarity': None,
              'context_mode': ''}

    def drop(result, key, reason):
        # Only what a diagnostic page can read; the count of passed-over candidates stays exact.
        result['dropped_total'] = result.get('dropped_total', 0) + 1
        if len(result['dropped']) < DROP_LIST_LIMIT:
            result['dropped'].append({'id': key, 'reason': reason})

    def finish(result):
        result['dropped_more'] = max(0, result.get('dropped_total', len(result['dropped']))
                                     - len(result['dropped']))
        return result

    if not options['enabled']:
        result['reason'] = 'disabled'
        for key, _ in candidates:
            drop(result, key, 'disabled')
        return finish(result)
    if not candidates:
        result['reason'] = 'no_candidates'
        return finish(result)

    if contextual:
        from veil.brain.recall.context_signal import analyze as analyze_context
        signal = context_signal if isinstance(context_signal, dict) else analyze_context(query, recent)
        result['context_mode'] = signal.get('mode') or ''
        if not signal.get('allow_association'):
            gate_reason = ('work_task_suppressed' if signal.get('is_work_task')
                           else 'low_context_signal')
            result['reason'] = gate_reason
            for key, _ in candidates:
                drop(result, key, gate_reason)
            return finish(result)
        sim_map = similarities if isinstance(similarities, dict) else None
        if not sim_map:
            # Never fall back to blind rotation without semantic similarity.
            result['reason'] = 'no_similarity_signal'
            for key, _ in candidates:
                drop(result, key, 'no_similarity_signal')
            return finish(result)
    else:
        if not light_chat(query, light_chars=options['light_chars']):
            result['reason'] = 'mood'
            for key, _ in candidates:
                drop(result, key, 'mood')
            return finish(result)
        sim_map = None

    turn = turn if type(turn) is int else 0
    already = already if isinstance(already, (set, frozenset, list, tuple)) else ()
    already = set(already)
    min_sim = float(options.get('min_similarity', 0.34))
    scored = []
    for key, body in candidates:
        if key in already:
            drop(result, key, 'already_in_context')
            continue
        meta = metas.get(key) if isinstance(metas, dict) else None
        importance = _importance(key, meta)
        if not contextual and importance < options['min_importance']:
            drop(result, key, 'not_meaningful')
            continue
        if not contextual and _NOT_LIGHT.search(str(body or '')[:600]):
            drop(result, key, 'work_topic')
            continue
        last = last_shown.get(key) if isinstance(last_shown, dict) else None
        gap = options['cooldown_turns']
        if isinstance(previous, dict) and previous.get('key') == key:
            streak = previous.get('repeat')
            streak = streak if type(streak) is int and 0 <= streak <= 8 else 0
            gap = gap * (2 ** min(streak, 3))
        if type(last) is int and gap and turn - last <= gap:
            drop(result, key, 'association_cooldown')
            continue
        silence = 50 if type(last) is not int else min(max(0, turn - last), 50)
        if contextual:
            sim = sim_map.get(key)
            if sim is None:
                drop(result, key, 'no_vector')
                continue
            sim = float(sim)
            if sim < min_sim:
                drop(result, key, 'low_similarity')
                continue
            pinned = str((meta or {}).get('pinned', '')).lower() == 'true'
            # Semantic similarity is the primary driver; importance & silence protect long-tail
            # memories without drowning out highly relevant ordinary daily experiences.
            weight = round(sim * 100.0 + importance * 1.2 + min(silence, 40) * 0.25
                           + (3.0 if pinned else 0.0), 4)
            scored.append((weight, sim, key, body))
        else:
            weight = importance * 10 + silence
            # A memory that came up by Ombre's random draw may still surface, but only if it is clearly
            # strong on its own terms: the importance-ordered pool wins ties against a lucky hit.
            if isinstance(origin, dict) and origin.get(key) == 'drift':
                weight -= DRIFT_PENALTY
            scored.append((weight, None, key, body))
    if not scored:
        result['reason'] = 'low_relevance' if contextual else 'nothing_suitable'
        return finish(result)
    # Deterministic: strongest score wins; a tie falls back to the id so a restart cannot reshuffle it.
    scored.sort(key=lambda row: (-row[0], row[2]))
    score, sim, key, body = scored[0]
    for _, _, other, _ in scored[1:]:
        drop(result, other, 'lower_score')
    result.update({'key': key, 'body': body, 'score': score,
                   'similarity': round(sim, 4) if sim is not None else None})
    return finish(result)
