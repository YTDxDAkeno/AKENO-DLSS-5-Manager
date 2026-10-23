"""GitHub API/cache/security behavior with deterministic HTTP responses."""
import copy
import io
import json
import urllib.error
import pytest
from akeno.mods.releases import GitHubReleases,version_key,asset_url
from akeno.mods.catalog import CommunityCatalog
from akeno.security.downloads import CancelToken,Cancelled

REPO='Skynizz/optiscaler-dlss5'
URL='https://github.com/'+REPO+'/releases/download/v1.3.0/package.zip'


def release(version='1.3.0',digest='a'*64):
    return {'id':1,'tag_name':'v'+version,'prerelease':'beta' in version,'assets':[{'id':2,'name':'package.zip','browser_download_url':URL,'digest':'sha256:'+digest if digest else None,'size':100}]}


class Response(io.BytesIO):
    def __init__(self,rows,url):super().__init__(json.dumps(rows).encode());self.url=url;self.headers={'ETag':'"test"'}
    def geturl(self):return self.url


class API:
    def __init__(self,answers):self.answers=list(answers);self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request);answer=self.answers.pop(0)
        if isinstance(answer,Exception):raise answer
        return Response(answer,request.full_url)


def test_numeric_versions_and_original_asset_urls():
    assert version_key('1.19.0')>version_key('1.9.0')
    assert version_key('v1.18.0')>version_key('1.18.0-beta.2')>version_key('1.18.0-beta.1')
    for url in ('http://github.com/'+REPO+'/releases/download/x/y','https://github.com/attacker/other/releases/download/x/y',URL+'?token=secret',URL+'/../x'):
        with pytest.raises(ValueError):asset_url(REPO,url)


def test_api_pagination_etag_304_and_no_silent_trust(db):
    api=API([[release()]*100,[release('1.2.1')]])
    client=GitHubReleases(db,[REPO],api);rows=client.refresh(REPO)
    assert len(rows)==101 and len(api.requests)==2
    assert api.requests[0].full_url.startswith('https://api.github.com/repos/')
    assert rows[0]['assets'][0]['installation_authority']=='requires_reviewed_version_manifest'
    old=copy.deepcopy(db.get_setting('mod-releases:'+REPO))
    api.answers=[urllib.error.HTTPError(api.requests[0].full_url,304,'unchanged',{},None),urllib.error.HTTPError(api.requests[1].full_url,304,'unchanged',{},None)]
    assert client.refresh(REPO)==rows
    assert api.requests[-1].get_header('If-none-match')=='"test"'
    api.answers=[[release()]*100,ValueError('page 2 failed')]
    with pytest.raises(ValueError):client.refresh(REPO)
    assert client.cached(REPO)==old['releases']


def test_unverified_assets_and_changed_release_never_replace_catalog(db):
    catalog=CommunityCatalog.bundled();before=copy.deepcopy(catalog.data)
    client=GitHubReleases(db,[REPO],API([[release(digest=None)]]))
    assert client.refresh(REPO)[0]['assets'][0]['checksum_source']=='unverified'
    p=dict(catalog.packages['optiscaler-dlss5-skynizz-1.3.0'],asset_name='package.zip',url=URL)
    assert client.asset_changes(REPO,[p])==[]
    client.opener=API([[release(digest='b'*64)]]);client.refresh(REPO)
    assert client.asset_changes(REPO,[p])==[{'package':p['id'],'state':'altered'}]
    assert catalog.data==before


def test_unknown_repository_cancel_and_malformed_api_preserve_cache(db):
    client=GitHubReleases(db,[REPO],API([[release()]]));client.refresh(REPO);saved=client.cached(REPO)
    with pytest.raises(PermissionError):client.refresh('attacker/unreviewed')
    token=CancelToken();token.cancel()
    with pytest.raises(Cancelled):client.refresh(REPO,token)
    bad=release();bad['assets'][0]['browser_download_url']='https://evil.invalid/payload'
    client.opener=API([[bad]])
    with pytest.raises(ValueError):client.refresh(REPO)
    assert client.cached(REPO)==saved


def test_catalog_rejects_unsafe_members_and_multiple_consumers():
    data=copy.deepcopy(CommunityCatalog.bundled().data)
    data['recipes'][0]['consumers']=['optiscaler_nr','renodx_nr']
    with pytest.raises(ValueError,match='one neural consumer'):CommunityCatalog(data)
    data=copy.deepcopy(CommunityCatalog.bundled().data);data['packages'][0]['files']['../evil.dll']='a'*64
    from akeno.security.archive import ArchiveError
    with pytest.raises((ValueError,ArchiveError)):CommunityCatalog(data)


def test_mixed_feeder_versions_do_not_satisfy_transport_contract():
    data=copy.deepcopy(CommunityCatalog.bundled().data)
    recipe=next(r for r in data['recipes'] if r['family']=='feeder')
    recipe['packages'][0]='dlss5-feeder-1.18.0-beta.1'
    with pytest.raises(ValueError,match='exact Feeder 1.19.0'):CommunityCatalog(data)
