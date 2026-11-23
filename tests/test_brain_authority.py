"""P2-A: the independent writer must never run beside the read-only shadow."""
import unittest
from types import SimpleNamespace

import brain_sync


class AuthoritySettingsTests(unittest.TestCase):
    def test_brain_writer_and_shadow_are_mutually_exclusive(self):
        cfg = SimpleNamespace(id='fake', memory={'brain': {'write_authority': True, 'shadow': True}})
        with self.assertRaises(ValueError):
            brain_sync.attach(cfg, SimpleNamespace(root='/tmp/none'))

    def test_authority_alone_does_not_start_the_shadow(self):
        cfg = SimpleNamespace(id='fake', memory={'brain': {'write_authority': True, 'shadow': False}})
        self.assertIsNone(brain_sync.attach(cfg, SimpleNamespace(root='/tmp/none')))


if __name__ == '__main__':
    unittest.main()
