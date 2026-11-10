"""Save Lan's GitHub token into her secrets folder (hidden input; the token is never printed or logged)."""
import getpass
import shutil
import sys
from pathlib import Path

from maintenance import TOKEN_FILE_NAME, TOKEN_PATTERN

ROOT = Path(__file__).resolve().parent


def main():
    target = ROOT.parent.parent / 'secrets' / TOKEN_FILE_NAME
    print('GitHub token setup for Peter')
    print('Target file:', target)
    print('gh (GitHub CLI):', shutil.which('gh') or 'NOT FOUND - run: winget install GitHub.cli')
    token = getpass.getpass('Paste the token (input is hidden), then press Enter: ').strip()
    if not TOKEN_PATTERN.fullmatch(token):
        print('That does not look like a GitHub token (it starts with github_pat_ or ghp_). Nothing was saved.')
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(token + '\n', encoding='utf-8')
    print('Saved. Restart Peter for it to take effect.')
    return 0


if __name__ == '__main__':
    try:
        code = main()
    except Exception as exc:  # the window must stay open so the error can be read
        print('Failed:', type(exc).__name__, exc)
        code = 1
    input('Press Enter to close.')
    sys.exit(code)
