"""Compatibility entrypoint kept at repository root for legacy CLI commands."""
from veil.core import veil_agent

if __name__ == '__main__':
    raise SystemExit(veil_agent.main())
