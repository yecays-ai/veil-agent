"""Local stdio MCP services, isolated from VEIL's built-in tool runtime."""
import asyncio
from concurrent.futures import Future, InvalidStateError, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time


NAME = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
TOOL_PREFIX = 'mcp__'
CALL_TIMEOUT = 20


class MCPConfigError(ValueError):
    pass


class MCPConflict(MCPConfigError):
    pass


def _validate_service(raw):
    if not isinstance(raw, dict) or not NAME.fullmatch(str(raw.get('id', ''))) or '__' in raw.get('id', '') or raw['id'].endswith('_'):
        raise MCPConfigError('服务 ID 只能使用小写字母、数字和下划线，并以字母开头。')
    command = raw.get('command')
    args = raw.get('args', [])
    if not isinstance(command, str) or not command.strip() or len(command) > 500 or '\x00' in command:
        raise MCPConfigError('请输入有效的本地启动命令。')
    if not isinstance(args, list) or len(args) > 30 or any(
            not isinstance(arg, str) or len(arg) > 2000 or '\x00' in arg for arg in args):
        raise MCPConfigError('启动参数必须是字符串列表。')
    cwd = raw.get('cwd') or None
    if cwd is not None and (not isinstance(cwd, str) or len(cwd) > 1000 or not Path(cwd).is_dir()):
        raise MCPConfigError('工作目录不存在。')
    permission = raw.get('permission', 'ask')
    if permission not in ('ask', 'direct'):
        raise MCPConfigError('服务权限无效。')
    if not isinstance(raw.get('enabled', True), bool):
        raise MCPConfigError('服务开关无效。')
    return {'id': raw['id'], 'command': command.strip(), 'args': args, 'cwd': cwd,
            'enabled': raw.get('enabled', True), 'permission': permission}


class _Worker:
    def __init__(self, service):
        self.service = service
        self.ready = Future()
        self.loop = None
        self.main_task = None
        self.jobs = None
        self.active_reply = None
        self.tools = []
        self.tool_limit_hit = False
        self.closing = threading.Event()
        self.started_at = time.monotonic()
        self.thread = threading.Thread(target=self._thread, daemon=True, name='mcp-' + service['id'])
        self.thread.start()

    def _thread(self):
        async def run():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            self.loop = asyncio.get_running_loop()
            self.main_task = asyncio.current_task()
            self.jobs = asyncio.Queue()
            if self.closing.is_set():
                return
            params = StdioServerParameters(command=self.service['command'], args=self.service['args'],
                                            cwd=self.service['cwd'])
            async with stdio_client(params, errlog=subprocess.DEVNULL) as (read, write):
                async with ClientSession(read, write) as session:
                    async with asyncio.timeout(10):
                        await session.initialize()
                        discovered = (await session.list_tools()).tools
                        self.tool_limit_hit = len(discovered) > 40
                        self.tools = discovered[:40]
                    self.ready.set_result(True)
                    if self.closing.is_set():
                        return
                    while True:
                        try:
                            job = await asyncio.wait_for(self.jobs.get(), timeout=2)
                        except TimeoutError:
                            async with asyncio.timeout(2):
                                await session.send_ping()
                            continue
                        if job is None:
                            break
                        name, args, reply = job
                        if reply.done():
                            continue
                        self.active_reply = reply
                        try:
                            async with asyncio.timeout(CALL_TIMEOUT):
                                response = await session.call_tool(name, args)
                            if response.isError:
                                detail = '\n'.join(str(getattr(block, 'text', '')) for block in response.content)
                                raise RuntimeError(('MCP 工具返回错误：' + detail)[:300])
                            if not reply.done():
                                value = '\n'.join(str(getattr(block, 'text', '')) for block in response.content)
                                if not value and response.structuredContent is not None:
                                    value = json.dumps(response.structuredContent, ensure_ascii=False)
                                reply.set_result(value[:16000])
                        except TimeoutError:
                            if not reply.done():
                                reply.set_exception(RuntimeError('MCP 工具调用超时，执行结果未知。'))
                            self.closing.set()
                            return
                        except Exception as exc:
                            if not reply.done():
                                reply.set_exception(exc)
                        finally:
                            self.active_reply = None
        try:
            asyncio.run(run())
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(RuntimeError(str(exc)[:300]))
            if self.jobs is not None:
                while not self.jobs.empty():
                    job = self.jobs.get_nowait()
                    if job is not None and not job[2].done():
                        job[2].set_exception(RuntimeError('MCP 服务已断开。'))

    def call(self, name, args, should_stop=None):
        if not self.thread.is_alive() or self.loop is None or self.closing.is_set():
            raise RuntimeError('MCP 服务已断开。')
        reply = Future()
        self.loop.call_soon_threadsafe(self.jobs.put_nowait, (name, args, reply))
        deadline = time.monotonic() + CALL_TIMEOUT + 3
        while True:
            if reply.done():
                return reply.result()
            if should_stop and should_stop():
                reply.cancel()
                self.close()
                raise RuntimeError('MCP 调用已停止，执行结果未知。')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reply.cancel()
                self.close()
                raise RuntimeError('MCP 工具调用超时，执行结果未知。')
            try:
                return reply.result(timeout=min(0.2, remaining))
            except FutureTimeout:
                pass

    def stop(self):
        self.closing.set()
        if self.active_reply is not None and not self.active_reply.done():
            try:
                self.active_reply.set_exception(RuntimeError('MCP 服务已停用。'))
            except InvalidStateError:
                pass
        if self.thread.is_alive() and self.loop and self.jobs:
            try:
                if self.main_task:
                    self.loop.call_soon_threadsafe(self.main_task.cancel)
                else:
                    self.loop.call_soon_threadsafe(self.jobs.put_nowait, None)
            except RuntimeError:
                pass

    def close(self):
        self.stop()
        if self.thread.is_alive():
            self.thread.join(timeout=3)


class MCPServices:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.workers = {}
        self.retiring = {}
        self.errors = {}
        self.failed = set()
        self.config = {'enabled': False, 'services': []}
        self.revision = 0
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            self.config = self._validate(raw)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            self.errors['_config'] = str(exc)[:300]

    @staticmethod
    def _validate(raw):
        if not isinstance(raw, dict) or not isinstance(raw.get('enabled'), bool):
            raise MCPConfigError('MCP 总开关无效。')
        services = raw.get('services')
        if not isinstance(services, list) or len(services) > 20:
            raise MCPConfigError('最多配置 20 个 MCP 服务。')
        result = [_validate_service(item) for item in services]
        if len({item['id'] for item in result}) != len(result):
            raise MCPConfigError('服务 ID 不能重复。')
        return {'enabled': raw['enabled'], 'services': result}

    def save(self, raw, expected_revision=None):
        config = self._validate(raw)
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision:
                raise MCPConflict('MCP 配置已在另一个窗口改变，请重试。')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self.path)
            old_config = self.config
            old = self.workers
            old_services = {item['id']: item for item in old_config['services']}
            self.workers = {item['id']: old[item['id']] for item in config['services']
                            if config['enabled'] and item['enabled'] and old_config['enabled']
                            and old_services.get(item['id']) == item and item['id'] in old}
            to_close = {key: worker for key, worker in old.items() if key not in self.workers}
            self.retiring.update(to_close)
            self.config = config
            self.revision += 1
            self.errors = {key: value for key, value in self.errors.items() if key in self.workers}
            self.failed.intersection_update(self.workers)
        for key, worker in to_close.items():
            worker.close()
            with self.lock:
                if not worker.thread.is_alive() and self.retiring.get(key) is worker:
                    self.retiring.pop(key, None)
        return self.view()

    def _worker(self, service, retry=False):
        with self.lock:
            if next((item for item in self.config['services'] if item['id'] == service['id']), None) != service:
                return None
            if not self.config['enabled'] or not service['enabled']:
                return None
            retiring = self.retiring.get(service['id'])
            if retiring:
                if retiring.thread.is_alive():
                    self.errors[service['id']] = '旧 MCP 服务正在关闭，请稍后刷新。'
                    return None
                self.retiring.pop(service['id'], None)
                self.errors.pop(service['id'], None)
            if service['id'] in self.failed and not retry:
                return None
            worker = self.workers.get(service['id'])
            if worker:
                if worker.ready.done():
                    try:
                        worker.ready.result()
                    except Exception as exc:
                        self.errors[service['id']] = (str(exc) or type(exc).__name__)[:300]
                        self.failed.add(service['id'])
                elif time.monotonic() - worker.started_at > 12:
                    self.errors[service['id']] = 'MCP 服务连接超时。'
                    self.failed.add(service['id'])
                    worker.stop()
                if service['id'] in self.failed and not retry:
                    return None
                if worker.thread.is_alive() and service['id'] not in self.failed:
                    return worker
                if not worker.thread.is_alive() and service['id'] not in self.failed:
                    self.errors[service['id']] = 'MCP 服务已断开。'
                    self.failed.add(service['id'])
                    if not retry:
                        return None
            if worker:
                worker.stop()
                if worker.thread.is_alive():
                    return None
                self.workers.pop(service['id'], None)
            worker = _Worker(service)
            self.workers[service['id']] = worker
            self.errors.pop(service['id'], None)
            self.failed.discard(service['id'])
            return worker

    def schemas(self, retry=False):
        result = []
        for service in list(self.config['services']):
            worker = self._worker(service, retry=retry)
            if not worker or not worker.ready.done():
                continue
            for tool in worker.tools:
                name = self._usable_name(service, tool)
                if name is None:
                    self.errors[service['id']] = '部分 MCP 工具名称或参数说明不兼容，Peter 无法调用这些工具。'
                    continue
                params = tool.inputSchema if isinstance(tool.inputSchema, dict) else {'type': 'object', 'properties': {}}
                result.append({'type': 'function', 'function': {'name': name,
                    'description': f"MCP 服务 {service['id']}（{service['permission']}）: {str(tool.description or tool.name)[:500]}",
                    'parameters': {key: value for key, value in params.items() if key != '$schema'}}})
        return result

    @staticmethod
    def _usable_name(service, tool):
        name = f"{TOOL_PREFIX}{service['id']}__{tool.name}"
        if len(name) > 64 or not re.fullmatch(r'[a-zA-Z0-9_]+', name):
            return None
        try:
            if len(json.dumps(tool.inputSchema, ensure_ascii=False)) > 12000:
                return None
        except (TypeError, ValueError):
            return None
        return name

    def permission_for(self, name):
        match = re.fullmatch(r'mcp__([a-z][a-z0-9_]{0,31}?)__(.+)', name)
        if not match:
            return 'deny'
        with self.lock:
            service = next((item for item in self.config['services'] if item['id'] == match.group(1)), None)
            return 'allow' if service and service['permission'] == 'direct' else 'ask'

    def view(self):
        with self.lock:
            rows = []
            for service in self.config['services']:
                worker = self.workers.get(service['id'])
                retiring = self.retiring.get(service['id'])
                if retiring and retiring.thread.is_alive():
                    self.errors[service['id']] = '旧 MCP 服务正在关闭，请稍后刷新。'
                if worker and not worker.ready.done() and time.monotonic() - worker.started_at > 12:
                    self.errors[service['id']] = 'MCP 服务连接超时。'
                    self.failed.add(service['id'])
                    worker.stop()
                if worker and worker.ready.done():
                    try:
                        worker.ready.result()
                    except Exception as exc:
                        self.errors[service['id']] = (str(exc) or type(exc).__name__)[:300]
                        self.failed.add(service['id'])
                if worker and not worker.thread.is_alive() and service['id'] not in self.failed:
                    self.errors[service['id']] = 'MCP 服务已断开。'
                    self.failed.add(service['id'])
                connected = bool(worker and worker.ready.done() and worker.thread.is_alive()
                                 and service['id'] not in self.failed and self.config['enabled'] and service['enabled'])
                connecting = bool(worker and not worker.ready.done() and service['id'] not in self.failed
                                  and self.config['enabled'] and service['enabled'])
                rows.append({**service, 'status': 'connected' if connected else 'connecting' if connecting else 'error' if service['id'] in self.failed or (retiring and retiring.thread.is_alive()) else 'off',
                             'tools': [tool.name for tool in worker.tools if self._usable_name(service, tool)] if connected else [],
                             'error': self.errors.get(service['id']) or (
                                 '该服务工具超过 40 个，仅提供前 40 个。' if connected and worker.tool_limit_hit else None)})
            return {'enabled': self.config['enabled'], 'services': rows,
                    'revision': self.revision, 'error': self.errors.get('_config')}

    def invoke(self, name, args, approval, should_stop=None):
        match = re.fullmatch(r'mcp__([a-z][a-z0-9_]{0,31}?)__(.+)', name)
        if not match:
            return 'MCP 工具名称无效。', '被拒绝', name
        service_id, tool_name = match.groups()
        with self.lock:
            service = next((item for item in self.config['services'] if item['id'] == service_id), None)
            if not self.config['enabled'] or not service or not service['enabled']:
                return 'MCP 服务已停用。', '被拒绝', name
            worker = self.workers.get(service_id)
            if not worker or not worker.thread.is_alive() or not any(
                    tool.name == tool_name and self._usable_name(service, tool) == name
                    for tool in worker.tools):
                return 'MCP 工具不可用。', '被拒绝', name
            permission = service['permission']
        if permission == 'ask':
            from agent_types import NOT_ASKED
            answer = approval(f'Peter 想调用 MCP 服务 {service_id} 的 {tool_name}。同意吗？y/N ')
            if answer == NOT_ASKED or str(answer).strip().casefold() != 'y':
                return 'Lan 没有批准这次 MCP 调用。', '被拒绝', name
        with self.lock:
            current = next((item for item in self.config['services'] if item['id'] == service_id), None)
            if not self.config['enabled'] or not current or not current['enabled'] or self.workers.get(service_id) is not worker:
                return 'MCP 服务已停用。', '被拒绝', name
        try:
            result = worker.call(tool_name, args, should_stop=should_stop)
            return result, '成功', name
        except Exception as exc:
            with self.lock:
                self.errors[service_id] = str(exc)[:300]
                if worker.closing.is_set() or not worker.thread.is_alive():
                    self.failed.add(service_id)
            return 'MCP 服务调用失败：' + str(exc)[:300], '出错', name

    def close(self):
        with self.lock:
            old = self.workers
            self.workers = {}
            retiring = self.retiring
            self.retiring = {}
        for worker in {**old, **retiring}.values():
            worker.close()
