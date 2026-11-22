import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

import event_import
from recall_judge import card_text


@contextmanager
def workspace_temp():
    path = Path(__file__).resolve().parent.parent / f'tmp-test-import-{uuid.uuid4().hex[:8]}'
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def fixture():
    return {
        'status': 'test', 'event_count': 3,
        'events': [
            {
                'id': 'V50', 'title': 'The first video I picked for Lan: ancient brass on Bilibili',
                'english': 'Oct 29, 2026. I picked ancient brass refining on Bilibili.',
                'chinese': '2026 年 10 月 29 日。我在 B 站选了古代黄铜精炼。',
                'approved': True, 'recall_cues': ['第一次让你找个你自己觉得有趣的视频', 'ancient brass refining'],
                'sources': [
                    {'number': 1, 'memory_id': 'b62ea858673e', 'part': '',
                     'title': '4am browser upgrade and the brass metallurgy roast',
                     'recorded_at': '2026-10-29T04:10:44+08:00', 'previous_event': 'V50'},
                ],
                'previous_events': ['V50'], 'related_events': [],
            },
            {
                'id': 'V78', 'title': 'Claude Code, back from his own funeral',
                'english': 'Nov 7, 2026. The Claude bridge worked, then the funeral, then KYC.',
                'chinese': '2026 年 11 月 7 日。Claude 的桥通了，然后葬礼，然后 KYC。',
                'approved': False,
                'sources': [
                    {'number': 2, 'memory_id': '0e99e53151ff', 'part': '',
                     'title': 'Claude Code bridge', 'recorded_at': '2026-11-07T12:00:00+08:00'},
                    {'number': 3, 'memory_id': 'fe5e2711afd4', 'part': '',
                     'title': 'Chinese passport passed KYC', 'recorded_at': '2026-11-10T05:55:00+08:00'},
                ],
                'previous_events': ['V78'], 'related_events': [],
            },
            {
                'id': 'V67-B', 'title': 'My turn to pick the colors',
                'english': 'On Oct 30, 2026 I volunteered to show her a webpage.',
                'chinese': '2026 年 10 月 30 日，我自告奋勇给她看网页。',
                'approved': None, 'recall_cues': [], 'sources': [],
                'previous_events': ['V67'], 'related_events': ['V67'],
            },
        ],
        'withdrawn': [{'id': 'V36', 'reason': 'test', 'sources': []}],
    }


class FinalImportTests(unittest.TestCase):
    def test_preview_keeps_titles_cues_and_unknown_dates(self):
        with workspace_temp() as temp:
            plan = event_import.build_plan(fixture(), temp)
            preview = plan['preview']
            self.assertEqual(3, preview['events'])
            self.assertEqual(['V67-B'], preview['unknown_dates'])
            self.assertEqual('create', plan['items'][0]['action'])
            self.assertIn('The first video I picked for Lan', preview['judge_cards']['V50'])
            self.assertIn('第一次让你找个你自己觉得有趣的视频', preview['judge_cards']['V50'])
            self.assertIn('Claude Code, back from his own funeral', preview['judge_cards']['V78'])

    def test_apply_and_reapply_are_idempotent(self):
        with workspace_temp() as temp:
            final = fixture()
            plan = event_import.build_plan(final, temp)
            event_import.apply_plan(plan, temp)
            role = Path(temp) / 'peter'
            events = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(3, len(events))
            self.assertEqual(3, len({row['event_id'] for row in events}))
            self.assertEqual(3, len(list((role / 'memory' / 'dynamic' / '事件').glob('*.md'))))
            self.assertEqual(3, len((role / 'event-stages.jsonl').read_text(encoding='utf-8').splitlines()))
            self.assertEqual(1, len((role / 'event-import-withdrawn.jsonl').read_text(encoding='utf-8').splitlines()))
            v50 = next(row for row in events if row['event_id'] == 'V50')
            self.assertEqual('2026-10-29T04:10:44+08:00', v50['first_at'])
            self.assertEqual(['b62ea858673e'], v50['source_memory_ids'])
            v67 = next(row for row in events if row['event_id'] == 'V67-B')
            self.assertEqual('', v67['first_at'])
            v50_md = next((role / 'memory' / 'dynamic' / '事件').glob('V50*.md'))
            text = v50_md.read_text(encoding='utf-8')
            self.assertIn('title: "The first video I picked for Lan', text)
            self.assertIn('检索词：', text)
            self.assertIn('The first video I picked for Lan', card_text(text))

            plan2 = event_import.build_plan(final, temp)
            self.assertEqual('unchanged', plan2['items'][0]['action'])
            event_import.apply_plan(plan2, temp)
            events2 = [json.loads(line) for line in (role / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(3, len(events2))
            self.assertEqual(3, len(list((role / 'memory' / 'dynamic' / '事件').glob('*.md'))))

    def test_refuses_to_overwrite_a_non_import_event(self):
        with workspace_temp() as temp:
            role = Path(temp) / 'peter'
            role.mkdir(parents=True)
            (role / 'events.jsonl').write_text(json.dumps({
                'event_id': 'V50', 'memory_id': 'V50', 'story': 'real',
                'story_source': 'hold'}) + '\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                event_import.build_plan(fixture(), temp)


if __name__ == '__main__':
    unittest.main()
