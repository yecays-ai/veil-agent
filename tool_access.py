"""One table for what each tool does to Lan's things.

Three places used to keep their own lists (long tasks: read-only and side-effect; delegated investigations:
read-only). They are now derived from this table, so a new tool is classified once.

Flags: r = read-only inside a long task, w = has side effects inside a long task, d = a delegated investigation
may use it. This table decides nothing about permission: asking, allowing and refusing stay in tools_agent.tool_policy
and in each tool.
"""

TOOL_ACCESS = {
    'anchor': 'w',
    'append_file': 'w',
    'breath_search': 'rd',
    'create_directory': 'w',
    'delete_file': 'w',
    'directory_tree': 'rd',
    'dream': 'r',
    'edit_file': 'w',
    'get_file_info': 'rd',
    'github_read': 'd',
    'grant_append': 'w',
    'hold': 'w',
    'lab_file_digest': 'r',
    'letter_lock_update': 'w',
    'letter_read': 'r',
    'letter_write': 'w',
    'list_directory': 'rd',
    'list_directory_with_sizes': 'rd',
    'mcp__galatea_garden__get_chat_messages': 'd',
    'mcp__galatea_garden__get_game_summary': 'd',
    'mcp__galatea_garden__get_machine': 'd',
    'mcp__galatea_garden__get_my_status': 'd',
    'mcp__galatea_garden__get_self': 'd',
    'mcp__galatea_garden__get_thread': 'd',
    'mcp__galatea_garden__get_tool_schema': 'd',
    'mcp__galatea_garden__list_activity': 'd',
    'mcp__galatea_garden__list_games': 'd',
    'mcp__galatea_garden__list_threads': 'd',
    'mcp__github__github_read': 'd',
    'memory_catalog': 'rd',
    'memory_history': 'rd',
    'memory_read': 'rd',
    'move_lab_file': 'w',
    'plan': 'w',
    'pulse': 'r',
    'read_multiple_files': 'rd',
    'read_text_file': 'rd',
    'release': 'w',
    'run_lab_python': 'w',
    'search_chat_history': 'd',
    'search_files': 'rd',
    'trace': 'w',
    'web_fetch': 'd',
    'web_research': 'd',
    'web_search': 'd',
    'windows_discover_apps': 'r',
    'write_file': 'w',
}


def tools_with(flag):
    return frozenset(name for name, flags in TOOL_ACCESS.items() if flag in flags)
