"""P3-2: one-way, idempotent migration of event auxiliary ledgers into brain.db.

The legacy files were the source of truth before P3-2. Each row is copied losslessly into
event_ledger / event_state, verified by its row hash, and only then is the file copied to a
read-only archive directory and removed from the live role directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import brain_events
import brain_writer
from brain_lock import write_lock


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _archive_dir(role_dir):
    stamp = time.strftime('%Y%m%dT%H%M%S')
    return role_dir / 'archive' / ('pre-p3-2-event-ledgers-' + stamp)


def migrate_role(store, role, archive_root=None):
    """Migrate this role's remaining event JSONL/state files; safe to call on every start."""
    role_dir = Path(store.root) / role
    db_path = role_dir / 'brain.db'
    if not db_path.is_file():
        return {'migrated_files': 0, 'rows': 0}
    files = [(name, kind) for name, kind in brain_events.EVENT_LEDGER_FILES.items()
             if (role_dir / name).is_file()]
    state_path = role_dir / brain_events.EVENT_STATE_FILE
    if not files and not state_path.is_file():
        return {'migrated_files': 0, 'rows': 0}
    archive = Path(archive_root) if archive_root else _archive_dir(role_dir)
    archive.mkdir(parents=True, exist_ok=True)
    manifest = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'files': {}}
    total = 0
    with write_lock(store, role):
        connection = sqlite3.connect(str(db_path), timeout=30)
        try:
            brain_events.ensure_schema(connection)
            for name, kind in files:
                source = role_dir / name
                data = source.read_bytes()
                try:
                    rows = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
                except (UnicodeError, ValueError) as exc:
                    raise RuntimeError('无法解析事件账本 %s：%s' % (name, exc)) from exc
                for row in rows:
                    if not isinstance(row, dict):
                        raise RuntimeError('事件账本 %s 含有非对象行' % name)
                    brain_events.append_ledger(connection, kind, row)
                connection.commit()
                # Verify every source row is now present by hash before the file may be archived.
                for row in rows:
                    payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
                    digest = _sha(payload.encode('utf-8'))
                    found = connection.execute(
                        'SELECT 1 FROM event_ledger WHERE kind=? AND row_sha256=?',
                        (kind, digest)).fetchone()
                    if not found:
                        raise RuntimeError('事件账本 %s 校验失败，未归档原文件' % name)
                manifest['files'][name] = {'rows': len(rows), 'sha256': _sha(data), 'kind': kind}
                total += len(rows)
            if state_path.is_file():
                raw = state_path.read_bytes()
                try:
                    value = json.loads(raw.decode('utf-8'))
                except (UnicodeError, ValueError) as exc:
                    raise RuntimeError('无法解析 %s：%s' % (state_path.name, exc)) from exc
                if not isinstance(value, dict):
                    raise RuntimeError('%s 不是对象' % state_path.name)
                brain_events.write_state(connection, 'event-dream', value)
                manifest['files'][state_path.name] = {
                    'rows': 1, 'sha256': _sha(raw), 'kind': 'event-state'}
            brain_writer.refresh_digest(connection)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        # DB is committed and verified; keep a read-only archive, then remove the live copy.
        for name in manifest['files']:
            source = role_dir / name
            if not source.is_file():
                continue
            target = archive / name
            target.write_bytes(source.read_bytes())
            os.chmod(target, 0o444)
            source.unlink()
        (archive / 'manifest.json').write_text(
            json.dumps({**manifest, 'character_id': role, 'rows': total}, ensure_ascii=False,
                       indent=1, sort_keys=True), encoding='utf-8')
    return {'migrated_files': len(manifest['files']), 'rows': total, 'archive': str(archive)}
