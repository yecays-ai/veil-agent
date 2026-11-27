"""Ticket 400: the default text and Lan's override for one instruction entry.

Defaults live in the repository and change with git. Overrides live in persona/instructions
(outside git, never touched by update-main), one file per entry, so editing one instruction
never rewrites the others. An override always wins, whole.
"""
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path

from veil.core.agent_types import UserError

ID = re.compile(r'^[a-z][a-z0-9]*(?:[._][a-z0-9_]+)*$')
MAX_BYTES = 40000
BASE = '.base.json'


class PromptStore:
    def __init__(self, defaults_dir, overrides_dir, *, max_bytes=MAX_BYTES):
        self.defaults = Path(defaults_dir)
        self.overrides = Path(overrides_dir)
        self.max_bytes = int(max_bytes)
        self.lock = threading.RLock()

    def default_path(self, entry_id):
        return self.defaults / (entry_id + '.md')

    def _base_path(self):
        return self.overrides / BASE

    def _base(self):
        try:
            data = json.loads(self._base_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _remember_base(self, entry_id, digest):
        with self.lock:
            data = self._base()
            data[entry_id] = digest
            try:
                self.overrides.mkdir(parents=True, exist_ok=True)
                temporary = self._base_path().with_name(BASE + '.' + uuid.uuid4().hex + '.tmp')
                with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                    handle.write(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self._base_path())
            except OSError:
                pass

    def _ensure_base(self, entry_id):
        """Baseline for an override saved before fingerprints existed; writes only the fingerprint file."""
        try:
            digest = self._digest(self.default(entry_id))
        except (UserError, OSError, UnicodeError):
            return None
        self._remember_base(entry_id, digest)
        return digest

    @staticmethod
    def _digest(text):
        return hashlib.sha1(text.encode('utf-8')).hexdigest()

    def override_path(self, entry_id):
        return self.overrides / (entry_id + '.md')

    def default(self, entry_id):
        try:
            return self.default_path(entry_id).read_text(encoding='utf-8').rstrip('\n')
        except FileNotFoundError:
            raise UserError('指令默认文本缺失：' + entry_id + '（veil/prompts/defaults/' + entry_id + '.md）。') from None
        except OSError as exc:
            raise UserError('读不到指令默认文本：' + entry_id + '（' + str(exc) + '）。') from exc

    def overridden(self, entry_id):
        try:
            return bool(self.override_path(entry_id).read_text(encoding='utf-8').strip())
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            return False

    def text(self, entry_id):
        """Override when present and non-empty, otherwise the shipped default."""
        try:
            value = self.override_path(entry_id).read_text(encoding='utf-8').rstrip('\n')
        except FileNotFoundError:
            return self.default(entry_id)
        except (OSError, UnicodeDecodeError):
            # A damaged override must not stop Peter; the default keeps him talking.
            return self.default(entry_id)
        return value if value.strip() else self.default(entry_id)

    def status(self, entry_id):
        value = self.text(entry_id)
        path = self.override_path(entry_id)
        overridden = self.overridden(entry_id)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        default_changed = False
        if overridden:
            try:
                remembered = self._base().get(entry_id)
                if not remembered:
                    # Ticket 404: an override saved before fingerprints existed gets the current
                    # default as its baseline. The override text is never touched.
                    remembered = self._ensure_base(entry_id)
                default_changed = bool(remembered) and remembered != self._digest(self.default(entry_id))
            except (UserError, OSError, UnicodeError):
                default_changed = False
        return {'id': entry_id, 'text': value, 'bytes': len(value.encode('utf-8')),
                'overridden': overridden, 'default_changed': default_changed, 'mtime': mtime,
                'path': str(path), 'default_path': str(self.default_path(entry_id))}

    def save(self, entry_id, value, *, editable=True):
        from . import registry
        entry = registry.get(entry_id)
        if not isinstance(value, str):
            raise UserError('内容必须是文字。')
        if not editable or not entry.editable:
            raise UserError('这一条不允许在界面上修改。')
        data = value.replace('\r\n', '\n').rstrip('\n')
        size = len(data.encode('utf-8'))
        limit = min(entry.max_bytes, self.max_bytes)
        if size > limit:
            raise UserError('这一条最多 ' + str(limit) + ' 字节，现在 ' + str(size) + ' 字节。')
        missing = [name for name in entry.placeholders if '{' + name + '}' not in data]
        if missing:
            raise UserError('保存失败：这一条要保留 ' + '、'.join('{' + name + '}' for name in missing)
                            + '，那是程序填真实数据的位置。删掉它，Peter 收到的这一段就没有时间、'
                              '金额或日程内容了。其他文字可以随便改。')
        with self.lock:
            self.overrides.mkdir(parents=True, exist_ok=True)
            target = self.override_path(entry_id)
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
                raise UserError('写 ' + target.name + ' 失败：' + str(exc) + '。') from exc
            try:
                # A fingerprint that cannot be computed must never turn a saved edit into a failure.
                self._remember_base(entry_id, self._digest(self.default(entry_id)))
            except (UserError, OSError, UnicodeError):
                pass
        return self.status(entry_id)
