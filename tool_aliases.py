"""Role-scoped names exposed to the model for the existing tool implementations."""
import copy
import re


TOOL_NAME = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


class AliasSchemas(list):
    def __init__(self, values, aliases):
        super().__init__(values)
        self.aliases = dict(aliases)


def validate_tool_aliases(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('tool_aliases must be a table of tool names to aliases')
    aliases = {}
    for original, alias in value.items():
        if (not isinstance(original, str) or not TOOL_NAME.fullmatch(original)
                or not isinstance(alias, str) or not TOOL_NAME.fullmatch(alias)):
            raise ValueError('tool aliases must use valid function names')
        if original == alias:
            raise ValueError('a tool alias must differ from its original name')
        aliases[original] = alias
    if len(set(aliases.values())) != len(aliases):
        raise ValueError('tool aliases must be unique within a role')
    if set(aliases.values()) & set(aliases):
        raise ValueError('tool aliases cannot conflict with an original tool name')
    return aliases


def _replace_tool_references(value, aliases):
    if isinstance(value, str):
        for original, alias in sorted(aliases.items(), key=lambda item: -len(item[0])):
            value = re.sub(r'(?<![A-Za-z0-9_/-])(?<!\.[A-Za-z0-9])' + re.escape(original) +
                           r'(?![A-Za-z0-9_-]|\.[A-Za-z0-9])', lambda _: alias, value)
        return value
    if isinstance(value, list):
        return [_replace_tool_references(item, aliases) for item in value]
    if isinstance(value, dict):
        return {key: _replace_tool_references(item, aliases) for key, item in value.items()}
    return value


def _rewrite_descriptions(value, aliases):
    if isinstance(value, list):
        return [_rewrite_descriptions(item, aliases) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == 'description' and isinstance(item, str):
            result[key] = _replace_tool_references(item, aliases)
        else:
            result[key] = _rewrite_descriptions(item, aliases)
    return result


def alias_tool_schemas(schemas, aliases, reserved_names=()):
    """Rename only schemas actually offered and align descriptions and parameter hints."""
    if not aliases:
        return schemas, {}
    schemas = copy.deepcopy(schemas)
    original_names = [schema.get('function', {}).get('name') for schema in schemas]
    original_names = [name for name in original_names if isinstance(name, str)]
    active = {}
    for name in original_names:
        alias_key = name
        if name.startswith('mcp__'):
            pieces = name.split('__', 2)
            if len(pieces) == 3 and pieces[2] in aliases:
                alias_key = pieces[2]
        if alias_key in aliases:
            candidate = aliases[alias_key]
            # MCP tools from separate services may share a short name. Keep
            # subsequent schemas under their service-qualified names.
            if (candidate not in active.values()
                    and candidate not in set(original_names) | set(reserved_names)):
                active[name] = candidate
    for index, schema in enumerate(schemas):
        schema = _rewrite_descriptions(schema, aliases)
        schemas[index] = schema
        function = schema.get('function')
        if isinstance(function, dict):
            name = function.get('name')
            if name in active:
                function['name'] = active[name]
    return AliasSchemas(schemas, active), active


def resolve_tool_name(name, aliases, active_aliases, available_names):
    """Map a model-facing alias only while its underlying schema is offered."""
    reverse = {alias: original for original, alias in active_aliases.items()}
    if name in reverse:
        original = reverse[name]
        return original if original in available_names else None
    if name in active_aliases or (name in aliases and name not in available_names):
        # Do not let an aliased implementation remain callable by its old name.
        return None
    return name if name in available_names else None


def alias_system_messages(messages, aliases):
    """Rewrite program-provided tool references without editing stored chat history."""
    if not aliases:
        return messages
    output = []
    for index, message in enumerate(messages):
        # The first system message is the character persona; keep its source and wording intact.
        if (index > 0 and message.get('role') == 'system'
                and isinstance(message.get('content'), str)):
            item = dict(message)
            item['content'] = _replace_tool_references(item['content'], aliases)
            output.append(item)
        else:
            output.append(message)
    return output
