"""Ticket 419: how automatic recall scales with the number of saved memories.

Builds fictional memory files (no model, no network, no paid call), runs the normal retrieval
selection and the whole-round budget, and reports wall time / peak memory / candidate and token
counts. This is the reproducible command referenced by the report:

    python scripts/recall_scale_probe.py --sizes 300,3000,10000

The event-link scan (``_ombre_event_sources``) and the directory pass are the parts that read the
vault, so this probe measures them honestly instead of hiding them behind a helper test.
"""
import argparse
import json
import random
import sys
import tempfile
import time
import tracemalloc
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veil.brain.memory_agent import Memory, vault_path  # noqa: E402
from veil.core.veil_agent import Budget, Character, Store  # noqa: E402


class ProbeBackend:
    """A fake Ombre: one search returns the fixed hit ids with the bodies actually written to disk.

    Reading the real files matters: a probe that returns short placeholders measures placeholder
    budgets, not the long-story continuation the ticket is about."""

    def __init__(self, vault, hit_ids):
        self.vault = Path(vault)
        self.hit_ids = list(hit_ids)
        self.calls = 0

    def _body(self, key):
        for path in self.vault.glob(f'{key}*.md'):
            raw = path.read_text(encoding='utf-8')
            return raw.split('\n---\n', 1)[-1].strip()
        return f'probe body for {key}'

    def call(self, name, args):
        self.calls += 1
        if name == 'breath':
            return ''
        if name == 'feel':
            return ''
        if name == 'breath_search':
            body = '\n'.join(f'[bucket_id:{key}]\n{self._body(key)}' for key in self.hit_ids)
            ids = json.dumps({'bucket_ids': self.hit_ids})
            return body + '\n=== ombre:result-ids ===\n```json\n' + ids + '\n```'
        raise AssertionError(name)


def write_vault(vault, count, hit_ids):
    vault.mkdir(parents=True, exist_ok=True)
    rng = random.Random(419)
    words = ('lamp brass tea rain station cat letter window morning train garden video '
             'spiral crane kite picnic dumpling umbrella cloud').split()
    for index in range(count):
        key = f'm{index:05d}'
        body = ' '.join(rng.choice(words) for _ in range(24))
        if key in hit_ids:
            body = ('\n\n'.join(
                f'Paragraph {part} of the long fictional story about the little brass lamp. ' * 4
                for part in range(4)) + '\n\nThe unique ending fact is at the very end of this story.')
        (vault / f'{key}.md').write_text(
            f"---\nid: {key}\ntitle: 'Probe {index}'\ncreated: '2026-10-{(index % 28) + 1:02d}T10:00:00'\n"
            f"importance: {rng.randint(1, 9)}\ntype: dynamic\n---\n{body}", encoding='utf-8')


def probe(count):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = Store(root / 'data')
        vault = vault_path(store.root, 'probe')
        hit_ids = [f'm{index:05d}' for index in range(8)]
        write_vault(vault, count, set(hit_ids))
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': Decimal(2),
                    'output_cny_per_million': Decimal(8), 'cached_input_cny_per_million': Decimal(1)}
        memory_cfg = {'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                      'base_url': 'https://example.invalid/v1',
                      'recall': {'cooldown_turns': 0, 'random_chance': 0}}
        cfg = Character('probe', 'Probe', 'fictional persona', 20, 1024, provider, 'fake', memory_cfg)
        backend = ProbeBackend(vault, hit_ids)
        tracemalloc.start()
        started = time.perf_counter()
        memory = Memory(cfg, store, Budget(store), None, backend)
        opened = time.perf_counter()
        memory.context('probe lamp brass tea ending', include_opening=False, defer=True)
        recalled = time.perf_counter()
        messages = memory.finish_recall(space=12000, history_tokens=3000, protected_tokens=300,
                                        calibration=0.4)
        finished = time.perf_counter()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        text = '\n'.join(message['content'] for message in messages)
        budget = memory.last_memory_budget or {}
        memory.__exit__(None, None, None)
        return {
            'memories': count,
            'open_s': round(opened - started, 3),
            'retrieve_select_s': round(recalled - opened, 3),
            'budget_s': round(finished - recalled, 3),
            'peak_mb': round(peak / (1024 * 1024), 2),
            'candidates_offered': 8,
            'injected': len(memory.last_context_refs),
            'injected_chars': len(text),
            'memory_tokens': budget.get('memory_used'),
            'memory_allowance': budget.get('memory_allowance'),
            'reason': budget.get('reason'),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sizes', default='300,3000,10000')
    args = parser.parse_args()
    sizes = [int(part) for part in args.sizes.split(',') if part.strip()]
    rows = []
    for size in sizes:
        row = probe(size)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    print('\n| memories | open s | retrieve+select s | budget s | peak MB | injected | tokens |')
    print('| --- | --- | --- | --- | --- | --- | --- |')
    for row in rows:
        print(f"| {row['memories']} | {row['open_s']} | {row['retrieve_select_s']} | "
              f"{row['budget_s']} | {row['peak_mb']} | {row['injected']} | {row['memory_tokens']} |")


if __name__ == '__main__':
    main()
