"""Ticket 402: tool descriptions are a class of instruction, managed as a class.

Every tool the role can call carries a description that reaches the model on every request. There are
dozens of them and they change whenever a tool is added, so they are not hand-registered one by one:
the page reads the tools this build actually offers, and each description can be overridden by name in
persona/instructions/tool.<name>.md. A new tool therefore appears in the page with no extra work.
"""
import os
import re
import threading
import uuid
from pathlib import Path

from agent_types import UserError

NAME = re.compile(r'^[A-Za-z0-9_]{1,80}$')
MAX_BYTES = 4000


class ToolText:
    def __init__(self, overrides_dir):
        self.dir = Path(overrides_dir)
        self.lock = threading.RLock()
        self._cache = {}
        self._stamp = None

    def _stamp_now(self):
        try:
            return tuple(sorted((item.name, item.stat().st_mtime_ns) for item in self.dir.glob('tool.*.md')))
        except OSError:
            return None

    def all(self):
        stamp = self._stamp_now()
        with self.lock:
            if stamp == self._stamp:
                return dict(self._cache)
            found = {}
            try:
                paths = sorted(self.dir.glob('tool.*.md'))
            except OSError:
                paths = []
            for path in paths:
                name = path.name[5:-3]
                if not NAME.match(name):
                    continue
                try:
                    text = path.read_text(encoding='utf-8').rstrip('\n')
                except (OSError, UnicodeDecodeError):
                    continue
                if text.strip():
                    found[name] = text
            self._cache = found
            self._stamp = stamp
            return dict(found)

    def text(self, name, default):
        return self.all().get(name) or default

    def save(self, name, text):
        if not isinstance(name, str) or not NAME.match(name):
            raise UserError('工具名无效。')
        if not isinstance(text, str):
            raise UserError('内容必须是文字。')
        data = text.replace('\r\n', '\n').rstrip('\n')
        if not data.strip():
            raise UserError('工具说明不能是空的。')
        size = len(data.encode('utf-8'))
        if size > MAX_BYTES:
            raise UserError('一条工具说明最多 ' + str(MAX_BYTES) + ' 字节，现在 ' + str(size) + ' 字节。')
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            target = self.dir / ('tool.' + name + '.md')
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                    handle.write(data + '\n')
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            except OSError as exc:
                try:
                    temporary.unlink()
                except OSError:
                    pass
                raise UserError('写工具说明失败：' + str(exc) + '。') from exc
            self._stamp = None  # the directory changed; the next all() re-reads every file
        return {'name': name, 'text': self.text(name, ''), 'bytes': size}

    def status(self, name, default):
        overridden = name in self.all()
        text = self.all().get(name) or default
        return {'text': text, 'overridden': overridden, 'bytes': len(text.encode('utf-8'))}
