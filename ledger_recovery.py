"""Reading, checking and reconciling JSONL ledgers without losing a single byte.

Nothing here deletes data. A line that cannot be trusted is moved to an evidence file, never
counted as zero cost and never dropped; while cost evidence is unverified the budget refuses to
start new paid requests (see ``Budget``)."""

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

UNVERIFIED_MARKER = 'costs-unverified.json'
UNVERIFIED_EVIDENCE = 'costs-unverified.jsonl'
COST_EVENT_TYPES = ('reserve', 'dispatch', 'settle')

PROBLEM_TEXT = {
    'invalid-json': '这一行不是有效的 JSON',
    'torn-tail': '文件最后一行写到一半就中断了（没有换行结尾，也不是完整记录）',
    'bad-utf8': '这一行含有无法解码的字节',
    'not-an-object': '这一行是有效 JSON，但不是一条记录',
    'unknown-type': '费用记录的类型无法识别',
    'missing-id': '费用记录缺少请求标识（id）',
    'invalid-amount': '费用记录的金额无效',
    'invalid-period': '费用记录缺少有效的日期（period）',
    'no-newline': '最后一行是完整记录，只是少了换行结尾（安全，补上换行即可）',
}


class Line:
    """One physical line of a JSONL file, kept verbatim."""

    def __init__(self, number, offset, raw, row, problem):
        self.number, self.offset, self.raw, self.row, self.problem = number, offset, raw, row, problem

    @property
    def preview(self):
        return self.raw[:100].decode('utf-8', errors='backslashreplace').rstrip('\r\n')


def read_lines(raw):
    """Split bytes into Lines. Blank lines are skipped (the app ignores them too)."""
    lines, offset, number = [], 0, 0
    chunks = raw.splitlines(keepends=True)
    for index, chunk in enumerate(chunks):
        number += 1
        start, offset = offset, offset + len(chunk)
        if not chunk.strip():
            continue
        is_last = index == len(chunks) - 1
        unterminated = not chunk.endswith((b'\n', b'\r'))
        row, problem = None, None
        try:
            row = json.loads(chunk.decode('utf-8'))
        except UnicodeDecodeError:
            problem = 'bad-utf8'
        except ValueError:
            problem = 'torn-tail' if is_last and unterminated else 'invalid-json'
        else:
            if not isinstance(row, dict):
                row, problem = None, 'not-an-object'
            elif is_last and unterminated:
                problem = 'no-newline'
        lines.append(Line(number, start, chunk, row, problem))
    return lines


def _decimal(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def check_cost_row(row):
    """The reason Budget would refuse this ledger row, or None."""
    if row.get('type') not in COST_EVENT_TYPES:
        return 'unknown-type'
    if not isinstance(row.get('id'), str) or not row['id']:
        return 'missing-id'
    if row['type'] in ('reserve', 'settle') and _decimal(row.get('amount')) is None:
        return 'invalid-amount'
    if row['type'] == 'reserve' and (not isinstance(row.get('period'), str) or len(row['period']) < 10):
        return 'invalid-period'
    return None


def find_problems(lines, *, cost_ledger=False):
    """Every line that needs attention, as (Line, problem). 'no-newline' is a fix, not a loss."""
    found = []
    for line in lines:
        problem = line.problem
        if problem is None and cost_ledger:
            problem = check_cost_row(line.row)
        if problem:
            found.append((line, problem))
    return found


def _stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')


def _terminated(raw):
    return raw if raw.endswith((b'\n', b'\r')) or not raw else raw + b'\n'


_SIGNIFICANT = {
    'reserve': ('amount', 'period', 'character', 'model', 'category'),
    'settle': ('amount',),
    'dispatch': (),
}


def _evidence_row(kind, line=None, **fields):
    row = {'kind': kind, 'recorded': datetime.now(timezone.utc).isoformat(), **fields}
    if line is not None:
        row['line_number'] = line.number
        row['raw_b64'] = base64.b64encode(line.raw).decode('ascii')
        row['preview'] = line.preview
    return row


class MergeResult:
    def __init__(self, merged, new_requests, completed, evidence):
        self.merged = merged              # bytes: the reconciled ledger
        self.new_requests = new_requests  # requests that only the current ledger knew
        self.completed = completed        # lifecycle steps (dispatch/settle) carried over
        self.evidence = evidence          # unverifiable items, to be kept as evidence

    @property
    def unverified(self):
        return bool(self.evidence)


def merge_cost_ledgers(backup_raw, current_raw):
    """Keep the backup ledger and add what only the newer current ledger knows.

    Reconciled per request id and per lifecycle step, so one request keeps all of its
    reserve / dispatch / settle rows. A step both ledgers have but with different
    content is a conflict: the backup row stays and the other version goes to evidence."""
    backup_lines = read_lines(backup_raw)
    known = {}
    for line in backup_lines:
        if line.row is not None and check_cost_row(line.row) is None:
            known[(line.row['id'], line.row['type'])] = line.row
    backup_ids = {key[0] for key in known}
    merged = [_terminated(backup_raw)]
    evidence, new_ids, completed, seen = [], set(), 0, {}
    for line in read_lines(current_raw):
        problem = line.problem if line.problem != 'no-newline' else None
        if problem is None and line.row is not None:
            problem = check_cost_row(line.row)
        if problem:
            evidence.append(_evidence_row('unreadable-current-line', line, problem=problem))
            continue
        row = line.row
        key = (row['id'], row['type'])
        fields = _SIGNIFICANT[row['type']]
        if key in seen:
            if any(seen[key].get(name) != row.get(name) for name in fields):
                evidence.append(_evidence_row('conflict', line, id=row['id'], type=row['type'],
                                              detail='current ledger repeats this step with different content'))
            continue
        seen[key] = row
        if key in known:
            if any(known[key].get(name) != row.get(name) for name in fields):
                evidence.append(_evidence_row('conflict', line, id=row['id'], type=row['type'],
                                              kept=known[key], detail='backup and current ledger disagree'))
            continue
        merged.append(_terminated(line.raw))
        if row['id'] in backup_ids:
            completed += 1
        else:
            new_ids.add(row['id'])
    return MergeResult(b''.join(merged), len(new_ids), completed, evidence)


def validate_cost_ledger(raw):
    """Raise ValueError unless the ledger is fully readable the way Budget reads it."""
    for line in read_lines(raw):
        problem = line.problem if line.problem != 'no-newline' else None
        if problem is None and line.row is not None:
            problem = check_cost_row(line.row)
        if problem:
            raise ValueError(f'line {line.number}: {problem}')


# ---- the "costs are unverified" state ---------------------------------------------------

def marker_path(data_dir):
    return os.path.join(str(data_dir), UNVERIFIED_MARKER)


def is_unverified(data_dir):
    return os.path.lexists(marker_path(data_dir))


def read_marker(data_dir):
    try:
        with open(marker_path(data_dir), encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {'items': []}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'items': [], 'unreadable': True}


def _write_json_atomic(path, value):
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    with open(temporary, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_evidence(data_dir, rows, name=UNVERIFIED_EVIDENCE):
    """Append evidence rows (one JSON object per line) and fsync."""
    path = os.path.join(str(data_dir), name)
    payload = b''.join((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8') for row in rows)
    with open(path, 'ab', buffering=0) as handle:
        handle.write(payload)
        os.fsync(handle.fileno())
    return name


def mark_unverified(data_dir, kind, count, evidence_file, **details):
    """Block paid requests until a person has checked these items against the provider bills."""
    previous = read_marker(data_dir) or {}
    items = [item for item in previous.get('items', []) if isinstance(item, dict)]
    items.append({'kind': kind, 'count': count, 'evidence': evidence_file,
                  'recorded': datetime.now(timezone.utc).isoformat(), **details})
    _write_json_atomic(marker_path(data_dir), {
        'status': 'unverified-costs',
        'created': previous.get('created') or datetime.now(timezone.utc).isoformat(),
        'items': items,
    })


def archive_marker(data_dir):
    """Called once a person confirmed the costs. Keeps the marker file as a record."""
    source = marker_path(data_dir)
    target = os.path.join(str(data_dir), f'costs-unverified.verified-{_stamp()}.json')
    os.replace(source, target)
    return target
