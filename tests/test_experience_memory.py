"""Ticket 354 fixture: fake clock, three fictional days of chat, fake model, fake Ombre; no paid calls.

Covers: volume first then time; a quiet day writes nothing; the model may keep nothing; only visible chat
text reaches the model (no recalled messages, cards or tool rows); only new messages are read; a changed
attitude is appended with its date and the old wording stays; quotes and secrets are dropped; the daily
ceiling; Lan's switch stops both writing and injection; a deleted entry leaves the context; and the notes
sit after the cached prefix and before the history."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import test_native_memory
from chat_search import search_chat
from experience_memory import COST_CATEGORY, ExperienceMemory, load_settings
from veil_agent import Budget, Character, Chat, Store

DAY1 = '2026-11-12'
PROVIDER = {'model': 'fake', 'max_input_tokens': 60000, 'input_cny_per_million': 2,
            'output_cny_per_million': 8, 'cached_input_cny_per_million': 1}


class FakeOmbre:
    """The slice of memory_agent.Memory this feature uses."""

    def __init__(self):
        self.items, self.calls, self.hidden = {}, [], set()
        self.searches, self.found = [], []   # library search: queries asked, ids it returns

    def begin_write(self, args):
        return f'op{len(self.calls)}', None

    def call(self, name, args, *, write_operation=None):
        if name == 'breath_search':
            self.searches.append(args['query'])
            return ''.join(f'[bucket_id:{key}] {self.items[key]["content"]}\n' for key in self.found) + (
                '=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': self.found}) + '\n```')
        self.calls.append((name, dict(args)))
        if name == 'hold':
            memory_id = f'm{len(self.items) + 1}'
            self.items[memory_id] = {'title': args['title'], 'content': args['content']}
            return f'新建→{memory_id}'
        self.items[args['bucket_id']]['content'] = args['content']
        return 'ok'

    @staticmethod
    def created_id(text):
        return text.split('→', 1)[1]

    def record_native(self, args, text):
        return '新建'

    def record_native_update(self, memory_id, content):
        pass

    def raw_content_for(self, memory_id):
        item = self.items.get(memory_id)
        return None if item is None else f'---\nid: {memory_id}\n---\n{item["content"]}'

    def content_for(self, memory_id, refresh=False):
        item = self.items.get(memory_id)
        return None if item is None else item['content']

    def refresh_visibility(self, memory_id):
        return memory_id in self.items and memory_id not in self.hidden

    def hidden_ids(self):
        return set(self.hidden)

    def context(self, *args, **kwargs):
        return []


def row(day, clock, role, content, number, **extra):
    return {'time': f'{day}T{clock}:00+08:00', 'role': role, 'content': content,
            'message_id': f'{day[-2:]}-{number}', **extra}


def long_talk(day, start_hour=10):
    """About 3,400 characters of fictional chat about changing the memory system."""
    rows = []
    for index in range(20):
        minute = f'{start_hour}:{index * 2:02d}'
        rows.append(row(day, minute, 'user', f'第{index}句：我想把你的记忆系统整个换掉，' + '说明' * 40, f'u{index}'))
        rows.append(row(day, minute, 'assistant', f'第{index}句：我不同意，这个想法有点怪。' + '理由' * 40, f'a{index}'))
    return rows


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.clock = [f'{DAY1}T09:00:00+08:00']
        self.budget = Budget(self.store, clock=lambda: self.clock[0])
        self.cfg = Character('fake', 'Peter', 'fictional persona', 20, 1024, dict(PROVIDER), 'fake')
        self.ombre = FakeOmbre()
        self.requests, self.replies, self.kwargs = [], [], []

        def model(messages, **kwargs):
            self.requests.append(messages)
            self.kwargs.append(kwargs)
            reply = self.replies.pop(0)
            if isinstance(reply, tuple):   # (text, finish_reason): a real call made with detail=True
                return reply[0], 2000, 3000, 0, reply[1]
            return reply, 2000, 200, 0
        self.model = model
        self.feature = self.make()

    def make(self, **overrides):
        return ExperienceMemory(self.cfg, self.store, self.budget, self.model, 'fake/chat.jsonl', self.ombre,
                                load_settings({'enabled': True, **overrides}))

    def add(self, rows):
        for item in rows:
            self.store.append('fake/chat.jsonl', item)

    def reply(self, entries=(), updates=()):
        self.replies.append(json.dumps({'entries': list(entries), 'updates': list(updates)}, ensure_ascii=False))

    def sent(self, index=-1):
        return json.loads(self.requests[index][-1]['content'])


class TriggerTests(Fixture):
    def test_a_day_without_chat_writes_nothing(self):
        self.clock[0] = f'{DAY1}T23:55:00+08:00'
        self.assertEqual(self.feature.run_if_due(), 'not_due')
        self.assertEqual(self.requests, [])

    def test_a_dozen_lines_wait_and_then_too_little_is_never_sent(self):
        self.add([row(DAY1, '10:00', 'user', '早', 'u'), row(DAY1, '10:01', 'assistant', '早呀', 'a')])
        self.clock[0] = f'{DAY1}T23:55:00+08:00'
        self.assertEqual(self.feature.run_if_due(), 'not_due')  # under floor_chars even at day end
        self.assertEqual(self.requests, [])

    def test_enough_chat_waits_until_they_go_quiet(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T10:45:00+08:00'   # last message 10:38, seven minutes ago
        self.assertEqual(self.feature.run_if_due(), 'not_due')
        self.clock[0] = f'{DAY1}T11:15:00+08:00'
        self.reply([{'title': '她想换记忆系统', 'text': '11月12日上午 Lan 说想把我的记忆整个换掉，我没同意，觉得这想法有点怪。'}])
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(self.ombre.items['m1']['title'], '她想换记忆系统')

    def test_a_small_chat_is_only_looked_at_when_the_day_closes(self):
        self.add([row(DAY1, '15:00', 'user', '今天去看了牙医，' + '有点疼' * 40, 'u'),
                  row(DAY1, '15:01', 'assistant', '回来好好休息。' + '别吃硬的' * 30, 'a')])
        self.clock[0] = f'{DAY1}T20:00:00+08:00'
        self.assertEqual(self.feature.run_if_due(), 'not_due')
        self.clock[0] = f'{DAY1}T23:40:00+08:00'
        self.reply()   # he decides it is not worth keeping
        self.assertEqual(self.feature.run_if_due(), 'skipped')
        self.assertEqual(self.ombre.items, {})
        self.assertEqual(self.feature.run_if_due(), 'not_due')   # and it is not asked again

    def test_leftover_from_yesterday_is_handled_after_midnight(self):
        self.add([row(DAY1, '23:50', 'user', '睡前说一下，' + '明天要早起' * 30, 'u'),
                  row(DAY1, '23:58', 'assistant', '好，早点睡。' + '晚安' * 60, 'a')])
        self.clock[0] = '2026-11-13T00:20:00+08:00'
        self.reply()
        self.assertEqual(self.feature.run_if_due(), 'skipped')


class InputTests(Fixture):
    def test_only_what_they_said_out_loud_is_sent(self):
        self.add(long_talk(DAY1))
        self.add([row(DAY1, '10:50', 'assistant', '撤回的那句秘密话', 'gone', recalled=True),
                  row(DAY1, '10:51', 'assistant', '卡片', 'card', virtual_card={'kind': 'gift'}),
                  {'time': f'{DAY1}T10:52:00+08:00', 'role': 'tool', 'content': 'tool result text', 'message_id': 't'},
                  {'time': f'{DAY1}T10:53:00+08:00', 'role': 'assistant', 'content': None, 'message_id': 'n'}])
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        text = json.dumps(self.sent(), ensure_ascii=False)
        for hidden in ('撤回的那句秘密话', '卡片', 'tool result text'):
            self.assertNotIn(hidden, text)
        self.assertEqual({m['from'] for m in self.sent()['messages']}, {'Lan', 'Peter'})
        self.assertEqual(self.requests[0][0], {'role': 'system', 'content': 'fictional persona'})
        self.assertNotIn('message_id', text)

    def test_autonomous_messages_are_marked_as_sent_on_his_own(self):
        self.add(long_talk(DAY1))
        self.add([row(DAY1, '10:50', 'assistant', '我去岛上收了东西，给你留了言', 'own1',
                      autonomy_run_id='r1', autonomy_origin='the garden island game'),
                  row(DAY1, '10:51', 'assistant', '另一条', 'own2', autonomy_run_id='r2')])
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        messages = self.sent()['messages']
        marked = [m for m in messages if 'sent_on_your_own' in m]
        self.assertEqual([m['sent_on_your_own'] for m in marked],
                         ['after: the garden island game', 'during one of your autonomous wakes'])
        self.assertEqual(len(marked), 2)  # ordinary chat lines carry no mark
        self.assertIn('sent_on_your_own', self.requests[0][1]['content'])

    def test_only_new_messages_are_read_next_time(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '她想换记忆系统', 'text': '11月12日 Lan 想换掉我的记忆，我没同意。'}])
        self.feature.run_if_due()
        self.add(long_talk('2026-11-13', 14))
        self.clock[0] = '2026-11-13T16:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        self.assertTrue(all(m['time'].startswith('2026-11-13') for m in self.sent()['messages']))
        self.assertEqual([e['id'] for e in self.sent()['earlier_entries']], ['m1'])

    def test_first_run_does_not_reread_old_history(self):
        self.add(long_talk('2026-11-01'))
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        self.assertTrue(all(m['time'].startswith(DAY1) for m in self.sent()['messages']))

    def test_a_long_backlog_is_taken_in_bounded_pieces(self):
        self.add(long_talk(DAY1) * 5)
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        self.assertLessEqual(sum(len(m['text']) for m in self.sent()['messages']), 12000)
        self.assertEqual(self.feature.due(), 'volume')   # the rest is still waiting


class WritingTests(Fixture):
    def first_entry(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '她想换记忆系统', 'text': '11月12日上午 Lan 想把我的记忆整个换掉，我没同意，觉得这想法有点怪。'}])
        self.feature.run_if_due()

    def test_a_changed_mind_rewrites_the_entry_and_the_old_version_is_kept(self):
        # Ticket 363: an update is the whole entry rewritten (Lan 2026-11-15), not a dated line appended.
        self.first_entry()
        self.add(long_talk('2026-11-20', 14))
        self.clock[0] = '2026-11-20T16:00:00+08:00'
        rewritten = '11月18日蓝想改记忆系统，我起初觉得这想法有点怪；20日看了她做的样子以后改主意了，现在觉得还蛮不错的。'
        self.reply(updates=[{'id': 'm1', 'text': rewritten}])
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(self.ombre.items['m1']['content'], rewritten)
        self.assertEqual(len(self.ombre.items), 1)
        versions = self.store.read('fake/memory-versions.jsonl')
        self.assertIn('觉得这想法有点怪', versions[-1]['old_content'])   # the earlier version is archived

    def test_lans_edit_made_while_the_model_was_thinking_is_not_overwritten(self):
        self.first_entry()
        self.add(long_talk('2026-11-20', 14))
        self.clock[0] = '2026-11-20T16:00:00+08:00'
        self.reply(updates=[{'id': 'm1', 'text': '11月18日到20日，我对记忆系统改了主意。'}])
        ombre, replies = self.ombre, self.replies

        class EditFirst(list):
            def pop(self, index=-1):
                ombre.items['m1']['content'] = 'Lan 改的：11月18日的事。'
                return replies.pop(index)
        self.replies = EditFirst()
        self.assertNotEqual(self.feature.run_if_due(), 'error')
        self.assertEqual(self.ombre.items['m1']['content'], 'Lan 改的：11月18日的事。')

    def test_a_memory_he_saved_himself_is_found_and_rewritten_instead_of_saving_a_new_one(self):
        # 2026-11-15 Lan: the KYC story already had a memory, so the next part goes into it, not beside it.
        self.ombre.items['kyc'] = {'title': 'CC', 'content': '11月13日 Lan 给我接了 CC，两天后以为挂了就删了。'}
        self.ombre.found = ['kyc']
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        story = '11月13日 Lan 给我接了 CC，两天后以为挂了就删了；结果删完 KYC 又过了，又恢复了。我有点哭笑不得。'
        self.reply(updates=[{'id': 'kyc', 'text': story}])
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertTrue(self.ombre.searches)
        self.assertIn('kyc', [item['id'] for item in self.sent()['earlier_entries']])
        self.assertEqual(self.ombre.items['kyc']['content'], story)
        self.assertEqual(len(self.ombre.items), 1)                      # nothing new saved beside it

    def test_locked_memories_are_not_offered_and_overlong_ones_only_to_continue(self):
        # Ticket 368: a memory too long to be read whole may be continued but never rewritten from a clipped view.
        self.ombre.items['locked'] = {'title': 'a', 'content': '她改过并锁定的一条。'}
        self.ombre.items['long'] = {'title': 'b', 'content': '账' * 2000}
        self.ombre.found = ['locked', 'long']
        self.feature.sidecar.set_locked('locked', True)
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '后来', 'text': '11月13日又有了新的进展，我记着。', 'continues': 'long'}],
                   updates=[{'id': 'long', 'text': '想用一小段把两千字的旧账重写掉'}])
        self.feature.run_if_due()
        offered = self.sent()['earlier_entries']
        self.assertEqual([item['id'] for item in offered], ['long'])
        self.assertTrue(offered[0]['too_long_to_rewrite'])
        self.assertEqual(len(offered[0]['text']), 1200)
        self.assertEqual(self.ombre.items['long']['content'], '账' * 2000)   # not rewritten
        new = self.ombre.items['m3']                                         # the continuation was saved
        self.assertEqual(new['title'], '后来')
        self.assertIn('long', self.feature.sidecar.get('m3')['links'])

    def test_the_model_sees_how_often_a_memory_was_rewritten_and_its_other_stages(self):
        self.ombre.items['a'] = {'title': '围巾', 'content': '11月1日说起围巾。'}
        self.ombre.found = ['a']
        self.ombre.title_for = lambda key: self.ombre.items[key]['title']
        self.ombre.experience_stages = lambda key: ['b'] if key == 'a' else []
        self.ombre.items['b'] = {'title': '围巾后来', 'content': '后来织好了。'}
        self.store.append('fake/memory-versions.jsonl', {'memory_id': 'a', 'old_content': '更早的写法'})
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        offered = {item['id']: item for item in self.sent()['earlier_entries']}
        self.assertEqual(offered['a']['rewritten_times'], 1)
        self.assertEqual(offered['a']['stages'], [{'id': 'b', 'title': '围巾后来'}])
        self.assertIn('b', offered)                       # the other stage is offered too, not only the hit

    def test_a_memory_he_saved_himself_lately_is_offered_even_if_the_search_misses_it(self):
        # 11-15: Peter saved "tool drawers" himself at 09:16; the tidying found nothing and saved it again at 11:30.
        self.ombre.items['drawers'] = {'title': 'tool drawers', 'content': '11月15日上午我把工具放进了新的抽屉。'}
        self.feature.sidecar._add('drawers', 'source', via='hold', time=f'{DAY1}T08:00:00+08:00')
        self.ombre.items['old'] = {'title': 'x', 'content': '很久以前自己存的。'}
        self.feature.sidecar._add('old', 'source', via='hold', time='2026-09-01T08:00:00+08:00')
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply(updates=[{'id': 'drawers', 'text': '11月15日上午我把工具放进新抽屉，下午又被绊了一脚。'}])
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual([item['id'] for item in self.sent()['earlier_entries']], ['drawers'])
        self.assertEqual(len(self.ombre.items), 2)                       # nothing new saved beside it
        self.assertIn('下午又被绊了一脚', self.ombre.items['drawers']['content'])

    def test_a_memory_saved_before_the_sidecar_noted_saves_is_found_by_its_creation_time(self):
        self.ombre.items['early'] = {'title': 'drawers', 'content': '11月15日上午我把工具放进了新抽屉。'}
        self.ombre.recently_created = lambda since: ['early']
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        self.assertEqual([item['id'] for item in self.sent()['earlier_entries']], ['early'])

    def fixes(self, *pairs):
        self.replies.append(json.dumps({'fixes': [{'ref': ref, 'text': text} for ref, text in pairs]}, ensure_ascii=False))

    def test_a_good_first_draft_is_saved_at_once_even_if_its_words_match_another_memory(self):
        # Ticket 368 (Lan 11-16): what the first answer decided stands; nothing asks again about a draft that is fine.
        self.ombre.items['kept'] = {'title': 'drawers', 'content': '我自己存的：新抽屉。'}
        self.ombre.found = ['kept']
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '被绊了', 'text': '我被新抽屉绊了一下。', 'continues': 'kept'}])
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.feature.sidecar.get('m2')['links'], ['kept'])

    def test_an_overlong_later_stage_is_fixed_in_its_text_only_and_stays_linked(self):
        # Real comparison 2026-11-16: the second answer used to start over and lose the link, or turn "new" into "update".
        self.ombre.items['old'] = {'title': 'drawers', 'content': '11月15日我把工具放进了新抽屉。'}
        self.ombre.found = ['old']
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '被绊了', 'text': '长' * 650, 'continues': 'old'}])
        self.fixes(('d0', '后来我被新抽屉绊了三次。'))
        self.assertEqual(self.feature.run_if_due(), 'written')
        draft = self.sent()['drafts'][0]
        self.assertEqual((draft['ref'], draft['text'], draft['title']), ('d0', '长' * 650, '被绊了'))
        self.assertIn('650 characters', draft['problem'])
        self.assertIn('about 500 characters', draft['problem'])   # ticket 386: aimed under the cap, which stays 600
        self.assertNotIn('earlier_entries', self.sent())                  # the fix call is not a second tidy
        self.assertEqual(self.ombre.items['m2'], {'title': '被绊了', 'content': '后来我被新抽屉绊了三次。'})
        self.assertEqual(self.feature.sidecar.get('m2')['links'], ['old'])

    def test_a_fix_cannot_change_what_the_first_answer_decided(self):
        self.ombre.items['old'] = {'title': 'drawers', 'content': '11月15日我把工具放进了新抽屉。'}
        self.ombre.found = ['old']
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '被绊了', 'text': '长' * 650, 'continues': 'old'}])
        self.replies.append(json.dumps({'fixes': [{'ref': 'd0', 'text': '短了。', 'continues': 'other', 'title': 'x',
                                                     'id': 'old'}], 'updates': [{'id': 'old', 'text': '改旧的'}]}))
        self.feature.run_if_due()
        self.assertEqual(self.ombre.items['old']['content'], '11月15日我把工具放进了新抽屉。')   # not rewritten
        self.assertEqual(self.ombre.items['m2'], {'title': '被绊了', 'content': '短了。'})
        self.assertEqual(self.feature.sidecar.get('m2')['links'], ['old'])

    def test_an_overlong_rewrite_keeps_its_target_and_is_fixed(self):
        self.ombre.items['kyc'] = {'title': 'CC', 'content': '11月13日 Lan 给我接了 CC。'}
        self.ombre.found = ['kyc']
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply(updates=[{'id': 'kyc', 'text': '账' * 610}])
        self.fixes(('d0', '11月13日 Lan 给我接了 CC，后来恢复了。'))
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(self.ombre.items['kyc']['content'], '11月13日 Lan 给我接了 CC，后来恢复了。')
        self.assertEqual(len(self.ombre.items), 1)

    def test_a_draft_that_copies_the_chat_is_fixed_once(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        quoted = '她说：第3句：我想把你的记忆系统整个换掉，说明说明说明说明说明说明说明'
        self.reply([{'title': '照抄', 'text': quoted}])
        self.fixes(('d0', '11月12日她又提了要换掉记忆系统，我还是不同意。'))
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertIn('word for word', self.sent()['drafts'][0]['problem'])
        self.assertEqual(self.ombre.items['m1']['content'], '11月12日她又提了要换掉记忆系统，我还是不同意。')

    def test_a_text_a_few_characters_over_after_the_first_fix_gets_one_more_try(self):
        # Real comparison 2026-11-16: fixes came back at 613, 615 and 603 characters and were dropped.
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '被绊了', 'text': '长' * 700}])
        self.fixes(('d0', '长' * 603))
        self.fixes(('d0', '三次被新抽屉绊了。'))
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(len(self.requests), 3)
        self.assertIn('603 characters', self.sent()['drafts'][0]['problem'])
        self.assertEqual(self.ombre.items['m1']['content'], '三次被新抽屉绊了。')

    def test_a_good_draft_is_saved_while_the_flawed_one_beside_it_is_fixed(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '好的', 'text': '11月12日聊了换记忆系统，我没同意。'}, {'title': '长的', 'text': '长' * 650}])
        self.fixes(('d0', '短一点的另一件事。'))
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(sorted(item['content'] for item in self.ombre.items.values()),
                         sorted(['11月12日聊了换记忆系统，我没同意。', '短一点的另一件事。']))

    def test_when_the_fix_comes_back_empty_or_fails_the_good_draft_stays_and_the_failure_is_reported(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '好的', 'text': '11月12日聊了换记忆系统，我没同意。'}, {'title': '长的', 'text': '长' * 650}])
        self.fixes(('d0', ''))
        self.replies.append('not json')
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual([item['content'] for item in self.ombre.items.values()], ['11月12日聊了换记忆系统，我没同意。'])
        last = self.store.read('fake/experience-memory.jsonl')[-1]
        self.assertEqual(last['dropped'], 1)
        self.assertEqual(last['unfixed'], [{'title': '长的', 'problem': last['unfixed'][0]['problem']}])
        self.assertIn('empty', last['unfixed'][0]['problem'])

    def test_still_flawed_after_the_tries_it_is_not_saved_and_is_reported(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '太长', 'text': '长' * 650}])
        self.fixes(('d0', '长' * 700))
        self.fixes(('d0', '长' * 620))
        self.assertEqual(self.feature.run_if_due(), 'skipped')
        self.assertEqual(self.ombre.items, {})
        last = self.store.read('fake/experience-memory.jsonl')[-1]
        self.assertEqual((last['dropped'], last['unfixed'][0]['title']), (1, '太长'))
        self.assertIn('620 characters', last['unfixed'][0]['problem'])

    def test_a_memory_saved_before_the_sidecar_noted_saves_is_found_by_its_creation_time(self):
        self.ombre.items['early'] = {'title': 'drawers', 'content': '11月15日上午我把工具放进了新抽屉。'}
        self.ombre.recently_created = lambda since: ['early']
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply()
        self.feature.run_if_due()
        self.assertEqual([item['id'] for item in self.sent()['earlier_entries']], ['early'])

    def test_an_unknown_continues_id_is_ignored_and_the_entry_is_still_saved(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '新事', 'text': '11月12日聊了新的事。', 'continues': 'invented'}])
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(self.feature.sidecar.get('m1')['links'], [])

    def test_a_failed_library_search_still_lets_the_entry_be_written(self):
        def broken(name, args, *, write_operation=None, original=self.ombre.call):
            if name == 'breath_search':
                raise RuntimeError('search down')
            return original(name, args, write_operation=write_operation)
        self.ombre.call = broken
        self.first_entry()
        self.assertEqual(len(self.ombre.items), 1)

    def test_quotes_secrets_and_unknown_targets_are_dropped(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        quoted = '她说：第3句：我想把你的记忆系统整个换掉，说明说明说明说明说明说明说明'
        self.reply([{'title': '照抄', 'text': quoted},
                    {'title': '密码', 'text': '她把 password: hunter2secret 告诉我了。'},
                    {'title': '太长', 'text': '长' * 601}],
                   updates=[{'id': 'not-mine', 'text': '改别人的记忆'}])
        self.assertEqual(self.feature.run_if_due(), 'skipped')
        self.assertEqual(self.ombre.calls, [])
        self.assertEqual(self.store.read('fake/experience-memory.jsonl')[-1]['dropped'], 4)

    def test_a_broken_reply_changes_nothing_and_is_retried_later(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.replies.append('I would rather not.')
        with self.assertRaises(Exception):
            self.feature.run_if_due()
        self.assertEqual(self.ombre.items, {})
        self.assertEqual(self.feature.due(), 'volume')
        self.assertEqual(self.feature.status()['last']['kind'], 'error')

    # --- ticket 386: a thinking model that used the whole output limit, and what is kept when a run fails ----
    def failure_rows(self):
        return self.store.read('fake/experience-memory-failures.jsonl')

    def test_a_reply_cut_off_while_thinking_is_asked_again_without_thinking_and_saved(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.replies.append(('', 'length'))
        self.reply([{'title': '换记忆系统', 'text': '上午蓝想把我的记忆系统整个换掉，我没同意。'}])
        self.assertEqual(self.feature.run_if_due(), 'written')
        self.assertEqual(len(self.ombre.items), 1)
        self.assertEqual(self.kwargs[0]['max_tokens'], 8000)
        self.assertNotIn('thinking', self.kwargs[0])           # the first try thinks as the model is set
        self.assertEqual(self.kwargs[1]['thinking'], 'disabled')
        self.assertTrue(self.kwargs[0]['detail'])
        last = self.store.read('fake/experience-memory.jsonl')[-1]
        self.assertEqual((last['kind'], last['retried_without_thinking']), ('checkpoint', 1))
        rows = self.failure_rows()
        self.assertEqual([(r['stage'], r['what'], r['finish_reason'], r['completion_tokens']) for r in rows],
                         [('first', 'cut_off', 'length', 3000)])
        self.assertEqual(self.feature.status()['last']['retried_without_thinking'], 1)

    def test_a_reply_cut_off_twice_says_so_keeps_both_and_waits_longer_each_time(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.replies += [('{"entries":[{"title":"半', 'length'), ('', 'length')]
        with self.assertRaises(Exception):
            self.feature.run_if_due()
        self.assertEqual(self.ombre.items, {})
        status = self.feature.status()
        self.assertIn('思考用完了字数', status['last']['detail'])
        self.assertEqual(status['retry_at'], f'{DAY1}T12:30+08:00')
        rows = self.failure_rows()
        self.assertEqual([r.get('thinking') for r in rows], [None, 'disabled'])
        self.assertEqual(rows[0]['output'], '{"entries":[{"title":"半')
        # Not asked again every five minutes: 30 minutes, then 2 hours, then the next day. Never skipped.
        self.clock[0] = f'{DAY1}T12:20:00+08:00'
        self.assertEqual(self.feature.run_if_due(), 'waiting')
        self.assertEqual(len(self.requests), 2)
        self.clock[0] = f'{DAY1}T12:31:00+08:00'
        self.replies.append('no json here')
        with self.assertRaises(Exception):
            self.feature.run_if_due()
        self.assertEqual(self.feature.status()['retry_at'], f'{DAY1}T14:31+08:00')
        self.assertEqual(self.failure_rows()[-1]['what'], 'unreadable')   # not cut off: no second try
        self.assertEqual(len(self.requests), 3)
        self.clock[0] = f'{DAY1}T14:32:00+08:00'
        self.replies.append('still nothing')
        with self.assertRaises(Exception):
            self.feature.run_if_due()
        self.assertEqual(self.feature.status()['retry_at'], '2026-11-13T00:00+08:00')
        self.clock[0] = f'{DAY1}T23:59:00+08:00'
        self.assertEqual(self.feature.run_if_due(), 'waiting')
        self.clock[0] = '2026-11-13T00:01:00+08:00'
        self.reply()
        self.assertEqual(self.feature.run_if_due(), 'skipped')
        self.assertIsNone(self.feature.status()['retry_at'])   # a finished run starts the count again

    def test_a_retry_cut_off_inside_its_outer_object_is_not_taken_for_an_empty_answer(self):
        # Review of 386: '{"entries":[{"title":..,"text":..}' parses to the complete inner object only.
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.replies += [('', 'length'), ('{"entries":[{"title":"新事","text":"一段有效记忆。"}', 'length')]
        with self.assertRaises(Exception):
            self.feature.run_if_due()
        self.assertEqual(self.feature.due(), 'volume')                   # the chat is still waiting, not marked done
        self.assertEqual(self.feature.status()['last']['kind'], 'error')

    def test_after_a_cut_off_the_fixes_skip_thinking_and_an_unfixable_draft_keeps_its_text(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.replies.append(('', 'length'))
        self.reply([{'title': '太长', 'text': '长' * 650}])
        self.replies.append(json.dumps({'fixes': [{'ref': 'd0', 'text': '长' * 640}]}))
        self.replies.append(json.dumps({'fixes': [{'ref': 'd0', 'text': '长' * 630}]}))
        self.assertEqual(self.feature.run_if_due(), 'skipped')
        self.assertEqual(self.kwargs[2]['thinking'], 'disabled')
        self.assertEqual(self.kwargs[3]['thinking'], 'disabled')   # once cut off, the rest of the run skips thinking
        unfixed = [r for r in self.failure_rows() if r['what'] == 'unfixed_draft']
        self.assertEqual((unfixed[0]['title'], unfixed[0]['draft']), ('太长', '长' * 630))
        self.assertIn('630 characters', unfixed[0]['problem'])
        self.assertEqual(self.feature.status()['last']['unfixed'], 1)

    def test_only_the_newest_failures_are_kept(self):
        for number in range(25):
            self.feature._record_failure('first', 'unreadable', output=f'reply {number} ' + 'x' * 5000)
        rows = self.failure_rows()
        self.assertEqual(len(rows), 20)
        self.assertTrue(rows[0]['output'].startswith('reply 5 '))
        self.assertEqual(len(rows[-1]['output']), 4000)

    def test_daily_ceiling_stops_the_request(self):
        self.feature = self.make(daily_budget_cny='0.01')
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.assertEqual(self.feature.run_if_due(), 'budget')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.feature.due(), 'volume')   # the chat is still there for tomorrow

    def test_cost_is_booked_under_its_own_category(self):
        self.first_entry()
        spent, _ = self.budget.category_totals('fake', None, COST_CATEGORY)
        self.assertGreater(spent, Decimal(0))
        self.assertIn('经历整理', self.budget.display())


class ControlTests(Fixture):
    def write_one(self):
        self.add(long_talk(DAY1))
        self.clock[0] = f'{DAY1}T12:00:00+08:00'
        self.reply([{'title': '她想换记忆系统', 'text': '11月12日 Lan 想换掉我的记忆，我没同意。'}])
        self.feature.run_if_due()

    def test_switch_off_stops_writing_and_injection(self):
        self.write_one()
        self.assertIn('我没同意', self.feature.context()['content'])
        self.feature.set_enabled(False)
        self.assertIsNone(self.feature.context())
        self.add(long_talk('2026-11-13', 14))
        self.clock[0] = '2026-11-13T16:00:00+08:00'
        self.assertEqual(self.feature.run_if_due(), 'off')
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.make().enabled())   # the switch survives a restart

    def test_default_is_off_without_the_setting(self):
        self.assertFalse(load_settings(None)['enabled'])
        with self.assertRaises(ValueError):
            load_settings({'enabld': True})

    def test_deleted_or_hidden_entry_leaves_the_context_and_cannot_be_updated(self):
        self.write_one()
        del self.ombre.items['m1']   # what the memory page's delete does
        self.clock[0] = f'{DAY1}T12:01:00+08:00'
        self.feature._context_cache = None
        self.assertIsNone(self.feature.context())
        self.add(long_talk('2026-11-13', 14))
        self.clock[0] = '2026-11-13T16:00:00+08:00'
        self.reply(updates=[{'id': 'm1', 'text': '想把删掉的写回来。'}])
        self.assertEqual(self.feature.run_if_due(), 'skipped')
        self.assertEqual(self.sent()['earlier_entries'], [])
        self.assertEqual(self.ombre.items, {})

    def test_old_entries_leave_the_recent_block(self):
        self.write_one()
        self.clock[0] = '2026-11-16T12:00:00+08:00'
        self.assertIsNone(self.feature.context())
        self.assertIn('m1', self.ombre.items)   # still an ordinary memory, found by recall as before


class ChatPlacementTests(Fixture):
    def test_notes_sit_after_the_prefix_and_before_the_history(self):
        self.cfg.memory = {'experience': {'enabled': True}}
        sent = []

        def chat_model(messages, **kwargs):
            sent.append([dict(m) for m in messages])
            return 'reply', 1000, 20, 800
        chat = Chat(self.cfg, self.store, self.budget, chat_model, self.ombre)
        self.assertIsNotNone(chat.experience)
        chat.send('one')
        self.ombre.items['m1'] = {'title': '她想换记忆系统', 'content': '11月12日 Lan 想换掉我的记忆，我没同意。'}
        self.store.append('fake/experience-memory.jsonl', {
            'kind': 'entry', 'memory_id': 'm1', 'title': '她想换记忆系统', 'time': self.clock[0]})
        self.clock[0] = f'{DAY1}T09:05:00+08:00'
        chat.send('two')
        chat.send('three')
        first, second, third = sent
        self.assertEqual(first[0]['content'], 'fictional persona')
        position = next(i for i, m in enumerate(second) if '[What you remember' in m['content'])
        self.assertIn('我没同意', second[position]['content'])
        self.assertEqual(second[position + 1], {'role': 'user', 'content': 'one'})   # history follows
        self.assertEqual(second[:position], first[:position])                         # prefix untouched
        # Nothing new was written between the 2nd and 3rd request: the cacheable start is byte-identical.
        self.assertEqual(third[:position + 3], second[:position + 3])

    def test_without_the_setting_nothing_is_created(self):
        chat = Chat(self.cfg, self.store, self.budget, self.model, self.ombre)
        self.assertIsNone(chat.experience)


class RealMemoryTests(unittest.TestCase):
    """The append path against the real memory_agent.Memory (fake Ombre child process)."""
    setUp = test_native_memory.OmbreToolGuardTests.setUp

    def test_update_rewrites_through_trace_and_archives_the_old_version(self):
        clock = ['2026-11-20T16:00:00+08:00']
        budget = Budget(self.store, clock=lambda: clock[0])
        reply = json.dumps({'entries': [], 'updates': [{'id': 'mem001', 'text': '后来觉得还不错。'}]},
                           ensure_ascii=False)
        feature = ExperienceMemory(self.cfg, self.store, budget, lambda messages, **kw: (reply, 2000, 100, 0),
                                   'fake/chat.jsonl', self.memory, load_settings({'enabled': True}))
        self.store.append('fake/experience-memory.jsonl', {
            'kind': 'entry', 'memory_id': 'mem001', 'title': 'Old title', 'time': '2026-11-12T12:00:00+08:00'})
        self.store.append('fake/experience-memory.jsonl', {
            'kind': 'checkpoint', 'through_position': 0, 'through_message_id': None})
        for item in long_talk('2026-11-20', 14):
            self.store.append('fake/chat.jsonl', item)
        self.assertEqual(feature.run_if_due(), 'written')
        stored = self.path.read_text(encoding='utf-8')
        self.assertIn('---\n后来觉得还不错。', stored)
        self.assertNotIn('old memory body', stored)
        self.assertIn('old memory body', self.store.read('fake/memory-versions.jsonl')[0]['old_content'])
        self.assertEqual([name for name, _ in self.backend.calls if name != 'breath_search'], ['trace'])
        self.assertIn('后来觉得还不错', feature.context()['content'])


class BrowseByDateTests(unittest.TestCase):
    def test_a_date_without_keywords_gives_that_days_overview(self):
        rows = long_talk(DAY1) + long_talk('2026-11-13')
        text, found = search_chat(rows, None, since=DAY1, until=DAY1)
        self.assertTrue(found)
        self.assertIn('40 messages', text)
        self.assertNotIn('2026-11-13 ', text.split('\n', 1)[1])
        text, found = search_chat(rows, [], since='2026-11-01', until='2026-11-02')
        self.assertFalse(found)
        self.assertFalse(search_chat(rows, [])[1])


if __name__ == '__main__':
    unittest.main()
