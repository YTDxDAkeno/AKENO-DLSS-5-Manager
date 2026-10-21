"""Shared reports omit local file locations unless explicitly requested."""
import re


def redact(value, include_paths=False):
    if include_paths:
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()
                if not any(word in k.lower() for word in ('path', 'directory', 'target', 'executable', 'install_dir'))}
    if isinstance(value, (list, tuple)):
        return [redact(x) for x in value]
    if isinstance(value, str):
        value = re.sub(r'[A-Za-z]:[\\/][^\r\n\t\"<>|]*', '[local path omitted]', value)
        return re.sub(r'(?<![\w:/])/(?:home|Users|workspace|tmp|mnt|media)/[^\r\n\t\"<>|]*', '[local path omitted]', value)
    return value
