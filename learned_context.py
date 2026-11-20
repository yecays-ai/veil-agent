
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
import threading
import uuid
from datetime import datetime
from pathlib import Path

import prompts
from agent_types import UserError

SECTIONS = ('facts', 'growth')
ONGOING_STATUSES = ('active', 'uncertain', 'completed', 'archived')
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
            self._validate(data)
            return data

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
        self._check_budget(self._content_bytes(data), 0)

    def _content_bytes(self, data=None):
        if data is None:
            data = self._load()
        total = _text_bytes(data['facts']['text']) + _text_bytes(data['growth']['text'])
        total += sum(_text_bytes(item['text']) for item in data['ongoing']['items'])
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

    def dynamic_context(self):
        """Ongoing items, injected late so updates do not break the stable prefix."""
        try:
            data = self._load()
        except UserError as exc:
            self._log_error('dynamic_context', exc)
            return None
        items = [item for item in data['ongoing']['items'] if item['status'] in ('active', 'uncertain')]
        if not items:
            return None
        lines = [prompts.text('memory.ongoing.header')]
        for item in items:
            label = 'uncertain' if item['status'] == 'uncertain' else 'active'
            lines.append(f'- [{label}] {item["text"].strip()}')
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
            self._validate_items(data['ongoing']['items'])
            self._check_budget(self._content_bytes(data), 0)
            self._write(data)
            self._record('save_ongoing', before, data)
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
                  'sections': {}, 'ongoing': {'items': [], 'updated_at': data['ongoing'].get('updated_at', '')}}
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
        if section not in SECTIONS:
            return 'section must be facts or growth.', '被拒绝', name
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

    def _invoke_ongoing(self, args):
        action = args.get('action')
        text = args.get('text')
        item_id = args.get('id')
        sources = args.get('sources', [])
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            return 'sources must be a list of strings.', '被拒绝', 'ongoing_update'
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
            elif action in ('update', 'complete', 'archive', 'reopen'):
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
                elif action == 'archive':
                    item['status'] = 'archived'
                else:
                    item['status'] = 'active'
                    item.pop('completed_at', None)
                item['updated_at'] = now
                message = f'Ongoing {item_id} is now {item["status"]}.'
            else:
                return 'action must be add, update, complete, archive or reopen.', '被拒绝', 'ongoing_update'
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
