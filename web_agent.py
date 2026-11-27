"""Compatibility entrypoint kept at repository root for legacy desktop builds."""
import runpy

if __name__ == '__main__':
    runpy.run_module('veil.server.web_agent', run_name='__main__')
