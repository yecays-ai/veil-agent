"""Read-only projection of configuration metadata and consumer observations.

Register code-owned descriptions, not configuration values. Domain adapters supply
only payload-safe, scoped observations; this module never reads files or resolves
settings independently of their runtime consumers.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import math
import re


class Scope(StrEnum):
    INSTANCE = 'instance'
    ROLE = 'role'


class ValueType(StrEnum):
    BOOLEAN = 'boolean'
    INTEGER = 'integer'
    NUMBER = 'number'
    STRING = 'string'
    MODEL_ID = 'model_id'


class Source(StrEnum):
    RUNTIME = 'runtime'
    STARTUP = 'startup'
    SAVED = 'saved'
    SETTINGS_DEFAULT = 'settings_default'
    UNKNOWN = 'unknown'


class ApplyMode(StrEnum):
    IMMEDIATE = 'immediate'
    NEXT_REQUEST = 'next_request'
    NEXT_TASK = 'next_task'
    RESTART = 'restart'
    UNKNOWN = 'unknown'


_IDENTIFIER = re.compile(r'[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+')
_ROLE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
Scalar = str | bool | int | float | None


@dataclass(frozen=True, slots=True)
class ConfigDefinition:
    key: str
    label: str
    scope: Scope
    value_type: ValueType
    apply_mode: ApplyMode = ApplyMode.UNKNOWN
    # Opt in to public values. Unclassified values are never implicitly exposed.
    sensitive: bool = True

    def __post_init__(self):
        if not isinstance(self.key, str) or not _IDENTIFIER.fullmatch(self.key):
            raise ValueError('invalid namespaced configuration key')
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError('configuration label is required')
        if not (isinstance(self.scope, Scope) and isinstance(self.value_type, ValueType)
                and isinstance(self.apply_mode, ApplyMode) and type(self.sensitive) is bool):
            raise ValueError('invalid configuration metadata')

    @property
    def domain(self) -> str:
        return self.key.split('.', 1)[0]


@dataclass(frozen=True, slots=True)
class Observation:
    source: Source
    value: Scalar


@dataclass(frozen=True, slots=True)
class ConfigEvidence:
    """Observation for one key: role_id=None denotes instance-level evidence."""
    role_id: str | None
    configured: Observation | None = None
    effective: Observation | None = None


def _valid_scalar(value, value_type):
    if value_type is ValueType.BOOLEAN:
        return type(value) is bool
    if value_type is ValueType.INTEGER:
        return type(value) is int and -(2**53 - 1) <= value <= 2**53 - 1
    if value_type is ValueType.NUMBER:
        return ((type(value) is int and -(2**53 - 1) <= value <= 2**53 - 1)
                or (type(value) is float and math.isfinite(value)))
    return type(value) is str and 0 < len(value) <= 256 and value.isprintable()


def _project(observation, definition, *, effective):
    if definition.sensitive:
        return {'status': 'redacted', 'value': None, 'source': None}
    allowed_sources = ((Source.RUNTIME, Source.STARTUP) if effective
                       else (Source.SAVED, Source.SETTINGS_DEFAULT))
    if not (isinstance(observation, Observation) and isinstance(observation.source, Source)
            and observation.source in allowed_sources):
        return {'status': 'unknown', 'value': None, 'source': None}
    value = observation.value
    if not effective and value is None:
        # An authoritative config snapshot can say no selection is configured.
        return {'status': 'unset', 'value': None, 'source': observation.source.value}
    if not _valid_scalar(value, definition.value_type):
        return {'status': 'unknown', 'value': None, 'source': None}
    return {'status': 'known', 'value': value, 'source': observation.source.value}


@dataclass(frozen=True, slots=True)
class ConfigRegistry:
    definitions: tuple[ConfigDefinition, ...] = field(default_factory=tuple)

    def __init__(self, definitions: Iterable[ConfigDefinition] = ()):
        definitions = tuple(definitions)
        seen = set()
        for definition in definitions:
            if not isinstance(definition, ConfigDefinition):
                raise TypeError('expected a configuration definition')
            if definition.key in seen:
                raise ValueError(f'duplicate configuration key: {definition.key}')
            seen.add(definition.key)
        object.__setattr__(self, 'definitions', definitions)

    def register(self, *definitions: ConfigDefinition) -> 'ConfigRegistry':
        """Return a new registry, failing atomically on duplicate keys."""
        return ConfigRegistry((*self.definitions, *definitions))

    def overview(self, *, role_id: str | None = None,
                 evidence: Mapping[str, ConfigEvidence] | None = None) -> dict:
        if role_id is not None and (not isinstance(role_id, str) or not _ROLE_ID.fullmatch(role_id)):
            raise ValueError('invalid configuration scope')
        observations = evidence or {}
        entries = []
        for definition in self.definitions:
            if role_id is None and definition.scope is Scope.ROLE:
                continue
            item = observations.get(definition.key)
            target = role_id if definition.scope is Scope.ROLE else None
            if not isinstance(item, ConfigEvidence) or item.role_id != target:
                item = None
            configured = _project(item.configured if item else None, definition, effective=False)
            effective = _project(item.effective if item else None, definition, effective=True)
            pending_restart = None
            if (definition.apply_mode is ApplyMode.RESTART and configured['status'] == 'known'
                    and effective['status'] == 'known'):
                pending_restart = configured['value'] != effective['value']
            entries.append({'key': definition.key, 'label': definition.label, 'domain': definition.domain,
                            'scope': definition.scope.value, 'value_type': definition.value_type.value,
                            'sensitive': definition.sensitive, 'mutability': 'read_only',
                            'apply_mode': definition.apply_mode.value, 'pending_restart': pending_restart,
                            'configured': configured, 'effective': effective})
        return {'schema_version': 1, 'scope': {'role_id': role_id}, 'entries': entries}


# Models adapter: translate existing consumer/Settings observations to the shared contract.
# Keep configured choices separate from actual loaded models. Delegated, Glance and
# autonomous calls resolve model/fallback at execution time, so remain unknown here.
from veil.core.model_settings import BUILTIN_PREFIX, FOLLOW_CHAT, ID_PATTERN, ROLE_LABELS, ROLES

_MODEL_APPLY = {
    'peter': ApplyMode.NEXT_REQUEST, 'background': ApplyMode.RESTART,
    'autonomy': ApplyMode.NEXT_TASK, 'glance': ApplyMode.NEXT_TASK,
    'delegate': ApplyMode.NEXT_TASK,
}
_MODEL_DEFINITIONS = ConfigRegistry(
    ConfigDefinition(f'models.{purpose}', ROLE_LABELS[purpose], Scope.ROLE, ValueType.MODEL_ID,
                     _MODEL_APPLY[purpose], sensitive=False) for purpose in ROLES
)


def model_config_registry() -> ConfigRegistry:
    return _MODEL_DEFINITIONS


def _model_id(value, *, follow_chat=False):
    if type(value) is not str:
        return None
    if value == FOLLOW_CHAT and follow_chat:
        return value
    if value.startswith(BUILTIN_PREFIX):
        name = value[len(BUILTIN_PREFIX):]
        return value if _ROLE_ID.fullmatch(name) else None
    return value if value.startswith('m') and ID_PATTERN.fullmatch(value) else None


def model_config_overview(*, role_id: str, settings: Mapping | None = None,
                          chat_model: str | None = None, background_model: str | None = None) -> dict:
    """Read ModelSettings.public()'s selections and proven loaded model identifiers only.

    The caller must obtain runtime IDs from the actual consumers, not by re-reading
    their saved selections. Never forward endpoint records, key hints or provider data,
    and never use the saved snapshot as evidence of readiness or activity.
    """
    use = settings.get('use') if isinstance(settings, Mapping) else None
    explicit = settings.get('use_explicit') if isinstance(settings, Mapping) else None
    runtime = {'peter': (Source.RUNTIME, chat_model), 'background': (Source.STARTUP, background_model)}
    evidence = {}
    for purpose in ROLES:
        configured = None
        if isinstance(use, Mapping) and purpose in use:
            raw = use[purpose]
            value = _model_id(raw, follow_chat=purpose in ('autonomy', 'glance'))
            if value is not None or raw is None:
                is_explicit = isinstance(explicit, Mapping) and explicit.get(purpose) is True
                configured = Observation(Source.SAVED if is_explicit else Source.SETTINGS_DEFAULT, value)
        source, raw = runtime.get(purpose, (Source.UNKNOWN, None))
        value = _model_id(raw)
        effective = Observation(source, value) if value is not None else None
        evidence[f'models.{purpose}'] = ConfigEvidence(role_id, configured, effective)
    view = model_config_registry().overview(role_id=role_id, evidence=evidence)
    catalog = settings.get('models') if isinstance(settings, Mapping) else None
    key_states: dict[str, str] = {}
    duplicates: set[str] = set()
    if isinstance(catalog, (list, tuple)):
        for row in catalog:
            if not isinstance(row, Mapping):
                continue
            model_id = _model_id(row.get('id'))
            if model_id is None:
                continue
            if model_id in key_states:
                duplicates.add(model_id)
            key_set = row.get('key_set')
            key_states[model_id] = ('configured' if key_set is True else
                                    'missing' if key_set is False else 'unknown')
    for entry in view['entries']:
        model_id = entry['effective']['value'] or entry['configured']['value']
        entry['readiness'] = ('unknown' if model_id in duplicates else key_states.get(model_id, 'unknown'))
    return view
