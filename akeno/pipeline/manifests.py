"""Private local components: explicit provenance/permission, hash-addressed immutable copies."""
import json
import re
import shutil
from pathlib import Path, PureWindowsPath
from akeno.paths import cache_dir
from akeno.security.downloads import sha256_file
from akeno.community.runtime import regular
from akeno.games import pe
from .dependencies import ordered_components

ROLES={'reshade','motion_provider','shader','dlss_runtime','nr_runtime','api_bridge'}


def safe_relative(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or Path(value).is_absolute() or PureWindowsPath(value).drive or '..' in Path(value).parts:
        raise ValueError('A safe relative component path is required.')
    return value


class ComponentStore:
    def __init__(self,db):self.db=db

    def import_manifest(self,path,permitted=False):
        if permitted is not True:raise PermissionError('Explicit local-use permission is required.')
        manifest=Path(path);regular(manifest)
        if manifest.stat().st_size>1024*1024:raise ValueError('Component manifest is too large.')
        data=json.loads(manifest.read_text(encoding='utf-8-sig'))
        if data.get('schema')!=1 or data.get('distribution')!='private-local-use':raise ValueError('A private-local-use schema-1 component manifest is required.')
        entries=data.get('components')
        if not isinstance(entries,list) or not 1<=len(entries)<=64:raise ValueError('Invalid component inventory.')
        validated={}
        for item in entries:
            role=item.get('role')
            if role not in ROLES or role in validated:raise ValueError('Unknown or duplicate component role.')
            if not all(isinstance(item.get(k),str) and item[k].strip() for k in ('version','source','license')):raise ValueError('Version, source and license metadata are required.')
            if not item['source'].startswith('https://'):raise ValueError('Declare the upstream HTTPS source identity.')
            if item.get('local_use_permitted') is not True:raise PermissionError('Each component needs local-use permission.')
            if not re.fullmatch('[a-fA-F0-9]{64}',item.get('sha256','')):raise ValueError('Component SHA-256 pin required.')
            src=manifest.parent/safe_relative(item['path']);regular(src)
            if not src.resolve().is_relative_to(manifest.parent.resolve()) or sha256_file(src)!=item['sha256'].lower():raise ValueError('Component path/hash mismatch: '+role)
            arch=item.get('architecture');info=pe.parse(src)
            if src.suffix.lower() in ('.dll','.addon64','.addon32'):
                if not info.valid or not info.is_dll or info.machine!=arch:raise ValueError('Component PE architecture mismatch.')
            if role=='reshade' and not {'ReShadeRegisterAddon','ReShadeUnregisterAddon'}<=set(info.exports):raise ValueError('ReShade full addon exports are required.')
            record=dict(item,sha256=item['sha256'].lower(),architectures=[arch] if arch else [],trust='local_declared_not_a_security_review')
            validated[role]=record|{'_source':str(src)}
        ordered_components(validated)
        for role,item in validated.items():
            dest=cache_dir()/'components'/item['sha256']/Path(item['path']).name
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists() and sha256_file(dest)!=item['sha256']:raise ValueError('Changed content-addressed cache.')
            if not dest.exists():
                tmp=dest.with_suffix(dest.suffix+'.tmp');shutil.copyfile(item.pop('_source'),tmp)
                if sha256_file(tmp)!=item['sha256']:raise ValueError('Component changed during import.')
                tmp.replace(dest)
            item.pop('_source',None);item['stored_path']=str(dest)
        existing=self.db.get_setting('pipeline_components',{})
        existing.update(validated);self.db.set_setting('pipeline_components',existing)
        return validated

    def records(self):return self.db.get_setting('pipeline_components',{})

    def verified(self,role):
        item=self.records().get(role)
        if not item:return None
        p=Path(item['stored_path'])
        try:
            regular(p)
            return item if sha256_file(p)==item['sha256'] else None
        except (OSError,ValueError):return None
