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
                        month += amount
            return day, month, pending
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise UserError('花费账本无法核对，已停止；请保留 data 文件夹。') from exc

    @synchronized
    def reserve(self, cfg):
        p = cfg.provider
        amount = (p['max_input_tokens'] * p['input_cny_per_million']
                  + cfg.max_output_tokens * p['output_cny_per_million']) / MILLION
        day, month, pending = self.totals()
        if day + pending + amount > self.daily or month + pending + amount > self.monthly:
            raise BudgetExceeded('本次费用上限会超过 ¥10/天或 ¥300/月，未发送。输入 /cost 查看。')
        request_id = uuid.uuid4().hex
        self.store.append('costs.jsonl', {'type': 'reserve', 'id': request_id,
                          'period': self.clock(), 'amount': str(amount),
                          'character': cfg.id, 'model': p['model']})
        return request_id, amount

    @synchronized
    def estimate(self, character, model, tokens, rate):
        rate = Decimal(str(rate))
        if type(tokens) is not int or tokens < 0 or not rate.is_finite() or rate < 0:
            raise UserError('向量费用配置无效。')
        amount = tokens * rate / MILLION
        day, month, pending = self.totals()
        if day + pending + amount > self.daily or month + pending + amount > self.monthly:
            raise UserError('向量请求超过共享预算，未发送。')
        request_id = uuid.uuid4().hex
        self.store.append('costs.jsonl', {'type': 'reserve', 'id': request_id,
            'period': self.clock(), 'amount': str(amount), 'character': character,
            'model': model, 'accounting': 'embedding-estimate', 'estimated_tokens': tokens})
        return request_id, amount

    @synchronized
    def estimate_model(self, character, model, tokens_in, tokens_out, in_rate, out_rate):
        # Ombre's own model calls happen inside the child process, so they are estimated here.
        in_rate, out_rate = Decimal(str(in_rate)), Decimal(str(out_rate))
        if (type(tokens_in) is not int or type(tokens_out) is not int or tokens_in < 0 or tokens_out < 0
                or not in_rate.is_finite() or not out_rate.is_finite() or in_rate < 0 or out_rate < 0):
            raise UserError('记忆模型费用配置无效。')
        amount = (tokens_in * in_rate + tokens_out * out_rate) / MILLION
        day, month, pending = self.totals()
        if day + pending + amount > self.daily or month + pending + amount > self.monthly:
            raise UserError('记忆模型请求超过共享预算，未发送。')
        request_id = uuid.uuid4().hex
        self.store.append('costs.jsonl', {'type': 'reserve', 'id': request_id,
            'period': self.clock(), 'amount': str(amount), 'character': character,
            'model': model, 'accounting': 'ombre-model-estimate',
            'estimated_tokens': tokens_in + tokens_out})
        return request_id, amount

    @synchronized
    def settle_estimate(self, reservation, accounting='embedding-estimate'):
        request_id, amount = reservation
        self.store.append('costs.jsonl', {'type': 'settle', 'id': request_id,
            'amount': str(amount), 'accounting': accounting})

    @synchronized
    def settle(self, request_id, cfg, prompt, completion, cached):
        if any(type(n) is not int or n < 0 for n in (prompt, completion, cached)) or cached > prompt:
            raise UserError('接口未返回有效用量；本次保留预留费用。')
        p = cfg.provider
        if prompt > p['max_input_tokens'] or completion > cfg.max_output_tokens:
            raise UserError('接口用量超过配置边界；已停止并保留预留费用，请核对供应商。')
        amount = ((prompt - cached) * p['input_cny_per_million']
                  + cached * p['cached_input_cny_per_million']
                  + completion * p['output_cny_per_million']) / MILLION
        self.store.append('costs.jsonl', {'type': 'settle', 'id': request_id,
                          'amount': str(amount), 'prompt_tokens': prompt,
                          'completion_tokens': completion, 'cached_tokens': cached})

    @synchronized
    def cache_display(self, stamp=None):
        """Today's share of chat-model input tokens the provider served from its cache."""
        stamp = stamp or self.clock()
        try:
            periods, prompt, cached = {}, 0, 0
            for event in self.store.read('costs.jsonl'):
                if event['type'] == 'reserve':
                    periods[event['id']] = event['period']
                elif event['type'] == 'settle' and 'prompt_tokens' in event:
                    if periods.get(event['id'], '')[:10] == stamp[:10]:
                        prompt += event['prompt_tokens']
                        cached += event['cached_tokens']
        except (KeyError, TypeError) as exc:
            raise UserError('花费账本无法核对，已停止；请保留 data 文件夹。') from exc
        if not prompt:
            return '今天还没有聊天模型的用量记录，缓存命中率暂无。'
        return (f'今天聊天缓存命中 {cached * 100 / prompt:.0f}%（命中 {cached} / 输入 {prompt} tokens；'
                '只统计聊天模型，不含记忆打标和向量）')

    def display(self, stamp=None):
        day, month, pending = self.totals(stamp)
        return (f'今天 ¥{day:.6f} / {self.daily}；本月 ¥{month:.6f} / {self.monthly}；'
                f'待核对预留 ¥{pending:.6f}')


def memory_mode(memory):
    return ('native' if getattr(memory, 'native', False) else True) if memory else False


def self_facts(cfg, budget, memory=False, capabilities=None):
    """(stable, changing): the stable part is identical every request so the provider can cache it;
    the changing part (time, spending ledger) is sent after the chat history."""
    stamp = budget.clock()
    local_time = datetime.fromisoformat(stamp)
    weekday = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')[local_time.weekday()]
    stable = (
        'SYSTEM FACTS (program-generated each request, not user-authored). '
        'Use these over conflicting persona/history; keep your character\'s voice.\n'
        f'You are an AI companion: {json.dumps(cfg.name, ensure_ascii=False)}; '
        f'provider {json.dumps(cfg.provider_name, ensure_ascii=False)}; '
        f'model {json.dumps(cfg.provider["model"], ensure_ascii=False)}.\n'
        f'Available: terminal text chat; up to {cfg.history_rounds} recent rounds included verbatim '
        'per request; full chat logs saved locally.\n'
        + (('Long-term memory: you decide what to save and call the hold tool yourself (the user need not say '
            '"remember"); relevant memories are also recalled automatically each turn; '
            if memory == 'native' else
            'Long-term memory: automatic saving every four completed rounds, automatic recall; ')
         + 'the user can list, suppress, restore and delete memories with /memory, /forget, /restore, /delete. '
         if memory else 'You have no long-term memory; after restart you know only restored recent rounds. ')
        +
        ((capabilities.description if isinstance(getattr(capabilities, 'description', None), str)
          else 'Tools are currently unavailable. ') if capabilities else
         ('You have no tools (cannot open files/folders or run scripts). '
          if not cfg.tools or not cfg.tools.get('enabled', True)
          else 'Tools are currently unavailable. '))
        + 'You have no voice. '
        'Unlisted abilities are unavailable. Do not invent memories, actions or tool results.'
    )
    changing = (f'Local time: {stamp} ({weekday}).\n'
                f'Pre-request ledger (CNY, excludes this reply): {budget.display(stamp)}\n'
                'Quote its cutoff when asked about spending; /cost includes new usage after this reply.')
    return stable, changing


def build_self_description(cfg, budget, memory=False, capabilities=None):
    stable, changing = self_facts(cfg, budget, memory, capabilities)
    return stable + '\n' + changing


def history_keep(total, limit):
    """How many recent rounds to send. Once the window is full, dropping one old round per message moves
    the start of every request and the provider's prefix cache never hits. So drop a chunk of rounds at
    once and grow back: the start stays put for several messages. Never more than `limit` rounds."""
    chunk = limit // 4
    if chunk < 2 or total <= limit:
        return min(total, limit)
    return limit - chunk + 1 + (total - limit - 1) % chunk


class Chat:
    def __init__(self, cfg, store, budget, model, memory=None, tools=None, approval=input, output=print):
        self.cfg, self.store, self.budget, self.model = cfg, store, budget, model
        self.path = f'{cfg.id}/chat.jsonl'
        self.history, self.rounds = self.recent()
        self.last_self = None
        self.trim_note = None
        self.trim_noted = False
        self.memory = memory
        self.tools, self.approval, self.output = tools, approval, output
        self.pending_capability_event = tools.capability_event if tools else None
        self.tool_mode = bool((cfg.tools and cfg.tools.get('enabled', True)) or (tools and tools.tools))
        self.sent_messages = []
        self.reply_displayed = False

    def tool_schemas(self):
        from tools_agent import permitted_tools
        return [*permitted_tools(self.tools.tools if self.tools else []), SEND_MESSAGE]

    def send_message(self, arguments, request_id):
        try:
            args = json.loads(arguments)
        except (ValueError, TypeError):
            args = None
        if (not isinstance(args, dict) or set(args) != {'text'} or
                not isinstance(args['text'], str) or not args['text'].strip()):
            return '程序拒绝：send_message 需要非空 text 文字。', '被拒绝', 'send_message'
        text = args['text']
        with saving():
            self.store.append(self.path, {'role': 'assistant', 'content': text,
                'request_id': request_id, 'origin': 'reply'})
            self.sent_messages.append(text)
            self.output(f'{self.cfg.name}: {text}')
        return '已发送。', '成功', 'send_message'

    def recent(self):
        groups = []
        try:
            for event in self.store.read(self.path):
                role, content = event['role'], event['content']
                if role not in ('user', 'assistant') or not isinstance(content, str):
                    raise ValueError('invalid chat record')
                if role == 'user':
                    groups.append([])
                if not groups:
                    groups.append([])
                groups[-1].append(event)
        except (KeyError, ValueError, TypeError) as exc:
            raise UserError('聊天记录无法读取，已停止；请保留 data 文件夹。') from exc
        groups = groups[-history_keep(len(groups), self.cfg.history_rounds):]
        self.history_events = [{**m, 'origin': m.get('origin', 'reply')} for group in groups for m in group]
        return [{'role': m['role'], 'content': m['content']} for m in self.history_events], sum(any(m['role'] == 'assistant' for m in g) for g in groups)

    def send(self, text):
        self.sent_messages = []
        self.reply_displayed = False
        recalled = self.memory.context(text) if self.memory else []
        stable, changing = self_facts(self.cfg, self.budget, memory_mode(self.memory), self.tools)
        description = stable + '\n' + changing
        # Order matters for the provider's prefix cache: everything that stays the same between requests
        # comes first, then the chat history, then what changes every request (recalled memories,
        # capability notice, time and ledger) right before the user's message.
        head = [{'role': 'system', 'content': self.cfg.persona},
                {'role': 'system', 'content': stable}]
        tail = [*recalled]
        if self.pending_capability_event:
            tail.append({'role': 'system', 'content':
                '（程序生成的能力变化记录）' + self.pending_capability_event})
        tail.append({'role': 'system', 'content': changing})
        user = {'role': 'user', 'content': text}
        tool_schema = []
        if self.tool_mode and self.cfg.tools['max_steps'] > 1:
            tool_schema = self.tool_schemas()
        # UTF-8 byte count is deliberately conservative, with ample chat framing
        # overhead. Reserve the entire configured input limit, not an average.
        def bound(history):
            return (sum(len(m['content'].encode('utf-8')) + 256 for m in [*head, *history, *tail, user])
                    + len(json.dumps(tool_schema, ensure_ascii=False).encode('utf-8')) + 256)
        limit = self.cfg.provider['max_input_tokens']
        history = list(self.history)
        # Long replies make the window outgrow the limit. Drop the oldest whole
        # rounds from this request only; chat.jsonl keeps everything.
        while history and bound(history) > limit:
            history.pop(0)
            while history and history[0]['role'] != 'user':
                history.pop(0)
        if bound(history) > limit:
            raise TooLong('这条输入加上人设和记忆太长，未发送；请缩短输入后再发。')
        kept = sum(m['role'] == 'user' for m in history)
        self.trim_note = None
        if len(history) < len(self.history) and not self.trim_noted:
            self.trim_noted = True
            self.trim_note = (f'（为了不超出长度上限，这次只带了最近 {kept} 轮原文；'
                              '更早的聊天仍保存在本机，长期记忆不受影响。）')
        messages = [*head, *history, *tail, user]
        tool_summaries = []
        with saving():
            tool_cfg = (replace(self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens))
                        if self.tool_mode else self.cfg)
            request_id, _ = self.budget.reserve(tool_cfg)
            self.store.append(self.path, {'role': 'user', 'content': text, 'request_id': request_id})
            if self.pending_capability_event:
                self.store.append(f'{self.cfg.id}/tools.jsonl', {'request_id': request_id,
                    'kind': 'capability', 'text': self.pending_capability_event})
                self.tools.consume_capability_event()
                self.pending_capability_event = None
        try:
            self.last_self = description
            if self.tool_mode:
                reply, tool_summaries = self._tool_chat(messages, request_id, len(history), len(head))
            else:
                try:
                    reply, prompt, completion, cached = self.model(messages)
                except UserError:
                    raise
                except Exception as exc:
                    # Keep the reservation pending: the provider may have received
                    # the request even when its response was lost.
                    raise RequestFailed('请求失败，未自动重试；输入已保存，本次保留预留费用。') from exc
        except (KeyboardInterrupt, EOFError):
            raise
        except (TooLong, RequestFailed):
            # The failed message may already be persisted (provider failure or a
            # tool-mode length failure). Keep this live session aligned with a
            # reopened one; recent() also fails closed on corrupt chat records.
            self.history, self.rounds = self.recent()
            raise
        with saving():
            if not isinstance(reply, str) or not reply.strip():
                raise UserError('接口没有返回文字回复；本次保留预留费用。')
            self.reply_displayed = reply in self.sent_messages
            if not self.reply_displayed:
                self.store.append(self.path, {'role': 'assistant', 'content': reply,
                    'request_id': request_id, 'origin': 'reply'})
            if not self.tool_mode:
                self.budget.settle(request_id, self.cfg, prompt, completion, cached)
        self.history, self.rounds = self.recent()
        if self.memory:
            self.memory.completed()
        return reply

    def _tool_chat(self, messages, request_id, history_count, history_start=None):
        from tools_agent import display_call
        summaries = []
        loop_messages = list(messages)
        if history_start is None:
            history_start = len(messages) - history_count - 1
        final = ''
        next_reservation = None

        # max_input_tokens is a TOKEN limit, but request bytes overcount tokens (roughly 3-4 bytes per token
        # in any language). After each response the provider reports the real prompt size, so later estimates
        # scale the bytes by that measured ratio, never below 0.5 (still a 2x margin over a typical text).
        calibration = 1.0

        def message_bytes(m):
            return len(json.dumps(m, ensure_ascii=False).encode('utf-8')) + 256

        def schema_bytes(offered):
            return len(json.dumps(offered or [], ensure_ascii=False).encode('utf-8')) + 256

        def request_size(offered):
            # Count each message once, including tool-call arguments and reasoning.
            return int(calibration * (sum(message_bytes(m) for m in loop_messages) + schema_bytes(offered)))

        def fit_history(offered):
            nonlocal history_count
            removed = False
            while history_count and request_size(offered) > self.cfg.provider['max_input_tokens']:
                end = history_start + 1
                while end < history_start + history_count and loop_messages[end]['role'] != 'user':
                    end += 1
                del loop_messages[history_start:end]
                history_count -= end - history_start
                removed = True
            if removed and not self.trim_noted:
                self.trim_noted = True
                kept = sum(m['role'] == 'user' for m in loop_messages[history_start:history_start + history_count])
                self.trim_note = (f'（为了不超出长度上限，这次只带了最近 {kept} 轮原文；'
                                  '更早的聊天仍保存在本机，长期记忆不受影响。）')

        for step in range(self.cfg.tools['max_steps']):
            offered = None if step == self.cfg.tools['max_steps'] - 1 else self.tool_schemas()
            step_cfg = replace(self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens)) if offered is not None else self.cfg
            fit_history(offered)
            if request_size(offered) > self.cfg.provider['max_input_tokens']:
                if step == 0:
                    self.budget.settle(request_id, step_cfg, 0, 0, 0)
                    raise TooLong('工具说明和本轮上下文超过长度上限，未调用工具；请缩短输入后再试。')
                if next_reservation:
                    self.budget.settle(next_reservation, step_cfg, 0, 0, 0)
                break
            reservation = request_id if step == 0 else next_reservation
            sent_bytes = sum(message_bytes(m) for m in loop_messages) + schema_bytes(offered)
            try:
                response = self.model(loop_messages, tools=offered, message_output=True,
                                      max_tokens=step_cfg.max_output_tokens, detail=True)
            except UserError:
                raise
            except Exception as exc:
                # Only the provider call is recoverable. Ledger, storage and tool
                # integrity failures below must still stop the session.
                raise RequestFailed('请求失败，未自动重试；输入已保存，本次保留预留费用。') from exc
            message, prompt, completion, cached, *finish = response
            if isinstance(prompt, int) and prompt > 0:
                calibration = max(0.5, min(1.0, prompt / sent_bytes))
            self.budget.settle(reservation, step_cfg, prompt, completion, cached)
            if finish and finish[0] == 'length' and message.tool_calls:
                final = '模型达到输出上限，工具未执行；请缩短要写入的内容后重试。'
                self.output(final)
                break
            if not message.tool_calls:
                content = message.content or ''
                if isinstance(content, str) and RAW_DSML_TOOL_CALL.search(content):
                    # Never display provider-internal tool syntax as a reply or feed
                    # it to another parser. Only structured, allowlisted tool_calls
                    # reach the existing permission and budget checks above.
                    final = '模型返回了未执行的工具调用文本；程序没有执行任何工具，请查看 /cost 后重试。'
                else:
                    final = content
                if step == self.cfg.tools['max_steps'] - 1 and summaries:
                    self.output('这次工具步骤用完了。')
                break
            if offered is None:
                final = '这次工具步骤用完了。'
                self.output(final)
                break
            # These calls finish in this response: no follow-up request or reservation.
            if all(call.function.name == 'send_message' for call in message.tool_calls):
                errors = []
                for call in message.tool_calls:
                    result, state, summary = self.send_message(call.function.arguments, request_id)
                    self.store.append(f'{self.cfg.id}/tools.jsonl', {'request_id': request_id,
                        'tool': 'send_message', 'path': summary, 'state': state})
                    if state != '成功':
                        errors.append(result)
                final = ('\n'.join(errors) if errors else
                         message.content or self.sent_messages[-1])
                break
            try:
                next_cfg = (self.cfg if step + 1 == self.cfg.tools['max_steps'] - 1 else
                            replace(self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens)))
                next_reservation = self.budget.reserve(next_cfg)[0]
            except BudgetExceeded:
                for call in message.tool_calls:
                    try:
                        args = json.loads(call.function.arguments)
                        summary = (self.tools.preview_summary(call.function.name, args)
                                   if self.tools and call.function.name != 'send_message' else call.function.name)
                    except (ValueError, TypeError):
                        summary = call.function.name
                    self.output(display_call(call.function.name, summary, '被拒绝', '继续处理会超过费用上限'))
                    self.store.append(f'{self.cfg.id}/tools.jsonl', {'request_id': request_id,
                        'tool': call.function.name, 'path': summary, 'state': '被拒绝'})
                self.output('这次工具步骤用完了。继续整理会超过当前费用上限。')
                final = '这次工具步骤用完了。'
                break
            try:
                assistant_record = message.model_dump(exclude_none=True)
                reasoning = getattr(message, 'reasoning_content', None)
                if reasoning is not None:
                    assistant_record['reasoning_content'] = reasoning
                loop_messages.append(assistant_record)
                next_offered = (None if step + 1 == self.cfg.tools['max_steps'] - 1
                                else self.tool_schemas())
                too_long_result = '工具结果超过本轮请求长度上限，未提供给模型。'
                placeholders = [{'role': 'tool', 'tool_call_id': call.id, 'content': too_long_result}
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


def run_chat(chat, input_fn=input, output=print, drop=drop_typeahead):
    output(f'接上了 {chat.rounds} 轮。输入 /cost 查花费，/self 看当前说明，/quit 退出。')
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
            if text == '/cost':
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
