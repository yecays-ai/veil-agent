"""Compatibility entrypoint kept at repository root for legacy desktop builds."""
from veil.server import web_agent

if __name__ == '__main__':
    raise SystemExit(web_agent.main())
