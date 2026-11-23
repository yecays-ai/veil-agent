
"""Ticket 394: Learned Context for Peter.

Three sections carried into every request:
- facts: what he knows about Lan (migrated from about-lan.md)
- growth: what he learned from real experience, especially conflict repair
- ongoing: things not finished yet, as items with a lifecycle

Facts and growth sit in the stable prefix; ongoing is injected near the user message so a
frequent update does not invalidate the provider's prefix cache. The user's edits always win.
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import prompts
from agent_types import UserError

SECTIONS = ('facts', 'growth')
# Ticket 422: the stable relationship base. Each item is one thing Peter currently understands about the
# two of them. `confirmed` and `uncertain` reach the model (the uncertain ones labelled as his guess);
# `outdated` is kept only as history and never sent, so a stale rule cannot pass as current consensus.
RELATIONSHIP_STATUSES = ('confirmed', 'uncertain', 'outdated')
# Who says so. Lan's own words outrank repeated evidence, evidence outranks Peter's reading.
RELATIONSHIP_SOURCES = ('lan', 'evidence', 'peter')
SOURCE_RANK = {'lan': 3, 'evidence': 2, 'peter': 1}
# Ticket 422 review: a relationship item keeps the smallest evidence a program can check.
# The event ledger owns event/memory ids; the chat log owns message ids.
SOURCE_REF = re.compile(r'^(chat|memory|event|long_task):[A-Za-z0-9_-]{1,100}$')
# Ticket 413: ongoing means "not finished yet". Finished items stay in the file as completed so Lan can
# read, reopen or delete them, but only active/uncertain reach the model and count toward the byte budget.
ONGOING_STATUSES = ('active', 'uncertain', 'completed')
# Old files may still say archived; it is read as completed and the next save rewrites it.
ONGOING_LEGACY = {'archived': 'completed'}
DEFAULT_MAX_BYTES = 4000
VERSION = 1


def _now():
    return datetime.now().astimezone().isoformat(timespec='microseconds')


def _text_bytes(value):
    return len(value.encode('utf-8'))


class LearnedContext:
    def __init__(self, data_root, character_id, settings):
        if not isinstance(settings, dict) or not isinstance(settings.get('file'), str):
            raise UserError('Learned Context 配置不完整。')
        self.root = Path(data_root) / character_id
        self.path = self.root / settings['file']
        self.legacy_path = self.root / settings.get('legacy_file', 'about-lan.md')
        self.max_bytes = int(settings.get('max_bytes', DEFAULT_MAX_BYTES))
        self.versions = self.root / (Path(self.path).stem + '.versions.jsonl')
        self.error_path = self.root / 'peter-page-errors.jsonl'
        self.lock = threading.RLock()
        self._logged_error = ''

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
            'relationship': {'items': [], 'updated_at': ''},
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
                raise UserError(f'读不到 {self.path.name}：{exc}。请保留 data 文件夹并查看 {self.error_path.name}。') from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise UserError(f'{self.path.name} 第 {exc.lineno} 行第 {exc.colno} 列不是合法 JSON：{exc.msg}。'
                                f'请恢复到上一个版本；当前聊天会继续使用旧笔记。错误记录在 {self.error_path}。') from exc
            self._upgrade_ongoing(data)
            self._upgrade_relationship(data)
            self._validate(data)
            return data

    @staticmethod
    def _upgrade_relationship(data):
        """Ticket 422: files written before this ticket have no relationship block; read it as empty.

        Nothing is written here: an untouched file stays byte-for-byte the same until Peter or Lan
        actually changes the stable relationship base."""
        if not isinstance(data, dict):
            return
        block = data.get('relationship')
        if not isinstance(block, dict):
            data['relationship'] = {'items': [], 'updated_at': ''}
            return
        if not isinstance(block.get('items'), list):
            block['items'] = []
        if not isinstance(block.get('updated_at'), str):
            block['updated_at'] = ''

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
        seen = set()
        for index, item in enumerate(ongoing['items']):
            if not isinstance(item, dict):
                raise UserError(f'{self.path.name} 的 ongoing.items[{index}] 不是对象。')
            for key, kind in (('id', str), ('text', str), ('status', str)):
                if not isinstance(item.get(key), kind) or (key == 'text' and not item[key].strip()):
                    raise UserError(f'{self.path.name} 的 ongoing.items[{index}].{key} 无效。')
            if item['status'] not in ONGOING_STATUSES:
                raise UserError(f'{self.path.name} 的 ongoing.items[{index}].status 无效。')
            if item['id'] in seen:
                raise UserError(f'{self.path.name} 的 ongoing 条目 id 重复：{item["id"]}。')
            seen.add(item['id'])
            sources = item.get('sources', [])
            if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
                raise UserError(f'{self.path.name} 的 ongoing.items[{index}].sources 无效。')
            for key in ('created_at', 'updated_at', 'last_verified_at'):
                if key in item and not isinstance(item[key], str):
                    raise UserError(f'{self.path.name} 的 ongoing.items[{index}].{key} 无效。')
        block = data.get('relationship') or {}
        if not isinstance(block, dict) or not isinstance(block.get('items'), list):
            raise UserError(f'{self.path.name} 的 relationship 块不完整。请恢复上一个版本。')
        self._validate_relationship_items(block['items'])
        self._check_budget(self._content_bytes(data), 0)

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
            for key in ('created_at', 'updated_at', 'superseded_by'):
                if key in item and not isinstance(item[key], str):
                    raise UserError(f'relationship 第 {index + 1} 条 {key} 无效。')
            sources = item.get('sources', [])
            if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
                raise UserError(f'relationship 第 {index + 1} 条 sources 无效。')
            if not all(SOURCE_REF.fullmatch(source) for source in sources):
                raise UserError(f'relationship 第 {index + 1} 条 sources 里有不认识的引用：'
                                f'只接受 chat:/memory:/event:/long_task: 加编号。')

    # ---------- evidence ----------

    def _known_chat_ids(self, limit=600, max_bytes=512 * 1024):
        """Message ids from the tail of this role's chat log, for checking a `chat:` evidence reference.

        Bounded on purpose: evidence is checked against what was actually said recently, never by
        scanning the whole history. An unreadable log means "cannot verify", which never upgrades a
        claim."""
        path = self.root / 'chat.jsonl'
        try:
            with path.open('rb') as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                raw = handle.read().decode('utf-8', 'replace')
        except OSError:
            return set()
        ids = set()
        for line in raw.splitlines()[-limit:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message_id = row.get('message_id') if isinstance(row, dict) else None
            if isinstance(message_id, str) and message_id:
                ids.add(message_id)
        return ids

    def _verified_sources(self, sources):
        """Keep only evidence references this program can check; report what was dropped.

        A `chat:` reference must name a real message in this role's own chat log. Memory/event
        references are kept as pointers (the event ledger owns them); they are only accepted as
        supporting evidence together with a verified chat reference."""
        valid, dropped = [], []
        chat_ids = None
        for source in sources or []:
            if not isinstance(source, str) or not SOURCE_REF.fullmatch(source):
                dropped.append(str(source))
                continue
            kind, _, value = source.partition(':')
            if kind == 'chat':
                if chat_ids is None:
                    chat_ids = self._known_chat_ids()
                if value not in chat_ids:
                    dropped.append(source)
                    continue
            valid.append(source)
        return list(dict.fromkeys(valid)), dropped

    def _relationship_provenance(self, status, sources):
        """Ticket 422 review: a claim is only consensus when the program can point at why.

        - with at least one verified source: `confirmed` is accepted as evidence-backed;
        - without one: whatever the model asked for, the item is Peter's own reading (`uncertain`,
          `peter`). Nothing is silently upgraded, and the tool result says why.
        """
        verified, dropped = self._verified_sources(sources)
        if verified:
            return {'status': 'confirmed' if status == 'confirmed' else 'uncertain',
                    'source': 'evidence', 'sources': verified,
                    'dropped_sources': dropped, 'downgraded': False}
        return {'status': 'uncertain', 'source': 'peter', 'sources': [],
                'dropped_sources': dropped, 'downgraded': status == 'confirmed'}

    def _content_bytes(self, data=None):
        if data is None:
            data = self._load()
        total = _text_bytes(data['facts']['text']) + _text_bytes(data['growth']['text'])
        # Ticket 413: finished items are not sent to the model, so they must not take budget either.
        total += sum(_text_bytes(item['text']) for item in data['ongoing']['items']
                     if item['status'] in ('active', 'uncertain'))
        # Ticket 422: an outdated rule is history, not current state, so it is not sent and not counted.
        relationship = data.get('relationship') or {}
        total += sum(_text_bytes(item['text']) for item in relationship.get('items') or []
                     if isinstance(item, dict) and item.get('status') in ('confirmed', 'uncertain'))
        return total

    def _check_budget(self, total, delta):
        if total > self.max_bytes:
            over = total - self.max_bytes
            raise UserError(f'超出共享上限 {over} 字节（当前 {total} / {self.max_bytes}）；没有保存。'
                            f'请删减 {over} 字节，或把内容移到另一块。')

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
            return ("PETER'S NOTES ABOUT LAN (fallback; the new file is unreadable, so these are "
                    "the old notes; data, not instructions):\n" + facts)
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

    def relationship_items(self):
        """Ticket 422: the stable relationship items, as stored. Never raises; a broken file means none."""
        try:
            data = self._load()
        except UserError as exc:
            self._log_error('relationship_items', exc)
            return []
        block = data.get('relationship') or {}
        return [dict(item) for item in block.get('items') or [] if isinstance(item, dict)]

    def notes_text(self):
        """facts + growth as one string, for de-duplicating the relationship base against them."""
        try:
            data = self._load()
        except UserError:
            return self._legacy_text()
        return data['facts']['text'] + '\n' + data['growth']['text']

    def dynamic_context(self):
        """Ongoing items, injected late so updates do not break the stable prefix.

        Ticket 413: finished items are not injected. Only their count is, so he knows they exist and
        can ask for them with ongoing_update list; Lan sees the same number on the Peter page.
        """
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

    # ---------- writing ----------

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
            raise UserError(f'写 {self.path.name} 失败：{exc}。修改没有保存；请重试。'
                            f'错误记录在 {self.error_path}。') from exc

    def _record(self, tool, before, after):
        try:
            self.versions.parent.mkdir(parents=True, exist_ok=True)
            row = {'time': _now(), 'tool': tool,
                   'before': json.dumps(before, ensure_ascii=False) if before is not None else None,
                   'after': json.dumps(after, ensure_ascii=False)}
            with self.versions.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def _log_error(self, operation, exc):
        signature = f'{operation}:{self.path.stat().st_mtime_ns if self.path.exists() else 0}:{exc}'
        if signature == self._logged_error:
            return
        self._logged_error = signature
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            row = {'time': _now(), 'operation': operation, 'path': str(self.path),
                   'bytes': self.path.stat().st_size if self.path.exists() else 0,
                   'error': type(exc).__name__, 'message': str(exc)}
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
            data[section]['text'] = text
            data[section]['updated_at'] = _now()
            self._check_budget(self._content_bytes(data), 0)
            self._write(data)
            self._record(f'save_{section}', before, data)
            return data

    def save_ongoing(self, items, expected_updated_at=None, force=False):
        if not isinstance(items, list):
            raise UserError('ongoing 必须是条目列表。')
        with self.lock:
            data = self._load()
            if not force and expected_updated_at and data['ongoing'].get('updated_at') != expected_updated_at:
                raise UserError('你打开之后 ongoing 又改过；请先刷新，或用“覆盖他的版本”保存。')
            before = json.loads(json.dumps(data, ensure_ascii=False))
            data['ongoing']['items'] = items
            data['ongoing']['updated_at'] = _now()
            # Ticket 413: completing from the page has to leave a completion time, same as the tool.
            for item in data['ongoing']['items']:
                if isinstance(item, dict) and item.get('status') == 'completed' \
                        and not isinstance(item.get('completed_at'), str):
                    item['completed_at'] = data['ongoing']['updated_at']
            self._validate_items(data['ongoing']['items'])
            self._check_budget(self._content_bytes(data), 0)
            self._write(data)
            self._record('save_ongoing', before, data)
            return data

    def save_relationship(self, items, expected_updated_at=None, force=False):
        """Ticket 422: Lan's own edits to the stable relationship base. Her text always wins.

        Review fix: saving the page must not relabel what Lan did not touch. Items are compared by
        their stable id; only a new item, or one whose text/status she actually changed, becomes
        hers (`source=lan`). Everything else keeps its own source, creation time and evidence."""
        if not isinstance(items, list):
            raise UserError('relationship 必须是条目列表。')
        with self.lock:
            data = self._load()
            block = data.get('relationship') or {}
            if not force and expected_updated_at and block.get('updated_at') != expected_updated_at:
                raise UserError('你打开之后这一块又改过；请先刷新，或用“覆盖他的版本”保存。')
            before = json.loads(json.dumps(data, ensure_ascii=False))
            stored = {item['id']: item for item in block.get('items') or []
                      if isinstance(item, dict) and isinstance(item.get('id'), str)}
            now = _now()
            cleaned = []
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                    raise UserError('relationship 每条都要有 id。')
                previous = stored.get(item['id'])
                text = item.get('text').strip() if isinstance(item.get('text'), str) else ''
                status = item.get('status')
                if status not in RELATIONSHIP_STATUSES:
                    raise UserError('relationship 第 %s 条状态无效。' % item['id'])
                row = {'id': item['id'], 'text': text, 'status': status}
                if previous is not None and text == previous.get('text') and status == previous.get('status'):
                    # Untouched: keep source, created time, evidence and superseded pointer as they were.
                    row['source'] = previous.get('source', 'peter')
                    row['created_at'] = previous.get('created_at', now)
                    row['updated_at'] = previous.get('updated_at', now)
                    for key in ('sources', 'superseded_by'):
                        if key in previous:
                            row[key] = previous[key]
                    cleaned.append(row)
                    continue
                # New, or actually edited by Lan: her word becomes the source, her edit the time.
                row['source'] = 'lan'
                row['created_at'] = (previous or {}).get('created_at') or now
                row['updated_at'] = now
                sources = item.get('sources')
                if sources is None:
                    sources = (previous or {}).get('sources', [])
                row['sources'] = sources if isinstance(sources, list) else []
                if previous and previous.get('superseded_by'):
                    row['superseded_by'] = previous['superseded_by']
                cleaned.append(row)
            self._validate_relationship_items(cleaned)
            data.setdefault('relationship', {})['items'] = cleaned
            data['relationship']['updated_at'] = now
            self._check_budget(self._content_bytes(data), 0)
            self._write(data)
            self._record('save_relationship', before, data)
            return data

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
            for key in ('created_at', 'updated_at', 'last_verified_at'):
                if key in item and not isinstance(item[key], str):
                    raise UserError(f'ongoing 第 {index + 1} 条 {key} 无效。')

    def status(self, with_text=True):
        try:
            data = self._load()
        except UserError as exc:
            return {'available': False, 'error': str(exc), 'path': str(self.path),
                    'error_log': str(self.error_path), 'max_bytes': self.max_bytes}
        result = {'available': True, 'error': '', 'path': str(self.path), 'max_bytes': self.max_bytes,
                  'used_bytes': self._content_bytes(data), 'updated_at': data.get('updated_at', ''),
                  'sections': {}, 'ongoing': {'items': [], 'updated_at': data['ongoing'].get('updated_at', '')},
                  'relationship': {'items': [],
                                   'updated_at': (data.get('relationship') or {}).get('updated_at', '')}}
        block = data.get('relationship') or {}
        for item in block.get('items') or []:
            copy = dict(item)
            if not with_text:
                copy['text'] = ''
            result['relationship']['items'].append(copy)
        for section in SECTIONS:
            value = data[section]
            result['sections'][section] = {
                'text': value['text'] if with_text else '',
                'bytes': _text_bytes(value['text']),
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
                    return f'old_text appears {count} times in {section}; it must appear exactly once. Nothing was changed.', '被拒绝', name
                after = before.replace(old, new).strip()
            old_data = json.loads(json.dumps(data, ensure_ascii=False))
            data[section]['text'] = after
            data[section]['updated_at'] = _now()
            total = self._content_bytes(data)
            if total > self.max_bytes:
                over = total - self.max_bytes
                return (f'Your notes would be {total} bytes; the shared limit is {self.max_bytes} ({over} over). '
                        f'Nothing was changed. Merge or shorten existing passages first.'), '被拒绝', name
            self._write(data)
            self._record(name, old_data, data)
            return (f'Saved. {section} is now {total} of {self.max_bytes} bytes; '
                    f'it takes effect from the next request.'), '成功', name

    def _invoke_relationship(self, name, args):
        """Ticket 422: Peter keeps the stable relationship base himself.

        A new item is what he now understands. Replacing one keeps the old item as history
        (`outdated`, pointing at the new one), so "I used to think X, then she confirmed Y" is still
        there for a recollection, while the current base only carries Y. An empty new_text retires
        the item without adding anything: that is how a rule stops being current.

        Review: `confirmed` is not something the model can simply assert. It becomes consensus only
        when the program can verify an evidence reference (a real message id, or an event/memory
        pointer supplied alongside it). Otherwise the item stays Peter's own reading."""
        requested = args.get('status') or 'confirmed'
        if requested not in ('confirmed', 'uncertain'):
            return 'status must be confirmed or uncertain (outdated is kept for history only).', '被拒绝', name
        raw_sources = args.get('sources') or []
        if not isinstance(raw_sources, list) or not all(isinstance(s, str) for s in raw_sources):
            return 'sources must be a list of strings.', '被拒绝', name
        with self.lock:
            data = self._load()
            before = json.loads(json.dumps(data, ensure_ascii=False))
            block = data.setdefault('relationship', {'items': [], 'updated_at': ''})
            items = block.setdefault('items', [])
            now = _now()
            provenance = self._relationship_provenance(requested, raw_sources)
            notes = []
            if provenance['downgraded']:
                notes.append(prompts.text('memory.relationship.no_source'))
            elif provenance['dropped_sources']:
                notes.append('Ignored source references the program could not verify: '
                             + ', '.join(provenance['dropped_sources'][:5]) + '.')
            elif provenance['source'] != 'evidence':
                notes.append('Kept as uncertain; no evidence reference was supplied.')
            if provenance['dropped_sources'] and provenance['downgraded']:
                notes.append('Uncheckable references: '
                             + ', '.join(provenance['dropped_sources'][:5]) + '.')
            if name == 'profile_append':
                text = args.get('text')
                if not isinstance(text, str) or not text.strip():
                    return 'text must be a non-empty string.', '被拒绝', name
                items.append({'id': uuid.uuid4().hex[:12], 'text': text.strip(),
                              'status': provenance['status'], 'source': provenance['source'],
                              'sources': provenance['sources'],
                              'created_at': now, 'updated_at': now})
                message = ('Saved as a current relationship note (%s, %s). It applies from the next request.'
                           % (provenance['status'], provenance['source']))
            else:
                old, new = args.get('old_text'), args.get('new_text')
                if not isinstance(old, str) or not old or not isinstance(new, str):
                    return ('old_text must be a non-empty string and new_text a string.',
                            '被拒绝', name)
                matches = [item for item in items if item['text'].strip() == old.strip()
                           and item['status'] != 'outdated']
                if len(matches) != 1:
                    return (f'old_text matches {len(matches)} current relationship items; it must match '
                            f'exactly one. Nothing was changed.', '被拒绝', name)
                previous = matches[0]
                replacement = None
                if new.strip():
                    replacement = {'id': uuid.uuid4().hex[:12], 'text': new.strip(),
                                   'status': provenance['status'], 'source': provenance['source'],
                                   'sources': provenance['sources'],
                                   'created_at': now, 'updated_at': now}
                    items.append(replacement)
                previous['status'] = 'outdated'
                previous['updated_at'] = now
                if replacement:
                    previous['superseded_by'] = replacement['id']
                message = ('Updated. The old note is kept as history and is no longer sent as current.'
                           if replacement else
                           'That relationship note is no longer current. Nothing was added.')
            self._validate_relationship_items(items)
            block['updated_at'] = now
            total = self._content_bytes(data)
            if total > self.max_bytes:
                over = total - self.max_bytes
                return (f'Your notes would be {total} bytes; the shared limit is {self.max_bytes} '
                        f'({over} over). Nothing was changed. Merge or shorten existing passages first.',
                        '被拒绝', name)
            self._write(data)
            self._record(name, before, data)
            return ' '.join([message, *notes,
                             f'{total} of {self.max_bytes} bytes used.']), '成功', name

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
            items = data['ongoing']['items']
            now = _now()
            if action == 'add':
                if not isinstance(text, str) or not text.strip():
                    return 'text must be a non-empty string.', '被拒绝', 'ongoing_update'
                item = {'id': uuid.uuid4().hex[:12], 'text': text.strip(), 'status': 'active',
                        'sources': sources, 'created_at': now, 'updated_at': now,
                        'last_verified_at': now}
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
                return ('Archive was removed; use complete when the thing is over.',
                        '被拒绝', 'ongoing_update')
            else:
                return 'action must be add, update, complete, reopen or list.', '被拒绝', 'ongoing_update'
            self._validate_items(items)
            total = self._content_bytes(data)
            if total > self.max_bytes:
                over = total - self.max_bytes
                return (f'Ongoing would make the notes {total} bytes; the shared limit is {self.max_bytes} '
                        f'({over} over). Nothing was changed.'), '被拒绝', 'ongoing_update'
            data['ongoing']['updated_at'] = now
            self._write(data)
            self._record(f'ongoing_{action}', before, data)
            return message, '成功', 'ongoing_update'

    def _list_ongoing(self, args, limit=20):
        """Ticket 413: finished items are not injected, so he reads them on request, with the time."""
        status = args.get('status') or 'completed'
        if status not in ('completed', 'active', 'uncertain', 'all'):
            return ('status must be completed, active, uncertain or all.',
                    '被拒绝', 'ongoing_update')
        try:
            data = self._load()
        except UserError as exc:
            return str(exc), '被拒绝', 'ongoing_update'
        rows = [item for item in data['ongoing']['items']
                if status == 'all' or item['status'] == status]
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
