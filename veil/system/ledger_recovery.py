"""Reading, checking and reconciling JSONL ledgers without losing a single byte.

Nothing here deletes data. A line that cannot be trusted is moved to an evidence file, never
counted as zero cost and never dropped; while cost evidence is unverified the budget refuses to
start new paid requests (see ``Budget``)."""

import base64
import json
import os
import re
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


_PERIOD_START = re.compile(r'^\d{4}-\d{2}-\d{2}')


def _valid_period(value):
    """Budget buckets by the first 10 / 7 characters, so they must be a real YYYY-MM-DD date."""
    if not isinstance(value, str) or not _PERIOD_START.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_cost_row(row):
    """The reason Budget would refuse this ledger row, or None."""
    if row.get('type') not in COST_EVENT_TYPES:
        return 'unknown-type'
    if not isinstance(row.get('id'), str) or not row['id']:
        return 'missing-id'
    if row['type'] in ('reserve', 'settle') and _decimal(row.get('amount')) is None:
        return 'invalid-amount'
    if row['type'] == 'reserve' and not _valid_period(row.get('period')):
        return 'invalid-period'
    return None


def find_problems(lines, *, cost_ledger=False):
    """Every line that needs attention, as (Line, problem). 'no-newline' is a fix, not a loss."""
    found = []
    for line in lines:
        problem = line.problem if line.problem != 'no-newline' else None
        if problem is None and cost_ledger and line.row is not None:
            problem = check_cost_row(line.row)
        if problem is None:
            problem = line.problem  # a clean row that only lacks its newline
        if problem:
            found.append((line, problem))
    return found


def _stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')


def _terminated(raw):
    return raw if raw.endswith((b'\n', b'\r')) or not raw else raw + b'\n'


def _same(left, right):
    """A ledger row is append-only, so the same step must be identical in every field."""
    return left == right


def _evidence_row(kind, line=None, **fields):
    row = {'kind': kind, **fields}
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
    known, evidence = {}, []
    for line in backup_lines:
        if line.row is None or check_cost_row(line.row) is not None:
            continue
        key = (line.row['id'], line.row['type'])
        if key in known and not _same(known[key], line.row):
            evidence.append(_evidence_row('backup-duplicate-conflict', line, id=key[0], type=key[1],
                                          detail='the backup ledger repeats this step with different content'))
        known[key] = line.row
    backup_ids = {key[0] for key in known}
    merged = [_terminated(backup_raw)]
    new_ids, completed, seen = set(), 0, {}
    for line in read_lines(current_raw):
        problem = line.problem if line.problem != 'no-newline' else None
        if problem is None and line.row is not None:
            problem = check_cost_row(line.row)
        if problem:
            evidence.append(_evidence_row('unreadable-current-line', line, problem=problem))
            continue
        row = line.row
        key = (row['id'], row['type'])
        if key in seen:
            if not _same(seen[key], row):
                evidence.append(_evidence_row('conflict', line, id=row['id'], type=row['type'],
                                              detail='current ledger repeats this step with different content'))
            continue
        seen[key] = row
        if key in known:
            if not _same(known[key], row):
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


def _evidence_key(row):
    return json.dumps({k: v for k, v in row.items() if k != 'recorded'}, sort_keys=True, ensure_ascii=False)


def append_evidence(data_dir, rows, name=UNVERIFIED_EVIDENCE):
    """Append evidence rows not already recorded (one JSON object per line) and fsync.

    Returns how many rows were new, so re-running a reconciliation never duplicates evidence."""
    path = os.path.join(str(data_dir), name)
    existing, tail_open = set(), False
    try:
        with open(path, 'rb') as handle:
            content = handle.read()
            tail_open = bool(content) and not content.endswith((b'\n', b'\r'))
            for raw in content.splitlines():
                try:
                    existing.add(_evidence_key(json.loads(raw.decode('utf-8'))))
                except (ValueError, UnicodeDecodeError, AttributeError):
                    continue
    except FileNotFoundError:
        pass
    fresh = []
    for row in rows:
        key = _evidence_key(row)
        if key not in existing:
            existing.add(key)
            fresh.append(row)
    if fresh:
        payload = b''.join((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8') for row in fresh)
        if tail_open:
            payload = b'\n' + payload  # never glue a new row onto an unterminated last line
        with open(path, 'ab', buffering=0) as handle:
            handle.write(payload)
            os.fsync(handle.fileno())
    return len(fresh)


def mark_unverified(data_dir, kind, count, evidence_file, **details):
    """Block paid requests until a person has checked these items against the provider bills.

    An unreadable existing marker is never overwritten: it keeps blocking and must be
    recovered explicitly."""
    previous = read_marker(data_dir) or {}
    if previous.get('unreadable'):
        return
    items = [item for item in previous.get('items', []) if isinstance(item, dict)]
    items.append({'kind': kind, 'count': count, 'evidence': evidence_file,
                  'recorded': datetime.now(timezone.utc).isoformat(), **details})
    _write_json_atomic(marker_path(data_dir), {
        'status': 'unverified-costs',
        'created': previous.get('created') or datetime.now(timezone.utc).isoformat(),
        'items': items,
    })


def _carry_evidence_file(source_data, target_data, name):
    """Copy an evidence file across byte for byte; lines the target lacks are appended verbatim."""
    if not isinstance(name, str) or not name or name != os.path.basename(name):
        return
    source = os.path.join(str(source_data), name)
    target = os.path.join(str(target_data), name)
    if os.path.islink(source):
        raise ValueError('the evidence file in the source is a symbolic link')
    if not os.path.isfile(source):
        return
    if os.path.islink(target):
        raise ValueError('the evidence file in the target is a symbolic link')
    with open(source, 'rb') as handle:
        source_lines = [line for line in handle.read().splitlines(keepends=True) if line.strip()]
    existing, existing_raw = set(), b''
    if os.path.exists(target):
        with open(target, 'rb') as handle:
            existing_raw = handle.read()
        existing = {line.rstrip(b'\r\n') for line in existing_raw.splitlines(keepends=True)}
    fresh = [_terminated(line) for line in source_lines if line.rstrip(b'\r\n') not in existing]
    if fresh:
        with open(target, 'ab', buffering=0) as handle:
            if existing_raw and not existing_raw.endswith((b'\n', b'\r')):
                handle.write(b'\n')  # never glue new evidence onto an unterminated last line
            handle.write(b''.join(fresh))
            os.fsync(handle.fileno())


def carry_unverified(source_data, target_data):
    """A restore must never lift the pause: keep an unverified state the old data already had."""
    source_marker = marker_path(source_data)
    if not os.path.lexists(source_marker):
        return False
    if os.path.islink(source_marker) or not os.path.isfile(source_marker):
        raise ValueError('the unverified-costs marker is not a regular file')
    # The standard evidence file always travels with the pause, whatever the marker says.
    _carry_evidence_file(source_data, target_data, UNVERIFIED_EVIDENCE)
    carried = read_marker(source_data)
    items = [item for item in (carried or {}).get('items', []) if isinstance(item, dict)]
    if carried and not carried.get('unreadable') and items:
        for item in items:
            _carry_evidence_file(source_data, target_data, item.get('evidence'))
            mark_unverified(target_data, item.get('kind', 'carried'), item.get('count', 0),
                            item.get('evidence'),
                            **{k: v for k, v in item.items() if k not in ('kind', 'count', 'evidence', 'recorded')})
    elif not os.path.lexists(marker_path(target_data)):
        with open(source_marker, 'rb') as src, open(marker_path(target_data), 'wb') as dst:
            dst.write(src.read())
            dst.flush()
            os.fsync(dst.fileno())
    return True


def archive_marker(data_dir):
    """Called once a person confirmed the costs. Keeps the marker file as a record."""
    source = marker_path(data_dir)
    target = os.path.join(str(data_dir), f'costs-unverified.verified-{_stamp()}.json')
    os.replace(source, target)
    return target
