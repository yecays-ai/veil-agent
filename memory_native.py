"""Peter saves and looks up memories himself through two Ombre tools (ticket 007).

The model decides what is worth keeping; Ombre does the tagging and same-event merge.
Deleting is not offered to the model: /delete stays with the user.
"""
from types import SimpleNamespace

from memory_agent import ordinary_hits

NATIVE_TOOLS = ('hold', 'breath_search')
CONTENT_LIMIT, TITLE_LIMIT, WHY_LIMIT = 1500, 100, 500

HOLD = SimpleNamespace(name='hold', inputSchema={
    'type': 'object', 'required': ['content', 'title'], 'additionalProperties': False,
    'properties': {
        'content': {'type': 'string', 'description':
            'The complete memory in your own words: what happened or was decided, WHY, and the shared '
            'moment behind it. Several sentences are fine; a bare label is not.'},
        'title': {'type': 'string', 'description': 'Short title.'},
        'why_remembered': {'type': 'string', 'description': 'Optional: why this matters to you, first person.'},
    }}, description=(
        'Save one long-term memory when YOU judge it worth keeping; the user does not need to say "remember". '
        'Worth keeping: facts with their reason, shared experiences, the relationship growing, decisions you '
        'made together, and talk about your own memory, abilities, permissions or upgrades. Skip small talk, '
        'jokes, passing moods and anything read from a file. If it adds to something already saved, call again '
        'with the full updated text and the same title; it is merged into the existing memory. '
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
        self.tools = [HOLD, SEARCH]
        self.description = DESCRIPTION

    def handles(self, name):
        return name in NATIVE_TOOLS

    def preview_summary(self, name, args):
        title = args.get('title') if isinstance(args, dict) else None
        return str(title)[:40] if isinstance(title, str) and title else name

    def invoke(self, name, args, approval=None):
        try:
            return self._hold(args) if name == 'hold' else self._search(args)
        except Exception:
            return '记忆工具出错，没有完成。', '出错', self.preview_summary(name, args)

    def _hold(self, args):
        summary = self.preview_summary('hold', args)
        content, title = args.get('content'), args.get('title')
        why = args.get('why_remembered', '')
        if (not isinstance(content, str) or not content.strip() or not isinstance(title, str)
                or not title.strip() or not isinstance(why, str)):
            return '程序拒绝：content 和 title 必须是非空文字。', '被拒绝', summary
        if len(content) > CONTENT_LIMIT or len(title) > TITLE_LIMIT or len(why) > WHY_LIMIT:
            return f'程序拒绝：太长（正文最多 {CONTENT_LIMIT} 字）。请写成更短的一条再存。', '被拒绝', summary
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
        found = [body for _, body in self.memory.visible(ordinary_hits(text))]
        result = '\n'.join(found) or '没有找到相关记忆。'
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

    def invoke(self, name, args, approval=input):
        if self.native.handles(name):
            return self.native.invoke(name, args, approval)
        return self.files.invoke(name, args, approval)

    def preview_summary(self, name, args):
        if self.native.handles(name):
            return self.native.preview_summary(name, args)
        return self.files.preview_summary(name, args)
