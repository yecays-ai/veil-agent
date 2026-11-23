
SECTION_PARAM = {'type': 'string', 'enum': ['facts', 'growth', 'relationship'],
                 'description': ('facts = important facts about Lan; growth = what you learned from real '
                                 'experience; relationship = what is currently true between you two '
                                 '(see status and sources).')}
RELATIONSHIP_STATUS = {'type': 'string', 'enum': ['confirmed', 'uncertain'],
                       'description': ('relationship only: confirmed when she said it or real events keep '
                                       'showing it; uncertain when it is still your own guess. confirmed '
                                       'only takes effect with a source the program can check.')}
RELATIONSHIP_SOURCES = {'type': 'array', 'items': {'type': 'string'},
                        'description': ('relationship only: evidence references, e.g. chat:<message_id> '
                                        'from her actual words, memory:<id> or event:<id>. Without one, '
                                        'the item is saved as your own uncertain reading.')}
PROFILE_APPEND = {'type': 'function', 'function': {
    'name': 'profile_append',
    'description': ('Add a passage to your Learned Context. facts holds what you know about Lan; growth holds '
                    'what you learned from real experience, especially after conflicts. relationship holds '
                    'what is currently true between you two: habits, boundaries, agreements. Shown every '
                    'request. Direct use; no approval.'),
    'parameters': {'type': 'object', 'properties': {
        'text': {'type': 'string', 'minLength': 1}, 'section': SECTION_PARAM,
        'status': RELATIONSHIP_STATUS, 'sources': RELATIONSHIP_SOURCES},
        'required': ['text'], 'additionalProperties': False},
}}
PROFILE_REPLACE = {'type': 'function', 'function': {
    'name': 'profile_replace',
    'description': ('Replace one passage in your Learned Context with new text, or remove it with an empty '
                    'new_text. old_text must appear exactly once in that section. In relationship the old '
                    'note is kept as history (no longer current), so a change of understanding is visible '
                    'without the old rule still applying. Direct use; no approval.'),
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
