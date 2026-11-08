"""Windows-only subprocess options for background Agent work."""
import os
import subprocess


def hidden_subprocess_kwargs(*, windows=None, module=subprocess):
    if (os.name == 'nt') if windows is None else windows:
        result = {'creationflags': getattr(module, 'CREATE_NO_WINDOW', 0x08000000)}
        if hasattr(module, 'STARTUPINFO'):
            startup = module.STARTUPINFO()
            startup.dwFlags |= module.STARTF_USESHOWWINDOW
            startup.wShowWindow = module.SW_HIDE
            result['startupinfo'] = startup
        return result
    return {}
