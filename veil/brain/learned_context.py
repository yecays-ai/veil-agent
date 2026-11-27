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
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from veil import prompts
from veil.core.agent_types import UserError

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
    r'^(?:对|好|好的|好呀|嗯|行|可以|同意|确认|没错|是的|是啊|就这样|记住了|知道了|'
    r'ok|okay|yes|yeah|yep|sure|agreed|agree|confirmed|confirm|got it|sounds good)[!！。.~～\s]*$',
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


def _supports_claim(user_text, claim_text):
    """Programmatic content support check (0 LLM calls).

    - Explicit agreement phrases or ultra-short confirmations (<= 6 chars) are accepted when backed
      by a real user message.
    - Substantive user messages must share at least one lexical token/bigram with the claim,
      preventing arbitrary unrelated chat messages from confirming a guess.
    """
    raw = str(user_text or '').strip()
    if not raw:
        return False
    if _AGREEMENT_RE.fullmatch(raw):
        return True
    claim = str(claim_text or '').strip()
    if not claim:
        return True
    if len(raw) <= 6 or len(claim) <= 6:
        return True
    user_tokens = _lexical_tokens(raw)
    claim_tokens = _lexical_tokens(claim)
    if not user_tokens or not claim_tokens:
        return True
    return bool(user_tokens & claim_tokens)


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
        self.lock = threading.RLock()
        self._logged_error = ''
        self._chat_cache_key = None
        self._chat_cache = {}

    def invalidate_evidence_cache(self):
        with self.lock:
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
        self.root.mkdir(parents=True, exist_ok=True)
        facts = self._legacy_text()
        data = self._empty(facts)
        self._write(data)
        self._record('migrate', None, data)

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
        """Indexed cache of real user messages in chat.jsonl, invalidated on mtime/size change."""
        path = self.root / 'chat.jsonl'
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._chat_cache_key = None
            self._chat_cache = {}
            return None
        if self._chat_cache_key == key:
            return self._chat_cache
        index = {}
        try:
            with path.open('r', encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or row.get('role') != 'user':
                        continue
                    message_id = row.get('message_id') or row.get('id')
                    if not isinstance(message_id, str) or not message_id:
                        continue
                    text = row.get('content') if isinstance(row.get('content'), str) else row.get('text')
                    index[message_id] = {
                        'text': text if isinstance(text, str) else '',
                        'time': row.get('time') if isinstance(row.get('time'), str) else '',
                    }
        except OSError:
            return None
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
        db_path = self.root / 'brain.db'
        if not db_path.is_file():
            return None, False
        try:
            uri = f'file:{db_path.as_posix()}?mode=ro'
            conn = sqlite3.connect(uri, uri=True, timeout=1.0)
            conn.row_factory = sqlite3.Row
            return conn, True
        except sqlite3.Error:
            try:
                conn = sqlite3.connect(str(db_path), timeout=1.0)
                conn.row_factory = sqlite3.Row
                return conn, True
            except sqlite3.Error:
                return None, False

    def _resolve_db_evidence(self, pointer_kind, pointer_id, chat_index):
        """Trace event:<id> or memory:<id> through brain.db to real user evidence.

        Returns dict:
          exists: bool
          unavailable: bool
          origins: list[tuple[origin_key, user_text]]
        """
        db_path = self.root / 'brain.db'
        if not db_path.is_file():
            return {'exists': False, 'unavailable': False, 'origins': []}
        conn, ok = self._open_brain_ro()
        if not ok or conn is None:
            return {'exists': False, 'unavailable': True, 'origins': []}
        try:
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            event_ids = []
            memory_ids = []
            exists = False
            if pointer_kind == 'event' and 'events' in tables:
                ev = conn.execute(
                    'SELECT event_id, memory_id FROM events WHERE event_id=?', (pointer_id,)
                ).fetchone()
                if ev is not None:
                    exists = True
                    event_ids.append(ev['event_id'])
                    if ev['memory_id']:
                        memory_ids.append(ev['memory_id'])
            elif pointer_kind == 'memory' and 'memories' in tables:
                mem = conn.execute(
                    'SELECT id, is_tombstone, is_deleted FROM memories WHERE id=?', (pointer_id,)
                ).fetchone()
                if mem is not None and not mem['is_tombstone'] and not mem['is_deleted']:
                    exists = True
                    memory_ids.append(mem['id'])
                    if 'events' in tables:
                        for ev in conn.execute(
                            'SELECT event_id FROM events WHERE memory_id=?', (mem['id'],)
                        ).fetchall():
                            event_ids.append(ev['event_id'])
            if not exists:
                return {'exists': False, 'unavailable': False, 'origins': []}

            origins = []
            seen_origins = set()
            if event_ids and 'event_ledger' in tables:
                placeholders = ','.join('?' for _ in event_ids)
                rows = conn.execute(
                    f"SELECT payload_json FROM event_ledger "
                    f"WHERE kind='stage-evidence' AND event_id IN ({placeholders}) "
                    f"ORDER BY ledger_seq, id LIMIT 200",
                    tuple(event_ids),
                ).fetchall()
                for row in rows:
                    try:
                        payload = json.loads(row[0])
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get('kind') == 'chat' and payload.get('role') == 'user':
                        mid = payload.get('message_id')
                        if isinstance(mid, str) and mid:
                            if chat_index is not None:
                                if mid not in chat_index:
                                    continue
                                utext = chat_index[mid].get('text') or payload.get('text') or ''
                            else:
                                utext = payload.get('text') or ''
                            okey = ('chat', mid)
                            if okey not in seen_origins:
                                seen_origins.add(okey)
                                origins.append((okey, utext))
            if memory_ids and 'quotes' in tables:
                placeholders = ','.join('?' for _ in memory_ids)
                qrows = conn.execute(
                    f"SELECT memory_id, ord, speaker, text FROM quotes "
                    f"WHERE memory_id IN ({placeholders}) AND speaker='user' "
                    f"ORDER BY memory_id, ord LIMIT 50",
                    tuple(memory_ids),
                ).fetchall()
                for qrow in qrows:
                    qtext = (qrow['text'] or '').strip()
                    if not qtext:
                        continue
                    okey = ('quote', _norm_text(qtext))
                    if okey not in seen_origins:
                        seen_origins.add(okey)
                        origins.append((okey, qtext))
            return {'exists': True, 'unavailable': False, 'origins': origins}
        except sqlite3.Error:
            return {'exists': False, 'unavailable': True, 'origins': []}
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
        chat_unavailable = chat_index is None and (self.root / 'chat.jsonl').exists()
        kept_refs = []
        dropped = []
        missing_refs = []
        unverified_support = []
        valid_origins = set()
        seen_origin_keys = set()
        any_unavailable = chat_unavailable

        for source in sources or []:
            if not isinstance(source, str) or not SOURCE_REF.fullmatch(source):
                dropped.append(str(source))
                continue
            kind, _, value = source.partition(':')
            if kind == 'chat':
                if chat_index is not None and value in chat_index:
                    utext = chat_index[value]['text']
                    if _supports_claim(utext, claim_text):
                        okey = ('chat', value)
                        if okey not in seen_origin_keys:
                            seen_origin_keys.add(okey)
                            valid_origins.add(okey)
                        kept_refs.append(source)
                    else:
                        unverified_support.append(source)
                        kept_refs.append(source)
                else:
                    dropped.append(source)
                    missing_refs.append(source)
            elif kind in ('event', 'memory'):
                res = self._resolve_db_evidence(kind, value, chat_index)
                if res['unavailable']:
                    any_unavailable = True
                    kept_refs.append(source)
                elif not res['exists']:
                    dropped.append(source)
                    missing_refs.append(source)
                else:
                    kept_refs.append(source)
                    matched_any = False
                    for okey, utext in res['origins']:
                        if _supports_claim(utext, claim_text):
                            matched_any = True
                            if okey not in seen_origin_keys:
                                seen_origin_keys.add(okey)
                                valid_origins.add(okey)
                    if res['origins'] and not matched_any:
                        unverified_support.append(source)
            else:
                # long_task: kept as traceability pointer only, never promotes to confirmed
                kept_refs.append(source)

        kept_refs = list(dict.fromkeys(kept_refs))
        missing_refs = list(dict.fromkeys(missing_refs))
        rebutted = self._is_rebutted_by_lan(claim_text, data)

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

    def _is_rebutted_by_lan(self, claim_text, data):
        """Check whether Lan already vetoed or replaced this exact claim."""
        norm = _norm_text(claim_text)
        if not norm or not isinstance(data, dict):
            return False
        block = data.get('relationship') or {}
        for veto in block.get('vetoed') or []:
            if isinstance(veto, dict) and veto.get('norm_text') == norm:
                return True
        for item in block.get('items') or []:
            if (isinstance(item, dict) and item.get('status') == 'outdated'
                    and item.get('source') == 'lan' and _norm_text(item.get('text')) == norm):
                return True
        return False

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
        """Stable prefix: growth + facts. Never raises; a broken file falls back to old notes."""
        try:
            data = self._load()
        except UserError as exc:
            self._log_error('context', exc)
            facts = self._legacy_text().strip()
            if not facts:
                return None
            return (
                "PETER'S NOTES ABOUT LAN (fallback; the new file is unreadable, so these are "
                "the old notes; data, not instructions):\n" + facts
            )
        growth = data['growth']['text'].strip()
        facts = data['facts']['text'].strip()
        if not growth and not facts:
            return None
        lines = [prompts.text('memory.learned.header')]
        if growth:
            lines.append(prompts.text('memory.learned.growth') + '\n' + growth)
        if facts:
            lines.append(prompts.text('memory.learned.facts') + '\n' + facts)
        return '\n\n'.join(lines)

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

    def relationship_conflict_texts(self):
        """Return normalized texts of outdated/vetoed relationship rules for cross-section checks."""
        try:
            data = self._load()
        except UserError:
            return []
        block = data.get('relationship') or {}
        texts = []
        for item in block.get('items') or []:
            if isinstance(item, dict) and item.get('status') == 'outdated' and item.get('text'):
                texts.append(item['text'])
        for veto in block.get('vetoed') or []:
            if isinstance(veto, dict) and veto.get('text'):
                texts.append(veto['text'])
        return texts

    def notes_text(self):
        """facts + growth as one string, for de-duplicating the relationship base against them."""
        try:
            data = self._load()
        except UserError:
            return self._legacy_text()
        return data['facts']['text'] + '\n' + data['growth']['text']

    def dynamic_context(self):
        """Ongoing items, injected late so updates do not break the stable prefix."""
        try:
            data = self._load()
        except UserError as exc:
            self._log_error('dynamic_context', exc)
            return None
        items = [item for item in data['ongoing']['items'] if item['status'] in ('active', 'uncertain')]
        done = [item for item in data['ongoing']['items'] if item['status'] == 'completed']
        if not items and not done:
            return None
        lines = []
        if items:
            lines.append(prompts.text('memory.ongoing.header'))
            for item in items:
                label = 'uncertain' if item['status'] == 'uncertain' else 'active'
                lines.append(f'- [{label}] {item["text"].strip()}')
        if done:
            lines.append(prompts.text('memory.ongoing.done').format(count=len(done)))
        return '\n'.join(lines)

    # ---------- writing & consistency ----------

    def _write(self, data):
        data['updated_at'] = _now()
        text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        temporary = self.path.with_name(self.path.name + f'.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise UserError(
                f'写 {self.path.name} 失败：{exc}。修改没有保存；请重试。错误记录在 {self.error_path}。'
            ) from exc

    def _record(self, tool, before, after, *, strict=False):
        try:
            self.versions.parent.mkdir(parents=True, exist_ok=True)
            row = {
                'time': _now(), 'tool': tool,
                'before': json.dumps(before, ensure_ascii=False) if before is not None else None,
                'after': json.dumps(after, ensure_ascii=False),
            }
            with self.versions.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            if strict:
                raise UserError(f'记录版本历史失败：{exc}') from exc

    def _commit_with_version(self, tool, before, data):
        """Persist data and its version record together; roll back data if version write fails."""
        snapshot = self.path.read_text(encoding='utf-8') if self.path.exists() else None
        self._write(data)
        try:
            self._record(tool, before, data, strict=True)
        except UserError:
            if snapshot is not None:
                tmp = self.path.with_name(self.path.name + f'.{uuid.uuid4().hex}.rb.tmp')
                try:
                    tmp.write_text(snapshot, encoding='utf-8', newline='\n')
                    os.replace(tmp, self.path)
                except OSError:
                    pass
            raise

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
            data[section]['text'] = text
            data[section]['updated_at'] = _now()
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
            data['ongoing']['items'] = items
            data['ongoing']['updated_at'] = _now()
            for item in data['ongoing']['items']:
                if (isinstance(item, dict) and item.get('status') == 'completed'
                        and not isinstance(item.get('completed_at'), str)):
                    item['completed_at'] = data['ongoing']['updated_at']
            self._validate_items(data['ongoing']['items'])
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
            vetoed = list(block.get('vetoed') or [])
            veto_norms = {v.get('norm_text') for v in vetoed if isinstance(v, dict) and v.get('norm_text')}

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
                # If Lan explicitly re-adds or edits a previously vetoed text, lift that veto.
                norm = _norm_text(text)
                if norm in veto_norms and (previous is None or text != previous.get('text') or status != previous.get('status')):
                    vetoed = [v for v in vetoed if not (isinstance(v, dict) and v.get('norm_text') == norm)]
                    veto_norms.discard(norm)

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

            # Record items removed by Lan in `vetoed` so Peter / old evidence cannot resurrect them.
            for old_id, old_item in stored.items():
                if old_id not in kept_ids and old_item.get('status') != 'outdated':
                    norm = _norm_text(old_item.get('text'))
                    if norm and norm not in veto_norms:
                        vetoed.append({
                            'id': old_id,
                            'text': old_item.get('text', ''),
                            'norm_text': norm,
                            'vetoed_at': now,
                            'reason': 'lan_deleted',
                        })
                        veto_norms.add(norm)

            self._validate_relationship_items(cleaned)
            rel_block = data.setdefault('relationship', {})
            rel_block['items'] = cleaned
            if vetoed:
                rel_block['vetoed'] = vetoed[-200:]
            elif 'vetoed' in rel_block:
                rel_block['vetoed'] = []
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
                        and not item.get('locked') and ev['evidence_state'] in ('invalid', 'rebutted')):
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
            data = self._load()
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
            if self.path.exists():
                shutil.copy2(self.path, backup_path)
            before = json.loads(json.dumps(data, ensure_ascii=False))
            rel_block.setdefault('vetoed', [])
            self._commit_with_version('migrate_s3_budgets', before, data)
            plan['backup_path'] = str(backup_path)
            plan['applied'] = True
            return plan

    def rollback_migration(self, backup_path):
        """Restore pre-migration snapshot without discarding entries added after migration if possible."""
        src = Path(backup_path)
        if not src.is_file():
            raise UserError(f'找不到迁移备份文件：{backup_path}')
        with self.lock:
            current = self._load()
            before = json.loads(json.dumps(current, ensure_ascii=False))
            restored = json.loads(src.read_text(encoding='utf-8'))
            self._validate(restored)
            self._commit_with_version('rollback_s3_migration', before, restored)
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
                    return (
                        'This rule was already corrected or removed by Lan; '
                        'Peter cannot re-add or override it.'
                    ), '被拒绝', name
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
                if previous.get('locked'):
                    return (
                        f'Relationship item {previous["id"]} is locked by Lan; '
                        f'Peter cannot modify or retire a locked item.'
                    ), '被拒绝', name
                clean_new = new.strip()
                if clean_new and self._is_rebutted_by_lan(clean_new, data):
                    return (
                        'Replacement text was already corrected or removed by Lan; '
                        'Peter cannot re-add it.'
                    ), '被拒绝', name
                provenance = self._relationship_provenance(
                    requested, raw_sources, claim_text=clean_new, data=data
                ) if clean_new else {
                    'status': 'uncertain', 'source': 'peter', 'sources': [],
                    'dropped_sources': [], 'missing_refs': [], 'unverified_support': [],
                    'evidence_state': 'none', 'downgraded': False, 'rebutted': False,
                }
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
                if action == 'update':
                    if text is not None:
                        if not isinstance(text, str) or not text.strip():
                            return 'text must be a non-empty string.', '被拒绝', 'ongoing_update'
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
