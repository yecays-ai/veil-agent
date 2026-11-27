"""Compatibility entrypoint kept at repository root for saved Brain inspection commands."""
from veil.brain.storage import veil_brain

if __name__ == '__main__':
    raise SystemExit(veil_brain.main())
