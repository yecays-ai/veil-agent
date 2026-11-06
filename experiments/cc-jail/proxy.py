"""Tiny allow-list CONNECT proxy: the only way out of the isolated sandbox network."""
import asyncio, datetime
ALLOW = {'github.com', 'codeload.github.com', 'raw.githubusercontent.com', 'objects.githubusercontent.com',
         'api.github.com', 'pypi.org', 'files.pythonhosted.org', 'registry.npmjs.org'}


def log(m):
    print(datetime.datetime.utcnow().isoformat(timespec='seconds'), m, flush=True)


async def pipe(r, w):
    try:
        while True:
            d = await r.read(65536)
            if not d:
                break
            w.write(d)
            await w.drain()
    except Exception:
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def handle(r, w):
    try:
        head = await asyncio.wait_for(r.readuntil(b'\r\n\r\n'), 15)
        method, target, _ = head.split(b'\r\n')[0].decode().split(' ', 2)
        host, _, port = target.partition(':')
        if method != 'CONNECT' or host.lower() not in ALLOW or port not in ('443', ''):
            log(f'DENY {method} {target}')
            w.write(b'HTTP/1.1 403 Forbidden\r\n\r\n')
            await w.drain()
            w.close()
            return
        ur, uw = await asyncio.open_connection(host, 443)
        log(f'ALLOW {target}')
        w.write(b'HTTP/1.1 200 OK\r\n\r\n')
        await w.drain()
        await asyncio.gather(pipe(r, uw), pipe(ur, w))
    except Exception as e:
        log(f'ERR {e!r}')
        try:
            w.close()
        except Exception:
            pass


async def main():
    s = await asyncio.start_server(handle, '0.0.0.0', 3128)
    async with s:
        await s.serve_forever()

asyncio.run(main())
