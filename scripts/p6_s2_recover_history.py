#!/usr/bin/env python3
"""Historical failure audit and idempotent recovery tool for P6-S2."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veil.brain.storage import brain_events  # noqa: E402
from veil.brain.storage import brain_writer  # noqa: E402
from veil.brain.storage.brain_lock import write_lock  # noqa: E402
from veil_agent import Store  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_jsonl(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    """Rows of a JSONL file, split on physical newlines only.

    A JSON string may legally contain U+2028; str.splitlines() would split such a record and the
    fragments would be dropped, so only '\n' separates records (P6-S2 review). `strict` refuses to
    continue when a record cannot be parsed: a recovery must stop rather than commit an incomplete
    slice that a rerun could never complete.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding='utf-8')
    for number, raw in enumerate(text.split('\n'), start=1):
        raw = raw.rstrip('\r').strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception as exc:
            if strict:
                raise ValueError(
                    f'{path.name} line {number} is not valid JSON; refusing a partial recovery') from exc
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _parse_time(value: Any, label: str = 'time') -> datetime:
    """A timezone-aware instant; a value without an offset is read as local time."""
    text = str(value or '').strip()
    if not text:
        raise ValueError(f'{label} is empty; an ISO timestamp is required')
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'{label} is not an ISO timestamp: {text!r}') from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _normalise_time(value: Any) -> str:
    """Canonical form of a boundary so equivalent instants share one recovery key."""
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        parsed = _parse_time(text)
    except ValueError:
        return text
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def make_recovery_source_key(memory_id: str, from_time: str, to_time: str) -> str:
    digest = hashlib.sha256(
        f'{memory_id}|{_normalise_time(from_time)}|{_normalise_time(to_time)}'.encode('utf-8')
    ).hexdigest()[:12]
    return f'p6-s2-recover:{memory_id}:{digest}'


def _legacy_recovery_key(memory_id: str, from_time: str, to_time: str) -> str:
    """The key format used before boundaries were normalised, so old recoveries are recognised."""
    digest = hashlib.sha256(f'{memory_id}|{from_time}|{to_time}'.encode('utf-8')).hexdigest()[:12]
    return f'p6-s2-recover:{memory_id}:{digest}'


def _recovery_keys(memory_id: str, from_time: str, to_time: str) -> list[str]:
    keys = [make_recovery_source_key(memory_id, from_time, to_time)]
    legacy = _legacy_recovery_key(memory_id, from_time, to_time)
    if legacy not in keys:
        keys.append(legacy)
    return keys


def _committed_recovery(db: Path, keys: list[str]) -> tuple[str, str] | None:
    """(event_id, stage_id) of an already committed recovery for any of these keys."""
    if not db.exists():
        return None
    connection = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        present = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='experience_recoveries'").fetchone()
        if not present:
            return None
        for key in keys:
            row = connection.execute(
                'SELECT event_id,stage_id FROM experience_recoveries WHERE source_key=?',
                (key,)).fetchone()
            if row is not None:
                return (row['event_id'], row['stage_id'])
        return None
    finally:
        connection.close()


def audit_failures(role_dir: str | Path) -> dict[str, Any]:
    """Audit historical unfixed_draft and update-conflict batches in role_dir."""
    role = Path(role_dir)
    failures = _read_jsonl(role / 'experience-memory-failures.jsonl')
    exp_rows = _read_jsonl(role / 'experience-memory.jsonl')
    recovered_keys = {
        str(r.get('source_key') or '')
        for r in exp_rows
        if r.get('kind') == 'p6-s2-recovery' and r.get('source_key')
    }

    unfixed: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for item in failures:
        reason = str(item.get('what') or item.get('reason') or '')
        kind = str(item.get('kind') or '')
        if reason == 'unfixed_draft' or kind == 'unfixed_draft' or item.get('dropped'):
            target = str(item.get('id') or item.get('update') or item.get('memory_id') or '')
            covers = item.get('covers') or []
            if isinstance(covers, dict):
                covers = [covers.get('from'), covers.get('to')]
            covers = [str(value) for value in covers if value]
            from_t = covers[0] if covers else str(item.get('from_time') or '')
            to_t = covers[-1] if len(covers) >= 2 else str(item.get('to_time') or from_t)
            skey = make_recovery_source_key(target, from_t, to_t) if target and from_t else ''
            unfixed.append({
                'ts': item.get('time') or item.get('ts'),
                'reason': reason or kind,
                'target_memory_id': target,
                'covers': covers,
                'pos': item.get('pos'),
                'source_key': skey,
                'already_recovered': skey in recovered_keys if skey else False,
            })
        elif 'conflict' in reason or 'conflict' in kind:
            conflicts.append({
                'ts': item.get('time') or item.get('ts'),
                'reason': reason or kind,
                'target_memory_id': item.get('id') or item.get('update') or item.get('memory_id'),
                'covers': item.get('covers'),
            })
    for item in exp_rows:
        if 'conflict' not in str(item.get('kind') or ''):
            continue
        conflicts.append({
            'ts': item.get('time') or item.get('ts'),
            'reason': item.get('kind'),
            'target_memory_id': item.get('memory_id'),
            'covers': item.get('covers'),
            'source_key': item.get('source_key'),
        })
    return {
        'total_failure_rows': len(failures),
        'unfixed_draft_count': len(unfixed),
        'unfixed_drafts': unfixed,
        'conflict_count': len(conflicts),
        'conflicts': conflicts,
    }


def _load_chat_slice(chat_path: Path, from_time: str, to_time: str) -> list[dict[str, Any]]:
    """Messages inside the inclusive window, compared as instants rather than as strings.

    ``04:00:00+00:00`` and ``12:00:00+08:00`` are the same moment; a string comparison would keep one
    out of a ``11:00+08:00``..``13:00+08:00`` window (P6-S2 review). A timestamp that cannot be parsed
    (or is missing) refuses the batch instead of silently shrinking it (ticket 453).
    """
    start = _parse_time(from_time, '--from-time')
    end = _parse_time(to_time, '--to-time')
    if end < start:
        raise ValueError('--to-time is earlier than --from-time')
    rows: list[dict[str, Any]] = []
    for item in _read_jsonl(chat_path, strict=True):
        raw = item.get('ts') or item.get('time') or item.get('created_at')
        if not raw:
            # Ticket 453: a message that cannot be placed in the window must refuse the batch. Dropping
            # it while still committing the source_key made the loss permanent (a rerun said
            # already_recovered), so a recovery can only commit a slice it can prove complete.
            raise ValueError('a chat message has no timestamp; refusing a partial recovery')
        try:
            stamp = _parse_time(raw, 'message time')
        except ValueError as exc:
            raise ValueError(
                f'message time {raw!r} is not an ISO timestamp; refusing a partial recovery') from exc
        if start <= stamp <= end:
            rows.append(item)
    return rows


def _legacy_recover_slice(
    role_dir: str | Path,
    db_path: str | Path,
    *,
    memory_id: str,
    from_time: str,
    to_time: str,
    draft_text: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Deprecated interrupted implementation retained only for audit comparison."""
    role = Path(role_dir)
    db = Path(db_path)
    source_key = make_recovery_source_key(memory_id, from_time, to_time)

    conn = sqlite3.connect(str(db), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        existing_stage = None
        for r in conn.execute('SELECT stage_id, event_id, payload_json FROM event_stages'):
            try:
                p = json.loads(r[2]) if r[2] else {}
            except Exception:
                p = {}
            if isinstance(p, dict) and p.get('source_key') == source_key:
                existing_stage = {'stage_id': r[0], 'event_id': r[1]}
                break
        if existing_stage is not None:
            return {
                'status': 'already_recovered',
                'source_key': source_key,
                'stage_id': existing_stage['stage_id'],
                'event_id': existing_stage['event_id'],
            }
        mem = conn.execute(
            'SELECT id, body, vector_state FROM memories WHERE id = ?',
            (memory_id,),
        ).fetchone()
        if mem is None:
            return {'status': 'target_missing', 'memory_id': memory_id, 'source_key': source_key}
        ev = conn.execute(
            'SELECT event_id AS id, story FROM events WHERE memory_id = ? LIMIT 1',
            (memory_id,),
        ).fetchone()

        if ev is not None:
            # Check locked status via sidecar_state
            try:
                locked_row = conn.execute(
                    'SELECT locked FROM sidecar_state WHERE memory_id = ?',
                    (memory_id,),
                ).fetchone()
                if locked_row and locked_row[0]:
                    return {'status': 'target_locked', 'memory_id': memory_id, 'event_id': ev['id'], 'source_key': source_key}
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()

    messages = _load_chat_slice(role / 'chat.jsonl', from_time, to_time)
    if not messages and not draft_text:
        return {'status': 'no_messages', 'memory_id': memory_id, 'source_key': source_key}

    if ev is None:
        return {'status': 'event_missing', 'memory_id': memory_id, 'source_key': source_key}

    msg_ids = [str(m.get('message_id') or m.get('id')) for m in messages
               if (m.get('message_id') or m.get('id')) is not None]
    summary_text = (draft_text or '').strip()
    if not summary_text:
        snippets = [
            f"{m.get('role', 'user')}: {str(m.get('text') or m.get('content') or '').strip()}"
            for m in messages[:6]
            if str(m.get('text') or m.get('content') or '').strip()
        ]
        summary_text = f'[P6-S2 recovered {from_time}..{to_time}, {len(messages)} msgs] ' + ' | '.join(snippets)

    if dry_run:
        return {
            'status': 'would_recover',
            'memory_id': memory_id,
            'event_id': ev['id'],
            'source_key': source_key,
            'message_count': len(messages),
            'message_ids': msg_ids,
            'preserves_existing_story': True,
        }

    stage_id = f'st-{uuid.uuid4().hex[:12]}'
    now = _now_iso()
    existing_story = str(ev['story'] or '')
    stage_record = {
        'stage_id': stage_id,
        'event_id': ev['id'],
        'at': to_time,
        'state': 'committed',
        'text': summary_text,
        'source_key': source_key,
        'source': {'type': 'p6-s2-recovery', 'from_time': from_time, 'to_time': to_time, 'message_ids': msg_ids},
    }

    conn2 = sqlite3.connect(str(db), timeout=30.0)
    try:
        brain_writer.ensure_schema(conn2)
        brain_events.ensure_schema(conn2)
        conn2.execute('BEGIN IMMEDIATE')
        brain_writer.apply_update_memory(
            conn2,
            memory_id,
            {'body': str(mem['body'])},
            operation_reason=f'p6-s2-recover:{from_time}..{to_time}',
        )
        brain_events.upsert_stage(conn2, stage_record, commit=False, ensure=False)
        brain_events.append_ledger(conn2, 'stage-evidence', {
            'at': now,
            'event_id': ev['id'],
            'stage_id': stage_id,
            'source_key': source_key,
            'evidence': [{'kind': 'chat', 'id': mid} for mid in msg_ids],
        })
        conn2.commit()
        brain_writer.refresh_digest(conn2)
    except Exception:
        conn2.rollback()
        raise
    finally:
        conn2.close()

    exp_path = role / 'experience-memory.jsonl'
    _append_jsonl_checked(exp_path, {
        'ts': now,
        'kind': 'p6-s2-recovery',
        'memory_id': memory_id,
        'event_id': ev['id'],
        'stage_id': stage_id,
        'source_key': source_key,
        'covers': [from_time, to_time],
        'message_count': len(messages),
    })

    return {
        'status': 'recovered',
        'memory_id': memory_id,
        'event_id': ev['id'],
        'stage_id': stage_id,
        'source_key': source_key,
        'message_count': len(messages),
    }


def _append_jsonl_checked(path: Path, row: dict[str, Any]) -> None:
    """Append one JSONL record while preserving line boundaries and refusing torn tails."""
    newline_prefix = b''
    if path.exists() and path.stat().st_size:
        raw = path.read_bytes()
        if raw.strip():
            lines = [ln for ln in raw.splitlines() if ln.strip()]
            try:
                json.loads(lines[-1].decode('utf-8'))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f'corrupted trailing JSONL record in {path}') from exc
            if not raw.endswith(b'\n'):
                newline_prefix = b'\n'
    payload = newline_prefix + (json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8')
    with path.open('ab') as handle:
        handle.write(payload)


def _ensure_external_marker(role: Path, row: dict[str, Any]) -> None:
    path = role / 'experience-memory.jsonl'
    existing = _read_jsonl(path)
    source_key = row.get('source_key')
    if any(item.get('kind') == 'p6-s2-recovery' and item.get('source_key') == source_key
           for item in existing):
        return
    _append_jsonl_checked(path, row)



def recover_slice(
    role_dir: str | Path,
    db_path: str | Path,
    *,
    memory_id: str,
    from_time: str,
    to_time: str,
    draft_text: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Idempotently append a dropped slice to its event story and projection."""
    role = Path(role_dir)
    db = Path(db_path)
    keys = _recovery_keys(memory_id, from_time, to_time)
    source_key = keys[0]
    # P6-S2 review: check the committed key before requiring the source messages. If the database
    # committed and only the external marker failed, the chat may already be archived; a rerun must
    # still report already_recovered and repair the marker from the persisted recovery record.
    committed = _committed_recovery(db, keys)
    if committed is not None:
        marker = {'ts': _now_iso(), 'kind': 'p6-s2-recovery', 'memory_id': memory_id,
                  'source_key': source_key, 'covers': [from_time, to_time],
                  'event_id': committed[0], 'stage_id': committed[1]}
        if not dry_run:
            store = Store(role.parent)
            with write_lock(store, role.name):
                _ensure_external_marker(role, marker)
        return {'status': 'already_recovered', **marker}
    messages = _load_chat_slice(role / 'chat.jsonl', from_time, to_time)
    if not messages and not draft_text:
        return {'status': 'no_messages', 'memory_id': memory_id, 'source_key': source_key}
    # Store.append() writes `message_id`; older rows may carry `id`.
    msg_ids = [str(m.get('message_id') or m.get('id')) for m in messages
               if (m.get('message_id') or m.get('id')) is not None]
    summary_text = (draft_text or '').strip()
    if not summary_text:
        summary_text = '\n'.join(
            f"{m.get('role', 'user')}: {str(m.get('text') or m.get('content') or '').strip()}"
            for m in messages if str(m.get('text') or m.get('content') or '').strip())
    marker = {
        'ts': _now_iso(), 'kind': 'p6-s2-recovery', 'memory_id': memory_id,
        'source_key': source_key, 'covers': [from_time, to_time],
        'message_count': len(messages),
    }
    if dry_run:
        conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            mem = conn.execute(
                'SELECT id,body FROM memories WHERE id=? AND is_deleted=0 AND is_tombstone=0',
                (memory_id,)).fetchone()
            if mem is None:
                return {'status': 'target_missing', 'memory_id': memory_id, 'source_key': source_key}
            ev = conn.execute(
                'SELECT event_id AS id,story FROM events WHERE memory_id=? LIMIT 1',
                (memory_id,)).fetchone()
            if ev is None:
                return {'status': 'event_missing', 'memory_id': memory_id, 'source_key': source_key}
            locked = conn.execute(
                'SELECT locked FROM sidecar_state WHERE memory_id=?', (memory_id,)).fetchone()
            if locked and locked[0]:
                return {'status': 'target_locked', 'memory_id': memory_id,
                        'event_id': ev['id'], 'source_key': source_key}
            stage_id = 'st-' + hashlib.sha256(source_key.encode('utf-8')).hexdigest()[:12]
            marker.update({'event_id': ev['id'], 'stage_id': stage_id})
            return {'status': 'would_recover', **marker, 'message_ids': msg_ids,
                    'preserves_existing_story': True, 'story_append': summary_text}
        finally:
            conn.close()
    store = Store(role.parent)
    with write_lock(store, role.name):
        conn = sqlite3.connect(str(db), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            brain_writer.ensure_schema(conn)
            brain_events.ensure_schema(conn)
            conn.execute(
                'CREATE TABLE IF NOT EXISTS experience_recoveries('
                'source_key TEXT PRIMARY KEY, memory_id TEXT NOT NULL, event_id TEXT NOT NULL, '
                'stage_id TEXT NOT NULL, created_at TEXT NOT NULL)')
            existing = None
            for key in keys:
                existing = conn.execute(
                    'SELECT event_id,stage_id FROM experience_recoveries WHERE source_key=?',
                    (key,)).fetchone()
                if existing is not None:
                    break
            if existing is not None:
                marker.update({'event_id': existing['event_id'], 'stage_id': existing['stage_id']})
                if not dry_run:
                    _ensure_external_marker(role, marker)
                return {'status': 'already_recovered', **marker}
            mem = conn.execute(
                'SELECT id,body FROM memories WHERE id=? AND is_deleted=0 AND is_tombstone=0',
                (memory_id,)).fetchone()
            if mem is None:
                return {'status': 'target_missing', 'memory_id': memory_id, 'source_key': source_key}
            ev = conn.execute(
                'SELECT event_id AS id,story,payload_json FROM events WHERE memory_id=? LIMIT 1',
                (memory_id,)).fetchone()
            if ev is None:
                return {'status': 'event_missing', 'memory_id': memory_id, 'source_key': source_key}
            locked = conn.execute(
                'SELECT locked FROM sidecar_state WHERE memory_id=?', (memory_id,)).fetchone()
            if locked and locked[0]:
                return {'status': 'target_locked', 'memory_id': memory_id,
                        'event_id': ev['id'], 'source_key': source_key}
            stage_id = 'st-' + hashlib.sha256(source_key.encode('utf-8')).hexdigest()[:12]
            marker.update({'event_id': ev['id'], 'stage_id': stage_id})
            old_story = str(ev['story'] or '')
            old_body = str(mem['body'] or '')
            new_story = '\n\n'.join(part for part in (old_story.rstrip(), summary_text) if part)
            new_body = '\n\n'.join(part for part in (old_body.rstrip(), summary_text) if part)
            payload = json.loads(ev['payload_json']) if ev['payload_json'] else {}
            payload.update({'event_id': ev['id'], 'memory_id': memory_id, 'story': new_story,
                            'story_source': 'p6-s2-recovery', 'dirty': False})
            stage_record = {
                'stage_id': stage_id, 'event_id': ev['id'], 'at': to_time,
                'state': 'committed', 'text': summary_text, 'source_key': source_key,
                'source': {'type': 'p6-s2-recovery', 'from_time': from_time,
                           'to_time': to_time, 'message_ids': msg_ids},
            }
            conn.execute('BEGIN IMMEDIATE')
            conn.execute(
                'INSERT INTO experience_recoveries(source_key,memory_id,event_id,stage_id,created_at) '
                'VALUES(?,?,?,?,?)', (source_key, memory_id, ev['id'], stage_id, marker['ts']))
            brain_writer.apply_update_memory(
                conn, memory_id, {'body': new_body}, state='pending',
                operation_reason=f'p6-s2-recover:{from_time}..{to_time}')
            brain_events.upsert_event(conn, payload, commit=False, ensure=False)
            brain_events.upsert_stage(conn, stage_record, commit=False, ensure=False)
            brain_events.append_ledger(conn, 'stage-evidence', {
                'at': marker['ts'], 'event_id': ev['id'], 'stage_id': stage_id,
                'memory_id': memory_id, 'source_key': source_key, 'message_ids': msg_ids,
                'evidence': [{'kind': 'chat', 'message_id': mid} for mid in msg_ids],
            })
            next_quote = conn.execute(
                'SELECT COALESCE(MAX(ord), -1) + 1 FROM quotes WHERE memory_id=?',
                (memory_id,)).fetchone()[0]
            user_quotes = [str(item.get('text') or item.get('content') or '').strip()
                           for item in messages if item.get('role') == 'user']
            user_quotes = [text for text in user_quotes if text]
            for offset, text in enumerate(user_quotes):
                conn.execute(
                    'INSERT INTO quotes(memory_id,ord,speaker,text) VALUES(?,?,?,?)',
                    (memory_id, int(next_quote) + offset, 'user', text))
            brain_writer.apply_digest(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        # P6-S2 review: the marker's check-and-append runs under the same role write lock as the
        # commit, so a concurrent retry cannot append a second marker for one recovery key.
        _ensure_external_marker(role, marker)
    return {'status': 'recovered', **marker}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='P6-S2 historical failure audit & idempotent recovery')
    parser.add_argument('--role-dir', required=True)
    parser.add_argument('--db-path', default=None)
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--memory-id', default=None)
    parser.add_argument('--from-time', default=None)
    parser.add_argument('--to-time', default=None)
    parser.add_argument('--draft-file', default=None)
    parser.add_argument('--apply', action='store_true', help='Actually write changes (default is dry-run)')
    args = parser.parse_args(argv)

    if args.audit:
        print(json.dumps(audit_failures(args.role_dir), ensure_ascii=False, indent=2))
        return 0
    if args.recover:
        if not (args.memory_id and args.from_time and args.to_time):
            parser.error('--recover requires --memory-id, --from-time, and --to-time')
        db_path = args.db_path or str(Path(args.role_dir) / 'brain.db')
        draft = Path(args.draft_file).read_text(encoding='utf-8') if args.draft_file else None
        res = recover_slice(
            args.role_dir,
            db_path,
            memory_id=args.memory_id,
            from_time=args.from_time,
            to_time=args.to_time,
            draft_text=draft,
            dry_run=not args.apply,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
