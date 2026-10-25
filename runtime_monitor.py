"""Small, local runtime safety summary built from existing VEIL events."""
from datetime import datetime, timezone
import json
import os
import threading


class RuntimeMonitor:
    """Persist one privacy-safe status row; never probes services or calls a model."""

    _VALID_STATES = {'healthy', 'recovered', 'attention', 'paused'}
    _VALID_CLASSIFICATIONS = {'healthy', 'safe_recovery', 'safe_to_retry', 'unknown'}
    _MEMORY_MUTATIONS = {'hold', 'trace', 'plan', 'letter_write', 'anchor', 'release'}

    def __init__(self, store, character_id, activity):
        self.store = store
        self.activity = activity
        self.path = store.root / character_id / 'runtime-status.json'
        self.lock = threading.RLock()
        self.current = self._load() or self._row(
            'runtime', 'healthy', 'healthy', 'ready', 'No action is needed.')

    @staticmethod
    def _time():
        return datetime.now(timezone.utc).isoformat()

    def _row(self, module, state, classification, code, action):
        return {'time': self._time(), 'module': module, 'state': state,
                'classification': classification, 'code': code, 'action': action}

    @staticmethod
    def _valid(row):
        required = {'time', 'module', 'state', 'classification', 'code', 'action'}
        return (isinstance(row, dict) and set(row) == required
                and all(isinstance(row[key], str) for key in required)
                and row['state'] in RuntimeMonitor._VALID_STATES
                and row['classification'] in RuntimeMonitor._VALID_CLASSIFICATIONS)

    def _load(self):
        try:
            row = json.loads(self.path.read_text(encoding='utf-8'))
            return row if self._valid(row) else None
        except (FileNotFoundError, OSError, ValueError, UnicodeError):
            return None

    def _save(self, row):
        with self.lock:
            self.current = row
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except (OSError, UnboundLocalError):
                    pass

    def _pause(self):
        try:
            self.activity.set(False)
            return not self.activity.enabled()
        except Exception:
            return False

    def _save_unknown_memory(self, code, action):
        paused = self._pause()
        self._save(self._row('memory', 'paused' if paused else 'attention', 'unknown',
                             code if paused else 'memory_pause_failed',
                             action if paused else
                             'The uncertain write was not replayed, but automatic pause could not be confirmed; disable autonomy and verify memory state before continuing.'))

    def observe_memory(self, facts):
        """Accept only classified MCP metadata; ignore payload fields by construction."""
        if not isinstance(facts, dict):
            return
        code, stage, tool = facts.get('code'), facts.get('stage'), facts.get('tool')
        if (self.current.get('module') == 'memory' and self.current.get('classification') == 'unknown'
                and self.current.get('code') in {'memory_result_unknown', 'memory_pause_failed'}
                and code != 'memory_operation_verified'):
            return  # Read/recovery events cannot resolve a different uncertain write.
        if code == 'retry_succeeded' and stage == 'mcp_verified':
            self._save(self._row('memory', 'recovered', 'safe_recovery',
                                 'memory_recovered', 'No action is needed.'))
        elif code == 'recovery_succeeded' and stage == 'mcp_verified':
            if self.current.get('module') != 'memory' or self.current.get('state') == 'healthy':
                self._save(self._row('memory', 'attention', 'safe_to_retry',
                                     'memory_service_recovered',
                                     'The service recovered; wait for the original operation result.'))
        elif code == 'recovery_failed':
            mutation = tool in self._MEMORY_MUTATIONS
            self._save(self._row('memory', 'attention',
                                 'unknown' if mutation else 'safe_to_retry',
                                 'memory_recovery_failed' if mutation else 'memory_unavailable',
                                 'The write outcome will be classified after the original call returns; do not repeat it.'
                                 if mutation else
                                 'Memory recovery failed; retry the read only from the original user action.'))
        elif code == 'mcp_call':
            self._save(self._row('memory', 'attention', 'safe_to_retry',
                                 'memory_unavailable',
                                 'The request is still being recovered; wait for its final result before retrying.'))
        elif code == 'memory_operation_failed':
            mutation = tool in self._MEMORY_MUTATIONS
            uncertain = facts.get('error_code') in {'unknown', 'timeout'}
            if mutation and uncertain:
                self._save_unknown_memory(
                    'memory_result_unknown',
                    'Verify memory state before retrying; do not repeat the operation.')
            elif mutation:
                self._save(self._row('memory', 'attention', 'safe_to_retry',
                                     'memory_operation_not_confirmed',
                                     'The write was not confirmed; retry only after checking the original action.'))
            elif not mutation:
                self._save(self._row('memory', 'attention', 'safe_to_retry',
                                     'memory_unavailable',
                                     'The read was not confirmed; retry only from the original user action.'))

    def observe_tool(self, name, state, autonomy=False, checkpoint_verified=False, **_ignored):
        if not isinstance(name, str):
            return
        if (state == '成功' and name == 'long_task_update' and checkpoint_verified is True
                and self.current.get('module') == 'long_task'):
            self._save(self._row('long_task', 'recovered', 'safe_recovery',
                                 'checkpoint_verified', 'No action is needed.'))
            return
        if (state == '成功' and self.current.get('module') == 'file_tools'
                and self.current.get('code') == 'tool_unavailable'):
            # A plain tool failure leaves nothing uncertain; the next success shows the tools work again.
            self._save(self._row('file_tools', 'recovered', 'safe_recovery',
                                 'tool_recovered', 'No action is needed.'))
            return
        if state != '出错':
            return
        from long_tasks import SIDE_EFFECT_TOOLS
        side_effect = name in SIDE_EFFECT_TOOLS
        if side_effect and autonomy:
            paused = self._pause()
        else:
            paused = True
        self._save(self._row(
            'long_task' if side_effect and autonomy else 'file_tools',
            'paused' if side_effect and autonomy and paused else 'attention',
            'unknown' if side_effect else 'safe_to_retry',
            ('side_effect_result_unknown' if paused else 'side_effect_pause_failed') if side_effect else 'tool_unavailable',
            ('Inspect the saved checkpoint and target before continuing; do not repeat the operation.'
             if side_effect and paused else
             'The outcome is unknown; automatic pause could not be confirmed. Disable autonomy manually, then inspect the saved checkpoint and target.'
             if side_effect else
             'Retry only when the user asks or the service becomes available.')))

    def snapshot(self, long_tasks=None, tools=None):
        with self.lock:
            row = dict(self.current)
        task_state = 'not_configured'
        if long_tasks is not None:
            try:
                task = long_tasks.read()
                task_state = ('verification_required' if task and task.get('pending_action')
                              else task.get('status', 'idle') if task else 'idle')
            except Exception:
                task_state = 'unavailable'
        files = getattr(tools, 'files', None) if tools else None
        files = files or tools
        if files is None:
            file_state = 'disabled'
        elif getattr(files, 'startup_notice', None):
            file_state = 'unavailable'
        elif getattr(files, 'enabled', True) is False:
            file_state = 'disabled'
        else:
            file_state = 'available'
        labels = {
            'healthy': '运行正常。',
            'recovered': '故障已安全恢复，没有重放结果不确定的操作。',
            'attention': '有操作未确认；请按提示处理，不会自动重复执行。',
            'paused': '已暂停高风险后台操作；请先核对状态，不要重试。',
        }
        return {**row, 'message': labels[row['state']], 'components': {
            'memory': ('recovered' if row['module'] == 'memory' and row['state'] == 'recovered'
                       else 'attention' if row['module'] == 'memory' and row['state'] != 'healthy'
                       else 'available'),
            'file_tools': file_state,
            'autonomy': 'enabled' if self.activity.enabled() else 'paused',
            'long_task': task_state,
        }}
