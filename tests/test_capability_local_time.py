import os
import time
import unittest

from capability_state import local_stamp


class CapabilityLocalTimeTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get('TZ')
        if not hasattr(time, 'tzset'):
            self.skipTest('time zone switching needs tzset')
        os.environ['TZ'] = 'Asia/Shanghai'
        time.tzset()

    def tearDown(self):
        if self.old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self.old
        time.tzset()

    def test_utc_entry_reads_as_local_time(self):
        # 22:20 UTC on the 25th is 06:20 on the 26th in Lan's time zone: the entry is new, not last night's.
        self.assertEqual(local_stamp('2026-10-25T22:20:59.123+00:00'), '2026-10-26 06:20 local time')

    def test_unparseable_or_naive_values_pass_through(self):
        self.assertEqual(local_stamp('2026-10-21'), '2026-10-21')
        self.assertEqual(local_stamp('soon'), 'soon')
