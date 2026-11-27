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
    'calendar_agreements.py',
    'delegate_agent.py',
    'veil/brain/experience_memory.py',
    'veil/brain/learned_context.py',
    'life_state.py',
    'veil/brain/memory_agent.py',
    'veil/brain/memory_native.py',
    'veil/brain/recall/recall_budget.py',   # ticket 419: builds one request-shaped message to measure its tokens
    'veil/brain/recall/recall_judge.py',
    'veil/brain/short_term_notes.py',
    'user_persona.py',
    'voice_style.py',
    'veil/tools/executor_opencode.py',
    'veil/windows/windows_observer.py',
)

MIN_CHARS = 100
MIN_SPACES = 8
MIN_ASCII_RATIO = 0.7
MIN_CJK = 40
CJK = re.compile('[\u3400-\u9fff]')
SENTENCE = re.compile('[。！？]')
FEEDBACK = Path(__file__).with_name('inline_feedback_allowlist.txt')
REASONS = ('feedback:', 'tool-param:', 'git:', 'record:', 'ui-error:')
BASELINE = 45


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


def _imports_prompts(path):
    """A root module that imports prompts is building model text by definition."""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == 'prompts' or alias.name.startswith('prompts.')
                   for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if module == 'prompts' or module.startswith('prompts.'):
                return True
    return False


def builds_requests(path):
    """Does this module build a message that goes to a model?

    Ticket 405 review: importing prompts is one way in, not the only one. A new module can
    construct the request by hand and never touch the registry, so the request shape itself is
    also checked: role/content message dicts, a messages list, or a tool schema call.
    """
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == 'role':
                    return True
        elif isinstance(node, ast.keyword) and node.arg == 'messages':
            return True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('messages', 'MESSAGES'):
                    return True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == 'tool_schemas':
            return True
    return False


def iter_source_files():
    """Yield repository-relative source files across root and veil/ (excluding veil/prompts/)."""
    files = list(ROOT.glob('*.py'))
    veil_dir = ROOT / 'veil'
    if veil_dir.is_dir():
        for path in sorted(veil_dir.rglob('*.py')):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith('veil/prompts/') or path.name == '__init__.py':
                continue
            files.append(path)
    return files


def modules():
    """The fixed prompt modules plus every module that imports prompts.

    Ticket 405 review: a hand-maintained list is how life_state.py escaped scanning for three
    tickets. Anything that imports prompts is building model text, so it is scanned without
    anyone remembering to add it here.
    """
    found = set(PROMPT_MODULES)
    for path in iter_source_files():
        if _imports_prompts(path):
            found.add(path.relative_to(ROOT).as_posix())
    return sorted(found)


def collect(paths=None):
    rows = []
    for name in (paths or modules()):
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
