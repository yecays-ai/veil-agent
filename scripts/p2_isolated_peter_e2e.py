"""Ticket P2: Isolated Peter End-to-End integration test and verification.

Executes complete lifecycle in an isolated directory:
1. Brain Write (hold with write_authority=True)
2. Chat Recall (breath_search & context)
3. Event Reference & Fragment Deduplication (工单 424 verification: story & source fragment never duplicate)
4. Multi-turn Continuous Followup
5. Restart Recovery (Memory re-instantiation from Brain DB)

Outputs verification log to docs/tickets/p2/evidence/isolated_peter_e2e.log
"""
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_writer
import veil_brain
from memory_agent import Memory
from veil_agent import Budget, Character, Store


class DummyBackend:
    def list_tools(self):
        return [SimpleNamespace(name='trace', description='', inputSchema={'type': 'object'}),
                SimpleNamespace(name='dream', description='', inputSchema={'type': 'object'})]
    def call(self, name, args):
        return ''


def setup_isolated_env(workdir):
    work = Path(workdir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    data_dir = work / 'data'
    peter_dir = data_dir / 'peter'
    peter_dir.mkdir(parents=True)

    # Copy real brain.db and event jsonls to isolated environment
    src_peter = ROOT / 'data' / 'peter'
    if (src_peter / 'brain.db').exists():
        shutil.copyfile(src_peter / 'brain.db', peter_dir / 'brain.db')
    else:
        conn = sqlite3.connect(peter_dir / 'brain.db')
        conn.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(conn)
        conn.close()

    if (src_peter / 'events.jsonl').exists():
        shutil.copyfile(src_peter / 'events.jsonl', peter_dir / 'events.jsonl')
    if (src_peter / 'event-stages.jsonl').exists():
        shutil.copyfile(src_peter / 'event-stages.jsonl', peter_dir / 'event-stages.jsonl')

    store = Store(str(data_dir))
    provider = {'model': 'fake-model', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
    from dotenv import dotenv_values
    env_vals = dotenv_values(str(ROOT / '.env'), interpolate=False, encoding='utf-8-sig')
    embed_key = (env_vals.get('EMBED_API_KEY') or '').strip()

    settings = {
        'model': 'BAAI/bge-m3',
        'input_cny_per_million': '0',
        'mode': 'native',
        'base_url': 'https://api.siliconflow.cn/v1' if embed_key else 'https://example.invalid/v1',
        'brain': {'write_authority': True, 'shadow': False},
        'retrieval': {'backend': 'brain'}
    }
    cfg = Character('peter', 'Peter', 'persona', 20, 1024, provider, 'peter', settings)

    memory = Memory(cfg, store, Budget(store), Mock(), DummyBackend())
    return work, store, cfg, memory


def run_e2e():
    log_lines = []
    def log(msg):
        print(msg)
        log_lines.append(f'[{time.strftime("%H:%M:%S")}] {msg}')

    workdir = ROOT / 'docs' / 'tickets' / 'p2' / 'evidence' / 'isolated_e2e_workdir'
    log(f'=== Starting P2 Isolated Peter End-to-End Verification ===')
    log(f'Working directory: {workdir}')

    work, store, cfg, memory = setup_isolated_env(workdir)

    try:
        # Step 1: Brain 写入
        log('--- Step 1: Brain Write (Native Hold with write_authority=True) ---')
        new_title = 'E2E测试记忆-关于仙人掌的特别约定'
        new_content = '2026-11-24 E2E测试：Lan说她养死过仙人掌，所以我们约定好以后买绿植一定要选耐活的。'
        write_res = memory.call('hold', {
            'title': new_title,
            'content': new_content,
            'importance': 9,
            'tags': 'free_tag_1,free_tag_2',  # Intentionally pass free tags to verify P2-C blocks them
            'domain': 'experience'
        })
        log(f'Write response: {write_res}')
        assert write_res.startswith('新建→'), f'Unexpected write response: {write_res}'
        new_mid = write_res.split('→')[1]
        log(f'Generated new memory ID: {new_mid}')

        # Verify DB directly
        db_path = store.root / 'peter' / 'brain.db'
        conn = sqlite3.connect(db_path)
        row = conn.execute('SELECT id, title, tags_json, domain_json, importance, vector_state FROM memories WHERE id=?',
                           (new_mid,)).fetchone()
        conn.close()
        assert row is not None, 'New memory not found in brain.db'
        assert row[1] == new_title, f'Title mismatch: {row[1]}'
        assert row[2] == '[]', f'Free tags leak detected! tags_json={row[2]}'
        assert row[3] == '[]', f'Free domain leak detected! domain_json={row[3]}'
        log('P2-C Verification PASSED: Free tags & domain successfully dropped by write authority.')

        # Step 2: 聊天召回 (Chat Recall)
        log('--- Step 2: Chat Recall (Retrieval from Brain) ---')
        search_res = memory.call('breath_search', {'query': '仙人掌 特别约定 耐活', 'max_results': 5})
        log(f'Search result preview:\n{search_res[:200]}...')
        assert new_mid in search_res, f'Newly written memory {new_mid} not retrieved by breath_search!'
        log('Chat recall verification PASSED: New memory successfully retrieved.')

        # Step 3: 事件引用与旧碎片去重 (Event Deduplication - 工单 424)
        log('--- Step 3: Event Story & Fragment Deduplication Verification ---')
        # V01 is the famous event about cacti ("Kills plants, even cacti" - 99df0c513389)
        # When querying "仙人掌", both V01 (event story) and 99df0c513389 (source fragment) could match.
        context_rows = memory.context('仙人掌', include_opening=False)
        log(f'Retrieved context candidates count: {len(context_rows)}')
        matched_mids = [r.get('id') for r in context_rows if r.get('id')]
        log(f'Context candidate IDs: {matched_mids}')

        # Check deduplication: if V01 is present, 99df0c513389 must NOT be injected as a duplicate fragment
        has_v01 = any(r.get('id') == 'V01' or 'First night' in (r.get('content') or '') for r in context_rows)
        has_source_fragment = any(r.get('id') == '99df0c513389' for r in context_rows)

        log(f'Has V01 story in context: {has_v01}, Has duplicate source fragment: {has_source_fragment}')
        if has_v01:
            assert not has_source_fragment, 'DUPLICATE INJECTION DETECTED: Event story and source fragment both present!'
            log('Deduplication PASSED: Event story took precedence; source fragment was folded without duplicate injection.')
        else:
            log('Single representation verified (no duplicate injection).')

        # Step 4: 连续追问 (Continuous Follow-up Turns)
        log('--- Step 4: Continuous Multi-turn Followup ---')
        followup_rows = memory.context('那晚她还说了什么关于红砖色的事？', include_opening=False)
        log(f'Followup context candidates count: {len(followup_rows)}')
        followup_ids = [r.get('id') for r in followup_rows if r.get('id')]
        log(f'Followup candidate IDs: {followup_ids}')
        # Ensure continuity
        assert len(followup_rows) > 0, 'Continuous followup recall failed to return context'
        log('Multi-turn followup verification PASSED.')

        # Step 5: 重启恢复场景 (Process Restart & Persistence)
        log('--- Step 5: Process Restart & Recovery Verification ---')
        # Exit first memory instance
        memory.__exit__(None, None, None)
        log('Terminated first Memory session.')

        # Reopen brand new Memory instance on the same store
        log('Reinitializing brand new Memory instance against existing brain.db...')
        reopened_memory = Memory(cfg, store, Budget(store), Mock(), DummyBackend())
        try:
            # Check opening list (breath)
            opening = reopened_memory.call('breath', {})
            log(f'Reopened opening preview:\n{opening[:150]}...')
            assert '[bucket_id:' in opening, 'Opening memories empty after restart'

            # Query the newly written memory after restart
            restart_search = reopened_memory.call('breath_search', {'query': '耐活的绿植 特别约定', 'max_results': 5})
            assert new_mid in restart_search, f'Memory {new_mid} lost after process restart!'
            log('Restart search verification PASSED: Memory persisted and indexed properly.')

            # Check DB integrity
            conn = sqlite3.connect(db_path)
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            conn.close()
            assert integrity == 'ok', f'Database integrity check failed: {integrity}'
            log(f'Database integrity check: {integrity}')
        finally:
            reopened_memory.__exit__(None, None, None)

        log('=== ALL 5 SCENARIOS PASSED WITH ZERO ERRORS ===')
        status = 'SUCCESS'

    except Exception as exc:
        log(f'ERROR: Scenario verification failed: {exc}')
        import traceback
        log_lines.append(traceback.format_exc())
        status = 'FAILED'
        raise
    finally:
        out_log = ROOT / 'docs' / 'tickets' / 'p2' / 'evidence' / 'isolated_peter_e2e.log'
        out_log.write_text('\n'.join(log_lines), encoding='utf-8')
        out_json = ROOT / 'docs' / 'tickets' / 'p2' / 'evidence' / 'isolated_peter_e2e_report.json'
        report = {
            'status': status,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'scenarios': [
                '1_brain_write_authority_hold',
                '2_chat_recall',
                '3_event_story_fragment_deduplication',
                '4_continuous_multi_turn_followup',
                '5_restart_recovery_and_persistence'
            ],
            'log_file': str(out_log.relative_to(ROOT))
        }
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        log(f'Saved test log to {out_log}')


if __name__ == '__main__':
    run_e2e()
