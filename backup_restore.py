"""Compatibility entrypoint kept at repository root for legacy update/backup commands."""
from veil.system import backup_restore

if __name__ == '__main__':
    raise SystemExit(backup_restore.main())
