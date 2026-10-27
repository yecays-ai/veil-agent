"""Ticket 134: Lan's own model list, kept on her computer.

Endpoints (name, address, key) and models (name, price, thinking) live in
data/<character>/models.json; keys go only into the project .env. Two jobs pick
a model each: "peter" (chat, wakes, glances, QQ and email: everything he says)
and "background" (Ombre tagging and short-term notes). The providers written in
character.toml stay available as built-in models and are the defaults.
"""
import json
import os
import re
import secrets
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

BUILTIN_PREFIX = 'builtin:'
ROLES = ('peter', 'background')
DEFAULT_DAILY = Decimal('10')
DEFAULT_MONTHLY = Decimal('300')
DEFAULT_MAX_INPUT = 32768
MAX_ENDPOINTS = 20
MAX_MODELS = 60
ID_PATTERN = re.compile(r'[em][0-9a-f]{6}')


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
        self.builtins = dict(builtins)
        self.default = BUILTIN_PREFIX + default_name
        self.lock = threading.RLock()
        self.data = self._clean(self._read())

    # ---- reading -------------------------------------------------------
    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def _clean(self, raw):
        """Keep only entries that would pass the settings page's own checks."""
        data = {'endpoints': [], 'models': [], 'use': {}, 'limits': {}}
        for item in raw.get('endpoints') or []:
            try:
                data['endpoints'].append(self._endpoint(item, existing={}))
            except (SettingsError, TypeError, AttributeError):
                continue
        endpoint_ids = {item['id'] for item in data['endpoints']}
        for item in raw.get('models') or []:
            try:
                model = self._model(item, endpoint_ids)
            except (SettingsError, TypeError, AttributeError):
                continue
            data['models'].append(model)
        use = raw.get('use') if isinstance(raw.get('use'), dict) else {}
        for role in ROLES:
            if self._provider(data, use.get(role)) is not None:
                data['use'][role] = use[role]
        limits = raw.get('limits') if isinstance(raw.get('limits'), dict) else {}
        try:
            data['limits'] = self._limits(limits) if limits else {}
        except SettingsError:
            data['limits'] = {}
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

    # ---- what the program uses ----------------------------------------
    def chosen(self, role):
        """(model id, provider name for SYSTEM FACTS, provider dict) for 'peter' or 'background'."""
        with self.lock:
            model_id = self.data['use'].get(role, self.default)
            provider = self._provider(self.data, model_id)
            if provider is None:
                model_id, provider = self.default, self._provider(self.data, self.default)
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
                models.append({'id': BUILTIN_PREFIX + name, 'builtin': True, 'label': f'{name}（内置）',
                               'model': provider['model'], 'endpoint_name': name,
                               'key_set': bool((env.get(provider['api_key_env']) or '').strip())})
            names = {item['id']: item['name'] for item in endpoints}
            keys = {item['id']: item['key_set'] for item in endpoints}
            for item in self.data['models']:
                models.append({**item, 'builtin': False, 'endpoint_name': names.get(item['endpoint'], ''),
                               'key_set': keys.get(item['endpoint'], False)})
            daily, monthly = self.limits()
            use = {role: self.chosen(role)[0] for role in ROLES}
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
        with self.lock:
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
            endpoint_ids = {item['id'] for item in endpoints}
            models = []
            for item in models_in:
                if not isinstance(item, dict):
                    raise SettingsError('设置格式不对。')
                models.append(self._model(item, endpoint_ids))
            data = {'endpoints': endpoints, 'models': models, 'use': {},
                    'limits': self._limits(payload.get('limits') or {})}
            use = payload.get('use') if isinstance(payload.get('use'), dict) else self.data['use']
            for role in ROLES:
                model_id = use.get(role, self.data['use'].get(role))
                if self._provider(data, model_id) is not None:
                    data['use'][role] = model_id
            removed = [item['key_env'] for item in self.data['endpoints'] if item['id'] not in endpoint_ids]
            self._write_env(new_keys, removed)
            self._write(data)
            self.data = data
            return self.public()

    def use(self, role, model_id):
        if role not in ROLES:
            raise SettingsError('不认识的用途。')
        with self.lock:
            if self._provider(self.data, model_id) is None:
                raise SettingsError('这个模型已经不在列表里了。')
            data = {**self.data, 'use': {**self.data['use'], role: model_id}}
            self._write(data)
            self.data = data
            return self.chosen(role)

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, self.path)

    def _write_env(self, new_keys, removed):
        if not new_keys and not removed:
            return
        from dotenv import set_key, unset_key
        if not self.env_path.exists():
            self.env_path.write_text('', encoding='utf-8')
        for name, value in new_keys.items():
            set_key(self.env_path, name, value, quote_mode='always', encoding='utf-8')
        for name in removed:
            if name not in new_keys:
                unset_key(self.env_path, name, encoding='utf-8')
