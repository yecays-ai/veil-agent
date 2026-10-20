"""Peter saves and looks up memories himself through two Ombre tools (ticket 007).

The model decides what is worth keeping; Ombre does the tagging and same-event merge.
Deleting is not offered to the model: /delete stays with the user.
"""
from types import SimpleNamespace

from memory_agent import ordinary_hits

NATIVE_TOOLS = ('hold', 'breath_search')
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

DESCRIPTION = ('Memory tools, separate from the file tools and always available: you keep long-term memory '
               'yourself, calling hold to save what is worth keeping and breath_search to look something up. ')


class NativeMemoryTools:
    def __init__(self, memory, max_result_bytes=8192):
        self.memory, self.max_result_bytes = memory, max_result_bytes
        self.search_results = {}
        self.tools = [HOLD, SEARCH]
        self.description = DESCRIPTION

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
            return self._hold(args) if name == 'hold' else self._search(args)
        except Exception:
            return '记忆工具出错，没有完成。', '出错', self.preview_summary(name, args)

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
            text = self.memory.call('trace', {'bucket_id': target_id, 'content': updated})
            stored = self.memory.content_for(target_id, refresh=True)
            preserved = [previous, *(self.search_results[key] for key in source_ids), addition]
            if stored is None or any(part and part not in stored for part in preserved):
                self.search_results = {}
                return '没有核对到完整保存结果；请检查这条记忆后再继续。', '出错', summary
            self.memory.record_native_update(target_id, stored)
            self.search_results[target_id] = stored
            merged_note = f'，并整合 {len(source_ids)} 条相关记忆' if source_ids else ''
            return '已追加到这条记忆；原文和标题均保留。', '成功', f'更新：{summary}（{target_id}）{merged_note}'
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
        self.files.consume_capability_event()

    def announce_autonomy(self):
        self.files.announce_autonomy()

    def invoke(self, name, args, approval=input):
        if self.native.handles(name):
            return self.native.invoke(name, args, approval)
        return self.files.invoke(name, args, approval)

    def preview_summary(self, name, args):
        if self.native.handles(name):
            return self.native.preview_summary(name, args)
        return self.files.preview_summary(name, args)

