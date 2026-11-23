"""P2-A: Brain authority adapter for the two write tools used by memory_native.

Only active when [memory.brain] write_authority = true; the shadow worker is then refused. The
storage layer is transactional (brain_writer); Ombre is never called in this mode.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import brain_writer


class BrainAuthority:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.db_path = Path(store.root) / cfg.id / 'brain.db'
        if not self.db_path.is_file():
            raise FileNotFoundError(str(self.db_path))
        self.mutex = threading.Lock()

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        brain_writer.ensure_schema(connection)
        return connection

    def call(self, name, args, embedder=None):
        if embedder is not None and hasattr(embedder, 'embed'):
            embedder = embedder.embed
        with self.mutex:
            connection = self._connect()
            try:
                if name == 'hold':
                    return self._hold(connection, args, embedder)
                if name == 'trace':
                    return self._trace(connection, args, embedder)
                raise ValueError('unsupported authority tool: %s' % name)
            finally:
                connection.close()

    def _hold(self, connection, args, embedder):
        content = args.get('content') or ''
        target_id = args.get('target_id')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('empty memory content')
        if target_id:
            result = brain_writer.update_memory(
                connection, target_id, {'body': content, 'title': args.get('title')},
                embedder=embedder, operation_reason='hold target_id')
            memory_id = result['memory_id']
            for merged in args.get('merge_ids') or []:
                if isinstance(merged, str) and merged != memory_id:
                    brain_writer.merge_into(connection, merged, memory_id, 'hold merge')
        else:
            record = {'body': content, 'title': args.get('title') or '',
                      'type': args.get('type') or 'dynamic',
                      'importance': args.get('importance') or 5,
                      'why_remembered': args.get('why_remembered') or '',
                      'meaning': args.get('meaning') or '',
                      'domain': _split(args.get('domain')),
                      'tags': _split(args.get('tags'))}
            result = brain_writer.create_memory(connection, record, embedder=embedder)
            memory_id = result['memory_id']
        link_to = args.get('link_to')
        if isinstance(link_to, str) and link_to and link_to != memory_id:
            brain_writer.link(connection, memory_id, link_to)
        prefix = '新建' if not target_id else '合并'
        return '%s→%s' % (prefix, memory_id)

    def _trace(self, connection, args, embedder):
        memory_id = args.get('bucket_id')
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError('missing bucket_id')
        if args.get('delete'):
            brain_writer.set_state(connection, memory_id, deleted=True,
                                   reason=args.get('delete_reason') or '')
            return '已删除→%s' % memory_id
        body = args.get('content')
        old_str, new_str = args.get('old_str'), args.get('new_str')
        if isinstance(old_str, str) and isinstance(new_str, str) and old_str:
            current = connection.execute('SELECT body FROM memories WHERE id=?', (memory_id,)).fetchone()
            if current is None:
                raise KeyError(memory_id)
            body = current[0].replace(old_str, new_str, 1)
        changes = {}
        if isinstance(body, str) and body:
            changes['body'] = body
        if isinstance(args.get('title'), str):
            changes['title'] = args['title']
        if changes:
            brain_writer.update_memory(connection, memory_id, changes, embedder=embedder,
                                       operation_reason=args.get('edit_reason') or 'trace',
                                       why_remembered=args.get('why_remembered') or '')
        if args.get('dont_surface') in (1, True):
            brain_writer.set_state(connection, memory_id, hidden=True)
        return '已更新→%s' % memory_id


def _split(value):
    if isinstance(value, str):
        return [part.strip() for part in value.split(',') if part.strip()]
    return list(value or [])


def attach_authority(cfg, store):
    settings = (getattr(cfg, 'memory', {}) or {}).get('brain') or {}
    if not isinstance(settings, dict) or settings.get('write_authority') is not True:
        return None
    if settings.get('shadow') is True:
        raise ValueError('write_authority=brain and shadow=true are mutually exclusive')
    return BrainAuthority(cfg, store)
