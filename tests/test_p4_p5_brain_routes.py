"""Tests for P4 Brain APIs, P5 strictly read-only health engine, and expanded diagnostics."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import hashlib
import json
import sqlite3

from veil.brain import brain_health
from veil.system import diagnostics
from veil.system import request_snapshots
from veil.server import web_agent


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

    def test_missing_database_scan_replaces_a_stale_healthy_report(self):
        reports = self.role_dir / 'health-reports'
        reports.mkdir()
        (reports / 'latest.json').write_text(json.dumps(
            {'schema_version': 1, 'summary': {'status': 'good'}, 'checks': []}), encoding='utf-8')
        brain_health.run_health_scan(self.data_root, 'peter', self.role_dir / 'missing.db')
        latest = json.loads((reports / 'latest.json').read_text(encoding='utf-8'))
        self.assertEqual('H-0', latest['checks'][0]['id'])
        self.assertFalse(latest['incomplete'])

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
            def panel_edit(self, memory_id, changes, expected_sha256=''):
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

    def test_growth_analysis_lifecycle_and_invalid_tx_filtering(self):
        from datetime import datetime, timedelta
        from veil.brain import brain_p4_api
        today = datetime.now().date().strftime('%Y-%m-%d')
        yday = (datetime.now().date() - timedelta(days=1)).strftime('%Y-%m-%d')

        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO memories(id, title, created, type, bucket, body, is_deleted) VALUES('m_del', '已删记忆', ?, 'fact', 'dynamic', '正文', 1)",
            (f'{yday}T10:00:00+08:00',),
        )
        conn.execute(
            "INSERT INTO memories(id, title, created, type, bucket, body) VALUES('m_arch', '归档记忆', ?, 'fact', 'archive', '正文')",
            (f'{yday}T11:00:00+08:00',),
        )
        conn.execute(
            "INSERT INTO write_journal(time, operation, memory_id, detail_json, status) VALUES(?, 'archive', 'm_arch', '{}', 'ok')",
            (f'{today}T09:00:00+08:00',),
        )
        conn.execute(
            "INSERT INTO write_journal(time, operation, memory_id, detail_json, status) VALUES(?, 'restore', 'm1', '{}', 'ok')",
            (f'{today}T09:30:00+08:00',),
        )
        conn.execute(
            "INSERT INTO write_journal(time, operation, memory_id, detail_json, status) VALUES(?, 'delete', 'm_del', '{}', 'ok')",
            (f'{today}T10:00:00+08:00',),
        )
        # Failed transaction must be excluded
        conn.execute(
            "INSERT INTO write_journal(time, operation, memory_id, detail_json, status) VALUES(?, 'archive', 'm1', '{}', 'failed')",
            (f'{today}T10:30:00+08:00',),
        )
        conn.commit()
        conn.close()

        res = brain_p4_api.growth_analysis(self.hub, range_key='7d')
        s = res['summary']
        self.assertEqual(3, s['total_created'])
        self.assertEqual(1, s['total_active'])
        self.assertEqual(1, s['total_archived'])
        self.assertEqual(1, s['total_deleted'])
        self.assertEqual(1, s['actual_archived'])
        self.assertEqual(1, s['actual_restored'])
        self.assertEqual(1, s['actual_deleted'])
        self.assertFalse(s['history_incomplete'])
        self.assertEqual(s['actual_added'] + s['actual_restored'] - s['actual_archived'] - s['actual_deleted'], s['net_growth'])

    def test_growth_report_no_silent_truncation_and_event_only_day_and_health_date_match(self):
        from datetime import datetime
        from veil.brain import brain_p4_api
        today = datetime.now().date().strftime('%Y-%m-%d')
        long_body = '这是一段超过六百字的长记忆正文。' * 50  # > 700 chars
        conn = sqlite3.connect(str(self.db_path))
        for i in range(25):
            conn.execute(
                "INSERT INTO memories(id, title, created, type, bucket, body) VALUES(?, ?, ?, 'fact', 'dynamic', ?)",
                (f'm_bulk_{i:02d}', f'批量记忆_{i:02d}', f'{today}T10:{i:02d}:00+08:00', long_body if i == 0 else f'正文{i}'),
            )
        conn.execute(
            "INSERT INTO event_ledger(ledger_seq, kind, source_file, source_seq, at, event_id, stage_id, memory_id, payload_json) "
            "VALUES(10, 'event-update', 'events.jsonl', 10, ?, 'ev1', 'st_9', 'm1', '{\"title\":\"旧书店避雨\",\"summary\":\"补充阶段\"}')",
            (f'{today}T18:00:00+08:00',),
        )
        conn.commit()
        conn.close()

        # Run a health scan with a DIFFERENT date range to verify mismatch warning
        brain_health.run_health_scan(self.data_root, 'peter', self.db_path, from_date='2026-01-01', to_date='2026-01-07')

        rep_full = brain_p4_api.generate_growth_report(
            self.hub, from_date=today, to_date=today, include_content=True, include_health=True, fmt='full',
        )
        md = rep_full['markdown']
        self.assertIn('批量记忆_24', md)  # > 20 items not truncated
        self.assertIn(long_body, md)     # > 600 chars not truncated
        self.assertIn('ev1', md)
        self.assertIn('日期范围不匹配', md)
        self.assertIn('关注', md)

        # include_content=False still retains non-content technical diagnostic details
        rep_no_content = brain_p4_api.generate_growth_report(
            self.hub, from_date=today, to_date=today, include_content=False, include_health=False, fmt='full',
        )
        self.assertIn('m_bulk_24', rep_no_content['markdown'])
        self.assertNotIn(long_body, rep_no_content['markdown'])

    def test_recall_turns_over_100_direct_report_and_missing_evidence_and_costs_amount(self):
        from veil.brain import brain_p4_api
        snap_path = diagnostics.snapshot_path(self.data_root, 'peter')
        with snap_path.open('w', encoding='utf-8') as fh:
            for i in range(120):
                row = {
                    'request_id': f'req_{i:03d}',
                    'time': f'2026-11-25T10:{i // 60:02d}:{i % 60:02d}+08:00',
                    'model': 'gemini-2.5-pro',
                    'purpose': 'chat',
                    'user_preview': f'问题 {i}',
                }
                if i == 0:
                    # Missing recall & missing cache evidence
                    pass
                else:
                    row['recall'] = {'injected': [{'memory_id': 'm1', 'title': '雨天旧书店'}], 'candidates': [{'memory_id': 'm1', 'title': '雨天旧书店'}, {'memory_id': 'm2', 'title': '候选2'}]}
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')

        with (self.data_root / 'costs.jsonl').open('w', encoding='utf-8') as fh:
            fh.write(json.dumps({'id': 'req_000', 'type': 'settle', 'amount': 0.0456}, ensure_ascii=False) + '\n')

        # Direct lookup for req_000 (which is #120 in reverse-time order, beyond page 1 / top 100)
        rep = brain_p4_api.recall_turn_report(self.hub, 'req_000')
        self.assertTrue(rep['ok'])
        turn = rep['turn']
        self.assertIsNone(turn['tokens']['cache_hit'])
        self.assertEqual('missing', turn['recall_evidence_state'])
        self.assertEqual('missing', turn['recall_chain'][0]['state'])
        self.assertAlmostEqual(0.0456, turn['cost_cny'], places=4)
        self.assertIn('未记录', rep['markdown'])
        self.assertIn('召回追踪证据缺失', rep['markdown'])

        # Missing drop_reason must not guess '预算/相关性筛选'
        rep1 = brain_p4_api.recall_turn_report(self.hub, 'req_001')
        self.assertIn('未记录未入选原因', rep1['markdown'])
        self.assertNotIn('预算/相关性筛选', rep1['markdown'])

    def test_layer_toggle_persistence_and_shared_budget_consistency(self):
        from veil.brain import brain_p4_api
        # short_notes persists to brain-layer-switches.json and survives reload
        res = brain_p4_api.toggle_layer(self.hub, 'short_notes', True)
        self.assertTrue(res['ok'])
        self.assertTrue(res['persisted'])
        ov = brain_p4_api.overview(self.hub)
        sn = next(l for l in ov['layers'] if l['id'] == 'short_notes')
        self.assertTrue(sn['enabled'])

        # Unready experience module without set_enabled returns real error
        res_err = brain_p4_api.toggle_layer(self.hub, 'experience', False)
        self.assertFalse(res_err['ok'])
        self.assertEqual(503, res_err['status_code'])

        # Shared budget authority matches UTF-8 calculation
        learned = {
            'max_bytes': 4000,
            'sections': {'facts': {'text': '事实A'}, 'growth': {'text': '成长B'}},
            'ongoing': {'items': [{'text': '进行中', 'status': 'active'}, {'text': '已完成', 'status': 'done'}]},
            'relationship': {'items': [{'text': '关系1', 'status': 'confirmed'}, {'text': '旧关系', 'status': 'outdated'}]},
        }
        b = brain_p4_api._compute_cognition_budget(learned)
        expected = sum(len(s.encode('utf-8')) for s in ('事实A', '成长B', '进行中', '关系1'))
        self.assertEqual(expected, b['used_bytes'])
        # Strictly read-only DB invariant still holds
        self.assertEqual(self.before_db_bytes, self.db_path.read_bytes())
