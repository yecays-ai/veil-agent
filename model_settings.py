"""Ticket 134: Lan's own model list, kept on her computer.

Endpoints (name, address, key) and models (name, price, thinking) live in
data/<character>/models.json; keys go only into the project .env. Roles pick models
for chat, autonomous activity, glance, and background work. The providers written in
character.toml stay available as built-in models and are the defaults.
"""
import json
import os
import re
import secrets
import base64
import shutil
import stat
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

BUILTIN_PREFIX = 'builtin:'
ROLES = ('peter', 'autonomy', 'background', 'glance')
FOLLOW_CHAT = 'follow-chat'  # glance only: use whatever Peter's chat model is
GLANCE_DEFAULT_MODEL = 'grok-4.6'  # Ticket 211: preferred glance model when Lan has configured it
DEFAULT_DAILY = Decimal('10')
DEFAULT_MONTHLY = Decimal('300')
DEFAULT_MAX_INPUT = 32768
MAX_ENDPOINTS = 20
MAX_MODELS = 60
ID_PATTERN = re.compile(r'[em][0-9a-f]{6}')
_ENV_LOCK = threading.RLock()


class SettingsError(ValueError):
    """A change Lan made on the settings page that cannot be saved; the text is shown to her."""


def _decimal(value, label, *, allow_zero=False, maximum=None):
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise SettingsError(f'{label}要填数字。') from None
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise SettingsError(f'{label}要大于 0。')
    if maximum is not None and result > maximum:
        raise SettingsError(f'{label}不能超过 {maximum}。')
    return result


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f'{label}不能为空。')
    value = value.strip()
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise SettingsError(f'{label}太长或含有特殊字符。')
    return value


def _base_url(value):
    value = _text(value, '接口地址', 300).rstrip('/')
    if not re.fullmatch(r'https://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?', value):
        raise SettingsError('接口地址要以 https:// 开头，例如 https://api.example.com/v1')
    return value


def _key(value):
    value = value.strip()
    if not value or len(value) > 500 or any(ch.isspace() or ord(ch) < 33 for ch in value) or "'" in value:
        raise SettingsError('API Key 看起来不对：不能有空格或引号。')
    return value


class ModelSettings:
    def __init__(self, root, character_id, builtins, default_name):
        self.root = Path(root)
        self.env_path = self.root / '.env'
        self.path = self.root / 'data' / character_id / 'models.json'
        self.journal = self.root / f'.env.models-{character_id}.pending'
        self.env_stage = self.root / f'.env.models-{character_id}.stage'
        self.builtins = dict(builtins)
        self.default = BUILTIN_PREFIX + default_name
        self.lock = threading.RLock()
        self.env_lock = _ENV_LOCK
        self.blocked = False
        with _ENV_LOCK:
            self._recover()
        self.data = self._clean(self._read())

    # ---- reading -------------------------------------------------------
    @staticmethod
    def _sync_directory(path):
        if os.name == 'nt':
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _atomic_bytes(path, content, mode=0o600):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        try:
            descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(descriptor, 'wb') as stream:
                os.chmod(tmp, mode)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            ModelSettings._sync_directory(path.parent)
        finally:
            tmp.unlink(missing_ok=True)

    def _recover(self):
        if not self.journal.exists():
            self.blocked = False
            try:
                self.env_stage.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            record = json.loads(self.journal.read_text(encoding='utf-8'))
            if record['state'] == 'prepared':
                for path, key in ((self.env_path, 'env'), (self.path, 'models')):
                    original = record[key]
                    if original is None:
                        path.unlink(missing_ok=True)
                        if path.parent.exists():
                            self._sync_directory(path.parent)
                    else:
                        mode = record.get('env_mode', 0o600) if key == 'env' else 0o600
                        self._atomic_bytes(path, base64.b64decode(original, validate=True), mode)
            elif record['state'] != 'committed':
                raise ValueError('bad transaction state')
            self.journal.unlink()
            self._sync_directory(self.journal.parent)
            self.env_stage.unlink(missing_ok=True)
            self.blocked = False
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.blocked = True
            raise SettingsError('模型设置上次保存未完成且无法自动恢复；已停止模型请求，请检查磁盘权限并保留配置文件。') from None

    @staticmethod
    def _snapshot(path):
        try:
            return base64.b64encode(path.read_bytes()).decode('ascii')
        except FileNotFoundError:
            return None

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeError):
            raise SettingsError('模型配置 models.json 无法读取或解析；原文件已保留，请修复后重启。') from None
        if not isinstance(value, dict):
            raise SettingsError('模型配置 models.json 顶层必须是对象；原文件已保留。')
        return value

    def _clean(self, raw):
        """Keep only entries that would pass the settings page's own checks."""
        data = {'endpoints': [], 'models': [], 'use': {}, 'limits': {}}
        for field, kind in (('endpoints', list), ('models', list), ('use', dict), ('limits', dict)):
            if field in raw and not isinstance(raw[field], kind):
                raise SettingsError(f'模型配置 models.json 的 {field} 格式错误；原文件已保留。')
        for item in raw.get('endpoints') or []:
            try:
                data['endpoints'].append(self._endpoint(item, existing={}))
            except (SettingsError, TypeError, AttributeError) as exc:
                raise SettingsError(f'模型配置 models.json 的接口无效：{exc}；原文件已保留。') from None
        endpoint_ids = {item['id'] for item in data['endpoints']}
        if len(endpoint_ids) != len(data['endpoints']):
            raise SettingsError('模型配置 models.json 的接口 ID 重复；原文件已保留。')
        for item in raw.get('models') or []:
            try:
                model = self._model(item, endpoint_ids)
            except (SettingsError, TypeError, AttributeError) as exc:
                raise SettingsError(f'模型配置 models.json 的模型无效：{exc}；原文件已保留。') from None
            data['models'].append(model)
        if len({item['id'] for item in data['models']}) != len(data['models']):
            raise SettingsError('模型配置 models.json 的模型 ID 重复；原文件已保留。')
        use = raw.get('use') if isinstance(raw.get('use'), dict) else {}
        for role in ROLES:
            if role in use:
                if not self._choice_ok(data, role, use[role]):
                    raise SettingsError(f'模型配置 models.json 中 {role} 选中的模型不存在或无效；原文件已保留。')
                data['use'][role] = use[role]
        limits = raw.get('limits') if isinstance(raw.get('limits'), dict) else {}
        try:
            data['limits'] = self._limits(limits) if limits else {}
        except SettingsError as exc:
            raise SettingsError(f'模型配置 models.json 的费用上限无效：{exc}；原文件已保留。') from None
        return data

    def _endpoint(self, item, existing):
        endpoint_id = item.get('id')
        if not (isinstance(endpoint_id, str) and ID_PATTERN.fullmatch(endpoint_id) and endpoint_id[0] == 'e'):
            endpoint_id = 'e' + secrets.token_hex(3)
            while endpoint_id in existing:
                endpoint_id = 'e' + secrets.token_hex(3)
        return {'id': endpoint_id, 'name': _text(item.get('name'), '接口名字', 40),
                'base_url': _base_url(item.get('base_url')),
                'key_env': f'VEIL_KEY_{endpoint_id.upper()}'}

    def _model(self, item, endpoint_ids):
        model_id = item.get('id')
        if not (isinstance(model_id, str) and ID_PATTERN.fullmatch(model_id) and model_id[0] == 'm'):
            model_id = 'm' + secrets.token_hex(3)
        endpoint = item.get('endpoint')
        if endpoint not in endpoint_ids:
            raise SettingsError('模型要选一个接口。')
        billing = item.get('billing')
        if billing not in ('call', 'tokens'):
            raise SettingsError('计费方式要选“按次”或“按 token”。')
        model = {'id': model_id, 'endpoint': endpoint, 'model': _text(item.get('model'), '模型名', 200),
                 'label': (_text(item['label'], '显示名', 40) if str(item.get('label') or '').strip() else ''),
                 'thinking': item.get('thinking', True) is not False, 'billing': billing}
        if billing == 'call':
            model['cny_per_call'] = str(_decimal(item.get('cny_per_call'), '每次价格', maximum=Decimal(100)))
        else:
            model['input_cny_per_million'] = str(_decimal(item.get('input_cny_per_million'), '输入价格',
                                                          maximum=Decimal(10000)))
            model['output_cny_per_million'] = str(_decimal(item.get('output_cny_per_million'), '输出价格',
                                                           maximum=Decimal(10000)))
            cached = item.get('cached_input_cny_per_million')
            cached = (Decimal(model['input_cny_per_million']) if cached in (None, '')
                      else _decimal(cached, '缓存输入价格', allow_zero=True))
            if cached > Decimal(model['input_cny_per_million']):
                raise SettingsError('缓存输入价格不能比输入价格高。')
            model['cached_input_cny_per_million'] = str(cached)
        max_input = item.get('max_input_tokens', DEFAULT_MAX_INPUT)
        try:
            max_input = int(max_input)
        except (TypeError, ValueError):
            raise SettingsError('最长输入要填整数。') from None
        if not 4096 <= max_input <= 2_000_000:
            raise SettingsError('最长输入要在 4096 到 2000000 tokens 之间。')
        model['max_input_tokens'] = max_input
        return model

    @staticmethod
    def _limits(raw):
        daily = _decimal(raw.get('daily_cny', DEFAULT_DAILY), '每天上限', maximum=Decimal(1000))
        monthly = _decimal(raw.get('monthly_cny', DEFAULT_MONTHLY), '每月上限', maximum=Decimal(30000))
        if monthly < daily:
            raise SettingsError('每月上限不能比每天上限小。')
        return {'daily_cny': str(daily), 'monthly_cny': str(monthly)}

    def _provider(self, data, model_id):
        if not isinstance(model_id, str):
            return None
        if model_id.startswith(BUILTIN_PREFIX):
            provider = self.builtins.get(model_id[len(BUILTIN_PREFIX):])
            return dict(provider) if provider else None
        model = next((item for item in data['models'] if item['id'] == model_id), None)
        endpoint = model and next((item for item in data['endpoints'] if item['id'] == model['endpoint']), None)
        if not endpoint:
            return None
        provider = {'model': model['model'], 'base_url': endpoint['base_url'],
                    'api_key_env': endpoint['key_env'],
                    'thinking': 'enabled' if model['thinking'] else 'disabled',
                    'max_input_tokens': model['max_input_tokens'],
                    'label': model['label'] or model['model'], 'endpoint_name': endpoint['name']}
        if model['billing'] == 'call':
            provider['cny_per_call'] = Decimal(model['cny_per_call'])
            for key in ('input_cny_per_million', 'cached_input_cny_per_million', 'output_cny_per_million'):
                provider[key] = Decimal(0)
        else:
            for key in ('input_cny_per_million', 'cached_input_cny_per_million', 'output_cny_per_million'):
                provider[key] = Decimal(model[key])
        return provider

    def _choice_ok(self, data, role, model_id):
        if role == 'glance' and model_id == FOLLOW_CHAT:
            return True
        provider = self._provider(data, model_id)
        if provider is not None and provider.get('kind') == 'claude_agent' and role != 'peter':
            return False  # the Claude Code channel only works as Peter's chat model
        return provider is not None

    def selected(self, role):
        """What the 模型 page shows for a role. Autonomy follows chat until explicitly selected."""
        with self.lock:
            if role == 'glance':
                stored = self.data['use'].get('glance')
                if stored is not None:
                    return stored
                # Ticket 211: default to grok-4.6 when Lan has it configured, else follow chat.
                for item in self.data['models']:
                    if GLANCE_DEFAULT_MODEL in (item.get('label') or '').lower() or \
                            GLANCE_DEFAULT_MODEL in item['model'].lower():
                        if self._provider(self.data, item['id']) is not None:
                            return item['id']
                for name, provider in self.builtins.items():
                    if GLANCE_DEFAULT_MODEL in name.lower() or GLANCE_DEFAULT_MODEL in provider['model'].lower():
                        return BUILTIN_PREFIX + name
                return FOLLOW_CHAT
            if role == 'autonomy' and 'autonomy' not in self.data['use']:
                chat_id = self.data['use'].get('peter', self.default)
                if self._choice_ok(self.data, role, chat_id):
                    return chat_id
                if self._choice_ok(self.data, role, self.default):
                    return self.default
                for name in self.builtins:
                    candidate = BUILTIN_PREFIX + name
                    if self._choice_ok(self.data, role, candidate):
                        return candidate
                for item in self.data['models']:
                    if self._choice_ok(self.data, role, item['id']):
                        return item['id']
                return self.default
            return self.data['use'].get(role, self.default)

    # ---- what the program uses ----------------------------------------
    def chosen(self, role):
        """(model id, provider name for SYSTEM FACTS, provider dict) for a configured role."""
        with self.lock:
            if self.blocked:
                raise SettingsError('模型设置保存未完成；已停止模型请求，请重启恢复。')
            model_id = self.selected(role)
            if model_id == FOLLOW_CHAT:
                return self.chosen('peter')
            provider = self._provider(self.data, model_id)
            if provider is None:
                raise SettingsError(f'{role} 选中的模型无效；已停止模型请求。')
            if role == 'autonomy' and provider.get('kind') == 'claude_agent':
                raise SettingsError('自主活动需要 API 模型；Claude Code 通道仅用于聊天。')
            if model_id.startswith(BUILTIN_PREFIX):
                name = model_id[len(BUILTIN_PREFIX):]
            else:
                name = provider['endpoint_name']
            return model_id, name, provider

    def limits(self):
        with self.lock:
            limits = self.data['limits']
            if not limits:
                return DEFAULT_DAILY, DEFAULT_MONTHLY
            return Decimal(limits['daily_cny']), Decimal(limits['monthly_cny'])

    def _env_values(self):
        try:
            from dotenv import dotenv_values
            return dotenv_values(self.env_path, interpolate=False, encoding='utf-8-sig')
        except (OSError, UnicodeError):
            return {}

    def public(self):
        """What the settings page shows. Keys are never sent; only whether one is saved and its last 4."""
        with self.lock:
            env = self._env_values()
            endpoints = []
            for item in self.data['endpoints']:
                key = (env.get(item['key_env']) or '').strip()
                endpoints.append({'id': item['id'], 'name': item['name'], 'base_url': item['base_url'],
                                  'key_set': bool(key), 'key_hint': ('…' + key[-4:]) if len(key) >= 8 else ''})
            models = []
            for name, provider in self.builtins.items():
                models.append({'id': BUILTIN_PREFIX + name, 'builtin': True,
                               'label': f'{provider.get("label", name)}（内置）', 'kind': provider.get('kind'),
                               'model': provider['model'], 'endpoint_name': name,
                               'key_set': provider.get('kind') == 'claude_agent'
                               or bool((env.get(provider['api_key_env']) or '').strip())})
            names = {item['id']: item['name'] for item in endpoints}
            keys = {item['id']: item['key_set'] for item in endpoints}
            for item in self.data['models']:
                models.append({**item, 'builtin': False, 'endpoint_name': names.get(item['endpoint'], ''),
                               'key_set': keys.get(item['endpoint'], False)})
            daily, monthly = self.limits()
            use = {role: (self.selected(role) if role == 'glance' else self.chosen(role)[0]) for role in ROLES}
            return {'endpoints': endpoints, 'models': models, 'use': use,
                    'limits': {'daily_cny': str(daily), 'monthly_cny': str(monthly)}}

    # ---- changes from the settings page -------------------------------
    def save(self, payload):
        """Replace endpoints, models and limits; new keys go to .env. Returns the new public view."""
        if not isinstance(payload, dict):
            raise SettingsError('设置格式不对。')
        endpoints_in = payload.get('endpoints') or []
        models_in = payload.get('models') or []
        if (not isinstance(endpoints_in, list) or not isinstance(models_in, list)
                or len(endpoints_in) > MAX_ENDPOINTS or len(models_in) > MAX_MODELS):
            raise SettingsError('接口或模型太多了。')
        with _ENV_LOCK, self.lock:
            if self.blocked or self.journal.exists():
                self._recover()
            endpoints, new_keys = [], {}
            known = {item['id']: item for item in self.data['endpoints']}
            taken = set(known)
            for item in endpoints_in:
                if not isinstance(item, dict):
                    raise SettingsError('设置格式不对。')
                endpoint = self._endpoint(item, existing=taken)
                taken.add(endpoint['id'])
                key = item.get('key')
                if isinstance(key, str) and key.strip():
                    new_keys[endpoint['key_env']] = _key(key)
                elif endpoint['id'] not in known:
                    raise SettingsError(f'新接口“{endpoint["name"]}”要填 API Key。')
                endpoints.append(endpoint)
            if len({item['name'] for item in endpoints}) != len(endpoints):
                raise SettingsError('两个接口不能同名。')
            if len({item['id'] for item in endpoints}) != len(endpoints):
                raise SettingsError('两个接口不能使用相同 ID。')
            endpoint_ids = {item['id'] for item in endpoints}
            models = []
            for item in models_in:
                if not isinstance(item, dict):
                    raise SettingsError('设置格式不对。')
                models.append(self._model(item, endpoint_ids))
            if len({item['id'] for item in models}) != len(models):
                raise SettingsError('两个模型不能使用相同 ID。')
            data = {'endpoints': endpoints, 'models': models, 'use': {},
                    'limits': self._limits(payload.get('limits') or {})}
            use = payload.get('use') if isinstance(payload.get('use'), dict) else self.data['use']
            for role in ROLES:
                model_id = use.get(role, self.data['use'].get(role))
                if self._choice_ok(data, role, model_id):
                    data['use'][role] = model_id
            removed = [item['key_env'] for item in self.data['endpoints'] if item['id'] not in endpoint_ids]
            if not new_keys and not removed:
                self._write(data)
                self.data = data
                return self.public()
            record = {'state': 'prepared', 'env': self._snapshot(self.env_path),
                      'models': self._snapshot(self.path),
                      'env_mode': stat.S_IMODE(self.env_path.stat().st_mode) if self.env_path.exists() else 0o600}
            self._atomic_bytes(self.journal, json.dumps(record).encode('utf-8'))
            try:
                self._write_env(new_keys, removed)
                self._write(data)
                self._atomic_bytes(self.journal, json.dumps({**record, 'state': 'committed'}).encode('utf-8'))
            except Exception:
                self._recover()
                raise
            self.data = data
            try:
                self.journal.unlink()
                self._sync_directory(self.journal.parent)
            except OSError:
                # Both files and the committed marker are durable. A leftover marker is
                # cleaned on restart; cleanup must not turn a committed save into failure.
                pass
            return self.public()

    def use(self, role, model_id):
        if role not in ROLES:
            raise SettingsError('不认识的用途。')
        with self.lock:
            if self.blocked or self.journal.exists():
                raise SettingsError('模型设置保存未完成；请重启恢复后再切换。')
            if not self._choice_ok(self.data, role, model_id):
                raise SettingsError('这个模型已经不在列表里了。')
            data = {**self.data, 'use': {**self.data['use'], role: model_id}}
            self._write(data)
            self.data = data
            return self.chosen(role)

    def _write(self, data):
        self._atomic_bytes(self.path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

    def _write_env(self, new_keys, removed):
        if not new_keys and not removed:
            return
        from dotenv import set_key, unset_key
        staged = self.env_stage
        try:
            if self.env_path.exists():
                shutil.copy2(self.env_path, staged)
            else:
                staged.write_text('', encoding='utf-8')
            os.chmod(staged, stat.S_IMODE(self.env_path.stat().st_mode) if self.env_path.exists() else 0o600)
            for name, value in new_keys.items():
                set_key(staged, name, value, quote_mode='always', encoding='utf-8')
            for name in removed:
                if name not in new_keys:
                    unset_key(staged, name, encoding='utf-8')
            with staged.open('rb+') as stream:
                os.fsync(stream.fileno())
            os.replace(staged, self.env_path)
            self._sync_directory(self.env_path.parent)
        finally:
            staged.unlink(missing_ok=True)

