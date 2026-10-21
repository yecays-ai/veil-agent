"""Install the unmodified, hash-verified Ombre source in a separate environment."""
import hashlib
import os
from pathlib import Path
import subprocess
import urllib.request
import venv
import zipfile

COMMIT = '115d831d979bf67efc7150f976e2cb34c333c935'
ARCHIVE_SHA256 = 'a4226e051e39c6bbe434b36951a9b24a74ff708577e2bd91a5502dd507737e43'
ROOT = Path(__file__).resolve().parent


def download(url, digest):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Dependency download hash mismatch')
    return data


def main():
    dep = ROOT / '.deps' / 'ombre'
    dep.mkdir(parents=True, exist_ok=True)
    marker = dep / 'installed'
    python = dep / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # Install is explicit and idempotent; runtime never downloads dependencies.
    if marker.exists() and marker.read_text() == COMMIT:
        try:
            check = subprocess.run([str(python), '-c', 'import jieba, rank_bm25, mcp'],
                                   capture_output=True, timeout=20)
            if check.returncode == 0 and (dep / 'source/src/server.py').is_file():
                print('Ombre already installed at the pinned commit.')
                return
        except (OSError, subprocess.SubprocessError):
            pass
    archive = dep / 'source.zip'
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        archive.write_bytes(download('https://codeload.github.com/P0luz/Ombre-Brain/zip/' + COMMIT,
                                     ARCHIVE_SHA256))
    source = dep / 'source'
    source.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if item.is_dir():
                continue
            relative = Path(*Path(item.filename).parts[1:])
            target = source / relative
            if not target.resolve().is_relative_to(source.resolve()):
                raise ValueError('Unsafe dependency archive path')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(item))
    environment = dep / 'venv'
    if not environment.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    req = source / 'requirements.lock.txt'
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-cache-dir', '-r', str(req)], check=True)
    subprocess.run([str(python), '-c', 'import jieba, rank_bm25, mcp'], check=True)
    marker.write_text(COMMIT)
    print('Pinned Ombre installed. No model requests were made.')


if __name__ == '__main__':
    main()
