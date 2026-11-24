"""P2-B isolated rollback drill: write new/updated/deleted memories, export the same IDs, start a
private Ombre instance on the exported vault, and prove the old system can read the changes.

Never touches the live Peter. Requires EMBED_API_KEY for the canary embeddings.
"""
import argparse
import json
import shutil
import socket
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_events
import brain_export
import brain_writer
import ombre_resident
import tag_governance
from dotenv import dotenv_values
from memory_agent import MCP
from recall_relevance import QueryEmbedder


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-brain', required=True)
    parser.add_argument('--events', required=True)
    parser.add_argument('--stages', required=True)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--veil-root', default=r'E:\Peter-Agent\VEIL\veil-agent')
    args = parser.parse_args(argv)
    work = Path(args.workdir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    db = work / 'brain.db'
    shutil.copyfile(args.source_brain, db)
    connection = sqlite3.connect(db)
    try:
        brain_events.import_events(connection, args.events, args.stages)
        tag_governance.apply(connection, dry_run=False)
    finally:
        connection.close()
    values = dotenv_values(args.env_file, interpolate=False, encoding='utf-8-sig')
    embedder = QueryEmbedder('https://api.siliconflow.cn/v1', 'BAAI/bge-m3',
                             values['EMBED_API_KEY'].strip())
    connection = sqlite3.connect(db)
    try:
        canary = brain_writer.create_memory(
            connection, {'body': 'rollback canary unique harbour bell', 'title': 'Canary'},
            embedder=embedder.embed)
        brain_writer.update_memory(connection, canary['memory_id'],
                                   {'body': 'rollback canary updated signal'},
                                   embedder=embedder.embed, operation_reason='rollback drill')
        doomed = brain_writer.create_memory(
            connection, {'body': 'rollback doomed memory xyzzy'}, embedder=embedder.embed)
        brain_writer.hard_delete(connection, doomed['memory_id'], 'rollback drill')
    finally:
        connection.close()
    manifest = brain_export.export_vault(db, work / 'fake')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    cfg = SimpleNamespace(id='fake', name='Fake', memory={
        'ombre_port': port, 'model': 'BAAI/bge-m3',
        'base_url': 'https://api.siliconflow.cn/v1', 'embedding_enabled': True,
        'ombre_model': False, 'input_cny_per_million': '0',
    }, provider={'api_key_env': 'DEEPSEEK_API_KEY', 'model': 'deepseek-flash',
                 'base_url': 'https://api.deepseek.com'})
    process = None
    result = {'export': {key: value for key, value in manifest.items() if key != 'body_sha256'},
              'canary_id': canary['memory_id']}
    try:
        started, token, process = ombre_resident.start_or_connect(Path(args.veil_root), cfg, data_root=work)
        client = MCP('http://127.0.0.1:%d/mcp' % started, token)
        try:
            found = client.call('breath_search', {'query': 'rollback canary updated signal',
                                                  'mode': 'automatic', 'with_ids': True,
                                                  'max_results': 5})
            gone = client.call('breath_search', {'query': 'rollback doomed memory xyzzy',
                                                 'mode': 'automatic', 'with_ids': True,
                                                 'max_results': 5})
        finally:
            client.close()
        result['canary_found'] = 'rollback canary updated signal' in found
        result['canary_id_present'] = canary['memory_id'] in found
        result['deleted_absent'] = 'rollback doomed' not in gone
    finally:
        if process is not None:
            process.close()
    (work / 'rollback-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                               encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('canary_found') and result.get('deleted_absent') else 1


if __name__ == '__main__':
    raise SystemExit(main())
