"""Ticket 187: relevance check for context-assisted recall.

Ombre's search returns memory ids without a relevance score, and Ombre must not be changed.
For a short message that only makes sense with the last rounds ("try again", "这个呢"), VEIL
asks Ombre with the recent rounds added, then keeps only the candidates whose stored bge-m3
vector is close enough to that same text. Stored vectors are read (never written) from Ombre's
own embeddings.db; only the one query text is embedded with the same provider and model.
"""
import array
import json
import math
import sqlite3
from pathlib import Path


def decode_vector(raw):
    """Ombre stores either float32 bytes or a JSON list; anything else is not a vector."""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        data = bytes(raw)
        if not data or len(data) % 4:
            raise ValueError('bad embedding blob')
        values = array.array('f')
        values.frombytes(data)
        if array.array('H', [1]).tobytes() != b'\x01\x00':  # big-endian host
            values.byteswap()
        return list(values)
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if (isinstance(parsed, list) and parsed
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in parsed)):
            return [float(x) for x in parsed]
    raise ValueError('bad embedding')


def cosine(a, b):
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def load_vectors(db_path, ids):
    """Read the stored content vectors of the given memories, read-only. Missing ones are left out."""
    ids = [i for i in dict.fromkeys(ids) if isinstance(i, str) and i]
    found = {}
    if not ids or not Path(db_path).is_file():
        return found
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        for start in range(0, len(ids), 50):
            chunk = ids[start:start + 50]
            marks = ','.join('?' * len(chunk))
            for bucket_id, raw in conn.execute(
                    f'SELECT bucket_id, embedding FROM embeddings WHERE bucket_id IN ({marks})', chunk):
                try:
                    found[bucket_id] = decode_vector(raw)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
    finally:
        conn.close()
    return found


class QueryEmbedder:
    """One small embeddings call with the memory provider's own key; the text is not stored."""

    def __init__(self, base_url, model, api_key, timeout=20):
        self.base_url, self.model, self.api_key, self.timeout = base_url, model, api_key, timeout
        self._client = None

    def embed(self, text):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout,
                                  max_retries=0)
        response = self._client.embeddings.create(model=self.model, input=text[:4000])
        vector = list(response.data[0].embedding)
        if not vector:
            raise ValueError('empty embedding')
        return vector

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass


def context_text(recent, message, rounds=2, clip=200):
    """The last `rounds` rounds of user/assistant text (assistant side clipped) plus the message."""
    rows = [(m.get('role'), m.get('content')) for m in recent or []
            if m.get('role') in ('user', 'assistant') and isinstance(m.get('content'), str)
            and m.get('content').strip()]
    kept, users = [], 0
    for role, text in reversed(rows):
        kept.append((role, text.strip()[:clip] if role == 'assistant' else text.strip()[:400]))
        if role == 'user':
            users += 1
            if users >= rounds:
                break
    kept.reverse()
    return '\n'.join([t for _, t in kept] + [message])
