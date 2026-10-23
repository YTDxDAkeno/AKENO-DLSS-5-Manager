"""Version-specific community manifests. Remote metadata cannot overwrite this authority."""
from pathlib import Path
import json
import re
from akeno.paths import resource_dir
from akeno.security.catalog import Catalog,Package
from .releases import asset_url,version_key,REPO,SHA


class CommunityCatalog:
    def __init__(self,data):
        if data.get('schema')!=2:raise ValueError('Community catalog schema 2 required.')
        self.data=data;self.projects={p['id']:p for p in data['projects']};self.packages={p['id']:p for p in data['packages']}
        self.recipes={r['id']:r for r in data['recipes']};self.profiles=data.get('profiles',[])
        if len(self.projects)!=len(data['projects']) or len(self.packages)!=len(data['packages']) or len(self.recipes)!=len(data['recipes']):raise ValueError('Duplicate catalog identity.')
        for project in self.projects.values():
            if not REPO.fullmatch(project['repository']):raise ValueError('Invalid original repository.')
        for package in self.packages.values():
            if package['project'] not in self.projects:raise ValueError('Unknown package project.')
            repo=self.projects[package['project']]['repository'];asset_url(repo,package['url'])
            if not SHA.fullmatch(package['sha256']):raise ValueError('Exact package SHA-256 required.')
            if package.get('installable'):
                if not package.get('files') or not package.get('contract') or not package.get('license'):raise ValueError('Installable version needs a reviewed file/API/license contract.')
                if package.get('redistribution') not in ('permitted_with_notices','original_download_only'):raise ValueError('Unresolved distribution rights.')
                if any(not SHA.fullmatch(v) for v in package['files'].values()):raise ValueError('Member hash missing.')
            for name in package.get('files',{}):
                from akeno.security.archive import normalize_member
                if normalize_member(name)!=name:raise ValueError('Unsafe member path.')
        for recipe in self.recipes.values():
            for pid in recipe.get('packages',[]):
                if pid not in self.packages:raise ValueError('Unknown recipe version dependency.')
            if recipe['family'] in ('feeder','fivem','renodx_feeder'):
                feeders=[self.packages[pid] for pid in recipe.get('packages',[]) if self.packages[pid].get('backend')=='dlss5_feeder']
                if len(feeders)!=1 or feeders[0]['version']!='1.19.0':raise ValueError('Recipe needs the reviewed exact Feeder 1.19.0 transport contract.')
            backends=[self.packages[pid].get('backend') for pid in recipe.get('packages',[])]
            if len(backends)!=len(set(backends)):raise ValueError('Multiple versions of the same component in one recipe.')
            if len(set(recipe.get('consumers',[])))!=len(recipe.get('consumers',[])) or len(recipe.get('consumers',[]))>1:raise ValueError('Exactly one neural consumer is allowed.')

    @classmethod
    def bundled(cls):return cls(json.loads((resource_dir()/'community/catalog.json').read_text(encoding='utf-8')))

    def versions(self,project,prerelease=False):
        return sorted([p for p in self.packages.values() if p['project']==project and (prerelease or p['channel']!='prerelease')],key=lambda p:version_key(p['version']),reverse=True)

    def package(self,pid):return self.packages.get(pid)

    def install_catalog(self,base):
        data={'schema':1,'catalog_version':'community-2','allowed_hosts':base.allowed_hosts,'packages':[]}
        from dataclasses import asdict
        data['packages']=[dict(asdict(p),trust=p.trust.value) for p in base.packages]
        existing={p['id'] for p in data['packages']}
        for p in self.packages.values():
            if not p.get('installable') or p['id'] in existing or p.get('backend') not in ('optiscaler_nr','dlss5_feeder'):continue
            data['packages'].append({'id':p['id'],'backend':p['backend'],'name':p['name'],'version':p['version'],
                'upstream':'https://github.com/'+self.projects[p['project']]['repository'],'url':p['url'],'sha256':p['sha256'],
                'license':p['license'],'trust':'verified','size':p.get('size'),'root':p.get('root',''),
                'files':p['files'],'apis':p['graphics_apis'],'architectures':p['architectures'],'reviewed':p['reviewed_at']})
        enriched=Catalog(data);enriched.tools=base.tools
        return enriched
