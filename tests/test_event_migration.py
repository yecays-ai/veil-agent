import tempfile
import unittest
from pathlib import Path

import event_migration


class MigrationPreviewTests(unittest.TestCase):
    def test_preview_groups_shared_memories_and_never_writes_memories(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / 'data' / 'fake' / 'memory' / 'dynamic'
            vault.mkdir(parents=True)
            files = {
                'a': ('Mem A', '2026-11-20T03:42:00+08:00', 'She was angry about the file list and the charcoal note.'),
                'b': ('Mem B', '2026-11-20T17:08:00+08:00', 'Later the charcoal note had one word: meh.'),
                'c': ('Mem C', '2026-11-20T18:29:00+08:00', 'The quantum conference in Oslo moved to Friday.'),
            }
            before = {}
            for key, (title, created, body) in files.items():
                path = vault / f'{key}.md'
                path.write_text(f'---\nid: {key}\ntitle: {title}\ncreated: {created}\n---\n{body}',
                                encoding='utf-8')
                before[key] = path.read_text(encoding='utf-8')
            rows = event_migration.propose(event_migration.read_memories(temp, 'fake'))
            self.assertEqual(len(rows), 3)
            self.assertEqual(len({row['proposed_event'] for row in rows}), 2)
            a = next(row for row in rows if row['memory_id'] == 'a')
            b = next(row for row in rows if row['memory_id'] == 'b')
            c = next(row for row in rows if row['memory_id'] == 'c')
            self.assertEqual(a['proposed_event'], b['proposed_event'])
            self.assertNotEqual(a['proposed_event'], c['proposed_event'])
            summary = event_migration.write_manifest(rows, Path(temp) / 'preview.jsonl')
            self.assertEqual(summary['memories'], 3)
            for key, text in before.items():
                self.assertEqual((vault / f'{key}.md').read_text(encoding='utf-8'), text)


if __name__ == '__main__':
    unittest.main()
