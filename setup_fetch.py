"""Compatibility entrypoint kept at repository root for legacy setup/update scripts."""
from veil.integrations import setup_fetch

if __name__ == '__main__':
    raise SystemExit(setup_fetch.main())
