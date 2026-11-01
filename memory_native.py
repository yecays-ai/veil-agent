"""Native Ombre memory tools exposed through the shared chat tool loop.

The model decides what is worth keeping; Ombre does the tagging and same-event merge.
Deleting is not offered to the model: /delete stays with the user.
"""
from types import SimpleNamespace
import inspect
import json
from datetime import datetime, timezone
import hashlib
import re
import uuid

from agent_types import UserError
from capability_state import capability_lock, save_capabilities
from memory_agent import ordinary_hits, MemoryToolError, vault_path as role_vault_path

OMBRE_TOOLS = ('trace', 'dream', 'letter_write', 'letter_read', 'letter_lock_update',
                'anchor', 'release', 'plan', 'pulse')
NATIVE_TOOLS = ('hold', 'breath_search', *OMBRE_TOOLS, 'memory_history', 'memory_catalog', 'memory_read')
CONTENT_LIMIT, TITLE_LIMIT, WHY_LIMIT = 1500, 100, 500
MERGED_MEMORY_LIMIT_BYTES = 48 * 1024
SURPRISE_TAGS = {'惊喜', 'surprise'}

HOLD = SimpleNamespace(name='hold', inputSchema={
    'type': 'object', 'required': ['content'], 'additionalProperties': False,
    'properties': {
        'content': {'type': 'string', 'description':
            'The complete memory in your own words: what happened or was decided, WHY, and the shared '
            'moment behind it. Several sentences are fine; a bare label is not.'},
        'title': {'type': 'string', 'description':
            'Short title, required for a new memory; existing title is kept when target_id is set.'},
        'target_id': {'type': 'string', 'description':
            'Optional stable ID of a memory you were shown (automatic recall or breath_search) when adding details to it. '
            'When set, content is treated as an addition and appended to that exact memory; '
            'the existing wording and title are preserved.'},
        'merge_ids': {'type': 'array', 'items': {'type': 'string'}, 'description':
            'Optional other IDs of shown memories that describe this same event. '
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


MEMORY_CATALOG = SimpleNamespace(name='memory_catalog', description=(
    'List current visible memory IDs and titles in stable ID order, five per page. '
    'Pass next_after as after to continue; an empty next_after means done. '
    'This reads local metadata without embeddings or a model. Use memory_read for current text; '
    'memory_history contains only older versions, and dream has no historical offset.'), inputSchema={
        'type': 'object', 'properties': {'after': {'type': 'string'}}, 'additionalProperties': False})
MEMORY_READ = SimpleNamespace(name='memory_read', description=(
    'Read an exact slice of a current visible memory, including its metadata. '
    'Pass its memory_id from memory_catalog and next_offset as offset until next_offset is null. '
    'Offsets count Unicode characters; pass the returned version on subsequent pages; restart if changed. '
    'This is read only and does not call embeddings or a model.'), inputSchema={
        'type': 'object', 'required': ['memory_id'], 'additionalProperties': False,
        'properties': {'memory_id': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}, 'version': {'type': 'string'}}})


# Ticket 145: Ombre's own docstrings are long Chinese text about its internals and test
# helpers. Peter gets the same schema with short English descriptions of what VEIL allows.
OMBRE_DESCRIPTIONS = {
    'trace': (
        'Edit one existing memory by its exact bucket_id; never guess an ID. Pass only the fields you change '
        '(-1 or empty means unchanged). content replaces the whole body; or old_str/new_str replaces one exact, '
        'unique passage (empty new_str removes it); not both. title is the memory\'s (or letter\'s) own title; '
        'name is only its bucket name. resolved=1 lets it settle, 0 reactivates it; pinned=1 makes it a permanent '
        'core memory; protected=1 stops decay without forcing it up; digested=1 hides it from passive recall and '
        'dream; dont_surface=1 keeps it out of daily recall. Removing the last pinned/protected needs importance '
        '1-10 in the same call. Metadata: importance 1-10, domain and tags comma-separated, valence/arousal 0-1, '
        'why_remembered, meaning_append/meaning_replace, media_append/media_replace (replace drops the old value; '
        'prefer append). status (active/resolved/abandoned) and weight belong to plan items. '
        'Each of these goes in its own call: reinforce=true when, after reading it, this memory truly matters '
        '(search hits do not reinforce); quotes_replace corrects or removes its original quotes (never adds); '
        'unlink=<id> removes a wrong link; relink=<id> with relation_type (caused_by, causes, continuation_of, '
        'continues, related_to, same_event) fixes an existing link. restore=true brings back an archived memory. '
        'delete=true with delete_reason asks Lan; during your own autonomous activity it becomes a request she '
        'answers on the memory page. edit_reason says why you change it now (kept in VEIL history only).'),
    'dream': (
        'Read every memory that changed in the last window_hours (default 48), in full, to look back and digest. '
        'What you have let go of can settle with trace(resolved=1). Active plans are listed at the end.'),
    'letter_write': (
        'Write a letter to Lan or to your future self. Letters are kept word for word forever, never compressed, '
        'merged or faded; put words meant to last here rather than in a memory. author: "ai" for you, "user" for '
        'Lan. title, date and user_name are optional. lock_type: "none" (default), "timed" with a future '
        'unlock_date (2027-01-01 or a full ISO time) opens on that date, "permanent" seals it unread forever. '
        'Only the writer can change a lock later with letter_lock_update, and only if ai_name (your name) was '
        'given when writing.'),
    'letter_read': (
        'Find letters: optional query (meaning search), author ("ai", "user" or a signature), date_from/date_to '
        '(ISO dates). Without query, the newest limit letters. Returns full text.'),
    'letter_lock_update': (
        'Change only the lock of an existing letter you wrote; letter_id is the bracketed ID from letter_read. '
        'lock_type "none", "timed" (with a future unlock_date) or "permanent". Lan\'s letters are not yours to lock.'),
    'anchor': (
        'Mark a memory as an anchor: a cold reference that stays out of default recall but still answers matching '
        'searches. At most 24; release one first when full.'),
    'release': 'Remove the anchor mark from a memory; it returns to default recall. pinned stays as it was.',
    'plan': (
        'Record a to-do, promise or open loop. status active (default), resolved or abandoned; related_bucket '
        'optionally links a memory; weight 0-1 is how heavy the promise is (not how important); why_remembered is '
        'optional. Plans never fade and appear only at the end of dream; close one with '
        'trace(bucket_id, status="resolved").'),
    'pulse': (
        'Memory library status: counts, storage, decay engine state and a one-line summary of every memory; '
        'anchors are marked. include_archive=true also lists archived ones.'),
}
DREAM_NOTE = ('Your recent memories, for looking back. Anything you are done with can settle with '
              'trace(bucket_id, resolved=1); nothing needs to be written.')


def neutral_dream_header(result):
    """Ticket 147: replace Ombre's reflection questions above the first memory with a plain note.

    Ombre asks what still weighs on him and suggests hold(feel=True), which VEIL does not offer."""
    if not isinstance(result, str) or not result.startswith('=== Dreaming'):
        return result
    header, newline, rest = result.partition('\n')
    guidance, blank, memories = rest.partition('\n\n')
    if not blank or 'trace(' not in guidance or re.search(r'(?m)^ID: ', guidance):
        return result
    return header + newline + DREAM_NOTE + blank + memories


TRACE_HIDDEN_FIELDS = ('hard_delete', 'deletion_request_id', 'deletion_decision', 'deletion_ai_reason')


def ombre_tool_for_model(tool):
    """Keep Ombre's parameters; drop schema titles and fields VEIL refuses; describe briefly."""
    schema = json.loads(json.dumps(tool.inputSchema))
    schema.pop('title', None)
    properties = schema.setdefault('properties', {})
    for value in properties.values():
        if isinstance(value, dict):
            value.pop('title', None)
    if tool.name == 'trace':
        for name in TRACE_HIDDEN_FIELDS:
            properties.pop(name, None)
        properties['edit_reason'] = {
            'type': 'string',
            'description': 'Optional: why you are making this change now. Saved only in VEIL history; not sent to Ombre.',
        }
    description = OMBRE_DESCRIPTIONS.get(tool.name, tool.description)
    return SimpleNamespace(name=tool.name, inputSchema=schema, description=description)


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except ValueError:
            return value[1:-1]
    return value


def parse_frontmatter(raw):
    """Read the simple YAML Ombre writes: scalars, '- item' lists and wrapped scalars."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != '---':
        return {}, raw
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), None)
    if end is None:
        return {}, raw
    meta, key = {}, None
    for line in lines[1:end]:
        if not line.strip():
            continue
        if not line[:1].isspace() and not line.startswith('- '):
            key, _, value = line.partition(':')
            key = key.strip()
            meta[key] = _unquote(value) if value.strip() else []
        elif key is None:
            continue
        elif line.lstrip().startswith('- ') and isinstance(meta[key], list):
            meta[key].append(_unquote(line.lstrip()[2:]))
        elif isinstance(meta[key], str):
            # A long scalar wrapped onto indented continuation lines.
            meta[key] += ' ' + line.strip()
    for name, value in meta.items():
        if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
            meta[name] = [_unquote(item) for item in value[1:-1].split(',') if item.strip()]
        elif isinstance(value, str):
            meta[name] = _unquote(value)
    return meta, '\n'.join(lines[end + 1:])


def as_list(value):
    if isinstance(value, list):
        return [str(item) for item in value]
    return [value] if value else []


class NativeMemoryTools:
    def __init__(self, memory, max_result_bytes=8192):
        self.memory, self.max_result_bytes = memory, max_result_bytes
        self.search_results = {}
        available = {tool.name: tool for tool in memory.backend.list_tools()} \
            if hasattr(memory.backend, 'list_tools') else {}
        missing = set(OMBRE_TOOLS) - set(available) if hasattr(memory.backend, 'list_tools') else set()
        if missing:
            raise UserError('Ombre 缺少本工单要求的工具：' + '、'.join(sorted(missing)))
        self.ombre_tools = {name: available[name] for name in OMBRE_TOOLS if name in available}
        model_tools = [ombre_tool_for_model(tool) for tool in self.ombre_tools.values()]
        self.tools = [HOLD, SEARCH, *model_tools, MEMORY_HISTORY, MEMORY_CATALOG, MEMORY_READ]
        self.description = DESCRIPTION
        self.autonomy_mode = False

    def bind_capabilities(self, files):
        path = self.memory.store.root / self.memory.cfg.id / 'capabilities.json'
        with capability_lock(path):
            try:
                row = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
                current = sorted(tool.name for tool in self.tools)
                previous = row.get('memory_tools')
                if previous != current:
                    names = ', '.join(name for name in current if name in NATIVE_TOOLS)
                    event = ('Capability update: new memory tools are available: ' + names +
                             '. Deletion through trace requires explicit UI approval; requests made during '
                             'autonomous activity remain pending for later approval.')
                    old = row.get('pending_event')
                    if not isinstance(old, str) or event not in old:
                        row['pending_event'] = (old + '\n' + event).strip() if old else event
                    row['pending_event_model'] = row['pending_event']
                    updates = row.get('capability_updates')
                    updates = updates if isinstance(updates, list) else []
                    if not updates or not isinstance(updates[-1], dict) or updates[-1].get('text') != event:
                        row['capability_updates'] = [*updates[-20:], {
                            'at': datetime.now(timezone.utc).isoformat(), 'text': event}]
                    row['memory_tools'] = current
                    save_capabilities(path, row)
                if hasattr(files, 'capability_event'):
                    files.capability_event = row.get('pending_event')
                    files.capability_event_model = row.get('pending_event_model')
            except Exception as exc:
                raise UserError('The memory capability manifest could not be saved safely, so memory tools were stopped.') from exc

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
            if name == 'memory_catalog':
                return self._catalog(args)
            if name == 'memory_read':
                return self._read(args)
            if name == 'memory_history':
                return self._history(args)
            return self._ombre(name, args, approval)
        except MemoryToolError as exc:
            return str(exc), '出错', self.preview_summary(name, args)
        except Exception:
            return 'The memory tool failed. Check the current memory state before retrying.', '出错', self.preview_summary(name, args)

    def _visible_records(self):
        # Read only actual bucket folders, never letters, plans, sources or private files.
        vault_path = role_vault_path(self.memory.store.root, self.memory.cfg.id)
        hidden = self.memory.hidden_ids()
        from tools_agent import _redirected
        if not vault_path.exists():
            return {}
        if _redirected(vault_path):
            raise ValueError('redirected vault')
        vault = vault_path.resolve()
        records = {}
        for folder in ('dynamic', 'permanent', 'feel'):
            for path in (vault / folder).rglob('*.md'):
                if (not path.resolve().is_relative_to(vault) or _redirected(path)
                        or any(_redirected(parent) for parent in path.parents if parent.is_relative_to(vault))):
                    continue
                raw = path.read_text(encoding='utf-8')
                lines = raw.splitlines()
                if not lines or lines[0].strip() != '---':
                    continue
                end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), None)
                if end is None:
                    continue
                meta = {}
                for line in lines[1:end]:
                    key, sep, value = line.partition(':')
                    # Match Memory.refresh_visibility(): relation_links may contain
                    # nested YAML keys such as ``type`` which are not bucket metadata.
                    if sep and not line[:1].isspace():
                        meta[key.strip()] = self.memory._frontmatter_scalar(value)
                if meta.get('type', folder).casefold() not in ('dynamic', 'permanent', 'feel'):
                    continue
                if meta.get('source_tool', '').casefold() == 'letter' or '__letter__' in '\n'.join(lines[1:end]):
                    continue
                key = meta.get('id', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key) or key in hidden:
                    continue
                if meta.get('deleted_at', '').casefold() not in ('', 'null', 'none', 'false'):
                    continue
                if meta.get('dont_surface', '').casefold() not in ('', '0', 'false', 'null', 'none'):
                    continue
                if self.memory.state['entries'].get(key, {}).get('deleted'):
                    continue
                if key in records:
                    raise ValueError('ambiguous memory ID')
                records[key] = (meta.get('title') or meta.get('name') or key, raw)
        return records

    def panel_entries(self, recent_requests=100):
        """Readable memory list for Lan's memory page; never sent to the model."""
        recalled = {}
        try:
            rows = self.memory.store.read_tail(f'{self.memory.cfg.id}/context-log.jsonl', recent_requests)
        except Exception:
            rows = []
        for row in rows:
            for memory_id in row.get('memory_ids') or []:
                recalled[memory_id] = recalled.get(memory_id, 0) + 1
        entries = []
        for key, (title, raw) in self._visible_records().items():
            meta, body = parse_frontmatter(raw)
            tags = as_list(meta.get('tags'))
            # Peter's surprises for Lan stay folded; the page only reveals them on request.
            surprise = any(tag.strip().casefold() in SURPRISE_TAGS for tag in tags)
            entries.append({
                'memory_id': key, 'title': title, 'created': meta.get('created', ''),
                'kind': meta.get('type', ''), 'domain': as_list(meta.get('domain')),
                'tags': [] if surprise else tags, 'importance': meta.get('importance', ''),
                'why': '' if surprise else meta.get('why_remembered', ''),
                'body': '' if surprise else body.strip(), 'surprise': surprise,
                'source_record': title.strip() == '原文来源', 'recalled': recalled.get(key, 0)})
        entries.sort(key=lambda entry: entry['created'], reverse=True)
        return {'items': entries, 'recent_requests': len(rows)}

    def panel_edit(self, memory_id, changes):
        """Lan's edit from the memory page; uses the same archived trace path as Peter's edits."""
        fields = {key: value for key, value in changes.items() if key in ('title', 'content', 'why_remembered')}
        if (not isinstance(memory_id, str) or not fields or len(fields) != len(changes)
                or any(not isinstance(value, str) or not value.strip() for value in fields.values())):
            return False, '没有可保存的修改。'
        limits = {'title': TITLE_LIMIT, 'content': CONTENT_LIMIT, 'why_remembered': WHY_LIMIT}
        too_long = [key for key, value in fields.items() if len(value) > limits[key]]
        if too_long:
            names = {'title': '标题', 'content': '内容', 'why_remembered': '为什么记'}
            return False, '、'.join(names[key] for key in too_long) + '太长了，请删短一点再保存。'
        if memory_id not in self._visible_records():
            return False, '这条记忆不存在或已经删除。'
        args = {'bucket_id': memory_id, **fields, 'edit_reason': 'Lan edited this memory in the memory page.'}
        result, state, _ = self._trace(args, None)
        return state == '成功', '已保存，旧版本已留档。' if state == '成功' else result

    def panel_delete(self, memory_id):
        """Lan's delete from the memory page; same erase as /delete 编号 确认."""
        if not isinstance(memory_id, str) or memory_id not in self._visible_records():
            return False, '这条记忆不存在或已经删除。'
        title = self.memory.title_for(memory_id) or memory_id
        failure = self._delete(memory_id, title)
        return (False, '这次没能确认 Ombre 的删除；这条已经不会再被想起，稍后可以再删一次。') if failure \
            else (True, '已删除。')

    def _catalog(self, args):
        after = args.get('after', '')
        if not isinstance(after, str) or (after and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', after)):
            return 'The memory catalog cursor is invalid. Restart the listing without a cursor.', '被拒绝', 'memory_catalog'
        records = self._visible_records()
        keys = sorted(key for key in records if key > after)
        page = []
        for key in keys[:5]:
            candidate = page + [{'memory_id': key, 'title': records[key][0][:80]}]
            result = json.dumps({'items': candidate, 'next_after': key if len(keys) > len(candidate) else ''}, ensure_ascii=False)
            if len(result.encode('utf-8')) > self.max_result_bytes:
                break
            page = candidate
        if keys and not page:
            return 'The result limit is too small to return one complete catalog entry.', '出错', 'memory_catalog'
        cursor = page[-1]['memory_id'] if len(keys) > len(page) else ''
        return json.dumps({'items': page, 'next_after': cursor}, ensure_ascii=False), '成功', '记忆目录'

    def _read(self, args):
        key, offset = args.get('memory_id'), args.get('offset', 0)
        if not isinstance(key, str) or type(offset) is not int or offset < 0:
            return 'The memory ID or content offset is invalid.', '被拒绝', 'memory_read'
        record = self._visible_records().get(key)
        if record is None:
            return 'This memory does not exist or cannot be read.', '被拒绝', 'memory_read'
        title, raw = record
        version = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        if (offset and args.get('version') != version) or (args.get('version') and args['version'] != version):
            return 'The memory changed or its version is missing. Re-read it from offset=0, then retry.', '被拒绝', 'memory_read'
        if offset > len(raw):
            return 'The requested offset is beyond the current memory length. Re-read it from offset=0.', '被拒绝', 'memory_read'
        end = min(len(raw), offset + 1000)
        while True:
            if end == offset and offset < len(raw):
                return 'The result limit is too small to return any memory text.', '出错', 'memory_read'
            result = json.dumps({'memory_id': key, 'offset': offset, 'text': raw[offset:end],
                                 'next_offset': end if end < len(raw) else None, 'version': version}, ensure_ascii=False)
            if len(result.encode('utf-8')) <= self.max_result_bytes:
                return result, '成功', f'{title[:80]}（{key}）'
            if end <= offset:
                return 'The result limit is too small to return the requested memory content.', '出错', 'memory_read'
            end = offset + (end - offset) // 2

    def _history(self, args):
        memory_id = args.get('memory_id')
        # Do not authorize historical text from an older cached visibility result.
        # The current record is the authority, the same as automatic recall.
        if (not isinstance(memory_id, str)
                or self.memory.state['entries'].get(memory_id, {}).get('hidden')
                or memory_id in self.memory.state['sources'].values()
                or not self.memory.refresh_visibility(memory_id)):
            return 'This memory does not exist or is hidden.', '被拒绝', str(memory_id or 'memory_history')
        summary = self.memory.title_for(memory_id)
        if not summary:
            return 'This memory does not exist or is hidden.', '被拒绝', memory_id
        rows = [row for row in self.memory.store.read(f'{self.memory.cfg.id}/memory-versions.jsonl')
                if row.get('memory_id') == memory_id]
        if not rows:
            return 'This memory has no earlier versions edited by you.', '成功', f'历史：{summary}'
        rendered = '\n\n---\n\n'.join(
            f"Time: {row['time']}\nChanged fields: {', '.join(row['fields']) or 'not recorded'}\n"
            f"Reason for this operation: {row.get('operation_reason') or 'not provided'}\n"
            f"Why this memory matters (why_remembered): {row.get('why_remembered') or 'not provided'}\n"
            + (f"Legacy undifferentiated reason field: {row['reason']}\n" if row.get('reason') else '')
            + f"Complete memory before the change:\n{row['old_content']}"
            for row in rows)
        return self._bounded(rendered), '成功', f'历史：{summary}（{len(rows)} 个旧版本）'

    def _bounded(self, result):
        raw = result.encode('utf-8')
        if len(raw) <= self.max_result_bytes:
            return result
        return raw[:self.max_result_bytes].decode('utf-8', errors='ignore') + '\n[truncated]'

    def _ombre(self, name, args, approval):
        summary = self.preview_summary(name, args)
        if name not in OMBRE_TOOLS or name not in self.ombre_tools:
            return 'This tool is not authorized for the current role. Choose an available tool; permissions cannot be bypassed.', '被拒绝', summary
        if not isinstance(args, dict):
            return 'Tool arguments must be a JSON object.', '被拒绝', summary
        if name == 'trace':
            return self._trace(args, approval)
        if name in ('anchor', 'release'):
            memory_id = args.get('bucket_id')
            if not isinstance(memory_id, str) or self.memory.is_hidden(memory_id):
                return 'The target memory does not exist or is hidden.', '被拒绝', summary
        if name == 'plan':
            related = args.get('related_bucket')
            if related and self.memory.is_hidden(related):
                return 'The linked target memory is hidden.', '被拒绝', summary
        try:
            result = self.memory.call(name, args)
        except MemoryToolError as exc:
            return str(exc), '出错', summary
        except Exception:
            return 'The memory tool failed. Check the current memory state before retrying.', '出错', summary
        if name in ('dream', 'pulse'):
            result = self._filter_hidden_result(result, name)
        if name == 'dream':
            result = neutral_dream_header(result)
        return self._bounded(result), '成功', summary

    def _trace(self, args, approval):
        memory_id = args.get('bucket_id')
        summary = self.memory.title_for(memory_id) if isinstance(memory_id, str) else None
        banned = ('hard_delete', 'deletion_request_id', 'deletion_decision')
        if any(args.get(key) not in (None, False, '', [], {}) for key in banned):
            return 'This delete operation or argument combination is not available. Use the supported delete flow and its required confirmation.', '被拒绝', str(summary or 'trace')
        if not summary or self.memory.is_hidden(memory_id):
            return 'The target memory does not exist or is hidden.', '被拒绝', str(memory_id or 'trace')
        delete = args.get('delete', False)
        if type(delete) is not bool:
            return 'delete must be a Boolean.', '被拒绝', summary
        edit_reason = args.get('edit_reason', '')
        if not isinstance(edit_reason, str) or len(edit_reason) > WHY_LIMIT:
            return f'edit_reason must be text within the {WHY_LIMIT}-character limit.', '被拒绝', summary
        reason = args.get('delete_reason', '')
        if delete and (not isinstance(reason, str) or not reason.strip() or len(reason) > WHY_LIMIT):
            return f'A delete request must include a reason within the {WHY_LIMIT}-character limit.', '被拒绝', summary
        old = self.memory.raw_content_for(memory_id)
        if old is None:
            return 'The target memory could not be read, so it was not changed.', '被拒绝', summary
        if delete and self.autonomy_mode:
            return self._queue_delete(memory_id, summary, args, old)
        if delete and (approval is None or approval(
                f"{self.memory.cfg.name} 想删除记忆 #{memory_id}：{summary}\n理由：{str(args.get('delete_reason') or '未提供')[:WHY_LIMIT]}。同意吗？y/N ").strip().casefold() != 'y'):
            return 'Lan did not approve the deletion; the memory remains.', '被拒绝', summary
        if delete:
            if not self._save_version(memory_id, args, old):
                return 'The previous version could not be archived safely, so this change was not made.', '出错', summary
            failure = self._delete(memory_id, summary)
            if failure:
                return 'Deletion was not confirmed. Check the memory state before retrying.', '出错', summary
            if self.memory.state['entries'].get(memory_id, {}).get('erased'):
                return 'Lan approved; the memory and its saved old versions were erased.', '成功', f'{summary}（已彻底删除）'
            return 'Lan approved and the memory was deleted.', '成功', f'{summary}（已删除）'
        try:
            safe_args = {key: value for key, value in args.items()
                         if key not in (*banned, 'edit_reason', 'delete')}
            # The authorized edit path must remain an edit after MCP conversion.
            safe_args['delete'] = False
            operation_id, duplicate_operation = self.memory.begin_write(safe_args)
            if duplicate_operation is not None:
                return ('This memory update is still unconfirmed after its earlier connection failure. '
                        'It was not sent again; check the memory service before another edit.'), '出错', summary
            if not self._save_version(memory_id, args, old):
                self.memory.finish_write(operation_id, 'not_submitted')
                return 'The previous version could not be archived safely, so this change was not made.', '出错', summary
            result = self.memory.call('trace', safe_args, write_operation=operation_id)
        except MemoryToolError as exc:
            return f'{exc} The previous version was archived.', '出错', summary
        except Exception:
            return 'The memory update failed; the previous version was archived.', '出错', summary
        self.memory.bucket_details_cache.pop(memory_id, None)
        updated = self.memory.content_for(memory_id, refresh=True)
        if updated is not None:
            self.memory.record_native_update(memory_id, updated)
        self.memory.refresh_visibility(memory_id)
        feedback = (f'{result}\nComplete memory before the change (including metadata) has been saved. '
                    f'Reason for this operation: {edit_reason.strip() or "not provided"}. '
                    'The content field and meaning_append/meaning_replace metadata are separate fields.')
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
            return 'delete_reason must be text within the 500-character limit.', '被拒绝', summary
        if not self._save_version(memory_id, args, old):
            return 'The previous version could not be archived safely, so no delete request was created.', '出错', summary
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
            return 'The pending deletion request could not be saved safely; the memory was not deleted. Retry later.', '出错', summary
        self.memory.messages.put(f'待处理的删除申请：记忆 #{memory_id}「{summary}」，理由：{reason or "未提供"}。')
        return 'The memory was placed in pending deletion for Lan to review.', '成功', summary

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
                notes.append(f'Memory {memory_id} ({title}) was already handled in the memory panel; '
                             'no duplicate deletion was performed.')
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
                notes.append(f'The pending deletion marker for memory {memory_id} ({title}) was removed; '
                             'the request is closed.')
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
                    self.memory.messages.put(f'Deletion request #{memory_id} was not completed; check its state before retrying.')
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
            outcome = 'was approved and deleted' if accepted else 'was not approved; its pending marker was removed'
            notes.append(f'The pending deletion request for memory {memory_id} ({title}) {outcome}. '
                         f'Request reason: {row.get("reason") or "not provided"}.')
        return notes

    def _delete(self, memory_id, title):
        entry = self.memory.state['entries'].setdefault(memory_id, {'title': title})
        entry.update(hidden=True, deleted=True, delete_pending=True)
        self.memory.persist()
        try:
            self.memory.call('trace', {'bucket_id': memory_id, 'delete': True})
        except Exception:
            return 'The deletion was not confirmed; this memory remains hidden. Check its state before retrying with /delete.'
        entry.pop('delete_pending', None)
        self.memory.persist()
        self.memory.erase(memory_id)
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

    SEARCH_RESULTS_LIMIT = 20

    def _editable(self, memory_id):
        """A memory may be extended only if Peter was shown it: his own search results, or an
        automatic recall (read fresh from disk now). Other IDs, hidden or erased ones stay refused."""
        shown = memory_id in self.search_results or memory_id in getattr(self.memory, 'surfaced_ids', {})
        # Visibility is re-read from disk every time: a memory hidden or archived after it was shown stays refused.
        if not shown or not self.memory.refresh_visibility(memory_id):
            return False
        if memory_id in self.search_results:
            return True
        text = self.memory.content_for(memory_id, refresh=True)
        if text is None:
            return False
        self.search_results[memory_id] = text
        while len(self.search_results) > self.SEARCH_RESULTS_LIMIT:
            self.search_results.pop(next(iter(self.search_results)))
        return True

    def _hold(self, args):
        summary = self.preview_summary('hold', args)
        content, title = args.get('content'), args.get('title')
        target_id = args.get('target_id')
        why = args.get('why_remembered', '')
        if (not isinstance(content, str) or not content.strip() or not isinstance(why, str)
                or (target_id is None and (not isinstance(title, str) or not title.strip()))):
            return 'To create a memory, provide content and title. To update one, provide content.', '被拒绝', summary
        if target_id is None and args.get('merge_ids'):
            return 'Retrieved memories can be merged only when target_id is explicitly specified.', '被拒绝', summary
        if (len(content) > CONTENT_LIMIT or len(why) > WHY_LIMIT
                or (isinstance(title, str) and len(title) > TITLE_LIMIT)):
            return f'The content exceeds the {CONTENT_LIMIT}-character limit. Shorten it and save again.', '被拒绝', summary
        if target_id is not None:
            if not isinstance(target_id, str) or not self._editable(target_id):
                return 'target_id must be a memory you were shown: an automatic recall or a breath_search result from this conversation. Search again and use its ID.', '被拒绝', summary
            previous = self.search_results[target_id]
            if not self.memory.visible([(target_id, previous)]):
                return 'This memory is hidden or unavailable; no update was made.', '被拒绝', summary
            merge_ids = args.get('merge_ids', [])
            if (not isinstance(merge_ids, list) or any(not isinstance(key, str) for key in merge_ids)
                    or len(set(merge_ids)) != len(merge_ids)):
                return 'merge_ids must be a list of distinct IDs of memories you were shown. Search again and use only those IDs.', '被拒绝', summary
            source_ids = [key for key in merge_ids if key != target_id]
            if any(not self._editable(key) for key in source_ids):
                return 'Every merge_id must be a memory you were shown (automatic recall or breath_search in this conversation). Search again before retrying.', '被拒绝', summary
            if (source_ids
                    and not self.memory.visible([(key, self.search_results[key]) for key in source_ids])):
                return 'A memory selected for merging is hidden or unavailable; no update was made.', '被拒绝', summary
            current = self.memory.content_for(target_id, refresh=True)
            if current != previous:
                self.search_results = {}
                return 'The target changed after search, so it was not updated. Run breath_search again before retrying.', '被拒绝', summary
            for source_id in source_ids:
                if self.memory.content_for(source_id, refresh=True) != self.search_results[source_id]:
                    self.search_results = {}
                    return 'A source memory changed after search, so nothing was updated. Run breath_search again before retrying.', '被拒绝', summary
            addition = content.strip()
            if why.strip() and why.strip() not in addition:
                addition = f'{addition}\n补充原因：{why.strip()}'
            if not addition:
                return 'The additional memory content cannot be empty.', '被拒绝', summary
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
                return 'This content is already in the memory; nothing was written twice.', '成功', f'已存在：{summary}'
            updated = merged
            if len(updated.encode('utf-8')) > MERGED_MEMORY_LIMIT_BYTES:
                return "The merged memory would exceed Ombre's safe per-memory size limit; the original was not changed. Shorten the addition and retry.", '被拒绝', summary
            old_document = self.memory.raw_content_for(target_id)
            if old_document is None:
                return 'The complete target memory could not be read, so no content was added.', '被拒绝', summary
            if not self._save_version(target_id, {'content': addition, 'why_remembered': why},
                                      old_document, operation_reason='hold(target_id) 追加'):
                return 'The complete previous version could not be safely archived, so no content was added.', '出错', summary
            text = self.memory.call('trace', {'bucket_id': target_id, 'content': updated})
            stored = self.memory.content_for(target_id, refresh=True)
            preserved = [previous, *(self.search_results[key] for key in source_ids), addition]
            if stored is None or any(part and part not in stored for part in preserved):
                self.search_results = {}
                return 'A complete saved result could not be verified. Check this memory before continuing.', '出错', summary
            self.memory.record_native_update(target_id, stored)
            self.search_results[target_id] = stored
            merged_note = f'，并整合 {len(source_ids)} 条相关记忆' if source_ids else ''
            return ('Content was appended to this memory. Its original text and title remain, and '
                    'the complete previous version with metadata was archived.',
                    '成功', f'更新：{summary}（{target_id}）{merged_note}')
        ombre_args = {'content': content.strip(), 'title': title.strip()}
        if why.strip():
            ombre_args['why_remembered'] = why.strip()
        operation_id, duplicate_operation = self.memory.begin_write(ombre_args)
        if duplicate_operation is not None:
            return ('This save is still unconfirmed after its earlier connection failure. '
                    'It was not sent again; check the memory service before creating a new save.'), '出错', summary
        try:
            text = self.memory.call('hold', ombre_args, write_operation=operation_id)
        except MemoryToolError as exc:
            if exc.code in {'closed', 'connection'}:
                return ('The memory service was closed before this save was submitted. '
                        'No memory was saved; retry only under the existing rules.'), '出错', summary
            return ('The save result is unconfirmed. Do not claim the memory was saved; it was not sent again '
                    'and will remain pending for safe review after recovery.'), '出错', summary
        try:
            kind = self.memory.record_native(ombre_args, text)
        except ValueError:
            # Memory.call already records Ombre's confirmed response before
            # this local index update.  Report the local failure without
            # inviting a duplicate hold.
            return 'The save was confirmed, but its local memory index could not be updated. Do not save it again; reopen the memory service before continuing.', '出错', summary
        memory_id = self.memory.created_id(text)
        return (('Merged into an existing memory.' if kind == '合并' else 'Memory saved.') + f' memory_id: {memory_id}',
                '成功', f'{kind}：{summary}')

    def _search(self, args):
        query = args.get('query')
        if not isinstance(query, str) or not query.strip():
            return 'The search query must be non-empty text.', '被拒绝', 'breath_search'
        text = self.memory.call('breath_search', {
            'query': query.strip(), 'mode': 'automatic', 'with_ids': True, 'max_results': 3})
        found = self.memory.visible(ordinary_hits(text))
        # Keep earlier results so a memory found a moment ago can still be extended (ticket 183).
        self.memory.mark_surfaced([key for key, _ in found])
        rendered = []
        for key, body in found:
            title = self.memory.title_for(key) or '标题未登记'
            exact_content = self.memory.content_for(key)
            if exact_content is not None:
                self.search_results.pop(key, None)
                self.search_results[key] = exact_content
                while len(self.search_results) > self.SEARCH_RESULTS_LIMIT:
                    self.search_results.pop(next(iter(self.search_results)))
                rendered.append(f'[Memory ID: {key} | Title: {title}]\n{exact_content}')
            else:
                display = body.removeprefix(f'[bucket_id:{key}]').strip()
                rendered.append(f'[Memory ID: {key} | Title: {title} | exact local text unavailable; cannot update]\n{display}')
        result = '\n---\n'.join(rendered) or 'No relevant memories were found.'
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
    def capability_event_model(self):
        return getattr(self.files, 'capability_event_model', None)

    @property
    def capability_record(self):
        return getattr(self.files, 'capability_record', None)

    @property
    def startup_notice(self):
        return getattr(self.files, 'startup_notice', None)

    def consume_capability_event(self, delivered_event=None):
        consume = getattr(self.files, 'consume_capability_event', None)
        if consume:
            # Older file-tool adapters only expose the original no-argument
            # acknowledgement hook.  Keep them usable while the persistent
            # Filesystem implementation receives the delivered snapshot.
            try:
                parameters = inspect.signature(consume).parameters.values()
            except (TypeError, ValueError):
                return consume(delivered_event)
            accepts_event = any(parameter.kind == parameter.VAR_POSITIONAL or
                                parameter.kind in (parameter.POSITIONAL_ONLY,
                                                   parameter.POSITIONAL_OR_KEYWORD)
                                for parameter in parameters)
            return consume(delivered_event) if accepts_event else consume()
        return False

    def set_autonomy(self, enabled):
        self.native.autonomy_mode = bool(enabled)

    def begin_request(self):
        begin = getattr(self.files, 'begin_request', None)
        if begin:
            begin()

    def announce_autonomy(self):
        self.files.announce_autonomy()

    def announce_autonomy_more_room(self):
        announce = getattr(self.files, 'announce_autonomy_more_room', None)
        if announce:
            announce()

    def announce_long_tasks(self):
        announce = getattr(self.files, 'announce_long_tasks', None)
        if announce:
            announce()

    def announce_autonomy_history(self):
        announce = getattr(self.files, 'announce_autonomy_history', None)
        if announce:
            announce()

    def announce_profile_notes(self, max_bytes):
        announce = getattr(self.files, 'announce_profile_notes', None)
        if announce:
            announce(max_bytes)

    def invoke(self, name, args, approval=input, *, should_stop=None):
        if self.native.handles(name):
            return self.native.invoke(name, args, approval)
        if should_stop is None:
            return self.files.invoke(name, args, approval)
        return self.files.invoke(name, args, approval, should_stop=should_stop)

    def preview_summary(self, name, args):
        if self.native.handles(name):
            return self.native.preview_summary(name, args)
        return self.files.preview_summary(name, args)



