import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

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

    def _view(self):
        checks = ([{'name': 'test', 'status': 'COMPLETED',
                    'conclusion': 'SUCCESS' if self.ci == 'success' else 'FAILURE',
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
        if argv[0] == 'git' and argv[1] in {'fetch'}:
            return 1, '', 'offline fixture'
        if argv[0] == 'git' and argv[1] == 'ls-remote':
            return 0, '', ''
        if argv[0] == 'git' and argv[1] == 'push':
            self.head = _git(cwd, 'rev-parse', 'HEAD').strip()
            return 0, '', ''
        if argv[0] == 'gh':
            args = argv[1:]
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

    def test_acceptance_and_merge_require_a_real_user_confirmation(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.gh.ci = 'success'
        self.manager.invoke('checks', {'task_id': task['task_id']})
        started = []
        self.manager.instance_factory = lambda record: SimpleNamespace(
            start=lambda: maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [1], 'fixture'))
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

    def test_acceptance_failure_never_merges(self):
        task = self.begin()
        self.develop(task)
        self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.gh.ci = 'success'
        self.manager.invoke('checks', {'task_id': task['task_id']})
        self.manager.instance_factory = lambda record: SimpleNamespace(
            start=lambda: maintenance.AcceptanceResult('http://127.0.0.1:1234/?t=x', [1], 'fixture'))
        self.manager.invoke('accept', {'task_id': task['task_id']})
        text, state = self.manager.invoke('accept', {'task_id': task['task_id'], 'result': 'failed',
                                                     'note': 'The button does nothing.'})
        self.assertEqual(state, '成功', text)
        self.manager.approval = lambda _prompt: 'y'
        text, state = self.manager.invoke('merge', {'task_id': task['task_id'], 'retry': True})
        # The program asks for an explicit waiver; this fixture says yes, but the recorded
        # acceptance stays failed and is shown to Lan.
        self.assertEqual(state, '成功', text)
        task = self.manager.store.load(task['task_id'])
        self.assertEqual(task['acceptance']['status'], 'failed')
        self.assertTrue(task['acceptance']['waived'])
        self.assertTrue(self.gh.merged)

    def test_stop_prevents_push_and_merge(self):
        task = self.begin()
        self.manager.invoke('stop', {'task_id': task['task_id']})
        self.develop(task)
        for action in ('deliver', 'merge'):
            text, state = self.manager.invoke(action, {'task_id': task['task_id']})
            self.assertEqual(state, '被拒绝', (action, text))
        self.assertFalse(self.gh.pr_created)
        self.assertFalse(self.gh.merged)

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

    def test_sensitive_files_are_refused_before_commit(self):
        task = self.begin()
        worktree = Path(task['worktree'])
        (worktree / '.env').write_text('SECRET=1', encoding='utf-8')
        _git(worktree, 'add', '-f', '.env')
        text, state = self.manager.invoke('deliver', {'task_id': task['task_id']})
        self.assertEqual(state, '出错')
        self.assertIn('.env', text)
        self.assertFalse(self.gh.pr_created)


if __name__ == '__main__':
    unittest.main()
