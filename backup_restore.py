"""Compatibility entrypoint kept at repository root for legacy update/backup commands."""
import runpy

if __name__ == '__main__':
    runpy.run_module('veil.system.backup_restore', run_name='__main__')
