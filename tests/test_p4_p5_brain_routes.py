"""Tests for P4 Brain APIs, P5 strictly read-only health engine, and expanded diagnostics."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import hashlib
import json
import sqlite3

import brain_health
import diagnostics
import request_snapshots
import web_agent


def _build_fixture_db(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE memories(
        id TEXT PRIMARY KEY, title TEXT, name TEXT, created TEXT, last_active TEXT,
        type TEXT, bucket TEXT, importance REAL, pinned INTEGER,
        why_remembered TEXT, body TEXT, body_sha256 TEXT, raw_markdown TEXT,
        vector_state TEXT DEFAULT 'ready', is_tombstone INTEGER DEFAULT 0, is_deleted INTEGER DEFAULT 0
    );
    CREATE TABLE sidecar_state(
        memory_id TEXT PRIMARY KEY, locked INTEGER, links_json TEXT,
        sources_json TEXT, merged_into TEXT, lan_edited TEXT, peter_after_lan TEXT
    );
    CREATE TABLE versions(
        seq INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT, time TEXT,
        operation_reason TEXT, why_remembered TEXT, fields_json TEXT,
        old_content TEXT, old_sha256 TEXT
    );
    CREATE TABLE vectors(memory_id TEXT PRIMARY KEY, dim INTEGER, content_hash TEXT, vector BLOB);
    CREATE TABLE tombstones(memory_id TEXT PRIMARY KEY, deleted_at TEXT, reason_hash TEXT);
    CREATE TABLE delete_requests(request_id TEXT PRIMARY KEY, memory_id TEXT, reason TEXT, status TEXT, created_at TEXT, raw_json TEXT);
    CREATE TABLE write_journal(seq INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, operation TEXT, memory_id TEXT, detail_json TEXT, status TEXT);
    CREATE TABLE events(event_id TEXT PRIMARY KEY, title TEXT, status TEXT, first_at TEXT, last_at TEXT, story_source TEXT, memory_id TEXT, payload_json TEXT);
    CREATE TABLE event_stages(stage_id TEXT PRIMARY KEY, event_id TEXT, at TEXT, text TEXT, facts_json TEXT, ord INTEGER, payload_json TEXT);
    CREATE TABLE event_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, ledger_seq INTEGER, kind TEXT, source_file TEXT, source_seq INTEGER, at TEXT, event_id TEXT, stage_id TEXT, memory_id TEXT, payload_json TEXT);
    """)
    b1 = '雨天一起去旧书店看手绘地图，在二楼窗边待了一下午。'
    s1 = hashlib.sha256(b1.encode('utf-8')).hexdigest()
    conn.execute(
        "INSERT INTO memories(id, title, created, type, bucket, importance, pinned, why_remembered, body, body_sha256, raw_markdown, vector_state) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ('m1', '雨天旧书店', '2026-11-25T14:00:00+08:00', 'memory', 'dynamic', 8, 1, '共同避雨的下午', b1, s1, f'---\nid: m1\n---\n{b1}', 'pending'),
    )
    conn.execute(
        "INSERT INTO sidecar_state VALUES('m1', 1, '[\"missing_id\"]', '[{\"kind\":\"chat\",\"time\":\"2026-11-25T14:05:00\"}]', NULL, NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO versions(memory_id, time, operation_reason, old_content, old_sha256) VALUES('m1', '2026-11-25T15:00:00+08:00', 'Lan 编辑', '旧版内容', 'abc')"
    )
    conn.execute(
        "INSERT INTO events VALUES('ev1', '旧书店避雨', 'active', '2026-11-25', '2026-11-25', 'model', 'm1', '{\"story_pending\": true}')"
    )
    conn.execute(
        "INSERT INTO write_journal(time, operation, memory_id, detail_json, status) VALUES('2026-11-25T14:00:00+08:00', 'hold', 'm1', '{\"title\":\"雨天旧书店\"}', 'saved_index_pending')"
    )
    conn.commit()
    conn.close()


class P4P5BrainRoutesTest(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_root = Path(tmp.name) / 'data'
        self.role_dir = self.data_root / 'peter'
        self.role_dir.mkdir(parents=True)
        self.db_path = self.role_dir / 'brain.db'
        _build_fixture_db(self.db_path)
        self.before_db_bytes = self.db_path.read_bytes()

        # Recall diagnostic row with expanded fields
        with (self.role_dir / 'recall-diagnostics.jsonl').open('w', encoding='utf-8') as fh:
            fh.write(json.dumps({
                'request_id': 'req_1',
                'time': '2026-11-25T16:00:00+08:00',
                'ombre': ['m1'],
                'judge': {'status': 'chosen', 'candidates': 1, 'picked': ['m1']},
                'natural_association': {'picked': [], 'reason': 'skipped_work'},
                'relationship_base': {'items': ['r1'], 'chars': 10},
                'budget': {'space': 8000, 'memory_used': 200},
                'carried': ['u1'],
            }, ensure_ascii=False) + '\n')

        store = SimpleNamespace(root=self.data_root, read_tail=lambda *_a: [])
        cfg = SimpleNamespace(id='peter', short_term_notes=SimpleNamespace(enabled=False))
        auth = SimpleNamespace(db_path=self.db_path)
        memory = SimpleNamespace(brain_authority=auth, judge=SimpleNamespace(status=lambda: {'enabled': True, 'available': True, 'breaker_open': False, 'spent_today': '0.02'}))
        chat = SimpleNamespace(cfg=cfg, store=store, memory=memory, experience=SimpleNamespace(status=lambda: {'enabled': True, 'available': True, 'pending_chars': 120, 'min_chars': 3000, 'spent_today': '0.01'}), profile=SimpleNamespace(status=lambda **_kw: {'available': True, 'bytes': 200, 'max_bytes': 3000}))
        self.hub = SimpleNamespace(name='Peter', ready=True, stopped=False, data_root=self.data_root, chat=chat, memory_tools=None)
        handler = web_agent.make_handler(self.hub, 'tok-test')
        self.req = handler.__new__(handler)
        self.req.server = SimpleNamespace(server_address=('127.0.0.1', 65100))
        self.req.headers = {'Host': '127.0.0.1:65100', 'X-Token': 'tok-test'}
        self.req._send = Mock()

    def test_brain_overview_and_library_serve_real_db_without_mutation(self):
        self.req.path = '/api/brain/overview'
        self.req.do_GET()
        status, overview = self.req._send.call_args.args
        self.assertEqual(200, status)
        self.assertEqual(1, overview['counts']['active'])
        self.assertEqual(1, overview['counts']['pinned'])
        self.assertEqual(1, overview['counts']['pending_vector'])
        self.assertEqual(1, overview['counts']['events_total'])
        self.assertEqual(6, len(overview['layers']))

        self.req.path = '/api/memory/library?q=%E6%97%A7%E4%B9%A6%E5%BA%97&status=locked'
        self.req.do_GET()
        status, lib = self.req._send.call_args.args
        self.assertEqual(200, status)
        self.assertEqual(1, lib['filtered'])
        self.assertEqual('m1', lib['items'][0]['memory_id'])
        self.assertEqual(1, lib['items'][0]['version_count'])
        self.assertEqual(self.before_db_bytes, self.db_path.read_bytes())

    def test_health_scan_is_strictly_read_only_and_exports_sanitized_report(self):
        self.req._post_brain_health_scan({})
        status, payload = self.req._send.call_args.args
        self.assertEqual(200, status)
        self.assertTrue(payload['read_only'])
        report = payload['report']
        ids = {c['id']: c for c in report['checks']}
        self.assertEqual('warn', ids['H-3']['severity'])  # dangling link m1 -> missing_id
        self.assertEqual('warn', ids['H-5']['severity'])  # story_pending=True
        self.assertEqual('warn', ids['H-7']['severity'])  # vector_state='pending'
        self.assertEqual(self.before_db_bytes, self.db_path.read_bytes())

        self.req.path = '/api/brain/health/export?format=markdown'
        self.req.do_GET()
        status, exp = self.req._send.call_args.args
        self.assertEqual(200, status)
        self.assertIn('VEIL Brain', exp['text'])
        self.assertNotIn('在二楼窗边待了一下午', exp['text'])

    def test_health_scan_reports_incomplete_instead_of_passing_unexecuted_checks(self):
        calls = {'n': 0}
        real_guard = brain_health._guard

        def guard(deadline):
            calls['n'] += 1
            if calls['n'] >= 2:
                raise brain_health._ScanTimeout
            return real_guard(deadline)

        with patch.object(brain_health, '_guard', guard):
            report = brain_health.run_health_scan(self.data_root, 'peter', self.db_path, persist=False)
        self.assertTrue(report['incomplete'])
        self.assertTrue(report['timed_out'])
        self.assertIn('H-T', [check['id'] for check in report['checks']])
        self.assertEqual('incomplete', report['summary']['status'])
        self.assertTrue(report['skipped_checks'])
        self.assertFalse(set(report['completed_checks']) & set(report['skipped_checks']))
        self.assertNotIn('H-2', report['completed_checks'])

    def test_health_scan_reports_storage_failure_instead_of_success(self):
        reports = self.role_dir / 'health-reports'
        reports.mkdir()
        (reports / 'health-snapshots.jsonl').mkdir()  # the append must fail loudly
        self.req._post_brain_health_scan({})
        status, payload = self.req._send.call_args.args
        self.assertEqual(500, status)
        self.assertIn('保存', payload['error'])

    def test_health_settings_roundtrip(self):
        self.req._post_brain_health_settings({'enabled': False, 'schedule': 'every_6h'})
        status, body = self.req._send.call_args.args
        self.assertEqual(200, status)
        self.assertFalse(body['settings']['enabled'])
        self.assertEqual('every_6h', body['settings']['schedule'])
    def test_memory_edit_requires_sha_and_archive_uses_the_write_path(self):
        calls = []

        class FixtureTools:
            def panel_edit(self, memory_id, changes):
                calls.append(('edit', memory_id, changes))
                return True, 'saved'

            def panel_archive(self, memory_id):
                calls.append(('archive', memory_id))
                return True, 'archived'

        self.hub.memory_tools = FixtureTools()
        body_sha = hashlib.sha256('雨天一起去旧书店看手绘地图，在二楼窗边待了一下午。'.encode('utf-8')).hexdigest()

        def call_post(path, body):
            raw = json.dumps(body).encode('utf-8')
            self.req.headers['Content-Length'] = str(len(raw))
            self.req.rfile = __import__('io').BytesIO(raw)
            self.req._post_memory_change(SimpleNamespace(path=path))
            return self.req._send.call_args.args

        self.assertEqual(400, call_post('/api/memory/edit', {'id': 'm1', 'changes': {'content': 'x'}})[0])
        status, payload = call_post('/api/memory/edit', {'id': 'm1', 'changes': {'content': 'x'},
                                                         'expected_sha256': '0' * 64})
        self.assertEqual(409, status)
        self.assertTrue(payload['conflict'])
        status, _payload = call_post('/api/memory/edit', {'id': 'm1', 'changes': {'content': 'x'},
                                                          'expected_sha256': body_sha})
        self.assertEqual(200, status)
        self.assertEqual(('edit', 'm1', {'content': 'x'}), calls[-1])
        status, _payload = call_post('/api/memory/archive', {'id': 'm1'})
        self.assertEqual(200, status)
        self.assertEqual(('archive', 'm1'), calls[-1])

    def test_diagnostics_trace_and_snapshot_titles_expose_full_recall_pipeline(self):
        self.assertEqual('关系底座', request_snapshots.title_of('relationship_base'))
        trace = diagnostics.trace(self.data_root, 'peter', request_id='req_1')
        self.assertIn('natural_association', trace['recall'])
        self.assertIn('relationship_base', trace['recall'])
        self.assertIn('budget', trace['recall'])
        self.assertEqual(['u1'], trace['recall']['carried'])
