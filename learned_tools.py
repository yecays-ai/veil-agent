
SECTION_PARAM = {'type': 'string', 'enum': ['facts', 'growth'],
                 'description': 'facts = important facts about Lan; growth = what you learned from real experience.'}
PROFILE_APPEND = {'type': 'function', 'function': {
    'name': 'profile_append',
    'description': ('Add a passage to your Learned Context. facts holds what you know about Lan; growth holds '
                    'what you learned from real experience, especially after conflicts. Shown every request. '
                    'Direct use; no approval.'),
    'parameters': {'type': 'object', 'properties': {
        'text': {'type': 'string', 'minLength': 1}, 'section': SECTION_PARAM},
        'required': ['text'], 'additionalProperties': False},
}}
PROFILE_REPLACE = {'type': 'function', 'function': {
    'name': 'profile_replace',
    'description': ('Replace one passage in your Learned Context with new text, or remove it with an empty '
                    'new_text. old_text must appear exactly once in that section. Direct use; no approval.'),
    'parameters': {'type': 'object', 'properties': {
        'old_text': {'type': 'string', 'minLength': 1}, 'new_text': {'type': 'string'},
        'section': SECTION_PARAM},
        'required': ['old_text', 'new_text'], 'additionalProperties': False},
}}
ONGOING_TOOL = {'type': 'function', 'function': {
    'name': 'ongoing_update',
    'description': ('Track something that is not finished yet: a promise, a shared plan, an unresolved argument, '
                    'or work you are in the middle of. Before add/update/complete, search recent chat and memory '
                    'for the actual history; keep source ids; if evidence is insufficient use status uncertain. '
                    'Never declare something resolved without evidence. Completed or archived items are not shown '
                    'to you anymore.'),
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['add', 'update', 'complete', 'archive', 'reopen']},
        'id': {'type': 'string', 'description': 'Required for update/complete/archive/reopen.'},
        'text': {'type': 'string', 'description': 'Short current state; required for add.'},
        'sources': {'type': 'array', 'items': {'type': 'string'},
                    'description': 'References such as chat:<message_id>, memory:<id>, long_task:<id>.'},
        'status': {'type': 'string', 'enum': ['active', 'uncertain']},
    }, 'required': ['action'], 'additionalProperties': False},
}}
