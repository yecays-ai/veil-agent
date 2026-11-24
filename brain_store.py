"""P2-A: a Store facade that keeps the legacy ledger API while Brain is authoritative.

Only versions/sidecar ledgers are redirected; everything else delegates to the original Store.
This lets existing callers keep their code while Brain owns the durable record. Active only when
write_authority is on.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import brain_events
import brain_writer


class BrainStore:
    def __init__(self, base, role, db_path):
        self.base = base
        self.role = role
        self.db_path = Path(db_path)
        self.root = base.root
        self.freeze_path = Path(base.root) / role / 'brain-write-freeze'

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        brain_writer.ensure_schema(connection)
        brain_events.ensure_schema(connection)
        return connection

    def append(self, path, row):
        if self.freeze_path.exists():
            raise RuntimeError('memory writes are frozen for cutover')
        name = str(path).replace('\\', '/')
        if name == '%s/memory-versions.jsonl' % self.role:
            return self._append_version(row)
        if name == '%s/memory-sidecar.jsonl' % self.role:
            return self._append_sidecar(row)
        if name == '%s/delete-requests.jsonl' % self.role:
            connection = self._connect()
            try:
                raw = json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
                connection.execute('INSERT INTO delete_requests(request_id,memory_id,reason,status,created_at,raw_json) '
                                   'VALUES(?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET status=excluded.status, '
                                   'raw_json=excluded.raw_json',
                                   (row.get('request_id'), row.get('memory_id'), row.get('reason'),
                                    row.get('status'), row.get('created_at'), raw))
                connection.commit()
            finally:
                connection.close()
            return None
        if name == '%s/events.jsonl' % self.role:
            connection = self._connect()
            try:
                brain_events.upsert_event(connection, dict(row))
            finally:
                connection.close()
            return None
        if name == '%s/event-stages.jsonl' % self.role:
            connection = self._connect()
            try:
                brain_events.upsert_stage(connection, dict(row))
            finally:
                connection.close()
            return None
        return self.base.append(path, row)

    def read(self, path):
        name = str(path).replace('\\', '/')
        if name == '%s/memory-versions.jsonl' % self.role:
            return self._read_versions()
        if name == '%s/memory-sidecar.jsonl' % self.role:
            return self._read_sidecar()
        if name == '%s/delete-requests.jsonl' % self.role:
            connection = self._connect()
            try:
                rows = []
                for item in connection.execute('SELECT raw_json FROM delete_requests ORDER BY created_at'):
                    try:
                        rows.append(json.loads(item[0]))
                    except ValueError:
                        continue
                return rows
            finally:
                connection.close()
        if name == '%s/events.jsonl' % self.role:
            connection = self._connect()
            try:
                return brain_events.read_rows(connection, 'events')
            finally:
                connection.close()
        if name == '%s/event-stages.jsonl' % self.role:
            connection = self._connect()
            try:
                return brain_events.read_rows(connection, 'event-stages')
            finally:
                connection.close()
        return self.base.read(path)

    def read_tail(self, path, limit):
        if str(path).replace('\\', '/') == '%s/memory-versions.jsonl' % self.role:
            return self._read_versions()[-limit:]
        return self.base.read_tail(path, limit)

    def _append_version(self, row):
        connection = self._connect()
        try:
            old = row.get('old_content')
            connection.execute(
                'INSERT INTO versions(memory_id,time,operation_reason,why_remembered,fields_json,'
                'old_content,old_sha256) VALUES(?,?,?,?,?,?,?)',
                (row.get('memory_id'), brain_writer.now(), row.get('operation_reason') or '',
                 row.get('why_remembered') or '', json.dumps(row.get('fields') or []),
                 old, brain_writer._sha(old) if isinstance(old, str) else None))
            connection.commit()
        finally:
            connection.close()

    def _read_versions(self):
        connection = self._connect()
        try:
            return [{'memory_id': row[0], 'time': row[1], 'operation_reason': row[2],
                     'why_remembered': row[3], 'fields': json.loads(row[4] or '[]'),
                     'old_content': row[5], 'old_sha256': row[6]}
                    for row in connection.execute(
                        'SELECT memory_id,time,operation_reason,why_remembered,fields_json,'
                        'old_content,old_sha256 FROM versions ORDER BY seq')]
        finally:
            connection.close()

    def _append_sidecar(self, row):
        raw = dict(row)
        connection = self._connect()
        try:
            kind = row.get('kind')
            memory_id = row.get('memory_id')
            if kind not in ('link',):
                connection.execute('INSERT INTO sidecar_events(memory_id,kind,time,to_id,via,raw_json) '
                                   'VALUES(?,?,?,?,?,?)',
                                   (memory_id, kind, row.get('time') or brain_writer.now(),
                                    row.get('to'), row.get('via'),
                                    json.dumps(raw, ensure_ascii=False, sort_keys=True)))
            if kind in ('lock', 'locked', 'unlock', 'unlocked'):
                locked = 1 if kind in ('lock', 'locked') else 0
                connection.execute(
                    "INSERT INTO sidecar_state(memory_id,locked,links_json,sources_json) VALUES(?,?, '[]','[]') "
                    'ON CONFLICT(memory_id) DO UPDATE SET locked=excluded.locked', (memory_id, locked))
            elif kind == 'link' and isinstance(row.get('to'), str):
                brain_writer.link(connection, memory_id, row['to'])
            elif kind in ('merged', 'merged_into') and isinstance(row.get('to'), str):
                connection.execute(
                    "INSERT INTO sidecar_state(memory_id,locked,links_json,sources_json,merged_into) "
                    "VALUES(?,0,'[]','[]',?) ON CONFLICT(memory_id) DO UPDATE SET merged_into=excluded.merged_into",
                    (memory_id, row['to']))
            connection.commit()
        finally:
            connection.close()

    def _read_sidecar(self):
        connection = self._connect()
        try:
            rows = []
            for raw in connection.execute('SELECT raw_json FROM sidecar_events ORDER BY seq'):
                try:
                    item = json.loads(raw[0]) if raw[0] else {}
                except ValueError:
                    continue
                if isinstance(item, dict):
                    item.setdefault('time', '')
                    rows.append(item)
            return rows
        finally:
            connection.close()
