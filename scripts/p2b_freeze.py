"""P2-B: freeze/unfreeze Brain writes during a cutover window."""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import brain_authority


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('freeze', 'unfreeze', 'status'))
    parser.add_argument('--data-root', default='data')
    parser.add_argument('--role', default='peter')
    args = parser.parse_args(argv)
    store = SimpleNamespace(root=Path(args.data_root))
    if args.action == 'freeze':
        result = {'frozen': True, 'path': brain_authority.freeze(store, args.role)}
    elif args.action == 'unfreeze':
        result = {'frozen': False, 'path': brain_authority.unfreeze(store, args.role)}
    else:
        result = {'frozen': brain_authority.frozen(store, args.role)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
