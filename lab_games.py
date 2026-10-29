"""Character-scoped Lab game pages and bounded, durable score receipts."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from lab_preview import _safe_lab_file


MAX_SCORE_BODY = 2048
MAX_SCORE_FILE = 2 * 1024 * 1024
MAX_GAME_RESOURCE = 5 * 1024 * 1024
ID_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


def _plain(path):
    info = path.lstat()
    return not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & REPARSE)


class LabGames:
    def __init__(self, lab, port):
        self.lab = Path(lab).resolve(strict=True)
        self.port = port
        self.entries = {}
        self.lock = threading.RLock()

    def open(self, relative):
        """Issue a session capability for one ordinary HTML entry inside one Lab game folder."""
        if not isinstance(relative, str) or not relative.startswith(self.lab.name + '/'):
            raise ValueError('Give a Lab-relative HTML path.')
        subpath = relative[len(self.lab.name) + 1:]
        entry = _safe_lab_file(self.lab, subpath, page=True)
        folder = entry.parent
        if folder == self.lab or not _plain(folder):
            raise ValueError('Put each game in its own plain Lab folder.')
        score_file = folder / 'scores.jsonl'
        if score_file.exists() and (not _plain(score_file) or not score_file.is_file()):
            raise ValueError('The score file is unavailable.')
        capability = secrets.token_urlsafe(24)
        with self.lock:
            self.entries[capability] = (folder, entry, subpath)
            if len(self.entries) > 64:
                self.entries.pop(next(iter(self.entries)))
        return f'http://127.0.0.1:{self.port}/game/{capability}/{quote(entry.name, safe="")}'

    def _entry(self, capability):
        with self.lock:
            item = self.entries.get(capability)
        if item is None:
            raise ValueError('The game link has expired; ask Peter to open it again.')
        return item

    def resource(self, capability, relative):
        folder, entry, _ = self._entry(capability)
        if relative == entry.name:
            path = _safe_lab_file(self.lab, entry.relative_to(self.lab).as_posix(), page=True)
        else:
            path = _safe_lab_file(self.lab, (folder.relative_to(self.lab) / relative).as_posix())
            if not path.is_relative_to(folder):
                raise ValueError('Game resources must stay in the game folder.')
        if path.stat().st_size > MAX_GAME_RESOURCE:
            raise ValueError('Game resource is too large.')
        with path.open('rb') as source:
            body = source.read(MAX_GAME_RESOURCE + 1)
        if len(body) > MAX_GAME_RESOURCE:
            raise ValueError('Game resource is too large.')
        if path == entry:
            helper = ("<script src='/game/" + capability + "/veil-score.js'></script>").encode()
            if b'</head>' in body.lower():
                at = body.lower().find(b'</head>')
                body = body[:at] + helper + body[at:]
            else:
                at = body.lower().find(b'</body>')
                body = body[:at] + helper + body[at:] if at >= 0 else body + helper
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        return body, mime

    def save(self, capability, raw):
        folder, _entry, game_id = self._entry(capability)
        if len(raw) > MAX_SCORE_BODY:
            raise ValueError('Score is too large.')
        try:
            row = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise ValueError('Invalid score JSON.') from exc
        expected = {'game_id', 'round_id', 'score', 'duration_ms', 'ending', 'ended_at'}
        if not isinstance(row, dict) or set(row) != expected or row.get('game_id') != game_id:
            raise ValueError('Score fields or game ID are invalid.')
        if not isinstance(row.get('round_id'), str) or not ID_RE.fullmatch(row['round_id']):
            raise ValueError('Invalid round ID.')
        if (type(row.get('score')) not in (int, float) or isinstance(row['score'], bool)
                or not -1_000_000_000 <= row['score'] <= 1_000_000_000):
            raise ValueError('Invalid score.')
        if type(row.get('duration_ms')) is not int or not 0 <= row['duration_ms'] <= 86_400_000:
            raise ValueError('Invalid duration.')
        if (not isinstance(row.get('ending'), str) or not row['ending'].strip()
                or len(row['ending']) > 160 or any(ord(c) < 32 for c in row['ending'])):
            raise ValueError('Invalid ending.')
        if not isinstance(row.get('ended_at'), str) or len(row['ended_at']) > 40:
            raise ValueError('Invalid ending time.')
        try:
            ended = datetime.fromisoformat(row['ended_at'].replace('Z', '+00:00'))
            if ended.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise ValueError('Invalid ending time.') from exc
        record = {**row, 'saved_at': datetime.now(timezone.utc).isoformat()}
        score_file = folder / 'scores.jsonl'
        with self.lock:
            if not _plain(folder) or not folder.is_dir():
                raise OSError('Game folder changed.')
            if score_file.exists():
                if not _plain(score_file) or not score_file.is_file() or score_file.stat().st_nlink != 1:
                    raise OSError('Score file changed.')
                if score_file.stat().st_size > MAX_SCORE_FILE:
                    raise OSError('Score file is full.')
                before = score_file.stat()
                with score_file.open('rb') as source:
                    existing = source.read(MAX_SCORE_FILE + 1)
                for line in existing.splitlines():
                    previous = json.loads(line)
                    if not isinstance(previous, dict):
                        raise ValueError('The existing score file is invalid.')
                    if previous.get('game_id') == game_id and previous.get('round_id') == row['round_id']:
                        return {'ok': True, 'duplicate': True}
            encoded = (json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            if (score_file.stat().st_size if score_file.exists() else 0) + len(encoded) > MAX_SCORE_FILE:
                raise OSError('Score file is full.')
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0)
            if score_file.exists():
                flags = os.O_WRONLY | os.O_APPEND | getattr(os, 'O_BINARY', 0)
            else:
                before = None
                flags |= os.O_EXCL
            fd = os.open(score_file, flags, 0o600)
            try:
                opened = os.fstat(fd)
                if (opened.st_nlink != 1 or not _plain(score_file)
                        or (before is not None and
                            (before.st_ino, before.st_size, before.st_mtime_ns) !=
                            (opened.st_ino, opened.st_size, opened.st_mtime_ns))):
                    raise OSError('Score file redirected.')
                if os.name == 'nt':
                    from desktop_files import _final_path_for_fd
                    final = _final_path_for_fd(fd)
                    if final is None or os.path.normcase(str(final)) != os.path.normcase(str(score_file)):
                        raise OSError('Score file redirected.')
                start_size = opened.st_size
                try:
                    if os.write(fd, encoded) != len(encoded):
                        raise OSError('Score write incomplete.')
                    os.fsync(fd)
                except OSError:
                    os.ftruncate(fd, start_size)
                    os.fsync(fd)
                    raise
            finally:
                os.close(fd)
        return {'ok': True, 'duplicate': False}


def score_helper(capability, game_id):
    """A visible receipt is shown only after the server confirms durable storage."""
    return ("""(() => {
  const endpoint = '/game/CAPABILITY/score';
  const gameId = GAME_ID;
  let badge;
  function status(message, ok) {
    if (!badge) {
      badge = document.createElement('div');
      badge.setAttribute('role', 'status');
      badge.style.cssText = 'position:fixed;bottom:12px;right:12px;padding:8px 12px;background:#161a20;color:white;z-index:2147483647;border-radius:6px;font:14px sans-serif';
      document.body.appendChild(badge);
    }
    badge.textContent = message;
    badge.style.background = ok ? '#176c45' : '#a32632';
  }
  window.veilGame = Object.freeze({
    gameId,
    async saveResult({ round_id, score, duration_ms, ending, ended_at } = {}) {
      status('正在保存成绩…', false);
      try {
        const response = await fetch(endpoint, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ game_id: gameId, round_id, score, duration_ms, ending, ended_at })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || '保存失败');
        status(result.duplicate ? '本局成绩已保存' : '成绩已保存', true);
        return result;
      } catch (error) {
        status('成绩保存失败，请重试：' + error.message, false);
        throw error;
      }
    }
  });
})();""".replace('CAPABILITY', capability).replace('GAME_ID', json.dumps(game_id)))
