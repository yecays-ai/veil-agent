
SECTION_PARAM = {'type': 'string', 'enum': ['facts', 'growth', 'relationship'],
                 'description': ('facts = stable facts/preferences about Lan; '
                                 'growth = Peter\'s own lessons/reflections from experience; '
                                 'relationship = shared agreements, boundaries, habits between both.')}
RELATIONSHIP_STATUS = {'type': 'string', 'enum': ['confirmed', 'uncertain'],
                       'description': ('relationship only: confirmed requires a verifiable user '
                                       'speech source (chat:<id> or event:/memory: tracing to her words). '
                                       'An independent background check may be needed; do not claim confirmation while pending. '
                                       'Use uncertain for your own unconfirmed reading.')}
RELATIONSHIP_SOURCES = {'type': 'array', 'items': {'type': 'string'},
                        'description': ('relationship only: evidence refs like chat:<message_id>, '
                                        'event:<id>, or memory:<id> tracing to Lan\'s words. '
                                        'Without verifiable user evidence it stays uncertain.')}
PROFILE_APPEND = {'type': 'function', 'function': {
    'name': 'profile_append',
    'description': ('Add a passage to Learned Context (independent section budgets). facts = about Lan; '
                    'growth = your own lessons; relationship = shared agreements/boundaries. Use '
                    'profile_replace to revise existing notes instead of duplicating. Direct use.'),
    'parameters': {'type': 'object', 'properties': {
        'text': {'type': 'string', 'minLength': 1}, 'section': SECTION_PARAM,
        'status': RELATIONSHIP_STATUS, 'sources': RELATIONSHIP_SOURCES},
        'required': ['text'], 'additionalProperties': False},
}}
PROFILE_REPLACE = {'type': 'function', 'function': {
    'name': 'profile_replace',
    'description': ('Revise or retire a passage in Learned Context (empty new_text retires). '
                    'old_text must match once in that section. In relationship, old notes stay as '
                    'outdated history; Lan-locked items cannot be modified. Direct use.'),
    'parameters': {'type': 'object', 'properties': {
        'old_text': {'type': 'string', 'minLength': 1}, 'new_text': {'type': 'string'},
        'section': SECTION_PARAM, 'status': RELATIONSHIP_STATUS, 'sources': RELATIONSHIP_SOURCES},
        'required': ['old_text', 'new_text'], 'additionalProperties': False},
}}
ONGOING_TOOL = {'type': 'function', 'function': {
    'name': 'ongoing_update',
    'description': ('Track something that is not finished yet: a promise, a shared plan, an unresolved argument, '
                    'or work you are in the middle of. Ongoing items reach you every turn, so keep the text '
                    'short and current; you maintain them yourself and may edit or finish them when the chat '
                    'shows the state changed. Before add/update/complete, search recent chat and memory for the '
                    'actual history; keep source ids; if evidence is insufficient use status uncertain. When '
                    'you complete something, say in sources what showed it was over (her tone counts as '
                    'evidence). Finished items are no longer sent to you: you only see how many there are, and '
                    'action list returns them with the completion time. Archive was removed; use complete.'),
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['add', 'update', 'complete', 'reopen', 'list']},
        'id': {'type': 'string', 'description': 'Required for update/complete/reopen.'},
        'text': {'type': 'string', 'description': 'Short current state; required for add.'},
        'sources': {'type': 'array', 'items': {'type': 'string'},
                    'description': 'References such as chat:<message_id>, memory:<id>, long_task:<id>.'},
        'status': {'type': 'string', 'enum': ['active', 'uncertain', 'completed', 'all'],
                   'description': 'For update: active or uncertain. For list: which items to show (default completed).'},
    }, 'required': ['action'], 'additionalProperties': False},
}}
