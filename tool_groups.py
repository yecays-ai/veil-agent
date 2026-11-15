"""Tickets 216 and 357: one table says, for every tool, whether each request carries its text or Peter opens it.

Opening a group only adds its tools to the list offered to the model; tool permissions, approvals and budgets are
unchanged. Groups come from the role's `[tools] tool_groups`; a role without that setting offers everything as before.

Adding a tool (ticket 357): put its name in ALWAYS_ON or in one group below. New tools belong in a group unless
about a tenth of recent requests use them. A tool this table does not know lands in `other`, and a new MCP service
becomes its own group, so nothing new is carried on every request by accident. `tests/test_tool_budget.py` fails when
a tool the program defines is missing here or the text carried on every request outgrows BUDGET.
"""

ALWAYS_ON = frozenset({
    # talking to Lan
    'send_message', 'recall_message', 'card_action', 'stay_silent', 'reply_later', 'stickers', 'screen_pop',
    # memory he uses in most conversations
    'hold', 'breath_search', 'memory_read', 'search_chat_history', 'thought', 'life_record',
    'profile_append', 'profile_replace', 'capability_history', 'autonomy_history',
    # his own files (lab, notes)
    'list_directory', 'list_directory_with_sizes', 'directory_tree', 'search_files', 'get_file_info',
    'read_text_file', 'read_multiple_files', 'write_file', 'append_file', 'grant_append', 'create_directory',
    'move_lab_file', 'edit_file', 'delete_file', 'lab_file_digest', 'preview_lab_page', 'open_lab_game',
    'run_lab_python',
    # looking at the screen (read-only) and finding apps
    'windows_foreground', 'windows_list', 'windows_observe', 'windows_discover_apps',
    # wakes and long tasks: a running long task must always be able to report progress
    'set_next_wake', 'set_wake_rhythm', 'long_task_status', 'long_task_update',
    # plumbing
    'expand_capability', 'mcp_status',
})

GROUP_TOOL_NAMES = {
    'web': frozenset({'web_search', 'web_fetch', 'web_research', 'browser'}),
    'github': frozenset({'github_read'}),
    'desktop_control': frozenset({'windows_launch', 'windows_manage', 'windows_control_action',
                                  'windows_canvas_action', 'desktop_icons_arrange', 'desktop_icons_restore'}),
    'memory_admin': frozenset({'trace', 'dream', 'letter_write', 'letter_read', 'letter_lock_update', 'anchor',
                               'release', 'plan', 'pulse', 'I', 'memory_history', 'memory_catalog'}),
    'build': frozenset({'workbench', 'maintenance', 'delegate'}),
    'desktop_files': frozenset({'desktop_list', 'desktop_find_file', 'desktop_read_text_file', 'desktop_read_image',
                                'desktop_create_file', 'desktop_move_to_trash', 'desktop_icons_read'}),
    'garden_forum': frozenset('mcp__galatea_garden__' + name for name in (
        'get_thread', 'list_threads', 'create_thread', 'create_reply', 'delete_reply', 'list_notifications',
        'list_activity', 'get_self', 'get_tool_schema')),
    'xiaohongshu': frozenset({'xiaohongshu'}),
}
# Built-in adapters and MCP services reach the model as mcp__<service>__<tool>. The longest prefix wins.
GROUP_TOOL_PREFIXES = {
    'github': ('mcp__github__',),
    'garden_play': ('mcp__galatea_garden__nostos_',),
    'garden_more': ('mcp__galatea_',),
    'nowhere': ('mcp__nowhere__',),
    'xiaohongshu': ('mcp__xiaohongshu__',),
}
GROUP_SUMMARIES = {
    'web': 'web search, reading pages, browsing',
    'github': 'reading the GitHub project',
    'desktop_control': 'launching apps, clicking, drawing, managing windows, arranging desktop icons',
    'memory_admin': 'editing or removing a memory (trace), dream review, letters, plans, your self-notes (I), '
                    'browsing the memory catalog and history',
    'build': 'the workbench for running code and commands, maintaining your own code, delegating research',
    'desktop_files': 'finding, reading, creating and trashing files on the Windows desktop',
    'garden_forum': 'the Galatea garden forum: threads, replies, notifications',
    'garden_play': 'the Nostos game in the garden',
    'garden_more': 'the rest of the garden: drift bottles, other games, profile and avatar',
    'nowhere': 'Nowhere travel',
    'xiaohongshu': 'Xiaohongshu',
    'other': 'newer abilities that have not been sorted into a group yet',
}
# Ticket 216's single `social` group, kept so a role can go back to it by listing "social" instead of its parts.
SOCIAL_PARTS = ('garden_forum', 'garden_play', 'garden_more', 'nowhere', 'xiaohongshu')
LEGACY_SUMMARIES = {'social': 'Xiaohongshu, the Galatea garden forum and drift bottle, Nowhere travel'}
TOOL_GROUPS = tuple(GROUP_SUMMARIES) + tuple(LEGACY_SUMMARIES)
# A group stays open for the request that opened it and this many later chat requests; an
# autonomous wake keeps it until that wake ends.
CHAT_WARM_REQUESTS = 3
EXPAND_TOOL_NAME = 'expand_capability'
SERVICE_GROUP_PREFIX = 'mcp_'
# Ticket 357 budget, in bytes of tool definitions as sent. Draft numbers until the full list has been measured on
# Lan's computer; OVERSIZE_EXEMPT lists the tools that were already larger and takes no new members.
BUDGET = {'always_on': 36000, 'tool': 1500, 'group': 12000}
OVERSIZE_EXEMPT = frozenset({'hold', 'trace', 'long_task_update', 'screen_pop', 'workbench', 'maintenance',
                             'delegate', 'windows_canvas_action'})
REGROUP_FLAG = 'tool_groups_regrouped_357'


def group_of(name):
    """Canonical tool name -> group name, or None when the tool is always offered."""
    if not isinstance(name, str):
        return None
    if name in ALWAYS_ON:
        return None
    for group, names in GROUP_TOOL_NAMES.items():
        if name in names:
            return group
    best = None
    for group, prefixes in GROUP_TOOL_PREFIXES.items():
        for prefix in prefixes:
            if name.startswith(prefix) and (best is None or len(prefix) > len(best[1])):
                best = (group, prefix)
    if best:
        return best[0]
    if name.startswith('mcp__') and name.count('__') >= 2:
        return SERVICE_GROUP_PREFIX + name.split('__')[1]
    return 'other'


def effective_group(name, configured):
    """The group that hides `name` for a role configured with these groups, or None when it is always offered."""
    group = group_of(name)
    if group is None or not configured:
        return None
    if group in configured:
        return group
    if group in SOCIAL_PARTS and 'social' in configured:
        return 'social'
    # A role that lists `other` also keeps services this table has never heard of out of every request.
    if group.startswith(SERVICE_GROUP_PREFIX) and 'other' in configured:
        return group
    return None


def groups_named(requested, configured):
    """Group names Peter asked to open -> the ones this role has. `social` still opens all of its parts."""
    opened = []
    for item in requested:
        parts = [item]
        if item == 'social' and 'social' not in configured:
            parts = [part for part in SOCIAL_PARTS if part in configured]
        for part in parts:
            if ((part in configured or part.startswith(SERVICE_GROUP_PREFIX) and 'other' in configured)
                    and part not in opened):
                opened.append(part)
    return opened


def summary_of(group):
    if group in GROUP_SUMMARIES:
        return GROUP_SUMMARIES[group]
    if group in LEGACY_SUMMARIES:
        return LEGACY_SUMMARIES[group]
    return f'tools from the {group[len(SERVICE_GROUP_PREFIX):]} service'


def expand_schema(hidden):
    listing = '; '.join(f'{group} ({summary_of(group)})' for group in hidden)
    return {'type': 'function', 'function': {
        'name': EXPAND_TOOL_NAME,
        'description': ('Open more of your abilities for now. Groups: ' + listing + '. Their tools can be used '
                        'from your next step. Permissions and approvals stay the same.'),
        'parameters': {'type': 'object', 'properties': {
            'groups': {'type': 'array', 'items': {'type': 'string', 'enum': list(hidden)}, 'minItems': 1},
            'reason': {'type': 'string', 'description': 'Optional, for yourself.'},
        }, 'required': ['groups'], 'additionalProperties': False}}}


def announce_text(groups):
    listing = ', '.join(groups)
    return ('Capability update: your tools are now grouped. Some abilities (' + listing + ') are not in your tool '
            'list until you open them with expand_capability, which lists the groups. Nothing was removed and '
            'permissions are unchanged. An opened group stays available for the rest of an autonomous wake, or for '
            'a few more chat turns.')


def regroup_text(groups):
    """One-time notice for ticket 357, or None for a role that keeps the earlier grouping."""
    moved = [group for group in ('memory_admin', 'build', 'desktop_files') if group in groups]
    split = [group for group in SOCIAL_PARTS if group in groups]
    if not moved and not split:
        return None
    parts = []
    if moved:
        parts.append('some abilities you rarely use moved out of your everyday tool list into groups you open with '
                     'expand_capability: ' + '; '.join(f'{group} ({summary_of(group)})' for group in moved))
    if split:
        parts.append('the old social group is now ' + ', '.join(split) + ', so you open only the part you want')
    return ('Capability update: ' + '. Also, '.join(parts) + '. Nothing was removed and permissions are unchanged. '
            'Saving, searching and reading memories, your lab files, messages and screen viewing stay in your '
            'list as before.')


def inventory(schemas, configured, aliases=None):
    """Every tool definition with its size and where it sits, for the context page and the budget check."""
    import json
    aliases = aliases or {}
    rows, always_on, group_bytes = [], 0, {}
    for schema in schemas:
        name = schema['function']['name']
        size = len(json.dumps(schema, ensure_ascii=False).encode('utf-8'))
        group = effective_group(name, configured)
        rows.append({'name': aliases.get(name, name), 'tool': name, 'bytes': size, 'group': group})
        if group is None:
            always_on += size
        else:
            group_bytes[group] = group_bytes.get(group, 0) + size
    over = []
    if always_on > BUDGET['always_on']:
        over.append({'kind': 'always_on', 'bytes': always_on, 'limit': BUDGET['always_on']})
    over.extend({'kind': 'group', 'group': group, 'bytes': size, 'limit': BUDGET['group']}
                for group, size in sorted(group_bytes.items()) if size > BUDGET['group'])
    rows.sort(key=lambda row: (row['group'] is not None, row['group'] or '', -row['bytes']))
    return {'rows': rows, 'always_on_bytes': always_on, 'group_bytes': group_bytes, 'budget': dict(BUDGET),
            'over': over}
