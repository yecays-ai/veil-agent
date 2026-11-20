"""Ticket 403: request snapshots keep what was really sent, without changing it."""
import os
import tempfile
import unittest
from pathlib import Path

import request_snapshots


class SnapshotBuildTests(unittest.TestCase):
    def test_labels_and_image_placeholder(self):
        messages = [
            {'role': 'system', 'content': 'persona text'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'look'},
                                         {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,xx'}}]},
        ]
        record = request_snapshots.build(messages, ['identity.persona', 'user'], request_id='r1',
                                         kind='chat', model='fake', estimated_tokens=10,
                                         cache_prefix=1, memory_ids=['m1'])
        self.assertEqual(record['blocks'][0]['title'], 'Peter 的人设')
        self.assertEqual(record['blocks'][0]['label'], 'identity.persona')
        self.assertIn('[图片]', record['blocks'][1]['text'])
        self.assertNotIn('base64', record['blocks'][1]['text'])
        self.assertEqual(record['cache_prefix'], 1)
        self.assertEqual(record['memory_ids'], ['m1'])

    def test_long_block_is_cut(self):
        record = request_snapshots.build([{'role': 'system', 'content': 'x' * 20000}], ['history'],
                                         request_id='r1', kind='chat', model='fake')
        self.assertLessEqual(record['blocks'][0]['chars'], request_snapshots.BLOCK_CHARS + 20)
        self.assertIn('以下省略', record['blocks'][0]['text'])


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def save(self, request_id, message_ids=(), kind='chat'):
        record = request_snapshots.build([{'role': 'user', 'content': request_id}], ['user'],
                                         request_id=request_id, kind=kind, model='fake',
                                         message_ids=message_ids)
        request_snapshots.save(self.root, 'role', record)
        return record

    def test_recent_get_and_message_lookup(self):
        self.save('r1', ['m1'])
        self.save('r2', ['m2'])
        self.assertEqual([row['request_id'] for row in request_snapshots.recent(self.root, 'role')], ['r2', 'r1'])
        self.assertEqual(request_snapshots.get(self.root, 'role', 'r1')['request_id'], 'r1')
        self.assertEqual(request_snapshots.for_message(self.root, 'role', 'm1')['request_id'], 'r1')
        self.assertIsNone(request_snapshots.for_message(self.root, 'role', 'missing'))

    def test_unsafe_id_is_refused(self):
        self.assertIsNone(request_snapshots.get(self.root, 'role', '../secret'))
        self.assertIsNone(request_snapshots.get(self.root, 'role', 'a/b'))

    def test_prune_keeps_newest(self):
        for index in range(request_snapshots.KEEP + 5):
            self.save('r%03d' % index)
        files = sorted(request_snapshots.folder(self.root, 'role').glob('*.json'))
        self.assertEqual(len(files), request_snapshots.KEEP)
        self.assertTrue(any(path.name == 'r%03d.json' % (request_snapshots.KEEP + 4) for path in files))


if __name__ == '__main__':
    unittest.main()
