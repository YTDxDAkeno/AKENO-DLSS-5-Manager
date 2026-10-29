"""OpenVPN component download and process state, with stand-ins (no Windows, no network)."""
import hashlib

import pytest

from akeno.network import openvpn_windows as ow
from akeno.network.windows import NativeUnavailable


def test_only_https_redirects_are_followed():
    handler = ow._HttpsRedirects()
    assert handler.redirect_request(None, None, 301, '', {}, 'http://evil.example/x.msi') is None


def test_download_must_equal_the_pinned_installer(monkeypatch):
    class Answer:
        status = 200
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return 'https://mirror.example/OpenVPN.msi'
        def read(self, n): return self.data
    for data, ok in ((b'x' * ow.MSI_SIZE, False), (b'short', False)):
        monkeypatch.setattr(ow.urllib.request, 'build_opener', lambda *a, data=data: type('O', (), {
            'open': lambda self, r, timeout: Answer(data)})())
        with pytest.raises(NativeUnavailable, match='does not match'):
            ow._download()
    payload = b'\0' * 16
    monkeypatch.setattr(ow, 'MSI_SIZE', len(payload))
    monkeypatch.setattr(ow, 'MSI_SHA256', hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(ow.urllib.request, 'build_opener', lambda *a: type('O', (), {
        'open': lambda self, r, timeout: Answer(payload)})())
    assert ow._download() == payload


class Proc:
    def __init__(self, code=None): self.code = code
    def poll(self): return self.code


@pytest.mark.parametrize('log,code,state', [
    ('Peer Connection Initiated\nInitialization Sequence Completed\n', None, 'connected'),
    ('Initialization Sequence Completed\nInactivity timeout (--ping-restart), restarting\nSIGUSR1[soft,ping-restart]\n', None, 'restarting'),
    ('Initialization Sequence Completed\nSIGUSR1[soft,ping-restart]\nInitialization Sequence Completed\n', None, 'connected'),
    ('Received control message: AUTH_FAILED\n', 0, 'auth_failed'),
    ('TLS Error: TLS handshake failed\n', None, 'tls_failed'),
    ('VERIFY X509NAME ERROR: CN=evil\n', 1, 'certificate_failed'),
    ('Attempting to establish TCP connection\n', None, 'connecting'),
    ('Attempting to establish TCP connection\n', 1, 'exited'),
])
def test_state_comes_from_openvpns_own_log(tmp_path, log, code, state):
    process = ow.OpenVPNProcess(tmp_path)
    process.log.write_text(log)
    process.process = Proc(code)
    assert process.state() == state


def test_a_slow_adapter_query_is_reported_never_taken_for_a_missing_adapter(monkeypatch):
    import subprocess
    from akeno.network import lan_windows
    from akeno.network import openvpn_windows as ow
    from akeno.network.windows import NativeUnavailable

    def slow(script, timeout=60):
        assert timeout >= 60                                  # the first start after an MSI install can be slow
        raise subprocess.TimeoutExpired('powershell.exe', timeout)
    monkeypatch.setattr(lan_windows, 'powershell', slow)
    with pytest.raises(NativeUnavailable) as exc:
        ow._adapter_present()
    assert 'did not answer' in str(exc.value)
