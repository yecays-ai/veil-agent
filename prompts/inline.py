"""Tickets 402/404: keep prompt prose out of the model-facing modules.

Two things reach the model: preset text (registered in prompts/registry.py or described per tool in
prompts/tools.py) and runtime feedback. Tool descriptions are recognised structurally and exempted.
This collector finds long prose still written as a bare literal, in English or (long) Chinese, so a
new prompt cannot quietly reappear in code. The reviewed feedback list is typed and may only shrink.
"""
import ast
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROMPT_MODULES = (
    'veil_agent.py',
    'autonomy.py',
    'background.py',
    'delegate_agent.py',
    'experience_memory.py',
    'learned_context.py',
    'memory_agent.py',
    'memory_native.py',
    'recall_judge.py',
    'short_term_notes.py',
    'user_persona.py',
    'voice_style.py',
)

MIN_CHARS = 100
MIN_SPACES = 8
MIN_ASCII_RATIO = 0.7
MIN_CJK = 40
CJK = re.compile('[\u3400-\u9fff]')
SENTENCE = re.compile('[。！？]')
FEEDBACK = Path(__file__).with_name('inline_feedback_allowlist.txt')
REASONS = ('feedback:', 'tool-param:', 'git:', 'record:', 'ui-error:')
BASELINE = 43


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, 'body', [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _marked(tree):
    """Tool descriptions and per-tool description tables are managed by prompts/tools.py."""
    marked = set()

    def mark(node):
        for item in ast.walk(node):
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                marked.add(id(item))

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == 'description':
            mark(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == 'description':
                    mark(value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and 'DESCRIPTION' in target.id.upper():
                    mark(node.value)
    return marked


def _is_prose(text):
    spaces = text.count(' ')
    if len(text) >= MIN_CHARS and spaces >= MIN_SPACES:
        letters = sum(1 for char in text if char.isascii() and char.isalpha())
        if len(text) and letters / len(text) >= MIN_ASCII_RATIO and text.count('.') >= 2:
            return True
    return len(CJK.findall(text)) >= MIN_CJK and bool(SENTENCE.search(text))


def _rows_from_tree(tree, name):
    rows = []
    skip = _docstrings(tree) | _marked(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        text = node.value
        if not _is_prose(text):
            continue
        digest = hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]
        rows.append({'file': name, 'line': getattr(node, 'lineno', 0), 'hash': digest,
                     'preview': re.sub(r'\s+', ' ', text)[:80], 'text': text})
    return rows


def collect(paths=None):
    rows = []
    for name in (paths or PROMPT_MODULES):
        path = ROOT / name
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError):
            continue
        rows.extend(_rows_from_tree(tree, name))
    return rows


def collect_text(source, name='inline-test.py'):
    return _rows_from_tree(ast.parse(source), name)


def feedback():
    try:
        lines = FEEDBACK.read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}
    found = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        digest, _, note = line.partition(' ')
        found[digest] = note.strip()
    return found


def update_feedback():
    lines = ['# Ticket 402/404: runtime feedback, tool-parameter text and user-facing errors still inline.',
             '# NOT preset prompts: these are short program messages. New prompt prose belongs in',
             '# prompts/defaults (call prompts.text) or in a tool description (managed by prompts/tools.py).',
             '# Format: <sha1-12> <reason:...> <file>:<line> <preview>']
    seen = set()
    for row in sorted(collect(), key=lambda item: (item['file'], item['line'])):
        if row['hash'] in seen:
            continue
        seen.add(row['hash'])
        lines.append(row['hash'] + ' feedback:runtime ' + row['file'] + ':' + str(row['line']) + ' ' + row['preview'])
    FEEDBACK.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return len(seen)


if __name__ == '__main__':
    import sys
    if '--update' in sys.argv[1:]:
        print('feedback list now has', update_feedback(), 'entries')
    else:
        for row in collect():
            print(row['hash'], row['file'] + ':' + str(row['line']), row['preview'])
