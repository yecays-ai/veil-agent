"""Ticket 400: find long prose still written inline in model-facing modules.

The registry is only a real fix if new instructions cannot quietly reappear as string literals. This
collector lists every long English-ish literal in the modules that build model messages. The test in
tests/test_prompt_registry.py fails when a literal is not registered in prompts/inline_allowlist.txt,
so adding a new prompt forces a decision: move it to prompts/defaults (preferred) or record it here
with a reason. The allowlist may shrink, never grow without a reason.
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

MIN_CHARS = 120
MIN_SPACES = 12
MIN_ASCII_RATIO = 0.7
ALLOWLIST = Path(__file__).with_name('inline_allowlist.txt')


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, 'body', [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def collect(paths=None):
    rows = []
    for name in (paths or PROMPT_MODULES):
        path = ROOT / name
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError):
            continue
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in skip:
                continue
            text = node.value
            if len(text) < MIN_CHARS or text.count(' ') < MIN_SPACES:
                continue
            letters = sum(1 for char in text if char.isascii() and char.isalpha())
            if letters / len(text) < MIN_ASCII_RATIO:
                continue
            if text.count('.') < 2:   # word lists and single clauses are not prose
                continue
            digest = hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]
            rows.append({'file': name, 'line': getattr(node, 'lineno', 0), 'hash': digest,
                         'preview': re.sub(r'\s+', ' ', text)[:70]})
    return rows


def allowlist():
    try:
        lines = ALLOWLIST.read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}
    result = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        digest, _, note = line.partition(' ')
        result[digest] = note.strip()
    return result


def update_allowlist():
    """Rewrite the allowlist from the current tree. Run only after a deliberate decision."""
    lines = ['# Ticket 400: long English prose still written inline in model-facing modules.',
             '# The test fails when a literal is missing here, so a new prompt cannot slip in silently.',
             '# Format: <sha1-12> <file>:<line> <preview>']
    seen = set()
    for row in sorted(collect(), key=lambda item: (item['file'], item['line'])):
        if row['hash'] in seen:
            continue
        seen.add(row['hash'])
        lines.append(row['hash'] + ' ' + row['file'] + ':' + str(row['line']) + ' ' + row['preview'])
    ALLOWLIST.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return len(seen)


if __name__ == '__main__':
    import sys
    if '--update' in sys.argv[1:]:
        print('allowlist now has', update_allowlist(), 'entries')
    else:
        for row in collect():
            print(row['hash'], row['file'] + ':' + str(row['line']), row['preview'])
