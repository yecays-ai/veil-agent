"""Compatibility entrypoint kept at repository root for legacy setup/update scripts."""
import runpy

if __name__ == '__main__':
    runpy.run_module('veil.integrations.setup_fetch', run_name='__main__')
