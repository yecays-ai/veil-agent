"""Thin Ombre integration. Memory content and scheduling stay in the role vault."""
import asyncio
import ast
from concurrent.futures import Future
from contextlib import contextmanager, asynccontextmanager
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import random
import re
import subprocess
import threading
import uuid

import prompts

from agent_types import UserError
from recall_judge import JudgeFailed

RECALL_DIAGNOSTICS_KEEP = 200


def vault_path(root, character):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', character):
        raise ValueError('invalid character')
    root = Path(root).resolve()
    vault = root / character / 'memory'
    if vault.resolve() != vault or not vault.is_relative_to(root):
        raise ValueError('memory directory redirects outside its role')
    return vault


class MemoryToolError(RuntimeError):
    """Safe diagnostic codes, never server text, credentials or memory contents."""
    def __init__(self, code, *, stage=None):
        self.code = code
        self.stage = stage if stage is not None else ('mcp_tool' if code == 'tool' else None)
        messages = {
            'connection': 'The memory connection is unavailable; the safe read still failed after reconnect, so try again later.',
            'tool': 'The memory service rejected this tool call; it was not retried.',
            'unknown': 'The memory operation outcome is unconfirmed; it was not retried, so verify its state before repeating changes.',
            'timeout': 'The memory call timed out with an unconfirmed result; check its state before retrying.',
            'closed': 'The memory connection is closed; this operation was not submitted.',
        }
        super().__init__(messages[code])


class MCPStageError(RuntimeError):
    """Classify a transport boundary without retaining its private details."""
    def __init__(self, stage):
        self.stage = stage
        super().__init__(stage)


class MCP:
    """One async owner; each operation gets a fresh, bounded HTTP connection."""
    # Search can invoke a metered embedding provider; do not replay it.
    RETRYABLE_READS = frozenset({'pulse'})

    def __init__(self, url, token, *, recovery=None):
        self.url, self.token = url, token
        self.recovery = recovery
        self.recovery_count = 0
        self.recovery_streak = 0  # recoveries since the last healthy reply
        self.diagnostic = None
        self.closed = False
        self.state_lock = threading.Lock()
        self.current_result = None
        self.ready = Future()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        try:
            self.ready.result(timeout=90)
        except Exception:
            self.close()
            raise

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            with self.state_lock:
                self.closed = True
            self._diagnose('connection', exc, 0)
            if not self.ready.done():
                self.ready.set_exception(RuntimeError('Ombre startup failed'))
            if hasattr(self, 'requests'):
                if self.current_result is not None and not self.current_result.done():
                    self.current_result.set_exception(MemoryToolError('unknown'))
                while not self.requests.empty():
                    job = self.requests.get_nowait()
                    if job is not None and not job[2].done():
                        job[2].set_exception(MemoryToolError('closed'))

    @asynccontextmanager
    async def _session(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        headers = {'Authorization': f'Bearer {self.token}'}
        try:
            async with streamablehttp_client(self.url, headers=headers) as (read, write, _):
                body_error = None
                try:
                    async with ClientSession(read, write) as session:
                        try:
                            await session.initialize()
                        except Exception as exc:
                            raise MCPStageError('mcp_initialize') from exc
                        try:
                            yield session
                        except BaseException as exc:
                            body_error = exc
                            raise
                except MCPStageError:
                    raise
                except Exception as exc:
                    if body_error is not None:
                        raise body_error
                    raise MCPStageError('mcp_session') from exc
        except MCPStageError:
            raise
        except Exception as exc:
            raise MCPStageError('http_session') from exc

    @staticmethod
    def _never_sent(exc):
        """True only when the failure was a refused connection, before any request was sent."""
        seen = set()
        def refused(error):
            if error is None or id(error) in seen:
                return False
            seen.add(id(error))
            if isinstance(error, ConnectionRefusedError) or type(error).__name__ == 'ConnectError':
                return True
            children = getattr(error, 'exceptions', ())
            if children:
                # Every grouped failure must prove non-delivery; a mixed group is uncertain.
                return all(refused(child) for child in children)
            return refused(error.__cause__) or refused(error.__context__)
        return refused(exc)

    def _diagnose(self, name, exc, attempt):
        kinds = []
        stages = []
        seen = set()
        def visit(error):
            if id(error) in seen:
                return
            seen.add(id(error))
            kinds.append(type(error).__name__)
            stage = getattr(error, 'stage', None)
            if stage in {'http_session', 'mcp_session', 'mcp_initialize', 'mcp_tool',
                         'mcp_verified', 'recovery'}:
                stages.append(stage)
            for child in getattr(error, 'exceptions', ()):
                visit(child)
            if error.__cause__ is not None:
                visit(error.__cause__)
            elif error.__context__ is not None:
                visit(error.__context__)
        visit(exc)
        if self.diagnostic:
            try:
                facts = {'code': 'mcp_call', 'tool': name, 'attempt': attempt,
                         'error_types': sorted(set(kinds))[:8]}
                if stages:
                    facts['stage'] = stages[0]
                self.diagnostic(facts)
            except Exception:
                pass  # Logging must not mask the tool's safe failure message.

    async def _perform(self, name, args):
        response = None
        try:
            async with self._session() as session:
                try:
                    self.active_stage = 'mcp_tool'
                    response = await session.call_tool(name, args)
                except Exception as exc:
                    raise MCPStageError('mcp_tool') from exc
        except Exception as exc:
            if response is None:
                raise
            # A confirmed reply remains evidence even if closing HTTP fails.
            self._diagnose(name, exc, 0)
        if response.isError:
            raise MemoryToolError('tool')
        return '\n'.join(c.text for c in response.content if hasattr(c, 'text'))

    async def _recover(self, name, attempt):
        if self.recovery is None or self.recovery_streak >= 2:
            return False
        self.recovery_count += 1
        self.recovery_streak += 1
        try:
            recovery_result = await asyncio.to_thread(self.recovery)
        except Exception as exc:
            stage = getattr(exc, 'stage', 'recovery')
            self._diagnose(name, exc, attempt)
            if self.diagnostic:
                try:
                    self.diagnostic({'code': 'recovery_failed', 'stage': stage,
                                     'tool': name, 'attempt': self.recovery_count})
                except Exception:
                    pass
            return False
        if self.diagnostic:
            try:
                facts = {'code': 'recovery_succeeded', 'stage': 'mcp_verified',
                         'attempt': self.recovery_count}
                if isinstance(recovery_result, dict):
                    facts['state'] = recovery_result.get('state')
                    facts['exit_code'] = recovery_result.get('exit_code')
                self.diagnostic(facts)
            except Exception:
                pass
        return True

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.requests = asyncio.Queue()
        async with asyncio.timeout(85):
            async with self._session() as session:
                self.tool_specs = (await session.list_tools()).tools
        self.ready.set_result(True)
        while True:
            job = await self.requests.get()
            if job is None:
                return
            name, args, result = job
            if not result.set_running_or_notify_cancel():
                continue
            self.current_result = result
            self.active_stage = None
            try:
                async with asyncio.timeout(200):
                    attempts = 2 if name in self.RETRYABLE_READS else 1
                    attempt = 0
                    resent = False
                    while attempt < attempts:
                        attempt += 1
                        try:
                            async with asyncio.timeout(40 if attempts == 2 else 85):
                                self.active_stage = 'mcp_session'
                                text = await self._perform(name, args)
                            break
                        except MemoryToolError:
                            raise
                        except Exception as exc:
                            self._diagnose(name, exc, attempt)
                            if attempt == attempts and name in self.RETRYABLE_READS:
                                raise MemoryToolError('connection') from exc
                            restored = await self._recover(name, attempt)
                            if restored and attempts == 1 and self._never_sent(exc):
                                # Ombre refused the connection, so this call never
                                # reached it; after a verified restart it is safe to send once.
                                attempts = 2
                                resent = True
                                continue
                            if attempt == attempts:
                                # A failed resend may have reached Ombre, so its outcome stays unknown.
                                raise MemoryToolError(
                                    'connection' if attempts == 2 and not resent else 'unknown',
                                    stage=('recovery' if self.recovery is not None and not restored
                                           else getattr(exc, 'stage', None))) from exc
                            if self.recovery is not None and not restored:
                                raise MemoryToolError('connection', stage='recovery') from exc
                    if attempt > 1 and self.diagnostic:
                        try:
                            self.diagnostic({'code': 'retry_succeeded', 'stage': 'mcp_verified',
                                             'tool': name, 'attempt': attempt})
                        except Exception:
                            pass
                    self.recovery_streak = 0  # A healthy reply restores the recovery allowance.
                    result.set_result(text)
            except TimeoutError as exc:
                self._diagnose(name, exc, 0)
                # Inner transport handling already attempted bounded recovery when eligible.
                # A write timeout stays uncertain and is never replayed.
                result.set_exception(MemoryToolError('timeout', stage=self.active_stage))
            except MemoryToolError as exc:
                self._diagnose(name, exc, 0)
                # Do not hand another thread a live coroutine traceback (or its
                # private transport cause). Consumers may clear its frames.
                result.set_exception(MemoryToolError(exc.code, stage=exc.stage))
            finally:
                self.active_stage = None
                self.current_result = None

    def call(self, name, args):
        result = Future()
        with self.state_lock:
            if self.closed or not self.thread.is_alive():
                raise MemoryToolError('closed')
            # Every request ahead is bounded by _serve's 200-second job timeout.
            # Include one possible in-flight job and this request's own window.
            wait_timeout = (self.requests.qsize() + 2) * 210
            try:
                self.loop.call_soon_threadsafe(self.requests.put_nowait, (name, args, result))
            except RuntimeError as exc:
                raise MemoryToolError('closed') from exc
        try:
            return result.result(timeout=wait_timeout)
        except TimeoutError as exc:
            raise MemoryToolError('closed' if result.cancel() else 'timeout') from exc

    def list_tools(self):
        return tuple(getattr(self, 'tool_specs', ()))

    def close(self):
        with self.state_lock:
            self.closed = True
            if self.thread.is_alive() and hasattr(self, 'loop') and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.requests.put_nowait, None)
        if self.thread.is_alive():
            self.thread.join(timeout=215)
        if self.thread.is_alive():
            raise RuntimeError('Ombre did not stop')


# The exact line Ombre appends to a search result when the vector query was unavailable or failed.
DEGRADED_NOTICE = '[检索降级：语义索引暂不可用'


def ombre_model_on(cfg):
    return bool((cfg.memory or {}).get('ombre_model', True))


# Pinned Ombre 115d831d uses analyze(max_tokens=4096), judge_same_event(max_tokens=1024),
# and retries transient provider errors up to three total attempts. VEIL reserves this
# upper estimate before MCP entry, then settles the normal estimate on confirmed success.
OMBRE_ANALYZE_PROMPT_BYTES, OMBRE_JUDGE_PROMPT_BYTES = 3200, 1000
OMBRE_OLD_MEMORY_BYTES = 5400
OMBRE_ANALYZE_OUTPUT_TOKENS, OMBRE_JUDGE_OUTPUT_TOKENS = 4096, 1024
OMBRE_MODEL_ATTEMPTS = 3
OMBRE_OUTPUT_TOKENS = 800


def ombre_model_tokens(content, *, worst_case=False):
    """Return normal or maximum estimated tokens for one hold's synchronous model work.

    The reserve covers pinned retries and output caps. It is still an estimate,
    not a hard limit on Ombre's asynchronous plan-resolution requests.
    """
    size = len(content.encode('utf-8'))
    data = (OMBRE_ANALYZE_PROMPT_BYTES + min(size, 6000) + OMBRE_JUDGE_PROMPT_BYTES
            + min(size, 5400) + OMBRE_OLD_MEMORY_BYTES)
    attempts = OMBRE_MODEL_ATTEMPTS if worst_case else 1
    output = (OMBRE_ANALYZE_OUTPUT_TOKENS + OMBRE_JUDGE_OUTPUT_TOKENS
              if worst_case else OMBRE_OUTPUT_TOKENS)
    return (data // 2) * attempts, output * attempts


@contextmanager
def open_ombre(root, cfg, *, data_root=None, api_key_overrides=None):
    from dotenv import dotenv_values
    from ombre_resident import mark_ready, start_or_connect
    root = Path(root)
    resident = None
    backend = None
    try:
        settings = cfg.memory
        if not settings['base_url'].startswith('https://'):
            raise ValueError('HTTPS required')
        rate = Decimal(str(settings['input_cny_per_million']))
        if not rate.is_finite() or rate < 0:
            raise ValueError('invalid embedding price')
        api_key_overrides = api_key_overrides or {}
        local_keys = None
        needs_local_embed = (settings.get('embedding_enabled', True)
                             and 'OMBRE_EMBED_API_KEY' not in api_key_overrides)
        needs_local_model = (ombre_model_on(cfg)
                             and 'OMBRE_COMPRESS_API_KEY' not in api_key_overrides)
        if needs_local_embed or needs_local_model:
            local_keys = dotenv_values(root / '.env', interpolate=False,
                                       encoding='utf-8-sig')
        if settings.get('embedding_enabled', True):
            key = api_key_overrides.get('OMBRE_EMBED_API_KEY',
                                        (local_keys or {}).get('EMBED_API_KEY'))
            if not key or not key.strip():
                raise UserError('本机 .env 缺少 EMBED_API_KEY，请填写后重开。')
        vault = vault_path(data_root if data_root is not None else root / 'data', cfg.id)
        dep = root / '.deps' / 'ombre'
        executable = dep / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.run([str(executable), '-c',
                        'import jieba, rank_bm25; from mcp.client.streamable_http import streamablehttp_client'],
                       check=True, capture_output=True, timeout=20)
        if ombre_model_on(cfg):
            model_key = api_key_overrides.get('OMBRE_COMPRESS_API_KEY',
                (local_keys or {}).get((getattr(cfg, 'background_provider', None) or cfg.provider)['api_key_env']))
            if not model_key or not model_key.strip():
                raise UserError('本机 .env 缺少聊天模型的 API Key（记忆打标和合并要用），请填写后重开。')
        port, token, resident = start_or_connect(
            root, cfg, data_root=data_root, api_key_overrides=api_key_overrides)
    except UserError:
        raise
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise UserError('记忆依赖或配置缺失（含 jieba / rank-bm25），请先重新运行 setup.bat。') from exc
    except Exception as exc:
        raise UserError('记忆进程启动失败，请重新运行 setup.bat；本机记录仍保留。') from exc
    try:
        try:
            backend = MCP(f'http://127.0.0.1:{port}/mcp', token,
                          recovery=getattr(resident, 'recover', None))
            mark_ready(vault / '_agent')
        except Exception as exc:
            raise UserError('记忆进程启动失败，请重新运行 setup.bat；本机记录仍保留。') from exc
        # Errors raised by the chat session itself keep their own message.
        yield backend
    finally:
        close_error = None
        if backend:
            try:
                backend.close()
            except Exception as exc:
                close_error = UserError('记忆连接未能正常退出，请保留记忆库后关闭窗口。')
                close_error.__cause__ = exc
        if resident:
            try:
                resident.close()
            except Exception as exc:
                if close_error is None:
                    close_error = UserError('记忆进程未能正常退出，请保留记忆库后关闭窗口。')
                    close_error.__cause__ = exc
        if close_error:
            raise close_error


def blocks(text):
    matches = list(re.finditer(r'\[bucket_id:([\w-]+)\]', text))
    return [(m.group(1), text[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)])
            for i, m in enumerate(matches)]


def ordinary_hits(text, limit=3):
    match = re.search(r'=== ombre:result-ids ===\s*```json\s*(.*?)\s*```', text, re.S)
    if not match:
        return []  # Exact-ID and random surfacing paths are not automatic hits.
    allowed = json.loads(match.group(1))['bucket_ids'][:limit]
    clean = text.split('=== 忽然想起来')[0].split('=== ombre:result-ids ===')[0]
    found = dict(blocks(clean))
    return [(key, found[key]) for key in allowed if key in found]


# Extra Ombre hits asked for beyond the three slots, so archived or hidden ones can be dropped without emptying a slot.
SEARCH_SPARE_HITS = 5
ERASE_NOTE = '记忆文件和程序保存的旧版本都已从硬盘抹掉。'


class Memory:
    # Ticket 419: the request builder may defer the memory budget to finish_recall. Explicit marker
    # so test doubles (SimpleNamespace/Mock) keep the old one-call path instead of being treated as
    # a real budgeter just because getattr creates an attribute.
    budgets_recall = True

    def __init__(self, cfg, store, budget, model, backend, runtime_monitor=None):
        self.cfg, self.store, self.budget, self.model, self.backend = cfg, store, budget, model, backend
        self.runtime_monitor = runtime_monitor
        vault_path(store.root, cfg.id)
        self.path = f'{cfg.id}/memory/_agent/state.jsonl'
        self.diagnostics = f'{cfg.id}/memory/_agent/diagnostics.jsonl'
        # This is an operation ledger, not a copy of memory text.  Ombre's
        # pinned hold API has no caller-supplied idempotency key or result
        # lookup, so an unresolved local operation must remain unresolved.
        self.write_reconciliation = f'{cfg.id}/memory/_agent/write-reconciliation.jsonl'
        self.write_fingerprint_key = vault_path(store.root, cfg.id) / '_agent' / 'write-reconciliation.key'
        self._fingerprint_key = self._load_fingerprint_key()
        if isinstance(backend, MCP):
            def diagnostic(facts):
                try:
                    if runtime_monitor:
                        runtime_monitor.observe_memory(facts)
                finally:
                    try:
                        self.store.append(self.diagnostics, facts)
                    except Exception:
                        pass  # Safety state is independent from best-effort diagnostics.
            backend.diagnostic = diagnostic
        self.delete_asked = None  # number shown by /delete, waiting for its 确认
        states = store.read(self.path)
        budget.reconcile_unsettled({'ombre-model-estimate', 'embedding-estimate',
                                    'memory-extraction'})
        try:
            self.state = states[-1]['state'] if states else {'entries': {}, 'sources': {}}
            if not isinstance(self.state, dict):
                raise ValueError('invalid memory state')
            legacy_pending_writes = self.state.pop('pending_writes', [])
            # The retired background extractor (ticket 090) kept a chat cursor here.
            self.state.pop('cursor', None)
            self.state.pop('pending', None)
            if (not isinstance(self.state['entries'], dict)
                    or not isinstance(self.state['sources'], dict)
                    or not isinstance(legacy_pending_writes, list)):
                raise ValueError('invalid memory state')
            for write in legacy_pending_writes:
                if (not isinstance(write, dict)
                        or not re.fullmatch(r'[0-9a-f]{32}', str(write.get('operation_id', '')))
                        or not re.fullmatch(r'[0-9a-f]{64}', str(write.get('fingerprint', '')))
                        or (write.get('submitted', True) is not True and write.get('submitted') is not False)):
                    raise ValueError('invalid pending write')
        except (ValueError, TypeError, KeyError) as exc:
            raise UserError('记忆调度记录损坏，已停止；请保留记忆库，不要删记录。') from exc
        self.mutex = threading.RLock()
        self.pending_writes = legacy_pending_writes
        self._reconcile_pending_writes()
        self.messages = queue.Queue()
        self._turn_notice_local = threading.local()
        self.last_context_refs = []
        # Ticket 183: IDs Peter was shown (automatic recall or his own search), newest last.
        self.surfaced_ids = {}
        # Ticket 353: automatic-recall cooldown and rotation, by memory ID only; survives restarts.
        from recall_rotation import Cooldown, settings as recall_settings
        self.recall_settings = recall_settings(cfg.memory)
        self.rotation_path = f'{cfg.id}/memory/_agent/recall-rotation.jsonl'
        try:
            saved = store.read(self.rotation_path)
        except Exception:
            saved = []  # a damaged cooldown file only means memories may return sooner
        self.rotation = Cooldown(saved[-1] if saved else None)
        self.rng = random.Random()
        self.last_context_kinds = {}
        self.last_recall_skipped = {'cooldown': 0, 'same_event': 0, 'stage_cap': 0, 'budget': 0}
        self.last_recall_marks = None
        # Ticket 419: the memories the previous turn actually injected (for an immediate follow-up)
        # and the budget report of the last finished recall.
        self.last_injected_refs = []
        self._previous_injected_refs = []
        self.last_memory_budget = None
        self._pending_recall = None
        self.bucket_details_cache = {}
        self.native_hidden = set()
        self.judge = None   # ticket 382: attached by the running app only (attach_recall_judge)
        try:
            self.opening = self.call('breath', {})
        except Exception as exc:
            raise UserError('开场记忆读取失败，请保留记忆库后重开。') from exc
        self.persist()
        erased = self.erase_deleted()
        if erased:
            self.messages.put(f'已彻底抹掉 {len(erased)} 条之前删除的记忆。')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if getattr(self, 'judge', None) is not None:
            self.judge.close()
        embedder = getattr(self, '_embedder', None)
        if embedder is not None and hasattr(embedder, 'close'):
            embedder.close()

    def persist(self):
        # Only the newest snapshot is kept (ticket 092); older ones held copies of erased memories.
        self.store.replace(self.path, {'state': self.state})

    def _load_fingerprint_key(self):
        """Keep the dedupe secret out of the readable operation ledger."""
        self.write_fingerprint_key.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = self.write_fingerprint_key.read_bytes()
        except FileNotFoundError:
            key = os.urandom(32)
            try:
                with self.write_fingerprint_key.open('xb') as handle:
                    handle.write(key)
                try:
                    os.chmod(self.write_fingerprint_key, 0o600)
                except OSError:
                    pass
            except FileExistsError:
                key = self.write_fingerprint_key.read_bytes()
        if len(key) != 32:
            raise ValueError('invalid write reconciliation key')
        return key

    def write_fingerprint(self, args):
        """Local duplicate guard only; it is never presented as Ombre evidence."""
        payload = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hmac.new(self._fingerprint_key, payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _reconcile_pending_writes(self):
        """Repair only local guards from the append-only operation ledger.

        A claim is made durable before the request can be dispatched.  A
        claim with no submitted event therefore proves that no request was
        sent and may be discarded after a restart.  Conversely, an event that
        says submitted is deliberately treated as unknown until a terminal
        Ombre result is recorded.
        """
        rows = self.store.read(self.write_reconciliation)
        submitted = {row.get('operation_id') for row in rows if row.get('status') == 'submitted'}
        pending = {row['operation_id']: row for row in self.pending_writes
                   if row.get('submitted', True) or row['operation_id'] in submitted}
        for row in rows:
            operation_id = row.get('operation_id')
            if row.get('status') == 'submitted' and isinstance(row.get('fingerprint'), str):
                pending[operation_id] = {'operation_id': operation_id,
                                         'fingerprint': row['fingerprint'], 'submitted': True}
            elif row.get('status') in {'confirmed', 'not_submitted'}:
                pending.pop(operation_id, None)
        self.pending_writes = list(pending.values())
        # Remove the old embedded guard once.  Future state snapshots stay
        # bounded; the append-only operation ledger is its sole durable home.
        if 'pending_writes' in self.state:
            self.state.pop('pending_writes', None)
            self.persist()

    def begin_write(self, args):
        """Durably claim one local hold attempt before it reaches Ombre."""
        fingerprint = self.write_fingerprint(args)
        with self.mutex:
            prior = next((row for row in self.pending_writes
                          if row['fingerprint'] == fingerprint), None)
            if prior:
                return None, prior['operation_id']
            operation_id = uuid.uuid4().hex
            self.pending_writes.append({'operation_id': operation_id,
                                        'fingerprint': fingerprint, 'submitted': False})
            # Do not call Ombre until this durable event has been recorded.
            # If the process dies before it, the false claim is safe to clear
            # on restart; if it dies after it, fail closed as unconfirmed.
            self.store.append(self.write_reconciliation, {
                'operation_id': operation_id, 'fingerprint': fingerprint, 'status': 'submitted'})
            self.pending_writes[-1]['submitted'] = True
        return operation_id, None

    def finish_write(self, operation_id, status, *, memory_id=None, error=None):
        """Close only proven outcomes; unknown results intentionally stay pending."""
        if status not in {'confirmed', 'not_submitted', 'unconfirmed'}:
            raise ValueError('invalid write status')
        row = {'operation_id': operation_id, 'status': status}
        if memory_id is not None:
            row['memory_id'] = memory_id
        if error is not None:
            row['error_type'] = self.safe_error_type(error)
            if error_code := self.safe_error_code(error):
                row['error_code'] = error_code
        # The terminal ledger event comes first.  A crash before the local
        # guard is removed is repaired from that event at next startup; doing
        # it in the opposite order could replay a committed write.
        self.store.append(self.write_reconciliation, row)
        if status in {'confirmed', 'not_submitted'}:
            with self.mutex:
                self.pending_writes = [row for row in self.pending_writes
                                       if row['operation_id'] != operation_id]

    def write_failure(self, operation_id, exc):
        # These two MCP codes mean a request did not enter its operation
        # queue.  Every other transport/service failure can be after Ombre
        # committed the write.
        status = ('not_submitted' if getattr(exc, 'code', None) in {'closed', 'connection'}
                  else 'unconfirmed')
        self.finish_write(operation_id, status, error=exc)
        return status

    def call(self, name, args, *, write_operation=None):
        reservation = None
        model_reservation = None
        normal_embedding_amount = None
        normal_model_amount = None
        try:
            if (name in {'hold', 'breath_search', 'feel', 'dream', 'pulse', 'anchor',
                         'release', 'letter_read', 'letter_write'}
                    or (name == 'trace' and args.get('content') not in (None, ''))
                    or (name == 'trace' and any(args.get(key) for key in
                        ('old_str', 'new_str', 'meaning_append', 'meaning_replace')))
                    or (name == 'plan' and args.get('content'))
                    or (name == 'I' and (args.get('content') or args.get('promote') or args.get('supersedes')))
                    or (name == 'letter_write' and args.get('content'))
                    or (name == 'letter_read' and args.get('query'))):
                normal_tokens = max(
                    8192, len(json.dumps(args, ensure_ascii=False).encode('utf-8')) + 2048)
                reserve_tokens = 12 * normal_tokens
                rate = Decimal(str(self.cfg.memory['input_cny_per_million']))
                reservation = self.budget.estimate(
                    self.cfg.id, self.cfg.memory['model'], reserve_tokens, rate)
                normal_embedding_amount = Decimal(normal_tokens) * rate / Decimal(1_000_000)
            if name == 'hold' and ombre_model_on(self.cfg):
                content = str(args.get('content', ''))
                tokens_in, tokens_out = ombre_model_tokens(content, worst_case=True)
                normal_in, normal_out = ombre_model_tokens(content)
                p = getattr(self.cfg, 'background_provider', None) or self.cfg.provider
                # Ticket 134: a per-request price covers analyze + judge, each retried up to three times.
                per_call = p.get('cny_per_call')
                model_reservation = self.budget.estimate_model(
                    self.cfg.id, p['model'], tokens_in, tokens_out,
                    p['input_cny_per_million'], p['output_cny_per_million'],
                    fixed=per_call * 2 * OMBRE_MODEL_ATTEMPTS if per_call else None)
                normal_model_amount = per_call * 2 if per_call else (
                    Decimal(normal_in) * p['input_cny_per_million']
                    + Decimal(normal_out) * p['output_cny_per_million']
                ) / Decimal(1_000_000)
        except Exception:
            # If a later preflight rejects, earlier reservations are known not to
            # have reached the child process and can safely be released.
            if reservation is not None:
                self.budget.release_estimate(reservation)
            if name in {'hold', 'trace'} and write_operation is not None:
                self.finish_write(write_operation, 'not_submitted')
            raise
        try:
            result = self.backend.call(name, args)
        except Exception as exc:
            # A child-process timeout or disconnect can happen after the provider
            # accepted a request. Keep the full conservative estimate as spent.
            if reservation is not None:
                self.budget.settle_estimate(reservation, accounting='embedding-estimate')
            if model_reservation is not None:
                self.budget.settle_estimate(model_reservation, accounting='ombre-model-estimate')
            self.tool_failure(name, exc)
            if name in {'hold', 'trace'} and write_operation is not None:
                self.write_failure(write_operation, exc)
            raise
        if name in {'hold', 'trace'} and write_operation is not None:
            # This is the earliest point at which Ombre's own response is
            # available.  Do not let a later cache/index failure turn a
            # confirmed server result back into a replayable pending write.
            try:
                memory_id = args.get('bucket_id') if name == 'trace' else self.created_id(result)
            except ValueError:
                # A malformed hold reply does not identify the resulting
                # memory, so it is not sufficient evidence of this operation.
                self.finish_write(write_operation, 'unconfirmed')
                raise MemoryToolError('unknown')
            self.finish_write(write_operation, 'confirmed', memory_id=memory_id)
        if reservation is not None:
            self.budget.settle_estimate(
                reservation, accounting='embedding-estimate',
                settled_amount=normal_embedding_amount)
        if model_reservation is not None:
            self.budget.settle_estimate(
                model_reservation, accounting='ombre-model-estimate',
                settled_amount=normal_model_amount)
        # Only Ombre's own notice counts, and Ombre always puts it on the first line, ahead of any memory text.
        if result.lstrip().startswith(DEGRADED_NOTICE):
            self._notice_once('degraded-search', f'Memory search is using keyword fallback ({name}); see memory/_agent/server.log for the cause.')
        return result

    def visible(self, candidates):
        with self.mutex:
            hidden = {k for k, v in self.state['entries'].items() if v.get('hidden')}
            hidden |= self.native_hidden
            parents = set(self.state['sources'].values())
            return [(k, text) for k, text in candidates if k not in hidden and k not in parents]

    @staticmethod
    def _frontmatter_scalar(value):
        scalar = value.strip()
        if scalar.startswith('"'):
            try:
                scalar = ast.literal_eval(scalar)
            except (SyntaxError, ValueError):
                pass
        elif scalar.startswith("'") and scalar.endswith("'"):
            scalar = scalar[1:-1].replace("''", "'")
        return str(scalar)

    def bucket_details(self, memory_id, *, refresh=False):
        """Resolve an Ombre title and exact body from its role-local Markdown record."""
        if not refresh and memory_id in self.bucket_details_cache:
            return self.bucket_details_cache[memory_id]
        vault = self.store.root / self.cfg.id / 'memory'
        try:
            paths = list(vault.rglob('*.md'))
            named = [p for p in paths
                     if p.stem == memory_id or p.stem.endswith('_' + memory_id)]
            candidates = [*named, *(p for p in paths if p not in named)]
            for index, path in enumerate(candidates):
                raw = path.read_text(encoding='utf-8')
                lines = raw.splitlines(keepends=True)
                if not lines or lines[0].strip() != '---':
                    continue
                end = next((i for i, line in enumerate(lines[1:], start=1)
                            if line.strip() == '---'), len(lines))
                header = lines[1:end]
                values = {}
                for line in header:
                    key, sep, value = line.partition(':')
                    if sep and not line[:1].isspace() and key.strip() in {'id', 'title', 'name'}:
                        values[key.strip()] = self._frontmatter_scalar(value)
                if values.get('id') and values['id'] != memory_id:
                    continue
                if not values.get('id') and index >= len(named):
                    continue
                body = ''.join(lines[end + 1:]) if end < len(lines) else ''
                details = (values.get('title') or values.get('name'), body)
                self.bucket_details_cache[memory_id] = details
                return details
        except OSError:
            pass
        self.bucket_details_cache[memory_id] = (None, None)
        return None, None

    def title_for(self, memory_id):
        """Resolve a result's display title from its Ombre record or local state."""
        title, _ = self.bucket_details(memory_id)
        if title:
            return title
        entry = self.state['entries'].get(memory_id)
        return str(entry['title']) if entry and entry.get('title') else None

    def content_for(self, memory_id, *, refresh=False):
        """Return exact stored content, never a rendered search snippet."""
        _, content = self.bucket_details(memory_id, refresh=refresh)
        return content

    def raw_content_for(self, memory_id):
        """Return the complete role-local Markdown file for an exact memory ID."""
        if not isinstance(memory_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', memory_id):
            return None
        vault = (self.store.root / self.cfg.id / 'memory').resolve()
        try:
            candidates = sorted(vault.rglob('*.md'), key=lambda path: (
                0 if path.stem == memory_id or path.stem.endswith('_' + memory_id) else 1,
                path.as_posix()))
            for path in candidates:
                if path.is_symlink() or not path.resolve().is_relative_to(vault):
                    continue
                raw = path.read_text(encoding='utf-8')
                lines = raw.splitlines()
                if not lines or lines[0].strip() != '---':
                    continue
                end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), len(lines))
                metadata = {}
                for line in lines[1:end]:
                    key, sep, value = line.partition(':')
                    if sep and not line[:1].isspace() and key.strip() in {'id', 'title', 'name'}:
                        metadata[key.strip()] = self._frontmatter_scalar(value)
                if metadata.get('id') == memory_id or (not metadata.get('id') and
                        (path.stem == memory_id or path.stem.endswith('_' + memory_id))):
                    return raw
        except OSError:
            return None
        return None

    def tags_for(self, memory_id):
        raw = self.raw_content_for(memory_id)
        if raw is None:
            return []
        lines = raw.splitlines()
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), len(lines)) \
            if lines and lines[0].strip() == '---' else 0
        for index, line in enumerate(lines[1:end], start=1):
            key, sep, value = line.partition(':')
            if not sep or key.strip() != 'tags':
                continue
            value = value.strip()
            if value:
                try:
                    parsed = ast.literal_eval(value)
                    if isinstance(parsed, (list, tuple)):
                        return [str(tag) for tag in parsed if str(tag).strip()]
                except (SyntaxError, ValueError):
                    pass
                return [tag.strip().strip("'\"") for tag in value.strip('[]').split(',') if tag.strip()]
            result = []
            for item in lines[index + 1:end]:
                if not item.strip():
                    continue
                match = re.match(r'^\s+-\s*(.+?)\s*$', item)
                if not match:
                    break
                result.append(match.group(1).strip("'\""))
            return result
        return []

    def deleted_for(self, memory_id):
        raw = self.raw_content_for(memory_id)
        if raw is None:
            return False
        lines = raw.splitlines()
        if not lines or lines[0].strip() != '---':
            return False
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), len(lines))
        for line in lines[1:end]:
            key, sep, value = line.partition(':')
            if sep and not line[:1].isspace() and key.strip() == 'deleted_at':
                return value.strip().strip(chr(39) + chr(34)).casefold() not in ('', 'null', 'none', 'false')
        return False

    def _event_map(self):
        """Ticket 407: memory_id -> event_id, read from the event ledger; empty when the layer is off."""
        try:
            rows = self.store.read(f'{self.cfg.id}/events.jsonl')
        except Exception:
            return {}
        owners = {}
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get('memory_id'), str) and isinstance(row.get('event_id'), str):
                owners[row['memory_id']] = row['event_id']
        return owners

    def _event_stories(self):
        """Ticket 419: event_id -> {story, title, first_at} from the event ledger.

        The complete reviewed story is what automatic recall should prefer over one old fragment;
        an absent or damaged ledger only means no upgrade, never a failed recall."""
        path = self.store.root / f'{self.cfg.id}/events.jsonl'
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return {}
        cached = getattr(self, '_event_stories_cache', None)
        if cached and cached[0] == signature:
            return cached[1]
        stories = {}
        try:
            for row in self.store.read(f'{self.cfg.id}/events.jsonl'):
                if not isinstance(row, dict) or not isinstance(row.get('event_id'), str):
                    continue
                story = row.get('story')
                if isinstance(story, str) and story.strip():
                    stories[row['event_id']] = {'story': story.strip(),
                                                'title': row.get('title') or '',
                                                'first_at': row.get('first_at') or ''}
        except Exception:
            return {}
        self._event_stories_cache = (signature, stories)
        return stories

    def hidden_ids(self):
        with self.mutex:
            hidden = {key for key, row in self.state['entries'].items() if row.get('hidden')}
            return hidden | self.native_hidden | set(self.state['sources'].values())

    def _notice_once(self, key, text):
        seen = getattr(self._turn_notice_local, 'seen', None)
        if seen is None:
            seen = self._turn_notice_local.seen = set()
        if key not in seen:
            seen.add(key)
            self.messages.put(text)

    def refresh_visibility(self, memory_id):
        """Check current Ombre lifecycle metadata, never the cached body/details."""
        raw = self.raw_content_for(memory_id)
        allowed = None
        if raw is not None:
            lines = raw.splitlines()
            end = next((i for i, line in enumerate(lines[1:], 1)
                        if line.strip() == '---'), None)
            if lines and lines[0].strip() == '---' and end is not None:
                metadata = {}
                for line in lines[1:end]:
                    key, sep, value = line.partition(':')
                    if sep and not line[:1].isspace() and key.strip() in {'dont_surface', 'tombstone', 'deleted_at', 'type'}:
                        key = key.strip()
                        if key in metadata:
                            break  # Ambiguous metadata cannot authorize cached content.
                        metadata[key] = self._frontmatter_scalar(value).casefold()
                else:
                    booleans = {'true': True, 'yes': True, 'on': True, '1': True,
                                'false': False, 'no': False, 'off': False, '0': False}
                    flags = [metadata.get(key, 'false') for key in ('dont_surface', 'tombstone')]
                    if all(flag in booleans for flag in flags):
                        deleted = metadata.get('deleted_at', '') not in ('', 'null', 'none', 'false')
                        allowed = not (any(booleans[flag] for flag in flags) or deleted
                                       or metadata.get('type') == 'archived')
        with self.mutex:
            if allowed is True:
                self.native_hidden.discard(memory_id)
            else:
                self.native_hidden.add(memory_id)
                self.bucket_details_cache.pop(memory_id, None)
        if allowed is None:
            self._notice_once('visibility', 'Memory visibility could not be confirmed; affected memories were withheld this turn.')
        return allowed is True

    @staticmethod
    def safe_error_type(exc):
        error_type = type(exc).__name__
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error_type):
            return 'UnknownError'
        return error_type

    @staticmethod
    def safe_error_code(exc):
        code = getattr(exc, 'code', None)
        return code if code in {'connection', 'tool', 'unknown', 'timeout', 'closed'} else None

    @staticmethod
    def safe_error_stage(exc):
        stage = getattr(exc, 'stage', None)
        return stage if stage in {'http_session', 'mcp_session', 'mcp_initialize', 'mcp_tool',
                                  'mcp_verified', 'recovery'} else None

    def tool_failure(self, tool, exc):
        """Keep autonomous and interactive tool failures query- and body-free."""
        error_code = self.safe_error_code(exc)
        try:
            row = {
                'time': self.budget.clock(), 'code': 'memory_tool_failed',
                'tool': tool, 'error_type': self.safe_error_type(exc),
            }
            if error_code:
                row['error_code'] = error_code
            self.store.append(self.diagnostics, row)
        except Exception:
            pass  # A diagnostic write must never hide the original tool failure.
        if self.runtime_monitor:
            try:
                self.runtime_monitor.observe_memory({'code': 'memory_operation_failed',
                                                     'tool': tool, 'error_code': error_code})
            except Exception:
                pass  # Monitoring must not change the tool's original result.

    def retrieval_failure(self, stage, exc):
        """Record only a stable stage and exception class for automatic recall."""
        error_type = self.safe_error_type(exc)
        error_code = self.safe_error_code(exc)
        error_stage = self.safe_error_stage(exc)
        try:
            row = {
                'time': self.budget.clock(), 'code': 'retrieval_failed',
                'stage': stage, 'error_type': error_type,
            }
            if error_code:
                row['error_code'] = error_code
            if error_stage:
                row['mcp_stage'] = error_stage
            self.store.append(self.diagnostics, row)
        except Exception:
            pass  # A diagnostic write must never make automatic recall unsafe.
        self._notice_once(
            'retrieval-failed',
            f'Memory retrieval partly failed at {stage} ({error_type}'
            + (f'/{error_code}' if error_code else '') + '); '
            + (f'Safe failure stage: {error_stage}. ' if error_stage else '')
            + 'unavailable content was not treated as memory. '
            'See memory/_agent/diagnostics.jsonl; it contains no chat content or keys.')

    def context_visible(self, candidates, *, failure_stage=None):
        # Recheck even a previously hidden ID, so a panel unhide can take effect.
        # A failed check withholds only that candidate; it cannot authorize cached text.
        checked = []
        for key, body in candidates:
            try:
                if self.refresh_visibility(key):
                    checked.append((key, body))
            except Exception as exc:
                if failure_stage:
                    self.retrieval_failure(failure_stage, exc)
        return self.visible(checked)

    def visibility_reason(self, memory_id):
        """Why a candidate may not be shown (ticket 391); returns a code, never content."""
        raw = self.raw_content_for(memory_id)
        if raw is None:
            return 'missing'
        lines = raw.splitlines()
        end = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == '---'), None)
        metadata = {}
        if lines and lines[0].strip() == '---' and end is not None:
            for line in lines[1:end]:
                key, sep, value = line.partition(':')
                if sep and not line[:1].isspace() and key.strip() in {
                        'dont_surface', 'tombstone', 'deleted_at', 'type'}:
                    metadata[key.strip()] = self._frontmatter_scalar(value).casefold()
        if metadata.get('type') == 'archived':
            return 'archived'
        if metadata.get('tombstone') in ('true', 'yes', 'on', '1'):
            return 'tombstone'
        if metadata.get('dont_surface') in ('true', 'yes', 'on', '1'):
            return 'dont_surface'
        if metadata.get('deleted_at', '') not in ('', 'null', 'none', 'false'):
            return 'deleted'
        with self.mutex:
            if memory_id in set(self.state['sources'].values()):
                return 'source'
            if memory_id in {key for key, row in self.state['entries'].items() if row.get('hidden')}:
                return 'hidden'
        return 'unavailable'

    def note_budget_dropped(self, keys, reason='final_trim'):
        """Ticket 391: memories removed only because this request was too long.

        Ticket 419: also marks that the final assembled request had to cut an already-budgeted
        memory (``final_trim``), removes it from the injected refs so the next turn cannot carry a
        memory that was never sent, and keeps ``budget_dropped`` in step with ``dropped``."""
        gone = [key for key in keys if isinstance(key, str)]
        debug = getattr(self, 'last_recall_debug', None)
        if isinstance(debug, dict):
            debug['budget_dropped'] = list(dict.fromkeys(
                list(debug.get('budget_dropped') or []) + gone))
            debug['final_trim'] = reason
        dropped = set(gone)
        self.last_context_refs = [key for key in self.last_context_refs if key not in dropped]
        self.last_context_kinds = {key: value for key, value in self.last_context_kinds.items()
                                   if key not in dropped}
        self.last_injected_refs = [key for key in self.last_injected_refs if key not in dropped]

    def save_recall_debug(self, request_id):
        """Append one read-only diagnostics row for this turn; IDs and reasons only, never text."""
        debug = getattr(self, 'last_recall_debug', None)
        if not isinstance(debug, dict):
            return
        row = {'time': self.budget.clock(), 'request_id': request_id, **debug}
        name = f'{self.cfg.id}/recall-diagnostics.jsonl'
        try:
            self.store.append(name, row)
            path = self.store.root / name
            with self.store.mutex:
                lines = path.read_bytes().splitlines(keepends=True)
                if len(lines) > RECALL_DIAGNOSTICS_KEEP + RECALL_DIAGNOSTICS_KEEP // 5:
                    temporary = path.with_suffix('.tmp')
                    temporary.write_bytes(b''.join(lines[-RECALL_DIAGNOSTICS_KEEP:]))
                    os.replace(temporary, path)
        except (OSError, UserError):
            pass

    def is_hidden(self, memory_id):
        return memory_id in self.hidden_ids()

    def record_native_update(self, memory_id, content):
        """Keep the local command index in step with a lossless native update."""
        with self.mutex:
            previous = self.state['entries'].get(memory_id, {})
            entry = {'title': previous.get('title') or self.title_for(memory_id) or memory_id,
                     'time': previous.get('time') or self.budget.clock(),
                     'signature': previous.get('signature'), 'hidden': bool(previous.get('hidden')),
                     'text': content[:600], 'native': True, 'updated': self.budget.clock()}
            for flag in ('deleted', 'delete_pending'):
                if previous.get(flag):
                    entry[flag] = True
            self.state['entries'][memory_id] = entry
            self.persist()
            self.bucket_details_cache[memory_id] = (entry['title'], content)

    def assisted_settings(self, query, recent):
        """Ticket 187: settings when this turn's recall may use the last rounds, else None (today's recall)."""
        raw = (self.cfg.memory or {}).get('recall_context')
        if not isinstance(raw, dict) or raw.get('enabled') is not True or not recent:
            return None
        threshold = raw.get('min_similarity')
        if type(threshold) not in (int, float) or not 0 < threshold <= 1:
            return None  # not calibrated yet: keep today's recall
        short = raw.get('short_chars', 12)
        if type(short) is not int or short < 1 or len(query.strip()) > short:
            return None
        rounds = raw.get('rounds', 2)
        return {'threshold': float(threshold), 'rounds': rounds if type(rounds) is int and 1 <= rounds <= 4 else 2}

    def embedder(self):
        if getattr(self, '_embedder', None) is None:
            from dotenv import dotenv_values
            from recall_relevance import QueryEmbedder
            key = dotenv_values(Path(__file__).resolve().parent / '.env', interpolate=False,
                                encoding='utf-8-sig').get('EMBED_API_KEY')
            if not key or not key.strip():
                raise UserError('missing EMBED_API_KEY')
            self._embedder = QueryEmbedder(self.cfg.memory['base_url'], self.cfg.memory['model'], key.strip())
        return self._embedder

    def assisted_recall(self, query, recent, settings, normal, emotional):
        """Search again with the last rounds, keep only candidates close to that text; may keep none."""
        from recall_relevance import context_text, cosine, load_vectors
        text = context_text(recent, query, settings['rounds'])
        embedder = self.embedder()  # a missing key fails here, before any extra search, reservation or request
        extra = self.context_visible(ordinary_hits(self.call('breath_search', {
            'query': text, 'mode': 'automatic', 'with_ids': True, 'max_results': 3 + SEARCH_SPARE_HITS}),
            limit=3 + SEARCH_SPARE_HITS))[:3]
        candidates = dict(normal + extra + emotional)
        tokens = max(512, len(text.encode('utf-8')))
        rate = Decimal(str(self.cfg.memory['input_cny_per_million']))
        reservation = self.budget.estimate(self.cfg.id, self.cfg.memory['model'], tokens, rate)
        settled_amount = Decimal(tokens) * rate / Decimal(1_000_000)
        usage_tokens = None
        accounting = 'embedding-estimate'
        try:
            query_vector = embedder.embed(text)
            reported_tokens = getattr(embedder, 'usage_tokens', None)
            if type(reported_tokens) is int and reported_tokens > 0:
                reported_amount = Decimal(reported_tokens) * rate / Decimal(1_000_000)
                # The reservation is the hard cap. A zero or above-cap report cannot be settled as
                # the real cost: keep the estimate, which charges the reserved upper bound.
                if reported_amount <= reservation[1]:
                    usage_tokens = reported_tokens
                    settled_amount = reported_amount
                    accounting = 'embedding-usage'
        finally:
            self.budget.settle_estimate(reservation, accounting=accounting,
                                        settled_amount=settled_amount, usage_tokens=usage_tokens)
        vectors = load_vectors(vault_path(self.store.root, self.cfg.id) / 'embeddings.db', list(candidates))
        scored = sorted(((cosine(query_vector, vectors[key]), key) for key in candidates if key in vectors),
                        reverse=True)
        kept = [(key, candidates[key]) for sim, key in scored if sim >= settings['threshold']][:3]
        self.last_assisted = {'candidates': [(key, round(sim, 4)) for sim, key in scored],
                              'kept': [key for key, _ in kept], 'threshold': settings['threshold']}
        try:
            self.store.append(self.diagnostics, {
                'time': self.budget.clock(), 'code': 'assisted_recall', 'candidates': len(candidates),
                'kept': len(kept), 'scores': [round(sim, 3) for sim, _ in scored[:6]],
                'threshold': settings['threshold']})
        except Exception:
            pass
        return kept

    def _recall_meta(self, keys):
        """Front matter of the candidates (importance, date, title, relation links), read locally."""
        from recall_rotation import front_matter
        metas = {}
        for key in dict.fromkeys(keys):
            try:
                metas[key] = front_matter(self.raw_content_for(key))
            except Exception:
                metas[key] = {'links': []}
        return metas

    def _recall_vectors(self, keys):
        """Stored content vectors, read-only; none when Ombre has no vector database yet."""
        from recall_relevance import load_vectors
        try:
            return load_vectors(vault_path(self.store.root, self.cfg.id) / 'embeddings.db', list(keys))
        except Exception:
            return {}

    def release_recall(self, keys):
        """Ticket 379: these memories count as not shown lately (a regenerated turn, or ones that never reached
        the model), so the next recall may bring them back. Nothing is rewritten."""
        with self.mutex:
            changed = False
            for key in keys or ():
                if key in self.rotation.last:
                    self.rotation.forget(key)
                    changed = True
        if changed:
            self._save_rotation()

    def rollback_recall(self):
        """Ticket 379: the last recall's cooldown marks are undone, because that turn delivered no reply.

        Ticket 419: a failed/retried turn keeps the last *successfully injected* context available,
        so an immediate follow-up or a retry can still use it."""
        with self.mutex:
            marks, self.last_recall_marks = self.last_recall_marks, None
            self.last_injected_refs = list(getattr(self, '_previous_injected_refs', []) or [])
            if not marks:
                return
            for key, previous in marks.items():
                if previous is None:
                    self.rotation.forget(key)
                else:
                    self.rotation.last.pop(key, None)
                    self.rotation.last[key] = previous
        self._save_rotation()

    def _save_rotation(self):
        try:
            self.store.replace(self.rotation_path, self.rotation.snapshot())
        except Exception as exc:
            # Losing the cooldown after a restart only means a memory may come back sooner.
            self.retrieval_failure('rotation_state', exc)

    def _event_adjacency(self):
        """Ticket 365: {memory_id: ids it shares an experience with} from Peter's own link_to notes (sidecar), both
        directions. Merged-away memories are left out. A damaged sidecar only means no chains this turn."""
        from memory_sidecar import Sidecar
        try:
            notes = Sidecar(self.store, self.cfg.id, self.budget.clock).all()
        except Exception:
            return {}
        adjacency = {}
        for key, note in notes.items():
            if note.get('merged_into'):
                continue
            for target in note.get('links', []):
                if notes.get(target, {}).get('merged_into'):
                    continue
                adjacency.setdefault(key, set()).add(target)
                adjacency.setdefault(target, set()).add(key)
        return adjacency

    def _ombre_event_sources(self):
        """Ticket 365: {memory_id: ids whose Ombre relation_links point at it with an event type}. Ombre writes a
        link on the newer memory, so reading only a memory's own links would never find the stages after it. The
        vault is re-read only when a file was added, removed or changed."""
        from recall_rotation import EVENT_LINKS, front_matter
        vault = self.store.root / self.cfg.id / 'memory'
        try:
            files = [path for path in vault.rglob('*.md') if not path.is_symlink()]
            signature = (len(files), sum(path.stat().st_mtime_ns for path in files))
        except OSError:
            return {}
        cached = getattr(self, '_event_sources_cache', None)
        if cached and cached[0] == signature:
            return cached[1]
        sources = {}
        for path in files:
            try:
                meta = front_matter(path.read_text(encoding='utf-8'))
            except (OSError, UnicodeError):
                continue
            own = meta.get('id') or path.stem.rpartition('_')[2] or path.stem
            for target, kind in meta['links']:
                if kind in EVENT_LINKS:
                    sources.setdefault(target, set()).add(own)
        self._event_sources_cache = (signature, sources)
        return sources

    def recently_created(self, since, limit=8):
        """Ticket 368: ids of the memories created at or after `since` (a datetime), newest first, from the vault's
        own front matter. The sidecar only began noting Peter's saves recently, so this is what finds the older
        ones. Any failure only means none."""
        from recall_rotation import created_key, front_matter
        vault = self.store.root / self.cfg.id / 'memory'
        floor = created_key(since.isoformat())
        try:
            rows = []
            for path in vault.rglob('*.md'):
                if path.is_symlink():
                    continue
                meta = front_matter(path.read_text(encoding='utf-8'))
                key = created_key(meta.get('created'))
                if key[0] == 0 and key >= floor:      # a creation time that cannot be read never counts as recent
                    rows.append((key, meta.get('id') or path.stem.rpartition('_')[2] or path.stem))
            return [memory_id for _, memory_id in sorted(rows, reverse=True)[:limit]]
        except Exception as exc:
            self.retrieval_failure('recently_created', exc)
            return []

    def experience_stages(self, key):
        """Ticket 368: the other visible memories that are stages of the same experience as `key` (Peter's own
        link_to notes in both directions, Ombre's same_event / continuation links), nearest first. Any failure
        only means no stages are shown."""
        from recall_rotation import EVENT_LINKS, experience_chain, front_matter
        try:
            adjacency, inbound, metas = self._event_adjacency(), self._ombre_event_sources(), {}

            def links_of(memory_id):
                if memory_id not in metas:
                    try:
                        metas[memory_id] = front_matter(self.raw_content_for(memory_id))
                    except Exception:
                        metas[memory_id] = {'links': []}
                return metas[memory_id]['links']

            def neighbours(memory_id):
                return (set(adjacency.get(memory_id, ())) | inbound.get(memory_id, set())
                        | {target for target, kind in links_of(memory_id) if kind in EVENT_LINKS})
            return [member for member in experience_chain(key, neighbours)[1:]
                    if self.refresh_visibility(member) and self.visible([(member, '')])]
        except Exception as exc:
            self.retrieval_failure('experience_stages', exc)
            return []

    def _experience_directories(self, kept, metas, adjacency, size, event_of=None):
        """Ticket 365: {memory_id: one line} for each recalled memory that is one stage of a longer experience: the
        others' dates and titles, so Peter sees the whole thing instead of one slice. Hidden, archived or deleted
        stages are left out; any failure just means no line for that memory."""
        from recall_rotation import EVENT_LINKS, directory_line, experience_chain, front_matter
        cache = {}
        event_of = event_of or {}
        inbound = self._ombre_event_sources()

        def meta_of(key):
            if key not in metas:
                try:
                    metas[key] = front_matter(self.raw_content_for(key))
                except Exception:
                    metas[key] = {'links': []}
            return metas[key]

        def neighbours(key):
            found = set(adjacency.get(key, ())) | inbound.get(key, set())
            found.update(target for target, kind in meta_of(key)['links'] if kind in EVENT_LINKS)
            return found

        lines = {}
        for key, _ in kept:
            try:
                members = experience_chain(key, neighbours)
                entries = []
                for member in members:
                    if member != key:
                        meta = meta_of(member)
                        if 'title' not in meta and 'name' not in meta:
                            continue  # no such memory file
                        if member not in cache:
                            # Ombre's own lifecycle flags, then the local ones (hidden on the memory page,
                            # folded into another): the same two checks automatic recall itself applies.
                            cache[member] = bool(self.refresh_visibility(member)
                                                 and self.visible([(member, '')]))
                        if not cache[member]:
                            continue
                    meta = meta_of(key if member == key else member)
                    entries.append((member, meta.get('created') or '',
                                    meta.get('title') or meta.get('name') or ''))
                line = directory_line(key, entries, size, event_of.get(key, ''))
                if line:
                    lines[key] = line
            except Exception as exc:
                self.retrieval_failure('experience_directory', exc)
        return lines

    def context(self, query, *, include_opening=True, recent=None, rotate=False, wake=False, defer=False):
        """This turn's automatic memories. `wake` selects related context for an autonomous activity.

        Ticket 419: retrieval, dedup and cooldown run here; how many memories are sent and how much
        of each is decided by ``finish_recall`` against the request's whole-round budget. With
        ``defer=True`` the request builder calls ``finish_recall`` later, once it knows how much
        space the required blocks leave. Without it this call finishes at once with no budget,
        which is what tests and the readiness probe want.
        """
        from recall_relevance import cosine
        from recall_rotation import (EVENT_LINKS, asks_about_past, drift_hits, excerpt, experience_chain,
                                     front_matter, is_followup, rotate as rotated, same_event)
        self._turn_notice_local.seen = set()
        self.last_context_refs = []
        self.last_context_kinds = {}
        self.last_assisted = None
        self.last_recall_skipped = {'cooldown': 0, 'same_event': 0, 'stage_cap': 0, 'budget': 0}
        self.last_recall_marks = None
        self.last_memory_budget = None
        self._pending_recall = None
        # Ticket 391: one read-only record of where each candidate went. IDs and reasons only, never text.
        self.last_recall_debug = {'query_chars': len(query or ''), 'query_hash': hashlib.sha256(
            (query or '').encode('utf-8')).hexdigest()[:12], 'ombre': [], 'hidden': [], 'vector': [],
            'judge': {'status': 'skipped', 'candidates': 0, 'picked': []}, 'dropped': [],
            'injected': [], 'opening': [], 'budget_dropped': [], 'failure': '',
            'followup': False, 'reused': [], 'items': [], 'budget': {},
            # Ticket 422: three separate paths plus the base that is always carried, and the reason
            # each natural-association candidate was passed over.
            'relationship_base': {}, 'relevant_recall': [], 'natural_association': [],
            'association': {}}
        recall_cfg = self.recall_settings
        explicit = asks_about_past(query) and not (rotate or wake)
        cooldown = 0 if explicit else recall_cfg['cooldown_turns']
        followup = bool(not (rotate or wake) and is_followup(query, recent))
        self.last_recall_debug['followup'] = followup
        widen = (rotate or wake) and recall_cfg['rotation_candidates'] > 3
        with self.mutex:
            self.rotation.turn += 1
        opening = []
        normal = []
        emotional = []
        drift = []
        pool = []
        judged = False
        # Ticket 422 review: weak association needs a candidate pool that is not only Ombre's random
        # "忽然想起来". Ombre's `breath` (its own importance-ordered list, already fetched for the
        # opening and cached for this conversation) is that pool; the random drift stays available as
        # a lower-priority extra. No extra model call, no whole-library scan by us.
        try:
            breath_blocks = blocks(self.opening)
        except Exception as exc:
            self.retrieval_failure('association_pool', exc)
            breath_blocks = []
        settings = self.assisted_settings(query, recent) if not (rotate or wake) else None
        judge = (self.judge if self.judge is not None and (wake or not rotate and not widen and not settings)
                 and query.strip() else None)
        prefetched = None
        if judge is not None and judge.active():
            prefetched = judge.prefetch(query)   # embedding her sentence overlaps with Ombre's own search below
        try:
            # Ticket 381: Ombre also returns archived memories, and its top three are cut before we look at
            # visibility, so an archived hit used up a slot and was then dropped. Ask for a few spare hits,
            # drop what Peter may not see, and only then keep the visible pool.
            asked = recall_cfg['rotation_candidates'] if widen else 3 + SEARCH_SPARE_HITS
            raw = self.call('breath_search', {
                'query': query, 'mode': 'automatic', 'with_ids': True, 'max_results': asked})
            normal = ordinary_hits(raw, limit=asked)
            self.last_recall_debug['ombre'] = [key for key, _ in normal]
            pool = self.context_visible(normal, failure_stage='recall_visibility')
            visible = {key for key, _ in pool}
            self.last_recall_debug['hidden'] = [{'id': key, 'reason': self.visibility_reason(key)}
                                                for key, _ in normal if key not in visible]
            # Ticket 419: the candidate pool stays finite (search cost), but its size is the
            # configured pool, not a hidden "at most three shown".
            normal = pool
            drift = drift_hits(raw)
        except Exception as exc:
            self.last_recall_debug['failure'] = 'breath_search'
            self.retrieval_failure('breath_search', exc)
        if not wake:
            try:
                emotional = blocks(self.call('feel', {'query': query}))
            except Exception as exc:
                self.last_recall_debug['failure'] = 'feel'
                self.retrieval_failure('feel', exc)
        if prefetched is not None:
            try:
                # "None fits" is an answer: nothing is filled back from Ombre. Only a technical failure
                # (JudgeFailed) keeps today's recall for this turn.
                normal = judge.choose(query, pool, prefetched, wake=wake,
                                      candidate_limit=max(3, recall_cfg['rotation_candidates']) if wake else None)
                judged = True
                self.last_recall_debug['judge'] = dict(getattr(judge, 'last_choice', {}) or {})
                self.last_recall_debug['vector'] = list(self.last_recall_debug['judge'].get('vector') or [])
                # Review fix: "nothing relevant to her sentence" is an answer about related recall only.
                # It no longer vetoes a memory that surfaces on its own; the two paths are deduped below.
            except JudgeFailed:
                self.last_recall_debug['judge'] = {'status': 'failed', 'candidates': 0, 'picked': []}
                if wake:
                    normal, drift = list(pool), []
            except Exception as exc:
                self.last_recall_debug['judge'] = {'status': 'failed', 'candidates': 0, 'picked': []}
                self.retrieval_failure('recall_judge', exc)
                if wake:
                    normal, drift = list(pool), []
        kinds = {key: 'search' for key, _ in normal}
        kinds.update({key: 'feel' for key, _ in emotional if key not in kinds})
        if settings:
            try:
                normal, emotional = self.assisted_recall(query, recent, settings, normal, emotional), []
                kinds.update({key: 'context' for key, _ in normal})
            except Exception as exc:
                # Any failure keeps today's recall for this turn.
                self.retrieval_failure('assisted_recall', exc)
        combined = self.context_visible(
            normal[:1] + emotional[:1] + normal[1:] + emotional[1:],
            failure_stage='recall_visibility')
        try:
            opening_candidates = breath_blocks if include_opening else []
        except Exception as exc:
            self.retrieval_failure('opening', exc)
            opening_candidates = []
        opening_visible = self.context_visible(opening_candidates, failure_stage='opening_visibility')
        metas = self._recall_meta([key for key, _ in combined + opening_visible + breath_blocks + drift])
        if wake:
            candidates = list(dict(combined).items())
        elif widen:
            candidates = rotated(list(dict(combined).items()), metas, self.rotation.last)
        else:
            candidates = list(dict(combined).items())
        # Ticket 419: a follow-up keeps the memories the previous turn judged relevant. She is still
        # on the same subject ("那我当时是什么反应？"), so neither a fresh keyword search nor the
        # cooldown may hide what the last turn already decided mattered.
        carry = []
        if followup and not (rotate or wake):
            seen = {key for key, _ in candidates}
            carry = [(key, self._block_body(key)) for key in (self.last_injected_refs or [])
                     if key not in seen]
            carry = [(key, body) for key, body in self.context_visible(carry, failure_stage='followup_visibility')
                     if body.strip()]
        vectors = (self._recall_vectors([key for key, _ in combined + drift])
                   if recall_cfg['dedup'] else {})
        threshold = recall_cfg['dedup_similarity']

        adjacency = self._event_adjacency() if recall_cfg['dedup'] or recall_cfg['directory'] else {}

        inbound = self._ombre_event_sources() if recall_cfg['dedup'] else {}
        link_cache = {}

        def own_links(node):
            if node not in link_cache:
                links = metas.get(node, {}).get('links')
                if links is None:
                    try:
                        links = front_matter(self.raw_content_for(node))['links']
                    except Exception:
                        links = []
                link_cache[node] = {target for target, kind in links if kind in EVENT_LINKS}
            return link_cache[node]

        def neighbours(node):
            # Ticket 369: Peter's link_to notes (sidecar) and Ombre's continuation links, both directions, so a
            # stage reached only through Ombre's links counts toward the same experience as the directory shows.
            return set(adjacency.get(node, ())) | inbound.get(node, set()) | own_links(node)

        def same_experience(key, other):
            return key == other or other in experience_chain(key, neighbours)

        event_of = getattr(self, '_event_map_cache', None)
        if event_of is None:
            event_of = self._event_map_cache = self._event_map()
        stories = self._event_stories()

        def duplicate(key, kept):
            # Ticket 407: one event is one recalled thing even if several of its stages are still separate memories.
            # Ticket 419: the stage count is no longer capped; a different stage of the same experience is a
            # different memory and the budget decides how many fit. Event-level folding only happens when the
            # event actually has a complete story to replace the stages; without one, every stage stays readable.
            if not recall_cfg['dedup']:
                return None
            if (key in event_of and (stories.get(event_of[key]) or {}).get('story')
                    and any(event_of.get(other) == event_of[key] for other, _ in kept)):
                return 'same_event'
            if any(same_event(key, other, metas, vectors, threshold, cosine) for other, _ in kept):
                return 'same_event'
            return None

        carry_keys = {key for key, _ in carry}

        def cooled(key):
            if not cooldown:
                return False
            if key in carry_keys:
                return False
            # Ticket 419: the judge just said this memory bears directly on what she said, and the
            # previous turn already showed it. Immediately following up on the same thing is the one
            # case the cooldown must not win.
            if judged and self.rotation.last.get(key) == self.rotation.turn - 1:
                return False
            return self.rotation.cooled(key, cooldown)

        kept = []
        sources = {}
        for key, body in carry:
            kept.append((key, body))
            sources[key] = 'followup'
            kinds[key] = 'followup'
        for key, body in candidates:
            if cooled(key):
                self.last_recall_skipped['cooldown'] += 1
                self.last_recall_debug['dropped'].append({'id': key, 'reason': 'cooldown'})
                continue
            reason = duplicate(key, kept)
            if reason:
                self.last_recall_skipped[reason] += 1
                self.last_recall_debug['dropped'].append({'id': key, 'reason': reason})
                continue
            kept.append((key, body))
            sources[key] = 'relevant' if wake else ('rotation' if widen else 'search')
            if wake:
                kinds[key] = 'relevant'
            elif widen:
                kinds[key] = 'rotation'
        if not wake and not widen and len(kept) < 2:
            # Ticket 422: weak natural association. Candidates come from Ombre's own importance-ordered
            # list (its `breath`, already cached for this conversation) plus the random "忽然想起来"
            # hits as a lower-priority extra; which one, or none, comes from importance, time since it
            # last surfaced, the cooldown, the room this moment leaves and what this request already
            # carries. No dice, and no dependence on the judge's relevance verdict.
            from natural_association import choose as choose_association
            shown = {other for other, _ in kept}
            origin = {}
            pool_rows = []
            for key, body in breath_blocks:
                origin.setdefault(key, 'importance')
                pool_rows.append((key, body))
            for key, body in drift:
                origin.setdefault(key, 'drift')
                pool_rows.append((key, body))
            visible_pool = self.context_visible(pool_rows, failure_stage='recall_visibility')
            considered = []
            association_dropped = []
            for key, body in visible_pool:
                if key in shown:
                    association_dropped.append({'id': key, 'reason': 'duplicate'})
                elif cooled(key):
                    association_dropped.append({'id': key, 'reason': 'cooldown'})
                elif duplicate(key, kept):
                    association_dropped.append({'id': key, 'reason': 'same_event'})
                else:
                    considered.append((key, body))
            choice = choose_association(query, considered, metas, self.rotation.last,
                                       self.rotation.turn, shown, recall_cfg, origin=origin)
            association_dropped.extend(choice['dropped'])
            self.last_recall_debug['association'] = {
                'candidates': len(visible_pool),
                'from_importance': len({key for key, _ in breath_blocks}),
                'from_drift': len({key for key, _ in drift}),
                'picked': [choice['key']] if choice['key'] else [],
                'dropped': association_dropped, 'reason': choice['reason'] or ''}
            if choice['key']:
                kept.append((choice['key'],
                             prompts.text('memory.association.label') + '\n' + choice['body']))
                sources[choice['key']] = 'association'
                kinds[choice['key']] = 'association'
        if not self.last_recall_debug.get('association'):
            self.last_recall_debug['association'] = {
                'candidates': 0, 'from_importance': len({key for key, _ in breath_blocks}),
                'from_drift': len({key for key, _ in drift}), 'picked': [], 'dropped': [],
                'reason': 'no_candidates' if not (breath_blocks or drift) else 'not_applicable'}
        if opening_visible:
            pinned = [item for item in opening_visible if '核心准则' in item[1].split('\n', 1)[0]
                      or str(metas.get(item[0], {}).get('pinned', '')).lower() == 'true']
            rest = [item for item in opening_visible if item not in pinned]
            recalled_keys = {other for other, _ in kept}
            opening_shown = []
            for key, body in pinned + rotated(rest, metas, self.rotation.last):
                # A memory also recalled this turn is shown once, as the (fresher) recall.
                if key not in recalled_keys and not duplicate(key, kept + opening_shown):
                    opening_shown.append((key, body))
        else:
            pinned = []
            opening_shown = []
        pinned_keys = {key for key, _ in pinned}
        for key, _ in opening_shown:
            kinds[key] = 'opening'
        directories = (self._experience_directories(kept, metas, adjacency, recall_cfg['directory_members'],
                                                    event_of)
                       if recall_cfg['directory'] else {})
        limit = recall_cfg['excerpt_chars']

        def resolved(key, body):
            event_id = event_of.get(key) or ''
            text = body
            if event_id:
                story = (stories.get(event_id) or {}).get('story') or ''
                # Ticket 419 review: the event's current authoritative story wins whenever it exists.
                # Completeness is not a length comparison: a merged story can be shorter and still
                # contain a later correction the old fragment does not have.
                if story:
                    text = f'[bucket_id:{key}]\n{story}'
            if limit:
                text = excerpt(key, text, metas.get(key, {}), limit, event_id)
            return text, event_id

        def pointer(key, event_id):
            where = 'memory_read with memory_id "%s"' % key
            if event_id:
                where += ' (or memory_timeline with event_id "%s" for the whole experience)' % event_id
            return ('(This story is longer than this request can carry in full. Use %s to read the rest.)' % where)

        blocks_out = []
        for index, (key, body) in enumerate(kept):
            text, event_id = resolved(key, body)
            directory = directories.get(key, '')
            pointer_text = pointer(key, event_id) + ((' ' + directory) if directory else '')
            if directory:
                text = text + '\n' + directory
            source = sources.get(key, 'search')
            blocks_out.append({'id': key, 'group': 'recall', 'text': text,
                               'priority': 900 if source in ('random', 'association') else 10 + index,
                               'source': source, 'event_id': event_id, 'pointer': pointer_text,
                               'kind': kinds.get(key, 'search')})
        for index, (key, body) in enumerate(opening_shown):
            text, event_id = resolved(key, body)
            directory = directories.get(key, '')
            pointer_text = pointer(key, event_id) + ((' ' + directory) if directory else '')
            if directory:
                text = text + '\n' + directory
            blocks_out.append({'id': key, 'group': 'opening', 'text': text,
                               'priority': 0 if key in pinned_keys else 500 + index,
                               'source': 'opening', 'event_id': event_id, 'pointer': pointer_text,
                               'kind': 'opening'})
        self.last_recall_debug['reused'] = []   # ticket 419 review: no prefix-only reuse claims
        self._pending_recall = {'blocks': blocks_out, 'include_opening': include_opening}
        if defer:
            return []
        return self.finish_recall()

    def _block_body(self, key):
        """The same `[bucket_id:key]` block shape Ombre returns, from the current file."""
        from recall_rotation import front_matter
        raw = self.raw_content_for(key)
        if raw is None:
            return ''
        body = front_matter(raw).get('body') or ''
        return f'[bucket_id:{key}]\n{body.strip()}'

    def finish_recall(self, *, space=None, history_tokens=0, protected_tokens=0, auxiliary_tokens=0,
                      calibration=0.4):
        """Apply the whole-round budget and build this turn's memory messages.

        ``space`` is the tokens left after the required request blocks; it is split between the
        conversation, the memories and optional auxiliary context. When ``space`` is None the call
        is unbudgeted (tests, readiness probe). The recorded ``memory_used`` is measured on the
        messages that are actually returned, including the recall/opening headers, empty-group
        text, pointers and JSON escaping — not on the bare memory bodies. Returns the messages and
        records the budget and every drop, so the request builder can use ``history_allowance`` /
        ``auxiliary_allowance`` and the panel can show why a candidate was not injected.
        """
        import recall_budget
        pending = self._pending_recall or {'blocks': [], 'include_opening': True}
        blocks_out = pending.get('blocks') or []
        include_opening = pending.get('include_opening', True)
        debug = self.last_recall_debug
        recall_head = prompts.text('memory.recall.head')
        opening_head = prompts.text('memory.opening.head')
        recall_empty = prompts.text('memory.recall.empty')
        opening_empty = prompts.text('memory.opening.empty')

        emit_empty = True

        def messages_for(recall, opening):
            if not emit_empty and not recall and not opening:
                return []
            recalled = ([{'role': 'system', 'content':
                         recall_head + '\n' + ('\n'.join(recall) or recall_empty)}]
                        if recall or emit_empty else [])
            if not include_opening:
                return recalled
            opening_messages = ([{'role': 'system', 'content':
                                 opening_head + '\n' + ('\n'.join(opening) or opening_empty)}]
                                if opening or emit_empty else [])
            return [*opening_messages, *recalled]

        overhead = recall_budget.tokens_for_messages(messages_for([], []), calibration)
        if space is not None and space < protected_tokens + overhead:
            # An empty memory notice must not displace the latest conversation.
            emit_empty = False
            overhead = 0
        if space is None:
            selected = [dict(block, tokens=recall_budget.tokens_for(block.get('text') or '', calibration),
                             complete=True) for block in blocks_out]
            dropped = []
            plan = {'reason': 'unbudgeted'}
            memory_allowance = None
            history_allowance = None
            auxiliary_allowance = None
        else:
            full_messages = messages_for(
                [item['text'] for item in blocks_out if item.get('group') == 'recall'],
                [item['text'] for item in blocks_out if item.get('group') == 'opening'])
            full_need = recall_budget.tokens_for_messages(full_messages, calibration)
            # Reserve empty notices only when they fit alongside the protected exchange.
            # Nonempty messages are measured separately, since they can be cheaper than a notice.
            memory_need = max(0, full_need - overhead)
            plan = recall_budget.split(max(0, space - overhead), history_tokens, memory_need,
                                       protected_tokens, auxiliary_tokens)
            plan['space'] = space
            plan['memory_need'] = memory_need + overhead
            plan['memory'] += overhead
            plan['fixed_overhead'] = overhead
            memory_allowance = plan['memory']
            fit_allowance = max(0, memory_allowance - overhead)
            if full_need <= memory_allowance:
                selected = [dict(block, tokens=recall_budget.tokens_for(block['text'], calibration),
                                 complete=True) for block in blocks_out]
                dropped = []
            else:
                selected, dropped = recall_budget.fit(blocks_out, fit_allowance, calibration)
            # Measure the real messages and refit until the measured size fits the memory share.
            for _ in range(3):
                recall_items = [item for item in selected if item.get('group') == 'recall']
                opening_items = [item for item in selected if item.get('group') == 'opening']
                messages = messages_for([item['text'] for item in recall_items],
                                        [item['text'] for item in opening_items])
                actual = recall_budget.tokens_for_messages(messages, calibration)
                if actual <= memory_allowance:
                    break
                fit_allowance = max(0, fit_allowance - (actual - memory_allowance) - 1)
                selected, dropped = recall_budget.fit(blocks_out, fit_allowance, calibration)
            # Last resort: every optional block obeys the same final budget.
            while actual > memory_allowance and selected:
                dropped_item = max(selected, key=lambda item: item.get('priority', 0))
                selected = [item for item in selected if item is not dropped_item]
                dropped = list(dropped) + [{'id': dropped_item['id'], 'reason': 'budget',
                                            'tokens': dropped_item.get('tokens')}]
                messages = messages_for(
                    [item['text'] for item in selected if item.get('group') == 'recall'],
                    [item['text'] for item in selected if item.get('group') == 'opening'])
                actual = recall_budget.tokens_for_messages(messages, calibration)
            auxiliary_allowance = min(max(0, int(auxiliary_tokens)), plan['auxiliary'])
            history_allowance = max(0, int(space) - actual - auxiliary_allowance)
        recall_items = [item for item in selected if item.get('group') == 'recall']
        opening_items = [item for item in selected if item.get('group') == 'opening']
        for item in selected:
            if item.get('event_id'):
                item['story'] = True
        messages = messages_for([item['text'] for item in recall_items],
                                [item['text'] for item in opening_items])
        memory_used = recall_budget.tokens_for_messages(messages, calibration)
        refs = ([item['id'] for item in opening_items] + [item['id'] for item in recall_items])
        self._previous_injected_refs = list(self.last_injected_refs)
        self.last_context_refs = list(dict.fromkeys(refs))
        self.last_context_kinds = {item['id']: item.get('kind', 'search')
                                   for item in opening_items + recall_items}
        self.last_injected_refs = [item['id'] for item in recall_items]
        self.mark_surfaced(self.last_context_refs)
        with self.mutex:
            # Ticket 379: remember what each mark replaced, so a turn that delivers nothing can take it back.
            self.last_recall_marks = {key: self.rotation.last.get(key) for key in self.last_context_refs}
            self.rotation.mark(self.last_context_refs)
        self._save_rotation()
        if dropped:
            self.last_recall_skipped['budget'] = len(dropped)
        debug['injected'] = [item['id'] for item in recall_items]
        debug['opening'] = [item['id'] for item in opening_items]
        debug['dropped'] = list(debug.get('dropped') or []) + dropped
        debug['budget_dropped'] = [item['id'] for item in dropped if item.get('reason') == 'budget']
        debug['items'] = [{'id': item['id'], 'source': item.get('source'),
                           'tokens': item.get('tokens'), 'complete': item.get('complete'),
                           'paragraphs': item.get('paragraphs'), 'not_shown': bool(item.get('not_shown')),
                           'read_id': item['id'], 'event': item.get('event_id') or '',
                           'story': bool(item.get('story'))}
                          for item in opening_items + recall_items]
        # Ticket 422: the four things a recall summary has to keep apart, so one "recall = 0" can never
        # again read as "he has no relationship background this turn".
        debug['relevant_recall'] = [item['id'] for item in recall_items
                                    if item.get('kind') in ('search', 'feel', 'context', 'relevant',
                                                            'rotation')]
        debug['natural_association'] = [item['id'] for item in recall_items
                                        if item.get('kind') == 'association']
        carried = [item['id'] for item in recall_items if item.get('source') == 'followup']
        if carried:
            debug['reused'] = list(dict.fromkeys([*(debug.get('reused') or []), *carried]))
        debug['budget'] = {**plan, 'memory_allowance': memory_allowance,
                           'memory_used': memory_used, 'history_allowance': history_allowance,
                           'auxiliary_allowance': auxiliary_allowance}
        self.last_memory_budget = dict(debug['budget'])
        return messages

    def reuse_recall(self, history, messages, calibration):
        """Omit exact current blocks only after their history copies survived request trimming."""
        import recall_budget
        contents = [row.get('content') for row in history if isinstance(row.get('content'), str)]
        complete = {item['id'] for item in self.last_recall_debug.get('items', [])
                    if item.get('complete')}
        reused = []
        result = [dict(message) for message in messages]
        for block in (self._pending_recall or {}).get('blocks', []):
            key, text = block['id'], block.get('text') or ''
            if (key not in complete or not text or not any(text.strip() in content for content in contents)):
                continue
            for message in result:
                content = message.get('content')
                if isinstance(content, str) and text in content:
                    message['content'] = content.replace(text, '', 1)
                    reused.append(key)
                    break
        if reused:
            debug = self.last_recall_debug
            debug['reused'] = reused
            debug['injected'] = [key for key in debug.get('injected', []) if key not in reused]
            debug['opening'] = [key for key in debug.get('opening', []) if key not in reused]
            for item in debug.get('items', []):
                if item['id'] in reused:
                    item.update({'source': 'history', 'tokens': 0, 'reused': True})
            self.last_memory_budget['memory_used'] = recall_budget.tokens_for_messages(result, calibration)
            debug['budget'] = dict(self.last_memory_budget)
            # Keep the effective-context references for follow-ups and rollback: these full stories
            # are still supplied to the model, in the retained history instead of a second copy.
        return result

    SURFACED_LIMIT = 40

    def mark_surfaced(self, memory_ids):
        """Remember memories Peter was shown, so hold(target_id) may extend them later."""
        with self.mutex:
            for key in memory_ids:
                if isinstance(key, str) and key:
                    self.surfaced_ids.pop(key, None)
                    self.surfaced_ids[key] = True
            while len(self.surfaced_ids) > self.SURFACED_LIMIT:
                self.surfaced_ids.pop(next(iter(self.surfaced_ids)))

    def attach_recall_judge(self, cfg, model, settings=None, warm=False):
        """Ticket 382: the running app turns the relevance judge on. Tests and tools that build a Memory never call this."""
        from recall_judge import RecallJudge, load_settings as judge_settings
        if self.judge is not None:
            self.judge.close()
        raw = (self.cfg.memory or {}).get('recall_judge')
        self.judge = RecallJudge(self, cfg, model, settings if settings is not None else judge_settings(raw))
        if warm:
            self.judge.warm()
        return self.judge

    def refresh_opening(self):
        """Fetch a fresh opening for a new conversation, without reusing the prior one on failure."""
        self.opening = ''
        try:
            self.opening = self.call('breath', {})
        except Exception as exc:
            self.retrieval_failure('opening', exc)

    def notices(self):
        result = []
        while not self.messages.empty():
            result.append(self.messages.get_nowait())
        return result

    def record_native(self, args, text):
        """Track a memory Peter saved through hold so /memory and /delete can reach it."""
        memory_id = self.created_id(text)
        kind = re.search(r'(新建|feel|合并|钉选)→', text).group(1)
        with self.mutex:
            previous = self.state['entries'].get(memory_id, {})
            entry = {'title': args['title'][:100], 'time': self.budget.clock(), 'signature': None,
                     'hidden': bool(previous.get('hidden')), 'text': args['content'][:600], 'native': True}
            for flag in ('deleted', 'delete_pending'):
                if previous.get(flag):
                    entry[flag] = True
            self.state['entries'][memory_id] = entry
            self.persist()
            self.bucket_details_cache.pop(memory_id, None)
        return '新建' if kind in ('feel', '钉选') else kind

    @staticmethod
    def created_id(text):
        match = re.search(r'(?:新建|feel|合并|钉选)→([\w-]+)', text)
        if not match:
            raise ValueError('memory save not confirmed')
        return match.group(1)

    def _bucket_files(self, memory_id):
        """Yield (path, in_archive) for role-local Markdown files carrying this exact memory ID."""
        vault = vault_path(self.store.root, self.cfg.id).resolve()
        archive = vault / 'archive'
        for path in vault.rglob('*.md'):
            if path.is_symlink() or not path.resolve().is_relative_to(vault):
                continue
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeError):
                continue
            if not lines or lines[0].strip() != '---':
                continue
            end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), len(lines))
            found = None
            for line in lines[1:end]:
                key, sep, value = line.partition(':')
                if sep and not line[:1].isspace() and key.strip() == 'id':
                    found = self._frontmatter_scalar(value)
            if found == memory_id or (found is None and path.stem.endswith('_' + memory_id)):
                yield path, path.resolve().is_relative_to(archive)

    def _purge_rows(self, name, memory_id, scrub):
        """Rewrite one local ledger without this memory's text (Lan's explicit erase decision)."""
        path = self.store.root / name
        with self.store.mutex:
            if not path.exists():
                return
            rows = self.store.read(name)
            kept = [row for row in (scrub(row) if row.get('memory_id') == memory_id else row
                                    for row in rows) if row is not None]
            if kept == rows:
                return
            temporary = path.with_suffix(path.suffix + '.tmp')
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                for row in kept:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)

    def erase(self, memory_id):
        """Physically remove a deleted memory and VEIL's own copies of its text (ticket 091).

        Only runs after Ombre confirmed the delete: a file still outside archive/
        means the delete did not land, so nothing is erased."""
        files = list(self._bucket_files(memory_id))
        if any(not in_archive for _, in_archive in files):
            return False
        for path, _ in files:
            path.unlink()
        self._purge_rows(f'{self.cfg.id}/memory-versions.jsonl', memory_id, lambda row: None)
        self._purge_rows(f'{self.cfg.id}/delete-requests.jsonl', memory_id,
                         lambda row: {**row, 'title': '', 'reason': ''})
        with self.mutex:
            previous = self.state['entries'].get(memory_id, {})
            self.state['entries'][memory_id] = {
                'time': previous.get('time') or self.budget.clock(), 'title': '',
                'hidden': True, 'deleted': True, 'erased': True}
            self.persist()
            self.bucket_details_cache.pop(memory_id, None)
            self.rotation.forget(memory_id)
        self._save_rotation()
        return True

    def erase_deleted(self):
        """Erase memories deleted before ticket 091, once each."""
        with self.mutex:
            pending = [key for key, row in self.state['entries'].items()
                       if row.get('deleted') and not row.get('delete_pending') and not row.get('erased')]
        return [key for key in pending if self.erase(key)]

    def command(self, text):
        with self.mutex:
            if text == '/memory':
                rows = sorted(((k, v) for k, v in self.state['entries'].items() if not v.get('deleted')),
                              key=lambda row: row[1]['time'], reverse=True)[:20]
                lines = [f'{k} | {v["time"]} | {v["title"]}' for k, v in rows]
                if not rows:
                    lines.append('还没有保存任何记忆。')
                lines.append('删除：/delete 编号，再按提示确认。')
                return '\n'.join(lines)
            command, _, key = text.partition(' ')
            if command in ('/forget', '/restore'):
                return '隐藏功能已取消；不想要的记忆请用 /delete 编号 删除。'
            key = key.strip()
            confirmed = False
            if command == '/delete' and key.endswith(' 确认'):
                key = key[:-3].strip()
                confirmed = key == self.delete_asked
            entry = self.state['entries'].get(key)
            retry = bool(entry and entry.get('deleted') and entry.get('delete_pending') and command == '/delete')
            if entry is None or (entry.get('deleted') and not retry):
                return '没有这个记忆编号；输入 /memory 查看。'
            return self.delete(key, entry, confirmed or retry)

    def delete(self, key, entry, confirmed):
        title = entry['title']
        if not confirmed:
            self.delete_asked = key
            return (f'要删除这条记忆吗：{key} | {title}\n'
                    f'确认请输入：/delete {key} 确认。删除后它不再出现在 /memory 里，也不再被想起，'
                    f'也不能恢复：记忆文件和程序保存的旧版本会从硬盘抹掉。')
        self.delete_asked = None
        entry['hidden'] = entry['deleted'] = entry['delete_pending'] = True
        self.persist()  # Fail closed: it is already out of recall even if Ombre's answer is lost.
        try:
            self.call('trace', {'bucket_id': key, 'delete': True})
        except Exception:
            return (f'这次没能确认 Ombre 的删除；这条已经标记为删除，不会被想起，也不能恢复。'
                    f'可稍后再输入 /delete {key} 重试，让 Ombre 也记上这次删除。')
        entry.pop('delete_pending', None)
        self.persist()
        self.erase(key)
        self.messages.put(f'🗑 {self.cfg.name} 的记忆已删除：{title[:40]}')
        return f'已删除：{title}。不能恢复。{ERASE_NOTE}'



