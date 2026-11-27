"""Validation shared by Lab attachments, chat rendering, and email delivery."""
import re
import stat


MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
ATTACHMENT_TYPES = {
    '.html': ('text/html', 'html'),
    '.htm': ('text/html', 'html'),
    '.png': ('image/png', 'image'),
    '.jpg': ('image/jpeg', 'image'),
    '.jpeg': ('image/jpeg', 'image'),
    '.gif': ('image/gif', 'image'),
    '.webp': ('image/webp', 'image'),
    '.txt': ('text/plain', 'text'),
    '.md': ('text/plain', 'text'),
    '.markdown': ('text/plain', 'text'),
    '.json': ('text/plain', 'text'),
    '.csv': ('text/plain', 'text'),
    '.tsv': ('text/plain', 'text'),
    '.log': ('text/plain', 'text'),
    '.xml': ('text/plain', 'text'),
    '.yaml': ('text/plain', 'text'),
    '.yml': ('text/plain', 'text'),
    '.toml': ('text/plain', 'text'),
    '.ini': ('text/plain', 'text'),
    '.css': ('text/plain', 'text'),
    '.js': ('text/plain', 'text'),
    '.ts': ('text/plain', 'text'),
    '.jsx': ('text/plain', 'text'),
    '.tsx': ('text/plain', 'text'),
    '.py': ('text/plain', 'text'),
    '.sh': ('text/plain', 'text'),
    '.sql': ('text/plain', 'text'),
    '.bat': ('text/plain', 'text'),
    '.cmd': ('text/plain', 'text'),
    '.ps1': ('text/plain', 'text'),
    '.svg': ('text/plain', 'text'),
}
ATTACHMENT_EXTENSIONS = frozenset(ATTACHMENT_TYPES)
MEDIA_ID_RE = re.compile(r'^[0-9a-f]{32}$')


def redirected_path(path):
    """Detect symlinks and Windows reparse points without following the path."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def attachment_name(value):
    """Return a short display name without path components or header controls."""
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('invalid attachment name')
    name = value.replace('\\', '/').rsplit('/', 1)[-1]
    if (not name or name in ('.', '..') or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or '/' in name or '\\' in name):
        raise ValueError('invalid attachment name')
    suffix = name.rpartition('.')[2]
    suffix = ('.' + suffix.lower()) if suffix else ''
    if suffix not in ATTACHMENT_TYPES:
        raise ValueError('unsupported attachment type')
    if len(name) > 120:
        stem = name[:-len(suffix)] if suffix else name
        name = stem[:120 - len(suffix)] + suffix
    return name, suffix


def inspect_attachment(name, raw):
    """Validate the formats ticket 101 can safely preview, and return metadata."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_ATTACHMENT_BYTES:
        raise ValueError('attachment must be between 1 byte and 5 MB')
    safe_name, suffix = attachment_name(name)
    mime, view = ATTACHMENT_TYPES[suffix]
    signatures = {
        '.png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
        '.jpg': raw.startswith(b'\xff\xd8\xff'),
        '.jpeg': raw.startswith(b'\xff\xd8\xff'),
        '.gif': raw.startswith((b'GIF87a', b'GIF89a')),
        '.webp': len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP',
    }
    if suffix in signatures and not signatures[suffix]:
        raise ValueError('image content does not match its file extension')
    if view in ('text', 'html'):
        try:
            raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('text attachments must be UTF-8') from exc
    return {'name': safe_name, 'suffix': suffix, 'mime': mime, 'view': view, 'size': len(raw)}


def valid_attachment_row(row):
    if not isinstance(row, dict) or row.get('kind') != 'attachment':
        return False
    media_id, filename, name = row.get('id'), row.get('filename'), row.get('name')
    if not isinstance(media_id, str) or not MEDIA_ID_RE.fullmatch(media_id):
        return False
    try:
        safe_name, suffix = attachment_name(name)
    except ValueError:
        return False
    mime, view = ATTACHMENT_TYPES[suffix]
    return (filename == media_id + suffix and row.get('mime') == mime and row.get('view') == view
            and safe_name == name and type(row.get('size')) is int
            and 0 < row['size'] <= MAX_ATTACHMENT_BYTES
            and isinstance(row.get('request_id'), str))
