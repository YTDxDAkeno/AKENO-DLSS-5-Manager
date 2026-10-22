"""Privacy-preserving nested exports; web provenance is never parsed as a drive path."""
import re

_URL = re.compile(r'https?://[^\s<>"\']+', re.I)
_WINDOWS = re.compile(r'(?<![\w])[A-Za-z]:[\\/][^\r\n\t"<>|]*')
_UNC = re.compile(r'\\\\[^\s\\]+\\[^\r\n\t"<>|]*')
_UNIX = re.compile(r'(?<![\w:/])/(?:[^/\s]+/)+[^\r\n\t"<>|]*')
_PATH_KEYS = {'path', 'paths', 'directory', 'directories', 'target', 'executable', 'exe_candidates', 'root'}


def redact(value, include_paths=False):
    if include_paths:
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()
                if not (str(k).lower() in _PATH_KEYS or str(k).lower().endswith(('_path', '_paths', '_dir')))}
    if isinstance(value, (list, tuple)):
        return [redact(x) for x in value]
    if isinstance(value, str):
        def local(text):
            for pattern in (_WINDOWS, _UNC, _UNIX):
                text = pattern.sub('[local path omitted]', text)
            return text
        pieces, cursor = [], 0
        for match in _URL.finditer(value):
            pieces.extend((local(value[cursor:match.start()]), match.group()))
            cursor = match.end()
        pieces.append(local(value[cursor:]))
        return ''.join(pieces)
    return value
