"""Native Ombre memory tools exposed through the shared chat tool loop.

The model decides what is worth keeping; Ombre does the tagging and same-event merge.
Deleting is not offered to the model: /delete stays with the user.
"""
from types import SimpleNamespace
import unicodedata
import inspect
import json
from datetime import datetime, timezone
import hashlib
import re
import uuid

from agent_types import UserError
from brain_writer import WriteConflict
from capability_state import capability_lock, save_capabilities
from memory_agent import ordinary_hits, MemoryToolError, SEARCH_SPARE_HITS, vault_path as role_vault_path
from memory_sidecar import Sidecar

# Ticket 427/441: Ombre's feature tools are gone. Only trace (edit/archive/delete) remains, and
# P3-1 defines its schema locally so memory no longer needs an Ombre process just to describe tools.
OMBRE_TOOLS = ('trace',)
NATIVE_TOOLS = ('hold', 'breath_search', *OMBRE_TOOLS, 'memory_history', 'memory_catalog', 'memory_read',
                'memory_timeline')
CONTENT_LIMIT, TITLE_LIMIT, WHY_LIMIT = 1500, 100, 500
# Ticket 421: Peter's own save is no longer capped at 600 characters. The real ceiling is the model's
# output budget (a tool call cannot be longer than the answer it came in), so the program only guards
# against a payload the model could not have written. 0 means no fixed character cap.
MEMORY_CHARS = 0
# Ombre allows 20 pinned memories; VEIL keeps the permanent front row much smaller (ticket 184).
PINNED_LIMIT = 8
CATALOG_PAGE = 5
MERGED_MEMORY_LIMIT_BYTES = 48 * 1024

HOLD = SimpleNamespace(name='hold', inputSchema={
    'type': 'object', 'required': ['content'], 'additionalProperties': False,
    'properties': {
        'content': {'type': 'string', 'description':
            'The whole memory in your own words; say when it happened (e.g. 11-16). Write it as long as it '
            'needs to be: the program keeps every stage and can read it back. With target_id it replaces the '
            'old text entirely: keep what still matters, drop repeats.'},
        'title': {'type': 'string', 'description':
            'Short title; required for a new memory. With target_id a new title replaces the old one.'},
        'target_id': {'type': 'string', 'description':
            'ID of a shown memory about this same event; you rewrite it whole.'},
        'merge_ids': {'type': 'array', 'items': {'type': 'string'}, 'description':
            'With target_id: other shown memories that are copies of the same event. They are archived '
            '(Lan can restore them).'},
        'link_to': {'type': 'string', 'description':
            'New memory only: ID of a shown memory this one continues (an earlier stage of the same experience).'},
        'why_remembered': {'type': 'string', 'description': 'Optional: why this matters to you, first person.'},
        'interpretation': {'type': 'string', 'description':
            'Optional: how you read it at the time; your own understanding, never a fact.'},
        'importance': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'description':
            'Optional, new memory only: 1-10, default 5; decides what surfaces first when a conversation opens.'},
        'pinned': {'type': 'boolean', 'description':
            'Optional, new memory only: a permanent core memory that always surfaces first. Very scarce: at most '
            + str(PINNED_LIMIT) + ' exist and why_remembered is required.'},
    }}, description=(
        'Save a long-term memory when YOU judge it worth keeping; Lan need not say "remember". Keep shared '
        'experiences, decisions and why, the relationship, your own changes of mind, and talk about your memory, '
        'abilities or upgrades. Not memories: small talk, game ledgers, to-dos (use life_record), status reports, '
        'transcripts. Write it as you would recall it: first person, what happened and what you thought of it. '
        'Before a new memory is saved the program shows related ones; then choose: the same event or more of it '
        '(new details, or a part of it not yet written) -> target_id, rewriting that memory whole; what happened '
        'afterwards because of it, on a later occasion -> link_to; unrelated -> send the '
        'same hold again. Only say you remembered it if this tool succeeded.'))
SEARCH = SimpleNamespace(name='breath_search', inputSchema={
    'type': 'object', 'required': ['query'], 'additionalProperties': False,
    'properties': {'query': {'type': 'string', 'description': 'What to look for.'},
                   'date_from': {'type': 'string', 'description': 'Optional YYYY-MM-DD: only memories created on or after it.'},
                   'date_to': {'type': 'string', 'description': 'Optional YYYY-MM-DD: only memories created on or before it.'}}},
    description='Look up your saved memories when the automatic recall did not bring up what you need. '
                'date_from / date_to narrow the search to memories created in that period.')

DESCRIPTION = ('Memory tools are separate from file access. Use hold to save lasting experience and '
               'breath_search to find a known memory; use trace for edits and the catalog/timeline tools '
               'for library status. Archived/deleted memories are withheld by the program. ')
TRACE = SimpleNamespace(name='trace', inputSchema={
    'type': 'object', 'required': ['bucket_id'], 'additionalProperties': False,
    'properties': {
        'bucket_id': {'type': 'string', 'description':
            'Exact ID of a memory shown in this conversation (recall or breath_search).'},
        'content': {'type': 'string', 'description':
            'Replace the whole body with this text. With old_str/new_str leave this empty.'},
        'old_str': {'type': 'string', 'description':
            'One exact, unique passage to replace; use together with new_str.'},
        'new_str': {'type': 'string', 'description':
            'Replacement for old_str; an empty string removes the passage.'},
        'title': {'type': 'string', 'description': 'New title for this memory.'},
        'importance': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'description':
            'How strongly this memory should surface (1-10).'},
        'pinned': {'type': 'boolean', 'description':
            'True keeps it in the opening front row; requires edit_reason. False unpins.'},
        'why_remembered': {'type': 'string', 'description':
            'Optional first-person note about why this matters.'},
        'edit_reason': {'type': 'string', 'description':
            'Short reason for this change, kept in VEIL history.'},
        'merge_into': {'type': 'string', 'description':
            'Archive this duplicate memory into another shown memory.'},
        'restore': {'type': 'boolean', 'description':
            'Bring an archived memory back into normal recall.'},
        'archive': {'type': 'boolean', 'description':
            'Move this memory to the archived list without deleting it.'},
        'delete': {'type': 'boolean', 'description':
            'Ask Lan to delete this memory; during autonomous activity it becomes a pending request.'},
        'delete_reason': {'type': 'string', 'description': 'Why this memory should be deleted.'},
    }}, description=(
        'Edit one existing memory by its exact bucket_id; never guess an ID. content replaces the whole body; '
        'or old_str/new_str replaces one exact passage; not both. title renames it. importance 1-10 controls '
        'how strongly it surfaces; pinned=true keeps it in the opening front row (needs edit_reason and '
        'why_remembered). merge_into archives a duplicate; restore=true unarchives; '
        'delete=true asks Lan (autonomous activity creates a request she answers).'
        ' edit_reason says why you change it now and is kept only in VEIL history.'))


MEMORY_HISTORY = SimpleNamespace(name='memory_history', inputSchema={
    'type': 'object', 'required': ['memory_id'], 'additionalProperties': False,
    'properties': {'memory_id': {'type': 'string', 'description':
        'Stable memory ID from breath_search.'}}},
    description='Read the exact previous versions VEIL saved before trace edits or hold(target_id) additions. '
                'Hidden memories are unavailable.' )


MEMORY_CATALOG = SimpleNamespace(name='memory_catalog', description=(
    'List current visible memory IDs, titles and creation dates, five per page, from local metadata '
    '(no embeddings, no model). order: "id" (default, stable ID order), "newest" or "oldest" by creation time. '
    'created_from / created_to (YYYY-MM-DD, both inclusive, optional) keep only memories created in that period; '
    'use them to walk the older memories of a given time span. Each result has total (all matches), items and '
    'next_after; pass next_after as after, with the same order and dates, to continue; an empty next_after '
    'means done. Use memory_read for current text; memory_history contains only older versions.'), inputSchema={
        'type': 'object', 'additionalProperties': False, 'properties': {
            'after': {'type': 'string'},
            'order': {'type': 'string', 'enum': ['id', 'newest', 'oldest']},
            'created_from': {'type': 'string', 'description': 'YYYY-MM-DD, inclusive.'},
            'created_to': {'type': 'string', 'description': 'YYYY-MM-DD, inclusive.'}}})
MEMORY_TIMELINE = SimpleNamespace(name='memory_timeline', description=(
    'Read one experience: the whole story, then its stages with evidence, one page at a time. '
    'Pass next_offset from the previous answer to continue; read only.'), inputSchema={
    'type': 'object', 'required': ['event_id'], 'additionalProperties': False, 'properties': {
        'event_id': {'type': 'string'},
        'offset': {'type': 'integer', 'minimum': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 60}}})
MEMORY_READ = SimpleNamespace(name='memory_read', description=(
    'Read an exact slice of a current visible memory, including its metadata. '
    'Pass its memory_id from memory_catalog and next_offset as offset until next_offset is null. '
    'Offsets count Unicode characters; pass the returned version on subsequent pages; restart if changed. '
    'This is read only and does not call embeddings or a model.'), inputSchema={
        'type': 'object', 'required': ['memory_id'], 'additionalProperties': False,
        'properties': {'memory_id': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}, 'version': {'type': 'string'}}})


# Ticket 145: Ombre's own docstrings are long Chinese text about its internals and test
# helpers. Peter gets the same schema with short English descriptions of what VEIL allows.
OMBRE_DESCRIPTIONS = {'trace': TRACE.description}


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


MONTHS = {name: index for index, names in enumerate(
    ('jan january', 'feb february', 'mar march', 'apr april', 'may', 'jun june', 'jul july', 'aug august',
     'sep sept september', 'oct october', 'nov november', 'dec december'), 1) for name in names.split()}
_MONTH = (r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?'
          r'|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?')
_DATE = re.compile(r'(?<!\d)(?:\d{4}-)?(\d{1,2})-(\d{1,2})(?!\d)|(\d{1,2})月(\d{1,2})[日号]'
                   r'|\b(' + _MONTH + r')\.? (\d{1,2})(?:st|nd|rd|th)?\b'
                   r'|\b(\d{1,2})(?:st|nd|rd|th)? (' + _MONTH + r')\b', re.I)


def dates_in(text):
    """(month, day) pairs written in a memory: 10-31, 2026-10-31, 10月31日, oct 31, 31 october."""
    found = set()
    for match in _DATE.finditer(text or ''):
        groups = match.groups()
        if groups[0]:
            month, day = int(groups[0]), int(groups[1])
        elif groups[2]:
            month, day = int(groups[2]), int(groups[3])
        elif groups[4]:
            month, day = MONTHS.get(groups[4].casefold()), int(groups[5])
        else:
            month, day = MONTHS.get(groups[7].casefold()), int(groups[6])
        if month and 1 <= month <= 12 and 1 <= day <= 31:
            found.add((month, day))
    return found


class NativeMemoryTools:
    def __init__(self, memory, max_result_bytes=8192, model=None):
        self.memory, self.max_result_bytes = memory, max_result_bytes
        self.model = model if callable(model) else None
        self.search_results = {}
        # P3-1: trace's schema is defined locally; memory no longer needs an Ombre process to
        # describe or authorize it. No dream tool: it was a no-op under Brain authority.
        self.ombre_tools = {'trace': TRACE}
        model_tools = [ombre_tool_for_model(tool) for tool in self.ombre_tools.values()]
        self.description = DESCRIPTION
        self.autonomy_mode = False
        # Ticket 363: VEIL's own notes on each memory (locks, links, sources, merges) and the save Peter
        # was last shown related memories or dropped dates for; sending that same save again confirms it.
        self.sidecar = Sidecar(memory.store, memory.cfg.id, memory.budget.clock)
        self.pending_confirm = []
        # Ticket 407: one event, one memory. Off unless [memory.events] enabled = true.
        self.events = None
        event_settings = (getattr(memory.cfg, 'memory', None) or {}).get('events')
        if isinstance(event_settings, dict) and event_settings.get('enabled'):
            from event_memory import EventMemory
            experience = (getattr(memory.cfg, 'memory', None) or {}).get('experience') or {}
            daily_cap = experience.get('daily_budget_cny') if isinstance(experience, dict) else None
            self.events = EventMemory(memory, event_settings, model=self.model, sidecar=self.sidecar,
                                      budget=memory.budget, cost_category='experience_memory',
                                      daily_cap=daily_cap)
            self.events.locked = self.sidecar.locked
            # Startup recovery: finish a stage/event write interrupted last run even when the chat-tidy
            # feature is not configured at all. Lock checks must already be bound.
            try:
                self.events.dream_pass('startup')
            except Exception:
                pass
        self.tools = [HOLD, SEARCH, *model_tools, MEMORY_HISTORY, MEMORY_CATALOG, MEMORY_READ,
                      *([MEMORY_TIMELINE] if self.events is not None else [])]

    def _memory_limit(self):
        """Ticket 421: a long memory is allowed; the ceiling is the real budget of a tool turn."""
        if not MEMORY_CHARS:
            try:
                model_tokens = int(getattr(self.memory.cfg, 'max_output_tokens', 0) or 0)
            except (TypeError, ValueError):
                model_tokens = 0
            try:
                tool_tokens = int((getattr(self.memory.cfg, 'tools', None) or {}).get(
                    'max_output_tokens', 0) or 0)
            except (TypeError, ValueError):
                tool_tokens = 0
            tokens = max(model_tokens, tool_tokens)
            return tokens * 4 if tokens > 0 else 0
        return MEMORY_CHARS

    def _too_long(self, text):
        limit = self._memory_limit()
        return (len(text), limit) if limit and len(text) > limit else None

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
            if name == 'memory_timeline':
                return self._timeline(args)
            if name == 'trace':
                return self._trace(args, approval)
            return ('This tool is not authorized for the current role. Choose an available tool; '
                    'permissions cannot be bypassed.'), '被拒绝', self.preview_summary(name, args)
        except MemoryToolError as exc:
            return str(exc), '出错', self.preview_summary(name, args)
        except Exception:
            return 'The memory tool failed. Check the current memory state before retrying.', '出错', self.preview_summary(name, args)

    def _visible_records(self, include_hidden=False):
        if getattr(self.memory, 'brain_authority', None) is not None:
            from brain_read import BrainRead
            try:
                with BrainRead(self.memory.brain_authority.db_path) as reader:
                    entries = reader.panel_entries(limit=10000)
                    records = {}
                    for entry in entries:
                        if entry.get('delete_pending'):
                            continue
                        if entry['bucket'] not in ('dynamic', 'permanent'):
                            continue
                        records[entry['memory_id']] = (entry['title'], reader.raw(entry['memory_id']) or '')
                    return records
            except Exception:
                return {}
        # Read only actual bucket folders, never letters, plans, sources or private files.
        vault_path = role_vault_path(self.memory.store.root, self.memory.cfg.id)
        hidden = self.memory.hidden_ids()
        from veil.tools.tools_agent import _redirected
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
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key) or (key in hidden and not include_hidden):
                    continue
                if meta.get('deleted_at', '').casefold() not in ('', 'null', 'none', 'false'):
                    continue
                if self.memory.state['entries'].get(key, {}).get('deleted'):
                    continue
                if key in records:
                    raise ValueError('ambiguous memory ID')
                records[key] = (meta.get('title') or meta.get('name') or key, raw)
        return records

    def _panel_records(self):
        """id -> (title, raw_markdown, body_sha256) from one consistent read.

        Reading the body and the hash together means the memory page can never show one body
        with another version's lock token. Brain is authoritative after the P3 switch.
        """
        auth = getattr(self.memory, 'brain_authority', None)
        if auth is not None:
            from brain_read import BrainRead
            with BrainRead(auth.db_path) as reader:
                pending = {row[0] for row in reader.connection.execute(
                    "SELECT memory_id FROM delete_requests WHERE status='pending'")}
                rows = reader.connection.execute(
                    'SELECT id,title,name,bucket,raw_markdown,body,body_sha256 FROM memories '
                    'WHERE is_tombstone=0 AND is_deleted=0').fetchall()
            records = {}
            for row in rows:
                if row['bucket'] not in ('dynamic', 'permanent') or row['id'] in pending:
                    continue
                body = row['body'] or ''
                sha = row['body_sha256'] or hashlib.sha256(body.encode('utf-8')).hexdigest()
                records[row['id']] = (row['title'] or row['name'] or row['id'],
                                      row['raw_markdown'] or '', sha)
            return records
        records = {}
        for key, (title, raw) in self._visible_records().items():
            _meta, body = parse_frontmatter(raw)
            records[key] = (title, raw, hashlib.sha256(body.encode('utf-8')).hexdigest())
        return records

    def _visible_id(self, memory_id):
        """Existence by ID, never by a bounded list; same bucket/delete rules as the page."""
        if not isinstance(memory_id, str) or not memory_id:
            return False
        auth = getattr(self.memory, 'brain_authority', None)
        if auth is not None:
            try:
                from brain_read import BrainRead
                with BrainRead(auth.db_path) as reader:
                    row = reader.connection.execute(
                        'SELECT bucket FROM memories WHERE id=? AND is_tombstone=0 AND is_deleted=0',
                        (memory_id,)).fetchone()
                    if row is None or row['bucket'] not in ('dynamic', 'permanent'):
                        return False
                    return reader.connection.execute(
                        "SELECT 1 FROM delete_requests WHERE memory_id=? AND status='pending'",
                        (memory_id,)).fetchone() is None
            except Exception:
                return False
        return memory_id in self._visible_records()

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
        for key, (title, raw, sha) in self._panel_records().items():
            meta, body = parse_frontmatter(raw)
            tags = as_list(meta.get('tags'))
            # P3-3: old surprise tags are historical data, not a page visibility rule.
            entries.append({
                'memory_id': key, 'title': title, 'created': meta.get('created', ''),
                'kind': meta.get('type', ''), 'domain': as_list(meta.get('domain')),
                'tags': tags, 'importance': meta.get('importance', ''),
                'why': meta.get('why_remembered', ''),
                'body': body.strip(), 'surprise': False,
                'sha256': sha,
                'source_record': title.strip() == '原文来源', 'recalled': recalled.get(key, 0)})
        try:
            notes = self.sidecar.all()
        except Exception:
            notes = {}
        titles = {entry['memory_id']: entry['title'] for entry in entries}
        merged_from = {}
        for key, note in notes.items():
            if note.get('merged_into'):
                merged_from.setdefault(note['merged_into'], []).append(key)
        for entry in entries:
            note = notes.get(entry['memory_id'], {})
            entry['locked'] = bool(note.get('locked'))
            entry['lan_edited'] = note.get('lan_edited')
            entry['peter_after_lan'] = note.get('peter_after_lan')
            entry['links'] = [{'memory_id': key, 'title': titles.get(key, '')} for key in note.get('links', [])
                              if key in titles]
            entry['continued_by'] = [{'memory_id': key, 'title': titles[key]} for key, other in notes.items()
                                     if entry['memory_id'] in other.get('links', []) and key in titles]
            entry['sources'] = note.get('sources', [])[-5:]
            entry['merged_from'] = len(merged_from.get(entry['memory_id'], []))
        entries.sort(key=lambda entry: entry['created'], reverse=True)
        return {'items': entries, 'recent_requests': len(rows)}

    # Ticket 363: what Lan can do on the memory page besides edit and delete.
    def panel_versions(self, memory_id):
        """Earlier versions of one memory, newest first, as plain text Lan can read and restore."""
        if not self._visible_id(memory_id):
            return None
        rows = [row for row in self.memory.store.read(f'{self.memory.cfg.id}/memory-versions.jsonl')
                if row.get('memory_id') == memory_id and isinstance(row.get('old_content'), str)]
        out = []
        for index, row in enumerate(rows):
            meta, body = parse_frontmatter(row['old_content'])
            out.append({'index': index, 'time': row.get('time', ''), 'reason': row.get('operation_reason') or '',
                        'title': meta.get('title') or meta.get('name') or '', 'body': body.strip()})
        return list(reversed(out))

    def panel_restore_version(self, memory_id, index):
        versions = self.panel_versions(memory_id)
        chosen = next((row for row in versions or [] if row['index'] == index), None)
        if chosen is None or not chosen['body']:
            return False, '找不到这个旧版本。'
        fields = {'content': chosen['body']}
        if chosen['title']:
            fields['title'] = chosen['title']
        # Restoring puts back exactly what was there: the page's 1500-character box is for typing by hand, and
        # recall showing only the first 600 characters (ticket 353) never touches what is stored. Only what Ombre
        # itself would refuse (its per-memory byte limit) stops a restore.
        if len(chosen['body'].encode('utf-8')) > MERGED_MEMORY_LIMIT_BYTES:
            return False, '这个旧版本超过记忆库能保存的长度，没有恢复。'
        args = {'bucket_id': memory_id, **fields, 'edit_reason': 'Lan restored an earlier version on the memory page.'}
        result, state, _ = self._trace(args, None, by_lan=True)
        if state != '成功':
            return False, result
        note = '已恢复，恢复前的样子也留了一份。'
        return True, note

    def panel_archived(self):
        """Archived memories (merged copies and ones Ombre let fade), never ones Lan deleted."""
        if getattr(self.memory, 'brain_authority', None) is not None:
            # P2-B: with Brain authoritative the vault archive is a stale Ombre copy. The list and
            # the unarchive check must come from brain.db or the page shows old rows and refuses
            # memories archived after the switch.
            from brain_read import BrainRead
            with BrainRead(self.memory.brain_authority.db_path) as reader:
                rows = reader.archived(limit=10000)
                titles = {into: reader.title(into) or into
                          for into in {row['merged_into'] for row in rows} if into}
            out = []
            for row in rows:
                into = row['merged_into']
                out.append({'memory_id': row['memory_id'], 'title': row['title'],
                            'created': row['created'] or '', 'body': (row['body'] or '').strip(),
                            'merged_into': into,
                            'merged_into_title': titles.get(into, '')})
            return out
        vault_path = role_vault_path(self.memory.store.root, self.memory.cfg.id)
        archive = vault_path / 'archive'
        try:
            notes = self.sidecar.all()
        except Exception:
            notes = {}
        out = []
        if not archive.exists():
            return out
        from veil.tools.tools_agent import _redirected
        if _redirected(vault_path) or _redirected(archive):
            raise ValueError('redirected vault')
        vault = vault_path.resolve()
        for path in archive.rglob('*.md'):
            if not path.resolve().is_relative_to(vault) or _redirected(path):
                continue
            meta, body = parse_frontmatter(path.read_text(encoding='utf-8'))
            key = str(meta.get('id') or '')
            if (not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key)
                    or self.memory.state['entries'].get(key, {}).get('deleted')
                    or meta.get('source_tool', '').casefold() == 'letter'):
                continue
            into = notes.get(key, {}).get('merged_into')
            out.append({'memory_id': key, 'title': meta.get('title') or meta.get('name') or key,
                        'created': meta.get('created', ''), 'body': body.strip(),
                        'merged_into': into, 'merged_into_title': (self.memory.title_for(into) or '') if into else ''})
        out.sort(key=lambda row: row['created'], reverse=True)
        return out

    def panel_unarchive(self, memory_id):
        if not isinstance(memory_id, str) or memory_id not in {row['memory_id'] for row in self.panel_archived()}:
            return False, '找不到这条已归档的记忆。'
        try:
            self.memory.call('trace', {'bucket_id': memory_id, 'restore': True})
        except Exception:
            return False, '记忆服务这次没有恢复成功，记忆还在归档里。'
        self.memory.bucket_details_cache.pop(memory_id, None)
        if not self.memory.refresh_visibility(memory_id):
            return False, '记忆服务回答了，但这条还没回到记忆里；稍后刷新再看。'
        self.sidecar.unarchived(memory_id)
        return True, '已恢复，它会重新被想起。'

    def panel_archive(self, memory_id):
        """Move a memory into the archive through the same reversible trace path Lan uses."""
        if not self._visible_id(memory_id):
            return False, '这条记忆不存在或已经删除。'
        args = {'bucket_id': memory_id, 'archive': True,
                'edit_reason': 'Lan archived this memory on the memory page.'}
        result, state, _ = self._trace(args, None, by_lan=True)
        return (True, '已移入归档，可随时恢复。') if state == '成功' else (False, result)

    def panel_lock(self, memory_id, locked):
        if not self._visible_id(memory_id):
            return False, '这条记忆不存在或已经删除。'
        self.sidecar.set_locked(memory_id, bool(locked))
        return True, ('已锁定：他不能改、不能合并、不能归档这条。' if locked else '已解锁。')

    def panel_seen(self, memory_id):
        if isinstance(memory_id, str):
            self.sidecar.seen(memory_id)
        return True, '好。'

    def panel_edit(self, memory_id, changes, expected_sha256=''):
        """Lan's edit from the memory page; uses the same archived trace path as Peter's edits.

        ``expected_sha256`` is checked inside the Brain write transaction, not just before it.
        """
        fields = {key: value for key, value in changes.items() if key in ('title', 'content', 'why_remembered')}
        if (not isinstance(memory_id, str) or not fields or len(fields) != len(changes)
                or any(not isinstance(value, str) or not value.strip() for value in fields.values())):
            return False, '没有可保存的修改。'
        limits = {'title': TITLE_LIMIT, 'content': CONTENT_LIMIT, 'why_remembered': WHY_LIMIT}
        too_long = [key for key, value in fields.items() if len(value) > limits[key]]
        if too_long:
            names = {'title': '标题', 'content': '内容', 'why_remembered': '为什么记'}
            return False, '、'.join(names[key] for key in too_long) + '太长了，请删短一点再保存。'
        if not self._visible_id(memory_id):
            return False, '这条记忆不存在或已经删除。'
        args = {'bucket_id': memory_id, **fields, 'edit_reason': 'Lan edited this memory in the memory page.'}
        if isinstance(expected_sha256, str) and expected_sha256:
            args['expected_sha256'] = expected_sha256
        result, state, _ = self._trace(args, None, by_lan=True)
        return state == '成功', '已保存，旧版本已留档。' if state == '成功' else result

    def panel_delete(self, memory_id):
        """Lan's delete from the memory page; same erase as /delete 编号 确认."""
        if not self._visible_id(memory_id):
            return False, '这条记忆不存在或已经删除。'
        title = self.memory.title_for(memory_id) or memory_id
        failure = self._delete(memory_id, title)
        return (False, '这次没能确认删除结果；这条已经不会再被想起，稍后可以再删一次。') if failure \
            else (True, '已删除。')

    @staticmethod
    def _date_bound(value, *, upper):
        """Parse an optional YYYY-MM-DD bound; None when empty. Raises ValueError when invalid."""
        if value in (None, ''):
            return None
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value.strip()):
            raise ValueError('dates must be YYYY-MM-DD')
        day = datetime.strptime(value.strip(), '%Y-%m-%d')
        return day.replace(hour=23, minute=59, second=59, microsecond=999999) if upper else day

    @staticmethod
    def _created_of(raw):
        """Creation time as written in the memory (local time, offset dropped); None if unreadable."""
        created = parse_frontmatter(raw)[0].get('created')
        if not isinstance(created, str) or not created.strip():
            return None
        try:
            return datetime.fromisoformat(created.strip().replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return None

    def _pinned_ids(self):
        if getattr(self.memory, 'brain_authority', None) is not None:
            from brain_read import BrainRead
            with BrainRead(self.memory.brain_authority.db_path) as reader:
                return set(reader.pinned_ids())
        pinned = set()
        for key, (_, raw) in self._visible_records(include_hidden=True).items():
            if str(parse_frontmatter(raw)[0].get('pinned', '')).casefold() in ('true', '1', 'yes'):
                pinned.add(key)
        return pinned

    def library_stats(self, records=None):
        """Ticket 427: one status line Peter can ask for, replacing Ombre's pulse.

        Counts what Lan may see plus how much is archived, the newest write and the
        on-disk size. It reads the vault directly, so it costs no model call and no Ombre round trip.
        """
        records = self._visible_records() if records is None else records
        events = sum(1 for _, raw in records.values() if 'import_marker:' in raw[:600])
        stamps = []
        for _, raw in records.values():
            stamp = self._created_of(raw)
            if stamp is not None:
                stamps.append(stamp)
        archived = 0
        if getattr(self.memory, 'brain_authority', None) is not None:
            from brain_read import BrainRead
            try:
                with BrainRead(self.memory.brain_authority.db_path) as reader:
                    archived = len(reader.archived(limit=10000))
            except Exception:
                archived = 0
        else:
            vault = role_vault_path(self.memory.store.root, self.memory.cfg.id)
            try:
                for path in (vault / 'archive').rglob('*.md'):
                    if path.is_file() and not path.is_symlink():
                        archived += 1
            except OSError:
                pass
        size = sum(len(raw.encode('utf-8')) for _, raw in records.values())
        newest = max(stamps).strftime('%Y-%m-%d') if stamps else ''
        parts = [f'{len(records)} memories']
        if events:
            parts.append(f'{events} events')
        if archived:
            parts.append(f'{archived} archived')
        if newest:
            parts.append(f'newest {newest}')
        if size:
            parts.append(f'{max(1, round(size / 1024 / 1024))} MB')
        return ', '.join(parts)

    def _catalog(self, args):
        after = args.get('after', '')
        if not isinstance(after, str) or (after and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', after)):
            return 'The memory catalog cursor is invalid. Restart the listing without a cursor.', '被拒绝', 'memory_catalog'
        order = args.get('order', 'id')
        if order not in ('id', 'newest', 'oldest'):
            return 'order must be "id", "newest" or "oldest".', '被拒绝', 'memory_catalog'
        try:
            created_from = self._date_bound(args.get('created_from'), upper=False)
            created_to = self._date_bound(args.get('created_to'), upper=True)
        except ValueError:
            return 'created_from and created_to must be dates written YYYY-MM-DD.', '被拒绝', 'memory_catalog'
        if created_from and created_to and created_from > created_to:
            return 'created_from cannot be later than created_to.', '被拒绝', 'memory_catalog'
        records = self._visible_records()
        stats = self.library_stats(records)
        stamps = {key: self._created_of(raw) for key, (_, raw) in records.items()}
        if created_from or created_to:
            # A memory whose creation time cannot be read cannot prove it is inside the period: leave it out.
            matching = [key for key, stamp in stamps.items() if stamp is not None
                        and (not created_from or stamp >= created_from) and (not created_to or stamp <= created_to)]
        else:
            matching = list(records)
        if order == 'id':
            keys = sorted(matching)
            keys = [key for key in keys if key > after]
        else:
            keys = sorted(matching, key=lambda key: (stamps[key] or datetime.min, key), reverse=order == 'newest')
            if after:
                if after not in keys:
                    return ('The catalog cursor no longer points to a listed memory. '
                            'Restart the listing without a cursor.'), '被拒绝', 'memory_catalog'
                keys = keys[keys.index(after) + 1:]
        page = []
        for key in keys[:CATALOG_PAGE]:
            stamp = stamps[key]
            item = {'memory_id': key, 'title': records[key][0][:80], 'created': stamp.strftime('%Y-%m-%d') if stamp else ''}
            candidate = page + [item]
            result = json.dumps({'stats': stats, 'total': len(matching), 'items': candidate,
                                 'next_after': key if len(keys) > len(candidate) else ''}, ensure_ascii=False)
            if len(result.encode('utf-8')) > self.max_result_bytes:
                break
            page = candidate
        if keys and not page:
            return 'The result limit is too small to return one complete catalog entry.', '出错', 'memory_catalog'
        cursor = page[-1]['memory_id'] if len(keys) > len(page) else ''
        return json.dumps({'stats': stats, 'total': len(matching), 'items': page, 'next_after': cursor}, ensure_ascii=False), '成功', '记忆目录'

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
                # Ticket 188: a memory Peter has just read is a memory he was shown; hold(target_id) may extend it.
                self.memory.mark_surfaced([key])
                return result, '成功', f'{title[:80]}（{key}）'
            if end <= offset:
                return 'The result limit is too small to return the requested memory content.', '出错', 'memory_read'
            end = offset + (end - offset) // 2

    def _timeline(self, args):
        event_id = args.get('event_id')
        if self.events is None:
            return 'The event timeline is not available for this role.', '被拒绝', 'memory_timeline'
        if not isinstance(event_id, str) or not event_id.strip():
            return 'event_id is required.', '被拒绝', 'memory_timeline'
        text = self.events.timeline(event_id.strip(), offset=args.get('offset') or 0,
                                    limit=args.get('limit'))
        if not text:
            return 'No experience with that id; nothing was read.', '被拒绝', event_id[:40]
        rendered, next_offset, total = text
        return rendered + f'\nnext_offset: {next_offset}' + ('' if next_offset is not None else f'\ntotal: {total}'), '成功', event_id[:40]

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
            return 'This memory does not exist, is archived, or is unavailable.', '被拒绝', memory_id
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

    def _trace(self, args, approval, *, by_lan=False):
        memory_id = args.get('bucket_id')
        summary = self.memory.title_for(memory_id) if isinstance(memory_id, str) else None
        banned = ('hard_delete', 'deletion_request_id', 'deletion_decision')
        if any(args.get(key) not in (None, False, '', [], {}) for key in banned):
            return 'This delete operation or argument combination is not available. Use the supported delete flow and its required confirmation.', '被拒绝', str(summary or 'trace')
        restore_requested = args.get('restore') in (1, True)
        if not summary:
            return 'The target memory does not exist, is archived, or is unavailable.', '被拒绝', str(memory_id or 'trace')
        if self.memory.is_hidden(memory_id):
            # P3-3: trace archive=True is reversible by trace restore=True. Tombstoned,
            # deleted or missing memories still cannot be restored through this path.
            if not (restore_requested and self.memory.is_archived(memory_id)):
                return 'The target memory does not exist, is archived, or is unavailable.', '被拒绝', str(memory_id or 'trace')
        delete = args.get('delete', False)
        if type(delete) is not bool:
            return 'delete must be a Boolean.', '被拒绝', summary
        if not by_lan:
            # Ticket 363: a memory Lan locked stays exactly as she left it; reading and reinforcing still work.
            changes = [key for key, value in args.items()
                       if key not in ('bucket_id', 'edit_reason', 'reinforce')
                       and not (value is None or value is False or value == '' or (type(value) is int and value == -1))]
            if changes and self.sidecar.locked(memory_id):
                return ('Lan locked this memory on the memory page, so you cannot change or delete it. '
                        'Save anything new as a separate memory with hold and link_to.'), '被拒绝', summary
            body = args.get('content')
            too_long = self._too_long(body.strip()) if isinstance(body, str) else None
            if too_long:
                return (f'content would be {too_long[0]} characters; the most this model can write in one answer '
                        f'is about {too_long[1]}. Nothing was changed.'), '被拒绝', summary
        edit_reason = args.get('edit_reason', '')
        if not isinstance(edit_reason, str) or len(edit_reason) > WHY_LIMIT:
            return f'edit_reason must be text within the {WHY_LIMIT}-character limit.', '被拒绝', summary
        reason = args.get('delete_reason', '')
        if delete and (not isinstance(reason, str) or not reason.strip() or len(reason) > WHY_LIMIT):
            return f'A delete request must include a reason within the {WHY_LIMIT}-character limit.', '被拒绝', summary
        old = self.memory.raw_content_for(memory_id)
        if old is None:
            return 'The target memory could not be read, so it was not changed.', '被拒绝', summary
        old_str, new_str = args.get('old_str'), args.get('new_str')
        if not by_lan and isinstance(old_str, str) and old_str and isinstance(new_str, str):
            body = (self.memory.content_for(memory_id) or '').replace(old_str, new_str, 1).strip()
            too_long = self._too_long(body)
            if too_long:
                return (f'After that replacement the memory would be {too_long[0]} characters; the most this '
                        f'model can write in one answer is about {too_long[1]}. Nothing was changed.'), '被拒绝', summary
        pin = args.get('pinned')
        if isinstance(pin, str):
            pin = pin.strip().casefold() in ('1', 'true', 'yes')
        elif isinstance(pin, float):
            pin = pin == 1.0
        if pin is True or (type(pin) is int and pin == 1):
            if self.autonomy_mode:
                return 'Pinning is available only while chatting with Lan, not during autonomous activity.', '被拒绝', summary
            already = self._pinned_ids()
            if memory_id not in already and not any(unicodedata.category(ch)[0] not in 'ZC' for ch in edit_reason):
                return ('Pinning needs edit_reason: say why this memory must always surface first.'), '被拒绝', summary
            if memory_id not in already and len(already) >= PINNED_LIMIT:
                return (f'{PINNED_LIMIT} memories are already pinned. Unpin one first '
                        '(trace pinned=0 with importance 1-10) before pinning another.'), '被拒绝', summary
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
        expected_sha = args.get('expected_sha256') if isinstance(args.get('expected_sha256'), str) else ''
        # BrainWriter can compare the hash inside the same write transaction; the Ombre path cannot.
        brain_atomic = bool(expected_sha) and getattr(self.memory, 'brain_authority', None) is not None
        operation_id = None
        version_warning = ''
        try:
            safe_args = {key: value for key, value in args.items()
                         if key not in (*banned, 'edit_reason', 'delete', 'expected_sha256')}
            # The authorized edit path must remain an edit after MCP conversion.
            safe_args['delete'] = False
            if brain_atomic:
                safe_args['expected_sha256'] = expected_sha
                # Keep the exact raw Markdown (title included) so Brain's restore_version
                # can restore the old title too, in the same BEGIN IMMEDIATE transaction.
                safe_args['archive_raw'] = True
            operation_id, duplicate_operation = self.memory.begin_write(safe_args)
            if duplicate_operation is not None:
                return ('This memory update is still unconfirmed after its earlier connection failure. '
                        'It was not sent again; check the memory service before another edit.'), '出错', summary
            if not brain_atomic and not self._save_version(memory_id, args, old):
                self.memory.finish_write(operation_id, 'not_submitted')
                return 'The previous version could not be archived safely, so this change was not made.', '出错', summary
            result = self.memory.call('trace', safe_args, write_operation=operation_id)
            if brain_atomic and not self._save_version(memory_id, args, old):
                version_warning = '（页面旧版本未能写入历史，请稍后核对。）'
        except WriteConflict:
            if operation_id:
                self.memory.finish_write(operation_id, 'not_submitted')
            return ('这段记忆在你编辑期间已被更新，请刷新核对后再保存（你的草稿仍保留）。',
                    '冲突', summary)
        except MemoryToolError as exc:
            suffix = 'Nothing was written.' if brain_atomic else 'The previous version was archived.'
            return f'{exc} {suffix}', '出错', summary
        except Exception:
            suffix = 'Nothing was written.' if brain_atomic else 'The previous version was archived.'
            return f'The memory update failed; {suffix}', '出错', summary
        self.memory.bucket_details_cache.pop(memory_id, None)
        updated = self.memory.content_for(memory_id, refresh=True)
        if updated is not None:
            self.memory.record_native_update(memory_id, updated)
        self.memory.refresh_visibility(memory_id)
        try:
            if by_lan:
                self.sidecar.lan_edited(memory_id)
            elif any(args.get(key) not in (None, '') for key in ('content', 'title', 'old_str', 'new_str')):
                self.sidecar.peter_changed(memory_id)
        except Exception:
            pass
        feedback = (f'{result}{version_warning}\nComplete memory before the change (including metadata) has been saved. '
                    f'Reason for this operation: {edit_reason.strip() or "not provided"}. '
                    'The content field and meaning_append/meaning_replace metadata are separate fields.')
        return self._bounded(feedback), '成功', f'{summary}（旧版本已留档）'

    def _save_version(self, memory_id, args, old, *, operation_reason=None):
        try:
            fields = sorted(key for key in args
                            if key not in {'bucket_id', 'delete_reason', 'edit_reason', 'expected_sha256'})
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
        path = f'{self.memory.cfg.id}/delete-requests.jsonl'
        request = {'request_id': request_id, 'memory_id': memory_id, 'title': summary,
                   'reason': reason.strip(), 'status': 'pending'}
        try:
            # P3-1: the pending marker is the request row itself; Brain no longer has free tags.
            self.memory.store.append(path, request)
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
        try:
            rows = self.memory.store.read(path)
        except Exception:
            # P2-B failure path: a temporarily unavailable Brain ledger must not stop the chat.
            return []
        for row in rows:
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
            if self.memory.is_archived(memory_id):
                # Archived/tombstoned targets cannot be approved for deletion. A request's own
                # pending flag must not count as hidden here, or every autonomous request would
                # be silently rejected before Lan can approve it.
                self.memory.store.append(path, {**row, 'status': 'rejected',
                                                'decision_reason': '目标记忆已归档'})
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
                status = 'rejected'
                note = (f'未同意删除记忆 #{memory_id}「{title}」；申请已关闭。'
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
            return 'The deletion was not confirmed; this memory remains unavailable. Check its state before retrying with /delete.'
        entry.pop('delete_pending', None)
        self.memory.persist()
        self.memory.erase(memory_id)
        return None

    SEARCH_RESULTS_LIMIT = 20

    def _editable(self, memory_id):
        """A memory may be extended only if Peter was shown it: his own search results, or an
        automatic recall (read fresh from disk now). Other IDs, archived or erased ones stay refused."""
        shown = memory_id in self.search_results or memory_id in getattr(self.memory, 'surfaced_ids', {})
        # Visibility is re-read from disk every time: a memory archived after it was shown stays refused.
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

    def _signature(self, kind, args):
        keep = {key: args.get(key) for key in ('content', 'title', 'target_id', 'merge_ids', 'link_to')}
        return kind + hashlib.sha256(json.dumps(keep, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()

    def _confirmed(self, kind, args):
        """True when this exact save was bounced once already: sending it again unchanged confirms it."""
        signature = self._signature(kind, args)
        if signature in self.pending_confirm:
            self.pending_confirm.remove(signature)
            return True
        # Several saves can be bounced in one turn; remember the last few.
        self.pending_confirm = [*self.pending_confirm[-7:], signature]
        return False

    def _related(self, title, content, exclude=()):
        """Visible memories the program finds for a new save, recorded as shown so Peter can pick one."""
        try:
            text = self.memory.call('breath_search', {'query': f'{title}\n{content}'[:1000], 'mode': 'automatic',
                                                      'with_ids': True, 'max_results': 3 + SEARCH_SPARE_HITS})
        except Exception:
            return []  # Not finding candidates must never block a save.
        found = []
        # Ticket 384: same order as automatic recall (381): drop archived/tombstoned hits first, then keep three.
        for key, _ in self.memory.context_visible(ordinary_hits(text, limit=3 + SEARCH_SPARE_HITS))[:3]:
            body = self.memory.content_for(key, refresh=True)
            if key in exclude or body is None or not self.memory.refresh_visibility(key):
                continue
            self.search_results.pop(key, None)
            self.search_results[key] = body
            found.append((key, body))
        while len(self.search_results) > self.SEARCH_RESULTS_LIMIT:
            self.search_results.pop(next(iter(self.search_results)))
        self.memory.mark_surfaced([key for key, _ in found])
        return found

    def _locked_refusal(self, memory_ids):
        locked = [key for key in memory_ids if self.sidecar.locked(key)]
        if locked:
            return ('Lan locked ' + ', '.join(locked) + ' on the memory page, so you cannot change, merge or '
                    'archive it. Save what is new as a separate memory with link_to instead.')
        return None

    def _hold(self, args):
        summary = self.preview_summary('hold', args)
        content, title = args.get('content'), args.get('title')
        target_id, link_to = args.get('target_id'), args.get('link_to')
        why = args.get('why_remembered', '')
        if (not isinstance(content, str) or not content.strip() or not isinstance(why, str)
                or (target_id is None and (not isinstance(title, str) or not title.strip()))):
            return 'To create a memory, provide content and title. To update one, provide content.', '被拒绝', summary
        if target_id is None and args.get('merge_ids'):
            return 'Retrieved memories can be merged only when target_id is explicitly specified.', '被拒绝', summary
        if target_id is not None and link_to is not None:
            return 'Use target_id to rewrite the same event, or link_to for a new memory that continues one; not both.', '被拒绝', summary
        too_long = self._too_long(content.strip())
        if too_long:
            return (f'The memory is {too_long[0]} characters; the most this model can write in one answer is about '
                    f'{too_long[1]}. Save it as a shorter whole memory, or split it into stages with link_to. '
                    'Nothing was saved.'), '被拒绝', summary
        if len(why) > WHY_LIMIT or (isinstance(title, str) and len(title) > TITLE_LIMIT):
            return f'The title or why_remembered is too long. Shorten it and save again.', '被拒绝', summary
        # Ticket 407: pinned/importance limits must run before the event branch creates the projection.
        importance, pinned = args.get('importance'), args.get('pinned')
        if (importance is not None or pinned is not None) and target_id is not None:
            return ('importance and pinned apply only to a new memory. Change an existing one with trace.',
                    '被拒绝', summary)
        if (importance is not None or pinned is not None) and target_id is None:
            if importance is not None and (type(importance) is not int or not 1 <= importance <= 10):
                return 'importance must be a whole number from 1 to 10.', '被拒绝', summary
            if pinned is not None and type(pinned) is not bool:
                return 'pinned must be true or false.', '被拒绝', summary
            if pinned:
                if self.autonomy_mode:
                    return 'Pinning is available only while chatting with Lan, not during autonomous activity.', '被拒绝', summary
                if importance is not None:
                    return 'A pinned memory always has importance 10; do not pass importance with pinned.', '被拒绝', summary
                if not why.strip():
                    return 'A pinned memory needs why_remembered: say why it must always surface first.', '被拒绝', summary
                if len(self._pinned_ids()) >= PINNED_LIMIT:
                    return (f'{PINNED_LIMIT} memories are already pinned. Unpin one with trace first, or save this '
                            'one without pinned.'), '被拒绝', summary
        # Ticket 407: manual target_id/link_to must land on the event, not create a parallel fragment.
        if self.events is not None and target_id is not None:
            event_id = self.events.event_for_memory(target_id)
            if event_id:
                if args.get('merge_ids'):
                    return ('merge_ids cannot be combined with an event story; rewrite the story, then '
                            'archive duplicates on the memory page.'), '被拒绝', summary
                previous = self.memory.content_for(target_id, refresh=True)
                seen = self.search_results.get(target_id) or previous
                new_title = title.strip() if isinstance(title, str) else ''
                if not self.events.revise(event_id, content.strip(), new_title, seen=seen,
                                          why='hold(target_id)'):
                    return ('This experience was changed by Lan or is locked; nothing was rewritten.',
                            '被拒绝', summary)
                return (f'Experience story rewritten (event {event_id}).'), '成功', summary
        if self.events is not None and link_to is not None:
            event_id = self.events.event_for_memory(link_to)
            if event_id:
                if not self._editable(link_to):
                    return ('link_to must be a memory you were shown and can still see; nothing was saved.'), '被拒绝', summary
                try:
                    self.events.record(content.strip(), title.strip() if isinstance(title, str) else 'Event',
                                       why=why, event_id=event_id,
                                       interpretation=str(args.get('interpretation') or '')[:300])
                except Exception:
                    return 'The experience could not be updated; nothing was saved.', '出错', summary
                return (f'Added to experience {event_id}.'), '成功', summary
        # Ticket 407: with the event layer on, a save is a stage of an event, not a memory of its own.
        # target_id/link_to keep the old manual path; a failed event save must NOT fall back to a second
        # hold (the stage may already be on disk), so the old path is never reached once events are on.
        if self.events is not None and target_id is None and link_to is None:
            try:
                saved = self.events.record(content.strip(), title.strip(), why=why,
                                           importance=args.get('importance'), pinned=args.get('pinned'),
                                           interpretation=str(args.get('interpretation') or '')[:300])
            except Exception:
                return ('The event save failed before anything was written; nothing was saved.',
                        '出错', summary)
            if saved:
                if saved.get('pending'):
                    return ('The stage is saved but not yet committed; the program will finish it. '
                            'Do not save it again.'), '出错', summary
                if saved.get('locked'):
                    return ('Lan locked this memory or it is archived, so its story was not rewritten; '
                            'the stage is saved.'), '成功', summary
                if not saved.get('changed'):
                    return ('The stage is saved; the story projection will be retried later.'), '出错', summary
                head = ('Continued the same experience' if saved.get('updated') else 'New experience')
                note = ('' if saved.get('story_source') != 'composed'
                        else ' The story is the full stage text for now; the narrative is tidied on a later save.')
                if saved.get('index_pending'):
                    # P2-A three-state feedback must survive the event layer too.
                    note += ' The text is saved, but its search index is pending and will be retried.'
                return (head + ': 《' + str(saved['title']) + '》 (event ' + str(saved['event_id'])
                        + '). Later parts of the same thing update this one; use memory_timeline to read it '
                          'from the start.' + note), '成功', f"故事：{summary}"
        importance, pinned = args.get('importance'), args.get('pinned')
        if importance is not None or pinned is not None:
            if target_id is not None:
                return 'importance and pinned apply only to a new memory. Change an existing one with trace.', '被拒绝', summary
            if importance is not None and (type(importance) is not int or not 1 <= importance <= 10):
                return 'importance must be a whole number from 1 to 10.', '被拒绝', summary
            if pinned is not None and type(pinned) is not bool:
                return 'pinned must be true or false.', '被拒绝', summary
            if pinned:
                if self.autonomy_mode:
                    return 'Pinning is available only while chatting with Lan, not during autonomous activity.', '被拒绝', summary
                if importance is not None:
                    return 'A pinned memory always has importance 10; do not pass importance with pinned.', '被拒绝', summary
                if not why.strip():
                    return 'A pinned memory needs why_remembered: say why it must always surface first.', '被拒绝', summary
                if len(self._pinned_ids()) >= PINNED_LIMIT:
                    return (f'{PINNED_LIMIT} memories are already pinned. Unpin one with trace first, or save this '
                            'one without pinned.'), '被拒绝', summary
        if target_id is not None:
            return self._rewrite(args, summary)
        if link_to is not None and (not isinstance(link_to, str) or not self._editable(link_to)):
            return ('link_to must be a memory you were shown: an automatic recall, a breath_search result or one the '
                    'program offered. Search again and use its ID.'), '被拒绝', summary
        if link_to is None and not self._confirmed('new', args):
            related = self._related(title.strip(), content.strip())
            if related:
                lines = []
                for key, body in related:
                    shown = body.strip()
                    shown = shown if len(shown) <= 300 else shown[:300] + '...'
                    lines.append(f'[Memory ID: {key} | Title: {self.memory.title_for(key) or key}]\n{shown}')
                return ('Not saved yet. These memories may be about the same thing:\n\n' + '\n---\n'.join(lines) +
                        '\n\nChoose one: the same event -> hold with target_id=<ID> and the whole memory rewritten '
                        '(it replaces the old text; merge_ids archives other copies of it). A later stage of one of '
                        'them -> hold with link_to=<ID>. None of these -> send this same hold again unchanged and '
                        'it is saved as a new memory.'), '待选择', f'先看相关记忆：{summary}'
            self.pending_confirm = [item for item in self.pending_confirm if item != self._signature('new', args)]
        ombre_args = {'content': content.strip(), 'title': title.strip()}
        if why.strip():
            ombre_args['why_remembered'] = why.strip()
        if pinned:
            ombre_args['pinned'] = True
        elif importance is not None:
            ombre_args['importance'] = importance
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
        linked = ''
        try:
            if memory_id:
                self.sidecar.source(memory_id, 'hold')
                if link_to is not None and link_to != memory_id:
                    self.sidecar.link(memory_id, link_to)
                    linked = f'，接着「{self.memory.title_for(link_to) or link_to}」'
        except Exception:
            pass  # The memory itself is saved; a missing note about it must not turn this into a failure.
        return (('Merged into an existing memory.' if kind == '合并' else 'Memory saved.') + f' memory_id: {memory_id}'
                + (f' It continues {link_to}.' if linked else ''),
                '成功', f'{kind}：{summary}{linked}')

    def _rewrite(self, args, summary):
        """hold(target_id): Peter rewrites the whole memory; same-event copies in merge_ids are archived."""
        target_id, content, why = args.get('target_id'), args['content'].strip(), args.get('why_remembered', '')
        title = args.get('title')
        if not isinstance(target_id, str) or not self._editable(target_id):
            return 'target_id must be a memory you were shown: an automatic recall or a breath_search result from this conversation. Search again and use its ID.', '被拒绝', summary
        previous = self.search_results[target_id]
        if not self.memory.visible([(target_id, previous)]):
            return 'This memory is archived or unavailable; no update was made.', '被拒绝', summary
        merge_ids = args.get('merge_ids', [])
        if (not isinstance(merge_ids, list) or any(not isinstance(key, str) for key in merge_ids)
                or len(set(merge_ids)) != len(merge_ids)):
            return 'merge_ids must be a list of distinct IDs of memories you were shown. Search again and use only those IDs.', '被拒绝', summary
        source_ids = [key for key in merge_ids if key != target_id]
        if any(not self._editable(key) for key in source_ids):
            return 'Every merge_id must be a memory you were shown (automatic recall or breath_search in this conversation). Search again before retrying.', '被拒绝', summary
        if (source_ids
                and not self.memory.visible([(key, self.search_results[key]) for key in source_ids])):
            return 'A memory selected for merging is archived or unavailable; no update was made.', '被拒绝', summary
        refusal = self._locked_refusal([target_id, *source_ids])
        if refusal:
            return refusal, '被拒绝', summary
        current = self.memory.content_for(target_id, refresh=True)
        if current != previous:
            self.search_results = {}
            return 'The target changed after search, so it was not updated. Run breath_search again before retrying.', '被拒绝', summary
        for source_id in source_ids:
            if self.memory.content_for(source_id, refresh=True) != self.search_results[source_id]:
                self.search_results = {}
                return 'A source memory changed after search, so nothing was updated. Run breath_search again before retrying.', '被拒绝', summary
        if content == previous.strip() and not source_ids and not (isinstance(title, str) and title.strip()):
            return 'This is already exactly what the memory says; nothing was written.', '成功', f'已存在：{summary}'
        dropped = sorted(dates_in(previous + ''.join(self.search_results[key] for key in source_ids))
                         - dates_in(content))
        if dropped and not self._confirmed('dates', args):
            shown = ', '.join(f'{month}-{day}' for month, day in dropped)
            return (f'Not saved yet. The old text mentions dates your rewrite leaves out: {shown}. If what happened '
                    'then still matters, put it back. If leaving it out is right, send this same hold again '
                    'unchanged.'), '待选择', f'核对日期：{summary}'
        old_document = self.memory.raw_content_for(target_id)
        if old_document is None:
            return 'The complete target memory could not be read, so nothing was changed.', '被拒绝', summary
        if getattr(self.memory, 'brain_authority', None) is None:
            if not self._save_version(target_id, {'content': content, 'why_remembered': why},
                                      old_document, operation_reason='hold(target_id) 重写'):
                return 'The complete previous version could not be safely archived, so nothing was changed.', '出错', summary
        trace_args = {'bucket_id': target_id, 'content': content, 'edit_reason': 'hold(target_id) 重写'}
        if getattr(self.memory, 'brain_authority', None) is not None:
            # P6-S2 review: store the full old Markdown (including front matter) inside the single
            # Brain version row instead of creating a second duplicate version row.
            trace_args['archive_raw'] = True
        if isinstance(title, str) and title.strip():
            trace_args['title'] = title.strip()[:TITLE_LIMIT]
        if why.strip():
            trace_args['why_remembered'] = why.strip()
        self.memory.call('trace', trace_args)
        self.memory.bucket_details_cache.pop(target_id, None)
        stored = self.memory.content_for(target_id, refresh=True)
        if stored is None or stored.strip() != content:
            self.search_results = {}
            return 'The rewrite could not be verified. Check this memory before continuing; its previous version was archived.', '出错', summary
        self.memory.record_native_update(target_id, stored)
        self.search_results[target_id] = stored
        try:
            self.sidecar.source(target_id, 'rewrite')
            self.sidecar.peter_changed(target_id)
        except Exception:
            pass
        archived, failed = [], []
        for source_id in source_ids:
            if self._archive(source_id, target_id, f'并进 {target_id}'):
                archived.append(source_id)
            else:
                failed.append(source_id)
        note = f'，并归档 {len(archived)} 条重复' if archived else ''
        result = ('The memory was rewritten; its previous version was archived and Lan can restore it.'
                  + (f' Archived as copies of it: {", ".join(archived)}.' if archived else '')
                  + (f' Could not archive: {", ".join(failed)}; they are unchanged.' if failed else ''))
        return result, '成功', f'重写：{summary}（{target_id}）{note}'

    def _archive(self, memory_id, into, reason):
        """Move a memory to Ombre's archive (not deleted: Lan restores it on the memory page)."""
        if getattr(self.memory, 'brain_authority', None) is not None:
            try:
                self.memory.call('trace', {'bucket_id': memory_id, 'merge_into': into})
            except Exception:
                return False
            self.memory.bucket_details_cache.pop(memory_id, None)
            self.search_results.pop(memory_id, None)
            try:
                self.sidecar.merged_into(memory_id, into)
            except Exception:
                pass
            return True
        old = self.memory.raw_content_for(memory_id)
        if old is None or not self._save_version(memory_id, {'archived': True}, old, operation_reason=reason):
            return False
        try:
            self.memory.call('trace', {'bucket_id': memory_id, 'delete': True})
        except Exception:
            return False
        self.memory.bucket_details_cache.pop(memory_id, None)
        self.search_results.pop(memory_id, None)
        if self.memory.refresh_visibility(memory_id):
            return False
        try:
            self.sidecar.merged_into(memory_id, into)
        except Exception:
            pass
        return True

    def _search(self, args):
        query = args.get('query')
        if not isinstance(query, str) or not query.strip():
            return 'The search query must be non-empty text.', '被拒绝', 'breath_search'
        ombre_args = {'query': query.strip(), 'mode': 'automatic', 'with_ids': True, 'max_results': 3 + SEARCH_SPARE_HITS}
        try:
            start = self._date_bound(args.get('date_from'), upper=False)
            end = self._date_bound(args.get('date_to'), upper=True)
        except ValueError:
            return 'date_from and date_to must be dates written YYYY-MM-DD.', '被拒绝', 'breath_search'
        if start and end and start > end:
            return 'date_from cannot be later than date_to.', '被拒绝', 'breath_search'
        if start:
            ombre_args['date_from'] = args['date_from'].strip()
        if end:
            ombre_args['date_to'] = args['date_to'].strip()
        text = self.memory.call('breath_search', ombre_args)
        # Ticket 384: Ombre returns archived memories too. Check each hit's current state on disk first, then keep
        # three, so an archived old version neither takes a slot nor is shown to Peter (same order as recall, 381).
        found = self.memory.context_visible(ordinary_hits(text, limit=3 + SEARCH_SPARE_HITS))[:3]
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

    def announce_tool_groups(self, groups):
        announce = getattr(self.files, 'announce_tool_groups', None)
        if announce:
            announce(groups)

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



