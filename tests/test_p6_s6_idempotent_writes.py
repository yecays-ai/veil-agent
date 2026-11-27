"""Ticket 461: S6 gate — persisted idempotency for cognition and ongoing writes."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from veil.brain import learned_context as lc_mod
from veil.brain.learned_context import LearnedContext


class IdempotentWriteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = self.reopen()
        self.profile.root.mkdir()

    def reopen(self):
        return LearnedContext(self.root, 'fake', {'file': 'learned-context.json'})

    def versions_count(self):
        try:
            return len([line for line in self.profile.versions.read_text(encoding='utf-8').splitlines()
                        if line.strip()])
        except FileNotFoundError:
            return 0

    def test_profile_append_retry_returns_same_result_and_writes_once(self):
        args = {'section': 'facts', 'text': 'Lan likes blue.'}
        first = self.profile.invoke('profile_append', dict(args))
        versions = self.versions_count()
        body = self.profile.path.read_bytes()
        second = self.profile.invoke('profile_append', dict(args))
        self.assertEqual(first, second)
        self.assertEqual(body, self.profile.path.read_bytes())
        self.assertEqual(versions, self.versions_count())
        self.assertEqual('Lan likes blue.', self.profile.read()['facts']['text'])
        self.assertEqual(1, self.profile.read()['facts']['text'].count('Lan likes blue.'))

    def test_replace_retry_after_old_text_is_gone_returns_original_success(self):
        self.profile.invoke('profile_append', {'section': 'growth', 'text': 'Old note.'})
        args = {'section': 'growth', 'old_text': 'Old note.', 'new_text': 'New note.'}
        first = self.profile.invoke('profile_replace', dict(args))
        versions = self.versions_count()
        second = self.profile.invoke('profile_replace', dict(args))
        self.assertEqual(first, second)
        self.assertEqual(versions, self.versions_count())
        self.assertEqual('New note.', self.profile.read()['growth']['text'])

    def test_ongoing_add_retry_returns_same_item_id_and_one_row(self):
        args = {'action': 'add', 'text': 'Buy stamps.'}
        first = self.profile.invoke('ongoing_update', dict(args))
        second = self.profile.invoke('ongoing_update', dict(args))
        self.assertEqual(first, second)
        items = self.profile.read()['ongoing']['items']
        self.assertEqual(1, len(items))
        self.assertIn(items[0]['id'], first[0])

    def test_relationship_append_retry_returns_same_item_and_one_version(self):
        self.profile.root.joinpath('chat.jsonl').write_text(
            json.dumps({'id': 'u1', 'role': 'user', 'content': '周末不发消息。'},
                       ensure_ascii=False) + '\n', encoding='utf-8')
        args = {'section': 'relationship', 'text': '周末不发消息。', 'status': 'confirmed',
                'sources': ['chat:u1']}
        first = self.profile.invoke('profile_append', dict(args))
        versions = self.versions_count()
        second = self.profile.invoke('profile_append', dict(args))
        self.assertEqual(first, second)
        self.assertEqual(versions, self.versions_count())
        items = self.profile.read()['relationship']['items']
        self.assertEqual(1, len(items))

    def test_retry_after_lost_response_and_process_restart(self):
        self.profile.invoke('profile_append', {'section': 'facts', 'text': 'Warm tea.'})
        args = {'section': 'facts', 'text': 'A quiet morning walk.'}
        real_commit = self.profile._commit_with_version

        def commit_then_lose_response(*a, **kw):
            real_commit(*a, **kw)
            raise ConnectionError('response lost after commit')

        with patch.object(self.profile, '_commit_with_version', side_effect=commit_then_lose_response):
            with self.assertRaises(ConnectionError):
                self.profile.invoke('profile_append', dict(args))
        self.assertEqual(1, self.profile.read()['facts']['text'].count('A quiet morning walk.'))
        versions = self.versions_count()
        restarted = self.reopen()
        replay = restarted.invoke('profile_append', dict(args))
        self.assertEqual('成功', replay[1])
        self.assertEqual(versions, self.versions_count())
        self.assertEqual(1, restarted.read()['facts']['text'].count('A quiet morning walk.'))

    def test_concurrent_retries_write_one_row_and_one_version(self):
        args = {'action': 'add', 'text': 'Water the plants.'}
        results = []
        errors = []

        def worker():
            try:
                results.append(self.profile.invoke('ongoing_update', dict(args)))
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(1, len({row for row in results}))
        self.assertEqual(1, len(self.profile.read()['ongoing']['items']))
        self.assertEqual(2, self.versions_count())  # migration + one add

    def test_explicit_key_cannot_be_reused_for_different_arguments(self):
        first = self.profile.invoke('profile_append', {'section': 'facts', 'text': 'A.',
                                                       'idempotency_key': 'op-1'})
        self.assertEqual('成功', first[1])
        second = self.profile.invoke('profile_append', {'section': 'facts', 'text': 'B.',
                                                        'idempotency_key': 'op-1'})
        self.assertEqual('被拒绝', second[1])
        self.assertIn('幂等键', second[0])
        self.assertEqual('A.', self.profile.read()['facts']['text'])

    def _ongoing_id(self):
        return self.profile.read()['ongoing']['items'][-1]['id']

    def test_complete_reopen_complete_does_not_replay_the_first_receipt(self):
        self.profile.invoke('ongoing_update', {'action': 'add', 'text': 'Buy stamps.'})
        item_id = self._ongoing_id()
        done = {'action': 'complete', 'id': item_id}
        first = self.profile.invoke('ongoing_update', dict(done))
        self.assertEqual('成功', first[1])
        reopened = self.profile.invoke('ongoing_update', {'action': 'reopen', 'id': item_id})
        self.assertEqual('成功', reopened[1])
        self.assertEqual('active', self.profile.read()['ongoing']['items'][0]['status'])
        versions = self.versions_count()
        second = self.profile.invoke('ongoing_update', dict(done))
        self.assertEqual('成功', second[1])
        self.assertEqual('completed', self.profile.read()['ongoing']['items'][0]['status'])
        self.assertGreater(self.versions_count(), versions)
        versions = self.versions_count()
        replay = self.profile.invoke('ongoing_update', dict(done))
        self.assertEqual(second, replay)
        self.assertEqual(versions, self.versions_count())

    def test_readd_after_complete_is_a_new_item(self):
        args = {'action': 'add', 'text': 'Buy stamps.'}
        self.profile.invoke('ongoing_update', dict(args))
        item_id = self._ongoing_id()
        self.profile.invoke('ongoing_update', {'action': 'complete', 'id': item_id})
        again = self.profile.invoke('ongoing_update', dict(args))
        self.assertEqual('成功', again[1])
        items = self.profile.read()['ongoing']['items']
        self.assertEqual(2, len(items))
        self.assertEqual({'completed', 'active'}, {row['status'] for row in items})

    def test_append_retry_survives_an_unrelated_edit(self):
        self.profile.invoke('profile_append', {'section': 'facts', 'text': 'Paragraph A.'})
        args = {'section': 'facts', 'text': 'Paragraph B.'}
        first = self.profile.invoke('profile_append', dict(args))
        self.assertEqual('成功', first[1])
        edited = self.profile.invoke('profile_replace', {
            'section': 'facts', 'old_text': 'Paragraph A.', 'new_text': 'Paragraph C.',
        })
        self.assertEqual('成功', edited[1])
        versions = self.versions_count()
        body = self.profile.path.read_bytes()
        second = self.profile.invoke('profile_append', dict(args))
        self.assertEqual(first, second)
        self.assertEqual(body, self.profile.path.read_bytes())
        self.assertEqual(versions, self.versions_count())
        text = self.profile.read()['facts']['text']
        self.assertEqual(1, text.count('Paragraph B.'))
        self.assertIn('Paragraph C.', text)
        self.assertNotIn('Paragraph A.', text)

    def test_relationship_refresh_does_not_replay_after_sources_change(self):
        self.profile.root.joinpath('chat.jsonl').write_text(
            json.dumps({'id': 'uA', 'role': 'user', 'content': '周末不发消息。'}, ensure_ascii=False) + '\n'
            + json.dumps({'id': 'uB', 'role': 'user', 'content': '周末不发消息。'}, ensure_ascii=False) + '\n',
            encoding='utf-8')
        base = {'section': 'relationship', 'text': '周末不发消息。', 'status': 'confirmed'}
        first = self.profile.invoke('profile_append', {**base, 'sources': ['chat:uA']})
        self.assertEqual('成功', first[1])
        self.assertEqual(['chat:uA'], self.profile.read()['relationship']['items'][0]['sources'])
        second = self.profile.invoke('profile_append', {**base, 'sources': ['chat:uB']})
        self.assertEqual('成功', second[1])
        self.assertEqual(['chat:uB'], self.profile.read()['relationship']['items'][0]['sources'])
        versions = self.versions_count()
        again = self.profile.invoke('profile_append', {**base, 'sources': ['chat:uA']})
        self.assertEqual('成功', again[1])
        self.assertEqual(['chat:uA'], self.profile.read()['relationship']['items'][0]['sources'])
        self.assertGreater(self.versions_count(), versions)
        versions = self.versions_count()
        retry = self.profile.invoke('profile_append', {**base, 'sources': ['chat:uA']})
        self.assertEqual(again, retry)
        self.assertEqual(versions, self.versions_count())
        self.assertEqual(1, len(self.profile.read()['relationship']['items']))

    def test_expired_explicit_key_is_rejected_instead_of_rewriting(self):
        with patch.object(lc_mod, 'EXPLICIT_IDEMPOTENCY_LIMIT', 2):
            first = self.profile.invoke('profile_append', {
                'section': 'facts', 'text': 'Keep this paragraph.', 'idempotency_key': 'keep-me',
            })
            self.assertEqual('成功', first[1])
            self.profile.invoke('profile_append', {
                'section': 'facts', 'text': 'Second paragraph.', 'idempotency_key': 'k2',
            })
            self.profile.invoke('profile_append', {
                'section': 'facts', 'text': 'Third paragraph.', 'idempotency_key': 'k3',
            })
            versions = self.versions_count()
            again = self.profile.invoke('profile_append', {
                'section': 'facts', 'text': 'Keep this paragraph.', 'idempotency_key': 'keep-me',
            })
        self.assertEqual('被拒绝', again[1])
        self.assertIn('过期', again[0])
        self.assertEqual(versions, self.versions_count())
        self.assertEqual(1, self.profile.read()['facts']['text'].count('Keep this paragraph.'))

    def test_evicted_auto_receipt_still_does_not_append_twice(self):
        with patch.object(lc_mod, 'IDEMPOTENCY_LIMIT', 1):
            self.profile.invoke('profile_append', {'section': 'facts', 'text': 'Alpha stay.'})
            self.profile.invoke('profile_append', {'section': 'facts', 'text': 'Beta push.'})
            versions = self.versions_count()
            again = self.profile.invoke('profile_append', {'section': 'facts', 'text': 'Alpha stay.'})
        self.assertEqual('成功', again[1])
        self.assertEqual(versions, self.versions_count())
        self.assertEqual(1, self.profile.read()['facts']['text'].count('Alpha stay.'))


if __name__ == '__main__':
    unittest.main()
