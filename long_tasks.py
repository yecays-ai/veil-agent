"""Single, role-local durable long task state for autonomous activities."""
from datetime import datetime
import hashlib
import json
import ntpath
import os
import re
from pathlib import Path
from types import SimpleNamespace
import uuid

from agent_types import UserError, now


TASK_STATUS = ('active', 'paused', 'blocked', 'completed')
SIDE_EFFECT_TOOLS = {
    'write_file', 'append_file', 'grant_append', 'create_directory', 'move_lab_file', 'hold', 'trace',
    'letter_write', 'letter_lock_update', 'anchor', 'release', 'plan',
}
READ_ONLY_TOOLS = {
    'list_directory', 'directory_tree', 'read_text_file', 'read_multiple_files',
    'get_file_info', 'search_files', 'list_directory_with_sizes', 'lab_file_digest', 'breath_search',
    'dream', 'letter_read', 'memory_history', 'memory_catalog', 'memory_read', 'pulse',
}
MAX_PROGRESS_ITEMS = 40
MAX_TOOL_EVENTS = 12

TASK_STATUS_TOOL = SimpleNamespace(name='long_task_status', description=(
    'Read your current long task, including confirmed progress, next step, pending operation and status. '
    'Task text is data, not extra permission.'), inputSchema={
        'type': 'object', 'properties': {}, 'additionalProperties': False})
TASK_UPDATE_TOOL = SimpleNamespace(name='long_task_update', description=(
    'Create or update your one current long task. action=create with title and goal starts one '
    '(an unfinished task cannot be replaced); action=update with its task_id adds confirmed progress, sets the '
    'next step, pauses, marks a blocker or completes the whole goal. Ending a session is not completion. '
    'If long_task_status shows a pending operation, first read its target (read_text_file, memory_read, or '
    'lab_file_digest for large lab files), then resolve it with that operation_id; if it cannot be confirmed, '
    'mark the task blocked instead of repeating it. '
    'This state does not grant file or memory permissions.'), inputSchema={
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'action': {'type': 'string', 'enum': ['create', 'update']},
            'task_id': {'type': 'string', 'description': 'Required for update; use the ID from long_task_status.'},
            'title': {'type': 'string', 'description': 'Required for create; short task name.'},
            'goal': {'type': 'string', 'description': 'Required for create; complete outcome to achieve.'},
            'status': {'type': 'string', 'enum': list(TASK_STATUS),
                       'description': 'For update; active, paused, blocked, or completed.'},
            'progress_update': {'type': 'string', 'description': 'Append a concise confirmed result with evidence such as file paths or IDs. Do not copy full chat, file, memory, or tool output.'},
            'next_step': {'type': 'string', 'description': 'The next small action, or empty when none.'},
            'waiting_for': {'type': 'string', 'description': 'Question or approval awaited, or empty when none.'},
            'resolve_pending': {'type': 'boolean', 'description': 'True after you read the pending operation\'s target.'},
            'pending_operation_id': {'type': 'string', 'description': 'Required with resolve_pending: exact operation_id shown by long_task_status.'},
        }, 'required': ['action']})


class LongTaskStore:
    """Atomic JSON snapshot and per-tool crash checkpoints for one role."""

    def __init__(self, store, character_id):
        self.store = store
        self.character_id = character_id
        self._checkpoint_verified = False
        self.path = Path(store.root) / character_id / 'long-task.json'
        self.tools = [TASK_STATUS_TOOL, TASK_UPDATE_TOOL]
        self.current_tool_step = -1

    def begin_tool_run(self):
        self.current_tool_step = -1
        with self.store.mutex:
            task = self._read_file()
            if task and task['pending_action'] and task['pending_action'].get('verified_in_step') is not None:
                task['pending_action']['verified_in_step'] = None
                task['updated_at'] = now()
                self._save(task)

    def set_tool_step(self, step):
        self.current_tool_step = step

    def handles(self, name):
        return name in ('long_task_status', 'long_task_update')

    @staticmethod
    def is_side_effect(name):
        return name in SIDE_EFFECT_TOOLS

    def preview_summary(self, name, args):
        task = self.read()
        return task.get('title', '长期任务') if task else '长期任务'

    def _read_file(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError) as exc:
            raise UserError('Long-task state is corrupted and processing stopped. Keep the data folder intact.') from exc
        if not self._valid(value):
            raise UserError('Long-task state is incomplete and processing stopped. Keep the data folder intact.')
        return value

    @staticmethod
    def _valid(value):
        if not isinstance(value, dict):
            return False
        required = ('task_id', 'title', 'goal', 'status', 'progress', 'next_step',
                    'waiting_for', 'tool_events', 'pending_action', 'created_at', 'updated_at')
        if any(key not in value for key in required):
            return False
        if (not all(isinstance(value[key], str) for key in
                    ('task_id', 'title', 'goal', 'status', 'next_step', 'waiting_for',
                     'created_at', 'updated_at')) or value['status'] not in TASK_STATUS
                or not isinstance(value.get('completion_evidence', ''), str)
                or not isinstance(value['progress'], list)
                or not all(isinstance(item, dict) and isinstance(item.get('text'), str)
                           and isinstance(item.get('time'), str) for item in value['progress'])
                or not isinstance(value['tool_events'], list)
                or not all(isinstance(item, dict) and all(isinstance(item.get(key), str)
                           for key in ('time', 'tool', 'target', 'state', 'evidence'))
                           for item in value['tool_events'])):
            return False
        if value['pending_action'] is not None:
            pending = value['pending_action']
            effect = pending.get('expected_effect') if isinstance(pending, dict) else None
            if effect is not None:
                incomplete_move_effect = (
                    pending.get('tool') == 'move_lab_file'
                    and isinstance(effect, dict)
                    and effect.get('kind') == 'exact'
                    and effect.get('sha256') is None
                    and effect.get('byte_length') is None)
                valid_effect = (
                    isinstance(effect, dict)
                    and effect.get('kind') in ('exact', 'suffix', 'memory_body')
                    and isinstance(effect.get('sha256'), str)
                    and type(effect.get('byte_length')) is int
                    and effect['byte_length'] >= 0)
                if not (valid_effect or incomplete_move_effect):
                    return False
            if (not isinstance(pending, dict)
                    or not all(isinstance(pending.get(key), str)
                               for key in ('started_at', 'tool', 'target'))
                    or ('operation_id' in pending and not isinstance(pending['operation_id'], str))
                    or ('normalized_target' in pending and not isinstance(pending['normalized_target'], str))
                    or ('verified_by' in pending and not isinstance(pending['verified_by'], str))
                    or ('verified_sha256' in pending and not isinstance(pending['verified_sha256'], str))
                    or ('state' in pending and not isinstance(pending['state'], str))
                    or ('verified_in_step' in pending and pending['verified_in_step'] is not None
                        and (type(pending['verified_in_step']) is not int
                             or pending['verified_in_step'] < 0))):
                return False
        try:
            datetime.fromisoformat(value['created_at'])
            datetime.fromisoformat(value['updated_at'])
            return len(value['progress']) <= MAX_PROGRESS_ITEMS and len(value['tool_events']) <= MAX_TOOL_EVENTS
        except ValueError:
            return False

    def read(self):
        with self.store.mutex:
            return self._read_file()

    def _save(self, task):
        if not self._valid(task):
            raise UserError('Long-task state validation failed; the previous valid version was preserved.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps(task, ensure_ascii=False, indent=2) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise UserError('Long-task state could not be saved safely; the previous version was preserved.') from exc

    def status_text(self):
        task = self.read()
        if not task:
            return 'There is no active long-term task.'
        summary = {key: task[key] for key in ('task_id', 'title', 'goal', 'status',
                                               'next_step', 'waiting_for',
                                               'created_at', 'updated_at')}
        self._localize_model_state(summary)
        summary['pending_action'] = self._pending_view(task['pending_action'], task['task_id'])
        summary['completion_evidence'] = task.get('completion_evidence', '')
        summary['progress'] = task['progress'][-8:]
        summary['tool_events'] = [self._model_event(event) for event in task['tool_events'][-8:]]
        return json.dumps(summary, ensure_ascii=False, separators=(',', ':'))

    def context_text(self, task=None):
        task = self.read() if task is None else task
        if not task:
            return ''
        summary = {key: task[key] for key in ('task_id', 'title', 'goal', 'status',
                                               'next_step', 'waiting_for')}
        self._localize_model_state(summary)
        summary['pending_action'] = self._pending_view(task['pending_action'], task['task_id'])
        summary['completion_evidence'] = task.get('completion_evidence', '')
        summary['progress'] = task['progress'][-3:]
        summary['tool_events'] = [self._model_event(event) for event in task['tool_events'][-4:]]
        return json.dumps(summary, ensure_ascii=False, separators=(',', ':'))

    @staticmethod
    def _localize_model_state(summary):
        if summary.get('next_step') == '拆分并开始第一项可验证工作。':
            summary['next_step'] = 'Break the task into steps and begin with the first verifiable item.'

    @staticmethod
    def _model_event(event):
        event = dict(event)
        labels = {'经检查确认': 'verified by inspection', '被拒绝': 'rejected',
                  '成功，需核实': 'succeeded; verification required', '结果不确定': 'outcome uncertain',
                  '无法核实，已阻塞': 'unverifiable; task marked blocked'}
        if event.get('state') in labels:
            event['state'] = labels[event['state']]
        if event.get('evidence', '').startswith('工具返回状态：'):
            event['evidence'] = event['evidence'].replace('工具返回状态：', 'Tool returned state: ', 1)
        return event

    @staticmethod
    def _operation_id(task_id, pending):
        operation_id = pending.get('operation_id')
        if operation_id:
            return operation_id
        legacy = '\\x1f'.join((task_id, pending['started_at'], pending['tool'], pending['target']))
        return hashlib.sha256(legacy.encode('utf-8')).hexdigest()[:32]

    @staticmethod
    def _pending_view(pending, task_id):
        if not pending:
            return None
        result = {key: pending[key] for key in ('operation_id', 'started_at', 'tool', 'target', 'state')
                  if key in pending}
        result['operation_id'] = LongTaskStore._operation_id(task_id, pending)
        labels = {'成功，需核实': 'succeeded; verification required', '被拒绝': 'rejected',
                  '结果不确定': 'outcome uncertain'}
        if result.get('state') in labels:
            result['state'] = labels[result['state']]
        return result

    def invoke(self, name, args, approval=None):
        self._checkpoint_verified = False
        if name == 'long_task_status':
            return self.status_text(), '成功', self.preview_summary(name, args)
        try:
            if not isinstance(args, dict) or args.get('action') not in ('create', 'update'):
                raise ValueError
            if args['action'] == 'create':
                return self._create(args)
            return self._update(args)
        except (KeyError, TypeError, ValueError):
            return ('The long-task arguments are invalid. For create, provide a title and goal; for update, use the current task ID.',
                    '被拒绝', '长期任务')

    def take_checkpoint_verified(self):
        """Consume the signal set only after _update persisted a verified resolution."""
        verified = self._checkpoint_verified
        self._checkpoint_verified = False
        return verified

    def _create(self, args):
        if set(args) - {'action', 'title', 'goal'}:
            return 'create accepts only title and goal.', '被拒绝', '长期任务'
        title, goal = args.get('title'), args.get('goal')
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            return 'The title must contain 1 to 120 characters.', '被拒绝', '长期任务'
        if not isinstance(goal, str) or not goal.strip() or len(goal.strip()) > 2000:
            return 'The goal must contain 1 to 2,000 characters.', '被拒绝', title[:40]
        with self.store.mutex:
            existing = self._read_file()
            if existing and existing['status'] != 'completed':
                return (f"An unfinished task already exists ({existing['task_id']}: {existing['title']}, "
                        f"status: {existing['status']}); it cannot be replaced.", '被拒绝', existing['title'])
            stamp = now()
            task = {'task_id': uuid.uuid4().hex, 'title': title.strip(), 'goal': goal.strip(),
                    'status': 'active', 'progress': [], 'next_step': '拆分并开始第一项可验证工作。',
                    'waiting_for': '', 'tool_events': [], 'pending_action': None,
                    'completion_evidence': '', 'created_at': stamp, 'updated_at': stamp}
            self._save(task)
        return f"Long task created with ID {task['task_id']}.", '成功', task['title']

    def _update(self, args):
        allowed = {'action', 'task_id', 'status', 'progress_update', 'next_step',
                   'waiting_for', 'resolve_pending', 'pending_operation_id'}
        if set(args) - allowed or not isinstance(args.get('task_id'), str):
            return 'Update requires the current task ID.', '被拒绝', '长期任务'
        status = args.get('status')
        if status is not None and status not in TASK_STATUS:
            return 'The task status is invalid. Choose active, paused, blocked, or completed.', '被拒绝', '长期任务'
        progress = args.get('progress_update')
        if progress is not None and (not isinstance(progress, str) or not progress.strip() or len(progress) > 500):
            return 'Progress must include a concise, confirmed result and its evidence, within the 500-character limit.', '被拒绝', '长期任务'
        next_step, waiting_for = args.get('next_step'), args.get('waiting_for')
        if next_step is not None and (not isinstance(next_step, str) or len(next_step) > 500):
            return 'The next step must be 500 characters or fewer.', '被拒绝', '长期任务'
        if waiting_for is not None and (not isinstance(waiting_for, str) or len(waiting_for) > 500):
            return 'The waiting item must be 500 characters or fewer.', '被拒绝', '长期任务'
        resolve = args.get('resolve_pending', False)
        if type(resolve) is not bool:
            return 'resolve_pending must be a Boolean.', '被拒绝', '长期任务'
        with self.store.mutex:
            task = self._read_file()
            if not task or task['task_id'] != args['task_id']:
                return 'The current task does not exist or the task ID does not match.', '被拒绝', '长期任务'
            if task['status'] == 'completed' and status == 'completed' and progress:
                return 'The task is already complete; its saved completion evidence cannot be replaced.', '被拒绝', task['title']
            if resolve and task['pending_action'] is None:
                return 'There is no pending operation to verify.', '被拒绝', task['title']
            if resolve and args.get('pending_operation_id') != self._operation_id(task['task_id'], task['pending_action']):
                return 'The pending operation ID does not match.', '被拒绝', task['title']
            # Ticket 140: the program itself has matched the read against the expected effect, so a
            # read earlier in the same step (or any earlier step of this run) is enough.
            if resolve and task['pending_action'].get('verified_in_step') is None:
                return ('First read the target of the pending operation with a read-only tool '
                        '(it can be earlier in the same step), then resolve it.'), '被拒绝', task['title']
            if status == 'completed' and not progress:
                return 'To mark the task complete, include verifiable final-result evidence in this progress_update.', '被拒绝', task['title']
            if task['pending_action'] and status == 'completed' and not resolve:
                return 'The task cannot be marked complete while an operation remains unverified.', '被拒绝', task['title']
            if progress:
                if len(task['progress']) >= MAX_PROGRESS_ITEMS and status == 'completed':
                    task['completion_evidence'] = progress.strip()
                else:
                    if len(task['progress']) >= MAX_PROGRESS_ITEMS:
                        # Ticket 140: keep the newest entries here; older ones move to an archive
                        # file instead of refusing new progress.
                        overflow = task['progress'][:len(task['progress']) - MAX_PROGRESS_ITEMS + 1]
                        for item in overflow:
                            self.store.append(f'{self.character_id}/long-task-progress-archive.jsonl',
                                              {'task_id': task['task_id'], 'title': task['title'], **item})
                        task['progress'] = task['progress'][len(overflow):]
                    task['progress'].append({'time': now(), 'text': progress.strip()})
            if resolve:
                pending = task['pending_action']
                task['tool_events'].append({'time': now(),
                    'operation_id': self._operation_id(task['task_id'], pending),
                    'tool': pending['tool'], 'target': pending['target'], 'state': '经检查确认',
                    'evidence': f"operation {self._operation_id(task['task_id'], pending)} verified by "
                                f"{pending['verified_by']} ({pending['verified_sha256'][:16]})"})
                task['pending_action'] = None
                task['tool_events'] = task['tool_events'][-MAX_TOOL_EVENTS:]
            if status == 'blocked' and task['pending_action'] and not resolve:
                pending = task['pending_action']
                task['tool_events'].append({'time': now(),
                    'operation_id': self._operation_id(task['task_id'], pending),
                    'tool': pending['tool'], 'target': pending['target'], 'state': '无法核实，已阻塞',
                    'evidence': 'Peter marked the task blocked because this operation could not be verified.'})
                task['pending_action'] = None
                task['tool_events'] = task['tool_events'][-MAX_TOOL_EVENTS:]
            if status is not None:
                task['status'] = status
            if next_step is not None:
                task['next_step'] = next_step.strip()
            if waiting_for is not None:
                task['waiting_for'] = waiting_for.strip()
            task['updated_at'] = now()
            self._save(task)
            if resolve:
                self._checkpoint_verified = True
        return f"Long task updated; status: {task['status']}.", '成功', task['title']

    @staticmethod
    def _normalized_target(target):
        value = ntpath.normcase(ntpath.normpath(str(target).strip()))
        return value.replace('\\', '/').casefold().rstrip('/')

    @staticmethod
    def checkpoint_target(name, args, fallback):
        """Return the stable business target used by a side-effect checkpoint.

        Tool summaries are for people and can change after a successful action
        (for example, trace adds an archive note to a memory title).  A memory
        ID is the durable target for native memory operations instead.
        """
        if name.startswith('mcp__') and isinstance(args, dict):
            encoded = json.dumps(args, sort_keys=True, separators=(',', ':')).encode('utf-8')
            return name + ':' + hashlib.sha256(encoded).hexdigest()
        if name in {'hold', 'trace', 'anchor', 'release'} and isinstance(args, dict):
            memory_id = args.get('bucket_id') or args.get('target_id')
            if isinstance(memory_id, str) and memory_id.strip():
                return memory_id
        if name == 'memory_read' and isinstance(args, dict):
            memory_id = args.get('memory_id')
            if isinstance(memory_id, str) and memory_id.strip():
                return memory_id
        if name == 'move_lab_file' and isinstance(args, dict):
            destination = args.get('destination')
            if isinstance(destination, str) and destination.strip():
                return destination
        return fallback

    @staticmethod
    def _expected_effect(name, args):
        if name == 'move_lab_file' and isinstance(args, dict):
            if all(isinstance(args.get(key), str) for key in ('source', 'destination')):
                # The successful move result carries the verified destination digest.
                # If the process stops before persisting that result, recovery fails closed.
                return {'kind': 'exact', 'sha256': None, 'byte_length': None}
            return None
        if name not in ('write_file', 'append_file', 'trace') or not isinstance(args, dict):
            return None
        content = args.get('content')
        if not isinstance(content, str):
            return None
        encoded = content.encode('utf-8')
        if name == 'trace':
            return {'kind': 'memory_body', 'sha256': hashlib.sha256(encoded).hexdigest(),
                    'byte_length': len(encoded)}
        return {'kind': 'exact' if name == 'write_file' else 'suffix',
                'sha256': hashlib.sha256(encoded).hexdigest(), 'byte_length': len(encoded)}

    def before_tool(self, name, target, autonomy, args=None):
        with self.store.mutex:
            task = self._read_file()
            if not task:
                return None, False
            if task['pending_action']:
                pending = task['pending_action']
                expected = pending.get('normalized_target') or self._normalized_target(pending['target'])
                proposed = self._normalized_target(target)
                same_target = bool(expected and expected == proposed)
                if autonomy or same_target:
                    if pending['tool'] in {'hold', 'trace', 'anchor', 'release'}:
                        how = f"Read memory {pending['target']} with memory_read"
                    elif pending['tool'] in {'write_file', 'append_file'}:
                        how = 'Read the target with read_text_file'
                    else:
                        how = 'Read the target first'
                    return (f"The previous side-effect operation {pending['tool']} on {pending['target']} "
                            f'is still unverified. {how}, then submit evidence with long_task_update; '
                            'if it cannot be verified, update the task with status=blocked to release it.',
                            False)
                return None, False
            if not autonomy or task['status'] != 'active':
                return None, False
            task['pending_action'] = {'operation_id': uuid.uuid4().hex, 'started_at': now(),
                'tool': name, 'target': str(target)[:240], 'normalized_target': self._normalized_target(target),
                'expected_effect': self._expected_effect(name, args), 'verified_in_step': None}
            task['updated_at'] = now()
            self._save(task)
            return None, True

    def begin_tool(self, name, target):
        """Start an autonomous side-effect checkpoint (test/helper convenience)."""
        blocked, _ = self.before_tool(name, target, autonomy=True)
        return blocked

    def finish_tool(self, name, target, state, result=None):
        with self.store.mutex:
            task = self._read_file()
            if not task or task['status'] != 'active':
                return
            pending = task['pending_action']
            if not pending or pending['tool'] != name:
                raise UserError('The long-task side-effect checkpoint did not match. Further tools were stopped.')
            if self._normalized_target(target) != (pending.get('normalized_target') or
                                                     self._normalized_target(pending['target'])):
                raise UserError('The side-effect target does not match its persisted checkpoint. The operation remains unverified.')
            task['tool_events'].append({'time': now(),
                                        'operation_id': self._operation_id(task['task_id'], pending),
                                        'tool': name, 'target': str(target)[:240],
                                        'state': state[:40],
                                        'evidence': f'工具返回状态：{state[:40]}'})
            task['tool_events'] = task['tool_events'][-MAX_TOOL_EVENTS:]
            if state == '成功':
                # Ticket 152: a write the tool itself reports as successful needs no read-back.
                # Only interrupted or uncertain operations stay pending for verification.
                task['pending_action'] = None
                task['updated_at'] = now()
                self._save(task)
                return
            if state == '被拒绝':
                task['pending_action'] = None
            else:
                pending['state'] = '结果不确定'
                pending['verified_in_step'] = None
            task['updated_at'] = now()
            self._save(task)

    def observe_tool_result(self, name, target, state, result=None):
        if name not in READ_ONLY_TOOLS or state != '成功' or self.current_tool_step < 0:
            return
        if not isinstance(result, str):
            return
        with self.store.mutex:
            task = self._read_file()
            if not task or not task['pending_action']:
                return
            pending = task['pending_action']
            expected = pending.get('normalized_target') or self._normalized_target(pending['target'])
            observed = self._normalized_target(target)
            if not expected or observed != expected:
                return
            content = result.encode('utf-8')
            effect = pending.get('expected_effect')
            if effect is None:
                if pending['tool'] in ('write_file', 'append_file', 'move_lab_file') or not result.strip():
                    return
            else:
                if pending['tool'] == 'move_lab_file' and (
                        not isinstance(effect.get('sha256'), str) or
                        not isinstance(effect.get('byte_length'), int)):
                    return
                if effect['kind'] == 'memory_body':
                    if name != 'memory_read':
                        return
                    try:
                        readback = json.loads(result)
                        if (self._normalized_target(readback.get('memory_id', '')) != expected
                                or readback.get('next_offset') is not None
                                or not isinstance(readback.get('text'), str)):
                            return
                        lines = readback['text'].splitlines()
                        end = next((index for index, line in enumerate(lines[1:], 1)
                                    if line.strip() == '---'), None)
                        if not lines or lines[0].strip() != '---' or end is None:
                            return
                        actual = '\n'.join(lines[end + 1:]).encode('utf-8')
                    except (TypeError, ValueError):
                        return
                    if hashlib.sha256(actual).hexdigest() != effect['sha256']:
                        return
                else:
                    if pending['tool'] == 'move_lab_file' and name == 'lab_file_digest':
                        try:
                            actual = json.loads(result)
                        except (TypeError, ValueError):
                            return
                        if (not isinstance(actual, dict)
                                or self._normalized_target(actual.get('path', '')) != expected
                                or type(actual.get('byte_length')) is not int
                                or actual['byte_length'] != effect['byte_length']
                                or actual.get('sha256') != effect['sha256']):
                            return
                    else:
                        if name != 'read_text_file':
                            return
                        if effect['kind'] == 'suffix' and pending.get('state') != '成功，需核实':
                            return
                        length = effect['byte_length']
                        if len(content) < length:
                            return
                        actual = content if effect['kind'] == 'exact' else content[-length:] if length else b''
                        digest = hashlib.sha256(actual).hexdigest()
                        if (effect['kind'] == 'exact' and len(content) != length) or digest != effect['sha256']:
                            return
            pending['verified_in_step'] = self.current_tool_step
            pending['verified_by'] = name
            pending['verified_sha256'] = hashlib.sha256(content).hexdigest()
            task['updated_at'] = now()
            self._save(task)

    def summary(self):
        task = self.read()
        if not task:
            return None
        last = task['progress'][-1]['text'] if task['progress'] else '尚无经 Peter 确认的阶段成果。'
        pending = task['pending_action']
        if pending:
            last = f"待核实操作：{pending['tool']}（{pending['target']}）"
        elif task['tool_events']:
            event = task['tool_events'][-1]
            if event['state'] == '经检查确认':
                last = f"已核实：{event['tool']} {event['target']}"
            elif event['state'] == '被拒绝':
                last = f"上次操作未执行：{event['tool']} {event['target']}"
            elif event['state'] == '无法核实，已阻塞':
                last = f"无法核实，已标记阻塞：{event['tool']} {event['target']}"
        return {'title': task['title'], 'status': task['status'], 'progress': last,
                'next_step': task['next_step'], 'waiting_for': task['waiting_for']}
