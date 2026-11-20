"""Ticket 403: each model request is recorded, with tools, redaction and honest truncation flags."""
import json
import tempfile
import unittest
from pathlib import Path

import request_snapshots


def tool(name, description='Do a thing.', parameters=None):
    return {'type': 'function', 'function': {'name': name, 'description': description,
                                             'parameters': parameters or {'type': 'object'}}}


class SnapshotBuildTests(unittest.TestCase):
    def test_labels_images_redaction_and_tools(self):
        messages = [
            {'role': 'system', 'content': 'persona text'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'my api_key=sk-abcdefghijklmnop'},
                                         {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,xx'}}]},
        ]
        record = request_snapshots.build(messages, ['identity.persona', 'user'], request_id='r1', step=1,
                                         kind='chat', model='fake', estimated_tokens=10, cache_prefix=1,
                                         memory_ids=['m1'], tools=[tool('lookup')])
        self.assertEqual(record['blocks'][0]['title'], 'Peter 的人设')
        self.assertIn('[图片]', record['blocks'][1]['text'])
        self.assertNotIn('sk-abcdefghijklmnop', record['blocks'][1]['text'])
        self.assertIn('[已隐藏]', record['blocks'][1]['text'])
        self.assertEqual(record['redacted'], 1)
        self.assertEqual(record['tools'][0]['name'], 'lookup')
        self.assertTrue(record['complete'])
        self.assertEqual(record['cache_prefix'], 1)

    def test_truncation_is_recorded(self):
        record = request_snapshots.build([{'role': 'system', 'content': 'x' * 20000}], ['history'],
                                         request_id='r1', kind='chat', model='fake')
        self.assertTrue(record['blocks'][0]['truncated'])
        self.assertFalse(record['complete'])
        self.assertEqual(record['truncated_blocks'], 1)
        self.assertIn('以下省略', record['blocks'][0]['text'])

    def test_oversize_tools_are_marked_incomplete(self):
        big = {'type': 'object', 'properties': {'a': {'type': 'string', 'description': 'x' * 70000}}}
        record = request_snapshots.build([{'role': 'user', 'content': 'x'}], ['user'], request_id='r1',
                                         kind='chat', model='fake', tools=[tool('one', parameters=big),
                                                                           tool('two', parameters=big)])
        self.assertFalse(record['tools_complete'])
        self.assertFalse(record['complete'])


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def save(self, request_id, step=1, kind='chat', message_ids=()):
        record = request_snapshots.build([{'role': 'user', 'content': request_id}], ['user'],
                                         request_id=request_id, step=step, kind=kind, model='fake',
                                         message_ids=message_ids)
        request_snapshots.save(self.root, 'role', record)
        return record

    def test_steps_are_kept_together(self):
        self.save('r1', 1, message_ids=['m1'])
        self.save('r1', 2)
        self.save('r2', 1)
        bundle = request_snapshots.for_request(self.root, 'role', 'r1')
        self.assertEqual([row['step'] for row in bundle['steps']], [1, 2])
        self.assertEqual(request_snapshots.get(self.root, 'role', 'r1')['step'], 2)
        self.assertEqual(request_snapshots.get(self.root, 'role', 'r1', 1)['step'], 1)

    def test_recent_groups_by_request(self):
        self.save('r1', 1)
        self.save('r1', 2)
        self.save('r2', 1)
        rows = request_snapshots.recent(self.root, 'role')
        self.assertEqual([row['request_id'] for row in rows], ['r2', 'r1'])
        self.assertEqual(rows[1]['steps'], 2)

    def test_prune_keeps_newest_requests(self):
        for index in range(request_snapshots.KEEP_REQUESTS + 5):
            self.save('r%03d' % index, 1)
            self.save('r%03d' % index, 2)
        names = {path.name for path in request_snapshots.folder(self.root, 'role').glob('*.json')}
        self.assertEqual(len(names), request_snapshots.KEEP_REQUESTS * 2)
        self.assertIn('r%03d.01.json' % (request_snapshots.KEEP_REQUESTS + 4), names)
        self.assertNotIn('r000.01.json', names)

    def test_unsafe_request_id_is_refused(self):
        self.assertIsNone(request_snapshots.for_request(self.root, 'role', '../secret'))
        self.assertIsNone(request_snapshots.get(self.root, 'role', 'a/b'))


if __name__ == '__main__':
    unittest.main()
