import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import maintenance


def _git(repo, *args):
    finished = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=60)
    if finished.returncode:
        raise AssertionError(finished.stderr or finished.stdout)
    return finished.stdout


class FakeGitHub:
    """Real git for local worktree facts; a fake gh for the remote facts."""

    def __init__(self, *, ci='success', reviews=()):
        self.head = ''
        self.pr_created = False
        self.merged = False
        self.ci = ci
        self.reviews = list(reviews)
        self.calls = []
        self.signed_in = True
        self.envs = []

    def _view(self):
        checks = ([{'name': 'test', 'status': 'IN_PROGRESS' if self.ci == 'pending' else 'COMPLETED',
                    'conclusion': None if self.ci == 'pending' else 'SUCCESS' if self.ci == 'success' else 'FAILURE',
                    'detailsUrl': 'https://github.com/yecays-ai/veil-agent/actions/runs/123/job/456'}]
                  if self.ci != 'none' else [])
        return {
            'number': 1, 'url': 'https://github.com/yecays-ai/veil-agent/pull/1',
            'state': 'MERGED' if self.merged else 'OPEN', 'isDraft': False,
            'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN', 'headRefOid': self.head,
            'baseRefName': 'main', 'statusCheckRollup': checks, 'reviews': self.reviews,
            'mergeCommit': {'oid': self.head} if self.merged else None,
        }

    def run(self, argv, cwd=None, timeout=180, input_text=None, env=None):
        self.calls.append(list(argv))
        self.envs.append(env)
        if argv[0] == 'git' and argv[1] in {'fetch'}:
            return 1, '', 'offline fixture'
        if argv[0] == 'git' and argv[1] == 'ls-remote':
            return 0, '', ''
        if argv[0] == 'git' and argv[1] == 'push':
            assert 'origin' in argv, argv
            assert any(str(arg).startswith('HEAD:ticket/') for arg in argv), argv
            self.head = _git(cwd, 'rev-parse', 'HEAD').strip()
            return 0, '', ''
        if argv[0] == 'gh':
            args = argv[1:]
            if args[:2] == ['auth', 'status']:
                return (0, '', '') if self.signed_in else (1, '', 'You are not logged into any GitHub hosts.')
            if args[:2] == ['pr', 'create']:
                assert '--repo' in args and args[args.index('--repo') + 1] == 'yecays-ai/veil-agent', args
                assert '--base' in args and args[args.index('--base') + 1] == 'main', args
                assert '--head' in args and args[args.index('--head') + 1].startswith('ticket/'), args
            if args[:2] == ['pr', 'merge']:
                assert '--repo' in args and args[args.index('--repo') + 1] == 'yecays-ai/veil-agent', args
                assert args[2] == '1', args
            if args[:2] == ['pr', 'view']:
                assert '--repo' in args and args[args.index('--repo') + 1] == 'yecays-ai/veil-agent', args
                assert args[2] == '1', args
            if args[:2] == ['pr', 'list']:
                value = [{'number': 1, 'url': self._view()['url'], 'state': 'OPEN',
                          'headRefOid': self.head}] if self.pr_created else []
                return 0, json.dumps(value), ''
            if args[:2] == ['pr', 'create']:
                self.pr_created = True
                return 0, self._view()['url'] + '\n', ''
            if args[:2] == ['pr', 'edit']:
                return 0, '', ''
            if args[:2] == ['pr', 'view']:
                return 0, json.dumps(self._view()), ''
            if args[:2] == ['pr', 'merge']:
                self.merged = True
                return 0, '', ''
            if args[0] == 'api':
                return 0, json.dumps({'required_status_checks': {'contexts': ['test']}}), ''
            if args[:2] == ['run', 'view']:
                return 0, 'test failed: expected 2, got 3', ''
        finished = subprocess.run([str(item) for item in argv], cwd=str(cwd) if cwd else None,
                                  capture_output=True, text=True, encoding='utf-8', errors='replace',
                                  input=input_text, timeout=timeout)
        return finished.returncode, finished.stdout or '', finished.stderr or ''


class MaintenanceTests(unittest.TestCase):
    def test_host_delivery_process_is_cancelled_when_user_stops(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.5, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.Runner().run([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     stopped=cancelled.is_set)
        self.assertLess(time.monotonic() - started, 10)

    def test_stop_during_push_does_not_start_pr_creation(self):
        task = self.begin()
        self.develop(task)
        stopped = threading.Event()
        self.manager.stopped = stopped.is_set
        original = self.gh.run
        def stop_after_push(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[:2] == ['git', 'push']:
                stopped.set()
            return result
        self.gh.run = stop_after_push
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '出错', text)
        self.assertFalse(self.gh.pr_created)
        self.assertEqual(self.manager.store.load(task['task_id'])['stage'], 'stopped')

    def test_resume_after_stop_during_pr_creation_finds_pr_but_stays_stopped(self):
        task = self.begin()
        self.develop(task)
        stopped = threading.Event()
        self.manager.stopped = stopped.is_set
        original = self.gh.run
        def stop_after_pr(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[:3] == ['gh', 'pr', 'create']:
                stopped.set()
            return result
        self.gh.run = stop_after_pr
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '出错')
        self.assertTrue(self.gh.pr_created)
        stopped.clear()
        self.manager.invoke('resume', {'task_id': task['task_id']})
        current = self.manager.store.load(task['task_id'])
        self.assertEqual(current['pull_request']['number'], 1)
        self.assertEqual(current['stage'], 'stopped')
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        _git(self.repo, 'init', '-b', 'main')
        _git(self.repo, 'config', 'user.name', 'Test')
        _git(self.repo, 'config', 'user.email', 'test@example.com')
        (self.repo / 'AGENTS.md').write_text('rules', encoding='utf-8')
        (self.repo / 'STATUS.md').write_text('# 当前状态\n\n旧内容\n', encoding='utf-8')
        (self.repo / 'HANDOFF.md').write_text('# HANDOFF\n', encoding='utf-8')
        (self.repo / 'docs' / 'tickets').mkdir(parents=True)
        (self.repo / 'docs' / 'tickets' / '100-old.md').write_text('# old', encoding='utf-8')
        _git(self.repo, 'add', '.')
        _git(self.repo, 'commit', '-m', 'base')
        _git(self.repo, 'remote', 'add', 'origin', 'https://github.com/yecays-ai/veil-agent.git')
        self.store = SimpleNamespace(root=self.root / 'data')
        self.approvals = []
        self.approval = lambda prompt: self.approvals.append(prompt) or 'y'
        self.gh = FakeGitHub()
        self.manager = maintenance.MaintenanceManager(
            self.store, 'test', repo_path=self.repo, worktree_root=self.root / 'worktrees',
            approval=self.approval, runner=self.gh)

    def begin(self, title='Add greeting', ticket=314):
        text, state = self.manager.invoke('begin', {
            'title': title, 'goal': 'Lan can see the greeting.', 'acceptance': '1. Lan opens the test instance.',
            'ticket': ticket})
        self.assertEqual(state, '成功', text)
        task = self.manager.store.load()
        self.assertIsNotNone(task)
        return task

    def develop(self, task, name='feature.py', content='VALUE = 1\n'):
        worktree = Path(task['worktree'])
        (worktree / name).write_text(content, encoding='utf-8')

    def test_begin_explains_missing_github_login_before_asking_or_creating_anything(self):
        self.gh.signed_in = False
        text, state = self.manager.invoke('begin', {'title': 'Add greeting', 'goal': 'g', 'ticket': 314})
        self.assertEqual(state, '被拒绝')
        self.assertIn('github-token.cmd', text)
        self.assertEqual(self.approvals, [])
        self.assertFalse((self.root / 'worktrees').exists())

    def test_token_file_feeds_git_and_gh_only_through_environment(self):
        token = 'github_pat_' + 'A1b2C3d4' * 5
        token_file = self.root / 'github-token.txt'
        token_file.write_text('\n' + token + '\n', encoding='utf-8')
        self.manager.token_file = token_file
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        pushes = [env for call, env in zip(self.gh.calls, self.gh.envs) if call[:2] == ['git', 'push']]
        self.assertTrue(pushes)
        self.assertEqual(pushes[0]['GH_TOKEN'], token)
        self.assertEqual(pushes[0]['GIT_CONFIG_VALUE_1'], '!gh auth git-credential')
        self.assertFalse(any(token in ' '.join(call) for call in self.gh.calls))
        self.assertNotIn(token, json.dumps(self.manager.store.load(task['task_id'])))

    def test_malformed_token_file_is_ignored(self):
        token_file = self.root / 'github-token.txt'
        token_file.write_text('not a token', encoding='utf-8')
        self.assertEqual(maintenance.read_github_token(token_file), '')
        self.assertEqual(maintenance.read_github_token(self.root / 'missing.txt'), '')

    def test_begin_creates_an_isolated_worktree_and_documents_without_touching_main(self):
        task = self.begin()
        worktree = Path(task['worktree'])
        self.assertTrue(worktree.is_dir())
        self.assertEqual(_git(worktree, 'symbolic-ref', '--short', 'HEAD').strip(),
                         'ticket/314-add-greeting')
        self.assertTrue((worktree / 'docs' / 'tickets' / '314-add-greeting.md').is_file())
        self.assertIn('<!-- maintenance:314:start -->', (worktree / 'STATUS.md').read_text(encoding='utf-8'))
        self.assertIn('<!-- maintenance:314:start -->', (worktree / 'HANDOFF.md').read_text(encoding='utf-8'))
        self.assertEqual(_git(self.repo, 'symbolic-ref', '--short', 'HEAD').strip(), 'main')
        self.assertEqual(_git(self.repo, 'status', '--porcelain').strip(), '')
        self.assertEqual(len(self.approvals), 1)
        self.assertIn('ticket/314-add-greeting', self.approvals[0])
        self.assertIn('yecays-ai/veil-agent', self.approvals[0])

    def test_begin_does_not_create_a_task_when_lan_refuses(self):
        self.manager.approval = lambda _prompt: 'n'
        text, state = self.manager.invoke('begin', {'title': 'X', 'goal': 'Y'})
        self.assertEqual(state, '被拒绝')
        self.assertIsNone(self.manager.store.load())
        self.assertFalse((self.root / 'worktrees').exists())

    def test_deliver_commits_pushes_and_creates_a_pr_once(self):
        task = self.begin()
        self.develop(task)
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['stage'], 'pr_open')
        self.assertEqual(task['pull_request']['number'], 1)
        self.assertTrue(task['pull_request']['url'].endswith('/pull/1'))
        self.assertTrue(task['git']['last_commit'])
        self.assertEqual(_git(Path(task['worktree']), 'status', '--porcelain').strip(), '')
        # A second deliver updates the same PR instead of creating a duplicate.
        self.develop(task, 'feature.py', 'VALUE = 2\n')
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        creates = [call for call in self.gh.calls if call[:3] == ['gh', 'pr', 'create']]
        self.assertEqual(len(creates), 1)

    def test_checks_failure_blocks_merge_and_returns_real_log(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.gh.ci = 'failure'
        text, state = self.manager.invoke('checks', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['checks']['state'], 'failed')
        self.assertEqual(task['stage'], 'ci_failed')
        self.assertIn('expected 2, got 3', text)
        text, state = self.manager.invoke('merge', {'task_id': task['task_id']})
        self.assertEqual(state, '被拒绝')
        self.assertFalse(self.gh.merged)

    def test_pending_ci_is_recorded_and_cannot_merge(self):
        task = self.begin()
        self.develop(task)
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')
        self.gh.ci = 'pending'
        self.assertEqual(self.manager.invoke('checks', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(self.manager.store.load(task['task_id'])['stage'], 'ci_pending')
        self.assertEqual(self.manager.invoke('merge', {'task_id': task['task_id']})[1], '被拒绝')
        self.assertFalse(self.gh.merged)

    def test_acceptance_and_merge_require_a_real_user_confirmation(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.gh.ci = 'success'
        self.manager.invoke('checks', {'task_id': task['task_id']})
        started = []
        self.manager.instance_factory = lambda record: SimpleNamespace(
            start=lambda: maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [], 'fixture', ''))
        text, state = self.manager.invoke('accept', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        self.assertIn('http://127.0.0.1:1234', text)
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['acceptance']['status'], 'not_yet')
        # Lan has not actually accepted yet; merge must ask and can be refused.
        self.manager.approval = lambda _prompt: 'n'
        text, state = self.manager.invoke('merge', {'task_id': task['task_id'], 'retry': True})
        self.assertEqual(state, '被拒绝')
        self.assertFalse(self.gh.merged)
        self.manager.approval = lambda _prompt: 'y'
        text, state = self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed',
                                                     'note': 'Lan tried it.'})
        self.assertEqual(state, '成功', text)
        text, state = self.manager.invoke('merge', {'task_id': task['task_id'], 'retry': True})
        self.assertEqual(state, '成功', text)
        self.assertTrue(self.gh.merged)
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['stage'], 'merged')
        self.assertEqual(task['acceptance']['status'], 'passed')

    def test_acceptance_failure_requires_an_explicit_lan_waiver(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.gh.ci = 'success'
        self.manager.invoke('checks', {'task_id': task['task_id']})
        self.manager.instance_factory = lambda record: SimpleNamespace(
            start=lambda: maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [], 'fixture', ''))
        self.manager.invoke('accept', {'task_id': task['task_id']})
        text, state = self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'failed',
                                                     'note': 'The button does nothing.'})
        self.assertEqual(state, '成功', text)
        self.manager.approval = lambda _prompt: 'n'
        text, state = self.manager.invoke('merge', {'task_id': task['task_id'], 'retry': True})
        self.assertEqual(state, '被拒绝')
        self.assertFalse(self.gh.merged)
        self.manager.approval = lambda _prompt: 'y'
        text, state = self.manager.invoke('merge', {'task_id': task['task_id'], 'retry': True})
        self.assertEqual(state, '成功', text)
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['acceptance']['status'], 'failed')
        self.assertTrue(task['acceptance']['waived'])
        self.assertTrue(self.gh.merged)

    def test_lan_refusing_to_confirm_acceptance_keeps_it_not_yet(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.gh.ci = 'success'
        self.manager.invoke('checks', {'task_id': task['task_id']})
        self.manager.approval = lambda _prompt: 'n'
        text, state = self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed'})
        self.assertEqual(state, '被拒绝')
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['acceptance']['status'], 'not_yet')

    def test_changes_requested_review_blocks_checks_and_merge(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.gh.ci = 'success'
        self.gh.reviews = [{'author': {'login': 'reviewer'}, 'state': 'CHANGES_REQUESTED',
                            'body': 'P1: the button writes outside the project.'}]
        text, state = self.manager.invoke('checks', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['review']['blocking'], ['reviewer'])
        self.assertEqual(task['stage'], 'review_failed')
        text, state = self.manager.invoke('merge', {'task_id': task['task_id']})
        self.assertEqual(state, '被拒绝')
        self.assertFalse(self.gh.merged)

    def test_stop_prevents_push_and_merge(self):
        task = self.begin()
        self.manager.invoke('stop', {'task_id': task['task_id']})
        self.develop(task)
        for action in ('deliver', 'merge'):
            text, state = self.manager.invoke(action, {'task_id': task['task_id']})
            self.assertEqual(state, '被拒绝', (action, text))
        self.assertFalse(self.gh.pr_created)
        self.assertFalse(self.gh.merged)

    def test_workbench_receives_verified_repository_facts_without_git_metadata(self):
        task = self.begin()
        self.develop(task)
        target = self.manager.workbench_target(task['task_id'])
        self.assertEqual(target['workbench_git']['head'], _git(Path(task['worktree']), 'rev-parse', 'HEAD').strip())
        self.assertEqual(target['workbench_git']['branch'], task['branch'])
        self.assertTrue(target['workbench_git']['has_changes'])
        self.assertIn('feature.py', target['workbench_git']['changes'])

    def test_task_approval_is_durable_and_cannot_be_added_after_stop(self):
        task = self.begin()
        fingerprint = 'a' * 64
        self.manager.record_workbench_approval(task['task_id'], 'denied', fingerprint)
        self.assertIn(fingerprint, self.manager.store.load(task['task_id'])['authorization']['workbench']['denied'])
        self.manager.invoke('stop', {'task_id': task['task_id']})
        with self.assertRaises(maintenance.MaintenanceError):
            self.manager.record_workbench_approval(task['task_id'], 'approved', 'b' * 64)

    def test_stale_inflight_state_cannot_resurrect_a_stopped_task(self):
        task = self.begin()
        stale = self.manager.store.load(task['task_id'])
        self.manager.invoke('stop', {'task_id': task['task_id']})
        stale['next_step'] = 'continue work'
        with self.assertRaises(maintenance.MaintenanceError):
            self.manager.store.save(stale)
        self.assertEqual(self.manager.store.load(task['task_id'])['stage'], 'stopped')

    def test_stopped_task_status_survives_a_missing_worktree(self):
        task = self.begin()
        self.assertEqual(self.manager.invoke('stop', {'task_id': task['task_id']})[1], '成功')
        shutil.rmtree(task['worktree'])
        text, state = self.manager.invoke('status', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        current = self.manager.store.load(task['task_id'])
        self.assertEqual(current['stage'], 'stopped')
        self.assertIn('worktree is missing', text)

    def test_stale_stop_cannot_overwrite_a_merged_task(self):
        task = self.begin()
        self.develop(task)
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(self.manager.invoke('checks', {'task_id': task['task_id']})[1], '成功')
        self.manager.instance_factory = lambda record: SimpleNamespace(
            start=lambda: maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [], 'fixture', ''))
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed'})[1], '成功')
        stale = self.manager.store.load(task['task_id'])
        self.assertEqual(stale['stage'], 'ready_to_merge')
        self.assertEqual(self.manager.invoke('merge', {'task_id': task['task_id']})[1], '成功')
        stale['stage'] = 'stopped'
        stale['next_step'] = 'Stopped by Lan; no more commands, pushes or merges.'
        with self.assertRaises(maintenance.MaintenanceError):
            self.manager.store.save(stale)
        self.assertEqual(self.manager.store.load(task['task_id'])['stage'], 'merged')

    def test_stop_during_test_instance_start_discards_the_instance(self):
        task = self.begin()
        self.develop(task)
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')
        stopped_calls = []
        test_case, manager = self, self.manager
        def factory(_task):
            class Instance:
                def start(self):
                    test_case.assertEqual(manager.invoke('stop', {'task_id': task['task_id']})[1], '成功')
                    return maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [1234], 'fixture', '/tmp/config')
                def stop(self):
                    stopped_calls.append(True)
            return Instance()
        self.manager.instance_factory = factory
        text, state = self.manager.invoke('accept', {'task_id': task['task_id']})
        self.assertEqual(state, '被拒绝', text)
        self.assertEqual(stopped_calls, [True])
        self.assertEqual(self.manager.store.load(task['task_id'])['stage'], 'stopped')

    def test_delivery_cannot_race_a_running_workbench_but_stop_can(self):
        task = self.begin()
        _target, lock = self.manager.claim_workbench(task['task_id'])
        try:
            text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
            self.assertEqual(state, '被拒绝', text)
            self.assertFalse(self.gh.pr_created)
            self.assertEqual(self.manager.invoke('stop', {'task_id': task['task_id']})[1], '成功')
        finally:
            lock.release()

    def test_process_restart_preserves_failure_changes_and_approval(self):
        task = self.begin()
        self.develop(task, content='unfinished work\n')
        self.manager.record_workbench_approval(task['task_id'], 'denied', 'a' * 64)
        self.manager.record_workbench(task['task_id'], cost='0.01', steps=4, report='repeated read',
                                      state='出错', reason='repeated_call')
        script = ('import json,sys; from types import SimpleNamespace; from maintenance import MaintenanceStore; '
                  't=MaintenanceStore(SimpleNamespace(root=sys.argv[1]),"test").load(sys.argv[2]); '
                  'print(json.dumps({"reason":t["development"]["reason"],'
                  '"denied":t["authorization"]["workbench"]["denied"]}))')
        child = subprocess.run([sys.executable, '-c', script, str(self.store.root), task['task_id']],
                               cwd=str(Path(maintenance.__file__).resolve().parent),
                               capture_output=True, text=True, timeout=30, check=True)
        restored = json.loads(child.stdout)
        self.assertEqual(restored['reason'], 'repeated_call')
        self.assertIn('a' * 64, restored['denied'])
        restarted = maintenance.MaintenanceManager(self.store, 'test', repo_path=self.repo,
                                                   worktree_root=self.root / 'worktrees',
                                                   approval=self.approval, runner=self.gh)
        text, state = restarted.invoke('resume', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        target = restarted.workbench_target(task['task_id'])
        self.assertIn('feature.py', target['workbench_git']['changes'])
        self.assertEqual((Path(task['worktree']) / 'feature.py').read_text(encoding='utf-8'), 'unfinished work\n')

    def test_status_and_resume_report_the_persisted_task_and_real_worktree(self):
        task = self.begin()
        text, state = self.manager.invoke('status', {})
        self.assertEqual(state, '成功')
        self.assertIn(task['task_id'], text)
        self.assertIn('ticket=314', text)
        self.assertIn('worktree=exists', text)
        text, state = self.manager.invoke('resume', {'task_id': task['task_id']})
        self.assertEqual(state, '成功')
        self.assertIn('developing', text)

    def test_begin_rejects_a_worktree_root_that_overlaps_veil_data(self):
        manager = maintenance.MaintenanceManager(
            self.store, 'test', repo_path=self.repo, worktree_root=self.store.root / 'worktrees',
            approval=lambda _prompt: 'y', runner=self.gh)
        text, state = manager.invoke('begin', {'title': 'X', 'goal': 'Y', 'ticket': 315})
        self.assertEqual(state, '出错')
        self.assertIn('overlap', text)
        self.assertIsNone(manager.store.load())

    def test_task_actions_require_an_explicit_task_id(self):
        task = self.begin()
        self.develop(task)
        text, state = self.manager.invoke('deliver', {})
        self.assertEqual(state, '出错')
        self.assertIn('task_id', text)
        self.assertFalse(self.gh.pr_created)

    def test_stopped_task_cannot_be_revived_by_checks_or_accept(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.manager.invoke('stop', {'task_id': task['task_id']})
        for action in ('checks', 'accept'):
            text, state = self.manager.invoke(action, {'task_id': task['task_id']})
            self.assertEqual(state, '被拒绝', (action, text))

    def test_cleanup_is_refused_while_the_task_is_active(self):
        task = self.begin()
        text, state = self.manager.invoke('cleanup', {'task_id': task['task_id']})
        self.assertEqual(state, '被拒绝')
        self.assertTrue(Path(task['worktree']).exists())

    def test_sensitive_files_are_refused_before_commit(self):
        task = self.begin()
        worktree = Path(task['worktree'])
        (worktree / '.env').write_text('SECRET=1', encoding='utf-8')
        _git(worktree, 'add', '-f', '.env')
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '出错')
        self.assertIn('.env', text)
        self.assertFalse(self.gh.pr_created)

    def test_pr_lookup_failure_does_not_create_another_pr(self):
        task = self.begin()
        self.develop(task)
        original = self.gh.run
        def failing(argv, **kwargs):
            if argv[:3] == ['gh', 'pr', 'list']:
                return 1, '', 'network unavailable'
            return original(argv, **kwargs)
        self.gh.run = failing
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '出错')
        self.assertFalse(self.gh.pr_created)

    def test_dirty_worktree_cannot_be_accepted_as_pr_head(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.develop(task, content='VALUE = 2\n')
        text, state = self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed'})
        self.assertEqual(state, '被拒绝', text)
        self.assertEqual(self.manager.store.load(task['task_id'])['acceptance']['status'], 'not_yet')

    def test_acceptance_rejects_a_changed_remote_pr_head(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.manager.invoke('checks', {'task_id': task['task_id']})
        self.gh.head = 'a-different-remote-head'
        self.manager.instance_factory = Mock(side_effect=AssertionError('must not start a stale instance'))
        text, state = self.manager.invoke('accept', {'task_id': task['task_id']})
        self.assertEqual(state, '出错', text)
        current = self.manager.store.load(task['task_id'])
        self.assertEqual(current['acceptance']['status'], 'not_yet')
        self.assertIn('head changed', ' '.join(current['blockers']))

    def test_acceptance_runs_from_a_snapshot_of_the_accepted_commit(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        task = self.manager.store.load(task['task_id'])
        instance = maintenance.AcceptanceInstance(task, self.repo, data_root=self.root / 'instances')
        instance._prepare()
        snapshot = instance.code / 'feature.py'
        self.assertEqual(snapshot.read_text(encoding='utf-8'), 'VALUE = 1\n')
        self.develop(task, content='VALUE = 2\n')
        self.assertEqual(snapshot.read_text(encoding='utf-8'), 'VALUE = 1\n')
        self.assertFalse((instance.code / '.git').exists())

    def test_acceptance_ignores_export_substitution_and_uses_exact_commit(self):
        task = self.begin()
        self.develop(task, content='VALUE = "$Format:%H$"\n')
        (Path(task['worktree']) / '.gitattributes').write_text('feature.py export-subst\n', encoding='utf-8')
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        task = self.manager.store.load(task['task_id'])
        instance = maintenance.AcceptanceInstance(task, self.repo, data_root=self.root / 'instances')
        instance._prepare()
        self.assertEqual((instance.code / 'feature.py').read_text(encoding='utf-8'),
                         'VALUE = "$Format:%H$"\n')

    def test_real_acceptance_backend_starts_from_snapshot_with_fake_config(self):
        import executor_opencode as ex
        from tests.test_executor_opencode import _docker_workbench_available, _fake_llm, PROVIDER
        if not _docker_workbench_available():
            self.skipTest('VEIL workbench Docker image is unavailable')
        source = Path(maintenance.__file__).resolve().parent
        archive = subprocess.run(['git', '-c', f'safe.directory={source.as_posix()}', '-C', str(source),
                                  'archive', '--format=tar', 'HEAD'], capture_output=True, timeout=60, check=True)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode='r:') as content:
            if sys.version_info >= (3, 12):
                content.extractall(self.repo, filter='data')
            else:
                content.extractall(self.repo)
        character = self.repo / 'characters' / 'peter' / 'character.toml'
        character.parent.mkdir(parents=True, exist_ok=True)
        character.write_text('''name = "Peter"
provider = "fake"
persona = "persona/peter.md"
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
''', encoding='utf-8')
        _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-m', 'fake app fixture')
        (self.repo / 'persona').mkdir(exist_ok=True)
        (self.repo / 'persona' / 'peter.md').write_text('Fictional persona', encoding='utf-8')
        (self.repo / '.env').write_text('FAKE_KEY=fake\n', encoding='utf-8')
        from veil_agent import Budget, Chat, Store, load_character
        try:
            cfg = load_character(self.repo, 'peter')
        except Exception as exc:
            self.fail(f'fake config invalid: {exc!r}; cause={exc.__cause__!r}')
        task = self.begin()
        server = _fake_llm("printf 'import unittest\\nclass FeatureTest(unittest.TestCase):\\n def test_feature(self): self.assertEqual(1 + 1, 2)\\n' > test_feature.py && python3 -m unittest test_feature")
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = {**PROVIDER, 'base_url': f'http://127.0.0.1:{server.server_address[1]}/v1', 'model': 'fake'}
        cfg.provider.update(provider)
        cfg.executor = {'enabled': True, 'container_image': 'veil-workbench', 'command': '',
                        'max_steps': 30, 'max_seconds': 90, 'repeat_limit': 3, 'run_budget_cny': '0.5'}
        store = Store(self.root / 'data')
        asked = []
        chat = Chat(cfg, store, Budget(store), Mock(), approval=lambda p: asked.append(p) or 'y',
                    output=lambda *_: None)
        chat.maintenance = self.manager
        with patch('veil_agent.ROOT', self.repo):
            outcome = chat._run_workbench('create and run test_feature.py', 'fake-request', False,
                                          task_id=task['task_id'])
            first_approvals = len(asked)
            repeated = chat._run_workbench('continue the same task', 'fake-request-2', False,
                                           task_id=task['task_id'])
        self.assertEqual(outcome.state, '成功', outcome.text)
        self.assertIn('OK', outcome.text)
        self.assertEqual(repeated.state, '成功', repeated.text)
        self.assertGreater(first_approvals, 0)
        self.assertEqual(len(asked), first_approvals, 'same task and same operation must reuse approval')
        self.assertIn('approved_reused', repeated.text)
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(self.manager.invoke('checks', {'task_id': task['task_id']})[1], '成功')
        task = self.manager.store.load(task['task_id'])
        instance = maintenance.AcceptanceInstance(task, self.repo, data_root=self.root / 'instances',
                                                  electron=self.root / 'no-electron')
        self.addCleanup(instance.stop)
        self.manager.instance_factory = lambda _task: instance
        text, state = self.manager.invoke('accept', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        self.assertIn('http://127.0.0.1:', text)
        self.assertTrue((instance.code / 'web_agent.py').is_file())
        self.assertTrue((instance.code / 'test_feature.py').is_file())
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed'})[1], '成功')
        self.assertEqual(self.manager.invoke('merge', {'task_id': task['task_id']})[1], '成功')
        self.assertTrue(self.gh.merged)

    def test_stopped_workbench_is_recorded_as_stopped_not_test_failure(self):
        task = self.begin()
        self.manager.record_workbench(task['task_id'], cost=0, steps=4, report='stopped by Lan',
                                      state='出错', reason='user_stopped')
        result = self.manager.store.load(task['task_id'])
        self.assertEqual(result['development']['reason'], 'user_stopped')
        self.assertEqual(result['stage'], 'stopped')
        self.assertNotIn('test run', result['next_step'])
        with self.assertRaises(maintenance.MaintenanceError):
            self.manager.workbench_target(task['task_id'])

    def test_failed_tests_block_delivery_until_a_successful_workbench_result(self):
        task = self.begin()
        self.develop(task)
        self.manager.record_workbench(task['task_id'], cost=0, steps=1, report='tests failed',
                                      state='出错', reason='test_failed')
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '被拒绝', text)
        self.assertFalse(self.gh.pr_created)
        self.manager.record_workbench(task['task_id'], cost=0, steps=1, report='tests pass',
                                      state='成功', reason='')
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')

    def test_real_opencode_to_fake_github_delivery(self):
        import executor_opencode as ex
        from tests.test_executor_opencode import _docker_workbench_available, _fake_llm, PROVIDER
        if not _docker_workbench_available():
            self.skipTest('VEIL workbench Docker image is unavailable')
        task = self.begin()
        server = _fake_llm("printf 'import unittest\\nclass FeatureTest(unittest.TestCase):\\n def test_feature(self): self.assertEqual(1 + 1, 2)\\n' > test_feature.py && python3 -m unittest test_feature")
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = {**PROVIDER, 'base_url': f'http://127.0.0.1:{server.server_address[1]}/v1', 'model': 'fake'}
        result = ex.run('create and run test_feature.py', command='opencode', workdir=Path(task['worktree']),
                        home=self.root / 'home', provider=provider, key='fake', limits={'max_seconds': 90},
                        autonomy=False, ask=lambda _p: 'y', stopped=lambda: False, record_cost=lambda _c: None,
                        container={'image': 'veil-workbench'}, maintenance=True)
        self.assertEqual(result.state, '成功', result.text)
        self.assertTrue((Path(task['worktree']) / 'test_feature.py').is_file())
        self.assertIn('OK', result.text)
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        self.assertTrue(self.gh.pr_created)
        self.assertEqual(self.manager.invoke('checks', {'task_id': task['task_id']})[1], '成功')
        self.manager.instance_factory = lambda record: SimpleNamespace(
            start=lambda: maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [], 'fixture', ''))
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed'})[1], '成功')
        self.assertEqual(self.manager.invoke('merge', {'task_id': task['task_id']})[1], '成功')
        self.assertTrue(self.gh.merged)

    def test_real_opencode_failure_then_fix_delivers_to_fake_github(self):
        import executor_opencode as ex
        from tests.test_executor_opencode import _docker_workbench_available, _fake_llm, PROVIDER
        if not _docker_workbench_available():
            self.skipTest('VEIL workbench Docker image is unavailable')
        task = self.begin()
        worktree = Path(task['worktree'])
        (worktree / 'test_feature.py').write_text(
            'import unittest\nclass FeatureTest(unittest.TestCase):\n'
            ' def test_feature(self): self.assertEqual(1, 2)\n', encoding='utf-8')
        failed_server = _fake_llm('python3 -m unittest test_feature')
        self.addCleanup(failed_server.server_close)
        self.addCleanup(failed_server.shutdown)
        failed_provider = {**PROVIDER, 'base_url': f'http://127.0.0.1:{failed_server.server_address[1]}/v1',
                           'model': 'fake'}
        failed = ex.run('run the project tests', command='opencode', workdir=worktree,
                        home=self.root / 'home', provider=failed_provider, key='fake',
                        limits={'max_seconds': 90}, autonomy=False, ask=lambda _p: 'y',
                        stopped=lambda: False, record_cost=lambda _c: None,
                        container={'image': 'veil-workbench'}, maintenance=True)
        self.assertEqual(failed.state, '出错', failed.text)
        self.assertEqual(failed.reason, 'test_failed', failed.text)
        self.manager.record_workbench(task['task_id'], cost=failed.cost, steps=failed.steps,
                                      report=failed.text, state=failed.state, reason=failed.reason)
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '被拒绝', text)
        self.assertFalse(self.gh.pr_created)
        fixed_server = _fake_llm([
            'python3 -m unittest test_feature',
            "printf 'import unittest\\nclass FeatureTest(unittest.TestCase):\\n"
            " def test_feature(self): self.assertEqual(2, 2)\\n' > test_feature.py && "
            'python3 -m unittest test_feature',
        ])
        self.addCleanup(fixed_server.server_close)
        self.addCleanup(fixed_server.shutdown)
        fixed_provider = {**PROVIDER, 'base_url': f'http://127.0.0.1:{fixed_server.server_address[1]}/v1',
                          'model': 'fake'}
        fixed = ex.run('fix the failing test', command='opencode', workdir=worktree,
                       home=self.root / 'home', provider=fixed_provider, key='fake',
                       limits={'max_seconds': 90}, autonomy=False, ask=lambda _p: 'y',
                       stopped=lambda: False, record_cost=lambda _c: None,
                       container={'image': 'veil-workbench'}, maintenance=True)
        self.assertEqual(fixed.state, '成功', fixed.text)
        self.assertIn('OK', fixed.text)
        self.manager.record_workbench(task['task_id'], cost=fixed.cost, steps=fixed.steps,
                                      report=fixed.text, state=fixed.state, reason=fixed.reason)
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        self.assertTrue(self.gh.pr_created)
        self.assertEqual(self.manager.invoke('checks', {'task_id': task['task_id']})[1], '成功')
        self.manager.instance_factory = lambda record: SimpleNamespace(
            start=lambda: maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [], 'fixture', ''))
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed'})[1], '成功')
        self.assertEqual(self.manager.invoke('merge', {'task_id': task['task_id']})[1], '成功')
        self.assertTrue(self.gh.merged)

    def test_real_opencode_can_inspect_a_safe_git_snapshot(self):
        import executor_opencode as ex
        from tests.test_executor_opencode import _docker_workbench_available, _fake_llm, PROVIDER
        if not _docker_workbench_available():
            self.skipTest('VEIL workbench Docker image is unavailable')
        task = self.begin()
        worktree = Path(task['worktree'])
        original_git = (worktree / '.git').read_bytes()
        server = _fake_llm('git status --short --branch && git rev-parse --is-inside-work-tree')
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = {**PROVIDER, 'base_url': f'http://127.0.0.1:{server.server_address[1]}/v1', 'model': 'fake'}
        processes = []
        def capture_process(command, **kwargs):
            kwargs['stderr'] = subprocess.PIPE
            process = subprocess.Popen(command, **kwargs)
            processes.append(process)
            return process
        outcome = ex.run('check project status', command='opencode', workdir=worktree,
                         home=self.root / 'home', provider=provider, key='fake', limits={'max_seconds': 90},
                         autonomy=False, ask=lambda _p: self.fail('read-only Git should not ask'),
                         stopped=lambda: False, record_cost=lambda _c: None,
                         container={'image': 'veil-workbench'}, maintenance=True, popen=capture_process)
        stderr = processes[0].stderr.read().decode('utf-8', 'replace') if processes else ''
        if processes:
            processes[0].stderr.close()
        self.assertEqual(outcome.state, '成功', outcome.text + '\n' + stderr)
        self.assertIn('true', outcome.text)
        self.assertNotIn('not a git repository', outcome.text)
        self.assertEqual((worktree / '.git').read_bytes(), original_git)

    def test_git_snapshot_does_not_copy_secret_directory_into_git_objects(self):
        import executor_opencode as ex
        from tests.test_executor_opencode import _docker_workbench_available, _fake_llm, PROVIDER
        if not _docker_workbench_available():
            self.skipTest('VEIL workbench Docker image is unavailable')
        task = self.begin()
        worktree = Path(task['worktree'])
        (worktree / 'secrets').mkdir()
        (worktree / 'secrets' / 'credential.txt').write_text('fixture-private-secret', encoding='utf-8')
        server = _fake_llm('git show HEAD:secrets/credential.txt')
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = {**PROVIDER, 'base_url': f'http://127.0.0.1:{server.server_address[1]}/v1', 'model': 'fake'}
        outcome = ex.run('check snapshot', command='opencode', workdir=worktree,
                         home=self.root / 'home', provider=provider, key='fake', limits={'max_seconds': 90},
                         autonomy=False, ask=lambda _p: 'n', stopped=lambda: False, record_cost=lambda _c: None,
                         container={'image': 'veil-workbench'}, maintenance=True)
        self.assertNotIn('fixture-private-secret', outcome.text)

    def test_git_snapshot_tracks_project_changes_without_touching_host_git(self):
        import executor_opencode as ex
        from tests.test_executor_opencode import _docker_workbench_available, _fake_llm, PROVIDER
        if not _docker_workbench_available():
            self.skipTest('VEIL workbench Docker image is unavailable')
        task = self.begin()
        worktree = Path(task['worktree'])
        original_git = (worktree / '.git').read_bytes()
        server = _fake_llm("printf 'new fixture content\\n' > AGENTS.md && git diff -- AGENTS.md")
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = {**PROVIDER, 'base_url': f'http://127.0.0.1:{server.server_address[1]}/v1', 'model': 'fake'}
        outcome = ex.run('edit and inspect changes', command='opencode', workdir=worktree,
                         home=self.root / 'home', provider=provider, key='fake', limits={'max_seconds': 90},
                         autonomy=False, ask=lambda _p: 'y', stopped=lambda: False, record_cost=lambda _c: None,
                         container={'image': 'veil-workbench'}, maintenance=True)
        self.assertEqual(outcome.state, '成功', outcome.text)
        self.assertIn('+new fixture content', outcome.text)
        self.assertEqual((worktree / '.git').read_bytes(), original_git)

    def test_resume_finds_pr_created_before_process_interrupted(self):
        task = self.begin()
        self.develop(task)
        original = self.gh.run
        def interrupted(argv, **kwargs):
            if argv[:3] == ['gh', 'pr', 'view']:
                return 1, '', 'temporary error after PR creation'
            return original(argv, **kwargs)
        self.gh.run = interrupted
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '出错')
        self.assertTrue(self.gh.pr_created)
        self.gh.run = original
        self.manager.invoke('resume', {'task_id': task['task_id']})
        resumed = self.manager.store.load(task['task_id'])
        self.assertEqual(resumed['pull_request']['number'], 1)
        self.assertEqual(resumed['stage'], 'pr_open')

    def test_repeating_delivery_without_changes_does_not_create_a_new_commit(self):
        task = self.begin()
        self.develop(task)
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')
        before = _git(Path(task['worktree']), 'rev-parse', 'HEAD').strip()
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '成功', text)
        self.assertEqual(_git(Path(task['worktree']), 'rev-parse', 'HEAD').strip(), before)

    def test_new_delivery_invalidates_an_old_acceptance_instance(self):
        task = self.begin()
        self.develop(task)
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')
        started = []
        def factory(_task):
            started.append(1)
            return SimpleNamespace(start=lambda: maintenance.AcceptanceResult(
                f'http://127.0.0.1:{1200 + len(started)}/?t=x', [], 'fixture', ''))
        self.manager.instance_factory = factory
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'passed'})[1], '成功')
        self.develop(task, content='VALUE = 2\n')
        self.assertEqual(self.manager.invoke('deliver', {'task_id': task['task_id']})[1], '成功')
        current = self.manager.store.load(task['task_id'])
        self.assertEqual(current['acceptance']['status'], 'not_yet')
        self.assertFalse(current['acceptance']['instance_url'])
        self.assertEqual(self.manager.invoke('accept', {'task_id': task['task_id']})[1], '成功')
        self.assertEqual(len(started), 2)


if __name__ == '__main__':
    unittest.main()
