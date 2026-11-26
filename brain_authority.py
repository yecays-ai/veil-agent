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


class ProjectionConflict(RuntimeError):
    """The committed body changed between the caller's read and the bundle transaction."""


class ProjectionLocked(RuntimeError):
    """The projection target was locked between the caller's check and the bundle transaction."""


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
                                      args.get('max_results') or 8, embedder,
                                      date_from=str(args.get('date_from') or ''),
                                      date_to=str(args.get('date_to') or ''))
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
        # P6-S1 post-merge review: after an embedding-model switch every stored vector belongs to
        # the old model. Skip the retry before taking the write lock / opening a payment path; the
        # explicit embedding_reindex script is the intended operator step, not repeated paid calls.
        model = self.embedding_model()
        stored_model = ''
        try:
            import brain_read
            with brain_read.BrainRead(self.db_path) as reader:
                stored_model = reader.embedding_model()
        except Exception:
            stored_model = ''
        if stored_model and model and stored_model != model:
            return
        self._last_pending_retry = now
        connection = None
        try:
            connection = self._connect()
            brain_writer.retry_pending_vectors(connection, embed)
        except Exception:
            # P6-S2 review: callers invoke this after their bundle already committed. A reconnect or
            # schema failure here must not turn a committed write into a reported failure; the memory
            # simply stays vector_state='pending' for the next retry.
            pass
        finally:
            if connection is not None:
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
                                                why_remembered=args.get('why_remembered') or '',
                                                archive_raw=bool(args.get('archive_raw')))
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
            query = ('SELECT id,title,name,body,importance,pinned,created FROM memories '
                     'WHERE is_tombstone=0 AND is_deleted=0 '
                     "AND bucket IN ('dynamic','permanent') "
                     "AND (type IS NULL OR type NOT IN ('archived','letter','plan','i','feel')) ")
            try:
                rows = reader.connection.execute(
                    query + "AND id NOT IN (SELECT memory_id FROM delete_requests "
                            "WHERE status='pending') "
                            'ORDER BY pinned DESC, importance DESC, created DESC LIMIT 8').fetchall()
            except sqlite3.OperationalError as exc:
                if 'no such table: delete_requests' not in str(exc).casefold():
                    raise
                rows = reader.connection.execute(
                    query + 'ORDER BY pinned DESC, importance DESC, created DESC LIMIT 8').fetchall()
        return '\n\n'.join('[bucket_id:%s] %s\n%s' % (row['id'], row['title'] or row['name'] or row['id'],
                                                       row['body']) for row in rows)

    def retrieval_settings(self):
        """The operator's [memory.retrieval] weights, not a second hard-coded default (P6-S1 G-A09)."""
        import brain_retrieval
        return brain_retrieval.load_settings((getattr(self.cfg, 'memory', {}) or {}).get('retrieval'))

    def embedding_model(self):
        return str(((getattr(self.cfg, 'memory', {}) or {}).get('model')) or '').strip()

    def breath_search(self, query, limit, embedder, *, date_from='', date_to=''):
        """Active search: the same BrainRetrieval path automatic recall uses, including the
        configured weights and the date_from/date_to filter (P6-S1 G-A09)."""
        import brain_retrieval
        text, _meta = brain_retrieval.search_text(
            self.db_path, query, limit=limit, embedder=embedder,
            settings=self.retrieval_settings(), model=self.embedding_model(),
            date_from=date_from, date_to=date_to)
        return text

    def commit_event_bundle(self, *, event, stage=None, projection=None, title='',
                            importance=None, pinned=None, embedder=None,
                            old_story='', operation_reason='事件故事更新',
                            ledger_rows=(), quotes=(), seen_projection=None,
                            locked_event=None, locked_ledger_rows=(), abort_on_locked=False):
        """P6-S2: atomically commit memory projection (single version), event, stage, and ledgers.

        Ticket 453: the caller's locked check runs outside this transaction, so it is re-read inside
        BEGIN IMMEDIATE. A locked target is never rewritten: ``locked_event`` (with
        ``locked_ledger_rows``) is the reviewed-story view to commit instead of the candidate
        projection, and without such a view a locked projection raises ProjectionLocked.
        ``abort_on_locked`` also refuses a projection-less commit so a pending stage stays retryable
        instead of committing a story under a locked memory.
        """
        import brain_events
        embed = self._embed_callable(embedder)
        with write_lock(self.store, self.cfg.id):
            if self.freeze_path.exists():
                raise RuntimeError('memory writes are frozen for cutover')
            with self.mutex:
                connection = self._connect()
                try:
                    brain_events.ensure_schema(connection)
                    memory_id = event.get('memory_id')
                    result = None
                    locked = False
                    vector, state = (None, None)
                    embed_model = brain_writer.embedding_model_of(embed)
                    if projection is not None and str(projection).strip():
                        vector, state = brain_writer._prepare_memory_vector(projection, None, embed)
                    connection.execute('BEGIN IMMEDIATE')
                    # Ticket 453: the caller's locked check runs before this transaction, so the panel
                    # can lock the target in between. Re-read it under BEGIN IMMEDIATE: never rewrite a
                    # locked body. A prepared reviewed-story view is committed instead; without one a
                    # locked projection is refused so the caller can keep the stage retryable.
                    if memory_id:
                        try:
                            lock_row = connection.execute(
                                'SELECT locked FROM sidecar_state WHERE memory_id=?',
                                (memory_id,)).fetchone()
                        except sqlite3.Error:
                            lock_row = None
                        locked = bool(lock_row and lock_row[0])
                    if locked:
                        if locked_event is not None:
                            event = dict(locked_event)
                            projection = None
                            ledger_rows = tuple(ledger_rows) + tuple(locked_ledger_rows or ())
                        elif abort_on_locked or (projection is not None and str(projection).strip()):
                            raise ProjectionLocked('event projection target is locked')
                    if memory_id and seen_projection is not None:
                        current = connection.execute(
                            'SELECT body FROM memories WHERE id=? AND is_deleted=0 AND is_tombstone=0',
                            (memory_id,)).fetchone()
                        if current is None or current[0].strip() != str(seen_projection).strip():
                            raise ProjectionConflict('event projection changed concurrently')
                    if projection is not None and str(projection).strip():
                        if memory_id:
                            changes = {'body': projection}
                            if title:
                                changes['title'] = title[:100]
                            result = brain_writer.apply_update_memory(
                                connection, memory_id, changes, vector=vector, state=state,
                                operation_reason=operation_reason, embedder_model=embed_model)
                        else:
                            record = {
                                'body': projection,
                                'title': (title or event.get('title') or 'Event')[:100],
                                'type': 'dynamic',
                                'importance': 10 if pinned is True else (importance or 5),
                                'pinned': bool(pinned is True),
                                'domain': [], 'tags': [],
                            }
                            result = brain_writer.apply_create_memory(
                                connection, record, vector=vector, state=state,
                                embedder_model=embed_model)
                            memory_id = result['memory_id']
                            event = {**event, 'memory_id': memory_id}
                    brain_events.upsert_event(connection, dict(event), commit=False, ensure=False)
                    if stage is not None:
                        brain_events.upsert_stage(connection, dict(stage), commit=False, ensure=False)
                        if memory_id:
                            connection.execute(
                                'INSERT OR IGNORE INTO event_sources(event_id,ord,memory_id,part) '
                                'VALUES(?,?,?,?)',
                                (event['event_id'], 0, memory_id, 'projection'))
                    if old_story and old_story != event.get('story'):
                        brain_events.append_ledger(
                            connection, 'event-version',
                            {'event_id': event['event_id'], 'memory_id': memory_id,
                             'old_story': old_story, 'old_content': None,
                             'at': brain_writer.now()})
                    for kind, row in ledger_rows:
                        brain_events.append_ledger(connection, kind, dict(row))
                    if memory_id:
                        next_quote = connection.execute(
                            'SELECT COALESCE(MAX(ord), -1) + 1 FROM quotes WHERE memory_id=?',
                            (memory_id,)).fetchone()[0]
                        for offset, quote in enumerate(quotes):
                            connection.execute(
                                'INSERT INTO quotes(memory_id,ord,speaker,text) VALUES(?,?,?,?)',
                                (memory_id, int(next_quote) + offset,
                                 quote.get('speaker'), quote.get('text')))
                    brain_writer.apply_digest(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
        if embed is not None:
            # Runs after this bundle committed, outside the role write lock (embedding is a network
            # call). retry_pending_vectors re-checks body and vector_state inside its own write
            # transaction, so a concurrent rewrite can never be indexed with a stale vector, and it
            # never raises: a retry failure must not turn a committed write into a reported failure.
            self._retry_pending(embed)
        return {
            'memory_id': (result or {}).get('memory_id') or event.get('memory_id'),
            'index_pending': (result or {}).get('vector_state') == 'pending',
            'changed': bool(result and result.get('changed', True)),
            'locked': locked,
        }

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
