"""P2-A: Brain authority adapter for the two write tools used by memory_native.

Only active when [memory.brain] write_authority = true; the shadow worker is then refused. The
storage layer is transactional (brain_writer); Ombre is never called in this mode.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from brain_lock import write_lock

import brain_writer
from brain_authority_init import BrainDatabaseProblem, prepare as prepare_brain_database


class BrainAuthority:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.db_path = Path(store.root) / cfg.id / 'brain.db'
        if not self.db_path.is_file():
            raise FileNotFoundError(str(self.db_path))
        self.mutex = threading.Lock()
        self.freeze_path = Path(store.root) / cfg.id / 'brain-write-freeze'
        self._last_pending_retry = 0.0
        self._pending_retry_seconds = 60.0

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        brain_writer.ensure_schema(connection)
        return connection

    @staticmethod
    def _embed_callable(embedder):
        """The writer takes a plain function; retrieval takes an object with .embed().

        Keep the caller's object intact here and convert only where the callee needs it. Converting
        once in call() used to hand a bound function to brain_retrieval, which calls embedder.embed()
        and silently fell back to Ombre (P2-B active recall path).
        """
        if embedder is None:
            return None
        method = getattr(embedder, 'embed', None)
        return method if callable(method) else embedder

    def call(self, name, args, embedder=None):
        if name in ('hold', 'trace'):
            with write_lock(self.store, self.cfg.id):
                if self.freeze_path.exists():
                    raise RuntimeError('memory writes are frozen for cutover')
                return self._write(name, args, embedder)
        if name == 'breath':
            return self.breath()
        if name == 'dream':
            return ''
        if name == 'breath_search':
            return self.breath_search(args.get('query') or '',
                                      args.get('max_results') or 8, embedder)
        raise ValueError('unsupported authority tool: %s' % name)

    def _write(self, name, args, embedder):
        embed = self._embed_callable(embedder)
        with self.mutex:
            connection = self._connect()
            try:
                if name == 'hold':
                    result = self._hold(connection, args, embed)
                elif name == 'trace':
                    result = self._trace(connection, args, embed)
                else:
                    raise ValueError('unsupported authority tool: %s' % name)
            finally:
                connection.close()
        if embed is not None:
            # P2-A: a save whose embedding failed is queued as vector_state='pending'. Retry it
            # when an embedder is available again instead of leaving recall blind forever.
            self._retry_pending(embed)
        return result

    def _retry_pending(self, embed):
        now = time.monotonic()
        # 0 means "no retry has happened yet" (and tests use it to force a retry). A fresh
        # runner can have monotonic() below the 60 s interval, so checking 0 as a timestamp
        # would wrongly skip the first retry on the CI machine.
        if self._last_pending_retry and now - self._last_pending_retry < self._pending_retry_seconds:
            return
        self._last_pending_retry = now
        connection = self._connect()
        try:
            brain_writer.retry_pending_vectors(connection, embed)
        except Exception:
            pass
        finally:
            connection.close()

    def _hold(self, connection, args, embedder):
        content = args.get('content') or ''
        target_id = args.get('target_id')
        status = 'saved'
        if not isinstance(content, str) or not content.strip():
            raise ValueError('empty memory content')
        if target_id:
            result = brain_writer.update_memory(
                connection, target_id, {'body': content, 'title': args.get('title')},
                embedder=embedder, operation_reason='hold target_id')
            status = result['status']
            memory_id = result['memory_id']
            for merged in args.get('merge_ids') or []:
                if isinstance(merged, str) and merged != memory_id:
                    brain_writer.merge_into(connection, merged, memory_id, 'hold merge')
        else:
            # P2-C: Brain is the write authority; free tags are not generated here.
            # domain/tags from callers are silently dropped so that Brain never accumulates
            # free-text tags.  category defaults to 'unclassified'; governance runs separately.
            pinned = args.get('pinned') is True
            record = {'body': content, 'title': args.get('title') or '',
                      'type': args.get('type') or 'dynamic',
                      'importance': 10 if pinned else (args.get('importance') or 5),
                      'pinned': pinned,
                      'why_remembered': args.get('why_remembered') or '',
                      'meaning': args.get('meaning') or '',
                      'domain': [],
                      'tags': []}
            result = brain_writer.create_memory(connection, record, embedder=embedder)
            status = result['status']
            memory_id = result['memory_id']
        link_to = args.get('link_to')
        if isinstance(link_to, str) and link_to and link_to != memory_id:
            brain_writer.link(connection, memory_id, link_to)
        prefix = '新建' if not target_id else '合并'
        return '%s→%s%s' % (prefix, memory_id, self._pending_notice(status))

    @staticmethod
    def _pending_notice(status):
        """P2-A three-state feedback: never call a memory fully saved when its index is pending."""
        return '（正文已保存，检索索引待重试）' if status == 'saved_index_pending' else ''

    def _trace(self, connection, args, embedder):
        memory_id = args.get('bucket_id')
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError('missing bucket_id')
        status = 'saved'
        merge_into = args.get('merge_into')
        if isinstance(merge_into, str) and merge_into:
            # P2-A merge: archive the duplicate copy, never treat it as a normal delete.
            brain_writer.merge_into(connection, memory_id, merge_into, 'tool merge')
            return '已归档→%s' % memory_id
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
        if isinstance(args.get('importance'), int):
            changes['importance'] = args['importance']
        if 'pinned' in args and isinstance(args['pinned'], bool):
            changes['pinned'] = args['pinned']
            if args['pinned']:
                changes['importance'] = 10
        if changes:
            result = brain_writer.update_memory(connection, memory_id, changes, embedder=embedder,
                                                operation_reason=args.get('edit_reason') or 'trace',
                                                why_remembered=args.get('why_remembered') or '')
            status = result['status']
        if args.get('archive') in (1, True):
            brain_writer.set_state(connection, memory_id, archived=True)
        if args.get('restore') in (1, True):
            brain_writer.set_state(connection, memory_id, archived=False, deleted=False)
        return '已更新→%s%s' % (memory_id, self._pending_notice(status))


    def breath(self):
        """Opening list: the same pinned/important visible memories, rendered as Ombre blocks."""
        import brain_read
        with brain_read.BrainRead(self.db_path) as reader:
            rows = reader.connection.execute(
                'SELECT id,title,name,body,importance,pinned,created FROM memories '
                'WHERE is_tombstone=0 AND is_deleted=0 '
                "AND bucket IN ('dynamic','permanent') "
                "AND (type IS NULL OR type NOT IN ('archived','letter','plan','i','feel')) "
                'ORDER BY pinned DESC, importance DESC, created DESC LIMIT 8').fetchall()
        return '\n\n'.join('[bucket_id:%s] %s\n%s' % (row['id'], row['title'] or row['name'] or row['id'],
                                                       row['body']) for row in rows)

    def breath_search(self, query, limit, embedder):
        import brain_retrieval
        text, _meta = brain_retrieval.search_text(
            self.db_path, query, limit=limit, embedder=embedder,
            settings={'backend': 'brain', 'weight_vector': 0.65, 'weight_keyword': 0.35})
        return text

def _split(value):
    if isinstance(value, str):
        return [part.strip() for part in value.split(',') if part.strip()]
    return list(value or [])


def attach_authority(cfg, store):
    settings = (getattr(cfg, 'memory', {}) or {}).get('brain') or {}
    if isinstance(settings, dict) and settings.get('shadow') is True:
        raise ValueError('Brain 已成为唯一记忆后端，旧影子同步开关必须先移除')
    # P3-1: a brand-new role gets an empty Brain database; an existing role whose database
    # disappeared or is damaged fails here, before any Memory object can hide the loss.
    prepare_brain_database(store, cfg.id)
    return BrainAuthority(cfg, store)



def freeze(store, role):
    path = Path(store.root) / role / 'brain-write-freeze'
    path.parent.mkdir(parents=True, exist_ok=True)
    with write_lock(store, role):
        path.write_text('frozen for cutover', encoding='utf-8')
    return str(path)


def unfreeze(store, role):
    path = Path(store.root) / role / 'brain-write-freeze'
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return str(path)


def frozen(store, role):
    return (Path(store.root) / role / 'brain-write-freeze').exists()
