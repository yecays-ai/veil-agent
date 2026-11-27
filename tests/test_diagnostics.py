"""Ticket 432: read-only diagnostics (fictional data only, no model, no network)."""
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from veil.system import diagnostics
from veil.system.diagnostics import (classify_denial, config_findings, links, module_states, overview,
                         recent_issues, report_text)


def iso(minutes_ago=0):
    return (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')


def write_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    return path


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


class Base(unittest.TestCase):
    def setUp(self):
        # mkdtemp() creates 0700 folders that some restricted Windows sandboxes
        # cannot write into; a plain mkdir under the same temp root always works.
        base = Path(os.environ.get('VEIL_TEST_TMPDIR') or tempfile.gettempdir())
        self.root = base / ('veil-diag-' + uuid.uuid4().hex[:10])
        self.root.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.role = 'test'

    def tool_row(self, **kwargs):
        row = {'time': iso(5), 'tool': kwargs.pop('tool', 'peek_screen'),
               'path': 'x', 'state': kwargs.pop('state', '被拒绝'),
               'request_id': kwargs.pop('request_id', 'req-1')}
        row.update(kwargs)
        return row


class ReaderTests(Base):
    def test_a_torn_last_line_is_ignored_instead_of_raising(self):
        path = self.root / 'tools.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'n': 1}) + '\n' + json.dumps({'n': 2}) + '\n{"n": 3',
                         encoding='utf-8')
        rows = diagnostics._rows(path, 10)
        self.assertEqual([row['n'] for row in rows], [1, 2])

    def test_missing_files_read_as_empty_lists(self):
        self.assertEqual(diagnostics._rows(self.root / 'nope.jsonl', 5), [])
        self.assertIsNone(diagnostics._json(self.root / 'nope.json', None))


class DenialTests(Base):
    def test_pending_approval_uses_the_wake_id_and_says_so(self):
        write_json(diagnostics.approvals_path(self.root, self.role),
                   {'version': 1, 'items': [{'id': 'a1', 'run_id': 'wake-1', 'status': 'pending',
                                             'prompt': 'may I', 'created_at': iso(4)}]})
        row = self.tool_row(autonomy_run_id='wake-1', detail='')
        result = classify_denial(row, diagnostics._approvals(self.root, self.role))
        self.assertEqual((result['code'], result['subclass']), ('pending', 'waiting'))
        self.assertEqual(result['confidence'], 'exact')
        self.assertIn('run_id 相同', result['evidence'])

    def test_a_denial_without_any_reason_stays_unknown(self):
        result = classify_denial(self.tool_row(detail=''), [])
        self.assertEqual(result['code'], 'unknown')
        self.assertIn('没有原因文本', result['evidence'])
        self.assertEqual(result['confidence'], 'none')

    def test_a_different_wake_is_never_matched_by_time(self):
        write_json(diagnostics.approvals_path(self.root, self.role),
                   {'version': 1, 'items': [{'id': 'a1', 'run_id': 'other-wake',
                                             'status': 'pending', 'prompt': 'x',
                                             'created_at': iso(5)}]})
        result = classify_denial(self.tool_row(autonomy_run_id='wake-2', detail=''),
                                 diagnostics._approvals(self.root, self.role))
        self.assertEqual(result['code'], 'unknown')

    def test_replay_guard_and_arguments_are_told_apart(self):
        replay = self.tool_row(tool='write_file', detail=(
            'This side effect was already completed, rejected, stopped, or has an unknown '
            'outcome; do not replay it automatically.'))
        self.assertEqual(classify_denial(replay, [])['code'], 'safety')
        args = self.tool_row(detail='The program rejected this call because the arguments '
                                    'are not a valid JSON object.')
        self.assertEqual(classify_denial(args, [])['code'], 'args')

    def test_a_successful_row_is_not_classified(self):
        self.assertIsNone(classify_denial(self.tool_row(state='成功'), []))


class ConfigTests(Base):
    def inventory(self, over, rows, always_on=1000, limit=36000):
        return {'rows': rows, 'always_on_bytes': always_on,
                'budget': {'always_on': limit, 'tool': 1500, 'group': 12000}, 'over': over}

    def test_a_group_over_budget_becomes_a_finding_with_its_tools(self):
        inventory = self.inventory(
            [{'kind': 'group', 'group': 'garden_more', 'bytes': 15911, 'limit': 12000}],
            [{'tool': 'mcp__galatea_garden__drift_bottle', 'group': 'garden_more'},
             {'tool': 'hold', 'group': None}])
        findings = config_findings(inventory)
        finding = next(item for item in findings if item['id'] == 'budget-group-garden_more')
        self.assertIn('花园其他', finding['title'])
        self.assertIn('超出', finding['detail'])
        self.assertEqual(finding['tools'], ['mcp__galatea_garden__drift_bottle'])
        self.assertEqual(finding['level'], 'warning')

    def test_no_inventory_is_unknown_and_never_healthy(self):
        findings = config_findings(None)
        self.assertEqual(findings[0]['id'], 'budget-unknown')
        self.assertEqual(findings[0]['level'], 'unknown')

    def test_always_on_near_the_limit_is_a_notice(self):
        findings = config_findings(self.inventory([], [], always_on=33934, limit=36000))
        self.assertIn('budget-always-on-near', [item['id'] for item in findings])

    def test_a_missing_model_key_is_reported_without_reading_it(self):
        cfg = SimpleNamespace(provider={'model': 'fake', 'base_url': 'https://example.invalid',
                                        'api_key_env': 'VEIL_TEST_KEY_THAT_IS_NOT_SET'})
        chat = SimpleNamespace(cfg=cfg)
        findings = config_findings(self.inventory([], []), chat=chat)
        self.assertIn('model-key-missing', [item['id'] for item in findings])


class IssueTests(Base):
    def test_provider_errors_merge_and_old_rows_are_unconfirmed(self):
        write_rows(diagnostics.provider_path(self.root, self.role), [
            {'time': iso(30), 'model': 'm', 'status': 503, 'type': 'InternalServerError',
             'detail': 'relay down'},
            {'time': iso(10), 'model': 'm', 'status': 401, 'type': 'AuthenticationError',
             'detail': 'bad key', 'request_id': 'req-9', 'kind': 'autonomy', 'run_id': 'wake-9'}])
        rows = recent_issues(self.root, self.role, hours=72)
        old = next(row for row in rows if row['request_id'] == '')
        new = next(row for row in rows if row['request_id'] == 'req-9')
        self.assertFalse(old['confirmed'])
        self.assertIn('旧记录', old['note'])
        self.assertTrue(new['confirmed'])
        self.assertEqual(new['run_id'], 'wake-9')
        self.assertEqual(new['kind'], 'model_error')

    def test_a_later_success_on_the_same_request_confirms_recovery(self):
        write_rows(diagnostics.provider_path(self.root, self.role), [
            {'time': iso(20), 'model': 'm', 'status': 503, 'type': 'X', 'detail': 'down',
             'request_id': 'req-1'}])
        write_rows(diagnostics.context_path(self.root, self.role), [
            {'time': iso(5), 'request_id': 'req-1', 'sent': True, 'usage_status': 'reported',
             'kind': 'chat'}])
        rows = recent_issues(self.root, self.role, hours=72)
        self.assertEqual(rows[0]['recovery'], 'confirmed')

    def test_autonomy_failure_and_budget_pose_are_kept_apart(self):
        write_rows(diagnostics.autonomy_path(self.root, self.role), [
            {'started_at': iso(60), 'finished_at': iso(50), 'result': '失败',
             'result_reason': '请求失败，接口返回 HTTP 401'},
            {'started_at': iso(40), 'finished_at': iso(35), 'result': '搁置',
             'result_reason': '后台预算不足'}])
        rows = recent_issues(self.root, self.role, hours=72)
        kinds = {row['kind'] for row in rows}
        self.assertIn('autonomy_失败', kinds)
        self.assertIn('autonomy_搁置', kinds)
        self.assertIn('预算', next(row for row in rows if row['kind'] == 'autonomy_搁置')['detail'])

    def test_denials_carry_their_class_into_the_timeline(self):
        write_rows(diagnostics.tools_path(self.root, self.role), [
            self.tool_row(detail='arguments are not a valid JSON object')])
        rows = recent_issues(self.root, self.role, hours=72)
        denial = next(row for row in rows if row['kind'] == 'denial')
        self.assertEqual(denial['denial']['code'], 'args')
        self.assertEqual(denial['denial']['label'], '参数错误')

    def test_unknown_side_effect_requires_a_human_check(self):
        write_json(diagnostics.checkpoint_path(self.root, self.role), {
            'id': 'c', 'goal': 'g', 'request_id': 'r', 'status': 'active', 'updated_at': iso(10),
            'evidence': [{'time': iso(8), 'tool': 'write_file', 'call': 'x', 'state': '结果未知',
                          'source': 's', 'evidence': 'e', 'read_only': False}]})
        rows = recent_issues(self.root, self.role, hours=72)
        row = next(item for item in rows if item['kind'] == 'unknown_side_effect')
        self.assertEqual(row['severity'], 'attention')
        self.assertIn('核对', row['note'])

    def test_broken_files_do_not_raise(self):
        (self.root / self.role).mkdir(parents=True, exist_ok=True)
        (diagnostics.tools_path(self.root, self.role)).write_text('not json\n', encoding='utf-8')
        (diagnostics.runtime_status_path(self.root, self.role)).write_text('{', encoding='utf-8')
        self.assertEqual(recent_issues(self.root, self.role), [])


class ReportTests(Base):
    def test_the_report_has_all_sections_and_no_chat_text(self):
        write_rows(diagnostics.chat_path(self.root, self.role), [
            {'time': iso(5), 'role': 'user', 'content': 'PRIVATE-CHAT-TEXT',
             'request_id': 'req-1'}])
        write_rows(diagnostics.provider_path(self.root, self.role), [
            {'time': iso(4), 'model': 'm', 'status': 503, 'type': 'X', 'detail': 'down',
             'request_id': 'req-1'}])
        write_rows(diagnostics.tools_path(self.root, self.role), [
            self.tool_row(detail='arguments are not a valid JSON object')])
        text = report_text(self.root, self.role, hours=72)
        for marker in ('VEIL 诊断报告', '[1] 版本与系统', '[2] 模块状态', '[3] 配置校验异常',
                       '[4] 后台活动失败', '[5] 授权与拒绝分类', '[6] 最近异常明细', '[7] 维护入口'):
            self.assertIn(marker, text)
        self.assertNotIn('PRIVATE-CHAT-TEXT', text)
        self.assertIn('参数错误', text)

    def test_the_cli_prints_a_report_without_the_service(self):
        write_rows(diagnostics.autonomy_path(self.root, self.role), [
            {'started_at': iso(30), 'finished_at': iso(20), 'result': '失败',
             'result_reason': '接口返回 HTTP 503'}])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = diagnostics.main(['--data-root', str(self.root), '--role', self.role])
        self.assertEqual(code, 0)
        self.assertIn('VEIL 诊断报告', buffer.getvalue())
        self.assertIn('HTTP 503', buffer.getvalue())

    def test_snapshot_is_written_once_and_then_reused(self):
        first = diagnostics.snapshot_if_stale(self.root, self.role, {'generated_at': 'x'})
        second = diagnostics.snapshot_if_stale(self.root, self.role, {'generated_at': 'y'})
        self.assertTrue(first)
        self.assertFalse(second)

    def test_links_wait_for_the_memory_page_and_never_guess_a_url(self):
        items = links(self.root, self.role)
        memory = next(item for item in items if item['id'] == 'memory')
        self.assertEqual(memory['state'], 'not_ready')
        cfg = SimpleNamespace(memory={'maintenance_url': 'http://127.0.0.1:8123/'})
        ready = next(item for item in links(self.root, self.role, cfg) if item['id'] == 'memory')
        self.assertEqual(ready['state'], 'ready')


    def test_memory_drafts_never_reach_the_report(self):
        write_rows(diagnostics.experience_failures_path(self.root, self.role), [
            {'time': iso(6), 'kind': 'entry', 'stage': 'tidy', 'problem': 'too long',
             'title': 'MEMORY-TITLE-TEXT', 'draft': 'MEMORY-DRAFT-TEXT',
             'output': 'MODEL-OUTPUT-TEXT', 'model': 'fake'}])
        text = report_text(self.root, self.role, hours=72)
        self.assertNotIn('MEMORY-TITLE-TEXT', text)
        self.assertNotIn('MEMORY-DRAFT-TEXT', text)
        self.assertNotIn('MODEL-OUTPUT-TEXT', text)
        self.assertIn('too long', text)

    def test_obvious_secrets_are_hidden_in_the_report(self):
        write_rows(diagnostics.provider_path(self.root, self.role), [
            {'time': iso(4), 'model': 'm', 'status': 401, 'type': 'X',
             'detail': 'bad key sk-abcdefghijklmnopqrstuv wx', 'request_id': 'req-1'}])
        text = report_text(self.root, self.role, hours=72)
        self.assertNotIn('sk-abcdefghijklmnopqrstuv', text)
        self.assertIn('[已隐藏]', text)


class ModuleTests(Base):
    def test_an_empty_data_folder_is_unknown_not_running(self):
        modules = module_states(self.root, self.role)
        states = {module['id']: module['state'] for module in modules}
        self.assertEqual(states['model'], 'unknown')
        self.assertEqual(states['memory'], 'unknown')
        self.assertEqual(states['tools'], 'unknown')
        self.assertNotIn('running', set(states.values()))

    def test_a_failed_tool_is_reported_with_evidence(self):
        write_rows(diagnostics.tools_path(self.root, self.role), [
            {'time': iso(30), 'tool': 'peek_screen', 'state': '成功', 'request_id': 'r'},
            {'time': iso(5), 'tool': 'peek_screen', 'state': '出错', 'detail': 'window',
             'request_id': 'r'}])
        modules = {module['id']: module for module in module_states(self.root, self.role)}
        self.assertEqual(modules['tools']['state'], 'error')
        self.assertIn('peek_screen', modules['tools']['reason'])
        self.assertTrue(modules['tools']['last_ok_at'])

    def test_overview_never_raises_on_garbage(self):
        (self.root / self.role).mkdir(parents=True, exist_ok=True)
        (self.root / self.role / 'runtime-status.json').write_text('[]', encoding='utf-8')
        data = overview(self.root, self.role)
        self.assertIn('modules', data)
        self.assertIn('config', data)
        self.assertIn('counts', data)


class ReviewFixTests(Base):
    def test_an_expired_approval_is_not_reported_as_waiting(self):
        write_json(diagnostics.approvals_path(self.root, self.role), {'version': 1, 'items': [
            {'id': 'a1', 'run_id': 'wake-1', 'status': 'pending', 'prompt': 'x',
             'expires_at': (datetime.now().astimezone() - timedelta(hours=1)).isoformat()}]})
        result = classify_denial(self.tool_row(autonomy_run_id='wake-1', detail=''),
                                 diagnostics._approvals(self.root, self.role))
        self.assertEqual(result['code'], 'expired')
        self.assertNotIn('等你批准', result['label'])
        self.assertIn('过期', result['label'])

    def test_a_row_without_any_structured_id_is_never_confirmed(self):
        row = diagnostics._issue(iso(1), 'tools', 'tool_error', 'error', 'x', confirmed=True)
        self.assertFalse(row['confirmed'])

    def test_run_trace_uses_its_own_requests_and_redacts_details(self):
        write_rows(diagnostics.tools_path(self.root, self.role), [
            {'time': iso(30), 'tool': 'tool-a', 'state': '成功', 'request_id': 'req-A',
             'autonomy_run_id': 'wake-1', 'call_id': 'c1'},
            {'time': iso(20), 'tool': 'tool-b', 'state': '出错', 'request_id': 'req-B',
             'autonomy_run_id': 'wake-1', 'call_id': 'c2',
             'detail': 'api_key=abcd1234 https://user:pass@127.0.0.1/x'},
            {'time': iso(10), 'tool': 'tool-c', 'state': '成功', 'request_id': 'req-C',
             'autonomy_run_id': 'wake-2', 'call_id': 'c3'}])
        write_rows(diagnostics.context_path(self.root, self.role), [
            {'time': iso(29), 'request_id': 'req-A', 'autonomy_run_id': 'wake-1',
             'kind': 'autonomy', 'sent': True},
            {'time': iso(19), 'request_id': 'req-B', 'autonomy_run_id': 'wake-1',
             'kind': 'tool_step_context', 'sent': True}])
        write_rows(diagnostics.chat_path(self.root, self.role), [
            {'time': iso(25), 'role': 'user', 'content': 'UNRELATED-CHAT',
             'request_id': 'req-C'}])
        chain = diagnostics.trace(self.root, self.role, run_id='wake-1')
        self.assertEqual(sorted(chain['request_ids']), ['req-A', 'req-B'])
        self.assertEqual([tool['tool'] for tool in chain['tools']], ['tool-a', 'tool-b'])
        self.assertEqual(chain['messages'], [])
        self.assertEqual(len(chain['model_calls']), 2)
        text = json.dumps(chain, ensure_ascii=False)
        self.assertNotIn('abcd1234', text)
        self.assertNotIn('user:pass', text)
        self.assertIn('[已隐藏]', text)

    def test_trace_for_one_request_keeps_that_turn_only(self):
        write_rows(diagnostics.tools_path(self.root, self.role), [
            {'time': iso(30), 'tool': 'tool-a', 'state': '成功', 'request_id': 'req-A'},
            {'time': iso(10), 'tool': 'tool-b', 'state': '成功', 'request_id': 'req-B'}])
        write_rows(diagnostics.chat_path(self.root, self.role), [
            {'time': iso(31), 'role': 'user', 'content': 'one', 'request_id': 'req-A'},
            {'time': iso(11), 'role': 'user', 'content': 'two', 'request_id': 'req-B'}])
        chain = diagnostics.trace(self.root, self.role, request_id='req-A')
        self.assertEqual(chain['request_ids'], ['req-A'])
        self.assertEqual(len(chain['messages']), 1)
        self.assertEqual([tool['tool'] for tool in chain['tools']], ['tool-a'])

    def test_redaction_covers_key_values_and_url_credentials(self):
        text = diagnostics._redact(
            'api_key=abcd1234 password: "hunter2" https://user:pass@example.test/x '
            'sk-abcdefghijklmnop Bearer abcdefghijkl')
        for secret in ('abcd1234', 'hunter2', 'user:pass', 'sk-abcdefghijklmnop', 'abcdefghijkl'):
            self.assertNotIn(secret, text)
        self.assertIn('[已隐藏]', text)


    def test_ids_allow_the_real_wake_format(self):
        self.assertTrue(diagnostics.valid_id('2026-11-23T23:36:01.806219+08:00'))
        self.assertTrue(diagnostics.valid_id('glance:2026-11-23T23:36:01.806219+08:00'))
        self.assertTrue(diagnostics.valid_id(
            '917705bd7bf240ecbd369bfff667f066:0:toolu_334f086065f83d43'))
        self.assertFalse(diagnostics.valid_id('a b'))
        self.assertFalse(diagnostics.valid_id('x' * 121))


    def test_a_bare_call_id_does_not_pull_in_every_other_row(self):
        write_rows(diagnostics.tools_path(self.root, self.role), [
            {'time': iso(30), 'tool': 'tool-a', 'state': '成功', 'call_id': 'call-1'},
            {'time': iso(20), 'tool': 'tool-b', 'state': '成功', 'call_id': 'call-2'}])
        write_rows(diagnostics.context_path(self.root, self.role), [
            {'time': iso(29), 'request_id': 'req-A', 'kind': 'chat', 'sent': True}])
        chain = diagnostics.trace(self.root, self.role, call_id='call-1')
        self.assertEqual([tool['tool'] for tool in chain['tools']], ['tool-a'])
        self.assertEqual(chain['context'], [])
        self.assertEqual(chain['messages'], [])


    def test_mcp_issue_ids_do_not_change_between_refreshes(self):
        first = diagnostics.recent_issues(self.root, self.role,
                                          mcp_errors={'galatea': 'start timeout'})
        second = diagnostics.recent_issues(self.root, self.role,
                                           mcp_errors={'galatea': 'start timeout'})
        mcp_first = [row for row in first if row['kind'] == 'mcp_error']
        mcp_second = [row for row in second if row['kind'] == 'mcp_error']
        self.assertEqual(len(mcp_first), 1)
        self.assertEqual(mcp_first[0]['id'], mcp_second[0]['id'])
        # the same message on another service is a different record
        other = diagnostics.recent_issues(self.root, self.role,
                                         mcp_errors={'nowhere': 'start timeout'})
        self.assertNotEqual(mcp_first[0]['id'],
                            [row for row in other if row['kind'] == 'mcp_error'][0]['id'])

    def test_a_partial_data_size_is_not_reported_as_the_total(self):
        folder = self.root / self.role
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(3):
            (folder / ('f' + str(index))).write_bytes(b'x' * 10)
        total, complete = diagnostics._dir_size(folder, limit=2)
        self.assertFalse(complete)
        self.assertEqual(total, 20)
        service = diagnostics.service_info(folder, self.role)
        self.assertFalse(service['data_bytes_complete'])
        text = diagnostics.report_text(folder, self.role, hours=24)
        self.assertIn('至少', text)

    def test_an_old_snapshot_with_only_role_still_shows_the_character(self):
        write_json(diagnostics.snapshot_path(self.root, self.role), {
            'generated_at': iso(5),
            'service': {'role': 'peter', 'commit': 'abc'},
            'modules': [], 'config': [], 'issues': []})
        text = diagnostics.report_text(self.root, self.role, hours=24)
        self.assertIn('角色：peter', text)


    def test_mcp_rows_do_not_pretend_the_refresh_time_is_the_event_time(self):
        rows = diagnostics.recent_issues(self.root, self.role,
                                         mcp_errors={'galatea': 'start timeout'})
        row = [item for item in rows if item['kind'] == 'mcp_error'][0]
        self.assertEqual(row['time'], '')
        self.assertTrue(row['observed_at'])
        line = diagnostics._issue_line(row)
        self.assertIn('时间未知', line)
        self.assertIn('本次运行内', line)


class SyncTests(unittest.TestCase):
    def test_group_labels_cover_every_registered_group(self):
        from veil.tools import tool_groups
        registered = set(tool_groups.GROUP_SUMMARIES)
        missing = registered - set(diagnostics.GROUP_LABELS)
        self.assertEqual(missing, set(), 'add the Chinese label for these groups')
        extra = set(diagnostics.GROUP_LABELS) - registered - {'social'}
        self.assertEqual(extra, set(), 'these group labels no longer exist')


if __name__ == '__main__':
    unittest.main()

