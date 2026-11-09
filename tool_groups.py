"""Ticket 216: abilities Peter can open when he wants them, so every request does not carry all of their tool text.

Opening a group only adds its tools to the list offered to the model; tool permissions, approvals and budgets are
unchanged. Groups come from the role's `[tools] tool_groups`; a role without that setting offers everything as before.
"""

GROUP_TOOL_NAMES = {
    'web': frozenset({'web_search', 'web_fetch', 'web_research', 'browser'}),
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
    'desktop_control': 'launching apps, clicking, drawing, managing windows, arranging desktop icons',
}
TOOL_GROUPS = tuple(GROUP_SUMMARIES)
# Ticket 334: with the host path on, these chat tools are hidden because the workbench does the same jobs behind
# VEIL's single checkpoint: the foreground/background browser and the desktop actions. Read-only looking at the
# screen, search and page reading stay in chat. Hidden, not deleted: `host_keep_old_tools` brings them back.
HOST_PATH_HIDDEN_BROWSER = frozenset({'browser'})
HOST_PATH_HIDDEN_DESKTOP = frozenset({'windows_discover_apps', 'windows_launch', 'windows_manage',
                                      'windows_control_action', 'windows_canvas_action',
                                      'desktop_icons_arrange', 'desktop_icons_restore'})
HOST_PATH_HIDDEN = HOST_PATH_HIDDEN_BROWSER | HOST_PATH_HIDDEN_DESKTOP


def host_hidden_names(browser, desktop):
    # Only what the host path really offers on this computer is handed over; the rest stays as it was.
    return (HOST_PATH_HIDDEN_BROWSER if browser else frozenset()) | (
        HOST_PATH_HIDDEN_DESKTOP if desktop else frozenset())

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


def host_handover_note(hidden):
    """One stable sentence for the system prompt: the tools described above that this turn does not offer, and where
    their jobs went. Names come from what is really hidden, so the text never claims more than the list."""
    names = ', '.join(sorted(hidden))
    return ('In this chat the workbench tool does the browsing and Windows app work: ' + names + ' are not in your '
            'tool list, so hand those jobs to the workbench (it opens apps, looks, clicks and types, and browses '
            'in a background browser). Looking at the screen, web_search and web_fetch stay as they are.')
