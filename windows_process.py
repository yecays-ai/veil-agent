"""Windows-only subprocess options for background Agent work."""
import os
import subprocess


def hidden_subprocess_kwargs(*, windows=None, module=subprocess):
    if (os.name == 'nt') if windows is None else windows:
        return {'creationflags': getattr(module, 'CREATE_NO_WINDOW', 0x08000000)}
    return {}
