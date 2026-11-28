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


_SEMANTIC_TYPES = {
    'daily_cap_cny': ('关系语义核验日额度（元）', ValueType.NUMBER),
    'timeout_seconds': ('关系语义核验超时（秒）', ValueType.INTEGER),
}
_SEMANTIC_DEFINITIONS = ConfigRegistry(
    ConfigDefinition(f'budget.relationship_semantic.{key}', label, Scope.ROLE,
                     value_type, ApplyMode.NEXT_TASK, sensitive=False)
    for key, (label, value_type) in _SEMANTIC_TYPES.items()
)


def semantic_config_overview(*, role_id: str, observation: tuple[dict, bool] | None) -> list:
    evidence = {}
    if observation is not None:
        settings, explicit = observation
        source = Source.SAVED if explicit else Source.SETTINGS_DEFAULT
        for name in _SEMANTIC_TYPES:
            value = settings[name]
            if name == 'daily_cap_cny':
                value = float(value)
            evidence[f'budget.relationship_semantic.{name}'] = ConfigEvidence(
                role_id, Observation(source, value), Observation(Source.RUNTIME, value))
    return _SEMANTIC_DEFINITIONS.overview(role_id=role_id, evidence=evidence)['entries']


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


# P6-S5: recall, retrieval & contextual association configuration registry (read-only projection).
_RECALL_DEFINITIONS = ConfigRegistry((
    ConfigDefinition('memory.retrieval.stopwords_enabled', '检索中文停用词过滤（候选开关）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.retrieval.candidate_k', '混合检索候选数 K',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall_judge.enabled', '召回判官总开关',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.IMMEDIATE, sensitive=False),
    ConfigDefinition('memory.recall_judge.timeout_seconds', '召回判官模型超时（秒）',
                     Scope.ROLE, ValueType.NUMBER, ApplyMode.RESTART, sensitive=False),
    ConfigDefinition('memory.recall.association_enabled', '自然联想总开关',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.association_contextual_enabled', '情境相关自然联想（候选开关）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.association_min_similarity', '情境自然联想最低余弦相似度',
                     Scope.ROLE, ValueType.NUMBER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.association_pool_size', '情境自然联想候选池上限',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.leisure_association_enabled', '自主闲暇自然联想（候选开关）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.leisure_association_min_similarity', '自主闲暇联想最低余弦相似度',
                     Scope.ROLE, ValueType.NUMBER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.leisure_association_min_importance', '自主闲暇联想最低长期重要度',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.leisure_association_cooldown_turns', '自主闲暇联想冷却轮数',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.recall.leisure_association_pool_size', '自主闲暇联想候选池上限',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
))


def recall_config_registry() -> ConfigRegistry:
    return _RECALL_DEFINITIONS


def recall_config_overview(*, role_id: str, memory_cfg: Mapping | None = None,
                           retrieval_runtime: Mapping | None = None,
                           recall_runtime: Mapping | None = None,
                           judge_enabled: bool | None = None,
                           judge_timeout: float | None = None) -> dict:
    """Project configured vs runtime-effective recall/retrieval/association settings."""
    from veil.brain.recall.brain_retrieval import load_settings as load_ret
    from veil.brain.recall.recall_judge import load_settings as load_judge
    from veil.brain.recall.recall_rotation import settings as load_rot

    mem = memory_cfg if isinstance(memory_cfg, Mapping) else {}
    ret_raw = mem.get('retrieval') if isinstance(mem.get('retrieval'), Mapping) else {}
    judge_raw = mem.get('recall_judge') if isinstance(mem.get('recall_judge'), Mapping) else {}
    rot_raw = mem.get('recall') if isinstance(mem.get('recall'), Mapping) else {}
    ret_cfg = load_ret(ret_raw)
    judge_cfg = load_judge(judge_raw)
    rot_cfg = load_rot(mem)
    ret_eff = retrieval_runtime if isinstance(retrieval_runtime, Mapping) else ret_cfg
    rot_eff = recall_runtime if isinstance(recall_runtime, Mapping) else rot_cfg

    def _obs(raw_map, key, val):
        return Observation(Source.SAVED if key in raw_map else Source.SETTINGS_DEFAULT, val)

    evidence = {
        'memory.retrieval.stopwords_enabled': ConfigEvidence(
            role_id, _obs(ret_raw, 'stopwords_enabled', bool(ret_cfg['stopwords_enabled'])),
            Observation(Source.RUNTIME, bool(ret_eff.get('stopwords_enabled', False)))),
        'memory.retrieval.candidate_k': ConfigEvidence(
            role_id, _obs(ret_raw, 'candidate_k', int(ret_cfg['candidate_k'])),
            Observation(Source.RUNTIME, int(ret_eff.get('candidate_k', 8)))),
        'memory.recall_judge.enabled': ConfigEvidence(
            role_id, _obs(judge_raw, 'enabled', bool(judge_cfg['enabled'])),
            Observation(Source.RUNTIME, bool(judge_enabled)) if type(judge_enabled) is bool else None),
        'memory.recall_judge.timeout_seconds': ConfigEvidence(
            role_id, _obs(judge_raw, 'timeout_seconds', float(judge_cfg['timeout_seconds'])),
            Observation(Source.STARTUP, float(judge_timeout)) if type(judge_timeout) in (int, float) else None),
        'memory.recall.association_enabled': ConfigEvidence(
            role_id, _obs(rot_raw, 'association_enabled', bool(rot_cfg['association_enabled'])),
            Observation(Source.RUNTIME, bool(rot_eff.get('association_enabled', True)))),
        'memory.recall.association_contextual_enabled': ConfigEvidence(
            role_id, _obs(rot_raw, 'association_contextual_enabled', bool(rot_cfg['association_contextual_enabled'])),
            Observation(Source.RUNTIME, bool(rot_eff.get('association_contextual_enabled', False)))),
        'memory.recall.association_min_similarity': ConfigEvidence(
            role_id, _obs(rot_raw, 'association_min_similarity', float(rot_cfg['association_min_similarity'])),
            Observation(Source.RUNTIME, float(rot_eff.get('association_min_similarity', 0.52)))),
        'memory.recall.association_pool_size': ConfigEvidence(
            role_id, _obs(rot_raw, 'association_pool_size', int(rot_cfg['association_pool_size'])),
            Observation(Source.RUNTIME, int(rot_eff.get('association_pool_size', 32)))),
        'memory.recall.leisure_association_enabled': ConfigEvidence(
            role_id, _obs(rot_raw, 'leisure_association_enabled', bool(rot_cfg['leisure_association_enabled'])),
            Observation(Source.RUNTIME, bool(rot_eff.get('leisure_association_enabled', False)))),
        'memory.recall.leisure_association_min_similarity': ConfigEvidence(
            role_id, _obs(rot_raw, 'leisure_association_min_similarity', float(rot_cfg['leisure_association_min_similarity'])),
            Observation(Source.RUNTIME, float(rot_eff.get('leisure_association_min_similarity', 0.38)))),
        'memory.recall.leisure_association_min_importance': ConfigEvidence(
            role_id, _obs(rot_raw, 'leisure_association_min_importance', int(rot_cfg['leisure_association_min_importance'])),
            Observation(Source.RUNTIME, int(rot_eff.get('leisure_association_min_importance', 5)))),
        'memory.recall.leisure_association_cooldown_turns': ConfigEvidence(
            role_id, _obs(rot_raw, 'leisure_association_cooldown_turns', int(rot_cfg['leisure_association_cooldown_turns'])),
            Observation(Source.RUNTIME, int(rot_eff.get('leisure_association_cooldown_turns', 12)))),
        'memory.recall.leisure_association_pool_size': ConfigEvidence(
            role_id, _obs(rot_raw, 'leisure_association_pool_size', int(rot_cfg['leisure_association_pool_size'])),
            Observation(Source.RUNTIME, int(rot_eff.get('leisure_association_pool_size', 36)))),
    }
    return recall_config_registry().overview(role_id=role_id, evidence=evidence)


# P6-S6-3: cognitive consolidation configuration registry (read-only projection).
_CONSOLIDATION_DEFINITIONS = ConfigRegistry((
    ConfigDefinition('memory.consolidation.enabled', '后台认知巩固开关（灰度默认关闭）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.consolidation.interval_hours', '认知巩固执行间隔（小时）',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.consolidation.max_runs_per_day', '认知巩固每日最多次数',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.consolidation.daily_budget_cny', '认知巩固每日费用上限（元）',
                     Scope.ROLE, ValueType.NUMBER, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.consolidation.timeout_seconds', '认知巩固单次超时（秒）',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.consolidation.auto_apply_importance', '巩固重要度建议自动应用安全增量',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_TASK, sensitive=False),
))


def consolidation_config_registry() -> ConfigRegistry:
    return _CONSOLIDATION_DEFINITIONS


def consolidation_config_overview(*, role_id: str, persisted: Mapping | None = None,
                                  runtime: Mapping | None = None) -> dict:
    from veil.brain.cognitive_consolidation import settings_from

    saved = persisted if isinstance(persisted, Mapping) else {}
    cfg_vals = settings_from(saved)
    eff_vals = settings_from(runtime) if isinstance(runtime, Mapping) else cfg_vals
    evidence = {}
    for key in ('enabled', 'interval_hours', 'max_runs_per_day', 'daily_budget_cny',
                'timeout_seconds', 'auto_apply_importance'):
        src = Source.SAVED if key in saved else Source.SETTINGS_DEFAULT
        evidence[f'memory.consolidation.{key}'] = ConfigEvidence(
            role_id,
            Observation(src, cfg_vals[key]),
            Observation(Source.RUNTIME, eff_vals[key]),
        )
    return consolidation_config_registry().overview(role_id=role_id, evidence=evidence)


# P7-S1: role-local experience injection settings (UI controls land in P7-S4).
_EXPERIENCE_INJECTION_DEFINITIONS = ConfigRegistry((
    ConfigDefinition('memory.p7.enabled', '情境经验注入总开关（默认关闭）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.p7.allow_in_autonomy', '允许自主活动使用情境经验',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.p7.allow_tool_step_recheck', '允许工具步骤补检工作经验',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.p7.auto_promote_objective', '客观工具实证自动晋升（默认关闭）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.p7.auto_promote_commitment', '承诺客观实证自动晋升（默认关闭）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_TASK, sensitive=False),
    ConfigDefinition('memory.p7.semantic_gate_enabled', '灰区语义复核（默认关闭）',
                     Scope.ROLE, ValueType.BOOLEAN, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.p7.semantic_gate_daily_budget_cny', '灰区语义复核每日上限（元）',
                     Scope.ROLE, ValueType.NUMBER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.p7.max_items', '单轮经验注入条数上限',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.p7.max_tokens', '单轮经验注入 Token 上限',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
    ConfigDefinition('memory.p7.session_cooldown_turns', '同会话经验冷却轮数',
                     Scope.ROLE, ValueType.INTEGER, ApplyMode.NEXT_REQUEST, sensitive=False),
))


def experience_injection_config_overview(*, role_id: str, persisted: Mapping | None = None,
                                         runtime: Mapping | None = None) -> dict:
    from veil.brain.experience_provider import DEFAULTS, settings_from

    saved = persisted if isinstance(persisted, Mapping) else {}
    configured = settings_from(saved)
    effective = settings_from(runtime) if isinstance(runtime, Mapping) else configured
    evidence = {}
    for key in DEFAULTS:
        source = Source.SAVED if key in saved else Source.SETTINGS_DEFAULT
        evidence[f'memory.p7.{key}'] = ConfigEvidence(
            role_id,
            Observation(source, configured[key]),
            Observation(Source.RUNTIME, effective[key]),
        )
    return _EXPERIENCE_INJECTION_DEFINITIONS.overview(role_id=role_id, evidence=evidence)
