"""Ticket 214: keep a dated copy of persona/peter.md whenever its content is new."""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION_DIR = 'versions'
_NAME = re.compile(r'^(?P<stem>.+)\.(?P<stamp>\d{8}-\d{6})(?:-(?P<n>\d+))?\.md$')


def versions_dir(persona_path):
    return Path(persona_path).parent / VERSION_DIR


def list_versions(persona_path):
    """Saved copies of this persona file, newest first."""
    persona_path = Path(persona_path)
    folder = versions_dir(persona_path)
    if not folder.is_dir():
        return []
    found = []
    for item in folder.iterdir():
        match = _NAME.match(item.name)
        if match and match.group('stem') == persona_path.stem and item.is_file():
            found.append(((match.group('stamp'), int(match.group('n') or -1)), item))
    return [item for _, item in sorted(found, key=lambda pair: pair[0], reverse=True)]


def snapshot(persona_path, now=None):
    """Save the persona file as a new version unless an identical copy already exists.

    Returns the new copy's path, or None when nothing new was saved. Never raises for
    file problems: a failed backup must not stop Peter from starting."""
    persona_path = Path(persona_path)
    try:
        data = persona_path.read_bytes()
        if not data.strip():
            return None
        digest = hashlib.sha256(data).digest()
        for item in list_versions(persona_path):
            try:
                if hashlib.sha256(item.read_bytes()).digest() == digest:
                    return None
            except OSError:
                continue  # one unreadable old copy must not stop the new one being saved
        folder = versions_dir(persona_path)
        folder.mkdir(exist_ok=True)
        stamp = (now or datetime.now(timezone.utc)).strftime('%Y%m%d-%H%M%S')
        for attempt in range(100):
            suffix = '' if attempt == 0 else f'-{attempt}'
            target = folder / f'{persona_path.stem}.{stamp}{suffix}.md'
            if target.exists():
                continue
            # Write under a name restore never lists, then move it into place, so an
            # interrupted write cannot leave a half-copy that looks like a version.
            partial = folder / (target.name + '.partial')
            try:
                with partial.open('wb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(partial, target)
            except OSError:
                try:
                    partial.unlink()
                except OSError:
                    pass
                raise
            return target
    except OSError as exc:
        print(f'[persona] 人设版本没能保存（不影响启动）：{exc}', file=sys.stderr)
        return None
    return None
