"""Installer policy with native/network stand-ins. These tests do not install a driver or prove Windows UAC."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest

from akeno.network import bootstrap as b
from akeno.network.windows import NativeUnavailable
from akeno.security.authenticode import SignatureInfo


INDEX = b'<a href="wireguard-amd64-1.1.1.msi">1.1.1</a>'


def test_index_chooses_native_architecture_and_numeric_version_without_external_paths():
    index = INDEX + b'''<a href="wireguard-amd64-1.9.0.msi">old</a>
        <a href="wireguard-amd64-1.10.0.msi">new</a>
        <a href="wireguard-arm64-8.0.0.msi">other</a>
        <a href="https://example.invalid/wireguard-amd64-99.0.msi">foreign</a>
        <a href="../wireguard-amd64-99.0.msi">traversal</a>
        <a href="wireguard-amd64-99.0.msi?x=1">query</a>'''
    assert b._select_msi(index, 'amd64') == ('wireguard-amd64-1.10.0.msi', '1.10.0')
    assert b._select_msi(index, 'arm64') == ('wireguard-arm64-8.0.0.msi', '8.0.0')


# Explicit ids: pytest exports the test id in PYTEST_CURRENT_TEST, and Windows rejects environment variables
# longer than 32767 characters, so the oversized index must not become part of the id.
@pytest.mark.parametrize('index,architecture', [(b'no MSI', 'amd64'), (INDEX, 'x86'),
    (INDEX, 'amd64/../../evil'), (b'\xff' + INDEX, 'amd64'), (b'x' * (b.MAX_INDEX + 1), 'amd64'),
    (b'<a href="wireguard-amd64-9999999.0.msi">bad</a>', 'amd64')],
    ids=['no-msi', 'unsupported-architecture', 'architecture-traversal', 'invalid-utf8', 'oversized-index',
         'version-out-of-range'])
def test_unusable_index_never_becomes_an_installer(index, architecture):
    with pytest.raises(NativeUnavailable):
        b._select_msi(index, architecture)


@pytest.mark.parametrize('consent', [False, None, 1, 'yes'])
def test_explicit_boolean_consent_required_before_any_privileged_or_network_action(monkeypatch, consent):
    monkeypatch.setattr(b, '_require_windows', lambda **kw: pytest.fail('no operation before consent'))
    with pytest.raises(NativeUnavailable, match='consent'):
        b.install(consent=consent)


class Response(BytesIO):
    def __init__(self, data, *, length=None, status=200, url=b.BASE):
        super().__init__(data)
        self.headers = {} if length is None else {'Content-Length': length}
        self.status = status
        self.url = url
    def geturl(self):
        return self.url


def network(monkeypatch, response):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return response
    def builder(*handlers):
        assert any(isinstance(handler, b._NoRedirect) for handler in handlers)
        assert any(isinstance(handler, b.urllib.request.HTTPSHandler) for handler in handlers)
        return Opener()
    monkeypatch.setattr(b.urllib.request, 'build_opener', builder)
    return calls


def test_download_preserves_verified_https_and_bounds_response(monkeypatch):
    calls = network(monkeypatch, Response(b'index', length='5'))
    assert b._download(b.BASE, 6) == b'index'
    request, timeout = calls[0]
    assert request.full_url == b.BASE and timeout == 30
    assert request.get_header('Accept-encoding') == 'identity'


@pytest.mark.parametrize('kwargs', [{'length': '6'}, {'length': '999'}, {'length': '-1'}, {'length': 'five'},
    {'length': '５'}, {'status': 302}, {'url': 'https://evil.invalid/'}, {'length': None}])
def test_truncated_oversized_or_redirected_download_is_rejected(monkeypatch, kwargs):
    network(monkeypatch, Response(b'index', **kwargs))
    with pytest.raises(NativeUnavailable):
        b._download(b.BASE, 4)


@pytest.mark.parametrize('url', ['http://download.wireguard.com/windows-client/',
    'https://example.invalid/wireguard-amd64-1.1.1.msi', b.BASE + '../wireguard-amd64-1.1.1.msi',
    b.BASE + 'wireguard-amd64-1.1.1.msi?script=x', b.BASE + 'wireguard-installer.exe'])
def test_caller_cannot_substitute_a_source_or_installer(url):
    with pytest.raises(NativeUnavailable, match='official'):
        b._download(url, 100)


@pytest.mark.parametrize('signature', [SignatureInfo(False, False, None, 'unsupported'),
    SignatureInfo(True, False, 'WireGuard LLC', 'revoked'), SignatureInfo(True, True, 'Another publisher', 'valid')])
def test_installer_requires_online_revocation_and_expected_verified_publisher(monkeypatch, signature, tmp_path):
    calls = []
    monkeypatch.setattr(b, 'verify', lambda path, **kw: calls.append((path, kw)) or signature)
    with pytest.raises(NativeUnavailable, match='signature'):
        b._verified_installer(tmp_path / 'wireguard.msi')
    assert calls[0][1] == {'online': True}


class Native:
    def __init__(self, root):
        self.root = root
        self.calls = []
    def _trusted(self, name):
        self.calls.append(('verify', name))
        return self.root / name
    def _call(self, name, *args):
        self.calls.append(('call', name, *args))


def installer(monkeypatch, tmp_path, *, code=0, signature_ok=True, initial='missing'):
    native = Native(tmp_path / 'Program Files' / 'WireGuard')
    native.root.parent.mkdir()
    calls = []
    monkeypatch.setattr(b, '_require_windows', lambda **kw: calls.append(('require', kw)))
    monkeypatch.setattr(b, '_architecture', lambda: 'amd64')
    monkeypatch.setattr(b, 'WindowsWireGuard', lambda: native)
    states = iter([{'state': initial}, {'state': 'ready'}])
    monkeypatch.setattr(b, 'status', lambda: next(states))
    monkeypatch.setattr(b, '_download', lambda url, limit: calls.append(('download', url)) or (INDEX if url == b.BASE else b'MSI'))
    monkeypatch.setattr(b, '_protected_directory', lambda path: path.mkdir(mode=0o700))
    monkeypatch.setattr(b, 'private_file', lambda path, data: path.write_bytes(data))
    def signature(path):
        calls.append(('signature', path))
        assert path.read_bytes() == b'MSI'
        if not signature_ok:
            raise NativeUnavailable('fixture signature failed')
    monkeypatch.setattr(b, '_verified_installer', signature)
    monkeypatch.setattr(b, '_system_executable', lambda name: Path('C:/Windows/System32') / name)
    def run(args, **kw):
        assert Path(args[2]).read_bytes() == b'MSI'
        calls.append(('execute', args, kw))
        return SimpleNamespace(returncode=code)
    monkeypatch.setattr(b.subprocess, 'run', run)
    monkeypatch.setattr(b, 'service_state', lambda *a, **kw: None)
    return native, calls


def test_installer_verifies_before_fixed_argv_execution_and_cleans_private_download(monkeypatch, tmp_path):
    native, calls = installer(monkeypatch, tmp_path)
    result = b.install(consent=True)
    assert result == {'state': 'ready', 'version': '1.1.1', 'reboot_required': False}
    names = [call[0] for call in calls]
    assert names.index('signature') < names.index('execute')
    execute = next(call for call in calls if call[0] == 'execute')
    # Exactly the System32 path returned by _system_executable (native separators), never a PATH lookup.
    assert execute[1][0] == str(Path('C:/Windows/System32') / 'msiexec.exe')
    assert execute[1][1:] == ['/i', str(next(call[1] for call in calls if call[0] == 'signature')),
                             '/qn', '/norestart', 'DO_NOT_LAUNCH=1']
    assert execute[2]['timeout'] == 180 and 'shell' not in execute[2]
    assert native.calls[-1] == ('call', 'wireguard.exe', '/installmanagerservice')
    assert list(native.root.parent.iterdir()) == []


def test_bad_signature_never_executes_installer_and_removes_download(monkeypatch, tmp_path):
    native, calls = installer(monkeypatch, tmp_path, signature_ok=False)
    with pytest.raises(NativeUnavailable, match='signature'):
        b.install(consent=True)
    assert not any(call[0] == 'execute' for call in calls)
    assert native.calls == [] and list(native.root.parent.iterdir()) == []


def test_msi_failure_never_runs_manager_and_cleans_download(monkeypatch, tmp_path):
    native, calls = installer(monkeypatch, tmp_path, code=1603)
    with pytest.raises(NativeUnavailable, match='Windows Installer'):
        b.install(consent=True)
    assert native.calls == [] and list(native.root.parent.iterdir()) == []


def test_msi_reboot_request_reported_without_claiming_a_connected_tunnel(monkeypatch, tmp_path):
    native, calls = installer(monkeypatch, tmp_path, code=3010)
    assert b.install(consent=True) == {'state': 'ready', 'version': '1.1.1', 'reboot_required': True}
    assert not any('installtunnelservice' in call for call in native.calls)


def test_repair_of_verified_installed_tools_does_not_download_or_reinstall(monkeypatch, tmp_path):
    native, calls = installer(monkeypatch, tmp_path, initial='incomplete')
    assert b.install(consent=True)['state'] == 'ready'
    assert not any(call[0] in ('execute', 'download') for call in calls)
    assert native.calls == [('verify', 'wireguard.exe'), ('verify', 'wg.exe'),
                            ('call', 'wireguard.exe', '/installmanagerservice')]


def test_ready_installation_is_a_read_only_noop(monkeypatch, tmp_path):
    native, calls = installer(monkeypatch, tmp_path, initial='ready')
    assert b.install(consent=True)['state'] == 'ready'
    assert [call[0] for call in calls] == ['require'] and native.calls == []


def test_authenticode_online_flag_preserves_legacy_offline_default(monkeypatch, tmp_path):
    """WinTrust ABI stand-in records policy flags; an actual certificate/CRL chain still needs Windows CI."""
    from akeno.security import authenticode as a
    flags = []
    class Function:
        def __call__(self, window, action, data):
            value = data._obj
            flags.append((value.dwStateAction, value.fdwRevocationChecks, value.dwProvFlags))
            return 0
    wintrust = SimpleNamespace(WinVerifyTrust=Function())
    monkeypatch.setattr(a, 'is_windows', lambda: True)
    monkeypatch.setattr(a.ctypes, 'WinDLL', lambda *a, **kw: wintrust, raising=False)
    monkeypatch.setattr(a, '_trusted_signer', lambda state: ('WireGuard LLC', 'certificate-digest'))
    path = tmp_path / 'signed.msi'
    path.write_bytes(b'fixture')
    assert a.verify(str(path)).valid
    assert a.verify(str(path), online=True).valid
    assert flags == [(1, 0, 0x1000), (2, 0, 0x1000), (1, 1, 0x80), (2, 1, 0x80)]
