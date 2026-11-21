import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import tool_failures
from tool_failures import UNKNOWN, finalize, internal_failure, refusal
from veil_agent import Chat, Store
from windows_observer import WindowsObservationTools


class FinalizeTests(unittest.TestCase):
    def test_success_and_a_plain_failure_keep_their_own_text(self):
        self.assertEqual(finalize('saved', '成功'), ('saved', None))
        self.assertEqual(finalize('old_text appears 0 times', '被拒绝'),
                         ('old_text appears 0 times', None))

    def test_a_known_fix_and_the_received_arguments_are_added_after_the_reason(self):
        result = refusal('This tool takes no arguments.',
                         fix='Call it with {}.', received={'window_id': 'w-1'},
                         detail='参数无效。')
        text, ledger = finalize(result, '被拒绝', result.detail)
        self.assertTrue(text.startswith('This tool takes no arguments.'))
        self.assertIn('[program] arguments received: {"window_id": "w-1"}', text)
        self.assertIn('[program] correction: Call it with {}.', text)
        self.assertIn('收到参数：{"window_id": "w-1"}', ledger)
        self.assertIn('怎么改：Call it with {}.', ledger)

    def test_received_arguments_alone_do_not_invent_a_correction(self):
        text, _ledger = finalize(refusal('Could not use this call.', received={'a': 1}),
                                 '被拒绝', None)
        self.assertIn('arguments received: {"a": 1}', text)
        self.assertNotIn('correction:', text)

    def test_an_unknown_fix_says_so_instead_of_guessing(self):
        text, ledger = finalize(refusal('The window could not be read.', fix=UNKNOWN), '出错', None)
        self.assertIn('does not know a corrective step', text)
        self.assertIn('不知道该怎么改', ledger)


class InternalFailureTests(unittest.TestCase):
    def test_the_exception_text_never_leaves_the_program(self):
        exc = ValueError(r'C:\Users\lan\Documents\secret.txt is locked')
        text, ledger, error_id = internal_failure('read_text_file', exc)
        self.assertTrue(error_id.startswith('err-'))
        self.assertIn(error_id, text)
        self.assertIn('ValueError', text)
        self.assertIn('failed inside the program', text)
        self.assertNotIn('secret.txt', text)
        self.assertNotIn('C:\\Users', text)
        self.assertNotIn('secret.txt', ledger)
        self.assertIn(error_id, ledger)

    def test_the_local_error_file_keeps_the_traceback_for_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'data')
            fake = SimpleNamespace(store=store, cfg=SimpleNamespace(id='test'))
            exc = ValueError(r'C:\Users\lan\Documents\secret.txt is locked')
            try:
                raise exc
            except ValueError:
                Chat._record_internal_failure(fake, 'read_text_file', 'err-12345678', exc, '{"path": "x"}')
            rows = store.read('test/tool-errors.jsonl')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['error_id'], 'err-12345678')
        self.assertEqual(rows[0]['tool'], 'read_text_file')
        self.assertIn('ValueError', rows[0]['traceback'])
        self.assertIn('secret.txt', rows[0]['traceback'])


class NoArgumentToolTests(unittest.TestCase):
    """The exact case lan saw: peek_screen / snooping_around rejected for taking no arguments."""

    def tools(self):
        return WindowsObservationTools(True, platform_enabled=True, backend=object())

    def test_listing_windows_with_arguments_says_what_it_received_and_how_to_fix(self):
        result, state, summary = self.tools().invoke('windows_list', {'window_id': 'w-1'})
        self.assertEqual(state, '被拒绝')
        self.assertEqual(summary, 'open application windows')
        text, ledger = finalize(result, state, result.activity_detail)
        self.assertIn('This tool takes no arguments.', text)
        self.assertIn('[program] arguments received: {"window_id": "w-1"}', text)
        self.assertIn('[program] correction:', text)
        self.assertIn('windows_observe', text)
        self.assertIn('收到参数：{"window_id": "w-1"}', ledger)
        self.assertIn('怎么改：', ledger)

    def test_foreground_window_with_arguments_gets_the_same_treatment(self):
        result, state, _summary = self.tools().invoke('windows_foreground', {'app': 'Chrome'})
        self.assertEqual(state, '被拒绝')
        text, _ledger = finalize(result, state, result.activity_detail)
        self.assertIn('arguments received: {"app": "Chrome"}', text)
        self.assertIn('correction:', text)

    def test_a_non_object_argument_is_reported_as_such(self):
        result, state, _summary = self.tools().invoke('windows_observe', 'not a dict')
        self.assertEqual(state, '被拒绝')
        text, _ledger = finalize(result, state, result.activity_detail)
        self.assertIn('A JSON object of arguments is required.', text)
        self.assertIn('correction:', text)


if __name__ == '__main__':
    unittest.main()
