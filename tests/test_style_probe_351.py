"""Ticket 351 fixture: fictional chat, fake models, no network, no paid calls."""
import importlib.util
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('style_probe', ROOT / 'experiments/style-snippet-351/style_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

MODELS = [
    {'label': 'm1', 'input_cny_per_million': Decimal(2), 'cached_input_cny_per_million': Decimal('0.04'),
     'output_cny_per_million': Decimal(3)},
    {'label': 'm2', 'input_cny_per_million': Decimal(1), 'cached_input_cny_per_million': Decimal('0.1'),
     'output_cny_per_million': Decimal(8)},
]
SCENARIOS = json.loads((ROOT / 'experiments/style-snippet-351/scenarios.json').read_text(encoding='utf-8'))


class FakeModel:
    def __init__(self, label):
        self.label, self.seen = label, []

    def __call__(self, messages, max_tokens):
        self.seen.append(messages)
        has = any('亲口说过' in m['content'] for m in messages if m['role'] == 'system')
        return f'{self.label}:{"with" if has else "without"}:{messages[-1]["content"][:4]}', {
            'prompt_tokens': 1000, 'cached_tokens': 600, 'completion_tokens': 100}


class StyleProbeTests(unittest.TestCase):
    def test_candidates_skip_dev_talk_and_spread_over_days(self):
        rows = [{'role': 'user', 'content': '你在干嘛呢今天过得好吗', 'at': '2026-11-01T10:00'}]
        for day in range(1, 6):
            rows.append({'role': 'assistant', 'at': f'2026-11-0{day}T10:00',
                         'content': f'哼，第{day}天也还是想你一下下。\n我去看看工单 3{day} 的代码\n好'})
        rows.append({'role': 'assistant', 'at': '2026-11-01T11:00', 'content': '哼，第1天也还是想你一下下。'})
        lines = probe.candidate_lines(rows, count=4)
        self.assertEqual(len(lines), 4)
        self.assertTrue(all('想你' in line for line in lines))
        self.assertEqual(len(set(lines)), 4)  # duplicate line dropped, four different days

    def test_parse_numbers(self):
        self.assertEqual(probe.parse_numbers('1 3，5-7、3', 10), [1, 3, 5, 6, 7])
        with self.assertRaises(ValueError):
            probe.parse_numbers('11', 10)

    def test_snippets_sit_in_the_stable_prefix_before_user(self):
        msgs = probe.build_messages('人设', '关于她', ['原话一'], 'Peter', '你好')
        self.assertEqual([m['role'] for m in msgs], ['system', 'system', 'system', 'user'])
        self.assertEqual(msgs[0]['content'], '人设')
        self.assertIn('原话一', msgs[2]['content'])
        self.assertIn('不要照抄', msgs[2]['content'])
        bare = probe.build_messages('人设', '关于她', None, 'Peter', '你好')
        self.assertEqual(bare[:2], msgs[:2])  # the only difference is the snippet block

    def test_run_page_reveal_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            fakes = {m['label']: FakeModel(m['label']) for m in MODELS}
            results, spent = probe.run('人设', None, ['原话'], 'Peter', MODELS, SCENARIOS, tmp, fakes)
            self.assertEqual(len(results), 24)
            self.assertGreater(spent, 0)
            again, spent2 = probe.run('人设', None, ['原话'], 'Peter', MODELS, SCENARIOS, tmp, fakes)
            self.assertEqual(spent2, spent)  # resumable: nothing re-sent
            self.assertEqual(sum(len(f.seen) for f in fakes.values()), 24)
            page, key = probe.blind_page(results, MODELS, SCENARIOS, seed=7)
            self.assertNotIn('m1', page.replace('m1:', ''))  # no model label outside reply text
            for index, row in key.items():
                scenario = row['scenario']
                with_group = row['with']
                self.assertTrue(all(f'{label}:with:' in results[f'{scenario}|{label}|with']
                                    for label in row['order'][with_group]))
            answers = ' '.join(f'{i}{row["with"]}' for i, row in key.items() if i != '6')
            counts = probe.reveal(key, answers + ' 6一样')
            self.assertEqual(counts, {'snippets_better': 5, 'snippets_worse': 0, 'same': 1, 'unanswered': 0})

    def test_budget_cap_stops_before_overspending(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = [dict(MODELS[0], output_cny_per_million=Decimal(1_000_000))]  # 600 tokens = ¥600 worst case
            results, spent = probe.run('人设', None, ['原话'], 'Peter', big, SCENARIOS, tmp,
                                       {'m1': FakeModel('m1')})
            self.assertEqual(results, {})
            self.assertEqual(spent, 0)

    def test_output_inside_repo_refused(self):
        with self.assertRaises(SystemExit):
            probe.main(['select', '--out', str(ROOT / 'experiments/x'), '--numbers', '1'])


if __name__ == '__main__':
    unittest.main()
