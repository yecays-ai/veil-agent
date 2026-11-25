"""P3-2: the production character config no longer carries a backend switch.

Brain is the memory backend by construction; the transitional [memory.brain] table and the
retrieval backend switch must not return and silently reopen Ombre or shadow sync.
"""
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PeterBrainDefaultTests(unittest.TestCase):
    def test_peter_config_has_no_backend_switch(self):
        with (ROOT / 'characters' / 'peter' / 'character.toml').open('rb') as handle:
            cfg = tomllib.load(handle)
        memory = cfg['memory']
        self.assertNotIn('brain', memory)
        self.assertNotIn('ombre_port', memory)
        self.assertNotIn('backend', memory.get('retrieval') or {})


if __name__ == '__main__':
    unittest.main()
