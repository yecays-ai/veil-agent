"""P6-S5: lightweight deterministic situational context analysis for recall judge and association.

Pure local heuristics over the current message and recent turns — zero model calls, zero network,
and never a hard-coded blocklist that permanently strips relational value from shared memories
merely because they contain technical words.

Its job is to distinguish:
- pure technical/error/command work tasks (suppress irrelevant old-memory intrusion),
- work-induced emotional/relational sharing (allow empathy & shared experience recall),
- emotional / relational / daily chat,
- explicit past recall & follow-up continuations,
- bare acknowledgements / pure stickers (do not force associations).
"""
from __future__ import annotations

import re

_STICKER_ONLY = re.compile(r'^(?:\s*\[(?:表情包|图片|语音|sticker|image)[^\]]*\]\s*)+$', re.I)
_PURE_PUNCT_OR_EMOJI = re.compile(r'^[\s\W_]+$', re.U)

_TECH_ERROR_OR_CMD = re.compile(
    r'(?:traceback|exception|stack\s*trace|exit\s*code|errno|syntaxerror|typeerror|keyerror|'
    r'importerror|attributeerror|valueerror|assertionerror|nullpointer|segfault|http\s*[45]\d\d|'
    r'报错|堆栈|崩溃|卡死|编译失败|构建失败|单测|单元测试|回归测试|日志里|查一下日志|'
    r'帮我(?:查|看|修|写|改|跑|测|部署|提交|合并|拉取|排查|定位|重构|整理|总结|翻译|分析|检查|生成|对比)|'
    r'请(?:帮我|检查|排查|分析|总结|翻译|修改|运行|执行|测试|确认)|'
    r'(?:git|pytest|unittest|npm|pnpm|pip|docker|kubectl|curl|python|node)\s+[a-z0-9_./-]+|'
    r'```|https?://|[A-Za-z]:\\[^\s]+|'
    r'\b(?:fix|debug|refactor|deploy|commit|merge|cherry-pick|rebase|PR\s*#?\d+|'
    r'please\s+(?:check|fix|help|run|explain|summarize|write|review))\b)',
    re.I,
)

_WORK_TOPIC = re.compile(
    r'(?:代码|接口|工单|仓库|分支|编译|安装|配置|权限|部署|提交|合并|测试|日志|报错|故障|线上|'
    r'加班|开会|上班|下班|值班|客户|需求|排期|会议|项目|版本|发布|'
    r'\b(?:ci|cd|api|sql|json|bug|crash|deadline|meeting|shift|repo|ticket|config|build|test)\b)',
    re.I,
)

_EMOTION_OR_RELATION = re.compile(
    r'(?:好累|累死了|好烦|烦死了|崩溃了|委屈|难过|难受|开心|高兴|兴奋|紧张|焦虑|害怕|担心|感动|'
    r'生气|气死|无语|沮丧|失落|心累|撑不住|想哭|想你|抱抱|安慰|陪我|聊聊|吐个槽|吐槽|受不了|'
    r'压力大|睡不着|失眠|头疼|不舒服|好困|熬夜|谢谢你|爱你|喜欢你|讨厌|生气了|对不起|'
    r'\b(?:tired|exhausted|stressed|anxious|sad|upset|angry|frustrated|happy|excited|miss\s+you|'
    r'love\s+you|hug|vent|burnout|overwhelmed|lonely|hurt|sorry)\b)',
    re.I,
)

_EXPLICIT_PAST = re.compile(
    r'(?:记得|还记不记得|想起|想起来|上次|上回|那次|那天|那晚|那时|当时|去年|前年|以前|之前|'
    r'remember|recall|last\s+time|that\s+time|back\s+when|earlier|you\s+said|we\s+talked)',
    re.I,
)

_SHORT_ACK = frozenset({
    '嗯', '哦', '对', '是', '好', '行', '可以', '好的', '好呀', '嗯嗯', '嗯好', '对对', '对啊',
    '是的', '哈哈', '哈哈哈', 'ok', 'okay', 'yes', 'no', 'yep', 'nope', 'lol', 'haha', 'thx',
    'thanks', '谢谢', '马上', '来了', '收到', '知道了', '懂了', '行吧', '好吧',
})


def _clean_ack(text: str) -> str:
    return re.sub(r'[，,。.!！?？…~\s]+', '', (text or '').strip().lower())


def _recent_text(recent, *, max_turns=2, max_chars=240) -> str:
    rows = []
    for item in reversed(list(recent or [])):
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        content = item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str) or not content.strip():
            continue
        rows.append(content.strip()[:120])
        if len(rows) >= max_turns * 2:
            break
    rows.reverse()
    return '\n'.join(rows)[:max_chars]


def analyze(query: str, recent=None, *, explicit_past: bool = False, followup: bool = False) -> dict:
    """Classify the current conversational situation without calling any external model.

    Returns a compact, diagnostic-safe dict (no raw user text):
      - mode: 'explicit_past' | 'followup' | 'short_ack' | 'technical_task' |
              'work_emotion' | 'emotional_chat' | 'casual_chat'
      - allow_association: bool
      - is_work_task: bool
      - has_emotion_signal: bool
      - has_work_signal: bool
      - recent_emotion_signal: bool
      - reasons: list[str]
    """
    text = (query or '').strip()
    reasons = []
    if not text:
        return {
            'mode': 'short_ack',
            'allow_association': False,
            'is_work_task': False,
            'has_emotion_signal': False,
            'has_work_signal': False,
            'recent_emotion_signal': False,
            'reasons': ['empty_query'],
        }

    if _STICKER_ONLY.match(text):
        return {
            'mode': 'short_ack',
            'allow_association': False,
            'is_work_task': False,
            'has_emotion_signal': False,
            'has_work_signal': False,
            'recent_emotion_signal': False,
            'reasons': ['sticker_only'],
        }

    cleaned = _clean_ack(text)
    if not cleaned or _PURE_PUNCT_OR_EMOJI.match(text) or cleaned in _SHORT_ACK:
        return {
            'mode': 'short_ack',
            'allow_association': False,
            'is_work_task': False,
            'has_emotion_signal': False,
            'has_work_signal': False,
            'recent_emotion_signal': False,
            'reasons': ['short_ack'],
        }

    has_tech_cmd = bool(_TECH_ERROR_OR_CMD.search(text))
    has_work = has_tech_cmd or bool(_WORK_TOPIC.search(text))
    has_emotion = bool(_EMOTION_OR_RELATION.search(text))
    has_past = bool(explicit_past or _EXPLICIT_PAST.search(text))
    recent_window = _recent_text(recent)
    recent_emotion = bool(_EMOTION_OR_RELATION.search(recent_window)) if recent_window else False

    if has_tech_cmd:
        reasons.append('tech_error_or_cmd')
    elif has_work:
        reasons.append('work_topic')
    if has_emotion:
        reasons.append('emotion_or_relation')
    if recent_emotion:
        reasons.append('recent_emotion')
    if has_past:
        reasons.append('explicit_past')
    if followup:
        reasons.append('followup')

    if has_past:
        mode = 'explicit_past'
        allow_assoc = False
        is_work_task = False
    elif followup:
        mode = 'followup'
        allow_assoc = False
        is_work_task = False
    elif has_work and (has_emotion or (recent_emotion and not has_tech_cmd)):
        # Work-induced emotional / relational sharing: never suppress as a pure technical task.
        mode = 'work_emotion'
        allow_assoc = True
        is_work_task = False
    elif has_tech_cmd or (has_work and not has_emotion):
        mode = 'technical_task'
        allow_assoc = False
        is_work_task = True
    elif not has_emotion and not recent_emotion and len(text) > 70:
        # Long informational/task-like input without emotional or relational cues: keep quiet.
        mode = 'informational'
        allow_assoc = False
        is_work_task = False
        reasons.append('long_non_emotional')
    elif has_emotion:
        mode = 'emotional_chat'
        allow_assoc = True
        is_work_task = False
    else:
        mode = 'casual_chat'
        allow_assoc = True
        is_work_task = False

    return {
        'mode': mode,
        'allow_association': allow_assoc,
        'is_work_task': is_work_task,
        'has_emotion_signal': has_emotion,
        'has_work_signal': has_work,
        'recent_emotion_signal': recent_emotion,
        'reasons': reasons,
    }


def format_recent_context(recent, *, max_turns=2, user_clip=140, assistant_clip=100) -> str:
    """Compact recent dialogue context for the recall judge (bounded so 4s timeout stays safe)."""
    rows = []
    users = 0
    for item in reversed(list(recent or [])):
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        content = item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        cleaned = ' '.join(content.split())
        if not cleaned:
            continue
        if role == 'user':
            rows.append(('Lan', cleaned[:user_clip]))
            users += 1
            if users >= max_turns:
                break
        else:
            rows.append(('Peter', cleaned[:assistant_clip]))
    rows.reverse()
    return '\n'.join(f'{speaker}: {line}' for speaker, line in rows)
