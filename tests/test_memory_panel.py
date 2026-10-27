import json
import unittest

from memory_native import parse_frontmatter
import test_native_memory

PARCEL = """---
activation_count: 0
created: '2026-10-27T13:00:56+08:00'
domain:
- 数字
- 人际
id: ed5d0ae68f8a
importance: 5
tags:
- parcel counter
- 菜鸟发货平台
title: Built her a parcel counter (180 boxes, one page)
type: dynamic
why_remembered: It''s the first thing I've made for the ugly part of her day
  rather than the fun part.
---

I built her a parcel counter.
"""


class FrontmatterTests(unittest.TestCase):
    def test_lists_quotes_and_wrapped_scalars(self):
        meta, body = parse_frontmatter(PARCEL)
        self.assertEqual(meta['created'], '2026-10-27T13:00:56+08:00')
        self.assertEqual(meta['domain'], ['数字', '人际'])
        self.assertEqual(meta['tags'], ['parcel counter', '菜鸟发货平台'])
        self.assertEqual(meta['why_remembered'],
                         "It''s the first thing I've made for the ugly part of her day rather than the fun part.")
        self.assertEqual(meta['title'], 'Built her a parcel counter (180 boxes, one page)')
        self.assertEqual(body.strip(), 'I built her a parcel counter.')

    def test_quoted_wrapped_scalar_is_unquoted_once_joined(self):
        meta, _ = parse_frontmatter("---\nwhy_remembered: 'It''s long\n  and wrapped'\n---\nbody")
        self.assertEqual(meta['why_remembered'], "It's long and wrapped")


class MemoryPanelTests(unittest.TestCase):
    setUp = test_native_memory.OmbreToolGuardTests.setUp

    def native(self):
        return self.tools.native

    def write(self, key, created, title, tags='[]', extra=''):
        (self.vault / f'{key}.md').write_text(
            f"---\nid: {key}\ncreated: '{created}'\ntitle: {title}\ntags: {tags}\n{extra}---\nbody of {key}\n",
            encoding='utf-8')

    def test_entries_are_newest_first_with_recall_counts(self):
        self.write('older', '2026-10-20T10:00:00+08:00', 'Older')
        self.write('newer', '2026-10-27T10:00:00+08:00', 'Newer')
        self.store.append('fake/context-log.jsonl', {'memory_ids': ['newer', 'older']})
        self.store.append('fake/context-log.jsonl', {'memory_ids': ['newer']})
        items = self.native().panel_entries()['items']
        self.assertEqual([item['memory_id'] for item in items][:2], ['newer', 'older'])
        self.assertEqual(items[0]['recalled'], 2)
        self.assertEqual(items[0]['body'], 'body of newer')

    def test_surprise_memories_never_send_their_text(self):
        self.write('hunt', '2026-10-25T06:46:28+08:00', 'The hunt key', tags='[惊喜, 生日]',
                   extra='why_remembered: secret reason\n')
        item = next(item for item in self.native().panel_entries()['items'] if item['memory_id'] == 'hunt')
        self.assertTrue(item['surprise'])
        self.assertEqual((item['body'], item['why'], item['tags']), ('', '', []))
        self.assertNotIn('body of hunt', json.dumps(self.native().panel_entries(), ensure_ascii=False))

    def test_lan_edit_goes_through_archived_trace(self):
        ok, message = self.native().panel_edit('mem001', {'content': 'corrected by Lan'})
        self.assertTrue(ok, message)
        self.assertEqual(self.backend.calls[-1], ('trace', {'bucket_id': 'mem001', 'content': 'corrected by Lan',
                                                             'delete': False}))
        version = self.store.read('fake/memory-versions.jsonl')[-1]
        self.assertIn('old memory body', version['old_content'])
        self.assertIn('Lan edited', version['operation_reason'])

    def test_edit_rejects_unknown_fields_empty_text_and_missing_memory(self):
        native = self.native()
        self.assertFalse(native.panel_edit('mem001', {'tags': 'x'})[0])
        self.assertFalse(native.panel_edit('mem001', {'content': '  '})[0])
        self.assertFalse(native.panel_edit('mem001', {'content': 'x' * 5000})[0])
        self.assertFalse(native.panel_edit('missing', {'content': 'x'})[0])
        self.assertEqual(self.backend.calls, [])

    def test_lan_delete_hides_and_asks_ombre_to_delete(self):
        ok, message = self.native().panel_delete('mem001')
        self.assertTrue(ok, message)
        self.assertIn(('trace', {'bucket_id': 'mem001', 'delete': True}), self.backend.calls)
        self.assertTrue(self.memory.is_hidden('mem001'))
        self.assertFalse(self.native().panel_delete('mem001')[0])


if __name__ == '__main__':
    unittest.main()
