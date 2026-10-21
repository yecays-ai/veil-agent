"""Local terminal chat. Private state stays beside this file."""
import argparse
import json
import math
import os
import re
import signal
import sys
import tomllib
from dataclasses import replace
import uuid
import threading
from functools import wraps
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from contextlib import contextmanager, nullcontext

ROOT = Path(__file__).resolve().parent
MILLION = Decimal(1_000_000)

RAW_DSML_TOOL_CALL = re.compile(r'<[|｜]DSML[|｜](?:tool_calls|function_calls|invoke)\b', re.IGNORECASE)
CJK_TEXT = re.compile(r'[\u3400-\u9fff]')
AUTONOMY_WAKE = object()
TOOL_DATA_RESULTS = {'read_text_file', 'read_multiple_files', 'directory_tree', 'list_directory',
                     'list_directory_with_sizes', 'get_file_info', 'search_files', 'memory_read',
                     'memory_history', 'breath_search', 'dream', 'pulse', 'letter_read',
                     'long_task_status'}


def capability_model_notice(tools):
    event = getattr(tools, 'capability_event_model', None) if tools else None
    return event if isinstance(event, str) else None

def model_tool_result(name, result, state):
    """Pass failures through unchanged; preserve the existing successful-data safeguard."""
    if not isinstance(result, str) or state != '成功':
        return result
    if CJK_TEXT.search(result) and name not in TOOL_DATA_RESULTS:
        return 'The tool operation completed successfully.'
    return result

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
    autonomy: dict = None


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
        autonomy = dict(cfg.get('autonomy', {}))
        interval = positive_int(autonomy.get('interval_minutes', 180))
        daily_runs = positive_int(autonomy.get('daily_max_runs', 6))
        autonomy_daily = price(autonomy.get('daily_budget_cny', '2'))
        autonomy = {'interval_minutes': interval, 'daily_max_runs': daily_runs,
                    'daily_budget_cny': autonomy_daily}
        memory = dict(cfg.get('memory') or {})
        if memory.get('enabled', False):
            memory_port = positive_int(memory.get('ombre_port'))
            if not 1024 <= memory_port <= 65535:
                raise ValueError('memory.ombre_port must be an available TCP port')
            memory['ombre_port'] = memory_port
        return Character(character_id, str(cfg['name']), persona,
                         positive_int(cfg.get('history_rounds', 20)),
                         positive_int(cfg.get('max_output_tokens', 1024)), provider, cfg['provider'],
                         memory, tools, autonomy)
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

    def snapshot(self):
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                return {'enabled': value.get('enabled') is True,
                        'enabled_at': value.get('enabled_at')}
            except FileNotFoundError:
                return {'enabled': False, 'enabled_at': None}
            except (ValueError, UnicodeError, AttributeError):
                return {'enabled': False, 'enabled_at': None}

    def set(self, enabled):
        with self.lock:
            previous = self.snapshot()
            enabled = bool(enabled)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + '.tmp')
            with tmp.open('w', encoding='utf-8', newline='\n') as handle:
                stamp = now() if enabled and not previous['enabled'] else previous['enabled_at']
                handle.write(json.dumps({'enabled': enabled, 'enabled_at': stamp}) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            return enabled


class Budget:
    def __init__(self, store, daily=Decimal('10'), monthly=Decimal('300'), clock=now,
                 autonomy_daily=Decimal('2')):
        self.mutex = threading.RLock()
        self.store, self.daily, self.monthly, self.clock = store, daily, monthly, clock
        self.autonomy_daily = Decimal(str(autonomy_daily))
        self.local = threading.local()

    @contextmanager
    def autonomous(self, character, daily=None):
        previous = getattr(self.local, 'autonomy', None)
        if daily is not None:
            self.autonomy_daily = Decimal(str(daily))
        self.local.autonomy = character
        try:
            yield
        finally:
            self.local.autonomy = previous

    @synchronized
    def category_totals(self, character=None, stamp=None):
        stamp = stamp or self.clock()
        reservations, settlements = {}, {}
        try:
            for event in self.store.read('costs.jsonl'):
                if event['type'] == 'reserve':
                    reservations[event['id']] = event
                elif event['type'] == 'settle':
                    settlements[event['id']] = event
            day = pending = Decimal(0)
            for key, event in reservations.items():
                if event.get('category') != 'autonomy':
                    continue
                active_character = event.get('character')
                if not isinstance(active_character, str) or not active_character:
                    raise ValueError('invalid autonomy character')
                if character is not None and active_character != character:
                    continue
                settled = settlements.get(key)
                amount = Decimal((settled or event)['amount'])
                if not amount.is_finite() or amount < 0:
                    raise ValueError('invalid amount')
                if settled is None:
                    pending += amount
                elif event['period'][:10] == stamp[:10]:
                    day += amount
            return day, pending
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise UserError('花费账本无法核对，已停止；请保留 data 文件夹。') from exc

    def _reserve_category(self, amount):
        character = getattr(self.local, 'autonomy', None)
        return ('autonomy' if character else 'chat', character)

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
    def reserve(self, cfg, *, accounting=None):
        p = cfg.provider
        amount = (p['max_input_tokens'] * p['input_cny_per_million']
                  + cfg.max_output_tokens * p['output_cny_per_million']) / MILLION
        day, month, pending = self.totals()
        category, character = self._reserve_category(amount)
        if category == 'autonomy':
            auto_day, auto_pending = self.category_totals(character)
            if auto_day + auto_pending + amount > self.autonomy_daily:
                raise BudgetExceeded(f'自主活动已达到 ¥{self.autonomy_daily}/日上限，聊天不受影响。')
        if day + pending + amount > self.daily or month + pending + amount > self.monthly:
            raise BudgetExceeded('本次费用上限会超过 ¥10/天或 ¥300/月，未发送。输入 /cost 查看。')
        request_id = uuid.uuid4().hex
        event = {'type': 'reserve', 'id': request_id,
                          'period': self.clock(), 'amount': str(amount),
                          'character': cfg.id, 'model': p['model'], 'category': category}
        if accounting:
            event['accounting'] = accounting
        self.store.append('costs.jsonl', event)
        return request_id, amount

    @synchronized
    def estimate(self, character, model, tokens, rate):
        rate = Decimal(str(rate))
        if type(tokens) is not int or tokens < 0 or not rate.is_finite() or rate < 0:
            raise UserError('向量费用配置无效。')
        amount = tokens * rate / MILLION
        day, month, pending = self.totals()
        category, active_character = self._reserve_category(amount)
        if category == 'autonomy':
            auto_day, auto_pending = self.category_totals(active_character)
            if auto_day + auto_pending + amount > self.autonomy_daily:
                raise UserError(f'自主活动已达到 ¥{self.autonomy_daily}/日上限。')
        if day + pending + amount > self.daily or month + pending + amount > self.monthly:
            raise UserError('向量请求超过共享预算，未发送。')
        request_id = uuid.uuid4().hex
        self.store.append('costs.jsonl', {'type': 'reserve', 'id': request_id,
            'period': self.clock(), 'amount': str(amount), 'character': character,
            'model': model, 'accounting': 'embedding-estimate', 'estimated_tokens': tokens,
            'category': category})
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
        category, active_character = self._reserve_category(amount)
        if category == 'autonomy':
            auto_day, auto_pending = self.category_totals(active_character)
            if auto_day + auto_pending + amount > self.autonomy_daily:
                raise UserError(f'自主活动已达到 ¥{self.autonomy_daily}/日上限。')
        if day + pending + amount > self.daily or month + pending + amount > self.monthly:
            raise UserError('记忆模型请求超过共享预算，未发送。')
        request_id = uuid.uuid4().hex
        self.store.append('costs.jsonl', {'type': 'reserve', 'id': request_id,
            'period': self.clock(), 'amount': str(amount), 'character': character,
            'model': model, 'accounting': 'ombre-model-estimate',
            'estimated_tokens': tokens_in + tokens_out, 'category': category})
        return request_id, amount

    @synchronized
    def settle_estimate(self, reservation, accounting='embedding-estimate', *, settled_amount=None):
        request_id, reserved_amount = reservation
        rows = self.store.read('costs.jsonl')
        reserve = next((row for row in rows if row.get('type') == 'reserve'
                        and row.get('id') == request_id), None)
        if reserve is None or Decimal(reserve['amount']) != Decimal(reserved_amount):
            raise UserError('预算预留无法核对，已停止；请保留 data 文件夹。')
        if any(row.get('type') == 'settle' and row.get('id') == request_id for row in rows):
            return False
        settled = Decimal(reserved_amount if settled_amount is None else settled_amount)
        if (not settled.is_finite() or settled < 0 or settled > Decimal(reserved_amount)):
            raise UserError('预算结算金额超过预留或无效，已停止；请保留 data 文件夹。')
        self.store.append('costs.jsonl', {'type': 'settle', 'id': request_id,
            'amount': str(settled), 'reserved_amount': str(reserved_amount),
            'released_amount': str(Decimal(reserved_amount) - settled),
            'accounting': accounting})
        return True

    @synchronized
    def release_estimate(self, reservation, accounting='preflight-cancelled'):
        """Release only when the external call was never entered."""
        request_id, amount = reservation
        rows = self.store.read('costs.jsonl')
        reserve = next((row for row in rows if row.get('type') == 'reserve'
                        and row.get('id') == request_id), None)
        if reserve is None or Decimal(reserve['amount']) != Decimal(amount):
            raise UserError('预算预留无法核对，已停止；请保留 data 文件夹。')
        if any(row.get('type') == 'settle' and row.get('id') == request_id for row in rows):
            return False
        self.store.append('costs.jsonl', {'type': 'settle', 'id': request_id,
            'amount': '0', 'accounting': accounting})
        return True

    @synchronized
    def reconcile_unsettled(self, accountings):
        """Convert interrupted memory reservations to their full conservative charge.

        Unknown outcomes are never released: after restart the reserved upper
        estimate becomes settled spend, so it cannot permanently occupy pending.
        """
        self.totals()  # Validate the full ledger before recovering any entry.
        rows = self.store.read('costs.jsonl')
        settled = {row.get('id') for row in rows if row.get('type') == 'settle'}
        for row in rows:
            if (row.get('type') == 'reserve' and row.get('accounting') in accountings
                    and row.get('id') not in settled):
                self.settle_estimate((row['id'], Decimal(row['amount'])),
                                     accounting=f"{row['accounting']}-recovered")
        return len([row for row in rows if row.get('type') == 'reserve'
                    and row.get('accounting') in accountings and row.get('id') not in settled])

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
        rows = self.store.read('costs.jsonl')
        if any(row.get('type') == 'settle' and row.get('id') == request_id for row in rows):
            return False
        self.store.append('costs.jsonl', {'type': 'settle', 'id': request_id,
                          'amount': str(amount), 'prompt_tokens': prompt,
                          'completion_tokens': completion, 'cached_tokens': cached})
        return True

    @synchronized
    def cache_display(self, stamp=None):
        """Today's share of chat-model input tokens the provider served from its cache."""
        stamp = stamp or self.clock()
        try:
            periods, prompt, cached = {}, 0, 0
            for event in self.store.read('costs.jsonl'):
                if event['type'] == 'reserve':
                    periods[event['id']] = (event['period'], event.get('category', 'chat'))
                elif event['type'] == 'settle' and 'prompt_tokens' in event:
                    period, category = periods.get(event['id'], ('', 'chat'))
                    if category != 'autonomy' and period[:10] == stamp[:10]:
                        prompt += event['prompt_tokens']
                        cached += event['cached_tokens']
        except (KeyError, TypeError) as exc:
            raise UserError('花费账本无法核对，已停止；请保留 data 文件夹。') from exc
        if not prompt:
            return '今天还没有聊天模型的用量记录，缓存命中率暂无。'
        return (f'今天聊天缓存命中 {cached * 100 / prompt:.0f}%（命中 {cached} / 输入 {prompt} tokens；'
                '只统计聊天模型，不含记忆打标和向量）')

    def display(self, stamp=None, character=None):
        day, month, pending = self.totals(stamp)
        all_autonomy, _ = self.category_totals(None, stamp)
        autonomy = all_autonomy if character is None else self.category_totals(character, stamp)[0]
        chat = day - all_autonomy
        return (f'今天 ¥{day:.6f} / {self.daily}；本月 ¥{month:.6f} / {self.monthly}；'
                f'待核对预留 ¥{pending:.6f}；其中聊天 ¥{chat:.6f}，自主 ¥{autonomy:.6f} / '
                f'{self.autonomy_daily}/日（自主是上限，不是消费目标）')


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
        f'Available: text chat (web page or terminal); up to {cfg.history_rounds} recent rounds included verbatim '
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
                f'Pre-request ledger (CNY, excludes this reply): {budget.display(stamp, cfg.id)}\n'
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



def message_request_bytes(message):
    return len(json.dumps(message, ensure_ascii=False).encode('utf-8')) + 256


def request_byte_size(messages, tools=None):
    """Approximate the bytes the provider tokenizes, including per-item framing."""
    message_bytes = sum(message_request_bytes(message) for message in messages)
    schema_bytes = len(json.dumps(tools or [], ensure_ascii=False).encode('utf-8')) + 256
    return message_bytes + schema_bytes


def estimate_request_tokens(messages, tools=None, calibration=0.5):
    """Conservative preflight estimate; tool-loop usage calibrates it after a real request."""
    ratio = max(0.5, min(1.0, calibration))
    return math.ceil(request_byte_size(messages, tools) * ratio)


class Chat:
    def __init__(self, cfg, store, budget, model, memory=None, tools=None, approval=input, output=print):
        self.cfg, self.store, self.budget, self.model = cfg, store, budget, model
        self.path = f'{cfg.id}/chat.jsonl'
        self.session_path = store.root / cfg.id / 'chat-session.json'
        self.reply_language = 'auto'
        self.session_start = 0
        self.history, self.rounds = self.recent()
        self.last_self = None
        self.trim_note = None
        self.trim_noted = False
        self.memory = memory
        self.tools, self.approval, self.output = tools, approval, output
        self.pending_capability_event = tools.capability_event if tools else None
        self.pending_capability_event_model = capability_model_notice(tools)
        from long_tasks import LongTaskStore
        self.long_tasks = LongTaskStore(store, cfg.id)
        self.task_tools_enabled = bool(cfg.tools and cfg.tools.get('max_steps', 1) > 1)
        self.tool_mode = bool((cfg.tools and cfg.tools.get('enabled', True)) or (tools and tools.tools)
                              or self.task_tools_enabled)
        if self.task_tools_enabled and tools and hasattr(tools, 'announce_long_tasks'):
            tools.announce_long_tasks()
            self.pending_capability_event = tools.capability_event
            self.pending_capability_event_model = capability_model_notice(tools)
        self.sent_messages = []
        self.reply_displayed = False
        self.autonomy_mode = False
        self.autonomy_should_yield = None
        self.autonomy_run_id = None
        self.autonomy_message_output = None
        self.autonomy_message_lock = None
        self.autonomy_step_limited = False
        self.autonomy_budget_limited = False
        self.tool_stop_reason = ''
        self.last_tool_summaries = []
        self.autonomy_interrupted = False

    def tool_schemas(self):
        from tools_agent import permitted_tools
        task_tools = [{'type': 'function', 'function': {
            'name': tool.name, 'description': tool.description, 'parameters': tool.inputSchema}}
            for tool in self.long_tasks.tools] if self.task_tools_enabled else []
        existing_tools = permitted_tools(self.tools.tools if self.tools else [])
        messaging = [SEND_MESSAGE] if ((self.cfg.tools and self.cfg.tools.get('enabled', True))
                                        or (self.tools and self.tools.tools)) else []
        schemas = [*existing_tools, *task_tools, *messaging]
        for schema in schemas:
            function = schema['function']
            if function['name'] in ('write_file', 'append_file', 'long_task_update', 'hold', 'trace', 'letter_write'):
                function = dict(function)
                function['description'] += (
                    f' Output budget: {(self.cfg.tools or {}).get("max_output_tokens", self.cfg.max_output_tokens)} tokens. '
                    'Keep new text under 1500 characters per call; persist one verified slice at a time. '
                    'Do not reproduce whole files or inventories in arguments.')
                schema['function'] = function
        return schemas

    def send_message(self, arguments, request_id, tool_call_id=None):
        try:
            args = json.loads(arguments)
        except (ValueError, TypeError):
            args = None
        if (not isinstance(args, dict) or set(args) != {'text'} or
                not isinstance(args['text'], str) or not args['text'].strip()):
            return 'send_message requires non-empty text.', '被拒绝', 'send_message'
        text = args['text']
        autonomy = self.autonomy_mode
        gate = self.autonomy_message_lock if autonomy else None
        with gate if gate is not None else nullcontext():
            if autonomy and self.autonomy_should_yield and self.autonomy_should_yield():
                self.autonomy_interrupted = True
                return 'Autonomous activity yielded before sending this message; nothing was sent.', '被拒绝', 'send_message'
            rows = self.store.read(self.path) if autonomy else []
            run_id = self.autonomy_run_id if autonomy else None
            if autonomy and (not isinstance(run_id, str) or not run_id or
                             not isinstance(tool_call_id, str) or not tool_call_id):
                return 'The current autonomous wake or tool-call identifier is missing; nothing was sent.', '被拒绝', 'send_message'
            if autonomy:
                existing = next((row for row in rows
                                 if row.get('autonomy_run_id') == run_id
                                 and row.get('autonomy_tool_call_id') == tool_call_id), None)
                if existing:
                    self.history, self.rounds = self.recent()
                    return 'This message was already saved and will not be sent again.', '已存在', 'send_message'
                sent_this_wake = sum(1 for row in rows if row.get('autonomy_run_id') == run_id)
                if sent_this_wake >= 3:
                    return 'An autonomous wake may send at most three messages. This message was not sent.', '被拒绝', 'send_message'
            event = {'role': 'assistant', 'content': text,
                     'request_id': request_id, 'origin': 'reply'}
            if autonomy:
                event.update({'autonomy_run_id': run_id,
                              'autonomy_tool_call_id': tool_call_id})
            with saving():
                self.store.append(self.path, event)
                self.sent_messages.append(text)
                if autonomy:
                    self.history, self.rounds = self.recent()
                output = self.autonomy_message_output if autonomy else None
                (output or self.output)(f'{self.cfg.name}: {text}')
        return 'Sent.', '成功', 'send_message'

    def start_new_conversation(self, language='auto'):
        if language not in ('auto', 'en'):
            raise UserError('不支持的对话语言设置。')
        # Cursor only: old messages stay on disk.
        start = len(self.store.read(self.path))
        path = self.session_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        try:
            with tmp.open('w', encoding='utf-8', newline='\n') as handle:
                json.dump({'start': start, 'language': language}, handle)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        self.history, self.rounds = self.recent()
        self.last_self = None
        self.trim_note = None
        self.trim_noted = False


    def recent(self):
        groups = []
        events = self.store.read(self.path)
        try:
            try:
                state = json.loads(self.session_path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                state = {'start': 0, 'language': 'auto'}
            start, language = state['start'], state['language']
            if type(start) is not int or not 0 <= start <= len(events) or language not in ('auto', 'en'):
                raise ValueError('invalid session cursor')
            self.session_start, self.reply_language = start, language
        except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
            raise UserError('新对话状态无法读取，已停止；请保留 data 文件夹。') from exc
        try:
            for event in events[start:]:
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

    def send(self, text, *, autonomy=False, extra_context=None):
        self.sent_messages = []
        self.reply_displayed = False
        self.autonomy_mode = autonomy
        if self.tools and hasattr(self.tools, 'set_autonomy'):
            self.tools.set_autonomy(autonomy)
        self.autonomy_interrupted = False
        self.autonomy_step_limited = False
        self.autonomy_budget_limited = False
        self.tool_stop_reason = ''
        self.last_tool_summaries = []
        self.long_tasks.begin_tool_run()
        if not self.pending_capability_event and self.tools:
            self.pending_capability_event = self.tools.capability_event
            self.pending_capability_event_model = capability_model_notice(self.tools)
        recalled = self.memory.context(text) if self.memory else []
        autonomy_handoff_id = None
        if not autonomy:
            from autonomy import recent_activity_context, recent_chat_handoff
            autonomy_handoff_id, _ = recent_chat_handoff(self.store, self.cfg)
            if extra_context is None:
                _, extra_context = recent_activity_context(self.store, self.cfg)
        stable, changing = self_facts(self.cfg, self.budget, memory_mode(self.memory), self.tools)
        description = stable + '\n' + changing
        # Order matters for the provider's prefix cache: everything that stays the same between requests
        # comes first, then the chat history, then what changes every request (recalled memories,
        # capability notice, time and ledger) right before the user's message.
        head = [{'role': 'system', 'content': self.cfg.persona},
                {'role': 'system', 'content': stable}]
        tail = [*recalled]
        if extra_context:
            tail.append({'role': 'system', 'content': extra_context})
        if isinstance(self.pending_capability_event_model, str) and self.pending_capability_event_model and not autonomy:
            tail.append({'role': 'system', 'content':
                'Program-generated capability update: ' + self.pending_capability_event_model})
        tail.append({'role': 'system', 'content': changing})
        user = {'role': 'user', 'content': text}
        tool_schema = []
        tool_config = self.cfg.tools or {}
        if self.tool_mode and tool_config.get('max_steps', 1) > 1:
            tool_schema = self.tool_schemas()
        # The input limit is in tokens, not UTF-8 bytes. Start with the same
        # conservative byte-to-token floor used by the tool loop; schema bytes
        # are included but must not be counted one-for-one as tokens.
        def bound(history):
            return estimate_request_tokens([*head, *history, *tail, user], tool_schema)
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
            tool_cfg = (replace(self.cfg, max_output_tokens=(self.cfg.tools or {}).get(
                            'max_output_tokens', self.cfg.max_output_tokens))
                        if self.tool_mode else self.cfg)
            request_id, _ = self.budget.reserve(tool_cfg)
            if not autonomy:
                self.store.append(self.path, {'role': 'user', 'content': text, 'request_id': request_id})
            if self.pending_capability_event and not autonomy:
                self.store.append(f'{self.cfg.id}/tools.jsonl', {'request_id': request_id,
                    'kind': 'capability', 'text': self.pending_capability_event})
                self.tools.consume_capability_event()
                self.pending_capability_event = None
                self.pending_capability_event_model = None
        try:
            self.last_self = description
            if self.tool_mode:
                reply, tool_summaries = self._tool_chat(messages, request_id, len(history), len(head),
                                                        autonomy=autonomy)
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
        if autonomy and self.autonomy_should_yield and self.autonomy_should_yield():
            self.autonomy_interrupted = True
        with saving():
            if not isinstance(reply, str) or not reply.strip():
                raise UserError('接口没有返回文字回复；本次保留预留费用。')
            self.last_tool_summaries = tool_summaries
            self.reply_displayed = reply in self.sent_messages
            if not autonomy and not self.reply_displayed:
                self.store.append(self.path, {'role': 'assistant', 'content': reply,
                    'request_id': request_id, 'origin': 'reply'})
            if not self.tool_mode:
                self.budget.settle(request_id, self.cfg, prompt, completion, cached)
        if not autonomy:
            self.history, self.rounds = self.recent()
            if autonomy_handoff_id is not None:
                from autonomy import acknowledge_chat_handoff
                acknowledge_chat_handoff(self.store, self.cfg, autonomy_handoff_id)
        if self.memory:
            self.memory.completed()
        self.autonomy_mode = False
        return reply

    def _tool_chat(self, messages, request_id, history_count, history_start=None, autonomy=False):
        from tools_agent import display_call
        summaries = []
        self.last_tool_summaries = summaries
        loop_messages = list(messages)
        if history_start is None:
            history_start = len(messages) - history_count - 1
        final = ''
        next_reservation = None
        autonomy_tool_steps = 0
        output_recovered = False


        def log_tool(name, path, state, **metadata):
            row = {'request_id': request_id, 'tool': name, 'path': path, 'state': state}
            row.update(metadata)
            if autonomy:
                row['autonomy_run_id'] = self.autonomy_run_id
            self.store.append(f'{self.cfg.id}/tools.jsonl', row)

        # Start at the same conservative floor used by Chat.send. After the first
        # response, provider-reported usage calibrates subsequent tool-loop estimates.
        calibration = 0.5
        measured_tokens = None
        measured_bytes = 0

        def request_bytes(offered):
            # Count each message once, including tool-call arguments and reasoning.
            return request_byte_size(loop_messages, offered)

        def request_size(offered):
            size = request_bytes(offered)
            if measured_tokens is None:
                return math.ceil(0.5 * size)
            # Preserve the measured baseline; only new bytes need the initial
            # conservative estimate. Removed history uses the measured ratio.
            delta = size - measured_bytes
            rate = 0.5 if delta >= 0 else min(0.5, calibration)
            return math.ceil(max(0, measured_tokens + delta * rate) * 1.05)

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

        configured_steps = (self.cfg.tools or {}).get('max_steps', 1)
        max_steps = min(configured_steps, 12) if autonomy else configured_steps
        for step in range(max_steps):
            self.long_tasks.set_tool_step(step)
            if autonomy and autonomy_tool_steps >= 12:
                self.autonomy_step_limited = True
                final = 'Autonomous activity reached the 12-tool-step limit for this run.'
                if next_reservation:
                    pending_cfg = self.cfg if step == max_steps - 1 else replace(
                        self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens))
                    self.budget.settle(next_reservation, pending_cfg, 0, 0, 0)
                break
            if autonomy and self.autonomy_should_yield and self.autonomy_should_yield():
                self.autonomy_interrupted = True
                final = 'Autonomous activity stopped before this tool step.'
                pending_reservation = request_id if step == 0 else next_reservation
                if pending_reservation:
                    pending_cfg = self.cfg if step == max_steps - 1 else replace(
                        self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens))
                    self.budget.settle(pending_reservation, pending_cfg, 0, 0, 0)
                break
            offered = None if step == max_steps - 1 else self.tool_schemas()
            step_cfg = replace(self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens)) if offered is not None else self.cfg
            fit_history(offered)
            if request_size(offered) > self.cfg.provider['max_input_tokens']:
                if step == 0:
                    self.budget.settle(request_id, step_cfg, 0, 0, 0)
                    raise TooLong('工具说明和本轮上下文超过长度上限，未调用工具；请缩短输入后再试。')
                if next_reservation:
                    self.budget.settle(next_reservation, step_cfg, 0, 0, 0)
                self.tool_stop_reason = '本轮上下文达到长度上限，已停止后续步骤。'
                final = self.tool_stop_reason
                break
            reservation = request_id if step == 0 else next_reservation
            sent_bytes = request_bytes(offered)
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
                calibration = min(1.0, prompt / sent_bytes)
                measured_tokens, measured_bytes = prompt, sent_bytes
            self.budget.settle(reservation, step_cfg, prompt, completion, cached)
            if autonomy and self.autonomy_should_yield and self.autonomy_should_yield():
                self.autonomy_interrupted = True
                final = 'Autonomous activity yielded after the model request to let chat or a control action proceed.'
                break
            if finish and finish[0] == 'length':
                self.tool_stop_reason = '模型回复达到输出上限，结果不完整。'
            if finish and finish[0] == 'length' and message.tool_calls:
                final = 'The model reached its output limit, so this step did not run; previously confirmed operations were preserved.'
                if not output_recovered and step + 1 < max_steps:
                    try:
                        next_cfg = (self.cfg if step + 1 == max_steps - 1 else replace(
                            self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens)))
                        next_reservation = self.budget.reserve(next_cfg)[0]
                    except BudgetExceeded:
                        self.autonomy_budget_limited = autonomy
                    else:
                        output_recovered = True
                        self.tool_stop_reason = ''
                        final = ''
                        # Never replay or submit the truncated assistant/tool-call JSON.
                        # Earlier complete assistant/tool pairs remain in loop_messages.
                        loop_messages.append({'role': 'system', 'content': (
                            'Your last response hit the output token limit. NONE of the tool calls '
                            'in that response ran, even if their arguments looked complete. Earlier '
                            'confirmed tools remain executed: do not repeat them. This is your only '
                            'recovery attempt. Choose one small next action; keep new written text '
                            'under 1000 characters. Do not regenerate a whole file or inventory. '
                            'If tools are unavailable, explain the confirmed progress and next step.'
                        )})
                        self.store.append(f'{self.cfg.id}/memory/_agent/diagnostics.jsonl', {
                            'code': 'tool_output_recovery', 'attempt': 1,
                            'prompt_tokens': prompt, 'completion_tokens': completion,
                            'tool_count': len(message.tool_calls)})
                        self.output('This step was too long, so no tool ran; the request is being shortened for one retry.')
                        continue
                self.tool_stop_reason = final
                self.output(final)
                break
            if not message.tool_calls:
                content = message.content or ''
                if isinstance(content, str) and RAW_DSML_TOOL_CALL.search(content):
                    # Never display provider-internal tool syntax as a reply or feed
                    # it to another parser. Only structured, allowlisted tool_calls
                    # reach the existing permission and budget checks above.
                    final = 'The model returned tool-call text that was not executed; no tools ran. Check /cost before retrying.'
                    self.tool_stop_reason = final
                else:
                    final = content
                if step == max_steps - 1 and summaries:
                    self.output('The tool-step limit for this request has been reached.')
                break
            if offered is None:
                final = 'The tool-step limit for this request has been reached.'
                self.tool_stop_reason = final
                self.output(final)
                break
            # These calls finish in this response: no follow-up request or reservation.
            # In autonomy the loop continues so Peter can still write the four-line record
            # (decision, reason, focus, next step) that the next wake is built from.
            if not autonomy and all(call.function.name == 'send_message' for call in message.tool_calls):
                errors = []
                for call_index, call in enumerate(message.tool_calls):
                    if autonomy and autonomy_tool_steps >= 12:
                        self.autonomy_step_limited = True
                        final = 'Autonomous activity reached the 12-tool-step limit for this run.'
                        break
                    if autonomy and self.autonomy_interrupted:
                        final = 'Autonomous activity yielded to let chat or a control action proceed.'
                        break
                    if autonomy:
                        autonomy_tool_steps += 1
                    result, state, summary = self.send_message(
                        call.function.arguments, request_id,
                        f'{step}:{getattr(call, "id", None) or call_index}')
                    log_tool('send_message', summary, state)
                    if state != '成功':
                        errors.append(result)
                if not final:
                    final = ('\n'.join(errors) if errors else
                             message.content or (self.sent_messages[-1] if self.sent_messages else '已完成。'))
                break
            try:
                next_cfg = (self.cfg if step + 1 == max_steps - 1 else
                            replace(self.cfg, max_output_tokens=self.cfg.tools.get('max_output_tokens', self.cfg.max_output_tokens)))
                next_reservation = self.budget.reserve(next_cfg)[0]
            except BudgetExceeded:
                if autonomy:
                    self.autonomy_budget_limited = True
                for call in message.tool_calls:
                    try:
                        args = json.loads(call.function.arguments)
                        summary = (self.tools.preview_summary(call.function.name, args)
                                   if self.tools and call.function.name != 'send_message' else call.function.name)
                    except (ValueError, TypeError):
                        summary = call.function.name
                    self.output(display_call(call.function.name, summary, '被拒绝', 'Continuing would exceed the spending limit.'))
                    log_tool(call.function.name, summary, '被拒绝')
                self.output('The tool-step limit for this request has been reached; continuing would exceed the current spending limit.')
                final = 'The tool-step limit for this request has been reached.'
                break
            try:
                assistant_record = message.model_dump(exclude_none=True)
                reasoning = getattr(message, 'reasoning_content', None)
                if reasoning is not None:
                    assistant_record['reasoning_content'] = reasoning
                loop_messages.append(assistant_record)
                next_offered = (None if step + 1 == max_steps - 1
                                else self.tool_schemas())
                too_long_result = 'Tool result exceeded this request input limit and was omitted.'
                too_long_display = 'The tool result exceeded this request limit and was omitted from the model context.'
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
                        self.output(display_call(call.function.name, summary, '被拒绝', 'The tool call history exceeded its size limit.'))
                        summaries.append({'tool': call.function.name, 'path': summary, 'state': '被拒绝'})
                        log_tool(call.function.name, summary, '被拒绝')
                    self.budget.settle(next_reservation, next_cfg, 0, 0, 0)
                    final = 'The tool call history exceeded its size limit; the tool was not executed.'
                    self.tool_stop_reason = final
                    break
                for call_index, call in enumerate(message.tool_calls):
                    if autonomy and autonomy_tool_steps >= 12:
                        self.autonomy_step_limited = True
                        final = 'Autonomous activity reached the 12-tool-step limit for this run.'
                        self.budget.settle(next_reservation, next_cfg, 0, 0, 0)
                        break
                    if autonomy:
                        autonomy_tool_steps += 1
                    try:
                        if call.function.name == 'send_message':
                            result, state, summary = self.send_message(
                                call.function.arguments, request_id,
                                f'{step}:{getattr(call, "id", None) or call_index}')
                        else:
                            args = json.loads(call.function.arguments)
                            if not isinstance(args, dict):
                                raise ValueError('arguments must be an object')
                            if self.long_tasks.handles(call.function.name):
                                result, state, summary = self.long_tasks.invoke(call.function.name, args,
                                                                                self.approval)
                            elif self.tools:
                                summary = (self.tools.preview_summary(call.function.name, args)
                                           if hasattr(self.tools, 'preview_summary') else call.function.name)
                                checkpoint = None
                                blocked = None
                                if self.long_tasks.is_side_effect(call.function.name):
                                    checkpoint_target = self.long_tasks.checkpoint_target(
                                        call.function.name, args, summary)
                                    blocked, checkpoint_started = self.long_tasks.before_tool(
                                        call.function.name, checkpoint_target, autonomy, args)
                                    if checkpoint_started:
                                        checkpoint = checkpoint_target
                                if blocked:
                                    result, state = blocked, '被拒绝'
                                else:
                                    result, state, summary = self.tools.invoke(call.function.name, args,
                                                                               self.approval)
                                    if checkpoint is not None:
                                        self.long_tasks.finish_tool(call.function.name, summary, state)
                                self.long_tasks.observe_tool_result(call.function.name, summary, state, result)
                            else:
                                result, state, summary = 'The program rejected this call because the tool is unavailable.', '被拒绝', call.function.name
                    except (ValueError, TypeError):
                        result, state, summary = 'The program rejected this call because the arguments are not a valid JSON object.', '被拒绝', call.function.name
                    display_detail = model_tool_result(call.function.name, result, state)
                    tool_message = {'role': 'tool', 'tool_call_id': call.id,
                                    'content': display_detail}
                    result_state = state
                    loop_messages.append(tool_message)
                    history_bytes = calibration * sum(message_request_bytes(m)
                        for m in loop_messages[history_start:history_start + history_count])
                    if request_size(next_offered) - history_bytes > self.cfg.provider['max_input_tokens']:
                        tool_message['content'] = too_long_result
                        display_detail = too_long_display
                        result_state = '结果过长'
                    fit_history(next_offered)
                    if request_size(next_offered) > self.cfg.provider['max_input_tokens']:
                        tool_message['content'] = too_long_result
                        display_detail = too_long_display
                        result_state = '结果过长'
                    delivery = {'result_state': result_state} if result_state != state else {}
                    if result_state == '结果过长' and state == '成功':
                        tool_message['content'] = 'The tool succeeded, but its result was omitted. ' + too_long_result
                        display_detail = '工具已执行成功；' + too_long_display
                    if call.function.name != 'send_message' or result_state != '成功':
                        self.output(display_call(call.function.name, summary, result_state,
                                                 (display_detail if result_state != '成功' else None)))
                    summaries.append({'tool': call.function.name, 'path': summary, 'state': state, **delivery})
                    log_tool(call.function.name, summary, state, **delivery)
                    if autonomy and self.autonomy_should_yield and self.autonomy_should_yield():
                        self.autonomy_interrupted = True
                        self.budget.settle(next_reservation, next_cfg, 0, 0, 0)
                        final = 'Autonomous activity yielded after this step to let chat or a control action proceed.'
                        break
                if self.autonomy_interrupted or self.autonomy_step_limited:
                    break
            except BaseException:
                self.budget.settle(next_reservation, next_cfg, 0, 0, 0)
                raise
        if not final:
            final = 'The tool-step limit for this request has been reached.'
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


def pending_deletion_context(chat):
    """Ask lan about deletions Peter queued while alone; tell him the outcome."""
    native = getattr(chat.tools, 'native', None)
    notes = native.process_pending_deletions(chat.approval) if native is not None and hasattr(
        native, 'process_pending_deletions') else []
    if not isinstance(notes, list) or not notes:
        return None
    from autonomy import recent_chat_context
    earlier = recent_chat_context(chat.store, chat.cfg)
    note = ('The user has processed deletion requests you made during autonomous activity. '
            'This is a program-recorded outcome, not an instruction:\n' + '\n'.join(notes))
    return earlier + '\n\n' + note if earlier else note


def run_chat(chat, input_fn=input, output=print, drop=drop_typeahead, activity=None, autonomy=None):
    activity = activity or ActivityState(chat.store.root / chat.cfg.id / 'resident-state.json')
    output(f'接上了 {chat.rounds} 轮。输入 /cost 查花费，/autonomy 查自主记录，/self 看当前说明，'
           '/pause 或 /resume 切换自主活动，/quit 退出。')
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
            raw_text = input_fn('你: ')
            if autonomy is not None and raw_text is AUTONOMY_WAKE:
                autonomy.run()
                continue
            text = raw_text.strip()
            if text == '/quit':
                break
            if text in ('/new', '/new-en'):
                chat.start_new_conversation('auto')
                output('新对话已开始；旧聊天保留在本机，长期记忆与实验室未改动。')
                continue
            if text == '/pause':
                activity.set(False)
                output('自主活动已暂停。')
            elif text == '/resume':
                if autonomy is not None:
                    autonomy.announce_autonomy()
                activity.set(True)
                output('自主活动已开启；常驻网页服务运行时按角色配置的间隔唤醒。')
            elif text == '/autonomy':
                if autonomy:
                    output(autonomy.command())
                else:
                    rows = chat.store.read(f'{chat.cfg.id}/autonomy.jsonl')[-10:]
                    output('\n'.join(f"{row.get('started_at')} | {row.get('decision') or '未留下决定'} | "
                                     f"{row.get('result')}：{row.get('result_reason')} | ¥{row.get('cost_cny')}"
                                     for row in reversed(rows)) or '还没有自主活动记录。')
            elif text == '/cost':
                output(chat.budget.display(character=chat.cfg.id))
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
                    reply = chat.send(text, extra_context=pending_deletion_context(chat))
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
            cfg = load_character(ROOT, args.character)
            budget.autonomy_daily = cfg.autonomy['daily_budget_cny']
            if args.cost:
                print(budget.display(character=cfg.id))
                print(budget.cache_display())
                return 0
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
