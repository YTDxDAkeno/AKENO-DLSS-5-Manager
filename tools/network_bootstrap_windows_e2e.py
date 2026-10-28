"""Explicit installer integration check for a disposable, elevated Windows runner.

Downloads and installs the official WireGuard MSI when absent. Never run against a machine with unreviewed
third-party WireGuard state. This is a component installation check, not a claim of a working country VPN exit,
UAC interaction, network leak protection, or a two-PC LAN connection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consent-install', action='store_true', help='Explicitly allow installation on this disposable runner')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    from akeno.network import bootstrap
    if not args.consent_install:
        parser.error('--consent-install is required for this integration check')
    bootstrap._require_windows(elevated=True)
    from akeno.network.windows import WindowsWireGuard
    native = WindowsWireGuard()
    checks = []
    def check(value, label):
        if not value:
            raise AssertionError(label)
        checks.append(label)
    before = bootstrap.status()
    # Establish that same-account non-elevated processes do not own/write the privileged download directory.
    path = native.root.parent / ('.AkenoNetworkInstall-' + uuid.uuid4().hex)
    bootstrap._protected_directory(path)
    try:
        from ctypes import wintypes as w
        import ctypes
        adv = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        required = w.DWORD()
        information = 0x1 | 0x4 | 0x10  # OWNER_SECURITY_INFORMATION|DACL_SECURITY_INFORMATION|LABEL_SECURITY_INFORMATION
        adv.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
        adv.GetFileSecurityW(str(path), information, None, 0, ctypes.byref(required))
        descriptor = ctypes.create_string_buffer(required.value)
        check(bool(adv.GetFileSecurityW(str(path), information, descriptor, len(descriptor), ctypes.byref(required))),
              'Read actual protected Windows directory security descriptor')
        sddl = w.LPWSTR()
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, w.DWORD,
            w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.c_void_p]
        kernel.LocalFree.argtypes = [w.HLOCAL]
        try:
            check(bool(adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, information,
                ctypes.byref(sddl), None)), 'Read Windows SDDL')
            value = sddl.value
            check(value.startswith('O:BA'), 'Temporary installer directory owned by Administrators')
            check('D:P' in value and '(A;OICI;FA;;;BA)' in value and '(A;OICI;FA;;;SY)' in value,
                  'Temporary installer directory has protected Administrators/SYSTEM DACL')
            check('(ML;OICI;NW;;;HI)' in value, 'Temporary installer directory enforces high integrity')
            check(';;;BU)' not in value and ';;;WD)' not in value and ';;;AU)' not in value,
                  'Temporary installer directory grants no Users/Everyone/AuthenticatedUsers write access')
        finally:
            if sddl:
                kernel.LocalFree(ctypes.cast(sddl, w.HLOCAL))
    finally:
        path.rmdir()
    result = bootstrap.install(consent=True)
    check(result['state'] == 'ready', 'Official component installation reaches real ready state')
    check(bootstrap.status()['state'] == 'ready', 'Signed installed tools and running manager verified')
    check(native._trusted('wireguard.exe').is_file(), 'Installed WireGuard manager Authenticode accepted')
    check(native._trusted('wg.exe').is_file(), 'Installed WireGuard control tool Authenticode accepted')
    check(not list(native.configs.glob('Akeno*.conf*')), 'Component installation created no AKENO tunnel profile')
    args.report.write_text(json.dumps({'platform': 'real Windows', 'component_installation_only': True,
        'before': before['state'], 'result': result, 'checks': checks, 'passed': len(checks)}, indent=2) + '\n', encoding='utf-8')
    print(f'{len(checks)} real Windows component checks passed; no network exit or UAC interaction claim')


if __name__ == '__main__':
    main()
