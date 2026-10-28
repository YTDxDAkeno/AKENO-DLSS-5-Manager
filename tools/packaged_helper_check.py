"""Exercise the built AKENO.exe's network helper through the Windows "runas" verb (CI, Windows only).

    python tools/packaged_helper_check.py dist/AKENO/AKENO.exe REPORT.json

The GUI never runs elevated: an explicit action writes one per-user DPAPI request and starts AKENO.exe
``--network-helper <id>`` with the runas verb (the UAC prompt on a normal PC; GitHub's runner account is already an
administrator, so no prompt appears there). This check uses that same path with read-only requests:

* a fresh ``status`` request is answered by the elevated packaged process through the DPAPI result store;
* an unknown action, an expired request and a malformed id are refused with fixed messages;
* afterwards no WireGuard tunnel service and no AKENO network adapter exist (nothing was installed or started).

It does not click a UAC prompt, connect a tunnel or prove anything about a VPN exit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def runas(exe: Path, *arguments: str) -> int:
    """Start ``exe`` with the runas verb and wait; PowerShell's Start-Process uses ShellExecuteEx like AKENO."""
    quoted = ','.join("'" + a.replace("'", "''") + "'" for a in arguments)
    script = (f"$p = Start-Process -FilePath '{exe}' -ArgumentList {quoted} -Verb RunAs -WindowStyle Hidden "
              "-Wait -PassThru; exit $p.ExitCode")
    return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], timeout=180).returncode


def ps(command: str) -> str:
    return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True,
                          text=True, timeout=60).stdout.strip()


def main(exe: Path, report: Path) -> int:
    if sys.platform != 'win32':
        raise SystemExit('Windows only.')
    from akeno.network.helper import request_store
    checks = []

    def check(ok, name, detail=''):
        checks.append({'name': name, 'passed': bool(ok), 'detail': str(detail)[:400]})
        print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f': {detail}' if detail != '' else ''), flush=True)
        return ok

    def ask(request):
        ident = uuid.uuid4().hex
        store = request_store(ident)
        store.save(request)
        code = runas(exe, '--network-helper', ident)
        result = store.load()
        store.clear()
        return code, result

    code, result = ask({'action': 'status', 'profile_id': None, 'created': time.time()})
    answered = result.get('done') is True and ('result' in result or 'error' in result)
    check(answered, 'packaged AKENO.exe answered a status request through runas and the DPAPI store',
          {'exit': code, 'state': (result.get('result') or {}).get('state'), 'error': result.get('error')})
    check(not result.get('error') or 'WireGuard' in result['error'],
          'status is read-only: only a missing official WireGuard can stop it', result.get('error', ''))

    code, result = ask({'action': 'shell', 'profile_id': None, 'created': time.time()})
    check(code != 0 and result.get('error') == 'The network request is invalid or expired.',
          'an unknown helper action is refused with a fixed message', result)
    code, result = ask({'action': 'status', 'profile_id': None, 'created': time.time() - 600})
    check(code != 0 and result.get('error') == 'The network request is invalid or expired.',
          'an expired helper request is refused', result)
    code = runas(exe, '--network-helper', 'not-a-request-id')
    check(code != 0, 'a malformed request id is refused before any operation', code)

    services = ps("@(Get-Service -Name 'WireGuardTunnel$Akeno*' -ErrorAction SilentlyContinue).Count")
    adapters = ps("@(Get-NetAdapter -Name 'Akeno*' -IncludeHidden -ErrorAction SilentlyContinue).Count")
    check(services == '0' and adapters == '0', 'no AKENO tunnel service or network adapter exists afterwards',
          f'services={services} adapters={adapters}')
    report.write_text(json.dumps({'platform': 'GitHub Windows runner (administrator, no UAC prompt)',
                                  'checks': checks}, indent=2), encoding='utf-8')
    failed = [c for c in checks if not c['passed']]
    print(f"{len(checks) - len(failed)}/{len(checks)} packaged helper checks passed", flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    # The elevated process does not inherit this process's environment on every Windows configuration, so the
    # request uses the same default per-user AKENO home the installed application uses (a disposable CI runner).
    os.environ.pop('AKENO_HOME', None)
    raise SystemExit(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2])))
