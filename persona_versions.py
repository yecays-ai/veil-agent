"""Ticket 214: keep a dated copy of persona/peter.md whenever its content is new."""
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

VERSION_DIR = 'versions'
_NAME = re.compile(r'^(?P<stem>.+)\.(?P<stamp>\d{8}-\d{6}(?:-\d+)?)\.md$')


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
            found.append(item)
    return sorted(found, key=lambda item: item.name, reverse=True)


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
            if hashlib.sha256(item.read_bytes()).digest() == digest:
                return None
        folder = versions_dir(persona_path)
        folder.mkdir(exist_ok=True)
        stamp = (now or datetime.now(timezone.utc)).strftime('%Y%m%d-%H%M%S')
        for attempt in range(100):
            suffix = '' if attempt == 0 else f'-{attempt}'
            target = folder / f'{persona_path.stem}.{stamp}{suffix}.md'
            try:
                with target.open('xb') as handle:
                    handle.write(data)
            except FileExistsError:
                continue
            return target
    except OSError:
        return None
    return None
