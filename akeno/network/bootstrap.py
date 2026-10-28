"""Explicit, elevated first-use installation of official signed WireGuard for Windows.

No work runs on import, module activation, application update, or page construction. ``install(consent=True)``
is called only by the fixed UAC helper after a user has accepted installation. The helper downloads into a new
Administrators/SYSTEM-owned protected directory below the OS Program Files directory, verifies Authenticode
with online revocation, invokes only System32 msiexec, then verifies the installed tools before manager setup.
The whole installer remains an independently serviced third-party product; AKENO never removes an existing
WireGuard installation or another application's adapters.
"""
from __future__ import annotations

import ctypes
from html.parser import HTMLParser
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from akeno.network.windows import NativeUnavailable, WindowsWireGuard, private_file, service_state
from akeno.security.authenticode import verify
from akeno.updater.staging import no_links_on_the_way


BASE = 'https://download.wireguard.com/windows-client/'
MAX_INDEX = 256 * 1024
MAX_INSTALLER = 32 * 1024 * 1024
SIGNER = 'wireguard llc'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download(url: str, limit: int) -> bytes:
    """Fixed official origin only. Preserve normal proxy/CA configuration and reject every redirect."""
    if url != BASE and not re.fullmatch(re.escape(BASE) + r'wireguard-(?:amd64|arm64)-\d+(?:\.\d+){1,3}\.msi', url):
        raise NativeUnavailable('Only the official WireGuard installer source is allowed.')
    opener = urllib.request.build_opener(_NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={'User-Agent': 'AKENO-Network-component-setup/1',
                                                   'Accept-Encoding': 'identity'})
    try:
        with opener.open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise NativeUnavailable('The official WireGuard source returned an unexpected response.')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isascii() or not length.isdecimal() or int(length) > limit):
                raise NativeUnavailable('The official WireGuard download exceeds the allowed size.')
            data = response.read(limit + 1)
            if not data or len(data) > limit or (length is not None and len(data) != int(length)):
                raise NativeUnavailable('The official WireGuard download is empty, truncated or too large.')
            return data
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise NativeUnavailable('The official WireGuard download is unavailable. Check the connection and retry.') from exc


class _Index(HTMLParser):
    def __init__(self, architecture):
        super().__init__()
        self.architecture = architecture
        self.versions = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        for key, value in attrs:
            if key != 'href' or not isinstance(value, str):
                continue
            match = re.fullmatch(r'wireguard-' + re.escape(self.architecture) + r'-(\d+(?:\.\d+){1,3})\.msi', value)
            if match:
                parts = tuple(int(part) for part in match[1].split('.'))
                if all(part <= 65535 for part in parts):
                    self.versions[parts + (0,) * (4 - len(parts))] = (value, match[1])


def _select_msi(index: bytes, architecture: str) -> tuple[str, str]:
    if architecture not in ('amd64', 'arm64') or not index or len(index) > MAX_INDEX:
        raise NativeUnavailable('The WireGuard component does not support this Windows architecture.')
    parser = _Index(architecture)
    try:
        parser.feed(index.decode('utf-8', errors='strict'))
    except (ValueError, UnicodeError) as exc:
        raise NativeUnavailable('The official WireGuard download index is invalid.') from exc
    if not parser.versions:
        raise NativeUnavailable('No official WireGuard installer is available for this architecture.')
    return parser.versions[max(parser.versions)]


def _require_windows(*, elevated=False):
    if sys.platform != 'win32':
        raise NativeUnavailable('Installing network components requires Windows.')
    if elevated and not ctypes.windll.shell32.IsUserAnAdmin():
        raise NativeUnavailable('Use the explicit elevated AKENO network installation helper.')


def _architecture() -> str:
    """Native OS architecture, never a user-controlled processor environment variable."""
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.IsWow64Process2.argtypes = [w.HANDLE, ctypes.POINTER(w.WORD), ctypes.POINTER(w.WORD)]
    kernel.IsWow64Process2.restype = w.BOOL
    process, native = w.WORD(), w.WORD()
    if not kernel.IsWow64Process2(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
        raise NativeUnavailable('Cannot determine the native Windows architecture.')
    architecture = {0x8664: 'amd64', 0xAA64: 'arm64'}.get(native.value)
    if architecture is None:
        raise NativeUnavailable('AKENO network setup supports 64-bit Windows only.')
    return architecture


def _system_executable(name: str) -> Path:
    """Never resolve a privileged executable from PATH, WINDIR, or another environment variable."""
    if name != 'msiexec.exe':
        raise NativeUnavailable('Unknown network component operation.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise NativeUnavailable('Cannot locate the Windows Installer service.')
    path = Path(buffer.value) / name
    no_links_on_the_way(path, path.anchor)
    if not path.is_file():
        raise NativeUnavailable('The Windows Installer service is unavailable.')
    return path


def _protected_directory(path: Path):
    """Create a new high-integrity directory, owned by Administrators, before any privileged download is saved.

    The directory does not grant the user's SID write access: a non-elevated process of the same account cannot
    replace the MSI between signature verification and execution. Existing paths are always refused.
    """
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    sddl = 'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)S:(ML;OICI;NW;;;HI)'
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise NativeUnavailable('Cannot secure the temporary component directory.')
    class Attributes(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', w.BOOL)]
    attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
    kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, ctypes.POINTER(Attributes)]
    kernel.CreateDirectoryW.restype = w.BOOL
    kernel.LocalFree.argtypes = [w.HLOCAL]
    kernel.LocalFree.restype = w.HLOCAL
    try:
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            raise NativeUnavailable('Cannot create the protected component directory.')
    finally:
        kernel.LocalFree(descriptor)


def _verified_installer(path: Path):
    signature = verify(str(path), online=True)
    if not signature.checked or not signature.valid or (signature.signer or '').casefold() != SIGNER:
        raise NativeUnavailable('The WireGuard installer signature or online certificate revocation check failed.')


def status() -> dict:
    """Read-only readiness, safe for a settings page. Never downloads, starts a service, or requests elevation."""
    if sys.platform != 'win32':
        return {'state': 'unsupported', 'reason': 'Network components require Windows.'}
    native = WindowsWireGuard()
    try:
        native._trusted('wireguard.exe')
        native._trusted('wg.exe')
    except NativeUnavailable:
        return {'state': 'missing', 'reason': 'The official signed WireGuard components are not installed.'}
    if not native.configs.is_dir() or service_state('WireGuardManager', prefix='') != 'running':
        return {'state': 'incomplete', 'reason': 'WireGuard manager setup is incomplete. Install or repair the network components.'}
    return {'state': 'ready', 'reason': None}


def install(*, consent: bool) -> dict:
    """Fixed helper operation; requires explicit consent and UAC elevation for the entire download/install.

    An MSI requesting a reboot is reported honestly. No tunnel, network route, driver signature policy or user
    profile is configured here. System32 msiexec and the installed signed manager are the only executed files.
    """
    if consent is not True:
        raise NativeUnavailable('Explicit consent is required to install the WireGuard network components.')
    _require_windows(elevated=True)
    native = WindowsWireGuard()
    before = status()
    if before['state'] == 'ready':
        return {'state': 'ready', 'version': None, 'reboot_required': False}
    version = None
    reboot = False
    if before['state'] == 'missing':
        architecture = _architecture()
        name, version = _select_msi(_download(BASE, MAX_INDEX), architecture)
        payload = _download(BASE + name, MAX_INSTALLER)
        program_files = native.root.parent
        no_links_on_the_way(program_files, program_files.anchor)
        folder = program_files / ('.AkenoNetworkInstall-' + uuid.uuid4().hex)
        _protected_directory(folder)
        path = folder / name
        try:
            private_file(path, payload)
            _verified_installer(path)
            # MSI installer runs inside the already elevated helper, with no UI launch or forced reboot.
            result = subprocess.run([str(_system_executable('msiexec.exe')), '/i', str(path), '/qn', '/norestart',
                                     'DO_NOT_LAUNCH=1'], capture_output=True, timeout=180,
                                    creationflags=0x08000000)
            if result.returncode not in (0, 3010):
                raise NativeUnavailable('Windows Installer could not install the official WireGuard component.')
            reboot = result.returncode == 3010
        except subprocess.TimeoutExpired as exc:
            raise NativeUnavailable('Windows Installer did not finish. Check installed components before retrying.') from exc
        finally:
            path.unlink(missing_ok=True)
            folder.rmdir()
    # Verify the installed source before running its only permitted setup verb. Never execute the MSI's payload
    # from the temporary directory, download path, caller input, or a configured environment variable.
    native._trusted('wireguard.exe')
    native._trusted('wg.exe')
    manager = service_state('WireGuardManager', prefix='')
    if manager != 'running':
        native._call('wireguard.exe', '/installmanagerservice')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = status()
        if current['state'] == 'ready':
            return {'state': 'ready', 'version': version, 'reboot_required': reboot}
        time.sleep(.25)
    if reboot:
        return {'state': 'reboot_required', 'version': version, 'reboot_required': True}
    raise NativeUnavailable('The WireGuard manager did not become ready. Repair the official component before connecting.')
