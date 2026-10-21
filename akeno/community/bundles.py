"""Read-only offline inventory. Public packages contain no proprietary runtimes."""
from __future__ import annotations
import json
from pathlib import Path, PureWindowsPath
import sys
from akeno.community.runtime import regular
from akeno.security.downloads import sha256_file


def distribution_root():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]


class OfflineBundle:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else distribution_root() / 'runtimes'
        self.errors = []
        self.distribution = "public"
        self.entries = []
        manifest = self.root / 'manifest.json'
        if manifest.exists():
            regular(manifest)
            if manifest.stat().st_size > 1024 * 1024:
                raise ValueError('Offline manifest exceeds inventory limit.')
            data = json.loads(manifest.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('components'), list):
                raise ValueError('Unsupported offline manifest.')
            if len(data['components']) > 256:
                raise ValueError('Offline inventory has too many components.')
            self.distribution = data.get("distribution", "public")
            ids = set()
            for item in data['components']:
                if not isinstance(item, dict) or not item.get('id') or item['id'] in ids:
                    raise ValueError('Offline components need unique identities.')
                ids.add(item['id'])
                self.entries.append(item)

    def path(self, item):
        rel = item['path']
        if not isinstance(rel, str) or '\\' in rel or ':' in rel or Path(rel).is_absolute() or PureWindowsPath(rel).drive or '..' in Path(rel).parts:
            raise ValueError('Unsafe offline component path.')
        p = self.root / rel
        regular(p)
        if not p.resolve().is_relative_to(self.root.resolve()) or sha256_file(p) != item['sha256']:
            raise ValueError('Missing or changed offline component: ' + item['id'])
        return p

    def candidates(self, manager):
        records = []
        for item in self.entries:
            if item.get('kind') != 'neural_runtime':
                continue
            try:
                result = manager.inspect(str(self.path(item)), item.get('profile'))
                if result.sha256 != item['sha256'] or result.category.value == 'rejected':
                    raise ValueError(result.detail)
                records.append(dict(result.to_dict(), origin="bundle"))
            except (OSError, ValueError, KeyError) as exc:
                self.errors.append(str(exc))
        return records

    def tool(self, tool):
        for item in self.entries:
            if item.get('kind') == 'tool' and item.get('id') == tool.id and item.get('sha256') == tool.sha256:
                return self.path(item)
        return None

    def official_runtime(self, name):
        from akeno.backends import runtimes
        for item in self.entries:
            if item.get('kind') == 'official_runtime' and item.get('runtime_name') == name:
                p = self.path(item)
                result = runtimes.evaluate(str(p), name)
                if result.trust.value == 'verified':
                    return {'stored_path': str(p), 'sha256': result.sha256, 'version': result.version}
        return None

    def backend_archive(self, package):
        for item in self.entries:
            if item.get('kind') == 'backend_archive' and item.get('sha256') == package.sha256:
                return self.path(item)
        return None
