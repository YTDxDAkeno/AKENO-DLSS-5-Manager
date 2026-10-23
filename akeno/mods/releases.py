"""Actual GitHub release API; discovery never grants installation authority."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from akeno.security.downloads import Cancelled

API_LIMIT=8*1024*1024
REPO=re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
SHA=re.compile(r'[a-f0-9]{64}\Z')


def version_key(value):
    """Stable numeric versions sort above their prereleases, without treating strings as numbers."""
    value=str(value).removeprefix('v');numbers=re.match(r'(\d+(?:\.\d+)*)',value)
    if not numbers:return ()
    main=tuple(int(x) for x in numbers[1].split('.'));main=main+(0,)*(4-len(main))
    suffix=value[numbers.end():]
    return main+(0 if suffix else 1,)+(tuple(int(x) for x in re.findall(r'\d+',suffix)) or (0,))


def asset_url(repo,url):
    u=urllib.parse.urlsplit(url)
    prefix='/'+repo+'/releases/download/'
    if u.scheme!='https' or u.netloc!='github.com' or u.query or u.fragment or not u.path.startswith(prefix):
        raise ValueError('Release asset is outside the selected original repository.')
    if '..' in urllib.parse.unquote(u.path).split('/'):raise ValueError('Unsafe release asset path.')
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('GitHub API redirect refused; review the repository identity.')


class GitHubReleases:
    def __init__(self,db,allowed_repositories,opener=None):
        self.db=db;self.allowed=frozenset(allowed_repositories)
        self.opener=opener or urllib.request.build_opener(_NoRedirect())

    def cached(self,repo):
        return self.db.get_setting('mod-releases:'+repo,{}).get('releases',[])

    def refresh(self,repo,token=None):
        if repo not in self.allowed or not REPO.fullmatch(repo):raise PermissionError('Repository is not reviewed for release discovery.')
        old=self.db.get_setting('mod-releases:'+repo,{})
        etags=old.get('etags',{});new_etags={};all_rows=[]
        for page in range(1,4):
            if token and token.cancelled:raise Cancelled('Release refresh cancelled.')
            url=f'https://api.github.com/repos/{repo}/releases?per_page=100&page={page}'
            headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'AKENO-Community-Mod-Manager'}
            if str(page) in etags:headers['If-None-Match']=etags[str(page)]
            try:
                with self.opener.open(urllib.request.Request(url,headers=headers),timeout=30) as response:
                    final=urllib.parse.urlsplit(response.geturl())
                    if final.scheme!='https' or final.netloc!='api.github.com' or final.path!=f'/repos/{repo}/releases':raise ValueError('Unexpected GitHub API response source.')
                    body=response.read(API_LIMIT+1)
                    if len(body)>API_LIMIT:raise ValueError('Release metadata exceeds 8 MiB.')
                    rows=json.loads(body);new_etags[str(page)]=response.headers.get('ETag','')
            except urllib.error.HTTPError as error:
                if error.code!=304:raise
                rows=old.get('pages',{}).get(str(page),[]);new_etags[str(page)]=etags.get(str(page),'')
            if not isinstance(rows,list) or len(rows)>100:raise ValueError('Invalid release metadata list.')
            if page==1:pages={}
            pages[str(page)]=rows
            for row in rows:
                if row.get('draft'):continue
                assets=[]
                for item in row.get('assets',[])[:256]:
                    name=item.get('name','');url=item.get('browser_download_url','')
                    if not isinstance(name,str) or not name or '/' in name or '\\' in name:continue
                    asset_url(repo,url)
                    digest=item.get('digest') or '';pin=digest[7:] if digest.startswith('sha256:') else None
                    if pin and not SHA.fullmatch(pin):raise ValueError('Invalid published asset SHA-256.')
                    assets.append({'id':item.get('id'),'name':name,'url':url,'size':item.get('size'),
                        'sha256':pin,'updated_at':item.get('updated_at'),'checksum_source':'github_asset_digest' if pin else 'unverified',
                        'installation_authority':'requires_reviewed_version_manifest'})
                all_rows.append({'id':row.get('id'),'tag':str(row.get('tag_name','')),'name':str(row.get('name') or row.get('tag_name','')),
                    'prerelease':row.get('prerelease') is True,'published_at':row.get('published_at'),
                    'url':row.get('html_url'),'assets':assets})
            if len(rows)<100:break
        # Commit a complete refresh only after all pages validate; keep old pins and cached releases on failure.
        self.db.set_setting('mod-releases:'+repo,{'checked_at':datetime.now(timezone.utc).isoformat(),
            'etags':new_etags,'pages':pages,'releases':all_rows})
        return all_rows

    def available(self,repo,prerelease=False):
        return sorted([r for r in self.cached(repo) if prerelease or not r['prerelease']],key=lambda r:version_key(r['tag']),reverse=True)

    def asset_changes(self,repo,pinned):
        """A successful refresh can identify removals/changes; a failed request cannot."""
        cached=self.db.get_setting('mod-releases:'+repo,{})
        if not cached:return []
        releases={r['tag']:r for r in cached['releases']};changes=[]
        for package in pinned:
            release=releases.get(package['release_tag'])
            if release is None:
                # API history is bounded: absence beyond the fetched window is not proof of removal.
                continue
            asset=next((a for a in release['assets'] if a['name']==package['asset_name']),None)
            if asset is None:changes.append({'package':package['id'],'state':'removed'})
            elif asset['url']!=package['url'] or asset.get('sha256') and asset['sha256']!=package['sha256']:
                changes.append({'package':package['id'],'state':'altered'})
        return changes
