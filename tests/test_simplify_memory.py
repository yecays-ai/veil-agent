"""Ticket 426: behaviour tests for simplify_memory.py on fake data only."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'simplify_memory.py'
ROLE = 'peter'

LETTER_ONE_ID = '54b1257a8088'
LETTER_TWO_ID = '04c5bbaf5f84'
PLAN_ONE_ID = '65d03df8e211'
PLAN_TWO_ID = '830b63fefd1a'
FEEL_ID = 'feel_202610191458_V050'
SELF_ID = 'aabfd4e3cc2d'
UNRELATED_ID = 'unrelated00000000000000'

LETTER_ONE_BODY = '\nDear Lan,\n\nThis is the first fake letter.\n'
LETTER_TWO_BODY = '\nHappy birthday.\n\nThis is the second fake letter.\n'
PLAN_ONE_BODY = "\nBuild Lan's desktop spider.\n\nSecond paragraph with detail."
PLAN_TWO_BODY = '\nBuild the birthday present.\nSix pieces, one per day.'
FEEL_BODY = '\nA fake feel memory.\n'
SELF_BODY = '\nI would rather be wrong out loud.\n'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8'))


def frontmatter(**values):
    lines = ['---']
    for key, value in values.items():
        lines.append(f'{key}: {value}')
    lines.append('---')
    return '\n'.join(lines) + '\n'


def run_script(data_root, *args):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--data-root', str(data_root), *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8',
        errors='replace', env=env, timeout=120)


def snapshot(root):
    files = {}
    if not root.exists():
        return files
    for path in sorted(root.rglob('*')):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def read_records(role_root):
    return json.loads((role_root / 'life-records.json').read_text(encoding='utf-8'))['records']


def log_entries(role_root):
    path = role_root / 'memory-simplify-log.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def embedding_ids(path):
    connection = sqlite3.connect(str(path))
    try:
        return {row[0] for row in connection.execute('SELECT bucket_id FROM embeddings')}
    finally:
        connection.close()


def _build_fake(tmp_path):
    data_root = tmp_path / 'data'
    role_root = data_root / ROLE
    memory = role_root / 'memory'
    write(data_root / 'session.lock', '0')

    letters = memory / 'letters' / 'history'
    write(letters / f'2026-10-25 03-09-01 Not from a post_{LETTER_ONE_ID}.md',
          frontmatter(id=LETTER_ONE_ID, title='Not from a post',
                      created="'2026-10-25T03:09:01+08:00'", author='Peter',
                      type='letter') + LETTER_ONE_BODY)
    write(letters / f'2026-10-30 15-59-35 For Oct 31_{LETTER_TWO_ID}.md',
          frontmatter(id=LETTER_TWO_ID, title='For Oct 31: the guy/from "hard drive"?',
                      created="'2026-10-30T15:59:35+08:00'", author='Peter',
                      type='letter') + LETTER_TWO_BODY)

    plans_active = memory / 'plans' / 'active'
    plans_resolved = memory / 'plans' / 'resolved'
    write(plans_active / f'2026-10-23 13-38-38_{PLAN_ONE_ID}.md',
          frontmatter(id=PLAN_ONE_ID, created="'2026-10-23T13:38:38+08:00'",
                      status='active', type='plan') + PLAN_ONE_BODY)
    write(plans_resolved / f'2026-10-25 03-23-25_{PLAN_TWO_ID}.md',
          frontmatter(id=PLAN_TWO_ID, created="'2026-10-25T03:23:25+08:00'",
                      status='resolved', type='plan') + PLAN_TWO_BODY)

    feel_path = memory / 'feel' / '沉淀物' / f'2026-10-19 14-58-41 Some feel_{FEEL_ID}.md'
    write(feel_path,
          frontmatter(id=FEEL_ID, created="'2026-10-19T14:58:41+08:00'",
                      type='feel') + FEEL_BODY)
    self_path = memory / 'dynamic' / 'self' / f'2026-11-06 11-20-50_{SELF_ID}.md'
    write(self_path,
          frontmatter(id=SELF_ID, created="'2026-11-06T11:20:50+08:00'",
                      type='i') + SELF_BODY)

    existing = {
        'id': 'existing0000000000000000',
        'revision': 1,
        'kind': 'note',
        'title': 'existing note',
        'content': 'keep me',
        'owner': 'Peter',
        'date': '2026-11-01',
        'time': '',
        'state': 'active',
        'mood': '',
        'repeat': 'none',
        'created_at': '2026-11-01T10:00:00+08:00',
        'created_by': 'Peter',
        'updated_at': '2026-11-01T10:00:00+08:00',
        'updated_by': 'Peter',
        'history': [],
    }
    write(role_root / 'life-records.json',
          json.dumps({'records': [existing]}, ensure_ascii=False, indent=2) + '\n')

    embeddings = memory / 'embeddings.db'
    connection = sqlite3.connect(str(embeddings))
    try:
        connection.execute(
            'CREATE TABLE embeddings ('
            'bucket_id TEXT PRIMARY KEY, embedding BLOB NOT NULL, '
            'updated_at TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT \'\')')
        for bucket_id in (FEEL_ID, SELF_ID, UNRELATED_ID):
            connection.execute(
                'INSERT INTO embeddings (bucket_id, embedding, updated_at) VALUES (?, ?, ?)',
                (bucket_id, b'fake-vector', '2026-11-01T10:00:00+08:00'))
        connection.commit()
    finally:
        connection.close()

    return {
        'data_root': data_root,
        'role_root': role_root,
        'memory': memory,
        'letters': letters,
        'plans_active': plans_active,
        'plans_resolved': plans_resolved,
        'feel_path': feel_path,
        'self_path': self_path,
        'embeddings': embeddings,
    }


class SimplifyMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = _build_fake(Path(self.temp.name))

    def test_dry_run_writes_nothing(self):
        before = snapshot(self.fake['data_root'])
        result = run_script(self.fake['data_root'])
        assert result.returncode == 0, result.stderr
        assert 'dry-run' in result.stdout
        assert PLAN_ONE_ID in result.stdout
        assert snapshot(self.fake['data_root']) == before
        assert not (self.fake['role_root'] / 'backups').exists()
        assert not (self.fake['role_root'] / 'workspace' / 'lab' / 'letters').exists()
        assert not (self.fake['role_root'] / 'memory-simplify-log.jsonl').exists()
        assert self.fake['feel_path'].exists()
        assert self.fake['self_path'].exists()
        assert embedding_ids(self.fake['embeddings']) == {FEEL_ID, SELF_ID, UNRELATED_ID}
        assert len(read_records(self.fake['role_root'])) == 1



    def test_apply_exports_letters_migrates_plans_and_deletes_feel_and_i(self):
        result = run_script(self.fake['data_root'], '--apply')
        assert result.returncode == 0, result.stderr
        assert '汇总' in result.stdout

        exported = sorted((self.fake['role_root'] / 'workspace' / 'lab' / 'letters').glob('*.md'))
        assert len(exported) == 2
        exported_bytes = {path.read_bytes() for path in exported}
        assert LETTER_ONE_BODY.encode('utf-8') in exported_bytes
        assert LETTER_TWO_BODY.encode('utf-8') in exported_bytes
        for path in exported:
            assert not set(path.name) & set('<>:"/\\|?*')
            assert len(path.stem) <= 60 + 1 + 32
        assert len(list(self.fake['letters'].glob('*.md'))) == 2

        records = read_records(self.fake['role_root'])
        assert len(records) == 3
        plans = [row for row in records if row['kind'] == 'plan']
        assert len(plans) == 2
        by_title = {row['title']: row for row in plans}
        spider = by_title["Build Lan's desktop spider."]
        birthday = by_title['Build the birthday present.']
        for row, body, day, _plan_id, created in (
                (spider, PLAN_ONE_BODY, '2026-10-23', PLAN_ONE_ID,
                 '2026-10-23T13:38:38+08:00'),
                (birthday, PLAN_TWO_BODY, '2026-10-25', PLAN_TWO_ID,
                 '2026-10-25T03:23:25+08:00')):
            assert row['kind'] == 'plan'
            assert row['state'] == 'completed'
            assert row['revision'] == 1
            assert row['owner'] == 'Peter'
            assert row['created_by'] == 'Peter'
            assert row['updated_by'] == 'Peter'
            assert row['date'] == day
            assert row['time'] == ''
            assert row['repeat'] == 'none'
            assert row['mood'] == ''
            assert row['content'] == body.strip()
            assert row['created_at'] == created
            assert row['history'] == []
            assert row['completed_at']

        assert self.fake['feel_path'].exists() is False
        assert self.fake['self_path'].exists() is False
        assert self.fake['plans_active'].joinpath(
            f'2026-10-23 13-38-38_{PLAN_ONE_ID}.md').exists()
        assert self.fake['plans_resolved'].joinpath(
            f'2026-10-25 03-23-25_{PLAN_TWO_ID}.md').exists()

        assert embedding_ids(self.fake['embeddings']) == {UNRELATED_ID}

        backups = sorted((self.fake['role_root'] / 'backups').glob('simplify-*'))
        assert len(backups) == 1
        backup = backups[0]
        assert (backup / 'memory' / 'feel' / '沉淀物' / self.fake['feel_path'].name).exists()
        assert (backup / 'memory' / 'dynamic' / 'self' / self.fake['self_path'].name).exists()
        assert (backup / 'memory' / 'embeddings.db').exists()
        backup_records = json.loads(
            (backup / 'life-records.json').read_text(encoding='utf-8'))['records']
        assert len(backup_records) == 1

        entries = log_entries(self.fake['role_root'])
        assert entries
        for entry in entries:
            assert {'time', 'action', 'target', 'result'} <= set(entry)
        assert any(entry['action'] == 'letter-export' and entry['result'] == 'exported'
                   for entry in entries)
        assert any(entry['action'] == 'plan-migrate' and entry['result'] == 'inserted'
                   for entry in entries)
        assert any(entry['action'] == 'feel-delete' and entry['result'] == 'deleted'
                   for entry in entries)
        assert any(entry['action'] == 'i-delete' and entry['result'] == 'deleted'
                   for entry in entries)
        assert any(entry['action'] == 'embedding-delete' and entry['result'] == 'deleted'
                   for entry in entries)
        assert not (self.fake['role_root'] / 'life-records.json.tmp').exists()



    def test_second_apply_is_idempotent(self):
        first = run_script(self.fake['data_root'], '--apply')
        assert first.returncode == 0, first.stderr
        records_after_first = (self.fake['role_root'] / 'life-records.json').read_bytes()
        exported_after_first = {
            path.name: path.read_bytes()
            for path in (self.fake['role_root'] / 'workspace' / 'lab' / 'letters').glob('*.md')
        }
        ids_after_first = embedding_ids(self.fake['embeddings'])
        log_after_first = len(log_entries(self.fake['role_root']))

        second = run_script(self.fake['data_root'], '--apply')
        assert second.returncode == 0, second.stderr
        assert '汇总' in second.stdout
        assert (self.fake['role_root'] / 'life-records.json').read_bytes() == records_after_first
        assert {
            path.name: path.read_bytes()
            for path in (self.fake['role_root'] / 'workspace' / 'lab' / 'letters').glob('*.md')
        } == exported_after_first
        assert embedding_ids(self.fake['embeddings']) == ids_after_first == {UNRELATED_ID}
        assert len([row for row in read_records(self.fake['role_root']) if row['kind'] == 'plan']) == 2

        entries = log_entries(self.fake['role_root'])
        assert len(entries) > log_after_first
        assert any(entry['action'] == 'letter-export' and entry['result'] == 'skipped-existing'
                   for entry in entries)
        assert any(entry['action'] == 'plan-migrate' and entry['result'] == 'skipped-duplicate'
                   for entry in entries)



    def test_refuses_when_veil_holds_session_lock(self):
        lock_path = self.fake['data_root'] / 'session.lock'
        handle = lock_path.open('a+b')
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = run_script(self.fake['data_root'], '--apply')
        finally:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert 'VEIL' in output
        assert '关闭' in output
        assert not (self.fake['role_root'] / 'backups').exists()
        assert not (self.fake['role_root'] / 'memory-simplify-log.jsonl').exists()
        assert self.fake['feel_path'].exists()
        assert len(read_records(self.fake['role_root'])) == 1



    def test_skip_plan_leaves_that_plan_out(self):
        result = run_script(self.fake['data_root'], '--apply', '--skip-plan', PLAN_ONE_ID)
        assert result.returncode == 0, result.stderr
        plans = [row for row in read_records(self.fake['role_root']) if row['kind'] == 'plan']
        assert [row['title'] for row in plans] == ['Build the birthday present.']
        assert self.fake['plans_active'].joinpath(
            f'2026-10-23 13-38-38_{PLAN_ONE_ID}.md').exists()
        assert any(entry['action'] == 'plan-skip'
                   and entry['target'] == PLAN_ONE_ID
                   and entry['result'] == 'skipped-by-flag'
                   for entry in log_entries(self.fake['role_root']))



    def test_duplicate_letter_title_gets_id_suffix(self):
        duplicate_id = 'deadbeef0001'
        duplicate_body = '\nA duplicate-title letter.\n'
        write(self.fake['letters'] / f'2026-10-25 03-10-00 Not from a post_{duplicate_id}.md',
              frontmatter(id=duplicate_id, title='Not from a post',
                          created="'2026-10-25T03:10:00+08:00'", author='Peter',
                          type='letter') + duplicate_body)

        result = run_script(self.fake['data_root'], '--apply')
        assert result.returncode == 0, result.stderr
        exported = {path.name: path.read_bytes()
                    for path in (self.fake['role_root'] / 'workspace' / 'lab' / 'letters').glob('*.md')}
        assert len(exported) == 3
        assert exported['2026-10-25-Not-from-a-post.md'] == LETTER_ONE_BODY.encode('utf-8')
        assert exported[f'2026-10-25-Not-from-a-post-{duplicate_id}.md'] == duplicate_body.encode('utf-8')
        assert self.fake['letters'].joinpath(
            f'2026-10-25 03-10-00 Not from a post_{duplicate_id}.md').exists()


if __name__ == '__main__':
    unittest.main()
