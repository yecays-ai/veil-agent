import json
import math
import unittest
from dataclasses import FrozenInstanceError

from veil.core.config_registry import (ConfigDefinition, ConfigEvidence, ConfigRegistry, Observation, Scope,
                             Source, ValueType, model_config_overview)
from veil.core.model_settings import ROLES


class ConfigRegistryTests(unittest.TestCase):
    def test_registration_is_immutable_and_rejects_duplicates_across_scopes(self):
        first = ConfigDefinition('feature.enabled', 'Feature', Scope.INSTANCE, ValueType.BOOLEAN)
        second = ConfigDefinition('memory.limit', 'Limit', Scope.ROLE, ValueType.INTEGER)
        originals = [first]
        initial = ConfigRegistry(originals)
        originals.clear()
        expanded = initial.register(second)
        self.assertEqual(initial.definitions, (first,))
        self.assertEqual(expanded.definitions, (first, second))
        with self.assertRaises(FrozenInstanceError):
            first.key = 'feature.changed'
        with self.assertRaises(FrozenInstanceError):
            initial.definitions = ()
        with self.assertRaisesRegex(ValueError, 'duplicate configuration key'):
            initial.register(ConfigDefinition(first.key, 'Other scope', Scope.ROLE, ValueType.STRING))
        with self.assertRaisesRegex(ValueError, 'invalid namespaced configuration key'):
            ConfigDefinition('not_namespaced', 'Feature', Scope.INSTANCE, ValueType.BOOLEAN)

    def test_role_observations_are_not_exposed_to_another_scope(self):
        instance = ConfigDefinition('feature.enabled', 'Enabled', Scope.INSTANCE, ValueType.BOOLEAN,
                                    sensitive=False)
        role = ConfigDefinition('feature.limit', 'Limit', Scope.ROLE, ValueType.INTEGER, sensitive=False)
        registry = ConfigRegistry([instance, role])
        evidence = {instance.key: ConfigEvidence(None, effective=Observation(Source.RUNTIME, False)),
                    role.key: ConfigEvidence('alpha', effective=Observation(Source.RUNTIME, 0)),
                    'not.registered': ConfigEvidence('alpha', effective=Observation(Source.RUNTIME, 'private'))}
        self.assertEqual([entry['key'] for entry in registry.overview(evidence=evidence)['entries']], [instance.key])
        matching = registry.overview(role_id='alpha', evidence=evidence)
        self.assertEqual([entry['effective']['value'] for entry in matching['entries']], [False, 0])
        other = registry.overview(role_id='beta', evidence=evidence)
        self.assertEqual(other['entries'][1]['effective']['status'], 'unknown')
        self.assertNotIn('private', json.dumps(matching))
        with self.assertRaises(ValueError):
            registry.overview(role_id='../alpha', evidence=evidence)

    def test_redaction_type_checks_and_evidence_sources(self):
        definitions = (ConfigDefinition('model.key', 'Credential', Scope.INSTANCE, ValueType.STRING),
                       ConfigDefinition('model.calls', 'Calls', Scope.INSTANCE, ValueType.INTEGER, sensitive=False),
                       ConfigDefinition('model.cost', 'Cost', Scope.INSTANCE, ValueType.NUMBER, sensitive=False),
                       ConfigDefinition('model.enabled', 'Enabled', Scope.INSTANCE, ValueType.BOOLEAN, sensitive=False))
        evidence = {
            'model.key': ConfigEvidence(None, Observation(Source.SAVED, 'private-token'),
                                         Observation(Source.RUNTIME, 'private-token')),
            'model.calls': ConfigEvidence(None, effective=Observation(Source.RUNTIME, True)),
            'model.cost': ConfigEvidence(None, effective=Observation(Source.RUNTIME, math.inf)),
            'model.enabled': ConfigEvidence(None, Observation(Source.SAVED, True), Observation(Source.SAVED, True)),
        }
        overview = ConfigRegistry(definitions).overview(evidence=evidence)
        entries = {entry['key']: entry for entry in overview['entries']}
        masked = {'status': 'redacted', 'value': None, 'source': None}
        self.assertEqual(entries['model.key']['configured'], masked)
        self.assertEqual(entries['model.key']['effective'], masked)
        for key in ('model.calls', 'model.cost', 'model.enabled'):
            self.assertEqual(entries[key]['effective']['status'], 'unknown')
        self.assertTrue(entries['model.enabled']['configured']['value'])
        self.assertNotIn('private-token', json.dumps(overview, allow_nan=False))


class ConfigModelAdapterTests(unittest.TestCase):
    def test_configured_and_runtime_are_independent_and_secret_fields_are_discarded(self):
        settings = {'use': {'peter': 'builtin:deepseek', 'background': 'mabcdef',
                            'autonomy': 'follow-chat', 'glance': 'builtin:deepseek', 'delegate': None},
                    'use_explicit': {'peter': True, 'background': True, 'delegate': False},
                    'endpoints': [{'key_hint': 'private-hint', 'base_url': 'private-url'}],
                    'models': [{'id': 'mabcdef', 'label': 'private-label'}]}
        result = model_config_overview(role_id='alpha', settings=settings, chat_model='builtin:deepseek',
                                       background_model='builtin:openai')
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['scope'], {'role_id': 'alpha'})
        entries = {entry['key']: entry for entry in result['entries']}
        self.assertEqual(tuple(entries), tuple(f'models.{purpose}' for purpose in ROLES))
        self.assertEqual(entries['models.peter']['effective'],
                         {'status': 'known', 'value': 'builtin:deepseek', 'source': 'runtime'})
        self.assertEqual(entries['models.background']['configured']['source'], 'saved')
        self.assertEqual(entries['models.background']['effective']['source'], 'startup')
        self.assertEqual(entries['models.background']['apply_mode'], 'restart')
        self.assertIs(entries['models.background']['pending_restart'], True)
        self.assertEqual(entries['models.autonomy']['configured']['value'], 'follow-chat')
        self.assertEqual(entries['models.glance']['configured']['source'], 'settings_default')
        self.assertEqual(entries['models.delegate']['configured']['status'], 'unset')
        for purpose in ('autonomy', 'glance', 'delegate'):
            self.assertEqual(entries[f'models.{purpose}']['effective']['status'], 'unknown')
        self.assertNotIn('private-', json.dumps(result))

    def test_readiness_uses_only_exact_unambiguous_catalog_id_and_boolean_key_presence(self):
        view = model_config_overview(role_id='alpha', chat_model='builtin:deepseek',
                                     background_model='mabcdef', settings={
            'use': {'peter': 'maaaaaa', 'background': 'mbbbbbb', 'glance': 'follow-chat',
                    'delegate': 'mcccccc', 'autonomy': 'mdddddd'},
            'models': [{'id': 'builtin:deepseek', 'key_set': True, 'key_hint': 'do-not-show'},
                       {'id': 'mabcdef', 'key_set': False},
                       {'id': 'mcccccc', 'key_set': 1},
                       {'id': 'mdddddd', 'key_set': True},
                       {'id': 'mdddddd', 'key_set': False}],
        })
        readiness = {entry['key']: entry['readiness'] for entry in view['entries']}
        self.assertEqual(readiness, {'models.peter': 'configured', 'models.background': 'missing',
                                     'models.glance': 'unknown', 'models.delegate': 'unknown',
                                     'models.autonomy': 'unknown'})
        self.assertNotIn('do-not-show', json.dumps(view))

    def test_invalid_or_missing_observations_are_unknown_not_settings_fallback(self):
        view = model_config_overview(role_id='beta', chat_model='https://invalid.example/secret',
                                     settings={'use': {'peter': 'builtin:deepseek', 'background': {}}})
        entries = {entry['key']: entry for entry in view['entries']}
        self.assertEqual(entries['models.peter']['configured']['status'], 'known')
        self.assertEqual(entries['models.peter']['effective']['status'], 'unknown')
        self.assertEqual(entries['models.background']['configured']['status'], 'unknown')
        self.assertIsNone(entries['models.background']['pending_restart'])
        self.assertNotIn('secret', json.dumps(view))
        for entry in model_config_overview(role_id='beta')['entries']:
            self.assertEqual(entry['configured']['status'], 'unknown')
            self.assertEqual(entry['effective']['status'], 'unknown')
