"""Opt-in real pinned Ombre runtime recovery with an isolated synthetic vault."""
import os
import json
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from types import SimpleNamespace

from memory_agent import Memory, MemoryToolError, open_ombre
from memory_native import NativeMemoryTools
from ombre_resident import PINNED_OMBRE, _lock_held, _pid_alive
from veil_agent import Budget, Store


@unittest.skipUnless(os.environ.get('OMBRE_RUNTIME_GATE') == '1',
                     'optional real pinned Ombre recovery gate')
class OmbreRuntimeRecoveryLive(unittest.TestCase):
    def test_killed_child_recovers_mcp_and_uncertain_hold_is_not_replayed(self):
        root = Path(__file__).resolve().parents[1]
        installed = (root / '.deps' / 'ombre' / 'installed').read_text().strip()
        self.assertEqual(installed.split(':', 1)[0], PINNED_OMBRE)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        cfg = SimpleNamespace(
            id='fixture', name='Fictional Recovery Test', provider={
                'model': 'offline-fixture', 'base_url': 'https://offline.invalid/v1',
                'api_key_env': 'FIXTURE_UNUSED_KEY'},
            memory={'enabled': True, 'ombre_port': port, 'ombre_model': False,
                    'embedding_enabled': False, 'model': 'offline-fixture',
                    'dimensions': 3, 'base_url': 'https://offline.invalid/v1',
                    'input_cny_per_million': '0'})
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / 'data'
            with open_ombre(root, cfg, data_root=data_root, api_key_overrides={
                    'OMBRE_EMBED_API_KEY': '', 'OMBRE_COMPRESS_API_KEY': ''}) as backend:
                self.assertIn('=== 我现在的记忆 ===', backend.call('pulse', {}))
                backend.call('hold', {'content': 'Fictional lighthouse checklist for Thursday.',
                    'title': 'Synthetic existing memory'})
                private = data_root / 'fixture' / 'memory' / '_agent'
                state_path = private / 'ombre.process.json'
                state = json.loads(state_path.read_text(encoding='utf-8'))
                original_host = state['host_pid']
                child_pid = state['server_pid']
                self.kill_test_child(child_pid)
                diagnostics = []
                backend.diagnostic = diagnostics.append
                with ThreadPoolExecutor(2) as pool:
                    try:
                        results = list(pool.map(lambda _: backend.call('pulse', {}), range(2)))
                    except MemoryToolError as exc:
                        self.fail(f'{exc.code}: {json.dumps(diagnostics, ensure_ascii=False)}')
                current = json.loads(state_path.read_text(encoding='utf-8'))
                self.assertNotEqual(current['host_pid'], original_host)
                self.assertEqual(backend.recovery_count, 1)
                self.assertTrue(_pid_alive(current['host_pid']))
                self.assertTrue(_pid_alive(current['server_pid']))
                self.assertTrue(_lock_held(private / 'ombre.lock'))
                self.assertTrue(all('=== 我现在的记忆 ===' in result for result in results))
                search = backend.call('breath_search', {'query': 'fictional lighthouse checklist'})
                self.assertIn('Fictional lighthouse checklist for Thursday.', search)

                actual_perform = backend._perform
                calls = []

                async def lose_hold_reply(name, args):
                    result = await actual_perform(name, args)
                    if name == 'hold' and args.get('title') == 'Synthetic uncertain write':
                        calls.append(name)
                        state = json.loads(state_path.read_text(encoding='utf-8'))
                        self.kill_test_child(state['server_pid'])
                        raise ConnectionError('synthetic response loss after server acceptance')
                    return result

                backend._perform = lose_hold_reply
                store = Store(data_root)
                memory = Memory(cfg, store, Budget(store), object(), backend)
                self.addCleanup(memory.__exit__, None, None, None)
                native = NativeMemoryTools(memory)
                result, status, _ = native.invoke('hold', {
                    'content': 'Synthetic runtime recovery memory.',
                    'title': 'Synthetic uncertain write'})
                self.assertEqual(status, '出错')
                self.assertIn('unconfirmed', result)
                self.assertEqual(calls, ['hold'])
                backend._perform = actual_perform
                self.assertEqual(len(memory.pending_writes), 1)
                journal = store.read('fixture/memory/_agent/write-reconciliation.jsonl')
                self.assertEqual([row['status'] for row in journal[-2:]], ['submitted', 'unconfirmed'])
                self.assertNotIn('Synthetic runtime recovery memory.', json.dumps(journal))

                memory.__exit__(None, None, None)
                reopened = Memory(cfg, store, Budget(store), object(), backend)
                self.addCleanup(reopened.__exit__, None, None, None)
                result, status, _ = NativeMemoryTools(reopened).invoke('hold', {
                    'content': 'Synthetic runtime recovery memory.',
                    'title': 'Synthetic uncertain write'})
                self.assertEqual(status, '出错')
                self.assertIn('not sent again', result)
                pulse = backend.call('pulse', {})
                self.assertEqual(pulse.count('Synthetic uncertain write'), 1)

    @staticmethod
    def kill_test_child(pid):
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                           check=True, capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
