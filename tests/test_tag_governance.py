"""P2-C: audited, reversible tag cleanup with controlled categories."""
import json
import sqlite3
import tempfile
import unittest

from veil.brain.storage import tag_governance
from veil.brain.storage import veil_brain


class TagGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = self.temp.name + '/brain.db'
        connection = sqlite3.connect(self.db)
        connection.executescript(veil_brain.SCHEMA)
        connection.execute("INSERT INTO memories(id,bucket,file_rel,title,name,body,body_sha256,"
                           "raw_markdown,raw_sha256,frontmatter_raw,frontmatter_json,tags_json,"
                           "domain_json) VALUES('m1','dynamic','dynamic/m1.md','T','T','body','',"
                           "'raw','','','{}','[\"Lan\",\"爱\"]','[\"恋爱\"]')")
        connection.execute("INSERT INTO memories(id,bucket,file_rel,title,name,body,body_sha256,"
                           "raw_markdown,raw_sha256,frontmatter_raw,frontmatter_json,tags_json,"
                           "domain_json) VALUES('m2','dynamic','dynamic/m2.md','T','T','body','',"
                           "'raw','','','{}','[\"work\"]','[\"工作\",\"恋爱\"]')")
        connection.commit()
        connection.close()

    def test_dry_run_write_verify_and_restore(self):
        connection = sqlite3.connect(self.db)
        try:
            dry = tag_governance.apply(connection, dry_run=True)
            self.assertEqual(dry['categories']['relationship'], 1)
            self.assertEqual(dry['categories']['unclassified'], 1)
            self.assertEqual(connection.execute("SELECT tags_json FROM memories WHERE id='m1'").fetchone()[0],
                             '["Lan","爱"]')
            tag_governance.apply(connection, dry_run=False)
            result = tag_governance.verify(connection)
            self.assertTrue(result['ok'])
            self.assertEqual(result['with_tags'], 0)
            self.assertEqual(connection.execute('SELECT body FROM memories WHERE id="m1"').fetchone()[0], 'body')
            tag_governance.restore_tags(connection)
            self.assertEqual(json.loads(connection.execute(
                "SELECT tags_json FROM memories WHERE id='m1'").fetchone()[0]), ['Lan', '爱'])
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
