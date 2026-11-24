"""P3-0: the production character config must stay Brain-authoritative.

The 2026-11-25 formal cutover made Brain the single memory write authority. That value used to
live only in the local working tree, and update-main's stash + reset --hard silently reverted it.
It is now committed to main, so this regression test fails if a future change reverts the
transitional [memory.brain] table before P3 removes it.
"""
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PeterBrainDefaultTests(unittest.TestCase):
    def test_peter_config_keeps_brain_as_authority(self):
        with (ROOT / 'characters' / 'peter' / 'character.toml').open('rb') as handle:
            cfg = tomllib.load(handle)
        memory = cfg['memory']
        self.assertIs(memory['brain']['write_authority'], True)
        self.assertIs(memory['brain']['shadow'], False)
        self.assertEqual(memory['retrieval']['backend'], 'brain')


if __name__ == '__main__':
    unittest.main()
