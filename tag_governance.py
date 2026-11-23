"""P2-C: audited tag cleanup and a controlled category field.

Free tags stop being a UI/retrieval input. The original tags are kept in a migration table so the
change is reversible; memory body, IDs, sources, events and versions are never touched.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

CATEGORIES = ('relationship', 'experience', 'preference', 'commitment', 'work', 'emotion', 'daily',
              'unclassified')
DOMAIN_MAP = {
    '恋爱': 'relationship', '人际': 'relationship', '社交': 'relationship', '人物': 'relationship',
    '事件': 'experience', '回忆': 'experience', '出行': 'experience',
    '兴趣': 'preference', '饮食': 'preference', '游戏': 'preference',
    '计划': 'commitment', 'plan': 'commitment',
    '工作': 'work', '编程': 'work', 'AI': 'work', '学习': 'work', '技术': 'work',
    '情绪': 'emotion', '心理': 'emotion', '自省': 'emotion', '内心': 'emotion',
    '日常': 'daily', '居家': 'daily', '健康': 'daily', '睡眠': 'daily', '财务': 'daily',
    '数字': 'daily', '未分类': 'unclassified',
}
PROGRAM_TAGS = {'待删除', '惊喜', 'surprise'}


def ensure_schema(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(memories)')}
    if 'category' not in columns:
        connection.execute("ALTER TABLE memories ADD COLUMN category TEXT NOT NULL DEFAULT 'unclassified'")
    connection.execute(
        'CREATE TABLE IF NOT EXISTS tag_migration(seq INTEGER PRIMARY KEY, memory_id TEXT,'
        'old_tags_json TEXT, old_domain_json TEXT, category TEXT, time TEXT)')
    connection.commit()


def _json(value):
    try:
        return json.loads(value) if isinstance(value, str) else (value or [])
    except ValueError:
        return []


def classify(tags, domains):
    mapped = {DOMAIN_MAP[value] for value in domains if value in DOMAIN_MAP}
    if len(mapped) == 1:
        return mapped.pop()
    if not mapped:
        return 'unclassified'
    # Several real domains conflict: do not guess.
    return 'unclassified'


def audit(connection):
    rows = connection.execute('SELECT tags_json,domain_json FROM memories').fetchall()
    tags = []
    for tag_json, _ in rows:
        tags.extend(_json(tag_json))
    return {'memories': len(rows), 'tag_uses': len(tags), 'distinct_tags': len(set(tags)),
            'singletons': sum(1 for value in set(tags) if tags.count(value) == 1)}


def apply(connection, dry_run=True):
    ensure_schema(connection)
    rows = connection.execute('SELECT id,tags_json,domain_json FROM memories ORDER BY id').fetchall()
    changes = []
    for memory_id, tags_json, domain_json in rows:
        tags = _json(tags_json)
        domains = _json(domain_json)
        category = classify(tags, domains)
        changes.append((memory_id, tags, domains, category))
        if not dry_run:
            connection.execute(
                'INSERT INTO tag_migration(memory_id,old_tags_json,old_domain_json,category,time) '
                'VALUES(?,?,?,?,?)', (memory_id, json.dumps(tags, ensure_ascii=False),
                                      json.dumps(domains, ensure_ascii=False), category,
                                      time.strftime('%Y-%m-%dT%H:%M:%S%z')))
            connection.execute("UPDATE memories SET tags_json='[]',category=? WHERE id=?",
                               (category, memory_id))
    if not dry_run:
        connection.commit()
    counts = {}
    for _, _, _, category in changes:
        counts[category] = counts.get(category, 0) + 1
    return {'dry_run': dry_run, 'memories': len(changes), 'categories': counts}


def restore_tags(connection):
    ensure_schema(connection)
    rows = connection.execute('SELECT memory_id,old_tags_json FROM tag_migration').fetchall()
    for memory_id, tags in rows:
        connection.execute('UPDATE memories SET tags_json=? WHERE id=?', (tags, memory_id))
    connection.commit()
    return {'restored': len(rows)}


def verify(connection):
    total, nonempty = connection.execute(
        "SELECT COUNT(*), SUM(CASE WHEN tags_json NOT IN ('[]','') THEN 1 ELSE 0 END) FROM memories"
    ).fetchone()
    categories = dict(connection.execute('SELECT category,COUNT(*) FROM memories GROUP BY category'))
    return {'memories': total, 'with_tags': nonempty or 0,
            'categories': categories, 'ok': (nonempty or 0) == 0 and set(categories) <= set(CATEGORIES)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('audit', 'apply', 'verify', 'restore'))
    parser.add_argument('--db', required=True)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    connection = sqlite3.connect(args.db)
    try:
        if args.action == 'audit':
            print(json.dumps(audit(connection), ensure_ascii=False))
        elif args.action == 'apply':
            print(json.dumps(apply(connection, dry_run=not args.write), ensure_ascii=False))
        elif args.action == 'verify':
            print(json.dumps(verify(connection), ensure_ascii=False))
        else:
            print(json.dumps(restore_tags(connection), ensure_ascii=False))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
