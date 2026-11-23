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

DEFAULTS = {'enabled': True, 'min_importance': 4, 'cooldown_turns': 8, 'light_chars': 40}
# Name at most this many passed-over candidates in diagnostics; the rest are counted only, so a big
# candidate pool cannot make every request record a thousand reasons.
DROP_LIST_LIMIT = 40
# How much a candidate loses for coming only from Ombre's random "忽然想起来" draw.
DRIFT_PENALTY = 20

# A short light sentence, or one about work and technical trouble, is not the moment to bring up an
# old personal memory. This is a coarse gate on purpose: it can only suppress an association, never
# invent one, and it makes the "CI failure walks into dinner" case impossible.
_NOT_LIGHT = re.compile(
    r'报错|错误|日志|崩溃|卡死|部署|提交|合并|测试|代码|接口|工单|仓库|编译|安装|配置|权限|bug|crash|'
    r'\bci\b|deploy|commit|merge|build|error|stack|traceback|log|test|repo|ticket|install|config',
    re.I)


def settings(recall_cfg):
    """`[memory.recall]` association keys with defaults; bad values fall back, never raise."""
    raw = recall_cfg if isinstance(recall_cfg, dict) else {}
    result = dict(DEFAULTS)
    if raw.get('association_enabled') is False:
        result['enabled'] = False
    # The old `random_chance` key is ignored on purpose: it was a per-turn probability and there is
    # no probability any more. A config that still sets it changes nothing here.
    value = raw.get('association_min_importance')
    if type(value) is int and 0 <= value <= 10:
        result['min_importance'] = value
    value = raw.get('association_cooldown_turns')
    if type(value) is int and 0 <= value <= 200:
        result['cooldown_turns'] = value
    value = raw.get('association_light_chars')
    if type(value) is int and 8 <= value <= 400:
        result['light_chars'] = value
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


def choose(query, candidates, metas, last_shown, turn, already, recall_cfg=None, origin=None):
    """Pick at most one weak association.

    ``candidates`` are (memory_id, body) pairs from a finite pool: Ombre's importance-ordered list
    and/or its random "忽然想起来" hits; ``metas`` is the per-id metadata already read for this turn;
    ``last_shown`` maps id -> turn it was last shown; ``already`` is the set of ids this request
    already carries; ``origin`` says where each candidate came from ('importance' or 'drift'), so a
    random hit cannot win merely because it happened to be the only one. Returns
    ``{'key', 'body', 'score'}`` (key/body empty when nothing fits) and every candidate's reason.
    """
    options = settings(recall_cfg)
    result = {'key': '', 'body': '', 'score': 0, 'dropped': [], 'reason': ''}

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
        return finish(result)
    if not candidates:
        result['reason'] = 'no_candidates'
        return finish(result)
    if not light_chat(query, light_chars=options['light_chars']):
        result['reason'] = 'mood'
        for key, _ in candidates:
            drop(result, key, 'mood')
        return finish(result)
    turn = turn if type(turn) is int else 0
    already = already if isinstance(already, (set, frozenset, list, tuple)) else ()
    already = set(already)
    scored = []
    for key, body in candidates:
        if key in already:
            drop(result, key, 'already_in_context')
            continue
        importance = _importance(key, metas.get(key) if isinstance(metas, dict) else None)
        if importance < options['min_importance']:
            drop(result, key, 'not_meaningful')
            continue
        last = last_shown.get(key) if isinstance(last_shown, dict) else None
        if type(last) is int and options['cooldown_turns'] and turn - last <= options['cooldown_turns']:
            drop(result, key, 'association_cooldown')
            continue
        silence = 50 if type(last) is not int else min(max(0, turn - last), 50)
        weight = importance * 10 + silence
        # A memory that came up by Ombre's random draw may still surface, but only if it is clearly
        # strong on its own terms: the importance-ordered pool wins ties against a lucky hit.
        if isinstance(origin, dict) and origin.get(key) == 'drift':
            weight -= DRIFT_PENALTY
        scored.append((weight, key, body))
    if not scored:
        result['reason'] = 'nothing_suitable'
        return finish(result)
    # Deterministic: strongest score wins; a tie falls back to the id so a restart cannot reshuffle it.
    scored.sort(key=lambda row: (-row[0], row[1]))
    score, key, body = scored[0]
    for _, other, _ in scored[1:]:
        drop(result, other, 'lower_score')
    result.update({'key': key, 'body': body, 'score': score})
    return finish(result)
