"""Diagnose and safely repair damaged JSONL files (chat records, the cost ledger...).

Offline only: VEIL and Ombre must be closed. The original file is always saved first, every
removed line goes to a quarantine file byte for byte, and nothing is ever deleted. For the cost
ledger a repair also blocks new paid requests until a person has checked the costs."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import uuid

import ledger_recovery as ledger
from backup_restore import BackupError, _cold_lock, _utc_stamp


def _is_cost_ledger(root, path):
    return path == (Path(root) / 'data' / 'costs.jsonl').resolve()


def _resolve(root, file):
    root = Path(root).resolve()
    raw = Path(file).expanduser()
    if not raw.is_absolute():
        raw = root / raw
    if raw.is_symlink():
        raise BackupError('文件是符号链接，已停止。')
    path = raw.resolve()
    data = (root / 'data').resolve()
    try:
        path.relative_to(data)
    except ValueError:
        raise BackupError('只能处理项目 data 文件夹里的文件。') from None
    if path.suffix != '.jsonl' or not path.is_file():
        raise BackupError('请指定 data 文件夹里已存在的 .jsonl 文件。')
    return path


def _describe(problem):
    return ledger.PROBLEM_TEXT.get(problem, problem)


def diagnose(root, file, limit=20):
    """Plain-language findings; changes nothing."""
    path = _resolve(root, file)
    raw = path.read_bytes()
    lines = ledger.read_lines(raw)
    cost = _is_cost_ledger(root, path)
    problems = ledger.find_problems(lines, cost_ledger=cost)
    out = [f'文件：{path}', f'大小：{len(raw)} 字节，记录 {len(lines)} 行，其中有问题的 {len(problems)} 行。']
    if not problems:
        out.append('没有发现问题。')
    for line, problem in problems[:limit]:
        out.append(f'  第 {line.number} 行（从第 {line.offset} 字节起）：{_describe(problem)}')
        out.append(f'    内容开头：{line.preview!r}')
    if len(problems) > limit:
        out.append(f'  …还有 {len(problems) - limit} 行未列出。')
    serious = [item for item in problems if item[1] != 'no-newline']
    if serious:
        out.append('修复时：先保存原文件，再把这些行原样移到隔离文件，其余记录一字不动；不会删除任何东西。')
        if cost:
            out.append('这是费用账本：被隔离的行可能对应真实花费，修复后会暂停所有付费请求，'
                       '直到你核对供应商账单并确认。')
    elif problems:
        out.append('只需要在末尾补一个换行，不会改动任何记录。')
    return out


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path, payload):
    temporary = path.with_name(path.name + f'.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def repair(root, file, *, confirm=input, notice=print):
    root = Path(root).resolve()
    path = _resolve(root, file)
    cost = _is_cost_ledger(root, path)
    with _cold_lock(root):
        raw = path.read_bytes()
        lines = ledger.read_lines(raw)
        problems = ledger.find_problems(lines, cost_ledger=cost)
        if not problems:
            notice('没有发现问题，文件没有改动。')
            return None
        serious = [(line, problem) for line, problem in problems if problem != 'no-newline']
        if not serious:
            with path.open('ab', buffering=0) as handle:
                handle.write(b'\n')
                os.fsync(handle.fileno())
            notice('只补上了末尾的换行，没有改动任何记录。')
            return {'quarantined': 0, 'original_copy': None}
        for line in diagnose(root, path):
            notice(line)
        if confirm('确认按上面的方式修复？输入 REPAIR：') != 'REPAIR':
            raise BackupError('未确认，文件没有改动。')
        stamp = _utc_stamp()
        original_copy = path.with_name(f'{path.name}.corrupt-{stamp}')
        shutil.copy2(path, original_copy)
        if _sha256(original_copy) != _sha256(path):
            original_copy.unlink(missing_ok=True)
            raise BackupError('原文件备份校验失败，已停止，文件没有改动。')
        bad = {id(line) for line, _ in serious}
        quarantine = path.with_name(f'{path.stem}.quarantine-{stamp}.jsonl')
        _write_atomic(quarantine, b''.join(ledger._terminated(line.raw) for line, _ in serious))
        repaired = b''.join(ledger._terminated(line.raw) for line in lines if id(line) not in bad)
        if cost:
            ledger.validate_cost_ledger(repaired)
        _write_atomic(path, repaired)
        after = ledger.find_problems(ledger.read_lines(path.read_bytes()), cost_ledger=cost)
        if after:
            raise BackupError(f'修复后仍有问题（{len(after)} 行）；原文件已保存为 {original_copy.name}。')
        if cost:
            ledger.mark_unverified(path.parent, 'repair-quarantined', len(serious), quarantine.name,
                                   original_copy=original_copy.name)
        notice(f'已修复。原文件保存为：{original_copy}')
        notice(f'被隔离的 {len(serious)} 行（原样保留）：{quarantine}')
        if cost:
            notice('费用账本里有被隔离的行，已暂停所有付费请求。请核对供应商账单后运行 '
                   'data_repair.py ledger-verify 确认。')
        return {'quarantined': len(serious), 'original_copy': str(original_copy), 'quarantine': str(quarantine)}


def ledger_status(root):
    data = Path(root).resolve() / 'data'
    marker = ledger.read_marker(data)
    if marker is None:
        return ['费用账本没有待核实的记录。']
    out = ['费用账本有待核实的记录，已暂停所有付费请求（聊天、自主活动、瞄一眼、记忆整理都不会发请求）。']
    for item in marker.get('items', []):
        out.append(f"  - {item.get('kind')}：{item.get('count')} 项，证据文件 data/{item.get('evidence')}"
                   + (f"，原文件 data/{item['original_copy']}" if item.get('original_copy') else ''))
    out.append('请对照供应商账单，确认保留的账本金额合理；确认后运行 data_repair.py ledger-verify。')
    return out


def ledger_verify(root, *, confirm=input, notice=print):
    root = Path(root).resolve()
    with _cold_lock(root):
        marker = ledger.read_marker(root / 'data')
        if marker is None:
            notice('没有待核实的费用记录。')
            return None
        for line in ledger_status(root):
            notice(line)
        if confirm('你已核对供应商账单并确认费用？输入 VERIFIED：') != 'VERIFIED':
            raise BackupError('未确认，仍然暂停付费请求。')
        archived = ledger.archive_marker(root / 'data')
        notice(f'已确认，付费请求恢复。记录保留在：{archived}（证据文件也都还在）。')
        return archived


def _parser():
    parser = argparse.ArgumentParser(description='VEIL Agent 数据文件诊断与修复（离线，需先关闭 VEIL）')
    sub = parser.add_subparsers(dest='command', required=True)
    for name, text in (('diagnose', '只查看，不改动任何东西'), ('repair', '保存原文件、隔离损坏行后修复')):
        item = sub.add_parser(name, help=text)
        item.add_argument('--file', required=True, help='data 文件夹里的 .jsonl 文件')
    sub.add_parser('ledger-status', help='查看费用账本是否有待核实的记录')
    sub.add_parser('ledger-verify', help='核对供应商账单后，确认费用并恢复付费请求')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parent
    try:
        if args.command == 'diagnose':
            print('\n'.join(diagnose(root, args.file)))
        elif args.command == 'repair':
            repair(root, args.file)
        elif args.command == 'ledger-status':
            print('\n'.join(ledger_status(root)))
        else:
            ledger_verify(root)
    except (BackupError, OSError) as exc:
        print(f'操作失败：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
