"""Official OpenVPN for Windows as the free VPN's transport: consented installation, verification and one process.

Installation (only ``install(consent=True)`` in the elevated helper, after the user agreed):
* the pinned official community MSI is downloaded from openvpn.org, its SHA-256 must equal the pinned value and its
  Authenticode signature (online revocation check) must name ``OpenVPN Inc.``;
* only the program and the TAP driver are installed (no GUI, no logon autostart, no auto-connect OpenVPNService);
  the MSI's program feature also brings OpenVPN's "Interactive Service" (verified on Windows), which AKENO does not
  use or change;
* AKENO creates its own TAP adapter ``AKENO-FreeVPN`` with the signed ``tapctl.exe`` so that routes and DNS checks
  concern exactly this tunnel. An existing OpenVPN installation is used when it is signed by OpenVPN Inc. and recent.

Running (``OpenVPNProcess``): ``openvpn.exe`` from the verified installation folder with fixed arguments only
(``--config`` AKENO's own generated file, ``--log`` and ``--service <event>`` for a clean stop). The helper process
puts itself in a kill-on-close job first, so OpenVPN never outlives a crashed helper.
"""
from __future__ import annotations

import ctypes
import hashlib
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from akeno.network.windows import NativeUnavailable

VERSION = '2.7.8'
MSI = 'OpenVPN-2.7.8-I002-amd64.msi'
MSI_SHA256 = '33104dd02c48cba172612882b1af6276422b1b4340d4c9c84dd2afb4b356fe9f'
MSI_SIZE = 5922816
URL = 'https://swupdate.openvpn.org/community/releases/' + MSI
SIGNER = 'openvpn inc.'
MIN_VERSION = (2, 6, 0)
FEATURES = 'OpenVPN,Drivers,Drivers.TAPWindows6'
ADAPTER = 'AKENO-FreeVPN'
TAP_HWID = 'root\\tap0901'

# Log lines that decide the state of one connection attempt (OpenVPN's own wording).
CONNECTED = 'Initialization Sequence Completed'
FAILURES = (('AUTH_FAILED', 'auth_failed'), ('TLS Error', 'tls_failed'), ('TLS handshake failed', 'tls_failed'),
            ('VERIFY ERROR', 'certificate_failed'), ('VERIFY X509NAME ERROR', 'certificate_failed'),
            ('Exiting due to fatal error', 'fatal'), ('All TAP-Windows adapters', 'adapter_busy'),
            ('Connection refused', 'refused'), ('Connection timed out', 'unreachable'))
RESTART = ('SIGUSR1[soft', 'Restart pause', 'Inactivity timeout')


def _program_files() -> Path:
    from akeno.network.windows import WindowsWireGuard
    return WindowsWireGuard().root.parent          # the OS known folder, never an environment variable


def install_dir() -> Path:
    return _program_files() / 'OpenVPN'


def _version(exe: Path) -> tuple[int, int, int] | None:
    try:
        out = subprocess.run([str(exe), '--version'], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r'OpenVPN (\d+)\.(\d+)\.(\d+)', out or '')
    return tuple(int(x) for x in match.groups()) if match else None


def trusted_executable(name: str = 'openvpn.exe', *, online: bool = False) -> Path:
    """A file of the official installation, signed by OpenVPN Inc. Raises NativeUnavailable otherwise."""
    if name not in ('openvpn.exe', 'tapctl.exe'):
        raise NativeUnavailable('Unknown OpenVPN component.')
    from akeno.security.authenticode import verify
    from akeno.updater.staging import no_links_on_the_way
    path = install_dir() / 'bin' / name
    no_links_on_the_way(path, path.anchor)
    if not path.is_file():
        raise NativeUnavailable('The official OpenVPN component is not installed.')
    signature = verify(str(path), online=online)
    if not signature.checked or not signature.valid or (signature.signer or '').casefold() != SIGNER:
        raise NativeUnavailable('The installed OpenVPN is not signed by OpenVPN Inc.')
    return path


ADAPTER_QUERY_SECONDS = 60      # the first PowerShell start right after an MSI installation can take over 15 s


def _adapter_present() -> bool:
    """Whether AKENO's TAP adapter exists. A query that Windows does not answer in time raises NativeUnavailable
    (never "missing": that would make the installation create a second adapter)."""
    from akeno.network.lan_windows import powershell
    try:
        out = powershell(f"@(Get-NetAdapter -Name '{ADAPTER}' -IncludeHidden -ErrorAction SilentlyContinue | "
                         "Where-Object InterfaceDescription -like 'TAP-Windows Adapter V9*').Count",
                         timeout=ADAPTER_QUERY_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise NativeUnavailable('Windows did not answer the network adapter query in time. Try again in a '
                                'moment.') from exc
    except (NativeUnavailable, OSError, ValueError):
        return False
    return out.strip() == '1'


def status() -> dict:
    """Read-only readiness for the page. Never downloads, installs or requests elevation."""
    if sys.platform != 'win32':
        return {'state': 'unsupported', 'reason': 'The free VPN requires Windows.'}
    try:
        exe = trusted_executable()
    except NativeUnavailable:
        return {'state': 'missing', 'reason': 'The official OpenVPN component is not installed.'}
    version = _version(exe)
    if version is None or version < MIN_VERSION:
        return {'state': 'outdated', 'reason': 'The installed OpenVPN is too old. Install the current version.'}
    try:
        present = _adapter_present()
    except NativeUnavailable as exc:
        return {'state': 'incomplete', 'reason': str(exc)}
    if not present:
        return {'state': 'incomplete', 'reason': 'The AKENO VPN adapter is missing. Repair the VPN component.'}
    return {'state': 'ready', 'reason': None, 'version': '.'.join(map(str, version))}


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    """openvpn.org answers with a redirect to its download mirror. Only HTTPS targets, at most three hops; the
    bytes are accepted only if they equal the pinned SHA-256 (and later the OpenVPN Inc. signature)."""
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).lower().startswith('https://'):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download() -> bytes:
    import ssl
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(_HttpsRedirects(),
                                         urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(URL, headers={'User-Agent': 'AKENO-Network-component-setup/1',
                                                   'Accept-Encoding': 'identity'})
    try:
        with opener.open(request, timeout=60) as response:
            if response.status != 200 or not response.geturl().lower().startswith('https://'):
                raise NativeUnavailable('The official OpenVPN source returned an unexpected response.')
            data = response.read(MSI_SIZE + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise NativeUnavailable('The official OpenVPN download is unavailable. Check the connection and retry.') from exc
    if len(data) != MSI_SIZE or hashlib.sha256(data).hexdigest() != MSI_SHA256:
        raise NativeUnavailable('The OpenVPN download does not match the reviewed official installer.')
    return data


def install(*, consent: bool) -> dict:
    """Fixed elevated helper operation after explicit consent: official OpenVPN (if needed) and AKENO's adapter."""
    if consent is not True:
        raise NativeUnavailable('Explicit consent is required to install the VPN component.')
    from akeno.network.bootstrap import _architecture, _protected_directory, _require_windows, _system_executable
    from akeno.network.windows import private_file
    from akeno.security.authenticode import verify
    _require_windows(elevated=True)
    before = status()
    if before['state'] == 'ready':
        return {'state': 'ready', 'version': before.get('version'), 'reboot_required': False}
    reboot = False
    if before['state'] in ('missing', 'outdated'):
        if _architecture() != 'amd64':
            raise NativeUnavailable('The free VPN component currently supports 64-bit x64 Windows only.')
        payload = _download()
        folder = _program_files() / ('.AkenoOpenVPNInstall-' + uuid.uuid4().hex)
        _protected_directory(folder)
        path = folder / MSI
        try:
            private_file(path, payload)
            signature = verify(str(path), online=True)
            if not signature.checked or not signature.valid or (signature.signer or '').casefold() != SIGNER:
                raise NativeUnavailable('The OpenVPN installer signature or online revocation check failed.')
            result = subprocess.run([str(_system_executable('msiexec.exe')), '/i', str(path), '/qn', '/norestart',
                                     'ADDLOCAL=' + FEATURES], capture_output=True, timeout=300,
                                    creationflags=0x08000000)
            if result.returncode not in (0, 3010):
                raise NativeUnavailable('Windows Installer could not install the official OpenVPN component.')
            reboot = result.returncode == 3010
        except subprocess.TimeoutExpired as exc:
            raise NativeUnavailable('Windows Installer did not finish. Check installed components before retrying.') from exc
        finally:
            path.unlink(missing_ok=True)
            folder.rmdir()
    if not _adapter_present():
        tapctl = trusted_executable('tapctl.exe', online=True)
        made = subprocess.run([str(tapctl), 'create', '--hwid', TAP_HWID, '--name', ADAPTER], capture_output=True,
                              text=True, timeout=120, creationflags=0x08000000)
        if made.returncode != 0 or not _adapter_present():
            raise NativeUnavailable('The AKENO VPN adapter could not be created.')
    after = status()
    if after['state'] != 'ready':
        if reboot:
            return {'state': 'reboot_required', 'version': VERSION, 'reboot_required': True}
        raise NativeUnavailable(after['reason'] or 'The VPN component is not ready.')
    return {'state': 'ready', 'version': after.get('version'), 'reboot_required': reboot}


def kill_with_this_process():
    """Put the current (elevated helper) process into a job that kills every member when its last handle closes.

    Children such as openvpn.exe inherit the job, so a crashed or killed helper never leaves a VPN running."""
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', w.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rb', 'wb', 'ob')]

    class Extended(ctypes.Structure):
        _fields_ = [('Basic', Basic), ('Io', IoCounters), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.GetCurrentProcess.restype = w.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise NativeUnavailable('Cannot protect the VPN process lifetime.')
    info = Extended()
    info.Basic.LimitFlags = 0x2000                     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or \
            not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise NativeUnavailable('Cannot protect the VPN process lifetime.')
    return job                                          # intentionally kept open for the process lifetime


def protected_workdir() -> Path:
    """A new Administrators/SYSTEM-only, high-integrity folder for one connection's configuration and log.

    The non-elevated user cannot change the configuration between AKENO writing it and OpenVPN reading it."""
    from akeno.network.bootstrap import _protected_directory
    from akeno.updater.staging import no_links_on_the_way
    base = _program_files()
    no_links_on_the_way(base, base.anchor)
    folder = base / ('.AkenoFreeVPN-' + uuid.uuid4().hex)
    _protected_directory(folder)
    return folder


class OpenVPNProcess:
    """One running openvpn.exe with AKENO's configuration; ``state()`` reads OpenVPN's own log."""

    def __init__(self, workdir: Path | None = None, executable: Path | None = None, *, popen=subprocess.Popen):
        self.workdir = Path(workdir) if workdir else protected_workdir()
        self.executable = executable
        self.popen = popen
        self.process = None
        self.event = None
        self.event_name = None
        self.log = self.workdir / 'openvpn.log'
        self.config = self.workdir / 'akeno.ovpn'
        self.offset = 0

    def start(self, config_text: str):
        from akeno.network.windows import private_file
        exe = self.executable or trusted_executable()
        self.log.unlink(missing_ok=True)
        self.config.unlink(missing_ok=True)
        private_file(self.config, config_text.encode('utf-8'))
        self.event_name = 'AkenoFreeVPN_' + uuid.uuid4().hex
        if sys.platform == 'win32':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateEventW.restype = ctypes.c_void_p
            kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
            self.event = kernel.CreateEventW(None, True, False, self.event_name)
            if not self.event:
                raise NativeUnavailable('Cannot prepare the VPN stop signal.')
        self.offset = 0
        self.process = self.popen([str(exe), '--config', str(self.config), '--log', str(self.log),
                                   '--service', self.event_name, '0'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=0x08000000)

    def text(self) -> str:
        try:
            return self.log.read_text(encoding='utf-8', errors='replace')[-512 * 1024:]
        except OSError:
            return ''

    def state(self) -> str:
        """'connected', 'connecting', 'restarting', one failure code from FAILURES, or 'exited'."""
        text = self.text()
        if self.process is not None and self.process.poll() is not None:
            for marker, code in FAILURES:
                if marker in text:
                    return code
            return 'exited'
        tail = text[text.rfind(CONNECTED):] if CONNECTED in text else ''
        if tail and not any(marker in tail for marker in RESTART):
            return 'connected'
        for marker, code in FAILURES:
            if marker in text[self.offset:]:
                return code
        if CONNECTED in text:
            return 'restarting'
        return 'connecting'

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 20.0) -> bool:
        """Signal OpenVPN's exit event (it removes its routes and DNS itself), then force-end if needed."""
        if self.process is None:
            return True
        if self.event and sys.platform == 'win32':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.SetEvent.argtypes = [ctypes.c_void_p]
            kernel.SetEvent(self.event)
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.25)
        if self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                return False
        if self.event and sys.platform == 'win32':
            ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self.event))
            self.event = None
        self.config.unlink(missing_ok=True)
        self.log.unlink(missing_ok=True)
        if self.workdir.name.startswith('.AkenoFreeVPN-'):
            try:
                self.workdir.rmdir()
            except OSError:
                pass
        return True
