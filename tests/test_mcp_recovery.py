"""Connection lifecycle and uncertain writes using fake transport, no real vault."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest

from memory_agent import MCP, MemoryToolError


class FakeMCP(MCP):
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.sessions = 0
        self.active = 0
        self.peak = 0
        self.calls = []
        self.exit_error = None
        super().__init__('http://example.invalid/mcp', 'fixture-token')

    @asynccontextmanager
    async def _session(self):
        self.sessions += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield SimpleNamespace(list_tools=self._specs, call_tool=self._invoke)
        finally:
            self.active -= 1
            if self.exit_error is not None:
                error, self.exit_error = self.exit_error, None
                raise error

    async def _specs(self):
        return SimpleNamespace(tools=[])

    async def _invoke(self, name, args):
        self.calls.append(name)
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text=outcome)])
        return outcome


class MCPRecoveryTests(unittest.TestCase):
    def backend(self, outcomes):
        client = FakeMCP(outcomes)
        self.addCleanup(client.close)
        return client

    def test_safe_read_reconnects_once_and_leaves_no_idle_connection(self):
        client = self.backend([ConnectionError('fixture secret must not be logged'), 'catalog', 'again'])
        diagnostics = []
        client.diagnostic = diagnostics.append
        self.assertEqual(client.active, 0)
        self.assertEqual(client.call('pulse', {}), 'catalog')
        self.assertEqual(client.active, 0)
        self.assertEqual(client.call('pulse', {}), 'again')
        self.assertEqual(client.sessions, 4)  # discovery + two attempts + next call
        self.assertEqual(client.calls, ['pulse'] * 3)
        self.assertIn('ConnectionError', diagnostics[0]['error_types'])
        self.assertNotIn('fixture secret', json.dumps(diagnostics))

    def test_bounded_read_failure_does_not_poison_next_call(self):
        client = self.backend([ConnectionError(), ConnectionError(), 'recovered'])
        with self.assertRaises(MemoryToolError) as failed:
            client.call('pulse', {})
        self.assertEqual(failed.exception.code, 'connection')
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.call('pulse', {}), 'recovered')

    def test_uncertain_mutation_is_never_replayed(self):
        for name in ('hold', 'trace', 'dream', 'plan', 'letter_write', 'breath_search'):
            with self.subTest(name=name):
                client = self.backend([ConnectionError('lost reply')])
                with self.assertRaises(MemoryToolError) as failed:
                    client.call(name, {})
                self.assertEqual(failed.exception.code, 'unknown')
                self.assertEqual(client.calls, [name])

    def test_service_refusal_is_not_a_transport_retry(self):
        client = self.backend([SimpleNamespace(isError=True, content=[SimpleNamespace(text='private')])])
        with self.assertRaises(MemoryToolError) as failed:
            client.call('pulse', {})
        self.assertEqual(failed.exception.code, 'tool')
        self.assertNotIn('private', str(failed.exception))
        self.assertEqual(client.calls, ['pulse'])

    def test_confirmed_write_survives_transport_cleanup_failure_without_replay(self):
        client = self.backend(['saved'])
        client.exit_error = ConnectionError('fixture cleanup failure')
        self.assertEqual(client.call('trace', {}), 'saved')
        self.assertEqual(client.calls, ['trace'])

    def test_service_refusal_survives_cleanup_failure_without_retry(self):
        client = self.backend([SimpleNamespace(isError=True, content=[])])
        client.exit_error = ConnectionError('fixture cleanup failure')
        with self.assertRaises(MemoryToolError) as failed:
            client.call('pulse', {})
        self.assertEqual(failed.exception.code, 'tool')
        self.assertEqual(client.calls, ['pulse'])

    def test_concurrent_callers_share_one_owner_and_shutdown_rejects_new_calls(self):
        client = self.backend(['one', 'two'])
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: client.call('pulse', {}), range(2)))
        self.assertEqual(set(results), {'one', 'two'})
        self.assertEqual(client.peak, 1)
        client.close()
        self.assertFalse(client.thread.is_alive())
        with self.assertRaises(MemoryToolError):
            client.call('pulse', {})


if __name__ == '__main__':
    unittest.main()
