Warning: truncated output (original token count: 12074)
Total output lines: 942

"""Local terminal chat. Private state stays beside this file."""
import argparse
import json
import os
import re
import signal
import sys
import tomllib
from dataclasses import replace
import uuid
import threading
from functools import wraps
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MILLION = Decimal(1_000_000)

RAW_DSML_TOOL_CALL = re.compile(r'<[|｜]DSML[|｜](?:tool_calls|function_calls|invoke)\b', re.IGNORECASE)

SEND_MESSAGE = {'type': 'function', 'function': {
    'name': 'send_message',
    'description': ('Send one independent chat message to the user immediately. In any language, '
                    'write like texting: one sentence or short thought per message; call this '
                    'tool multiple times in the same step when you have more to say. You may '
                    'send just one. Code or a long list may stay in one longer message. '
                    'A step containing only send_message calls ends the turn.'),
    'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                   'required': ['text'], 'additionalProperties': False},
}}


def synchronized(fn):
    @wraps(fn)
    def locked(self, *args, **kwargs):
        with self.mutex:
            return fn(self, *args, **kwargs)
    return locked


class UserError(Exception):
    pass


class TooLong(UserError):
    """Nothing was saved or sent; the session can carry on."""


class RequestFailed(UserError):
    """A provider request failed after being sent; its reservation stays pending."""


class BudgetExceeded(UserError):
    """A request was refused before sending because of the configured limit."""


def now():
    return datetime.now().astimezone().isoformat()


@contextmanager
def saving():
    # Once a reply is available, finish saving before honoring Ctrl+C.
    interrupted = []
    previous = signal.signal(signal.SIGINT, lambda *_: interrupted.append(True))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
    if interrupted:
        raise KeyboardInterrupt


class Store:
    def __init__(self, root):
        self.mutex = threading.RLock()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @synchronized
    def read(self, name):
        path = self.root / name
        if not path.exists():
            return []
        try:
            with path.open(encoding='utf-8') as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except (ValueError, UnicodeError) as exc:
            raise UserError('本机账本损坏，已停止；请保留 data 文件夹，不要删除记录。') from exc

    @synchronized
    def append(self, name, event):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to append to a torn final record rather than hiding corruption.
        if path.exists() and path.stat().st_size:
            with path.open('rb') as handle:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b'\n':
                    raise UserError('账本末行不完整，已停止；请保留 data 文件夹。')
        data = (json.dumps({'time': now(), **event}, ensure_ascii=False) + '\n').encode('utf-8')
        with path.open('ab', buffering=0) as handle:
            if handle.write(data) != len(data):
                raise UserError('记录未完整保存，已停止。')
            os.fsync(handle.fileno())

    @contextmanager
    def lock(self):
        # OS advisory lock is released even after a crash; shared by all roles.
        with (self.root / 'session.lock').open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UserError('已有聊天窗口在运行，请先关闭那个窗口。') from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass
class Character:
    id: str
    name: str
    persona: str
    history_rounds: int
    max_output_tokens: int
    provider: dict
    provider_name: str
    memory: dict = None
    tools: dict = None


def positive_int(value):
    if type(value) is not int or value <= 0:
        raise ValueError('positive integer required')
    return value


def price(value):
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError('positive finite price required')
    return result


def load_character(root, character_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', character_id):
        raise UserError('角色编号只能用小写英文、数字、下划线和短横线。')
    root = Path(root).resolve()
    try:
        with (root / 'characters' / character_id / 'character.toml').open('rb') as handle:
            cfg = tomllib.load(handle)
        persona_path = (root / cfg['persona']).resolve()
        if not persona_path.is_relative_to(root / 'persona'):
            raise ValueError('persona must stay in persona directory')
        try:
            persona = persona_path.read_text(encoding='utf-8-sig').strip()
        except OSError as exc:
            raise UserError('读不到人设：请按 README 在 persona 文件夹放好本机人设后重开。') from exc
        if not persona:
            raise UserError('人设文件为空，请填写后重开。')
        provider = dict(cfg['providers'][cfg['provider']])
        for key in ('model', 'base_url', 'api_key_env'):
            if not isinstance(provider[key], str) or not provider[key].strip():
                raise ValueError(key)
        if not provider['base_url'].startswith('https://'):
            raise ValueError('HTTPS required')
        for key in ('input_cny_per_million', 'cached_input_cny_per_million', 'output_cny_per_million'):
            provider[key] = price(provider[key])
        if provider['cached_input_cny_per_million'] > provider['input_cny_per_million']:
            raise ValueError('cached rate exceeds regular rate')
        provider['max_input_tokens'] = positive_int(provider['max_input_tokens'])
        tools = dict(cfg['tools']) if 'tools' in cfg else None
        if tools is not None:
            enabled = tools.get('enabled', True)
            if type(enabled) is not bool:
                raise ValueError('tools.enabled must be boolean')
            tools = {
                'enabled': enabled,
                'folder': str(tools.get('folder', f'data/{character_id}/workspace')),
                'max_steps': positive_int(tools.get('max_steps', 6)),
                'max_result_bytes': positive_int(tools.get('max_result_bytes', 8192)),
                'max_output_tokens': positive_int(tools.get('max_output_tokens', cfg.get('max_output_tokens', 1024))),
                'persistent_append_file': tools.get('persistent_append_file'),
                'lab': tools.get('lab', 'lab'),
                'lab_max_write_bytes': positive_int(tools.get('lab_max_write_bytes', 200000)),
            }
            if tools['max_steps'] < 2:
                raise ValueError('tools.max_steps must allow a tool call and a final response')
            folder = Path(tools['folder'])
            if folder.is_absolute() or '..' in folder.parts:
                raise ValueError('tools folder must be a safe relative path')
            grant_file = tools['persistent_append_file']
            if grant_file is not None and (not isinstance(grant_file, str) or
                                          Path(grant_file).name != grant_file or
                                          grant_file in ('', '.', '..')):
                raise ValueError('persistent append file must be a filename')
            from tools_agent import lab_name_problem
            lab = tools['lab']
            if lab is not False and (lab_name_problem(lab) or
                    (isinstance(grant_file, str) and lab.casefold() == grant_file.casefold())):
                raise ValueError('lab must be a safe folder name distinct from the journal')
        if tools is None and (cfg.get('memory') or {}).get('mode') == 'native':
            # Native memory runs on the tool loop; files stay off unless [tools] says otherwise.
            tools = {'enabled': False, 'folder': f'data/{character_id}/workspace',
                     'max_steps': 6, 'max_result_bytes': 8192}
        return Character(character_id, str(cfg['name']), persona,
                         positive_int(cfg.get('history_rounds', 20)),
                         positive_int(cfg.get('max_output_tokens', 1024)), provider, cfg['provider'], cfg.get('memory'), tools)
    except UserError:
        raise
    except (OSError, KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise UserError('角色配置无法读取或字段无效，请检查 character.toml。') from exc


class ActivityState:
    """Persist the user-controlled 4B activity switch; this does not run tasks."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def enabled(self):
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                return value.get('enabled') is True
            except FileNotFoundError:
                return False
            except (ValueError, UnicodeError, AttributeError):
                return False

    def set(self, enabled):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + '.tmp')
            with tmp.open('w', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps({'enabled': bool(enabled)}) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            return bool(enabled)


class Budget:
    def __init__(self, store, daily=Decimal('10'), monthly=Decimal('300'), clock=now):
        self.mutex = threading.RLock()
        self.store, self.daily, self.monthly, self.clock = store, daily, monthly, clock

    @synchronized
    def totals(self, stamp=None):
        stamp = stamp or self.clock()
        reservations, settlements = {}, {}
        try:
            for event in self.store.read('costs.jsonl'):
                if event['type'] == 'reserve':
                    reservations[event['id']] = event
                elif event['type'] == 'settle':
                    settlements[event['id']] = event
                else:
                    raise ValueError('unknown cost event')
            day = month = pending = Decimal(0)
            for key, event in reservations.items():
                settled = settlements.get(key)
                amount = Decimal((settled or event)['amount'])
                if not amount.is_finite() or amount < 0:
                    raise ValueError('invalid amount')
                if settled is None:
                    # Uncertain requests remain reserved, including after midnight.
                    pending += amount
                else:
                    if event['period'][:10] == stamp[:10]:
                        day += amount
                    if event['period'][:7] == stamp[:7]:
     …6074 tokens truncated…d, 'content': too_long_result}
                                for call in message.tool_calls]
                loop_messages.extend(placeholders)
                fit_history(next_offered)
                can_report_results = request_size(next_offered) <= self.cfg.provider['max_input_tokens']
                del loop_messages[-len(placeholders):]
                if not can_report_results:
                    self.output(f'（长度检查：估算 {request_size(next_offered)}，上限 {self.cfg.provider["max_input_tokens"]}，'
                                f'按实测比例 {calibration:.2f} 折算。）')
                    for call in message.tool_calls:
                        try:
                            summary = self.tools.preview_summary(call.function.name, json.loads(call.function.arguments))
                        except (ValueError, TypeError, AttributeError):
                            summary = None
                        summary = summary if isinstance(summary, str) else call.function.name
                        self.output(display_call(call.function.name, summary, '被拒绝', '工具调用记录超过长度上限'))
                        summaries.append({'tool': call.function.name, 'path': summary, 'state': '被拒绝'})
                        self.store.append(f'{self.cfg.id}/tools.jsonl', {'request_id': request_id,
                            'tool': call.function.name, 'path': summary, 'state': '被拒绝'})
                    self.budget.settle(next_reservation, next_cfg, 0, 0, 0)
                    final = '工具调用记录超过长度上限，未执行工具。'
                    break
                for call in message.tool_calls:
                    try:
                        if call.function.name == 'send_message':
                            result, state, summary = self.send_message(call.function.arguments, request_id)
                        else:
                            args = json.loads(call.function.arguments)
                            if not isinstance(args, dict):
                                raise ValueError('arguments must be an object')
                            if self.tools:
                                result, state, summary = self.tools.invoke(call.function.name, args, self.approval)
                            else:
                                result, state, summary = '程序拒绝：工具未开放。', '被拒绝', call.function.name
                    except (ValueError, TypeError):
                        result, state, summary = '程序拒绝：工具参数不是合法 JSON 对象。', '被拒绝', call.function.name
                    tool_message = {'role': 'tool', 'tool_call_id': call.id, 'content': result}
                    loop_messages.append(tool_message)
                    history_bytes = calibration * sum(message_bytes(m)
                        for m in loop_messages[history_start:history_start + history_count])
                    if request_size(next_offered) - history_bytes > self.cfg.provider['max_input_tokens']:
                        tool_message['content'] = too_long_result
                        state = '结果过长'
                    fit_history(next_offered)
                    if request_size(next_offered) > self.cfg.provider['max_input_tokens']:
                        tool_message['content'] = too_long_result
                        state = '结果过长'
                    if call.function.name != 'send_message' or state != '成功':
                        self.output(display_call(call.function.name, summary, state,
                                                 tool_message['content'] if state != '成功' else None))
                    summaries.append({'tool': call.function.name, 'path': summary, 'state': state})
                    self.store.append(f'{self.cfg.id}/tools.jsonl', {'request_id': request_id,
                        'tool': call.function.name, 'path': summary, 'state': state})
            except BaseException:
                self.budget.settle(next_reservation, next_cfg, 0, 0, 0)
                raise
        if not final:
            final = '这次工具步骤用完了。'
            self.output(final)
        return final, summaries


def make_model(root, cfg):
    from dotenv import dotenv_values
    from openai import OpenAI
    # No environment fallback or interpolation: key comes only from this file.
    values = dotenv_values(Path(root) / '.env', interpolate=False, encoding='utf-8-sig')
    key = values.get(cfg.provider['api_key_env'])
    if not key or not key.strip():
        raise UserError('本机 .env 缺少所选接口的 API Key，请按 README 填写。')
    client = OpenAI(api_key=key, base_url=cfg.provider['base_url'], max_retries=0, timeout=60)

    def call(messages, *, json_output=False, max_tokens=None, thinking=None, detail=False,
             tools=None, message_output=False):
        extra = {}
        if (thinking or cfg.provider.get('thinking')) == 'disabled':
            extra['extra_body'] = {'thinking': {'type': 'disabled'}}
        if json_output:
            extra['response_format'] = {'type': 'json_object'}
        request = {'model': cfg.provider['model'], 'messages': messages,
                   'max_tokens': max_tokens or cfg.max_output_tokens, 'stream': False, **extra}
        if tools is not None:
            request['tools'] = tools
        response = client.chat.completions.create(**request)
        usage = response.usage
        if usage is None:
            raise UserError('接口未返回用量；本次保留预留费用。')
        details = getattr(usage, 'prompt_tokens_details', None)
        cached = getattr(usage, 'prompt_cache_hit_tokens', None)
        if cached is None:
            cached = getattr(details, 'cached_tokens', 0) or 0
        choice = response.choices[0]
        if tools is not None or message_output:
            message = choice.message.model_dump(exclude_none=True)
            reasoning = getattr(choice.message, 'reasoning_content', None)
            if reasoning is not None:
                message['reasoning_content'] = reasoning
            # Keep raw OpenAI-shaped message object for the tool loop.
            result = (choice.message, usage.prompt_tokens, usage.completion_tokens, cached)
            return result + (choice.finish_reason,) if detail else result
        result = (choice.message.content, usage.prompt_tokens, usage.completion_tokens, cached)
        return result + (choice.finish_reason,) if detail else result

    return client, call


def drop_typeahead():
    # Keys typed while waiting for a reply get echoed into the reply on the
    # Windows console, so discard them before the next prompt.
    try:
        import msvcrt
    except ImportError:
        return False
    dropped = False
    while msvcrt.kbhit():
        msvcrt.getwch()
        dropped = True
    return dropped


def run_chat(chat, input_fn=input, output=print, drop=drop_typeahead, activity=None):
    activity = activity or ActivityState(chat.store.root / chat.cfg.id / 'resident-state.json')
    output(f'接上了 {chat.rounds} 轮。输入 /cost 查花费，/self 看当前说明，/pause 或 /resume 切换自主活动，/quit 退出。')
    if getattr(chat.tools, 'startup_notice', None):
        output(chat.tools.startup_notice)
    if chat.pending_capability_event:
        output(chat.pending_capability_event)

    def notify_dropped():
        if drop():
            output('（等回复时打的字没有收到，请重新输入。）')

    try:
        while True:
            if chat.memory:
                for notice in chat.memory.notices():
                    output(notice)
            text = input_fn('你: ').strip()
            if text == '/quit':
                break
            if text == '/pause':
                activity.set(False)
                output('自主活动已暂停。')
            elif text == '/resume':
                activity.set(True)
                output('自主活动已开启；当前版本不会运行自主任务。')
            elif text == '/cost':
                output(chat.budget.display())
                output(chat.budget.cache_display())
            elif text == '/self':
                if chat.last_self is None:
                    output('（尚未发送消息：这是当前说明的预览，发送前会刷新。）')
                    output(build_self_description(chat.cfg, chat.budget, memory_mode(chat.memory), chat.tools))
                else:
                    output(chat.last_self)
            elif text == '/memory' or text.startswith(('/forget ', '/restore ', '/delete ')):
                if chat.memory:
                    output(chat.memory.command(text))
                else:
                    output('当前没有接入长期记忆。')
            elif text:
                # Storage and accounting errors stop the session rather than
                # permitting a subsequent request on uncertain local state.
                try:
                    reply = chat.send(text)
                except TooLong as exc:
                    output(str(exc))
                    continue
                except RequestFailed as exc:
                    output(str(exc))
                    notify_dropped()
                    continue
                if chat.trim_note:
                    output(chat.trim_note)
                if not chat.reply_displayed:
                    output(f'{chat.cfg.name}: {reply}')
                notify_dropped()
    except (KeyboardInterrupt, EOFError):
        output('\n已退出，已保存的记录仍在；未完成请求的预留额可用 /cost 查看。')


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='VEIL 本机文字聊天')
    parser.add_argument('--character', default='peter')
    parser.add_argument('--cost', action='store_true')
    args = parser.parse_args()
    try:
        store = Store(ROOT / 'data')
        with store.lock():
            budget = Budget(store)
            if args.cost:
                print(budget.display())
                print(budget.cache_display())
                return 0
            cfg = load_character(ROOT, args.character)
            client, model = make_model(ROOT, cfg)
            with client:
                from tools_agent import FilesystemTools
                with FilesystemTools(ROOT, cfg, store) as tools:
                    if cfg.memory and cfg.memory.get('enabled'):
                        from memory_agent import Memory, open_ombre
                        with open_ombre(ROOT, cfg) as backend:
                            with Memory(cfg, store, budget, model, backend) as memory:
                                if memory.native:
                                    from memory_native import NativeMemoryTools, ToolSet
                                    tools = ToolSet(tools, NativeMemoryTools(
                                        memory, cfg.tools['max_result_bytes']))
                                run_chat(Chat(cfg, store, budget, model, memory, tools))
                            for notice in memory.notices():
                                print(notice)
                    else:
                        run_chat(Chat(cfg, store, budget, model, tools=tools))
        return 0
    except UserError as exc:
        print(str(exc))
    except ImportError:
        print('缺少运行依赖，请先按 README 完成安装。')
    except (OSError, UnicodeError):
        print('本机文件无法读写，已停止；请检查磁盘空间和文件权限。')
    except (KeyboardInterrupt, EOFError):
        print('\n已退出。')
        return 0
    return 1


if __name__ == '__main__':
    sys.modules.setdefault('veil_agent', sys.modules[__name__])
    raise SystemExit(main())

