"""Compatibility entrypoint kept at repository root for saved repair commands."""
from veil.system import data_repair

if __name__ == '__main__':
    raise SystemExit(data_repair.main())
