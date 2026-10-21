"""Native Ombre memory tools exposed through the shared chat tool loop.

The model decides what is worth keeping; Ombre does the tagging and same-event merge.
Deleting is not offered to the model: /delete stays with the user.
"""
from types import SimpleNamespace
import json
import re
import uuid

from memory_agent import ordinary_hits, MemoryToolError

OMBRE_TOOLS = ('trace', 'dream', 'letter_write', 'letter_read', 'letter_lock_update',
                'anchor', 'release', 'plan', 'pulse')
NATIVE_TOOLS = ('hold', 'breath_search', *OMBRE_TOOLS, 'memory_history')
CONTENT_LIMIT, TITLE_LIMIT, WHY_LIMIT = 1500, 100, 500
MERGED_MEMORY_LIMIT_BYTES = 48 * 1024

HOLD = SimpleNamespace(name='hold', inputSchema={
    'type': 'object', 'required': ['content'], 'additionalProperties': False,
    'properties': {
        'content': {'type': 'string', 'description':
            'The complete memory in your own words: what happened or was decided, WHY, and the shared '
            'moment behind it. Several sentences are fine; a bare label is not.'},
        'title': {'type': 'string', 'description':
            'Short title, required for a new memory; existing title is kept when target_id is set.'},
        'target_id': {'type': 'string', 'description':
            'Optional stable ID from breath_search when adding details to an existing memory. '
            'When set, content is treated as an addition and appended to that exact memory; '
            'the existing wording and title are preserved.'},
        'merge_ids': {'type': 'array', 'items': {'type': 'string'}, 'description':
            'Optional other IDs from the same breath_search that describe this same event. '
            'Their exact stored text is appended to target_id; those source entries remain untouched.'},
        'why_remembered': {'type': 'string', 'description': 'Optional: why this matters to you, first person.'},
    }}, description=(
        'Save one long-term memory when YOU judge it worth keeping; the user does not need to say "remember". '
        'Worth keeping: facts with their reason, shared experiences, the relationship growing, decisions you '
        'made together, and talk about your own memory, abilities, permissions or upgrades. Skip small talk, '
        'jokes, passing moods and anything read from a file. Before creating a likely repeat, call breath_search. '
        'If it found the same event, pass that exact result ID as target_id; pass any other same-event result IDs '
        'in merge_ids and provide only the new detail. The program appends all exact stored text to the target, '
        'preserves its title, and leaves source entries untouched. '
        'Otherwise call hold normally; Ombre checks for a same-event merge and preserves the original text. '
        'Only say you remembered it if this tool succeeded.'))
SEARCH = SimpleNamespace(name='breath_search', inputSchema={
    'type': 'object', 'required': ['query'], 'additionalProperties': False,
    'properties': {'query': {'type': 'string', 'description': 'What to look for.'}}},
    description='Look up your saved memories when the automatic recall did not bring up what you need.')

DESCRIPTION = ('Memory tools are separate from file access. Use hold to save lasting experience and '
               'breath_search to find a known memory; use the listed Ombre tools for edits, reflection, '
               'letters, anchors, plans and library status. Every hidden memory is blocked by the program. ')
MEMORY_HISTORY = SimpleNamespace(name='memory_history', inputSchema={
    'type': 'object', 'required': ['memory_id'], 'additionalProperties': False,
    'properties': {'memory_id': {'type': 'string', 'description':
        'Stable memory ID from breath_search.'}}},
    description='Read the exact previous versions VEIL saved before trace edits or hold(target_id) additions. '
                'Hidden memories are unavailable.' )


def trace_tool_for_model(tool):
    """Keep Ombre's schema while adding VEIL-only history context."""
    schema = json.loads(json.dumps(tool.inputSchema))
    schema.setdefault('properties', {})['edit_reason'] = {
        'type': 'string',
        'description': 'Optional: why you are making this change now. Saved only in VEIL history; not sent to Ombre.',
    }
    description = (tool.description.rstrip() + '\n\n'
        'VEIL fields: content changes the memory body; meaning_append/meaning_replace change its meaning metadata. '
        'why_remembered says why the memory matters, while edit_reason says why you are changing it now. '
        'Use edit_reason for the change history; it is stored locally and not sent to Ombre.')
    return SimpleNamespace(name=tool.name, inputSchema=schema, description=description)


class NativeMemoryTools:
    def __init__(self, memory, max_result_bytes=8192):
        self.memory, self.max_result_bytes = memory, max_result_bytes
        self.search_results = {}
        available = {tool.name: tool for tool in memory.backend.list_tools()} \
            if hasattr(memory.backend, 'list_tools') else {}
        missing = set(OMBRE_TOOLS) - set(available) if hasattr(memory.backend, 'list_tools') else set()
        if missing:
            from veil_agent import UserError
            raise UserError('Ombre 缺少本工单要求的工具：' + '、'.join(sorted(missing)))
        self.ombre_tools = {name: available[name] for name in OMBRE_TOOLS if name in available}
        model_tools = [trace_tool_for_model(tool) if name == 'trace' else tool
                       for name, tool in self.ombre_tools.items()]
        self.tools = [HOLD, SEARCH, *model_tools, MEMORY_HISTORY]
        self.description = DESCRIPTION
        self.autonomy_mode = False

    def bind_capabilities(self, files):
        path = self.memory.store.root / self.memory.cfg.id / 'capabilities.json'
        try:
            row = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            current = sorted(tool.name for tool in self.tools)
            previous = row.get('memory_tools')
            if previous != current:
                names = ', '.join(name for name in current if name in self.ombre_tools or name in ('hold', 'breath_search'))
                event = (f'{self.memory.cfg.name} 新增记忆能力：' + names +
                         '。trace 删除必须先经页面审批；自主活动中的删除会进入待删除栏目。')
                old = row.get('pending_event')
                if not isinstance(old, str) or event not in old:
                    row['pending_event'] = (old + '\n' + event).strip() if old else event
                row['memory_tools'] = current
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(path)
            if hasattr(files, 'capability_event'):
                files.capability_event = row.get('pending_event')
        except Exception as exc:
            from veil_agent import UserError
            raise UserError('记忆能力清单无法安全保存，记忆工具已停止。') from exc

    def handles(self, name):
        return name in NATIVE_TOOLS

    def preview_summary(self, name, args):
        title = args.get('title') if isinstance(args, dict) else None
        target_id = args.get('target_id') if isinstance(args, dict) else None
        if isinstance(target_id, str):
            return self.memory.title_for(target_id) or target_id
        return str(title)[:40] if isinstance(title, str) and title else name

    def invoke(self, name, args, approval=None):
        try:
            if name == 'hold':
                return self._hold(args)
            if name == 'breath_search':
                return self._search(args)
            if name == 'memory_history':
                return self._history(args)
            return self._ombre(name, args, approval)
        except MemoryToolError as exc:
            return str(exc), '出错', self.preview_summary(name, args)
        except Exception:
            return '记忆工具出错，没有完成。', '出错', self.preview_summary(name, args)

    def _history(self, args):
        memory_id = args.get('memory_id')
        summary = self.memory.title_for(memory_id) if isinstance(memory_id, str) else None
        if not summary or self.memory.is_hidden(memory_id):
            return '程序拒绝：这条记忆不存在或已隐藏。', '被拒绝', str(memory_id or 'memory_history')
        rows = [row for row in self.memory.store.read(f'{self.memory.cfg.id}/memory-versions.jsonl')
                if row.get('memory_id') == memory_id]
        if not rows:
            return '这条记忆还没有由你改动过的旧版本。', '成功', f'历史：{summary}'
        rendered = '\n\n---\n\n'.join(
            f"时间：{row['time']}\n改动字段：{', '.join(row['fields']) or '未记录'}\n"
            f"本次操作原因：{row.get('operation_reason') or '未提供'}\n"
            f"记忆缘由（why_remembered）：{row.get('why_remembered') or '未提供'}\n"
            + (f"旧版未区分类型的原因字段：{row['reason']}\n" if row.get('reason') else '')
            + f"改动前的完整记忆：\n{row['old_content']}"
            for row in rows)
        return self._bounded(rendered), '成功', f'历史：{summary}（{len(rows)} 个旧版本）'

    def _bounded(self, result):
        raw = result.encode('utf-8')
        if len(raw) <= self.max_result_bytes:
            return result
        return raw[:self.max_result_bytes].decode('utf-8', errors='ignore') + '\n[已截断]'

    def _ombre(self, name, args, approval):
        summary = self.preview_summary(name, args)
        if name not in OMBRE_TOOLS or name not in self.ombre_tools:
            return '程序拒绝：此工具没有授权。', '被拒绝', summary
        if not isinstance(args, dict):
            return '程序拒绝：工具参数必须是对象。', '被拒绝', summary
        if name == 'trace':
            return self._trace(args, approval)
        if name in ('anchor', 'release'):
            memory_id = args.get('bucket_id')
            if not isinstance(memory_id, str) or self.memory.is_hidden(memory_id):
                return '程序拒绝：目标记忆不存在或已隐藏。', '被拒绝', summary
        if name == 'plan':
            related = args.get('related_bucket')
            if related and self.memory.is_hidden(related):
                return '程序拒绝：关联目标记忆已隐藏。', '被拒绝', summary
        try:
            result = self.memory.call(name, args)
        except MemoryToolError as exc:
            return str(exc), '出错', summary
        except Exception:
            return '记忆工具出错，没有完成。', '出错', summary
        if name in ('dream', 'pulse'):
            result = self._filter_hidden_result(result, name)
        return self._bounded(result), '成功', summary

    def _trace(self, args, approval):
        memory_id = args.get('bucket_id')
        summary = self.memory.title_for(memory_id) if isinstance(memory_id, str) else None
        banned = ('hard_delete', 'deletion_request_id', 'deletion_decision')
        if any(args.get(key) not in (None, False, '', [], {}) for key in banned):
            return '程序拒绝：这类删除参数不开放。', '被拒绝', str(summary or 'trace')
        if not summary or self.memory.is_hidden(memory_id):
            return '程序拒绝：目标记忆不存在或已隐藏。', '被拒绝', str(memory_id or 'trace')
        delete = args.get('delete') is True
        if args.get('delete') not in (None, False, True):
            return '程序拒绝：delete 必须是布尔值。', '被拒绝', summary
        edit_reason = args.get('edit_reason', '')
        if not isinstance(edit_reason, str) or len(edit_reason) > WHY_LIMIT:
            return f'程序拒绝：edit_reason 必须是不超过 {WHY_LIMIT} 字的文字。', '被拒绝', summary
        reason = args.get('delete_reason', '')
        if delete and (not isinstance(reason, str) or not reason.strip() or len(reason) > WHY_LIMIT):
            return f'程序拒绝：删除申请必须提供不超过 {WHY_LIMIT} 字的理由。', '被拒绝', summary
        old = self.memory.raw_content_for(memory_id)
        if old is None:
            return '程序拒绝：无法读取目标记忆原文，未做修改。', '被拒绝', summary
        if delete and self.autonomy_mode:
            return self._queue_delete(memory_id, summary, args, old)
        if delete and (approval is None or approval(
                f"{self.memory.cfg.name} 想删除记忆 #{memory_id}：{summary}\n理由：{str(args.get('delete_reason') or '未提供')[:WHY_LIMIT]}。同意吗？y/N ").strip().casefold() != 'y'):
            return 'lan 没有同意；这条记忆仍保留。', '被拒绝', summary
        if not self._save_version(memory_id, args, old):
            return '程序拒绝：旧版本未能安全保存，本次修改未执行。', '出错', summary
        if delete:
            failure = self._delete(memory_id, summary)
            if failure:
                return failure, '出错', summary
            return '已获 lan 人工批准并完成删除；旧版本已留档。', '成功', f'{summary}（旧版本已留档）'
        try:
            safe_args = {key: value for key, value in args.items()
                         if key not in (*banned, 'edit_reason')}
            result = self.memory.call('trace', safe_args)
        except MemoryToolError as exc:
            return f'{exc} 修改前旧版本已留档。', '出错', summary
        except Exception:
            return '记忆修改失败；旧版本已留档。', '出错', summary
        self.memory.bucket_details_cache.pop(memory_id, None)
        updated = self.memory.content_for(memory_id, refresh=True)
        if updated is not None:
            self.memory.record_native_update(memory_id, updated)
        feedback = (f'{result}\n修改前完整版本（含元数据）已留档；本次操作原因：'
                    f'{edit_reason.strip() or "未提供"}。正文 content 与含义元数据 meaning_append/meaning_replace 是不同字段。')
        return self._bounded(feedback), '成功', f'{summary}（旧版本已留档）'

    def _save_version(self, memory_id, args, old, *, operation_reason=None):
        try:
            fields = sorted(key for key in args if key not in {'bucket_id', 'delete_reason', 'edit_reason'})
            reason = operation_reason
            if reason is None:
                reason = args.get('edit_reason') or args.get('delete_reason') or ''
            self.memory.store.append(f'{self.memory.cfg.id}/memory-versions.jsonl', {
                'memory_id': memory_id, 'fields': fields,
                'operation_reason': str(reason)[:WHY_LIMIT],
                'why_remembered': str(args.get('why_remembered') or '')[:WHY_LIMIT],
                'old_content': old,
            })
            return True
        except Exception:
            return False

    def _queue_delete(self, memory_id, summary, args, old):
        reason = args.get('delete_reason', '')
        if not isinstance(reason, str) or len(reason) > WHY_LIMIT:
            return '程序拒绝：delete_reason 必须是 500 字以内的文字。', '被拒绝', summary
        if not self._save_version(memory_id, args, old):
            return '程序拒绝：旧版本未能安全保存，删除申请未创建。', '出错', summary
        request_id = uuid.uuid4().hex
        tags = self.memory.tags_for(memory_id)
        if '待删除' not in tags:
            tags.append('待删除')
        path = f'{self.memory.cfg.id}/delete-requests.jsonl'
        request = {'request_id': request_id, 'memory_id': memory_id, 'title': summary,
                   'reason': reason.strip(), 'status': 'pending'}
        try:
            self.memory.store.append(path, request)
            self.memory.call('trace', {'bucket_id': memory_id, 'tags': ','.join(tags) or ' '})
        except Exception:
            try:
                self.memory.store.append(path, {**request, 'status': 'failed'})
            except Exception:
                pass
            return '待删除申请未能安全保存；记忆没有被删除，请稍后重试。', '出错', summary
        self.memory.messages.put(f'待处理的删除申请：记忆 #{memory_id}「{summary}」，理由：{reason or "未提供"}。')
        return '已放进待删除栏目，等 lan 回来处理。', '成功', summary

    def process_pending_deletions(self, approval):
        path = f'{self.memory.cfg.id}/delete-requests.jsonl'
        latest = {}
        for row in self.memory.store.read(path):
            latest[row.get('request_id')] = row
        notes = []
        for row in latest.values():
            if row.get('status') != 'pending':
                continue
            memory_id, title = row.get('memory_id'), row.get('title')
            if not isinstance(memory_id, str):
                self.memory.store.append(path, {**row, 'status': 'rejected',
                                                'decision_reason': '请求记录无效'})
                continue
            if self.memory.deleted_for(memory_id):
                note = f'记忆 #{memory_id}「{title}」已在记忆面板处理；没有重复删除。'
                self.memory.store.append(path, {**row, 'status': 'approved_external'})
                self.memory.messages.put(note)
                notes.append(note)
                continue
            if self.memory.is_hidden(memory_id):
                tags = [tag for tag in self.memory.tags_for(memory_id) if tag != '待删除']
                old = self.memory.raw_content_for(memory_id)
                if old is not None and '待删除' in self.memory.tags_for(memory_id):
                    if not self._save_version(memory_id, {'tags': '待删除 removed'}, old):
                        continue
                    self.memory.call('trace', {'bucket_id': memory_id, 'tags': ','.join(tags) or ' '})
                self.memory.store.append(path, {**row, 'status': 'rejected',
                                                'decision_reason': '目标记忆已隐藏'})
                continue
            if '待删除' not in self.memory.tags_for(memory_id):
                note = f'记忆 #{memory_id}「{title}」的待删除标记已移除；申请已结束。'
                self.memory.store.append(path, {**row, 'status': 'rejected',
                                                'decision_reason': '待删除标记已移除'})
                self.memory.messages.put(note)
                notes.append(note)
                continue
            prompt = (f"{self.memory.cfg.name} 的自主删除申请：记忆 #{memory_id}「{title}」\n"
                      f"理由：{row.get('reason') or '未提供'}。同意删除吗？y/N ")
            accepted = approval is not None and approval(prompt).strip().casefold() == 'y'
            old = self.memory.raw_content_for(memory_id)
            decision_reason = f'自主删除申请：{"同意" if accepted else "拒绝"}'
            if old is None or not self._save_version(
                    memory_id, {'delete': accepted}, old, operation_reason=decision_reason):
                self.memory.messages.put(f'删除申请 #{memory_id} 未处理：旧版本无法安全留档。')
                continue
            if accepted:
                failure = self._delete(memory_id, title)
                if failure:
                    self.memory.messages.put(f'删除申请 #{memory_id} 未完成：{failure}')
                    continue
                status = 'approved'
                note = (f'已同意并删除自主申请的记忆 #{memory_id}「{title}」。'
                        f' {self.memory.cfg.name} 的理由：{row.get("reason") or "未提供"}')
            else:
                tags = [tag for tag in self.memory.tags_for(memory_id) if tag != '待删除']
                self.memory.call('trace', {'bucket_id': memory_id, 'tags': ','.join(tags) or ' '})
                status = 'rejected'
                note = (f'未同意删除记忆 #{memory_id}「{title}」；已移除待删除标记。'
                        f' {self.memory.cfg.name} 的理由：{row.get("reason") or "未提供"}')
            self.memory.store.append(path, {**row, 'status': status})
            self.memory.messages.put(note)
            notes.append(note)
        return notes

    def _delete(self, memory_id, title):
        entry = self.memory.state['entries'].setdefault(memory_id, {'title': title})
        entry.update(hidden=True, deleted=True, delete_pending=True)
        self.memory.persist()
        try:
            self.memory.call('trace', {'bucket_id': memory_id, 'delete': True})
        except Exception:
            return 'Ombre 删除未确认；这条已隐藏，可通过 /delete 重试。'
        entry.pop('delete_pending', None)
        self.memory.persist()
        return None

    def _filter_hidden_result(self, result, name):
        hidden = self.memory.hidden_ids()
        if not hidden:
            return result
        hidden_titles = {title for key in hidden if (title := self.memory.title_for(key))}
        # Ombre emits each dream record as a separate block and each pulse entry
        # as a line containing its stable ID. Remove those records and aggregate
        # counts before the result reaches the chat model.
        pieces = result.split('\n---\n')
        if name == 'dream':
            first, *blocks = pieces
            first_id = re.search(r'(?m)^ID: ([A-Za-z0-9_-]+)\s*$', first)
            visible_count = 0
            if first_id:
                title_hidden = any(title in first for title in hidden_titles)
                id_hidden = first_id.group(1) in hidden
                if id_hidden or title_hidden:
                    separator = first.rfind('\n\n', 0, first_id.start())
                    first = first[:separator] if separator >= 0 else first[:first_id.start()]
                else:
                    visible_count += 1
            visible = []
            for piece in blocks:
                match = re.search(r'(?m)^ID: ([A-Za-z0-9_-]+)\s*$', piece)
                if (match and match.group(1) in hidden) or any(title in piece for title in hidden_titles):
                    continue
                visible.append(piece)
                if match:
                    visible_count += 1
            first = re.sub(r'（\d+ 个桶）', f'（{visible_count} 个桶）', first, count=1)
            result = '\n---\n'.join([first, *visible])
        lines = result.splitlines()
        if name == 'pulse':
            aggregate = ('固化桶:', '动态桶:', '归档桶:', 'feel 桶:', 'plan 桶:', 'letter 桶:',
                         '总占用:', '向量索引队列:', '⚠️ 索引漂移:')
            lines = [line for line in lines if not line.startswith(aggregate)
                     and not re.match(r'^=== (计划|feel|信件)（\d+', line)]
        result = '\n'.join(line for line in lines if not any(key in line for key in hidden)
                            and not any(title in line for title in hidden_titles))
        if name == 'pulse':
            result += '\n\n（包含隐藏记忆的统计与条目已隐藏。）'
        return result

    def _hold(self, args):
        summary = self.preview_summary('hold', args)
        content, title = args.get('content'), args.get('title')
        target_id = args.get('target_id')
        why = args.get('why_remembered', '')
        if (not isinstance(content, str) or not content.strip() or not isinstance(why, str)
                or (target_id is None and (not isinstance(title, str) or not title.strip()))):
            return '程序拒绝：新建记忆需提供 content 和 title，更新需提供 content。', '被拒绝', summary
        if target_id is None and args.get('merge_ids'):
            return '程序拒绝：只有指定 target_id 时才能整合检索到的记忆。', '被拒绝', summary
        if (len(content) > CONTENT_LIMIT or len(why) > WHY_LIMIT
                or (isinstance(title, str) and len(title) > TITLE_LIMIT)):
            return f'程序拒绝：太长（正文最多 {CONTENT_LIMIT} 字）。请写成更短的一条再存。', '被拒绝', summary
        if target_id is not None:
            if not isinstance(target_id, str) or target_id not in self.search_results:
                return '程序拒绝：target_id 必须来自本轮 breath_search 的可见结果。', '被拒绝', summary
            previous = self.search_results[target_id]
            if not self.memory.visible([(target_id, previous)]):
                return '程序拒绝：这条记忆已隐藏或不可用，未做更新。', '被拒绝', summary
            merge_ids = args.get('merge_ids', [])
            if (not isinstance(merge_ids, list) or any(not isinstance(key, str) for key in merge_ids)
                    or len(set(merge_ids)) != len(merge_ids)):
                return '程序拒绝：merge_ids 必须是本轮检索结果中的不重复编号列表。', '被拒绝', summary
            source_ids = [key for key in merge_ids if key != target_id]
            if any(key not in self.search_results for key in source_ids):
                return '程序拒绝：每个 merge_id 都必须来自本轮 breath_search 的结果。', '被拒绝', summary
            if (source_ids
                    and not self.memory.visible([(key, self.search_results[key]) for key in source_ids])):
                return '程序拒绝：有待整合的记忆已隐藏或不可用，未做更新。', '被拒绝', summary
            current = self.memory.content_for(target_id, refresh=True)
            if current != previous:
                self.search_results = {}
                return '程序拒绝：目标记忆在检索后发生变化，请重新 breath_search；本次未更新。', '被拒绝', summary
            for source_id in source_ids:
                if self.memory.content_for(source_id, refresh=True) != self.search_results[source_id]:
                    self.search_results = {}
                    return '程序拒绝：来源记忆在检索后发生变化，请重新 breath_search；本次未更新。', '被拒绝', summary
            addition = content.strip()
            if why.strip() and why.strip() not in addition:
                addition = f'{addition}\n补充原因：{why.strip()}'
            if not addition:
                return '程序拒绝：补充内容不能为空。', '被拒绝', summary
            merged = previous
            changed = False
            for source_id in source_ids:
                source = self.search_results[source_id]
                if source not in merged:
                    merged = f'{merged}\n\n---\n{source}'
                    changed = True
            if addition not in merged:
                merged = f'{merged}\n\n补充：{addition}'
                changed = True
            if not changed:
                return '这段内容已在该记忆中，没有重复写入。', '成功', f'已存在：{summary}'
            updated = merged
            if len(updated.encode('utf-8')) > MERGED_MEMORY_LIMIT_BYTES:
                return '程序拒绝：合并后超过 Ombre 单条记忆安全上限；原记忆未改动。', '被拒绝', summary
            old_document = self.memory.raw_content_for(target_id)
            if old_document is None:
                return '程序拒绝：无法读取目标记忆的完整原文，未做追加。', '被拒绝', summary
            if not self._save_version(target_id, {'content': addition, 'why_remembered': why},
                                      old_document, operation_reason='hold(target_id) 追加'):
                return '程序拒绝：修改前的完整旧版本无法安全留档，未做追加。', '出错', summary
            text = self.memory.call('trace', {'bucket_id': target_id, 'content': updated})
            stored = self.memory.content_for(target_id, refresh=True)
            preserved = [previous, *(self.search_results[key] for key in source_ids), addition]
            if stored is None or any(part and part not in stored for part in preserved):
                self.search_results = {}
                return '没有核对到完整保存结果；请检查这条记忆后再继续。', '出错', summary
            self.memory.record_native_update(target_id, stored)
            self.search_results[target_id] = stored
            merged_note = f'，并整合 {len(source_ids)} 条相关记忆' if source_ids else ''
            return ('已追加到这条记忆；原文和标题均保留，改动前完整版本（含元数据）已留档。',
                    '成功', f'更新：{summary}（{target_id}）{merged_note}')
        ombre_args = {'content': content.strip(), 'title': title.strip()}
        if why.strip():
            ombre_args['why_remembered'] = why.strip()
        text = self.memory.call('hold', ombre_args)
        try:
            kind = self.memory.record_native(ombre_args, text)
        except ValueError:
            return '没有确认保存成功，请不要说已经记住。', '出错', summary
        return ('已合并进已有记忆。' if kind == '合并' else '已保存。'), '成功', f'{kind}：{summary}'

    def _search(self, args):
        query = args.get('query')
        if not isinstance(query, str) or not query.strip():
            return '程序拒绝：query 必须是非空文字。', '被拒绝', 'breath_search'
        text = self.memory.call('breath_search', {
            'query': query.strip(), 'mode': 'automatic', 'with_ids': True, 'max_results': 3})
        found = self.memory.visible(ordinary_hits(text))
        self.search_results = {}
        rendered = []
        for key, body in found:
            title = self.memory.title_for(key) or '标题未登记'
            exact_content = self.memory.content_for(key)
            if exact_content is not None:
                self.search_results[key] = exact_content
                rendered.append(f'[记忆编号: {key} | 标题: {title}]\n{exact_content}')
            else:
                display = body.removeprefix(f'[bucket_id:{key}]').strip()
                rendered.append(f'[记忆编号: {key} | 标题: {title} | 本地原文未定位，不能更新]\n{display}')
        result = '\n---\n'.join(rendered) or '没有找到相关记忆。'
        raw = result.encode('utf-8')
        if len(raw) > self.max_result_bytes:
            result = raw[:self.max_result_bytes].decode('utf-8', errors='ignore') + '\n[已截断]'
        return result, '成功', query.strip()[:40]


class ToolSet:
    """Files (006) and memory (007) behind the one interface Chat already uses."""
    def __init__(self, files, native):
        self.files, self.native = files, native
        self.native.bind_capabilities(files)

    @property
    def tools(self):
        return [*self.files.tools, *self.native.tools]

    @property
    def description(self):
        return self.files.description + self.native.description

    @property
    def capability_event(self):
        return self.files.capability_event

    @property
    def startup_notice(self):
        return getattr(self.files, 'startup_notice', None)

    def consume_capability_event(self):
        consume = getattr(self.files, 'consume_capability_event', None)
        if consume:
            consume()

    def set_autonomy(self, enabled):
        self.native.autonomy_mode = bool(enabled)

    def announce_autonomy(self):
        self.files.announce_autonomy()

    def announce_long_tasks(self):
        announce = getattr(self.files, 'announce_long_tasks', None)
        if announce:
            announce()

    def invoke(self, name, args, approval=input):
        if self.native.handles(name):
            return self.native.invoke(name, args, approval)
        return self.files.invoke(name, args, approval)

    def preview_summary(self, name, args):
        if self.native.handles(name):
            return self.native.preview_summary(name, args)
        return self.files.preview_summary(name, args)


