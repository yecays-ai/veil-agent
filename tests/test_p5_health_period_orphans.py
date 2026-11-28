"""Ticket 461: P5 health inspection — orphans vs dangling, dynamic vector identity, date slices."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from veil.brain import brain_health


def _body(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class HealthPeriodOrphanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'data'
        self.role = self.root / 'peter'
        self.role.mkdir(parents=True)
        self.db = self.role / 'brain.db'
        conn = sqlite3.connect(str(self.db))
        conn.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE memories(
            id TEXT PRIMARY KEY, title TEXT, name TEXT, created TEXT, bucket TEXT,
            type TEXT, importance INTEGER, pinned INTEGER, vector_state TEXT,
            is_tombstone INTEGER DEFAULT 0, is_deleted INTEGER DEFAULT 0,
            body TEXT, body_sha256 TEXT, raw_markdown TEXT, why_remembered TEXT
        );
        CREATE TABLE sidecar_state(memory_id TEXT PRIMARY KEY, locked INTEGER, links_json TEXT,
            sources_json TEXT, merged_into TEXT);
        CREATE TABLE versions(seq INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT,
            time TEXT, old_content TEXT);
        CREATE TABLE vectors(memory_id TEXT PRIMARY KEY, dim INTEGER, content_hash TEXT, vector BLOB);
        CREATE TABLE tombstones(memory_id TEXT PRIMARY KEY, deleted_at TEXT);
        CREATE TABLE events(event_id TEXT PRIMARY KEY, title TEXT, first_at TEXT, last_at TEXT,
            memory_id TEXT, payload_json TEXT);
        CREATE TABLE event_sources(event_id TEXT, ord INTEGER, memory_id TEXT);
        CREATE TABLE event_stages(stage_id TEXT PRIMARY KEY, event_id TEXT, at TEXT);
        CREATE TABLE event_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, ledger_seq INTEGER,
            kind TEXT, event_id TEXT, payload_json TEXT);
        CREATE TABLE quotes(memory_id TEXT, ord INTEGER, speaker TEXT, text TEXT);
        CREATE TABLE sources(memory_id TEXT, ord INTEGER, kind TEXT, ref TEXT, status TEXT);
        CREATE TABLE relations(memory_id TEXT, ord INTEGER, target_id TEXT, type TEXT);
        """)
        conn.commit()
        conn.close()

    def add_memory(self, mid, created, body, *, bucket='dynamic', vector_state='ready', title=None):
        conn = sqlite3.connect(str(self.db))
        conn.execute(
            "INSERT INTO memories(id, title, created, bucket, type, importance, pinned, vector_state, "
            "body, body_sha256, raw_markdown) VALUES(?,?,?,?, 'fact', 5, 0,?,?,?,?)",
            (mid, title or mid, created, bucket, vector_state, body, _body(body), body))
        conn.commit()
        conn.close()

    def add_vector(self, mid, dim, *, fill=0.1):
        import array
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO vectors(memory_id, dim, content_hash, vector) VALUES(?,?,NULL,?)",
                     (mid, dim, array.array('f', [fill] * dim).tobytes()))
        conn.commit()
        conn.close()

    def run_scan(self, **kwargs):
        return brain_health.run_health_scan(self.root, 'peter', self.db, persist=False, **kwargs)

    def check(self, report, cid):
        return next(row for row in report['checks'] if row['id'] == cid)

    def test_isolated_records_are_reported_separately_from_dangling_refs(self):
        self.add_memory('m_iso', '2026-11-20T10:00:00+08:00', '一条没有事件、来源与引述的记忆。')
        self.add_memory('m_link', '2026-11-20T11:00:00+08:00', '一条带事件的记忆。')
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO sidecar_state VALUES('m_link', 0, '[\"gone\"]', '[]', NULL)")
        conn.execute("INSERT INTO events VALUES('ev_link', '带事件的记忆', '2026-11-20', '2026-11-20', 'm_link', '{}')")
        conn.execute("INSERT INTO event_stages VALUES('st1', 'ev_link', '2026-11-20T11:05:00')")
        conn.execute("INSERT INTO events VALUES('ev_iso', '孤立事件', '2026-11-21', '2026-11-21', NULL, '{}')")
        conn.commit()
        conn.close()
        before = self.db.read_bytes()
        report = self.run_scan()
        check = self.check(report, 'H-3')
        self.assertEqual('warn', check['severity'])
        self.assertEqual(1, check['counts']['dangling'])
        self.assertEqual(1, check['counts']['unlinked_memories'])
        self.assertEqual(1, check['counts']['unlinked_events'])
        categories = {ev.get('category') for ev in check['evidences']}
        self.assertEqual({'dangling', 'unlinked'}, categories)
        self.assertTrue(all(ev.get('basis') for ev in check['evidences']))
        self.assertEqual('unlinked', next(ev for ev in check['evidences'] if ev.get('memId') == 'm_iso')['category'])
        self.assertEqual('unlinked', next(ev for ev in check['evidences'] if ev.get('eventId') == 'ev_iso')['category'])
        self.assertEqual(before, self.db.read_bytes())  # strictly read-only

    def test_vector_identity_uses_stored_dimensions_and_model_not_1024(self):
        self.add_memory('m1', '2026-11-20T10:00:00+08:00', '768 维记忆正文。')
        self.add_vector('m1', 768)
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO meta VALUES('embedding_model', 'emb-768')")
        conn.commit()
        conn.close()
        report = self.run_scan(configured_embedding_model='emb-768')
        check = self.check(report, 'H-7')
        self.assertEqual('good', check['severity'])
        self.assertEqual({'768': 1}, report['stats']['vector_dimensions'])
        self.assertFalse(check['vector_identity']['mixed_dimensions'])
        self.assertNotIn('1024', json.dumps(check, ensure_ascii=False))
        self.assertEqual('emb-768', report['stats']['embedding_model'])

    def test_mixed_dimension_and_model_mismatch_are_explained_without_false_1024_claim(self):
        self.add_memory('m1', '2026-11-20T10:00:00+08:00', '旧模型向量。')
        self.add_memory('m2', '2026-11-20T11:00:00+08:00', '新模型向量。')
        self.add_vector('m1', 768)
        self.add_vector('m2', 1536)
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO meta VALUES('embedding_model', 'emb-old')")
        conn.commit()
        conn.close()
        report = self.run_scan(configured_embedding_model='emb-new')
        check = self.check(report, 'H-7')
        self.assertEqual('warn', check['severity'])
        notes = ' '.join(ev['note'] for ev in check['evidences'])
        self.assertIn('维度', notes)
        self.assertIn('1536', notes)
        self.assertIn('模型身份不一致', notes)
        self.assertNotIn('≠ 1024', json.dumps(report, ensure_ascii=False))

    def test_different_vector_lengths_are_not_near_duplicates(self):
        import array
        self.assertEqual(0.0, brain_health._cosine([1.0, 0.0], [1.0]))
        self.add_memory('dim_a', '2026-11-20T10:00:00+08:00', '向量甲的正文足够区分，不会被当成碎片。')
        self.add_memory('dim_b', '2026-11-20T11:00:00+08:00', '向量乙的正文也不相同，同样不是碎片。')
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO vectors(memory_id, dim, content_hash, vector) VALUES('dim_a', 2, NULL, ?)",
                     (array.array('f', [1.0, 0.0]).tobytes(),))
        conn.execute("INSERT INTO vectors(memory_id, dim, content_hash, vector) VALUES('dim_b', 1, NULL, ?)",
                     (array.array('f', [1.0]).tobytes(),))
        conn.commit()
        conn.close()
        notes = ' '.join(ev.get('note', '') for ev in self.check(self.run_scan(), 'H-2')['evidences'])
        self.assertNotIn('余弦', notes)

    def test_vector_bytes_mismatch_is_reported_against_its_own_dimension(self):
        self.add_memory('m1', '2026-11-20T10:00:00+08:00', '字节数不符。')
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO vectors(memory_id, dim, content_hash, vector) VALUES('m1', 768, ?, ?)",
                     (_body('x'), b'\x00' * 512))
        conn.commit()
        conn.close()
        report = self.run_scan()
        notes = ' '.join(ev['note'] for ev in self.check(report, 'H-7')['evidences'])
        self.assertIn('512', notes)
        self.assertIn('768', notes)

    def test_period_slice_reports_growth_duplicates_fragments_links_and_index(self):
        # In range: duplicate pair, fragment, dangling sidecar, pending vector, event with broken link.
        self.add_memory('p_dup1', '2026-11-10T10:00:00+08:00', '同一段重复正文，足够长以避免碎片判断。', vector_state='ready')
        self.add_memory('p_dup2', '2026-11-11T10:00:00+08:00', '同一段重复正文，足够长以避免碎片判断。', vector_state='ready')
        self.add_memory('p_frag', '2026-11-12T10:00:00+08:00', '短短', vector_state='pending')
        self.add_vector('p_dup1', 1024)
        self.add_vector('p_dup2', 1024)
        self.add_memory('outside', '2026-10-01T10:00:00+08:00', '范围外的记忆。', vector_state='ready')
        self.add_vector('outside', 1024)
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO sidecar_state VALUES('p_dup1', 0, '[\"missing_target\"]', '[]', NULL)")
        conn.execute("INSERT INTO events VALUES('ev_bad', '期间事件', '2026-11-13', '2026-11-13', 'deleted_mem', '{}')")
        conn.execute("INSERT INTO event_stages VALUES('st_bad', 'ev_bad', '2026-11-13T10:00:00')")
        conn.execute("INSERT INTO versions(memory_id, time, old_content) VALUES('p_dup1', '2026-11-13T10:00:00+08:00', '旧')")
        conn.commit()
        conn.close()
        versions = self.role / 'learned-context.versions.jsonl'
        versions.write_text(json.dumps({'time': '2026-11-14T10:00:00+08:00', 'tool': 'save_facts'}) + '\n',
                            encoding='utf-8')
        report = self.run_scan(from_date='2026-11-10', to_date='2026-11-20')
        period = report['period']
        self.assertTrue(period['requested'])
        self.assertEqual('available', period['status'])
        self.assertEqual(3, period['growth']['memories_created'])
        self.assertEqual(1, period['growth']['events_created'])
        self.assertEqual(1, period['growth']['versions'])
        self.assertEqual(1, period['growth']['learned_context_changes'])
        self.assertGreaterEqual(period['duplicates']['count'], 1)
        self.assertGreaterEqual(period['fragments']['count'], 1)
        self.assertGreaterEqual(period['dangling']['count'], 1)
        self.assertGreaterEqual(period['index_anomalies']['count'], 1)
        self.assertFalse(period['historical_state']['available'])
        self.assertEqual('no_snapshot_in_range', period['historical_state']['reason'])
        self.assertEqual('current+range', self.check(report, 'H-1')['scope'])
        self.assertEqual('range', self.check(report, 'H-8')['scope'])
        self.assertEqual('current', self.check(report, 'H-9')['scope'])
        self.assertEqual('全库当前 + 指定期间', report['scope']['label'])
        # The out-of-range memory must not be counted in the period growth.
        self.assertNotIn('outside', json.dumps(period, ensure_ascii=False))
        text = brain_health.export_report(report)
        self.assertIn('指定期间变化', text)
        self.assertIn('历史快照：缺失', text)
        self.assertIn('全库当前状态检查明细', text)
        self.assertNotIn('范围外的记忆。', text)

    def test_health_probe_reports_cache_empty_hint_and_p7_slot_usage(self):
        rows = [
            {'time': '2026-11-28T09:00:00+08:00', 'request_id': 'r1',
             'kind': 'tool_step_context', 'sent': True, 'actual_input_tokens': 1000,
             'cached_tokens': 700, 'empty_memory_notices_omitted': 0,
             'p7_experience_hints': {'status': 'injected'}},
            {'time': '2026-11-28T09:00:01+08:00', 'request_id': 'r1',
             'kind': 'tool_step_context', 'sent': True, 'actual_input_tokens': 1100,
             'cached_tokens': 800, 'empty_memory_notices_omitted': 1},
            {'time': '2026-11-28T09:00:00+08:00', 'request_id': 'r1',
             'kind': 'chat', 'sent': False,
             'p7_experience_hints': {'status': 'injected'}},
        ]
        (self.role / 'context-log.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

        impact = self.check(self.run_scan(), 'H-8')['impact']

        self.assertIn('缓存用量已报告 2 次（1500/2100 tokens）', impact)
        self.assertIn('省略空记忆提示 1 块', impact)
        self.assertIn('P7 槽位注入 1 次', impact)

    def test_health_range_reads_context_records_beyond_the_latest_600(self):
        rows = [{'time': '2026-11-10T10:00:00+08:00', 'request_id': 'old',
                 'kind': 'chat', 'sent': True, 'actual_input_tokens': 1000,
                 'cached_tokens': 700,
                 'p7_experience_hints': {'status': 'injected'}}]
        for index in range(610):
            rows.append({'time': '2026-11-27T10:00:00+08:00', 'request_id': 'new-%d' % index,
                         'kind': 'chat', 'sent': True, 'actual_input_tokens': 10,
                         'cached_tokens': 5})
        (self.role / 'context-log.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

        impact = self.check(self.run_scan(from_date='2026-11-10', to_date='2026-11-11'), 'H-8')['impact']

        self.assertIn('实际发送 1 次', impact)
        self.assertIn('缓存用量已报告 1 次（700/1000 tokens）', impact)
        self.assertIn('P7 槽位注入 1 次', impact)

    def test_health_probe_marks_h8_unavailable_when_context_log_cannot_be_read(self):
        (self.role / 'context-log.jsonl').write_text('', encoding='utf-8')
        real_open = Path.open

        def failing_open(inner_self, *args, **kwargs):
            if inner_self.name == 'context-log.jsonl':
                raise OSError('fictional permission failure')
            return real_open(inner_self, *args, **kwargs)

        with mock.patch.object(Path, 'open', failing_open):
            report = self.run_scan()

        check = self.check(report, 'H-8')
        self.assertEqual(check['severity'], 'warn')
        self.assertIn('不可用', check['status_label'])
        self.assertIn('context-log.jsonl', check['impact'])
        self.assertNotIn('实际发送 0 次', check['impact'])

    def test_period_uses_existing_snapshots_and_marks_coverage(self):
        self.add_memory('m1', '2026-11-10T10:00:00+08:00', '记忆正文。')
        reports = self.role / 'health-reports'
        reports.mkdir()
        rows = [
            {'day': '2026-11-10', 'scanned_at': '2026-11-10T02:00:00+0800',
             'active_memories': 1, 'events_total': 0, 'warn_count': 0},
            {'day': '2026-11-20', 'scanned_at': '2026-11-20T02:00:00+0800',
             'active_memories': 4, 'events_total': 2, 'warn_count': 1},
        ]
        (reports / 'health-snapshots.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        report = self.run_scan(from_date='2026-11-10', to_date='2026-11-20')
        hist = report['period']['historical_state']
        self.assertTrue(hist['available'])
        self.assertEqual(2, hist['observations'])
        self.assertEqual(3, hist['delta']['active_memories'])
        self.assertEqual(2, hist['delta']['events_total'])

    def test_scan_without_range_keeps_period_not_requested(self):
        self.add_memory('m1', '2026-11-10T10:00:00+08:00', '记忆正文。')
        report = self.run_scan()
        self.assertFalse(report['period']['requested'])
        self.assertEqual('not_requested', report['period']['status'])
        self.assertEqual('current', self.check(report, 'H-1')['scope'])
        self.assertEqual('全库当前', report['scope']['label'])

    def test_event_sources_and_sidecar_sources_are_links(self):
        self.add_memory('m_evt', '2026-11-20T10:00:00+08:00', '只通过事件来源关联的记忆。')
        self.add_memory('m_side', '2026-11-20T11:00:00+08:00', '只通过 sidecar 来源关联的记忆。')
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO events VALUES('ev_src', '来源事件', '2026-11-20', '2026-11-20', NULL, '{}')")
        conn.execute("INSERT INTO event_sources VALUES('ev_src', 0, 'm_evt')")
        conn.execute("INSERT INTO sidecar_state VALUES('m_side', 0, '[]', ?, NULL)",
                     (json.dumps(['chat:u1']),))
        conn.commit()
        conn.close()
        check = self.check(self.run_scan(), 'H-3')
        linked = {ev.get('memId') for ev in check['evidences'] if ev.get('category') == 'unlinked'}
        self.assertNotIn('m_evt', linked)
        self.assertNotIn('m_side', linked)
        self.assertEqual(0, check['counts']['unlinked_memories'])
        self.assertEqual(0, check['counts']['unlinked_events'])

    def test_deleted_memory_still_counts_as_created_in_the_period(self):
        self.add_memory('gone', '2026-11-12T10:00:00+08:00', '期间创建，后来删除。')
        self.add_memory('live', '2026-11-13T10:00:00+08:00', '期间创建，仍然在库。')
        conn = sqlite3.connect(str(self.db))
        conn.execute("UPDATE memories SET is_deleted=1 WHERE id='gone'")
        conn.commit()
        conn.close()
        period = self.run_scan(from_date='2026-11-10', to_date='2026-11-20')['period']
        self.assertEqual(2, period['growth']['memories_created'])

    def test_single_snapshot_delta_is_unknown_and_incomplete_snapshots_are_ignored(self):
        self.add_memory('m1', '2026-11-10T10:00:00+08:00', '记忆正文。')
        reports = self.role / 'health-reports'
        reports.mkdir()
        rows = [
            {'day': '2026-11-10', 'scanned_at': '2026-11-10T02:00:00+0800',
             'active_memories': 0, 'events_total': 0, 'incomplete': True},
            {'day': '2026-11-20', 'scanned_at': '2026-11-20T02:00:00+0800',
             'active_memories': 4, 'events_total': 2},
        ]
        (reports / 'health-snapshots.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        report = self.run_scan(from_date='2026-11-10', to_date='2026-11-20')
        hist = report['period']['historical_state']
        self.assertTrue(hist['available'])
        self.assertFalse(hist['delta_known'])
        self.assertEqual(1, hist['observations'])
        self.assertEqual('single_complete_snapshot', hist['reason'])
        self.assertIsNone(hist['delta']['active_memories'])
        self.assertEqual(1, hist['incomplete_snapshots_ignored'])
        text = brain_health.export_report(report)
        self.assertIn('变化未知', text)
        self.assertNotIn('活跃记忆变化 0', text)

    def test_snapshot_older_than_the_recent_tail_is_still_in_range(self):
        self.add_memory('m1', '2026-01-02T10:00:00+08:00', '很早的记忆。')
        reports = self.role / 'health-reports'
        reports.mkdir()
        rows = [{
            'day': '2026-01-02', 'scanned_at': '2026-01-02T02:00:00+0800',
            'active_memories': 1, 'events_total': 0,
        }]
        rows.extend({
            'day': '2026-06-01', 'scanned_at': '2026-06-01T02:00:00+0800',
            'active_memories': 9, 'events_total': 9,
        } for _ in range(400))
        (reports / 'health-snapshots.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        learned = [{
            'time': '2026-01-02T03:00:00+08:00', 'tool': 'save_facts',
        }]
        learned.extend({'time': '2026-06-02T03:00:00+08:00', 'tool': 'save_facts'} for _ in range(20))
        (self.role / 'learned-context.versions.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in learned), encoding='utf-8')
        period = self.run_scan(from_date='2026-01-01', to_date='2026-01-31')['period']
        hist = period['historical_state']
        self.assertTrue(hist['available'])
        self.assertFalse(hist['delta_known'])
        self.assertEqual(1, hist['observations'])
        self.assertEqual('single_complete_snapshot', hist['reason'])
        self.assertEqual(1, period['growth']['learned_context_changes'])

    def test_two_complete_snapshots_ignore_an_incomplete_zero(self):
        self.add_memory('m1', '2026-11-10T10:00:00+08:00', '记忆正文。')
        reports = self.role / 'health-reports'
        reports.mkdir()
        rows = [
            {'day': '2026-11-10', 'scanned_at': '2026-11-10T02:00:00+0800',
             'active_memories': 1, 'events_total': 0},
            {'day': '2026-11-15', 'scanned_at': '2026-11-15T02:00:00+0800',
             'active_memories': 0, 'events_total': 0, 'incomplete': True},
            {'day': '2026-11-20', 'scanned_at': '2026-11-20T02:00:00+0800',
             'active_memories': 4, 'events_total': 2},
        ]
        (reports / 'health-snapshots.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        hist = self.run_scan(from_date='2026-11-10', to_date='2026-11-20')['period']['historical_state']
        self.assertTrue(hist['delta_known'])
        self.assertEqual(3, hist['delta']['active_memories'])
        self.assertEqual(1, hist['incomplete_snapshots_ignored'])

    def test_incomplete_period_export_does_not_print_zeros(self):
        text = brain_health.export_report({
            'summary': {'status_label': '未完成', 'good_count': 0, 'warn_count': 1},
            'from_date': '2026-11-01', 'to_date': '2026-11-02',
            'scope': {'label': '全库当前 + 指定期间'},
            'period': {
                'requested': True, 'status': 'incomplete',
                'growth': {'memories_created': 0, 'events_created': 0, 'event_stages': 0,
                           'versions': 0, 'learned_context_changes': 0},
                'duplicates': {'count': 0}, 'fragments': {'count': 0},
                'dangling': {'count': 0}, 'index_anomalies': {'count': 0},
                'historical_state': {'available': False, 'reason': 'no_snapshot_in_range'},
            },
        })
        self.assertIn('未完成', text)
        self.assertIn('不判断有没有快照', text)
        self.assertNotIn('期间新增记忆：0', text)
        self.assertNotIn('历史快照：缺失', text)


if __name__ == '__main__':
    unittest.main()
