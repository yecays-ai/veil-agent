"""Ticket 394 / P6-S3: Learned Context for Peter.

Four sections with independent UTF-8 byte budgets:
- facts (1800B): what he knows about Lan
- growth (2000B): what he learned from real experience, especially conflict repair
- relationship (1500B): shared agreements, boundaries, and relationship consensus
- ongoing (900B): things not finished yet, as items with a lifecycle

Facts, growth, and the relationship base sit in the stable prefix; ongoing is injected near the
user message so frequent updates do not invalidate the provider's prefix cache.
Lan's edits, locks, corrections, and deletions always win.
"""
import base64
import copy
import json
import os
import re
import sqlite3
import threading
import uuid
from collections import deque
from contextlib import ExitStack, contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from veil import prompts
from veil.brain.storage.brain_lock import write_lock
from veil.core.agent_types import UserError
from veil.core.model_settings import ModelSettings

SECTIONS = ('facts', 'growth')
ALL_SECTIONS = ('facts', 'growth', 'relationship', 'ongoing')

# P6-S3 / AB-A3: independent UTF-8 byte budgets per section (total 6200B).
SECTION_BUDGETS = {
    'facts': 1800,
    'growth': 2000,
    'relationship': 1500,
    'ongoing': 900,
}

# Ticket 422: the stable relationship base. Each item is one thing Peter currently understands about
# the two of them. `confirmed` and `uncertain` reach the model (the uncertain ones labelled as his
# guess); `outdated` is kept only as history and never sent.
RELATIONSHIP_STATUSES = ('confirmed', 'uncertain', 'outdated')
# Who says so. Lan's own words outrank repeated evidence, evidence outranks Peter's reading.
RELATIONSHIP_SOURCES = ('lan', 'evidence', 'peter')
SOURCE_RANK = {'lan': 3, 'evidence': 2, 'peter': 1}
EVIDENCE_STATES = ('valid', 'partial_stale', 'invalid', 'unavailable', 'rebutted', 'none')
# Ticket 422 review: a relationship item keeps the smallest evidence a program can check.
SOURCE_REF = re.compile(r'^(chat|memory|event|long_task):[A-Za-z0-9_-]{1,100}$')
# Ticket 413: ongoing means "not finished yet". Finished items stay in the file as completed so Lan
# can read, reopen or delete them, but only active/uncertain reach the model and count toward budget.
ONGOING_STATUSES = ('active', 'uncertain', 'completed')
ONGOING_LEGACY = {'archived': 'completed'}
DEFAULT_MAX_BYTES = sum(SECTION_BUDGETS.values())  # 6200
VERSION = 1

_AGREEMENT_RE = re.compile(
    r'^(?:对|好的?|好呀|嗯|行|可以|同意|确认|没错|是的|是啊|'
    r'ok|okay|yes|yeah|yep|agreed|agree|confirmed|right|correct)[!！。.\s]*$',
    re.IGNORECASE,
)
_NON_ASSERTION_RE = re.compile(''.join((
    r'["“”‘「」『』《》�\x00\x60:：；;。.!！?？\r\n]|(?<!\w)[\x27’]|[\x27’](?!\w)',
    r'|(?:说|觉得|认为|以为|坦言|表示|提到|声称|写道|问我|可能|大概|也许|假如|如果|万一|',
    r'只要|除非|假设|好像|据说|不确定|不一定|吗$|么$|吧$|难道|反话|开玩笑)',
    r'|\b(?:said|says|told|claims?|thinks?|thought|believes?|according to|heard|',
    r'guess|maybe|perhaps|possibly|probably|if|unless|suppose|imagine|joking)\b',
)), re.IGNORECASE)
_COMPOUND_QUESTION_RE = re.compile(
    r'[,，、/]|还是|或者|而且|并且|以及|又要|同时|不但|可不可以|是不是|要不要'
    r'|\b(?:and|or|but|also|then|otherwise)\b', re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r'(?:不要|不能|不可|不会|不想|别|禁止|勿|无需|不用|不再|从未|从来不|不喜欢|讨厌|反感|'
    r'\b(?:never|don\'t|doesn\'t|didn\'t|cannot|can\'t|won\'t|not|no\s+longer|stop|avoid|hate|dislike)\b)',
    re.IGNORECASE,
)
_REBUTTAL_CUE_RE = re.compile(
    r'(?:不对|错了|不算|别这样|不是的|收回|取消|改成|改为|不用了|不要再|其实我|'
    r'\b(?:actually|no,|not\s+anymore|forget\s+that|changed\s+my\s+mind|stop\s+doing|never\s+mind)\b)',
    re.IGNORECASE,
)
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_WORD_RE = re.compile(r'[a-z0-9]{2,}', re.IGNORECASE)


def _now():
    return datetime.now().astimezone().isoformat(timespec='microseconds')


def _text_bytes(value):
    return len(str(value or '').encode('utf-8'))


def _norm_text(value):
    return ' '.join(str(value or '').split()).strip().lower()


def _lexical_tokens(text):
    """Extract ASCII words and CJK bigrams/chars for lightweight programmatic support checks."""
    cleaned = _norm_text(text)
    tokens = set(_WORD_RE.findall(cleaned))
    cjk = ''.join(_CJK_RE.findall(cleaned))
    if len(cjk) == 1:
        tokens.add(cjk)
    elif len(cjk) >= 2:
        for idx in range(len(cjk) - 1):
            tokens.add(cjk[idx:idx + 2])
    return tokens


def _jaccard_similar(text_a, text_b, *, threshold=0.72, min_tokens=4):
    """Conservative lexical Jaccard check to catch minor rephrasings of vetoed/locked rules."""
    ta = _lexical_tokens(text_a)
    tb = _lexical_tokens(text_b)
    if len(ta) < min_tokens or len(tb) < min_tokens:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    return union > 0 and (inter / union) >= threshold


def _claim_units(text):
    """Split for comparison only; never use these units to rewrite a stored note."""
    return [piece.strip() for piece in re.split(r'\n+|(?<=[.!?;])\s+|(?<=[。！？；])', text)
            if piece.strip()]


def _matches_governed_claim(text, reference):
    """Match exact or high-overlap wording; this is a veto check, not entailment."""
    ref = _norm_text(reference)
    if not ref:
        return False
    for candidate in [text, *_claim_units(text)]:
        left = re.sub(r'^\s*(?:[-*+•]\s+|\d+[.)]\s+)', '', _norm_text(candidate)).rstrip('.!?。！？;；')
        right = ref.rstrip('.!?。！？;；')
        if (left == right or _jaccard_similar(left, right)
                or (len(right) >= 8 and re.search(r'(?<![a-z0-9])' + re.escape(right) + r'(?![a-z0-9])', left))):
            return True
    return False


def _veto_text(block, text, now, reason, item_id=None):
    norm = _norm_text(text)
    if not norm:
        return
    rows = block.setdefault('vetoed', [])
    if not any(isinstance(v, dict) and v.get('norm_text') == norm for v in rows):
        rows.append({'id': item_id, 'text': text, 'norm_text': norm,
                     'vetoed_at': now, 'reason': reason})


def _lift_exact_veto(block, text):
    norm = _norm_text(text)
    if block.get('vetoed'):
        block['vetoed'] = [row for row in block['vetoed']
                           if not (isinstance(row, dict) and row.get('norm_text') == norm)]


def _has_negation(text):
    return bool(_NEGATION_RE.search(str(text or '')))


def _literal_proposition(text):
    """Normalize only whitespace, case and a single terminal statement mark."""
    if not isinstance(text, str):
        return ''
    text = text.strip()
    if text.endswith(('.', '。', '!', '！')):
        text = text[:-1].rstrip()
    if _NON_ASSERTION_RE.search(text) or not (_CJK_RE.search(text) or _WORD_RE.search(text)):
        return ''
    return _norm_text(text)


def _confirmation_target(question):
    """Accept an explicit yes/no check of one literal proposition, never a free-form question."""
    if not isinstance(question, str) or not question.strip().endswith(('?', '？')):
        return ''
    question = question.strip()[:-1].rstrip()
    tag = re.fullmatch(r'(.+?)[,，]\s*(?:对吗|是吗|没错吧|right|correct)', question, re.I)
    if tag:
        target = tag[1]
    elif question.endswith('吗'):
        target = question[:-1]
    else:
        english = re.fullmatch(r'(?:do (you .+)|is it true that (.+))', question, re.I)
        if not english:
            return ''
        target = english[1] or english[2]
    if _COMPOUND_QUESTION_RE.search(target) or target.rstrip().endswith(('.', '。', '!', '！')):
        return ''
    return _literal_proposition(target)


def _supports_claim(user_text, claim_text, *, confirmation_target=''):
    """Require literal whole-claim support; unknown entailment must stay uncertain."""
    if not isinstance(user_text, str):
        return False
    claim = _literal_proposition(claim_text)
    if not claim:
        return False
    if _AGREEMENT_RE.fullmatch(user_text.strip()):
        return bool(confirmation_target and claim == confirmation_target)
    return claim == _literal_proposition(user_text)


def _resolve_section_budgets(settings):
    budgets = dict(SECTION_BUDGETS)
    if not isinstance(settings, dict):
        return budgets
    raw = settings.get('section_budgets')
    if isinstance(raw, dict):
        for sec in ALL_SECTIONS:
            val = raw.get(sec)
            if type(val) is int and val > 0:
                budgets[sec] = val
    for sec in ALL_SECTIONS:
        val = settings.get(f'{sec}_max_bytes')
        if type(val) is int and val > 0:
            budgets[sec] = val
    return budgets


class LearnedContext:
    def __init__(self, data_root, character_id, settings):
        if not isinstance(settings, dict) or not isinstance(settings.get('file'), str):
            raise UserError('Learned Context 配置不完整。')
        self.root = Path(data_root) / character_id
        self.path = self.root / settings['file']
        self.legacy_path = self.root / settings.get('legacy_file', 'about-lan.md')
        self.section_budgets = _resolve_section_budgets(settings)
        self.max_bytes = sum(self.section_budgets.values())
        self.legacy_max_bytes = settings.get('max_bytes')
        self.versions = self.root / (Path(self.path).stem + '.versions.jsonl')
        self.error_path = self.root / 'peter-page-errors.jsonl'
        self._instance_lock = threading.RLock()
        self._storage_lock_held = False
        self._lock_store = SimpleNamespace(root=self.root.parent)
        self._journal = self.path.with_name(self.path.name + '.pending')
        self._logged_error = ''
        self._chat_cache_key = None
        self._chat_cache = {}

    @property
    def lock(self):
        return self._storage_lock()

    @contextmanager
    def _storage_lock(self):
        """Serialize the whole read/modify/write with other instances and Brain bundles."""
        with self._instance_lock:
            if self._storage_lock_held:
                yield
                return
            with ExitStack() as stack:
                try:
                    stack.enter_context(write_lock(self._lock_store, self.root.name))
                except OSError as exc:
                    raise UserError(f'无法取得 {self.path.name} 的保存锁：{exc}') from exc
                self._storage_lock_held = True
                try:
                    self._recover_storage_transaction()
                    yield
                finally:
                    self._storage_lock_held = False

    def invalidate_evidence_cache(self):
        with self._instance_lock:
            self._chat_cache_key = None
            self._chat_cache = {}

    # ---------- reading ----------

    def _legacy_text(self):
        try:
            return self.legacy_path.read_text(encoding='utf-8-sig')
        except OSError:
            return ''

    def _empty(self, facts=''):
        return {
            'version': VERSION,
            'facts': {'text': facts, 'updated_at': _now() if facts else ''},
            'growth': {'text': '', 'updated_at': ''},
            'relationship': {'items': [], 'vetoed': [], 'updated_at': ''},
            'ongoing': {'items': [], 'updated_at': ''},
            'updated_at': _now(),
        }

    def _migrate(self):
        if self.path.exists():
            return
        self._commit_with_version('migrate', None, self._empty(self._legacy_text()))

    def _load(self):
        with self.lock:
            self._migrate()
            try:
                raw = self.path.read_text(encoding='utf-8')
            except OSError as exc:
                raise UserError(
                    f'读不到 {self.path.name}：{exc}。请保留 data 文件夹并查看 {self.error_path.name}。'
                ) from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise UserError(
                    f'{self.path.name} 第 {exc.lineno} 行第 {exc.colno} 列不是合法 JSON：{exc.msg}。'
                    f'请恢复到上一个版本；当前聊天会继续使用旧笔记。错误记录在 {self.error_path}。'
                ) from exc
            self._upgrade_ongoing(data)
            self._upgrade_relationship(data)
            self._validate(data)
            return data

    @staticmethod
    def _upgrade_relationship(data):
        """Ticket 422: files written before this ticket have no relationship block; read it as empty."""
        if not isinstance(data, dict):
            return
        if 'relationship' not in data:
            data['relationship'] = {'items': [], 'vetoed': [], 'updated_at': ''}
        elif isinstance(data['relationship'], dict) and 'vetoed' not in data['relationship']:
            # Keep in-memory compatibility without mutating disk until a real save occurs.
            pass

    @staticmethod
    def _upgrade_ongoing(data):
        """Ticket 413: archived is gone. Read old entries as completed so old files stay usable."""
        ongoing = data.get('ongoing') if isinstance(data, dict) else None
        items = ongoing.get('items') if isinstance(ongoing, dict) else None
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get('status') in ONGOING_LEGACY:
                item['status'] = ONGOING_LEGACY[item['status']]
            if item.get('status') == 'completed' and not isinstance(item.get('completed_at'), str):
                item['completed_at'] = item.get('updated_at') or _now()

    def _validate(self, data):
        """Structural validation only; over-budget sections remain readable (capacity_degraded)."""
        if not isinstance(data, dict) or data.get('version') != VERSION:
            raise UserError(f'{self.path.name} 版本或结构不对。请恢复上一个版本。')
        for section in SECTIONS:
            value = data.get(section)
            if not isinstance(value, dict) or not isinstance(value.get('text'), str):
                raise UserError(f'{self.path.name} 的 {section} 块不完整。请恢复上一个版本。')
            if not isinstance(value.get('updated_at', ''), str):
                raise UserError(f'{self.path.name} 的 {section}.updated_at 无效。')
        ongoing = data.get('ongoing')
        if not isinstance(ongoing, dict) or not isinstance(ongoing.get('items'), list):
            raise UserError(f'{self.path.name} 的 ongoing 块不完整。请恢复上一个版本。')
        self._validate_items(ongoing['items'])
        block = data.get('relationship') or {}
        if not isinstance(block, dict) or not isinstance(block.get('items'), list):
            raise UserError(f'{self.path.name} 的 relationship 块不完整。请恢复上一个版本。')
        self._validate_relationship_items(block['items'])
        vetoed = block.get('vetoed')
        if vetoed is not None and not isinstance(vetoed, list):
            raise UserError(f'{self.path.name} 的 relationship.vetoed 无效。')

    def _validate_relationship_items(self, items):
        seen = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise UserError(f'relationship 第 {index + 1} 条不是对象。')
            if not isinstance(item.get('id'), str) or not item['id']:
                raise UserError(f'relationship 第 {index + 1} 条缺少 id。')
            if item['id'] in seen:
                raise UserError(f'relationship id 重复：{item["id"]}。')
            seen.add(item['id'])
            if not isinstance(item.get('text'), str) or not item['text'].strip():
                raise UserError(f'relationship 第 {index + 1} 条正文为空。')
            if item.get('status') not in RELATIONSHIP_STATUSES:
                raise UserError(f'relationship 第 {index + 1} 条状态无效。')
            if item.get('source') not in RELATIONSHIP_SOURCES:
                raise UserError(f'relationship 第 {index + 1} 条来源无效。')
            if 'locked' in item and type(item['locked']) is not bool:
                raise UserError(f'relationship 第 {index + 1} 条 locked 无效。')
            if 'stale_ref' in item and type(item['stale_ref']) is not bool:
                raise UserError(f'relationship 第 {index + 1} 条 stale_ref 无效。')
            if 'evidence_state' in item and item['evidence_state'] not in EVIDENCE_STATES:
                raise UserError(f'relationship 第 {index + 1} 条 evidence_state 无效。')
            if 'missing_refs' in item:
                mr = item['missing_refs']
                if not isinstance(mr, list) or not all(isinstance(x, str) for x in mr):
                    raise UserError(f'relationship 第 {index + 1} 条 missing_refs 无效。')
            for key in ('created_at', 'updated_at', 'superseded_by', 'restored_from'):
                if key in item and not isinstance(item[key], str):
                    raise UserError(f'relationship 第 {index + 1} 条 {key} 无效。')
            sources = item.get('sources', [])
            if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
                raise UserError(f'relationship 第 {index + 1} 条 sources 无效。')
            if not all(SOURCE_REF.fullmatch(source) for source in sources):
                raise UserError(
                    f'relationship 第 {index + 1} 条 sources 里有不认识的引用：'
                    f'只接受 chat:/memory:/event:/long_task: 加编号。'
                )

    # ---------- evidence ----------

    def _chat_user_index(self):
        """Index live user speech; record only directly adjacent assistant questions."""
        path = self.root / 'chat.jsonl'
        try:
            with path.open('rb') as handle:
                before = os.fstat(handle.fileno())
                content = handle.read()
            after = path.stat()
            if (before.st_ino, before.st_mtime_ns, before.st_size) != (
                    after.st_ino, after.st_mtime_ns, after.st_size) or len(content) != after.st_size:
                return None
        except FileNotFoundError:
            self.invalidate_evidence_cache()
            return {}
        except OSError:
            self.invalidate_evidence_cache()
            return None
        # Windows timestamps may not change on same-size rewrites within one clock tick.
        key = sha256(content).digest()
        if self._chat_cache_key == key:
            return self._chat_cache
        index, seen, duplicates = {}, set(), set()
        previous = None
        for line in content.split(b'\n'):
            preceding, previous = previous, None
            try:
                row = json.loads(line.decode('utf-8'))
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            mid = row.get('message_id') or row.get('id')
            if not isinstance(mid, str) or not SOURCE_REF.fullmatch(f'chat:{mid}'):
                continue
            if mid in seen:
                duplicates.add(mid)
                continue
            seen.add(mid)
            role = row.get('role')
            text = row.get('content') if 'content' in row else row.get('text')
            if not isinstance(text, str) or not text.strip() or '�' in text:
                continue
            # For short acknowledgments, the proposed text is not itself evidence;
            # it is only the object of this user turn's explicit confirmation.
            if role == 'assistant':
                target = _confirmation_target(text)
                if target:
                    previous = (mid, target)
            elif role == 'user':
                index[mid] = {
                    'text': text, 'confirmation': preceding,
                    'time': row.get('time') if isinstance(row.get('time'), str) else '',
                }
        for mid in duplicates:
            index.pop(mid, None)
        for info in index.values():
            preceding = info.pop('confirmation')
            info['confirmation_target'] = preceding[1] if preceding and preceding[0] not in duplicates else ''
        self._chat_cache_key = key
        self._chat_cache = index
        return index

    def _chat_user_messages(self, ids):
        """Return dict of {message_id: {'text': ..., 'time': ...}} for real user messages."""
        wanted = {value for value in ids if isinstance(value, str) and value}
        if not wanted:
            return {}
        index = self._chat_user_index()
        if index is None:
            return {}
        return {mid: index[mid] for mid in wanted if mid in index}

    def _open_brain_ro(self):
        try:
            uri = self.root.joinpath('brain.db').resolve().as_uri() + '?mode=ro'
            conn = sqlite3.connect(uri, uri=True, timeout=1.0)
            conn.row_factory = sqlite3.Row
            return conn, True
        except (OSError, sqlite3.Error, ValueError):
            return None, False

    def _resolve_db_evidence(self, pointer_kind, pointer_id, chat_index):
        """Resolve event and memory pointers to deduplicated, still-live chat row IDs."""
        result = {'exists': False, 'unavailable': False, 'missing_origin': False, 'origins': set()}
        try:
            (self.root / 'brain.db').stat()
        except FileNotFoundError:
            return result
        except OSError:
            return {**result, 'unavailable': True}
        conn, ok = self._open_brain_ro()
        if not ok:
            return {**result, 'unavailable': True}
        try:
            conn.execute('BEGIN')  # One snapshot for pointer, ledger, and quote reads.
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            memory_id = pointer_id
            if pointer_kind == 'event':
                if 'events' not in tables:
                    return result
                event = conn.execute('SELECT memory_id FROM events WHERE event_id=?', (pointer_id,)).fetchone()
                if event is None:
                    return result
                memory_id = event['memory_id']
            if pointer_kind == 'memory' or memory_id:
                if 'memories' not in tables or conn.execute(
                    'SELECT 1 FROM memories WHERE id=? AND is_tombstone=0 AND is_deleted=0', (memory_id,),
                ).fetchone() is None:
                    return result
            result['exists'] = True
            if chat_index is None:
                return {**result, 'unavailable': True}

            def verify(mid, copied):
                raw = chat_index.get(mid) if isinstance(mid, str) else None
                # Copies can never supply the text we judge, nor can a later unrelated chat
                # row with identical wording replace the identified original turn.
                if (raw and isinstance(copied, str) and copied.strip()
                        and ' '.join(copied.split()) == ' '.join(raw['text'].split())):
                    result['origins'].add(mid)
                else:
                    result['missing_origin'] = True

            if 'event_ledger' in tables and 'events' in tables:
                if pointer_kind == 'event':
                    where = 'event_id=?'
                else:
                    where = 'event_id IN (SELECT event_id FROM events WHERE memory_id=?)'
                for row in conn.execute(
                    f"SELECT payload_json FROM event_ledger WHERE kind='stage-evidence' AND {where} "
                    "ORDER BY ledger_seq DESC, id DESC LIMIT 200", (pointer_id,),
                ):
                    try:
                        payload = json.loads(row[0])
                    except (ValueError, TypeError):
                        continue
                    if (isinstance(payload, dict) and payload.get('kind') == 'chat'
                            and payload.get('role') == 'user'):
                        verify(payload.get('message_id'), payload.get('text'))

            if memory_id and 'quotes' in tables:
                quotes = [r[0] for r in conn.execute(
                    "SELECT text FROM quotes WHERE memory_id=? AND speaker='user' ORDER BY ord LIMIT 50",
                    (memory_id,),
                ) if isinstance(r[0], str) and r[0].strip()]
                # The quote projection has no message ID. A structured, live chat ref on
                # this same memory can supply it; never search globally for similar text.
                if quotes and 'sources' in tables:
                    quote_texts = {' '.join(text.split()) for text in quotes}
                    for row in conn.execute(
                        "SELECT ref FROM sources WHERE memory_id=? AND kind='chat' "
                        "AND (status IS NULL OR status IN ('', 'active', 'verified')) ORDER BY ord LIMIT 50",
                        (memory_id,),
                    ):
                        ref = row[0]
                        mid = ref.removeprefix('chat:') if isinstance(ref, str) else None
                        raw = chat_index.get(mid)
                        if raw and ' '.join(raw['text'].split()) in quote_texts:
                            result['origins'].add(mid)
                if quotes and not result['origins']:
                    result['missing_origin'] = True
            return result
        except sqlite3.Error:
            return {'exists': False, 'unavailable': True, 'missing_origin': False, 'origins': set()}
        finally:
            conn.close()

    def _evaluate_sources(self, sources, claim_text='', data=None):
        """Unified strict evidence verification entry point.

        Distinguishes:
        1. Whether each ref exists (chat.jsonl / brain.db).
        2. Whether it traces to real user speech (never assistant/tool/autonomy/story).
        3. Same-origin deduplication across direct chat: and indirect event:/memory: refs.
        4. Programmatic content support & whether Lan already vetoed/corrected this claim.
        """
        chat_index = self._chat_user_index()
        kept_refs, dropped, missing_refs, unverified_support = [], [], [], []
        valid_origins = set()
        any_unavailable = False
        for source in dict.fromkeys(s for s in (sources or []) if isinstance(s, str)):
            if not SOURCE_REF.fullmatch(source):
                dropped.append(source)
                continue
            kind, _, value = source.partition(':')
            if kind == 'chat':
                if chat_index is None:
                    any_unavailable = True
                    kept_refs.append(source)
                    continue
                if value not in chat_index:
                    dropped.append(source)
                    missing_refs.append(source)
                    continue
                origins = {value}
            elif kind in ('event', 'memory'):
                resolved = self._resolve_db_evidence(kind, value, chat_index)
                any_unavailable |= resolved['unavailable']
                origins = resolved['origins']
                if not resolved['unavailable'] and (not resolved['exists'] or resolved['missing_origin']):
                    missing_refs.append(source)
            else:
                kept_refs.append(source)  # long_task is for traceability only.
                continue
            kept_refs.append(source)
            matched = set()
            for mid in origins:
                info = chat_index[mid]
                if _supports_claim(info['text'], claim_text, confirmation_target=info['confirmation_target']):
                    matched.add(('chat', mid))
            valid_origins.update(matched)
            if origins and not matched:
                unverified_support.append(source)

        rebutted = self._is_rebutted_by_lan(claim_text, data)
        if not rebutted and valid_origins and chat_index and claim_text:
            cited_times = [
                chat_index[oid]['time']
                for kind, oid in valid_origins
                if kind == 'chat' and oid in chat_index and chat_index[oid].get('time')
            ]
            if cited_times:
                latest_cited = max(cited_times)
                claim_tokens = _lexical_tokens(claim_text)
                for mid, info in chat_index.items():
                    if ('chat', mid) in valid_origins:
                        continue
                    mtime = info.get('time') or ''
                    mtext = info.get('text') or ''
                    if mtime > latest_cited and _REBUTTAL_CUE_RE.search(mtext):
                        if claim_tokens and (_lexical_tokens(mtext) & claim_tokens):
                            rebutted = True
                            break

        if rebutted:
            evidence_state = 'rebutted'
        elif any_unavailable and not valid_origins:
            evidence_state = 'unavailable'
        elif missing_refs and valid_origins:
            evidence_state = 'partial_stale'
        elif missing_refs and not valid_origins:
            evidence_state = 'invalid'
        elif valid_origins:
            evidence_state = 'valid'
        elif kept_refs:
            evidence_state = 'none'
        else:
            evidence_state = 'none'

        return {
            'kept_refs': kept_refs,
            'dropped': dropped,
            'missing_refs': missing_refs,
            'unverified_support': unverified_support,
            'valid_origin_count': len(valid_origins),
            'evidence_state': evidence_state,
            'rebutted': rebutted,
        }

    def _is_rebutted_by_lan(self, claim_text, data, *, section=None):
        """Apply persistent user vetoes across cognition sections."""
        if not _norm_text(claim_text) or not isinstance(data, dict):
            return False
        for key in ALL_SECTIONS:
            for veto in (data.get(key) or {}).get('vetoed') or []:
                if isinstance(veto, dict) and _matches_governed_claim(claim_text, veto.get('text', '')):
                    return True
        for item in (data.get('relationship') or {}).get('items') or []:
            if (isinstance(item, dict) and item.get('status') == 'outdated'
                    and item.get('source') == 'lan'
                    and _matches_governed_claim(claim_text, item.get('text', ''))):
                return True
        return False

    def _automatic_write_error(self, data, text, *, source='peter', previous=None):
        """Single admission check for new/replaced text and retirement."""
        rank = SOURCE_RANK[source]
        if previous and (previous.get('locked') or SOURCE_RANK.get(previous.get('source'), 1) > rank):
            return 'This cognition is locked or has a higher-priority source; nothing was changed.'
        if not text:
            return None
        if self._is_rebutted_by_lan(text, data):
            return 'This cognition was already vetoed or corrected by Lan; nothing was changed.'
        for item in data['relationship']['items']:
            if (item is not previous and item['status'] != 'outdated'
                    and (item.get('locked') or SOURCE_RANK.get(item.get('source'), 1) > rank)
                    and _matches_governed_claim(text, item['text'])):
                return 'This overlaps a locked or higher-priority relationship cognition; nothing was changed.'
        return None

    def _verified_sources(self, sources):
        """Backwards-compatible helper returning (verified_chat, pointers, dropped)."""
        ev = self._evaluate_sources(sources)
        verified = [s for s in ev['kept_refs'] if s.startswith('chat:')]
        pointers = [s for s in ev['kept_refs'] if not s.startswith('chat:')]
        return verified, pointers, ev['dropped']

    def _relationship_provenance(self, status, sources, claim_text='', data=None):
        """Strict evidence-chain provenance check.

        Only claims backed by >= 1 verified, non-rebutted user origin (via direct `chat:` or
        `event:`/`memory:` traced to user `stage-evidence` / `quotes`) can be `confirmed`.
        """
        ev = self._evaluate_sources(sources, claim_text=claim_text, data=data)
        if ev['rebutted']:
            return {
                'status': 'uncertain', 'source': 'peter', 'sources': ev['kept_refs'],
                'dropped_sources': ev['dropped'], 'missing_refs': ev['missing_refs'],
                'unverified_support': ev['unverified_support'],
                'evidence_state': 'rebutted', 'downgraded': status == 'confirmed',
                'rebutted': True,
            }
        if ev['valid_origin_count'] >= 1:
            return {
                'status': 'confirmed' if status == 'confirmed' else 'uncertain',
                'source': 'evidence', 'sources': ev['kept_refs'],
                'dropped_sources': ev['dropped'], 'missing_refs': ev['missing_refs'],
                'unverified_support': ev['unverified_support'],
                'evidence_state': ev['evidence_state'], 'downgraded': False,
                'rebutted': False,
            }
        return {
            'status': 'uncertain', 'source': 'peter', 'sources': ev['kept_refs'],
            'dropped_sources': ev['dropped'], 'missing_refs': ev['missing_refs'],
            'unverified_support': ev['unverified_support'],
            'evidence_state': ev['evidence_state'], 'downgraded': status == 'confirmed',
            'rebutted': False,
        }

    # ---------- independent 4-section budget ----------

    def _section_bytes(self, data, section):
        if section in SECTIONS:
            return _text_bytes((data.get(section) or {}).get('text', ''))
        if section == 'ongoing':
            items = (data.get('ongoing') or {}).get('items') or []
            return sum(_text_bytes(item.get('text', '')) for item in items
                       if isinstance(item, dict) and item.get('status') in ('active', 'uncertain'))
        if section == 'relationship':
            items = (data.get('relationship') or {}).get('items') or []
            return sum(_text_bytes(item.get('text', '')) for item in items
                       if isinstance(item, dict) and item.get('status') in ('confirmed', 'uncertain'))
        return 0

    def section_budget_status(self, data=None):
        if data is None:
            data = self._load()
        sections = {}
        degraded = []
        total_used = 0
        for sec in ALL_SECTIONS:
            used = self._section_bytes(data, sec)
            limit = self.section_budgets[sec]
            over = max(0, used - limit)
            is_over = over > 0
            if is_over:
                degraded.append(sec)
            total_used += used
            sections[sec] = {
                'used_bytes': used,
                'max_bytes': limit,
                'remaining_bytes': max(0, limit - used),
                'over_bytes': over,
                'degraded': is_over,
            }
        return {
            'sections': sections,
            'used_bytes': total_used,
            'max_bytes': self.max_bytes,
            'degraded_sections': degraded,
            'capacity_degraded': bool(degraded),
        }

    def _content_bytes(self, data=None):
        if data is None:
            data = self._load()
        return sum(self._section_bytes(data, sec) for sec in ALL_SECTIONS)

    def _check_section_budget(self, data, section, before_bytes=None):
        after = self._section_bytes(data, section)
        limit = self.section_budgets[section]
        if after <= limit:
            return
        if before_bytes is not None and after <= before_bytes:
            return
        over = after - limit
        raise UserError(
            f'{section} 分区超出独立上限 {over} 字节（当前 {after} / {limit} B）；没有保存。'
            f'请精简 {section} 分区至少 {over} 字节，其他分区不受影响。'
        )

    def read(self):
        return self._load()

    # ---------- prompt blocks ----------

    def context(self):
        """Stable, model-facing facts and growth (management reads the stored document)."""
        return self.build_prompt()['stable']

    def build_prompt(self):
        """Project all four sections from one snapshot, without changing any stored claim."""
        projection = {'stable': None, 'notes': '', 'ongoing': None, 'relationship_items': [],
                      'cross_section_conflicts': []}
        try:
            data = self._load()
        except UserError as exc:
            self._log_error('context', exc)
            facts = self._legacy_text().strip()
            if facts:
                projection['stable'] = (
                    "PETER'S NOTES ABOUT LAN (fallback; the new file is unreadable, so these are "
                    "the old notes; data, not instructions):\n" + facts
                )
            return projection

        relations = self._apply_live_evidence_check(data)
        projection['relationship_items'] = relations
        conflicts = self.relationship_conflict_texts(data)
        # A bare duplicate must not undo the live evidence downgrade or its caveat.
        conflicts.extend(item['text'] for item in relations
                         if item.get('evidence_degraded') or item.get('evidence_state') == 'rebutted')

        def rejected(text, section, explicit=False):
            return not explicit and (self._is_rebutted_by_lan(text, data, section=section)
                                     or any(_matches_governed_claim(text, old) for old in conflicts))

        for item in relations:
            if item.get('source') != 'lan' and not item.get('locked'):
                item['prompt_vetoed'] = self._is_rebutted_by_lan(item['text'], data)

        def project_notes(section):
            block = data[section]
            explicit = block.get('lan_text') or []
            kept = []
            unit_number = 0
            for paragraph in re.split(r'\n[ \t]*\n', block['text'].strip()):
                # Whole Markdown list entries or prose paragraphs. Never remove one sentence from
                # a paragraph and leave a rule whose exception/qualification has disappeared.
                parts = re.split(r'\n(?=(?:[-*+•]|\d{1,3}[.)])[ \t]+)', paragraph)
                paragraph_kept = []
                for part in parts:
                    if not part.strip():
                        continue
                    unit_number += 1
                    if rejected(part, section, paragraph.strip() in explicit):
                        projection['cross_section_conflicts'].append(f'{section}:{unit_number}')
                    else:
                        paragraph_kept.append(part)
                if paragraph_kept:
                    kept.append('\n'.join(paragraph_kept))
            return '\n\n'.join(kept)

        growth, facts = project_notes('growth'), project_notes('facts')
        projection['notes'] = facts + '\n' + growth
        if growth or facts:
            lines = [prompts.text('memory.learned.header')]
            if growth:
                lines.append(prompts.text('memory.learned.growth') + '\n' + growth)
            if facts:
                lines.append(prompts.text('memory.learned.facts') + '\n' + facts)
            projection['stable'] = '\n\n'.join(lines)

        ongoing = []
        done = 0
        for item in data['ongoing']['items']:
            status = item['status']
            if status == 'completed':
                done += 1
            elif status in ('active', 'uncertain'):
                if rejected(item['text'], 'ongoing', item.get('lan_text') is True):
                    projection['cross_section_conflicts'].append('ongoing:' + item['id'])
                else:
                    ongoing.append(f'- [{status}] {item["text"].strip()}')
        lines = [prompts.text('memory.ongoing.header'), *ongoing] if ongoing else []
        if done:
            lines.append(prompts.text('memory.ongoing.done').format(count=done))
        projection['ongoing'] = '\n'.join(lines) or None
        return projection

    def _apply_live_evidence_check(self, data):
        """Evaluate current evidence health for active relationship items without mutating disk."""
        block = data.get('relationship') or {}
        items = []
        for raw in block.get('items') or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if item.get('status') in ('confirmed', 'uncertain'):
                sources = item.get('sources') or []
                if sources or item.get('source') == 'evidence':
                    ev = self._evaluate_sources(sources, claim_text=item.get('text', ''), data=data)
                    item['stale_ref'] = bool(ev['missing_refs'])
                    if ev['missing_refs']:
                        item['missing_refs'] = ev['missing_refs']
                    else:
                        item.pop('missing_refs', None)
                    item['evidence_state'] = ev['evidence_state']
                    # Model-promoted confirmed item whose evidence became invalid/unavailable/rebutted
                    # must safely degrade so it cannot pass as confirmed consensus.
                    if (item.get('status') == 'confirmed' and item.get('source') == 'evidence'
                            and not item.get('locked')
                            and ev['evidence_state'] in ('invalid', 'unavailable', 'rebutted', 'none')):
                        item['effective_status'] = 'uncertain'
                        item['evidence_degraded'] = True
            items.append(item)
        return items

    def relationship_items(self):
        """Ticket 422 / P6-S3: relationship items with live evidence health. Never raises."""
        try:
            data = self._load()
        except UserError as exc:
            self._log_error('relationship_items', exc)
            return []
        return self._apply_live_evidence_check(data)

    def relationship_conflict_texts(self, data=None):
        """Conflict references for the read-only prompt projection, never copied into diagnostics."""
        if data is None:
            try:
                data = self._load()
            except UserError:
                return []
        return [item['text'] for item in data['relationship']['items']
                if item.get('status') == 'outdated']

    def notes_text(self):
        """facts + growth as one string, for de-duplicating the relationship base against them."""
        try:
            data = self._load()
        except UserError:
            return self._legacy_text()
        return data['facts']['text'] + '\n' + data['growth']['text']

    def dynamic_context(self):
        """Projected ongoing items, injected late so updates do not break the stable prefix."""
        return self.build_prompt()['ongoing']

    # ---------- writing & consistency ----------

    def _write(self, data):
        data['updated_at'] = _now()
        text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        ModelSettings._atomic_bytes(self.path, text.encode('utf-8'))

    def _record(self, tool, before, after):
        row = {
            'time': _now(), 'tool': tool,
            'before': json.dumps(before, ensure_ascii=False) if before is not None else None,
            'after': json.dumps(after, ensure_ascii=False),
        }
        encoded = (json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8')
        self.versions.parent.mkdir(parents=True, exist_ok=True)
        with self.versions.open('ab') as handle:
            if handle.write(encoded) != len(encoded):
                raise OSError('short version write')
            handle.flush()
            os.fsync(handle.fileno())
        ModelSettings._sync_directory(self.versions.parent)

    def _recover_storage_transaction(self):
        """Recover with the role's Brain write lock already held (including bundle callers)."""
        try:
            try:
                record = json.loads(self._journal.read_bytes())
            except FileNotFoundError:
                return None
            state = record['state']
            if state == 'prepared':
                original = record['body']
                original = base64.b64decode(original, validate=True) if original is not None else None
                size = record['versions_size']
                if size is not None and (type(size) is not int or size < 0):
                    raise ValueError('invalid version history length')
                # An append can only extend the file. Never manufacture missing history.
                if size is None:
                    self.versions.unlink(missing_ok=True)
                    ModelSettings._sync_directory(self.versions.parent)
                else:
                    with self.versions.open('r+b') as handle:
                        disk_size = os.fstat(handle.fileno()).st_size
                        if disk_size < size:
                            raise ValueError('version history is shorter than recorded')
                        if disk_size > size:
                            handle.truncate(size)
                        os.fsync(handle.fileno())
                if original is None:
                    self.path.unlink(missing_ok=True)
                    ModelSettings._sync_directory(self.path.parent)
                else:
                    ModelSettings._atomic_bytes(self.path, original)
                self._journal.unlink()
                ModelSettings._sync_directory(self._journal.parent)
            elif state == 'committed':
                # A prior replace may have reached disk before its directory sync failed.
                # Keep the marker and stop if we still cannot confirm the commit.
                try:
                    ModelSettings._sync_directory(self._journal.parent)
                except OSError as exc:
                    raise UserError('笔记正文与版本已写入，但无法确认保存是否持久化；请修复磁盘并刷新确认，不要重试写入。') from exc
                # Match the model settings contract: a cleanup error is not a lost save.
                try:
                    self._journal.unlink()
                    ModelSettings._sync_directory(self._journal.parent)
                except OSError:
                    pass
            else:
                raise ValueError('invalid transaction state')
            return state
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise UserError('笔记正文与版本历史的上次保存无法自动恢复；已暂停保存，请保留数据目录并检查磁盘。') from exc

    def _commit_with_version(self, tool, before, data):
        """Commit a body snapshot and a history append using the prepared/committed protocol."""
        with self.lock:
            if self._journal.exists():
                raise UserError('无法清理上次笔记保存的事务记录；本次没有保存，请检查磁盘。')
            try:
                # The history is append-only under this lock; journalling its original byte
                # offset lets us roll back partial appends without copying all past versions.
                try:
                    size = self.versions.stat().st_size
                except FileNotFoundError:
                    size = None
                record = {'state': 'prepared', 'body': ModelSettings._snapshot(self.path),
                          'versions_size': size}
                ModelSettings._atomic_bytes(self._journal, json.dumps(record).encode('utf-8'))
                self._write(data)
                self._record(tool, before, data)
                ModelSettings._atomic_bytes(self._journal, b'{"state":"committed"}')
            except Exception as exc:
                if self._recover_storage_transaction() == 'committed':
                    return
                if isinstance(exc, OSError):
                    raise UserError(f'保存 {self.path.name} 及版本历史失败：{exc}。修改没有保存。') from exc
                raise
            self._recover_storage_transaction()

    def read_version_history(self):
        """Read the latest committed version rows, newest first, without initializing a file."""
        with self.lock:
            rows = deque(maxlen=20)
            try:
                with self.versions.open(encoding='utf-8', errors='replace') as handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict):
                            rows.append(row)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise UserError(f'读取笔记版本历史失败：{exc}') from exc
            return list(reversed(rows))

    def _log_error(self, operation, exc):
        signature = f'{operation}:{self.path.stat().st_mtime_ns if self.path.exists() else 0}:{exc}'
        if signature == self._logged_error:
            return
        self._logged_error = signature
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            row = {
                'time': _now(), 'operation': operation, 'path': str(self.path),
                'bytes': self.path.stat().st_size if self.path.exists() else 0,
                'error': type(exc).__name__, 'message': str(exc),
            }
            with self.error_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def save_section(self, section, text, expected_updated_at=None, force=False):
        if section not in SECTIONS:
            raise UserError('只能保存 facts 或 growth。')
        if not isinstance(text, str):
            raise UserError('内容必须是文字。')
        with self.lock:
            data = self._load()
            if not force and expected_updated_at and data[section].get('updated_at') != expected_updated_at:
                raise UserError('你打开之后这一块又改过；请先刷新，或用“覆盖他的版本”保存。')
            before = json.loads(json.dumps(data, ensure_ascii=False))
            before_bytes = self._section_bytes(data, section)
            block = data[section]
            old_units = set(_claim_units(block['text']))
            new_units = set(_claim_units(text))
            now = _now()
            for unit in sorted(old_units - new_units):
                _veto_text(block, unit, now, 'lan_edited')
            for unit in sorted(new_units - old_units):
                _lift_exact_veto(block, unit)
            if text != block['text']:
                block['lan_text'] = [part.strip() for part in re.split(r'\n[ \t]*\n', text) if part.strip()]
            block['text'] = text
            block['updated_at'] = now
            self._check_section_budget(data, section, before_bytes=before_bytes)
            self._commit_with_version(f'save_{section}', before, data)
            return data

    def save_ongoing(self, items, expected_updated_at=None, force=False):
        if not isinstance(items, list):
            raise UserError('ongoing 必须是条目列表。')
        with self.lock:
            data = self._load()
            if not force and expected_updated_at and data['ongoing'].get('updated_at') != expected_updated_at:
                raise UserError('你打开之后 ongoing 又改过；请先刷新，或用“覆盖他的版本”保存。')
            before = json.loads(json.dumps(data, ensure_ascii=False))
            before_bytes = self._section_bytes(data, 'ongoing')
            block = data['ongoing']
            self._validate_items(items)
            now = _now()
            stored = {item['id']: item for item in block['items']}
            revised = {item['id']: dict(item) for item in items}
            for item_id, previous in stored.items():
                item = revised.get(item_id)
                if item is None or item['text'] != previous['text']:
                    _veto_text(block, previous['text'], now, 'lan_edited' if item else 'lan_deleted', item_id)
            for item_id, item in revised.items():
                previous = stored.get(item_id, {})
                if item['text'] != previous.get('text'):
                    item['lan_text'] = True
                    _lift_exact_veto(block, item['text'])
                elif previous.get('lan_text'):
                    item['lan_text'] = True
                if item['status'] == 'completed':
                    if previous.get('status') != 'completed' or previous.get('lan_completed'):
                        item['lan_completed'] = True
                    if not isinstance(item.get('completed_at'), str):
                        item['completed_at'] = now
                else:
                    item.pop('lan_completed', None)
            block['items'] = list(revised.values())
            block['updated_at'] = now
            self._check_section_budget(data, 'ongoing', before_bytes=before_bytes)
            self._commit_with_version('save_ongoing', before, data)
            return data

    def save_relationship(self, items, expected_updated_at=None, force=False):
        """Ticket 422 / P6-S3: Lan's edits to the relationship base. Her text & locks always win.

        Preserves all governance metadata (locked, stale_ref, missing_refs, evidence_state,
        superseded_by, restored_from) on untouched items, and records removed items in `vetoed`
        so old evidence or recovery cannot resurrect them.
        """
        if not isinstance(items, list):
            raise UserError('relationship 必须是条目列表。')
        with self.lock:
            data = self._load()
            block = data.get('relationship') or {}
            if (not force and expected_updated_at is not None
                    and block.get('updated_at') != expected_updated_at):
                raise UserError('你打开之后这一块又改过；请先刷新，或用“覆盖他的版本”保存。')
            before = json.loads(json.dumps(data, ensure_ascii=False))
            before_bytes = self._section_bytes(data, 'relationship')
            stored = {
                item['id']: item for item in block.get('items') or []
                if isinstance(item, dict) and isinstance(item.get('id'), str)
            }
            now = _now()
            cleaned = []
            kept_ids = set()
            rel_block = data['relationship']

            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                    raise UserError('relationship 每条都要有 id。')
                kept_ids.add(item['id'])
                previous = stored.get(item['id'])
                text = item.get('text').strip() if isinstance(item.get('text'), str) else ''
                status = item.get('status')
                if status not in RELATIONSHIP_STATUSES:
                    raise UserError('relationship 第 %s 条状态无效。' % item['id'])
                req_locked = item.get('locked') if 'locked' in item else (previous or {}).get('locked', False)
                row = {'id': item['id'], 'text': text, 'status': status}
                if req_locked:
                    row['locked'] = True
                if previous and (previous['text'] != text
                                 or (previous['status'] != 'outdated' and status == 'outdated')):
                    _veto_text(rel_block, previous['text'], now, 'lan_corrected', item['id'])
                if status != 'outdated' and (previous is None or previous['text'] != text
                                             or previous['status'] == 'outdated'):
                    _lift_exact_veto(rel_block, text)

                if (previous is not None and text == previous.get('text')
                        and status == previous.get('status')
                        and bool(req_locked) == bool(previous.get('locked', False))):
                    row['source'] = previous.get('source', 'peter')
                    row['created_at'] = previous.get('created_at', now)
                    row['updated_at'] = previous.get('updated_at', now)
                    for key in ('sources', 'superseded_by', 'stale_ref', 'missing_refs',
                                'evidence_state', 'restored_from'):
                        if key in previous:
                            row[key] = previous[key]
                    cleaned.append(row)
                    continue

                # Untouched text/status but lock toggled
                if (previous is not None and text == previous.get('text')
                        and status == previous.get('status')):
                    row['source'] = previous.get('source', 'lan')
                    row['created_at'] = previous.get('created_at', now)
                    row['updated_at'] = now
                    for key in ('sources', 'superseded_by', 'stale_ref', 'missing_refs',
                                'evidence_state', 'restored_from'):
                        if key in previous:
                            row[key] = previous[key]
                    cleaned.append(row)
                    continue

                # New or actually edited by Lan: her word becomes the source.
                row['source'] = 'lan'
                row['created_at'] = (previous or {}).get('created_at') or now
                row['updated_at'] = now
                sources = item.get('sources')
                if sources is None:
                    sources = (previous or {}).get('sources', [])
                row['sources'] = sources if isinstance(sources, list) else []
                for key in ('superseded_by', 'restored_from'):
                    if previous and previous.get(key):
                        row[key] = previous[key]
                cleaned.append(row)

            for old_id, old_item in stored.items():
                if old_id not in kept_ids:
                    _veto_text(rel_block, old_item['text'], now, 'lan_deleted', old_id)

            self._validate_relationship_items(cleaned)
            rel_block['items'] = cleaned
            rel_block['updated_at'] = now
            self._check_section_budget(data, 'relationship', before_bytes=before_bytes)
            self._commit_with_version('save_relationship', before, data)
            return data

    def set_relationship_lock(self, item_id, locked, expected_updated_at=None):
        """Toggle lock on a single relationship item."""
        if not isinstance(item_id, str) or not item_id:
            raise UserError('缺少条目 id。')
        with self.lock:
            data = self._load()
            block = data.get('relationship') or {}
            if expected_updated_at is not None and block.get('updated_at') != expected_updated_at:
                raise UserError('你打开之后这一块又改过；请先刷新。')
            before = json.loads(json.dumps(data, ensure_ascii=False))
            target = next(
                (row for row in block.get('items') or [] if isinstance(row, dict) and row.get('id') == item_id),
                None,
            )
            if target is None:
                raise UserError(f'找不到关系条目：{item_id}。')
            now = _now()
            if locked:
                target['locked'] = True
            else:
                target.pop('locked', None)
            target['updated_at'] = now
            block['updated_at'] = now
            self._commit_with_version('lock_relationship', before, data)
            return data

    def restore_relationship_item(self, item_id, expected_updated_at=None):
        """Restore an outdated relationship item as a new current item owned by Lan."""
        if not isinstance(item_id, str) or not item_id:
            raise UserError('缺少要恢复的条目 id。')
        with self.lock:
            data = self._load()
            block = data.get('relationship') or {}
            if expected_updated_at is not None and block.get('updated_at') != expected_updated_at:
                raise UserError('你打开之后这一块又改过；请先刷新。')
            before = json.loads(json.dumps(data, ensure_ascii=False))
            before_bytes = self._section_bytes(data, 'relationship')
            items = block.setdefault('items', [])
            source_item = next(
                (row for row in items if isinstance(row, dict) and row.get('id') == item_id),
                None,
            )
            if source_item is None:
                raise UserError(f'找不到关系条目：{item_id}。')
            now = _now()
            new_item = {
                'id': uuid.uuid4().hex[:12],
                'text': source_item['text'],
                'status': 'confirmed',
                'source': 'lan',
                'sources': list(source_item.get('sources') or []),
                'restored_from': item_id,
                'created_at': now,
                'updated_at': now,
            }
            items.append(new_item)
            block['updated_at'] = now
            # Clear any veto for the restored text since Lan explicitly restored it.
            norm = _norm_text(source_item['text'])
            if block.get('vetoed'):
                block['vetoed'] = [
                    v for v in block['vetoed'] if not (isinstance(v, dict) and v.get('norm_text') == norm)
                ]
            self._validate_relationship_items(items)
            self._check_section_budget(data, 'relationship', before_bytes=before_bytes)
            self._commit_with_version('restore_relationship', before, data)
            return data

    def reverify_relationship_evidence(self):
        """Re-verify all active relationship items and persist updated stale_ref/degradation."""
        with self.lock:
            data = self._load()
            block = data.get('relationship') or {}
            items = block.get('items') or []
            before = json.loads(json.dumps(data, ensure_ascii=False))
            changed = False
            now = _now()
            for item in items:
                if not isinstance(item, dict) or item.get('status') not in ('confirmed', 'uncertain'):
                    continue
                sources = item.get('sources') or []
                if not sources and item.get('source') != 'evidence':
                    continue
                ev = self._evaluate_sources(sources, claim_text=item.get('text', ''), data=data)
                new_stale = bool(ev['missing_refs'])
                if item.get('stale_ref', False) != new_stale:
                    item['stale_ref'] = new_stale
                    changed = True
                if ev['missing_refs']:
                    if item.get('missing_refs') != ev['missing_refs']:
                        item['missing_refs'] = ev['missing_refs']
                        changed = True
                elif 'missing_refs' in item:
                    item.pop('missing_refs', None)
                    changed = True
                if item.get('evidence_state') != ev['evidence_state']:
                    item['evidence_state'] = ev['evidence_state']
                    changed = True
                # Only downgrade model-promoted items when evidence is definitively invalid or rebutted
                # (do not permanently mutate on temporary unavailability).
                if (item.get('status') == 'confirmed' and item.get('source') == 'evidence'
                        and not item.get('locked') and ev['evidence_state'] in ('invalid', 'rebutted', 'none')):
                    item['status'] = 'uncertain'
                    item['source'] = 'peter'
                    item['updated_at'] = now
                    changed = True
            if changed:
                block['updated_at'] = now
                self._commit_with_version('reverify_relationship', before, data)
            return {
                'changed': changed,
                'items': [dict(i) for i in items if isinstance(i, dict)],
            }

    # ---------- migration ----------

    def migrate_storage(self, dry_run=True):
        """Idempotent, lossless migration to 4-section independent budgets.

        Never deletes, truncates, or rewrites original text, IDs, sources, or history.
        """
        with self.lock:
            data = self._empty(self._legacy_text()) if dry_run and not self.path.exists() else self._load()
            budgets = self.section_budget_status(data)
            rel_block = data.get('relationship') or {}
            needs_upgrade = 'vetoed' not in rel_block
            plan = {
                'dry_run': bool(dry_run),
                'path': str(self.path),
                'needs_upgrade': needs_upgrade,
                'budgets': budgets,
                'backup_path': '',
                'applied': False,
            }
            if dry_run or not needs_upgrade:
                return plan
            backup_path = self.path.with_name(
                f'{self.path.stem}.pre-s3-migrate-{uuid.uuid4().hex[:8]}.bak.json'
            )
            try:
                ModelSettings._atomic_bytes(backup_path, self.path.read_bytes())
            except OSError as exc:
                raise UserError(f'创建迁移备份失败：{exc}') from exc
            before = copy.deepcopy(data)
            rel_block.setdefault('vetoed', [])
            self._commit_with_version('migrate_s3_budgets', before, data)
            plan['backup_path'] = str(backup_path)
            plan['applied'] = True
            return plan

    def rollback_migration(self, backup_path):
        """Undo this schema migration, not any user edits made since the backup."""
        src = Path(backup_path)
        with self.lock:
            try:
                backup = json.loads(src.read_bytes())
            except (OSError, ValueError) as exc:
                raise UserError(f'读取迁移备份失败：{exc}') from exc
            self._upgrade_ongoing(backup)
            self._upgrade_relationship(backup)
            self._validate(backup)
            if not self.path.exists():
                raise UserError('当前笔记文件缺失；无法确认迁移后的修改，请保留备份并先恢复当前文件。')
            current = self._load()
            restored = copy.deepcopy(current)
            # The migration only added an empty veto list. Current content is authoritative,
            # including removals and cleared vetoes: copying old rows would resurrect them.
            if 'vetoed' not in backup['relationship'] and restored['relationship'].get('vetoed') == []:
                restored['relationship'].pop('vetoed')
            self._commit_with_version('rollback_s3_migration', current, restored)
            return restored

    def _validate_items(self, items):
        seen = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise UserError(f'ongoing 第 {index + 1} 条不是对象。')
            if not isinstance(item.get('id'), str) or not item['id']:
                raise UserError(f'ongoing 第 {index + 1} 条缺少 id。')
            if item['id'] in seen:
                raise UserError(f'ongoing id 重复：{item["id"]}。')
            seen.add(item['id'])
            if not isinstance(item.get('text'), str) or not item['text'].strip():
                raise UserError(f'ongoing 第 {index + 1} 条正文为空。')
            if item.get('status') not in ONGOING_STATUSES:
                raise UserError(f'ongoing 第 {index + 1} 条状态无效。')
            sources = item.get('sources', [])
            if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
                raise UserError(f'ongoing 第 {index + 1} 条来源无效。')
            for key in ('created_at', 'updated_at', 'last_verified_at', 'completed_at'):
                if key in item and not isinstance(item[key], str):
                    raise UserError(f'ongoing 第 {index + 1} 条 {key} 无效。')

    def status(self, with_text=True):
        try:
            data = self._load()
        except UserError as exc:
            return {
                'available': False, 'error': str(exc), 'path': str(self.path),
                'error_log': str(self.error_path), 'max_bytes': self.max_bytes,
                'section_budgets': dict(self.section_budgets),
            }
        budgets = self.section_budget_status(data)
        rel_items = self._apply_live_evidence_check(data)
        result = {
            'available': True, 'error': '', 'path': str(self.path),
            'max_bytes': self.max_bytes,
            'used_bytes': budgets['used_bytes'],
            'section_budgets': dict(self.section_budgets),
            'budgets': budgets,
            'capacity_degraded': budgets['capacity_degraded'],
            'degraded_sections': budgets['degraded_sections'],
            'updated_at': data.get('updated_at', ''),
            'sections': {},
            'ongoing': {
                'items': [],
                'updated_at': data['ongoing'].get('updated_at', ''),
                **budgets['sections']['ongoing'],
            },
            'relationship': {
                'items': [],
                'vetoed': list((data.get('relationship') or {}).get('vetoed') or []),
                'updated_at': (data.get('relationship') or {}).get('updated_at', ''),
                **budgets['sections']['relationship'],
            },
        }
        for item in rel_items:
            copy = dict(item)
            if not with_text:
                copy['text'] = ''
            result['relationship']['items'].append(copy)
        for section in SECTIONS:
            value = data[section]
            sec_budget = budgets['sections'][section]
            result['sections'][section] = {
                'text': value['text'] if with_text else '',
                'bytes': sec_budget['used_bytes'],
                'max_bytes': sec_budget['max_bytes'],
                'remaining_bytes': sec_budget['remaining_bytes'],
                'over_bytes': sec_budget['over_bytes'],
                'degraded': sec_budget['degraded'],
                'updated_at': value.get('updated_at', ''),
            }
        for item in data['ongoing']['items']:
            copy = dict(item)
            if not with_text:
                copy['text'] = ''
            result['ongoing']['items'].append(copy)
        return result

    # ---------- unified candidate submission contract (for P6-S6 / background consolidation) ----------

    def submit_candidate(self, section, text, *, status='uncertain', sources=(),
                         replace_old_text=None, origin='consolidation'):
        """Unified governance entry point for S6 consolidation or automated candidate proposals.

        Always enforces 4-section independent budgets, strict evidence verification (no direct
        unverified `confirmed` writes), Lan `locked`/`vetoed` sovereignty, and atomic versioning.
        Returns `(message, state, tool_name)` identical to `invoke()`.
        """
        if section == 'ongoing':
            return self.invoke('ongoing_update', {
                'action': 'add', 'text': text, 'sources': list(sources or ()),
            })
        if replace_old_text is not None:
            return self.invoke('profile_replace', {
                'section': section, 'old_text': replace_old_text, 'new_text': text,
                'status': status, 'sources': list(sources or ()),
            })
        return self.invoke('profile_append', {
            'section': section, 'text': text, 'status': status, 'sources': list(sources or ()),
        })

    # ---------- Peter's tools ----------

    def invoke(self, name, args):
        if not isinstance(args, dict):
            return 'arguments must be an object.', '被拒绝', name
        try:
            if name in ('profile_append', 'profile_replace'):
                return self._invoke_profile(name, args)
            if name == 'ongoing_update':
                return self._invoke_ongoing(args)
            return 'Unknown profile tool.', '被拒绝', name
        except UserError as exc:
            self._log_error(name, exc)
            return str(exc), '被拒绝', name

    def _invoke_profile(self, name, args):
        section = args.get('section') or 'facts'
        if section == 'relationship':
            return self._invoke_relationship(name, args)
        if section not in SECTIONS:
            return 'section must be facts, growth or relationship.', '被拒绝', name
        with self.lock:
            data = self._load()
            before = data[section]['text']
            before_bytes = self._section_bytes(data, section)
            if name == 'profile_append':
                text = args.get('text')
                if not isinstance(text, str) or not text.strip():
                    return 'text must be a non-empty string.', '被拒绝', name
                candidate = text.strip()
                after = (before + '\n\n' + text.strip()) if before else text.strip()
            else:
                old, new = args.get('old_text'), args.get('new_text')
                if not isinstance(old, str) or not old or not isinstance(new, str):
                    return 'old_text must be a non-empty string and new_text a string.', '被拒绝', name
                count = before.count(old)
                if count != 1:
                    return (
                        f'old_text appears {count} times in {section}; it must appear exactly once. '
                        f'Nothing was changed.'
                    ), '被拒绝', name
                after = before.replace(old, new).strip()
                old_parts = {part.strip() for part in re.split(r'\n[ \t]*\n', before)}
                new_parts = {part.strip() for part in re.split(r'\n[ \t]*\n', after)}
                if any(part in old_parts and part not in new_parts for part in data[section].get('lan_text', [])):
                    return 'Cannot replace text saved by Lan.', '被拒绝', name
                candidate = '\n\n'.join(sorted(new_parts - old_parts))
            error = self._automatic_write_error(data, candidate)
            if error:
                return error, '被拒绝', name
            old_data = json.loads(json.dumps(data, ensure_ascii=False))
            data[section]['text'] = after
            data[section]['updated_at'] = _now()
            after_bytes = self._section_bytes(data, section)
            limit = self.section_budgets[section]
            if after_bytes > limit and after_bytes > before_bytes:
                over = after_bytes - limit
                return (
                    f'{section} would be {after_bytes} bytes; the {section} limit is {limit} '
                    f'({over} over). Nothing was changed. Merge or shorten {section} first; '
                    f'other sections are unaffected.'
                ), '被拒绝', name
            self._commit_with_version(name, old_data, data)
            return (
                f'Saved. {section} is now {after_bytes} of {limit} bytes; '
                f'it takes effect from the next request.'
            ), '成功', name

    def _invoke_relationship(self, name, args):
        """Ticket 422 / P6-S3: Peter updates the stable relationship base through unified governance."""
        requested = args.get('status') or 'uncertain'
        if requested not in ('confirmed', 'uncertain'):
            return 'status must be confirmed or uncertain (outdated is kept for history only).', '被拒绝', name
        raw_sources = args.get('sources') or []
        if not isinstance(raw_sources, list) or not all(isinstance(s, str) for s in raw_sources):
            return 'sources must be a list of strings.', '被拒绝', name
        with self.lock:
            data = self._load()
            before = json.loads(json.dumps(data, ensure_ascii=False))
            before_bytes = self._section_bytes(data, 'relationship')
            block = data.setdefault('relationship', {'items': [], 'updated_at': ''})
            items = block.setdefault('items', [])
            now = _now()

            if name == 'profile_append':
                text = args.get('text')
                if not isinstance(text, str) or not text.strip():
                    return 'text must be a non-empty string.', '被拒绝', name
                clean_text = text.strip()
                norm = _norm_text(clean_text)
                if self._is_rebutted_by_lan(clean_text, data):
                    return 'This rule was already corrected or removed by Lan.', '被拒绝', name
                # Idempotency: avoid duplicate active items with identical normalized text.
                existing = next(
                    (row for row in items if isinstance(row, dict)
                     and row.get('status') != 'outdated' and _norm_text(row.get('text')) == norm),
                    None,
                )
                if existing is not None:
                    if existing.get('locked'):
                        return (
                            f'An identical relationship item ({existing["id"]}) is already locked by Lan. '
                            f'Nothing was changed.'
                        ), '被拒绝', name
                    return (
                        f'Relationship note already exists ({existing["id"]}, {existing["status"]}, '
                        f'{existing["source"]}). Nothing duplicated.'
                    ), '成功', name
                provenance = self._relationship_provenance(requested, raw_sources, claim_text=clean_text, data=data)
                error = self._automatic_write_error(data, clean_text, source=provenance['source'])
                if error:
                    return error, '被拒绝', name
                new_item = {
                    'id': uuid.uuid4().hex[:12], 'text': clean_text,
                    'status': provenance['status'], 'source': provenance['source'],
                    'sources': provenance['sources'],
                    'created_at': now, 'updated_at': now,
                }
                if provenance['missing_refs']:
                    new_item['stale_ref'] = True
                    new_item['missing_refs'] = provenance['missing_refs']
                if provenance['evidence_state'] != 'none':
                    new_item['evidence_state'] = provenance['evidence_state']
                items.append(new_item)
                message = (
                    'Saved as a current relationship note (%s, %s). It applies from the next request.'
                    % (provenance['status'], provenance['source'])
                )
            else:
                old, new = args.get('old_text'), args.get('new_text')
                if not isinstance(old, str) or not old or not isinstance(new, str):
                    return ('old_text must be a non-empty string and new_text a string.', '被拒绝', name)
                if new != '' and not new.strip():
                    return (
                        'new_text is only whitespace. Pass empty text to retire the note, or the replacement text.'
                    ), '被拒绝', name
                matches = [
                    item for item in items if item['text'].strip() == old.strip()
                    and item['status'] != 'outdated'
                ]
                if len(matches) != 1:
                    return (
                        f'old_text matches {len(matches)} current relationship items; it must match '
                        f'exactly one. Nothing was changed.'
                    ), '被拒绝', name
                previous = matches[0]
                clean_new = new.strip()
                provenance = self._relationship_provenance(
                    requested, raw_sources, claim_text=clean_new, data=data
                ) if clean_new else {
                    'status': 'uncertain', 'source': 'peter', 'sources': [],
                    'dropped_sources': [], 'missing_refs': [], 'unverified_support': [],
                    'evidence_state': 'none', 'downgraded': False, 'rebutted': False,
                }
                error = self._automatic_write_error(
                    data, clean_new, source=provenance['source'], previous=previous,
                )
                if error:
                    return error, '被拒绝', name
                replacement = None
                if clean_new:
                    replacement = {
                        'id': uuid.uuid4().hex[:12], 'text': clean_new,
                        'status': provenance['status'], 'source': provenance['source'],
                        'sources': provenance['sources'],
                        'created_at': now, 'updated_at': now,
                    }
                    if provenance['missing_refs']:
                        replacement['stale_ref'] = True
                        replacement['missing_refs'] = provenance['missing_refs']
                    if provenance['evidence_state'] != 'none':
                        replacement['evidence_state'] = provenance['evidence_state']
                    items.append(replacement)
                previous['status'] = 'outdated'
                previous['updated_at'] = now
                if replacement:
                    previous['superseded_by'] = replacement['id']
                message = (
                    'Updated. The old note is kept as history and is no longer sent as current.'
                    if replacement else
                    'That relationship note is no longer current. Nothing was added.'
                )

            notes = []
            if provenance['downgraded']:
                notes.append(prompts.text('memory.relationship.no_source'))
            elif provenance['dropped_sources']:
                notes.append(
                    'Ignored source references the program could not verify: '
                    + ', '.join(provenance['dropped_sources'][:5]) + '.'
                )
            elif provenance['source'] != 'evidence' and (name == 'profile_append' or new.strip()):
                notes.append('Kept as uncertain; no evidence reference was supplied.')
            if provenance['dropped_sources'] and provenance['downgraded']:
                notes.append(
                    'Uncheckable references: ' + ', '.join(provenance['dropped_sources'][:5]) + '.'
                )
            if provenance.get('unverified_support'):
                notes.append(
                    'Content support could not be verified for: '
                    + ', '.join(provenance['unverified_support'][:5]) + '.'
                )

            self._validate_relationship_items(items)
            block['updated_at'] = now
            after_bytes = self._section_bytes(data, 'relationship')
            limit = self.section_budgets['relationship']
            if after_bytes > limit and after_bytes > before_bytes:
                over = after_bytes - limit
                return (
                    f'relationship would be {after_bytes} bytes; the relationship limit is {limit} '
                    f'({over} over). Nothing was changed. Merge or retire outdated/low-priority items first.'
                ), '被拒绝', name
            self._commit_with_version(name, before, data)
            return ' '.join([message, *notes, f'relationship: {after_bytes} of {limit} bytes used.']), '成功', name

    def _invoke_ongoing(self, args):
        action = args.get('action')
        text = args.get('text')
        item_id = args.get('id')
        sources = args.get('sources', [])
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            return 'sources must be a list of strings.', '被拒绝', 'ongoing_update'
        if action == 'list':
            return self._list_ongoing(args)
        with self.lock:
            data = self._load()
            before = json.loads(json.dumps(data, ensure_ascii=False))
            before_bytes = self._section_bytes(data, 'ongoing')
            items = data['ongoing']['items']
            now = _now()
            if action == 'add':
                if not isinstance(text, str) or not text.strip():
                    return 'text must be a non-empty string.', '被拒绝', 'ongoing_update'
                error = self._automatic_write_error(data, text.strip())
                if error:
                    return error, '被拒绝', 'ongoing_update'
                item = {
                    'id': uuid.uuid4().hex[:12], 'text': text.strip(), 'status': 'active',
                    'sources': sources, 'created_at': now, 'updated_at': now,
                    'last_verified_at': now,
                }
                items.append(item)
                message = f'Added ongoing {item["id"]}.'
            elif action in ('update', 'complete', 'reopen'):
                if not isinstance(item_id, str) or not item_id:
                    return 'id is required for this action.', '被拒绝', 'ongoing_update'
                item = next((row for row in items if row['id'] == item_id), None)
                if item is None:
                    return f'No ongoing item with id {item_id}.', '被拒绝', 'ongoing_update'
                if (action == 'reopen' or (action == 'update' and args.get('status') in ('active', 'uncertain'))):
                    error = self._automatic_write_error(data, item['text'])
                    if item.get('lan_completed') or error:
                        return error or 'This task was completed by Lan.', '被拒绝', 'ongoing_update'
                if action == 'update':
                    if text is not None:
                        if not isinstance(text, str) or not text.strip():
                            return 'text must be a non-empty string.', '被拒绝', 'ongoing_update'
                        if text.strip() != item['text']:
                            error = self._automatic_write_error(data, text.strip())
                            if item.get('lan_text') or error:
                                return error or 'This task text was saved by Lan.', '被拒绝', 'ongoing_update'
                            item['text'] = text.strip()
                    if sources:
                        item['sources'] = sources
                    if args.get('status') in ('active', 'uncertain'):
                        item['status'] = args['status']
                    if text is None and not sources and not args.get('status'):
                        return 'Nothing to update: pass text, sources and/or status.', '被拒绝', 'ongoing_update'
                elif action == 'complete':
                    item['status'] = 'completed'
                    item['completed_at'] = now
                else:
                    item['status'] = 'active'
                    item.pop('completed_at', None)
                item['updated_at'] = now
                message = f'Ongoing {item_id} is now {item["status"]}.'
            elif action == 'archive':
                return ('Archive was removed; use complete when the thing is over.', '被拒绝', 'ongoing_update')
            else:
                return 'action must be add, update, complete, reopen or list.', '被拒绝', 'ongoing_update'
            self._validate_items(items)
            after_bytes = self._section_bytes(data, 'ongoing')
            limit = self.section_budgets['ongoing']
            if after_bytes > limit and after_bytes > before_bytes:
                over = after_bytes - limit
                return (
                    f'ongoing would be {after_bytes} bytes; the ongoing limit is {limit} '
                    f'({over} over). Nothing was changed.'
                ), '被拒绝', 'ongoing_update'
            data['ongoing']['updated_at'] = now
            self._commit_with_version(f'ongoing_{action}', before, data)
            return f'{message} ongoing: {after_bytes} of {limit} bytes used.', '成功', 'ongoing_update'

    def _list_ongoing(self, args, limit=20):
        """Ticket 413: finished items are not injected, so he reads them on request, with the time."""
        status = args.get('status') or 'completed'
        if status not in ('completed', 'active', 'uncertain', 'all'):
            return ('status must be completed, active, uncertain or all.', '被拒绝', 'ongoing_update')
        try:
            data = self._load()
        except UserError as exc:
            return str(exc), '被拒绝', 'ongoing_update'
        rows = [
            item for item in data['ongoing']['items']
            if status == 'all' or item['status'] == status
        ]
        if not rows:
            return f'No {status} ongoing items.', '成功', 'ongoing_update'
        lines = [f'{len(rows)} {status} ongoing item(s):']
        for item in rows[:limit]:
            stamp = ''
            when = item.get('completed_at', '')
            if item['status'] == 'completed' and isinstance(when, str) and when:
                stamp = ' (completed ' + when[:16].replace('T', ' ') + ')'
            lines.append(f'- {item["id"]}{stamp}: {item["text"].strip()}')
        if len(rows) > limit:
            lines.append(f'... {len(rows) - limit} more not shown.')
        return '\n'.join(lines), '成功', 'ongoing_update'
