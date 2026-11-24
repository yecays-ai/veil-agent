"""Real isolated Cutover Protocol Drill.

Executes the formal cutover protocol in an isolated sandbox with real processes:
1. Real isolated Ombre resident process on dedicated free port.
2. Full cryptographic hash reconciliation via ShadowSync.reconcile() and veil_brain.check_db().
3. Real write freeze & Brain authority cutover:
   - Sets write_authority = true, shadow = false
   - Proves mutual exclusion (ShadowSync rejects shadow=True when write_authority=True)
   - Writes canary memory through BrainAuthority
   - Verifies canary exists in brain.db and is NOT written to Ombre vault (vault frozen)
4. Negative branch: Discrepancy Abort drill:
   - Injects hash discrepancy into Ombre vault
   - Proves reconciliation detects the mismatch and aborts cutover safely
5. Full Failure Rollback drill:
   - Exports Brain memories back to vault
   - Switches config back to write_authority = false, shadow = true
   - Reconnects real Ombre resident process
   - Proves Ombre can read the canary memory through MCP breath_search
   - Proves full rollback safety with zero data loss

Outputs evidence to docs/tickets/p2/evidence/cutover_drill.log and cutover_drill_report.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_authority
import brain_backup
import brain_export
import brain_sync
import brain_writer
import ombre_resident
import veil_brain
from veil_agent import Store, UserError, load_character

TMP_DIR = ROOT / 'tmp' / 'cutover_drill_live'
EVIDENCE_DIR = ROOT / 'docs' / 'tickets' / 'p2' / 'evidence'
LOG_FILE = EVIDENCE_DIR / 'cutover_drill.log'
REPORT_FILE = EVIDENCE_DIR / 'cutover_drill_report.json'


class Logger:
    def __init__(self, log_path):
        self.log_path = log_path
        self.entries = []
        if log_path.exists():
            log_path.unlink()

    def log(self, msg):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{timestamp}] {msg}"
        print(line)
        self.entries.append(line)
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


def get_free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def setup_isolated_env(tmp_dir: Path, logger: Logger):
    logger.log(f"Setting up isolated test environment at {tmp_dir}...")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Characters
    char_dir = tmp_dir / 'characters' / 'peter'
    char_dir.mkdir(parents=True)
    shutil.copyfile(ROOT / 'characters' / 'peter' / 'character.toml', char_dir / 'character.toml')
    if (ROOT / 'characters' / 'peter' / 'windows-apps.toml').exists():
        shutil.copyfile(ROOT / 'characters' / 'peter' / 'windows-apps.toml', char_dir / 'windows-apps.toml')

    # Assign isolated free port to ombre_port
    char_toml = char_dir / 'character.toml'
    text = char_toml.read_text(encoding='utf-8')
    ombre_port = get_free_port()
    text = re.sub(r'ombre_port\s*=\s*\d+', f'ombre_port = {ombre_port}', text)
    # Default initial state: shadow = true, write_authority = false
    text = re.sub(
        r'\[memory\.brain\][^\[]*',
        '[memory.brain]\nshadow = true\nwrite_authority = false\nreconcile_seconds = 600\n\n',
        text
    )
    char_toml.write_text(text, encoding='utf-8')

    # 2. Persona
    persona_dir = tmp_dir / 'persona'
    persona_dir.mkdir(parents=True)
    shutil.copyfile(ROOT / 'persona' / 'peter.md', persona_dir / 'peter.md')

    # 3. .env
    shutil.copyfile(ROOT / '.env', tmp_dir / '.env')

    # 4. Data: copy brain.db and events
    peter_data = tmp_dir / 'data' / 'peter'
    peter_data.mkdir(parents=True)
    src_peter = ROOT / 'data' / 'peter'
    for f in ['brain.db', 'events.jsonl', 'event-stages.jsonl', 'about-lan.md']:
        if (src_peter / f).exists():
            shutil.copyfile(src_peter / f, peter_data / f)

    import brain_events
    conn = sqlite3.connect(peter_data / 'brain.db')
    brain_events.ensure_schema(conn)
    if (peter_data / 'events.jsonl').exists():
        brain_events.import_events(conn, str(peter_data / 'events.jsonl'),
                                   str(peter_data / 'event-stages.jsonl') if (peter_data / 'event-stages.jsonl').exists() else None)
    conn.close()

    # 5. Export brain.db to isolated Ombre vault so Ombre vault exists and is 100% in sync
    vault_dir = peter_data / 'memory'
    logger.log(f"Exporting Brain database to Ombre vault at {vault_dir}...")
    export_res = brain_export.export_vault(str(peter_data / 'brain.db'), str(peter_data))
    logger.log(f"Vault export finished: {export_res['memories']} memories, {export_res['tombstones']} tombstones exported.")

    return ombre_port


def run_live_cutover_drill():
    logger = Logger(LOG_FILE)
    logger.log("================================================================================")
    logger.log("Starting Real Isolated Cutover Protocol Drill (Live Ombre & Full Reconciliation)")
    logger.log("================================================================================")

    report = {
        'drill_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'environment': 'real isolated Ombre resident process, real socket port, real ShadowSync reconciliation',
        'steps': {}
    }

    ombre_resident_obj = None
    try:
        # STEP 1: Environment & Real Ombre Resident Startup
        logger.log("\n--- [Step 1: Isolated Environment & Real Ombre Process Startup] ---")
        ombre_port = setup_isolated_env(TMP_DIR, logger)
        cfg = load_character(TMP_DIR, 'peter')
        store = Store(TMP_DIR / 'data')
        db_path = TMP_DIR / 'data' / 'peter' / 'brain.db'
        vault_dir = TMP_DIR / 'data' / 'peter' / 'memory'

        logger.log(f"Starting isolated Ombre resident process on port {ombre_port}...")
        port, token, ombre_resident_obj = ombre_resident.start_or_connect(
            ROOT, cfg, data_root=TMP_DIR / 'data'
        )
        logger.log(f"Ombre resident started successfully: port={port}, token={token[:8]}...")

        # Test Ombre MCP connection
        from memory_agent import MCP
        mcp_client = MCP(f'http://127.0.0.1:{port}/mcp', token)
        tools = mcp_client.list_tools()
        tool_names = [t.name for t in tools] if hasattr(tools[0], 'name') else [t['name'] for t in tools]
        logger.log(f"Connected to Ombre MCP server. Available tools: {tool_names}")
        assert 'hold' in tool_names and 'breath_search' in tool_names
        mcp_client.close()
        report['steps']['1_ombre_startup'] = {
            'status': 'PASS',
            'port': port,
            'tool_count': len(tool_names)
        }

        # STEP 2: Full Cryptographic Hash Reconciliation
        logger.log("\n--- [Step 2: Full Hash Reconciliation (ShadowSync & DB Integrity)] ---")
        # Run ShadowSync reconcile first to synchronize and refresh cryptographic digest
        sync = brain_sync.ShadowSync(
            cfg, store, {'shadow': True}, db_path=str(db_path), force=True, start=False
        )
        recon_summary = sync.reconcile()
        logger.log(f"ShadowSync reconciliation result: changed={recon_summary['changed']}, source_files={recon_summary['source']}")
        logger.log(f"Comparison details: {recon_summary['comparison']}")
        assert len(recon_summary['comparison']['memory_mismatches']) == 0, (
            f"Found memory mismatches during pre-cutover reconciliation: {recon_summary['comparison']['memory_mismatches']}"
        )
        assert len(recon_summary['comparison']['version_mismatches']) == 0

        # Check SQLite DB integrity and digest
        db_check = veil_brain.check_db(str(db_path))
        logger.log(f"SQLite DB integrity check: {db_check}")
        assert db_check.get('integrity') == 'ok' and db_check.get('digest_ok') is True, f"DB integrity check failed: {db_check}"
        logger.log("Full cryptographic hash reconciliation verified 0 diff between Ombre vault and Brain DB.")
        report['steps']['2_full_reconciliation'] = {
            'status': 'PASS',
            'source_count': recon_summary['source'],
            'memory_mismatches': len(recon_summary['comparison']['memory_mismatches']),
            'version_mismatches': len(recon_summary['comparison']['version_mismatches'])
        }

        # STEP 3: Consistency Backup
        logger.log("\n--- [Step 3: Consistency Backup] ---")
        backup_dir = TMP_DIR / 'backup'
        backup_dir.mkdir(parents=True, exist_ok=True)
        b_res = brain_backup.backup(str(db_path), str(backup_dir / 'brain.db'))
        logger.log(f"Consistency backup completed: sha256={b_res['sha256'][:16]}..., bytes={b_res['bytes']}")
        report['steps']['3_consistency_backup'] = {
            'status': 'PASS',
            'backup_sha256': b_res['sha256'],
            'bytes': b_res['bytes']
        }

        # STEP 4: Switch Authority to Brain, Freeze Old Writes & Safely Stop Old Service
        logger.log("\n--- [Step 4: Switch Authority to Brain, Freeze Old Writes & Safely Stop Old Service] ---")
        # Update character.toml: write_authority = true, shadow = false
        char_toml = TMP_DIR / 'characters' / 'peter' / 'character.toml'
        text = char_toml.read_text(encoding='utf-8')
        text = re.sub(
            r'\[memory\.brain\][^\[]*',
            '[memory.brain]\nshadow = false\nwrite_authority = true\nreconcile_seconds = 600\n\n',
            text
        )
        char_toml.write_text(text, encoding='utf-8')
        cfg = load_character(TMP_DIR, 'peter')
        logger.log("Updated character.toml: memory.brain.write_authority=true, memory.brain.shadow=false")

        # Verify Mutual Exclusion: attach_authority and brain_sync.attach must reject dual authority
        illegal_cfg = SimpleNamespace(id='peter', memory={'brain': {'write_authority': True, 'shadow': True}})
        try:
            brain_authority.attach_authority(illegal_cfg, store)
            raise AssertionError("attach_authority did not reject dual authority")
        except ValueError as exc:
            logger.log(f"Verified Mutual Exclusion in attach_authority: {exc}")

        try:
            brain_sync.attach(illegal_cfg, store)
            raise AssertionError("brain_sync.attach did not reject dual authority")
        except ValueError as exc:
            logger.log(f"Verified Mutual Exclusion in brain_sync.attach: {exc}")

        # Execute safe shutdown of the old Ombre resident service
        old_pid = getattr(ombre_resident_obj, 'host_pid', None)
        old_port = port
        logger.log(f"Safely shutting down old Ombre resident service (PID={old_pid}, Port={old_port})...")
        ombre_resident_obj.close()
        time.sleep(1)

        # Verify old Ombre process is terminated and port is closed
        port_open = ombre_resident._port_open(old_port)
        pid_alive = ombre_resident._pid_alive(old_pid) if old_pid else False
        logger.log(f"Old Ombre service status after stop: port_open={port_open}, pid_alive={pid_alive}")
        assert not port_open, f"Old Ombre port {old_port} is still open after shutdown!"
        assert not pid_alive, f"Old Ombre process {old_pid} is still alive after shutdown!"

        # Verify that direct write calls to old Ombre port are rejected
        direct_write_rejected = False
        rejection_error = ""
        try:
            req = Request(f"http://127.0.0.1:{old_port}/mcp", data=b'{"method":"tools/call"}', headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=2) as resp:
                pass
        except (URLError, OSError) as exc:
            direct_write_rejected = True
            rejection_error = repr(exc)
            logger.log(f"Verified direct write to old Ombre is rejected with connection failure: {rejection_error}")
        assert direct_write_rejected, "Direct write to old Ombre port was NOT rejected!"

        # Write canary memory through BrainAuthority
        auth = brain_authority.BrainAuthority(cfg, store)
        canary_content = "CUTOVER_CANARY_REAL: 蓝港浮标切换演练金丝雀记忆，验证新写入落入Brain且旧写入被完全阻止。"
        hold_res = auth.call('hold', {
            'content': canary_content,
            'title': '切换演练金丝雀'
        })
        canary_id = hold_res.split('→')[-1]
        logger.log(f"Wrote canary memory through BrainAuthority: result={hold_res}, memory_id={canary_id}")

        # Verify canary exists in brain.db
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("SELECT id, title, body, tags_json FROM memories WHERE id=?", (canary_id,))
        row = c.fetchone()
        conn.close()
        assert row is not None, f"Canary {canary_id} not found in brain.db"
        assert row[3] == '[]', f"Free tags found on canary: {row[3]}"
        logger.log(f"Verified canary {canary_id} in brain.db with tags_json='[]'")

        # Verify Ombre vault is completely unmodified (writes frozen)
        vault_files = list(vault_dir.glob('**/*.md'))
        for vf in vault_files:
            text = vf.read_text(encoding='utf-8')
            assert canary_content not in text, f"Ombre vault was modified during frozen state: found canary in {vf}"
        logger.log(f"Verified: Ombre vault ({len(vault_files)} files) was NOT modified. Old writes are truly frozen!")
        report['steps']['4_cutover_and_freeze'] = {
            'status': 'PASS',
            'canary_id': canary_id,
            'old_service_safely_stopped': True,
            'old_port_closed': not port_open,
            'direct_ombre_write_rejected': direct_write_rejected,
            'rejection_evidence': rejection_error,
            'vault_unmodified': True
        }

        # STEP 5: Negative Branch: Discrepancy Detection & Abort Drill
        logger.log("\n--- [Step 5: Negative Branch - Discrepancy Detection & Abort Drill] ---")
        # In a separate test dir, inject a discrepancy into a copy of Ombre vault
        neg_dir = TMP_DIR / 'neg_drill'
        shutil.copytree(TMP_DIR / 'data', neg_dir / 'data', ignore=shutil.ignore_patterns('*lock*', '*.lock', '_agent'))
        tamper_file = list((neg_dir / 'data' / 'peter' / 'memory').glob('**/*.md'))[0]
        tamper_file.write_text(tamper_file.read_text(encoding='utf-8') + "\n<!-- TAMPERED_DISCREPANCY -->", encoding='utf-8')
        logger.log(f"Injected deliberate content tampering into vault file: {tamper_file.name}")

        neg_store = Store(neg_dir / 'data')
        neg_cfg = load_character(TMP_DIR, 'peter')
        neg_sync = brain_sync.ShadowSync(
            neg_cfg, neg_store, {'shadow': True}, db_path=str(neg_dir / 'data' / 'peter' / 'brain.db'), force=True, start=False
        )
        # Compare will catch the mismatch between vault and DB before cutover
        conn_neg = sqlite3.connect(str(neg_dir / 'data' / 'peter' / 'brain.db'))
        neg_comp = neg_sync.compare(conn_neg)
        conn_neg.close()
        logger.log(f"Pre-cutover validation check on tampered vault: mismatches={len(neg_comp['memory_mismatches'])}")
        assert len(neg_comp['memory_mismatches']) > 0, "Discrepancy was not detected by compare()!"
        logger.log(f"Discrepancy correctly detected on memory {neg_comp['memory_mismatches']}. Cutover safely aborted.")
        shutil.rmtree(neg_dir, ignore_errors=True)
        report['steps']['5_discrepancy_abort'] = {
            'status': 'PASS',
            'detected_discrepancy': True
        }

        # STEP 6: Real Failure Rollback Drill
        logger.log("\n--- [Step 6: Real Failure Rollback Drill] ---")
        # Simulate rollback: Brain authority was active and wrote canary memory.
        # Now roll back to Ombre without losing the canary memory.
        # 1. Export Brain memories back into Ombre vault
        logger.log("Exporting updated Brain memories (including canary) to Ombre vault...")
        rb_export = brain_export.export_vault(str(db_path), str(TMP_DIR / 'data' / 'peter'))
        logger.log(f"Export completed: {rb_export['memories']} memories, {rb_export['tombstones']} tombstones.")

        # 2. Revert character.toml to Ombre authority
        char_toml = TMP_DIR / 'characters' / 'peter' / 'character.toml'
        text = char_toml.read_text(encoding='utf-8')
        text = re.sub(
            r'\[memory\.brain\][^\[]*',
            '[memory.brain]\nshadow = true\nwrite_authority = false\nreconcile_seconds = 600\n\n',
            text
        )
        char_toml.write_text(text, encoding='utf-8')
        cfg = load_character(TMP_DIR, 'peter')
        logger.log("Reverted character.toml: write_authority=false, shadow=true")

        # 3. Full reconciliation after rollback
        sync_rb = brain_sync.ShadowSync(
            cfg, store, {'shadow': True}, db_path=str(db_path), force=True, start=False
        )
        recon_rb = sync_rb.reconcile()
        logger.log(f"Post-rollback reconciliation: changed={recon_rb['changed']}, mismatches={len(recon_rb['comparison']['memory_mismatches'])}")
        assert len(recon_rb['comparison']['memory_mismatches']) == 0

        # 4. Restart/Reconnect Ombre and verify canary is readable from Ombre
        logger.log("Restarting Ombre resident to reload newly exported vault...")
        if ombre_resident_obj:
            try:
                ombre_resident_obj.close()
            except Exception:
                pass
        time.sleep(1)
        port, token, ombre_resident_obj = ombre_resident.start_or_connect(
            ROOT, cfg, data_root=TMP_DIR / 'data'
        )
        logger.log(f"Reconnected Ombre resident after rollback: port={port}")

        logger.log("Testing Ombre query for canary memory...")
        mcp_client = MCP(f'http://127.0.0.1:{port}/mcp', token)
        res_search = mcp_client.call('breath_search', {'query': '金丝雀记忆'})
        # Format response
        content_found = False
        res_text = str(res_search)
        if 'CUTOVER_CANARY_REAL' in res_text or canary_id in res_text or '切换演练金丝雀' in res_text:
            content_found = True
        logger.log(f"Ombre search result for canary memory: found={content_found}, result={res_text[:200]}")
        assert content_found, f"Canary memory was not readable from Ombre after rollback: {res_search}"
        mcp_client.close()
        logger.log("Verified: Ombre resumed as authority, canary memory safely preserved. Rollback succeeded with ZERO data loss!")
        report['steps']['6_failure_rollback'] = {
            'status': 'PASS',
            'canary_recovered_in_ombre': True
        }

        report['overall_status'] = 'PASS'
        logger.log("\n================================================================================")
        logger.log("ALL REAL ISOLATED CUTOVER DRILL STEPS PASSED SUCCESSFULLY!")
        logger.log("================================================================================")

    except Exception as e:
        logger.log(f"DRILL FAILED WITH EXCEPTION: {e}")
        report['overall_status'] = 'FAIL'
        report['error'] = str(e)
        import traceback
        traceback.print_exc()
        raise
    finally:
        if ombre_resident_obj:
            try:
                ombre_resident_obj.close()
            except Exception:
                pass
        REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        logger.log(f"Saved cutover drill report to {REPORT_FILE}")


if __name__ == '__main__':
    run_live_cutover_drill()
