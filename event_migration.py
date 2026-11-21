"""Ticket 410: preview how the existing memories would be grouped into events.

This never writes a memory. It reads the role's Markdown vault, proposes events from shared
names/objects plus time proximity, and writes a per-memory manifest so Lan can check every row
before any formal migration. Formal migration is a separate, explicitly approved step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from event_memory import entities

FOLDERS = ('dynamic', 'permanent', 'feel')
GAP_DAYS = 7


def _frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return {}, text
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == '---'), None)
    if end is None:
        return {}, text
    meta = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(':')
        if sep and not line[:1].isspace():
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, '\n'.join(lines[end + 1:])


def read_memories(root, role):
    vault = Path(root) / 'data' / role / 'memory'
    memories = []
    for folder in FOLDERS:
        for path in sorted((vault / folder).rglob('*.md')):
            try:
                text = path.read_text(encoding='utf-8')
            except OSError:
                continue
            meta, body = _frontmatter(text)
            memory_id = meta.get('id') or path.stem.rsplit('_', 1)[-1]
            created = meta.get('created') or ''
            memories.append({
                'memory_id': memory_id, 'title': meta.get('title') or meta.get('name') or memory_id,
                'created': created, 'folder': folder, 'body': body.strip(),
                'entities': sorted(entities(body))[:200],
            })
    return memories


def _when(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def propose(memories, gap_days=GAP_DAYS):
    """Anchor-based clustering: a new memory joins a group only through shared names/objects with that
    group's latest member (>=2), or one shared term plus closeness for a still-single group. This avoids
    the transitive chaining that would collapse the whole vault into one event."""
    ordered = sorted(memories, key=lambda memory: str(memory.get('created') or ''))
    frequency = {}
    for memory in ordered:
        for term in set(memory['entities']):
            frequency[term] = frequency.get(term, 0) + 1
    common_limit = max(3, int(len(ordered) * 0.1))

    def strong(memory):
        return {term for term in memory['entities'] if frequency.get(term, 0) <= common_limit}

    groups = []
    for memory in ordered:
        chosen = None
        best = 0
        for group in groups:
            last = group['members'][-1]
            first_when = _when(group['members'][0].get('created'))
            when, last_when = _when(memory.get('created')), _when(last.get('created'))
            if first_when and when and abs((when - first_when).days) > gap_days:
                continue
            shared = len(strong(memory) & strong(last))
            close = bool(when and last_when and abs((when - last_when).days) <= gap_days)
            if shared >= 2 or (shared >= 1 and close):
                score = shared * 2 + (1 if close else 0)
                if score > best:
                    best, chosen = score, group
        if chosen is None:
            groups.append({'members': [memory]})
        else:
            chosen['members'].append(memory)
    rows = []
    for group in groups:
        members = sorted(group['members'], key=lambda memory: str(memory.get('created') or ''))
        anchor = members[0]
        when = _when(anchor.get('created'))
        stamp = when.strftime('%Y%m%d') if when else 'unknown'
        event_id = 'e-' + stamp + '-' + hashlib.sha1(
            ','.join(sorted(memory['memory_id'] for memory in members)).encode('utf-8')).hexdigest()[:6]
        for memory in members:
            rows.append({'memory_id': memory['memory_id'], 'title': memory['title'],
                         'created': memory.get('created'), 'folder': memory.get('folder'),
                         'proposed_event': event_id, 'reason': 'shared names/objects + time proximity'})
    rows.sort(key=lambda row: (row['proposed_event'], row['created']))
    return rows


def write_manifest(rows, path, protected=()):
    """Write the preview ledger, but never inside a protected memory vault."""
    path = Path(path).resolve()
    for root in protected:
        root = Path(root).resolve()
        if path == root or path.is_relative_to(root):
            raise ValueError('refusing to write the migration manifest inside the memory vault')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return {'memories': len(rows), 'events': len({row['proposed_event'] for row in rows}), 'path': str(path)}


def main():
    parser = argparse.ArgumentParser(description='Preview event grouping for existing memories (read-only).')
    parser.add_argument('--root', default='.')
    parser.add_argument('--role', default='peter')
    parser.add_argument('--out', default='event-migration-preview.jsonl')
    args = parser.parse_args()
    rows = propose(read_memories(args.root, args.role))
    vault = Path(args.root) / 'data' / args.role / 'memory'
    print(json.dumps(write_manifest(rows, args.out, protected=[vault]), ensure_ascii=False))


if __name__ == '__main__':
    main()
