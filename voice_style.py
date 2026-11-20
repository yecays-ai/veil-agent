"""Ticket 355: Peter's own "how I talk" reference, kept in a local file next to the persona.

The file is written by Lan (rules, then short sample exchanges). The program adds no wording of its own
about his personality: it only frames the file, strips speaker labels, and places it right after the
persona so every model sees the same reference. It can be switched off and edited from the desktop.
"""
import json
import os
import re
import uuid
from pathlib import Path

import prompts
from agent_types import UserError

MAX_CHARS = 8000   # the file is sent on every request; a longer one is refused on save and cut on read
SWITCH = 'voice-style.json'
SPEAKERS = ('Lan', 'user', 'assistant')  # plus the role's own name
# Import-time copy for tests only; context() reads the live text so an edit applies to the next request.
FRAME = prompts.text('voice.frame')


def voice_path(persona_path):
    persona_path = Path(persona_path)
    return persona_path.with_name(persona_path.stem + '.voice.md')


def clean(text, names=()):
    """Normalise line endings, drop speaker labels in front of bubbles, collapse runs of blank lines."""
    label = re.compile(r'^\s*(?:' + '|'.join(re.escape(name) for name in (*names, *SPEAKERS) if name)
                       + r')\s*[:：]\s*', re.IGNORECASE)
    lines, blank = [], False
    for raw in str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = raw.rstrip()
        if not line.strip():
            blank = bool(lines)
            continue
        if blank:
            lines.append('')
            blank = False
        if line.lstrip().startswith('>'):
            line = '> ' + label.sub('', line.lstrip()[1:].strip())
        elif not line.lstrip().startswith('#'):
            line = label.sub('', line)
        lines.append(line)
    return '\n'.join(lines).strip()


def sample_count(text):
    """Moments in the samples part: blocks that contain at least one line from Lan."""
    return sum(any(line.startswith('>') for line in block.split('\n'))
               for block in re.split(r'\n\s*\n', text or ''))


class VoiceStyle:
    def __init__(self, store, role_id, path, name=''):
        self.store = store
        self.names = (name,) if name else ()
        self.path = Path(path) if path else None
        self.switch_path = f'{role_id}/{SWITCH}'

    def enabled(self):
        try:
            saved = json.loads((self.store.root / self.switch_path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return True
        return saved.get('enabled') is not False if isinstance(saved, dict) else True

    def set_enabled(self, enabled):
        self.store.replace(self.switch_path, {'enabled': bool(enabled)})
        return self.enabled()

    def text(self):
        """The cleaned file, or '' when it is missing, unreadable or empty."""
        if self.path is None:
            return ''
        try:
            return clean(self.path.read_text(encoding='utf-8-sig'), self.names)
        except (OSError, UnicodeDecodeError):
            return ''

    def save(self, text):
        if self.path is None:
            raise UserError('这个角色没有本机人设文件，无法保存说话样例。')
        if not isinstance(text, str):
            raise UserError('说话样例必须是文字。')
        cleaned = clean(text, self.names)
        if len(cleaned) > MAX_CHARS:
            raise UserError(f'说话样例太长（{len(cleaned)} 字），最多 {MAX_CHARS} 字；它每次请求都会带上。')
        import persona_versions
        persona_versions.snapshot(self.path)  # keep the version being replaced; never raises
        temp = self.path.with_name(f'{self.path.name}.{uuid.uuid4().hex}.tmp')  # one per save: two saves never share it
        try:
            temp.write_text(cleaned + ('\n' if cleaned else ''), encoding='utf-8')
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
        return self.status()

    def context(self):
        """System message placed after the persona, or None when off or empty."""
        if not self.enabled():
            return None
        text = self.text()
        if not text:
            return None
        return {'role': 'system', 'content': prompts.text('voice.frame') + '\n\n' + text[:MAX_CHARS]}

    def status(self, with_text=False):
        text = self.text()
        result = {'available': self.path is not None, 'enabled': self.enabled(), 'characters': len(text),
                  'samples': sample_count(text), 'limit': MAX_CHARS}
        if with_text:
            result['text'] = text
        return result
