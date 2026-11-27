"""P6-S6-2: Autonomous leisure natural association, strict leisure gating, and autonomy output audit.

Zero external model calls, deterministic scoring, and strict separation between:
- work / active task wakes (hard-blocked: zero interference),
- genuine idle / leisure wakes (eligible for contextually grounded long-tail associations).

Reuses the existing memory pool, S5 shared-embedding cosine similarity, cooldown/streak backoff,
personal meaning (`why_remembered`), and long-term importance (`importance` / `pinned`, plus
optional read-only S6-1 dynamic evaluation signals when present).
"""
from __future__ import annotations

import re

from veil.brain.recall.brain_retrieval import tokenize
from veil.brain.recall.context_signal import analyze as analyze_context
from veil.brain.recall.natural_association import DROP_LIST_LIMIT, _NOT_LIGHT

DEFAULTS = {
    'enabled': False,               # Default off for controlled rollout (P6-S6-2).
    'min_similarity': 0.38,         # Contextual similarity gate for longer wake summaries.
    'min_importance': 5,            # Prefer meaningful long-tail experiences.
    'cooldown_turns': 12,           # Independent / longer cooldown for leisure wakes.
    'pool_size': 36,
}

# Work/task cues specific to autonomous wakes (scheduled reminders, unfinished engineering work, etc.).
_WAKE_WORK_CUE = re.compile(
    r'(?:工单|排查|修复|重构|回归|单测|单元测试|集成测试|提交|合并|部署|上线|编译|构建|报错|异常|崩溃|'
    r'日志|堆栈|分支|仓库|脚本|接口|配置|权限|同步|验收|巡检|迁移|备份|恢复|清理|安装|升级|调试|'
    r'\b(?:pr\s*#?\d+|ci|cd|bug|fix|debug|refactor|deploy|commit|merge|rebase|test|pytest|unittest|'
    r'build|traceback|exception|error|ticket|repo|branch|migration|backup|audit|verify|benchmark)\b)',
    re.I,
)

# Leisure / personal interest cues (creative work, reflection, reading, journaling, resting, etc.).
_LEISURE_CUE = re.compile(
    r'(?:日记|随笔|画画|涂鸦|发呆|散步|休息|闲逛|旅行|灵感|故事|音乐|电影|小说|诗|想法|整理心情|'
    r'看看世界|小岛|花园|咖啡|茶|夜里|睡前|放空|安静|创作|涂抹|素描|'
    r'\b(?:diary|journal|sketch|draw|paint|poem|story|music|movie|novel|walk|rest|quiet|wander|'
    r'travel|nowhere|garden|island|coffee|tea|reflect|thought|curious|idle|leisure|creative)\b)',
    re.I,
)

# Third-person stage directions / narrative actions that violate Peter's grounded first-person persona.
_THIRD_PERSON_ACTION = re.compile(
    r'(?:'
    r'\*[^*\n]{2,80}\*'
    r'|（(?:他|彼得|Peter)[^）\n]{1,40}）'
    r'|\((?:he|peter)\s+(?:looks?|glances?|leans?|sighs?|smiles?|sits?|stands?|walks?|pauses?|opens?|checks?|types?|taps?|nods?|shakes?)\b[^)\n]{0,60}\)'
    r'|(?:^|\n)\s*(?:Peter|He)\s+(?:looks?|glances?|leans?|sighs?|smiles?|sits?|stands?|walks?|pauses?|opens?|checks?|types?|taps?|decides?\s+to)\b'
    r'|(?:^|\n)\s*(?:他|彼得)(?:起身|转头|看了看|叹了口气|笑了笑|坐下来|打开了|点开了|伸了个懒腰|静静地)'
    r')',
    re.I,
)

# Claims of concrete tool-backed actions when no tool actually succeeded in this wake.
_UNBACKED_ACTION_CLAIM = re.compile(
    r'(?:'
    r'(?:我(?:已经|刚刚|刚才|刚)|已(?:经)?)(?:查看了|检查了|打开了|读取了|翻看了|搜索了|查了|修改了|写入了|保存了|更新了|创建了|删除了|发送了|发了消息|整理了桌面|画好了)'
    r'|\bI\s+(?:just\s+)?(?:checked|opened|read|inspected|searched|updated|wrote|saved|edited|created|deleted|sent\s+(?:a\s+message|her)|tidied|drew|ran)\b'
    r')',
    re.I,
)

# Pure technical/error memory filter so work incident logs do not intrude on quiet leisure wakes.
_WORK_INCIDENT_MEMORY = re.compile(
    r'(?:traceback|stack\s*trace|exit\s*code|syntaxerror|typeerror|keyerror|importerror|'
    r'编译失败|构建失败|单测失败|回归测试|报错日志|堆栈信息|PR\s*#\d+|git\s+(?:push|rebase|merge))',
    re.I,
)


def settings(recall_cfg):
    """Extract `[memory.recall]` leisure association settings with safe defaults."""
    raw = recall_cfg if isinstance(recall_cfg, dict) else {}
    result = dict(DEFAULTS)
    if type(raw.get('leisure_association_enabled')) is bool:
        result['enabled'] = raw['leisure_association_enabled']
    # Respect master association switch and recall master switch.
    if raw.get('association_enabled') is False or raw.get('enabled') is False:
        result['master_enabled'] = False
    else:
        result['master_enabled'] = True
    sim = raw.get('leisure_association_min_similarity')
    if type(sim) in (int, float) and 0.05 <= float(sim) <= 0.95:
        result['min_similarity'] = float(sim)
    imp = raw.get('leisure_association_min_importance')
    if type(imp) is int and 0 <= imp <= 10:
        result['min_importance'] = imp
    cd = raw.get('leisure_association_cooldown_turns')
    if type(cd) is int and 0 <= cd <= 200:
        result['cooldown_turns'] = cd
    pool_size = raw.get('leisure_association_pool_size')
    if type(pool_size) is int and 8 <= pool_size <= 128:
        result['pool_size'] = pool_size
    return result


def classify_wake_mode(wake_context=None, query='', recent=None) -> dict:
    """Strictly separate idle/leisure autonomous wakes from work/task wakes.

    Returns a diagnostic-safe dict (no raw user text):
      - is_leisure: bool
      - mode: 'idle_leisure' | 'active_long_task' | 'pending_action' | 'checkpoint_active' |
              'unconfirmed_tool_recovery' | 'scheduled_task_wake' | 'technical_focus_task' |
              'non_autonomy_wake'
      - reasons: list[str]
      - leisure_signals: list[str]
    """
    ctx = wake_context if isinstance(wake_context, dict) else {}
    reasons = []
    leisure_signals = []

    wake_kind = str(ctx.get('wake_kind') or 'autonomy').strip().lower()
    if wake_kind not in ('autonomy', 'scheduled', 'natural', ''):
        return {
            'is_leisure': False,
            'mode': 'non_autonomy_wake',
            'reasons': [f'wake_kind:{wake_kind}'],
            'leisure_signals': [],
        }

    # 1. Active checkpoint (work or investigation) always blocks leisure association.
    if ctx.get('has_work_checkpoint') or ctx.get('has_investigation_checkpoint'):
        if ctx.get('has_work_checkpoint'):
            reasons.append('active_work_checkpoint')
        if ctx.get('has_investigation_checkpoint'):
            reasons.append('active_investigation_checkpoint')
        return {
            'is_leisure': False,
            'mode': 'checkpoint_active',
            'reasons': reasons,
            'leisure_signals': [],
        }

    # 2. Active long task (non-completed, non-stale) blocks leisure association.
    if ctx.get('has_pending_action'):
        return {
            'is_leisure': False,
            'mode': 'pending_action',
            'reasons': ['long_task_pending_action'],
            'leisure_signals': [],
        }

    if ctx.get('has_active_long_task'):
        task_status = str(ctx.get('long_task_status') or 'active').strip().lower()
        task_title = str(ctx.get('long_task_title') or '').strip()
        if task_status not in ('completed', 'done', 'cancelled', 'paused'):
            reasons.append(f'active_long_task:{task_status}')
            return {
                'is_leisure': False,
                'mode': 'active_long_task',
                'reasons': reasons,
                'leisure_signals': [],
            }
        # Even a paused task with explicit work cues is treated carefully if scheduled to resume it.
        if task_title and _WAKE_WORK_CUE.search(task_title):
            reasons.append('paused_work_task_present')

    # 3. Previous wake left unconfirmed tools or failed mid-task recovery.
    prev_unconfirmed = int(ctx.get('previous_unconfirmed_tools') or 0)
    prev_result = str(ctx.get('previous_result') or '').strip()
    if prev_unconfirmed > 0:
        return {
            'is_leisure': False,
            'mode': 'unconfirmed_tool_recovery',
            'reasons': ['previous_unconfirmed_tools'],
            'leisure_signals': [],
        }

    # 4. Scheduled alarm wake with work/task reason.
    sched = ctx.get('scheduled_reason')
    sched_text = ' '.join(sched) if isinstance(sched, list) else str(sched or '')
    if sched_text.strip():
        sched_sig = analyze_context(sched_text, recent=None)
        if sched_sig.get('is_work_task') or _WAKE_WORK_CUE.search(sched_text):
            return {
                'is_leisure': False,
                'mode': 'scheduled_task_wake',
                'reasons': ['scheduled_work_alarm'],
                'leisure_signals': [],
            }
        if _LEISURE_CUE.search(sched_text):
            leisure_signals.append('scheduled_leisure_note')

    # 5. Inspect previous wake focus / next_step and structured recall query for active technical tasks.
    prev_focus = str(ctx.get('previous_focus') or '').strip()
    prev_next = str(ctx.get('previous_next_step') or '').strip()
    focus_blob = '\n'.join(part for part in (prev_focus, prev_next) if part)
    if focus_blob:
        if prev_result in ('失败', '部分完成', '被打断') and _WAKE_WORK_CUE.search(focus_blob):
            return {
                'is_leisure': False,
                'mode': 'unconfirmed_tool_recovery',
                'reasons': ['previous_failed_work_step'],
                'leisure_signals': [],
            }
        focus_sig = analyze_context(focus_blob, recent=None)
        if focus_sig.get('has_tech_cmd') or _WAKE_WORK_CUE.search(focus_blob):
            # Only allow if it is clearly a leisure creative note without concrete engineering task cues.
            if not (_LEISURE_CUE.search(focus_blob) and not focus_sig.get('has_tech_cmd')):
                return {
                    'is_leisure': False,
                    'mode': 'technical_focus_task',
                    'reasons': ['previous_focus_or_next_step_work'],
                    'leisure_signals': [],
                }

    # 6. Check the recall query itself (excluding boilerplate headings).
    q_text = str(query or '').strip()
    if q_text:
        # Strip fixed boilerplate lines from _recall_query so "(none)" never misleads.
        meaningful_lines = []
        for line in q_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.endswith('(none)'):
                continue
            if stripped.startswith('Active long task:'):
                val = stripped.partition(':')[2].strip()
                if val and val != '(none)':
                    return {
                        'is_leisure': False,
                        'mode': 'active_long_task',
                        'reasons': ['query_active_long_task'],
                        'leisure_signals': [],
                    }
                continue
            meaningful_lines.append(stripped)
        cleaned_q = '\n'.join(meaningful_lines)
        if cleaned_q:
            q_sig = analyze_context(cleaned_q, recent=None)
            if q_sig.get('has_tech_cmd') or (q_sig.get('is_work_task') and not _LEISURE_CUE.search(cleaned_q)):
                return {
                    'is_leisure': False,
                    'mode': 'technical_focus_task',
                    'reasons': ['query_work_task_signal'],
                    'leisure_signals': [],
                }
            if _WAKE_WORK_CUE.search(cleaned_q) and not _LEISURE_CUE.search(cleaned_q):
                return {
                    'is_leisure': False,
                    'mode': 'technical_focus_task',
                    'reasons': ['query_wake_work_cue'],
                    'leisure_signals': [],
                }
            if _LEISURE_CUE.search(cleaned_q):
                leisure_signals.append('query_leisure_cue')

    if ctx.get('has_thoughts'):
        leisure_signals.append('active_thoughts')
    if ctx.get('has_life_state'):
        leisure_signals.append('life_state_present')
    if not leisure_signals:
        leisure_signals.append('open_idle_wake')

    return {
        'is_leisure': True,
        'mode': 'idle_leisure',
        'reasons': reasons or ['no_active_task'],
        'leisure_signals': leisure_signals,
    }


def _effective_importance(meta: dict | None) -> tuple[int, bool]:
    """Read long-term importance with graceful read-only compatibility for S6-1 dynamic fields."""
    if not isinstance(meta, dict):
        return 0, False
    pinned = str(meta.get('pinned', '')).lower() == 'true' or meta.get('pinned') is True
    # Smoothly honor optional S6-1 dynamic evaluation columns if present, without requiring them.
    raw_imp = meta.get('effective_importance')
    if raw_imp is None:
        raw_imp = meta.get('dynamic_importance')
    if raw_imp is None:
        raw_imp = meta.get('importance')
    try:
        imp = int(round(float(raw_imp))) if raw_imp is not None else 0
    except (TypeError, ValueError):
        imp = 0
    if pinned:
        imp = max(imp, 9)
    return max(0, min(10, imp)), pinned


def choose_leisure(query, candidates, metas, last_shown, turn, already, recall_cfg=None, *,
                   wake_context=None, similarities=None, recent=None, previous=None) -> dict:
    """Pick at most one contextually grounded long-tail memory for an idle autonomous wake.

    Deterministically combines:
    - strict leisure gate (`classify_wake_mode`),
    - semantic similarity over the shared turn embedding (hard gate; never bypassed by importance),
    - personal meaning (`why_remembered` presence and situational lexical resonance),
    - long-term importance (`importance` / `pinned`, plus optional S6-1 read-only signals),
    - cooldown & repeat-streak backoff + deduplication (`already`).
    """
    options = settings(recall_cfg)
    gate = classify_wake_mode(wake_context, query=query, recent=recent)
    result = {
        'key': '',
        'body': '',
        'score': 0.0,
        'similarity': None,
        'meaning_bonus': 0.0,
        'dropped': [],
        'dropped_total': 0,
        'dropped_more': 0,
        'reason': '',
        'mode': 'leisure',
        'context_mode': gate['mode'],
        'wake_gate': gate,
    }

    def drop(key, reason):
        result['dropped_total'] += 1
        if len(result['dropped']) < DROP_LIST_LIMIT:
            result['dropped'].append({'id': key, 'reason': reason})

    def finish():
        result['dropped_more'] = max(0, result['dropped_total'] - len(result['dropped']))
        return result

    if not options['master_enabled']:
        result['reason'] = 'master_disabled'
        for key, _ in candidates or ():
            drop(key, 'master_disabled')
        return finish()

    if not options['enabled']:
        result['reason'] = 'leisure_disabled'
        for key, _ in candidates or ():
            drop(key, 'leisure_disabled')
        return finish()

    if not gate['is_leisure']:
        result['reason'] = f"task_wake_suppressed:{gate['mode']}"
        for key, _ in candidates or ():
            drop(key, 'task_wake_suppressed')
        return finish()

    if not candidates:
        result['reason'] = 'no_candidates'
        return finish()

    sim_map = similarities if isinstance(similarities, dict) else None
    if not sim_map:
        # Never fall back to blind ungrounded rotation during autonomous wakes.
        result['reason'] = 'no_similarity_signal'
        for key, _ in candidates:
            drop(key, 'no_similarity_signal')
        return finish()

    turn = turn if type(turn) is int else 0
    already_set = set(already or ())
    last_map = last_shown if isinstance(last_shown, dict) else {}
    min_sim = float(options['min_similarity'])
    min_imp = int(options['min_importance'])
    base_cooldown = int(options['cooldown_turns'])

    # Build situational tokens from the wake context + query for personal-meaning resonance.
    situation_parts = [str(query or '')]
    if isinstance(wake_context, dict):
        for field in ('scheduled_reason', 'previous_focus', 'previous_next_step', 'thought_preview', 'life_preview'):
            val = wake_context.get(field)
            if isinstance(val, list):
                situation_parts.extend(str(x) for x in val if x)
            elif isinstance(val, str) and val.strip():
                situation_parts.append(val.strip())
    situation_tokens = set(tokenize(' '.join(situation_parts), stopwords_enabled=True))

    scored = []
    for key, body in candidates:
        if key in already_set:
            drop(key, 'already_in_context')
            continue
        meta = metas.get(key) if isinstance(metas, dict) else None
        imp, pinned = _effective_importance(meta)
        if imp < min_imp:
            drop(key, 'below_min_importance')
            continue
        body_head = str(body or '')[:600]
        if _WORK_INCIDENT_MEMORY.search(body_head) or (not pinned and _NOT_LIGHT.search(body_head) and imp < 7):
            drop(key, 'work_incident_memory')
            continue
        last = last_map.get(key)
        gap = base_cooldown
        if isinstance(previous, dict) and previous.get('key') == key:
            streak = previous.get('repeat')
            streak = streak if type(streak) is int and 0 <= streak <= 8 else 0
            gap = gap * (2 ** min(streak, 3))
        if type(last) is int and gap and turn - last <= gap:
            drop(key, 'association_cooldown')
            continue

        sim = sim_map.get(key)
        if sim is None:
            drop(key, 'no_vector')
            continue
        sim = float(sim)
        if sim < min_sim:
            drop(key, 'low_similarity')
            continue

        silence = 50 if type(last) is not int else min(max(0, turn - last), 50)
        why = str((meta or {}).get('why_remembered') or '').strip()
        meaning_bonus = 0.0
        if why:
            meaning_bonus += 2.0
            if situation_tokens:
                why_tokens = set(tokenize(why, stopwords_enabled=True))
                shared = len(situation_tokens & why_tokens)
                if shared:
                    meaning_bonus += min(6.0, shared * 2.0)

        # Light lexical resonance with title/body (bounded so semantic similarity remains primary).
        lexical_bonus = 0.0
        if situation_tokens:
            title = str((meta or {}).get('title') or '')
            mem_tokens = set(tokenize(title + ' ' + body_head[:300], stopwords_enabled=True))
            shared_mem = len(situation_tokens & mem_tokens)
            if shared_mem:
                lexical_bonus = min(5.0, shared_mem * 1.25)

        # Semantic similarity is primary; personal meaning, long-term importance & silence break ties
        # and surface neglected meaningful experiences without ever bypassing min_similarity.
        weight = round(
            sim * 100.0
            + imp * 1.4
            + min(silence, 45) * 0.30
            + meaning_bonus
            + lexical_bonus
            + (3.5 if pinned else 0.0),
            4,
        )
        scored.append((weight, sim, meaning_bonus, key, body))

    if not scored:
        result['reason'] = 'low_relevance'
        return finish()

    scored.sort(key=lambda row: (-row[0], row[3]))
    weight, sim, meaning_bonus, key, body = scored[0]
    for _, _, _, other, _ in scored[1:]:
        drop(other, 'lower_score')
    result.update({
        'key': key,
        'body': body,
        'score': weight,
        'similarity': round(sim, 4),
        'meaning_bonus': round(meaning_bonus, 2),
        'reason': 'leisure_selected',
    })
    return finish()


def audit_autonomy_output(answer: str, tool_summaries=None) -> dict:
    """Detect third-person narration or unbacked tool-action roleplay in autonomous wake output.

    Pure diagnostic audit (never alters or deletes user content); recorded in `autonomy.jsonl`
    and recall diagnostics so Lan and VEIL Lab can verify grounded autonomous behaviour.
    """
    text = str(answer or '')
    tools = [item for item in (tool_summaries or ()) if isinstance(item, dict)]
    successful = [item for item in tools if item.get('state') == '成功']
    third_person = bool(_THIRD_PERSON_ACTION.search(text))
    unbacked_claim = bool(not successful and _UNBACKED_ACTION_CLAIM.search(text))
    flags = []
    if third_person:
        flags.append('third_person_narration')
    if unbacked_claim:
        flags.append('unbacked_tool_claim')
    return {
        'clean': not flags,
        'third_person_narration': third_person,
        'unbacked_tool_claim': unbacked_claim,
        'flags': flags,
        'successful_tools': len(successful),
    }
