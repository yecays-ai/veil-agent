
"""Ticket 394: Lan's own persona, separate from Peter's notes about her."""
import os
import uuid
from pathlib import Path

import prompts
from agent_types import UserError

# Import-time copy for tests only; context() reads the live text so an edit applies to the next request.
FRAME = prompts.text('identity.user_frame')


class UserPersona:
    def __init__(self, persona_dir, settings):
        self.path = Path(persona_dir) / settings['file']
        self.max_bytes = int(settings['max_bytes'])

    def read(self):
        try:
            return self.path.read_text(encoding='utf-8-sig').strip()
        except FileNotFoundError:
            return ''
        except OSError as exc:
            raise UserError(f'读不到 {self.path.name}：{exc}') from exc

    def context(self):
        text = self.read()
        return {'role': 'system', 'content': prompts.text('identity.user_frame') + '\n' + text} if text else None

    def save(self, text):
        if not isinstance(text, str):
            raise UserError('内容必须是文字。')
        if len(text.encode('utf-8')) > self.max_bytes:
            raise UserError(f'关于你（Lan 的人设）超出上限：{len(text.encode("utf-8"))} / {self.max_bytes} 字节。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from veil.persona import persona_versions
        persona_versions.snapshot(self.path)
        temporary = self.path.with_name(self.path.name + f'.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                handle.write(text.rstrip() + ('\n' if text.strip() else ''))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise UserError(f'写 {self.path.name} 失败：{exc}。') from exc
        return self.status()

    def status(self):
        text = self.read()
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = 0
        return {'text': text, 'bytes': len(text.encode('utf-8')), 'max_bytes': self.max_bytes,
                'mtime': mtime, 'path': str(self.path)}
