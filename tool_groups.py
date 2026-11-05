"""Ticket 216: abilities Peter can open when he wants them, so every request does not carry all of their tool text.

Opening a group only adds its tools to the list offered to the model; tool permissions, approvals and budgets are
unchanged. Groups come from the role's `[tools] tool_groups`; a role without that setting offers everything as before.
"""

GROUP_TOOL_NAMES = {
    'web': frozenset({'web_search', 'web_fetch', 'browser'}),
    'github': frozenset({'github_read'}),
    'social': frozenset({'xiaohongshu'}),
    'desktop_control': frozenset({'windows_launch', 'windows_manage', 'windows_control_action',
                                  'windows_canvas_action', 'desktop_icons_arrange', 'desktop_icons_restore'}),
}
# Built-in adapters (GitHub, Xiaohongshu) reach the model as mcp__<service>__<tool>.
GROUP_TOOL_PREFIXES = {
    'github': ('mcp__github__',),
    'social': ('mcp__galatea_', 'mcp__nowhere__', 'mcp__xiaohongshu__'),
}
GROUP_SUMMARIES = {
    'web': 'web search, reading pages, browsing',
    'github': 'reading the GitHub project',
    'social': 'Xiaohongshu, the Galatea garden forum and drift bottle, Nowhere travel',
    'desktop_control': 'launching apps, clicking, drawing, managing windows',
}
TOOL_GROUPS = tuple(GROUP_SUMMARIES)
# A group stays open for the request that opened it and this many later chat requests; an
# autonomous wake keeps it until that wake ends.
CHAT_WARM_REQUESTS = 3
EXPAND_TOOL_NAME = 'expand_capability'


def group_of(name):
    """Canonical tool name -> group name, or None when the tool is always offered."""
    if not isinstance(name, str):
        return None
    for group, names in GROUP_TOOL_NAMES.items():
        if name in names:
            return group
    for group, prefixes in GROUP_TOOL_PREFIXES.items():
        if name.startswith(prefixes):
            return group
    return None


def expand_schema(hidden):
    listing = '; '.join(f'{group} ({GROUP_SUMMARIES[group]})' for group in hidden)
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
