"""Compatibility entrypoint kept at repository root for legacy CLI commands."""
import runpy

if __name__ == '__main__':
    runpy.run_module('veil.core.veil_agent', run_name='__main__')
