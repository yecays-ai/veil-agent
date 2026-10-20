"""Run the pinned Ombre server with VEIL-specific HTTP safety guards."""
import runpy
import json
import os
import sys
import threading
import time
from pathlib import Path


class RestrictedHTTPApp:
    """Keep the dashboard, while disabling remote update and tunnel controls."""
    BLOCKED = {
        ('POST', '/api/do-update'), ('POST', '/api/update-settings'),
        ('POST', '/api/tunnel/start'), ('POST', '/api/tunnel/config'),
        ('POST', '/api/transport'), ('POST', '/api/config'),
        ('POST', '/api/github/config'), ('POST', '/api/github/validate'),
        ('POST', '/api/github/sync'), ('POST', '/api/github/import'),
        ('POST', '/api/embedding/local/install'), ('POST', '/api/embedding/local/start'),
    }

    def __init__(self, app):
        self.app = app

    @classmethod
    def is_blocked(cls, method, path, body=None):
        method, path = method.upper(), path.split('?', 1)[0]
        blocked_prefix = ('/api/tunnel/', '/api/github/', '/api/update-',
                          '/api/embedding/local/')
        if ((method, path) in cls.BLOCKED or
                (method == 'POST' and path.startswith(blocked_prefix))):
            return True
        if method == 'POST' and path == '/api/env-config' and body:
            try:
                updates = json.loads(body).get('updates', {})
                return isinstance(updates, dict) and any(
                    key != 'OMBRE_DASHBOARD_PASSWORD' for key in updates)
            except (ValueError, AttributeError):
                return False
        return False

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http':
            method = scope.get('method', '').upper()
            path = scope.get('path', '')
            body = None
            replay = receive
            if method == 'POST' and path == '/api/env-config':
                messages = []
                while True:
                    message = await receive()
                    messages.append(message)
                    if message.get('type') != 'http.request' or not message.get('more_body'):
                        break
                body = b''.join(message.get('body', b'') for message in messages)
                pending = list(messages)

                async def replay():
                    return pending.pop(0) if pending else {'type': 'http.disconnect'}

            if self.is_blocked(method, path, body):
                body = b'{"ok":false,"error":"disabled in VEIL"}'
                await send({'type': 'http.response.start', 'status': 404,
                            'headers': [(b'content-type', b'application/json'),
                                        (b'content-length', str(len(body)).encode())]})
                await send({'type': 'http.response.body', 'body': body})
                return
        await self.app(scope, replay if scope.get('type') == 'http' else receive, send)


def main():
    server_path = Path(sys.argv[1]).resolve()
    source_root = server_path.parent
    sys.path.insert(0, str(source_root))
    import uvicorn
    import server_app
    import web.tunnel as tunnel
    from web import ollama_local
    tunnel._load_tunnel_config = lambda: {'enabled': False, 'auto_start': False, 'token': ''}
    tunnel._start_tunnel = lambda *_args, **_kwargs: (False, 'disabled in VEIL')
    ollama_local.ensure_child_on_boot = lambda *_args, **_kwargs: None
    ollama_local.stop_child = lambda *_args, **_kwargs: None
    build = server_app.build_http_app

    def guarded(*args, **kwargs):
        return RestrictedHTTPApp(build(*args, **kwargs))

    server_app.build_http_app = guarded
    stop_path = Path(os.environ['VEIL_OMBRE_STOP_PATH'])

    def managed_run(app, **kwargs):
        server = uvicorn.Server(uvicorn.Config(app, **kwargs))

        def watch_stop():
            while not server.should_exit:
                if stop_path.exists():
                    server.should_exit = True
                    return
                time.sleep(0.1)

        threading.Thread(target=watch_stop, daemon=True).start()
        server.run()

    uvicorn.run = managed_run
    runpy.run_path(str(server_path), run_name='__main__')


if __name__ == '__main__':
    main()
