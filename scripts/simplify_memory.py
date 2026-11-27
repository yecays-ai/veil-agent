"""Ticket 426: one-shot, repeatable cleanup for the four Ombre features.

Default is a dry-run: every planned action is printed but nothing is written.
With --apply the script first backs up the role's whole memory/ tree and
life-records.json to data/<role>/backups/simplify-<timestamp>/, then:

  1. exports letters to data/<role>/workspace/lab/letters/ as plain Markdown;
  2. moves plans into data/<role>/life-records.json as completed kind=plan rows;
  3. deletes feel/I Markdown files and their rows in memory/embeddings.db.

Letter and plan source files are kept. Only standard library, no network and no
model calls. The script refuses to run while VEIL holds data/session.lock.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import date as calendar_date, datetime
from pathlib import Path

ROLE_RE = re.compile(r'^[A-Za-z0-9_-]+$')
DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
WINDOWS_BAD_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MULTI_HYPHEN_RE = re.compile(r'-{2,}')
SPACE_RE = re.compile(r'\s+')
MAX_LETTER_STEM = 60


class MigrationError(RuntimeError):
    """A data or filesystem problem that stops the migration."""


class AlreadyRunning(MigrationError):
    """VEIL currently owns data/session.lock."""


def _now():
    return datetime.now().astimezone().isoformat()


def _stamp():
    return datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')


def split_frontmatter(text):
    """Return (simple top-level metadata, body).

    The body is exactly the text after the closing '---' line; no newline is
    added or removed. Only one-line 'key: value' pairs are parsed, which is all
    the four Ombre features write. Multiline YAML is ignored.
    """
    if text.startswith('\ufeff'):
        text = text[1:]
    if not text.startswith('---'):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        return {}, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            end = index
            break
    if end is None:
        return {}, text
    meta = {}
    for line in lines[1:end]:
        if not line.strip() or line[:1].isspace():
            continue
        key, separator, value = line.partition(':')
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in meta:
            meta[key] = value
    return meta, ''.join(lines[end + 1:])


def _valid_day(value):
    day = str(value or '').strip()[:10]
    if not DAY_RE.fullmatch(day):
        return ''
    try:
        calendar_date.fromisoformat(day)
    except ValueError:
        return ''
    return day


def _day_from_name(path):
    match = re.match(r'(\d{4}-\d{2}-\d{2})', path.name)
    return _valid_day(match.group(1)) if match else ''


def _slug(title):
    text = WINDOWS_BAD_RE.sub('', str(title or ''))
    text = SPACE_RE.sub('-', text.strip())
    text = MULTI_HYPHEN_RE.sub('-', text).strip(' .-_')
    return text or 'letter'


def _letter_stem(day, title):
    slug = _slug(title)
    room = MAX_LETTER_STEM - len(day) - 1
    if len(slug) > room:
        slug = slug[:room].rstrip(' .-_') or 'letter'
    return f'{day}-{slug}'


def _safe_id(value):
    text = re.sub(r'[^A-Za-z0-9_-]+', '', str(value or ''))
    return text[:32] or 'letter'


def _letter_target(out_dir, stem, letter_id, wanted):
    """Pick a target path; return (path, 'new'|'same').

    A rerun that finds the same bytes at the same name is a no-op. A real name
    clash gets the source id as a suffix, as agreed in ticket 426.
    """
    for name in (f'{stem}.md', f'{stem}-{letter_id}.md'):
        path = out_dir / name
        if not path.exists():
            return path, 'new'
        if path.read_bytes() == wanted:
            return path, 'same'
    index = 2
    while True:
        path = out_dir / f'{stem}-{letter_id}-{index}.md'
        if not path.exists():
            return path, 'new'
        if path.read_bytes() == wanted:
            return path, 'same'
        index += 1


def _plan_title(body, fallback):
    for line in body.splitlines():
        line = line.strip()
        if line:
            return line[:80].rstrip()
    return (str(fallback or '').strip() or 'plan')[:80].rstrip()


def _try_lock(handle):
    """Take the one-byte advisory lock Windows/macOS and Linux both understand."""
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle):
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


def ombre_running(lock_path):
    """True while the role's Ombre resident still owns memory/_agent/ombre.lock."""
    if not lock_path.exists():
        return False
    try:
        handle = lock_path.open('a+b')
    except OSError:
        return True
    try:
        if not _try_lock(handle):
            return True
        _unlock(handle)
        return False
    finally:
        handle.close()


@contextlib.contextmanager
def session_guard(lock_path, create):
    """Hold VEIL's global run lock for the whole migration.

    Opening with 'a+b' mirrors veil_agent.py. A dry-run never creates the lock
    file, so it stays read-only even on an untouched data directory.
    """
    if not lock_path.exists():
        if not create:
            yield
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open('a+b')
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        empty = handle.tell() == 0
        if empty and not create:
            yield
            return
        if empty:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if not _try_lock(handle):
            raise AlreadyRunning(
                f'{lock_path} 被占用，VEIL 正在运行；请先完全关闭 VEIL 再运行。')
        locked = True
        yield
    finally:
        if locked:
            _unlock(handle)
        handle.close()


class Migration:
    def __init__(self, data_root, role, apply, skip_plans):
        self.data_root = data_root
        self.role = role
        self.apply = apply
        self.mode = 'apply' if apply else 'dry-run'
        self.skip_plans = set(skip_plans)
        self.role_root = data_root / role
        self.memory_root = self.role_root / 'memory'
        self.life_path = self.role_root / 'life-records.json'
        self.embeddings_path = self.memory_root / 'embeddings.db'
        self.log_path = self.role_root / 'memory-simplify-log.jsonl'
        self.actions = []
        self.errors = []

    # --- reporting ---------------------------------------------------------
    def label(self, path):
        try:
            return Path(path).relative_to(self.data_root.parent).as_posix()
        except ValueError:
            return str(path)

    def record(self, action, target, result):
        self.actions.append((action, str(target), result))
        print(f'[{self.mode}] {action} {target} -> {result}')
        if not self.apply:
            return
        entry = {'time': _now(), 'action': action, 'target': str(target), 'result': result}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def count(self, action, *results):
        wanted = set(results)
        return sum(1 for name, _, result in self.actions
                   if name == action and (not wanted or result in wanted))

    # --- backup ------------------------------------------------------------
    def backup_dir(self):
        base = self.role_root / 'backups' / f'simplify-{_stamp()}'
        candidate = base
        index = 1
        while candidate.exists():
            candidate = base.with_name(f'{base.name}-{index}')
            index += 1
        return candidate

    def backup(self):
        destination = self.backup_dir()
        staged = destination.with_name(destination.name + '.partial')
        staged.mkdir(parents=True)
        copied = []
        try:
            if self.memory_root.is_dir():
                shutil.copytree(self.memory_root, staged / 'memory', symlinks=True)
                copied.append('memory')
            if self.life_path.is_file():
                shutil.copy2(self.life_path, staged / 'life-records.json')
                copied.append('life-records.json')
            # Publish only a complete backup: a half copy must never look restorable.
            os.replace(staged, destination)
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return destination, copied

    # --- letters -----------------------------------------------------------
    def safe_feature_files(self, base):
        """Files inside the role memory tree only; a redirected directory is refused."""
        root = self.memory_root.resolve()
        if base.is_symlink() or not base.resolve().is_relative_to(root):
            raise MigrationError(f'记忆目录被重定向：{base}')
        found = []
        for path in sorted(base.rglob('*.md')):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                self.record('skip-outside', self.label(path), 'skipped-symlink')
                continue
            found.append(path)
        return found

    def export_letters(self):
        source = self.memory_root / 'letters' / 'history'
        if not source.is_dir():
            return
        out_dir = self.role_root / 'workspace' / 'lab' / 'letters'
        for path in self.safe_feature_files(source):
            try:
                text = path.read_bytes().decode('utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                self.errors.append(f'读信件失败 {path}: {exc}')
                self.record('letter-export', self.label(path), f'failed: {exc}')
                continue
            meta, body = split_frontmatter(text)
            file_type = meta.get('type', '').strip()
            if file_type and file_type != 'letter':
                self.record('letter-export', self.label(path), 'skipped-not-letter')
                continue
            day = _valid_day(meta.get('created')) or _day_from_name(path)
            if not day:
                self.errors.append(f'信件没有可用日期 {path}')
                self.record('letter-export', self.label(path), 'skipped-no-date')
                continue
            if not body.strip():
                self.record('letter-export', self.label(path), 'skipped-empty')
                continue
            title = meta.get('title') or meta.get('name') or path.stem
            letter_id = _safe_id(meta.get('id') or path.stem.rsplit('_', 1)[-1])
            wanted = body.encode('utf-8')
            try:
                target, state = _letter_target(
                    out_dir, _letter_stem(day, title), letter_id, wanted)
            except OSError as exc:
                self.errors.append(f'检查导出文件失败 {path}: {exc}')
                self.record('letter-export', self.label(path), f'failed: {exc}')
                continue
            if state == 'same':
                self.record('letter-export', self.label(target), 'skipped-existing')
                continue
            if not self.apply:
                self.record('letter-export', self.label(target), 'would-export')
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.tmp')
                with temporary.open('wb') as handle:
                    handle.write(wanted)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            except OSError as exc:
                try:
                    target.with_name(target.name + '.tmp').unlink()
                except OSError:
                    pass
                self.errors.append(f'导出信件失败 {target}: {exc}')
                self.record('letter-export', self.label(target), f'failed: {exc}')
                continue
            self.record('letter-export', self.label(target), 'exported')

    # --- plans -> life-records.json ----------------------------------------
    def read_life(self):
        if not self.life_path.exists():
            return {'records': []}, []
        try:
            payload = json.loads(self.life_path.read_bytes().decode('utf-8-sig'))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise MigrationError(f'life-records.json 读取失败：{exc}') from exc
        if not isinstance(payload, dict) or not isinstance(payload.get('records'), list):
            raise MigrationError('life-records.json 结构不对（应为 {"records": [...]}）')
        rows = payload['records']
        if not all(isinstance(row, dict) for row in rows):
            raise MigrationError('life-records.json 里混有非对象记录')
        return payload, rows

    def write_life(self, payload, rows):
        payload = dict(payload) if isinstance(payload, dict) else {'records': []}
        payload['records'] = rows
        data = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
        temporary = self.life_path.with_name(self.life_path.name + '.tmp')
        self.life_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('w', encoding='utf-8') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.life_path)

    def migrate_plans(self):
        directories = (self.memory_root / 'plans' / 'active',
                       self.memory_root / 'plans' / 'resolved')
        plan_paths = [path for directory in directories if directory.is_dir()
                      for path in self.safe_feature_files(directory)]
        payload, rows = self.read_life()
        existing = {(row.get('kind'), row.get('date'), row.get('title')) for row in rows}
        existing_ids = {row.get('ombre_plan_id') for row in rows if row.get('ombre_plan_id')}
        stamp = _now()
        added = []
        for path in plan_paths:
            try:
                text = path.read_bytes().decode('utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                self.errors.append(f'读计划失败 {path}: {exc}')
                self.record('plan-migrate', self.label(path), f'failed: {exc}')
                continue
            meta, body = split_frontmatter(text)
            file_type = meta.get('type', '').strip()
            if file_type and file_type != 'plan':
                self.record('plan-migrate', self.label(path), 'skipped-not-plan')
                continue
            fallback_id = path.stem.rsplit('_', 1)[-1]
            plan_id = (meta.get('id') or fallback_id).strip()
            if not plan_id:
                plan_id = self.label(path)
            if plan_id in self.skip_plans:
                self.record('plan-skip', plan_id, 'skipped-by-flag')
                continue
            day = _valid_day(meta.get('created')) or _day_from_name(path)
            if not day:
                self.errors.append(f'计划没有可用日期 {path}')
                self.record('plan-migrate', plan_id, 'skipped-no-date')
                continue
            title = _plan_title(body, meta.get('name') or plan_id)
            key = ('plan', day, title)
            if plan_id in existing_ids or key in existing:
                self.record('plan-migrate', plan_id, 'skipped-duplicate')
                continue
            existing.add(key)
            existing_ids.add(plan_id)
            created_at = str(meta.get('created') or day).strip()
            row = {
                'id': secrets.token_hex(12),
                'revision': 1,
                'kind': 'plan',
                'title': title,
                'content': body.strip(),
                'owner': 'Peter',
                'date': day,
                'time': '',
                'state': 'completed',
                'mood': '',
                'repeat': 'none',
                'remind': False,
                'remind_before': 0,
                'auto_recorded': False,
                'ombre_plan_id': plan_id,
                'created_at': created_at,
                'created_by': 'Peter',
                'updated_at': stamp,
                'updated_by': 'Peter',
                'history': [],
                'completed_at': stamp,
            }
            added.append((plan_id, row))
        if not added:
            return
        if not self.apply:
            for plan_id, _ in added:
                self.record('plan-migrate', plan_id, 'would-insert')
            return
        try:
            self.write_life(payload, rows + [row for _, row in added])
        except (OSError, ValueError) as exc:
            self.errors.append(f'写 life-records.json 失败：{exc}')
            for plan_id, _ in added:
                self.record('plan-migrate', plan_id, f'failed: {exc}')
            return
        for plan_id, _ in added:
            self.record('plan-migrate', plan_id, 'inserted')

    # --- feel / I ----------------------------------------------------------
    def delete_embedding(self, bucket_id):
        if not self.embeddings_path.exists():
            return 'db-missing'
        try:
            connection = sqlite3.connect(str(self.embeddings_path), timeout=10)
        except (sqlite3.Error, OSError):
            return 'db-error'
        try:
            found = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='embeddings'"
            ).fetchone()
            if not found:
                return 'table-missing'
            cursor = connection.execute(
                'DELETE FROM embeddings WHERE bucket_id = ?', (bucket_id,))
            connection.commit()
            return 'deleted' if cursor.rowcount else 'not-found'
        except sqlite3.Error:
            return 'db-error'
        finally:
            connection.close()

    def remove_feature(self, feature):
        if feature == 'feel':
            base = self.memory_root / 'feel'
            expected_type = 'feel'
            action = 'feel-delete'
        elif feature == 'i':
            base = self.memory_root / 'dynamic' / 'self'
            expected_type = 'i'
            action = 'i-delete'
        else:
            raise ValueError(feature)
        if not base.is_dir():
            return
        for path in self.safe_feature_files(base):
            try:
                text = path.read_bytes().decode('utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                self.errors.append(f'读 {feature} 记忆失败 {path}: {exc}')
                self.record(action, self.label(path), f'failed: {exc}')
                continue
            meta, _ = split_frontmatter(text)
            file_type = meta.get('type', '').strip()
            if file_type != expected_type:
                self.record(action, self.label(path), f'skipped-not-{expected_type}')
                continue
            memory_id = meta.get('id', '').strip()
            if not memory_id:
                self.errors.append(f'{feature} 记忆没有 id {path}')
                self.record(action, self.label(path), 'skipped-no-id')
                continue
            if not self.apply:
                self.record(action, self.label(path), 'would-delete')
                db_result = 'would-delete' if self.embeddings_path.exists() else 'db-missing'
                self.record('embedding-delete',
                            f'{self.label(self.embeddings_path)}:{memory_id}', db_result)
                continue
            embedding_result = self.delete_embedding(memory_id)
            self.record('embedding-delete',
                        f'{self.label(self.embeddings_path)}:{memory_id}', embedding_result)
            if embedding_result == 'db-error':
                # A locked or damaged index must not leave a deleted file with a live vector:
                # keep the Markdown and let a rerun finish once the database is writable.
                self.errors.append(f'删向量失败，保留文件 {memory_id}')
                self.record(action, self.label(path), 'kept-db-error')
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                self.record(action, self.label(path), 'skipped-missing')
                continue
            except OSError as exc:
                self.errors.append(f'删 {feature} 记忆失败 {path}: {exc}')
                self.record(action, self.label(path), f'failed: {exc}')
                continue
            self.record(action, self.label(path), 'deleted')

    # --- summary / entry ---------------------------------------------------
    def print_summary(self):
        letters = self.count('letter-export', 'exported', 'would-export')
        letters_skipped = self.count('letter-export', 'skipped-existing')
        plans = self.count('plan-migrate', 'inserted', 'would-insert')
        plan_duplicates = self.count('plan-migrate', 'skipped-duplicate')
        plan_skipped = self.count('plan-skip')
        feel = self.count('feel-delete', 'deleted', 'would-delete')
        identities = self.count('i-delete', 'deleted', 'would-delete')
        vectors = self.count('embedding-delete', 'deleted', 'would-delete')
        vectors_missing = self.count('embedding-delete', 'not-found', 'db-missing', 'table-missing')
        prefix = '汇总：' if self.apply else '汇总（dry-run，未写任何文件）：'
        print()
        print(prefix
              + f'备份 {self.count("backup")}；信件导出 {letters}（已存在跳过 {letters_skipped}）；'
              + f'计划迁入 {plans}（重复跳过 {plan_duplicates}，--skip-plan {plan_skipped}）；'
              + f'feel 删除 {feel}；I 删除 {identities}；'
              + f'向量删除 {vectors}（未找到/无库 {vectors_missing}）；错误 {len(self.errors)}')
        for error in self.errors[:10]:
            print(f'  ! {error}')
        if len(self.errors) > 10:
            print(f'  ! ... 另有 {len(self.errors) - 10} 条错误')

    def run(self):
        print('VEIL 记忆简化迁移（工单 426）')
        print(f'数据目录：{self.data_root}  角色：{self.role}  模式：{self.mode}')
        lock_path = self.data_root / 'session.lock'
        with session_guard(lock_path, create=self.apply):
            ombre_lock = self.memory_root / '_agent' / 'ombre.lock'
            if ombre_running(ombre_lock):
                self.errors.append(f'Ombre 还在运行（{self.label(ombre_lock)} 被占用）；'
                                   '等它退出后再运行，否则迁移会和它的写入打架。')
                self.record('preflight', self.label(ombre_lock), 'ombre-running')
                self.print_summary()
                return 1
            print('运行检测：VEIL 与 Ombre 都没在运行，可以继续。')
            if self.apply:
                self.record('start', self.label(self.role_root), 'apply')
                try:
                    destination, copied = self.backup()
                except OSError as exc:
                    self.errors.append(f'备份失败：{exc}')
                    self.record('backup', self.label(self.role_root / 'backups'), f'failed: {exc}')
                    self.print_summary()
                    return 1
                self.record('backup', self.label(destination),
                            'copied' + (': ' + ','.join(copied) if copied else ''))
            else:
                self.record('backup', self.label(self.backup_dir()), 'would-copy')
            for step, action in ((self.export_letters, 'letter-export'),
                                 (self.migrate_plans, 'plan-migrate'),
                                 (lambda: self.remove_feature('feel'), 'feel-delete'),
                                 (lambda: self.remove_feature('i'), 'i-delete')):
                try:
                    step()
                except MigrationError as exc:
                    self.errors.append(str(exc))
                    self.record(action, self.label(self.memory_root), f'failed: {exc}')
            if self.apply:
                self.record('finish', self.label(self.role_root),
                            'ok' if not self.errors else f'errors={len(self.errors)}')
        self.print_summary()
        return 0 if not self.errors else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Migrate away the four Ombre features (letters/plans/feel/I). '
                    'Dry-run by default; use --apply to write.')
    parser.add_argument('--role', default='peter', help='role id (default: peter)')
    parser.add_argument('--data-root', default='data', help='data directory (default: data)')
    parser.add_argument('--apply', action='store_true', help='really write; default is dry-run')
    parser.add_argument('--skip-plan', action='append', default=[], metavar='ID',
                        help='leave this plan id in place (repeatable)')
    args = parser.parse_args(argv)
    role = args.role.strip()
    if not ROLE_RE.fullmatch(role):
        print('--role 只能是字母、数字、下划线或连字符。', file=sys.stderr)
        return 2
    data_root = Path(args.data_root).expanduser()
    if not data_root.is_dir():
        print(f'找不到 data 目录：{data_root}', file=sys.stderr)
        return 2
    data_root = data_root.resolve()
    if not (data_root / role).is_dir():
        print(f'找不到角色目录：{data_root / role}', file=sys.stderr)
        return 2
    skip_plans = [item.strip() for item in args.skip_plan if item.strip()]
    migration = Migration(data_root, role, args.apply, skip_plans)
    try:
        return migration.run()
    except AlreadyRunning as exc:
        print(f'拒绝执行：{exc}', file=sys.stderr)
        return 1
    except (MigrationError, OSError) as exc:
        print(f'迁移失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
