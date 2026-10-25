"""End-to-end update test on a real Windows PC (CI runner): installer, packaged executables, real file locks.

    python tools/update_e2e.py --installer packaging/out/AKENO-Gaming-Suite-3.0.0-Setup-x64.exe \
        --next dist-next/AKENO --work %RUNNER_TEMP%/e2e [--report e2e-report.md]

**Only for a disposable test machine.** It changes the machine: it adds ``akeno.online`` and ``api.akeno.online``
to the hosts file (pointing to this PC), trusts a test certificate authority it creates, serves a local Release
Manager on port 443, installs AKENO for the current user and, in that test installation only, replaces the
release verification key with a test key (the production key and the release signing key are never involved).

What runs where:
* the release information, download, verification and unpacking: the AKENO source of this commit (``akeno.updater``,
  the same code that is inside the build), here in Python on Windows;
* the updater, the health check, the rollback, the recovery and every start: the real packaged ``AKENO.exe`` files
  (the installed release candidate, and ``--next``, a build of the same commit with a higher version number).
The confirmation click on the Updates page is not automated: the updater is started the way the page starts it.

Scenarios: update 3.0.0 → 3.0.1 with a second AKENO window open; broken updates (no window; failing its own start
checks); a wrong build (another version reports back); a power cut right after the folder switch, recovered by the next start;
a manipulated download and a forged signature (nothing changes); the manual rollback; uninstalling. Each checks the
program folder file by file and that user data (license state, settings, themes, wallpapers) is unchanged.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import release_manifest as tool  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402

HOSTS = ("akeno.online", "api.akeno.online")


def bump(version: str, n: int) -> str:
    """The release candidate's version with its patch number raised by ``n`` (3.0.0, 1 -> 3.0.1)."""
    major, minor, patch = (int(x) for x in version.split(".")[:3])
    return f"{major}.{minor}.{patch + n}"
HOSTS_FILE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, ok: bool | None, what: str, detail: str = "") -> bool:
        state = "OK" if ok else ("INFO" if ok is None else "FAIL")
        self.rows.append((state, what, detail))
        print(f"[{state:4}] {what}" + (f": {detail}" if detail else ""), flush=True)
        return bool(ok)

    @property
    def failed(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == "FAIL")

    def markdown(self) -> str:
        lines = ["### Windows update end-to-end test", "", "| Result | Check | Detail |", "|---|---|---|"]
        lines += [f"| {s} | {w} | {d.replace('|', '/')[:300]} |" for s, w, d in self.rows]
        lines += ["", f"**{len(self.rows) - self.failed} passed, {self.failed} failed**"]
        return "\n".join(lines)


# -- the test Release Manager ----------------------------------------------------------------------------------------
def certificates(folder: Path) -> tuple[Path, Path]:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AKENO update test CA (disposable)")])
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(start + timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])]))
            .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca_file, chain = folder / "test-ca.cer", folder / "server.pem"
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.DER))
    chain.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_file, chain


class ReleaseServer:
    """``GET /api/v1/releases/stable`` and ``/downloads/<file>`` on https://(api.)akeno.online (this PC)."""

    def __init__(self, chain: Path, files: Path):
        self.files = files
        self.release: bytes | None = None
        self.tamper: dict[str, bytes] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/v1/releases/stable" and server.release is not None:
                    return self._send(200, server.release, "application/json")
                if self.path.startswith("/downloads/"):
                    name = self.path.rsplit("/", 1)[-1]
                    if name in server.tamper:
                        return self._send(200, server.tamper[name])
                    path = server.files / name
                    if path.is_file():
                        return self._send(200, path.read_bytes())
                self._send(404, b"{}", "application/json")

            def _send(self, code, body, ctype="application/octet-stream"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 443), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(chain)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def package(dist: Path, out: Path, version: str, drop: tuple[str, ...] = (), extra: dict | None = None) -> Path:
    """A portable ZIP of a program folder (files at the top, like the CI's Compress-Archive)."""
    path = out / f"AKENO-Gaming-Suite-{version}-portable-x64.zip"
    dropped = set()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for f in sorted(dist.rglob("*")):
            rel = f.relative_to(dist).as_posix()
            if not f.is_file():
                continue
            hit = [d for d in drop if rel.lower().endswith(d.lower())]
            if hit:
                dropped.update(hit)
                continue
            z.write(f, rel)
        for rel, data in (extra or {}).items():
            z.writestr(rel, data)
    if set(drop) - dropped:                     # the scenario would not test what it says
        raise AssertionError(f"not in the build, so not removed: {sorted(set(drop) - dropped)}")
    return path


def content_aggregate(pkg: Path) -> str:
    """content_sha256 as the Release Manager defines it, written here independently of the client's code: per
    regular file the name as stored, SHA-256 and size of the decompressed bytes; sorted by code point; compact
    JSON with sorted keys, UTF-8, not ASCII-escaped, no trailing newline."""
    records = []
    with zipfile.ZipFile(pkg) as z:
        for info in z.infolist():
            if not info.is_dir():
                data = z.read(info)
                records.append({"name": info.filename, "sha256": hashlib.sha256(data).hexdigest(),
                                "size_bytes": len(data)})
    records.sort(key=lambda r: r["name"])
    return hashlib.sha256(json.dumps(records, ensure_ascii=False, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8")).hexdigest()


def signed_release(key, version: str, sequence: int, pkg: Path, portable_changes: dict | None = None) -> bytes:
    data = pkg.read_bytes()
    with zipfile.ZipFile(pkg) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
    issued = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    auth = {"verified": False, "status": "not_signed", "publisher_certificate_sha256": None}
    portable = {"download_url": f"https://akeno.online/downloads/{pkg.name}", "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "authenticode": auth, "pe_files": 1,
                "verified_pe_files": 0, "file_count": len(infos),
                "uncompressed_size_bytes": sum(i.file_size for i in infos), "content_sha256": content_aggregate(pkg)}
    for name, change in (portable_changes or {}).items():      # a value, or a function of the real value
        portable[name] = change(portable[name]) if callable(change) else change
    m = {"schema_version": 2, "updater_protocol": 2, "product": "akeno-gaming-suite", "channel": "stable",
         "platform": "windows-x64", "version": version, "release_notes": f"AKENO {version} (update test)",
         "release_date": issued.date().isoformat(), "published_at": iso(issued), "issued_at": iso(issued),
         "expires_at": iso(issued + timedelta(days=7)), "sequence": sequence, "request_id": hashlib.md5(
             f"{version}{sequence}".encode()).hexdigest(),
         "download_url": f"https://akeno.online/downloads/AKENO-Gaming-Suite-{version}-Setup-x64.exe",
         "size_bytes": 1, "sha256": "0" * 64, "authenticode": auth, "minimum_supported_version": None,
         "unattended_install_allowed": False, "install_mode": "portable-directory-swap", "portable": portable}
    payload = json.dumps(m, sort_keys=True, separators=(",", ":"))
    sig = base64.urlsafe_b64encode(key.sign(payload.encode())).rstrip(b"=").decode()
    doc = dict(m, signed_payload=payload, signature={"algorithm": "Ed25519", "key_id": tool.kid_of(tool.public_of(key)),
                                                     "value": sig})
    return json.dumps(doc).encode()


# -- the machine -----------------------------------------------------------------------------------------------------
def prepare_machine(work: Path) -> tuple[Path, Path]:
    ca, chain = certificates(work)
    subprocess.run(["certutil", "-f", "-addstore", "Root", str(ca)], check=True, capture_output=True)
    text = HOSTS_FILE.read_text(encoding="ascii", errors="ignore")
    lines = [f"127.0.0.1 {h}  # AKENO update test" for h in HOSTS if f" {h}  # AKENO update test" not in text]
    if lines:
        HOSTS_FILE.write_text(text.rstrip("\r\n") + "\r\n" + "\r\n".join(lines) + "\r\n", encoding="ascii")
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
    return ca, chain


def restore_machine(ca: Path | None) -> None:
    try:
        text = HOSTS_FILE.read_text(encoding="ascii", errors="ignore")
        HOSTS_FILE.write_text("\r\n".join(x for x in text.splitlines() if "# AKENO update test" not in x) + "\r\n",
                              encoding="ascii")
    except OSError:
        pass
    if ca is not None:
        serial = x509.load_der_x509_certificate(ca.read_bytes()).serial_number
        subprocess.run(["certutil", "-delstore", "Root", format(serial, "x")], capture_output=True)


def run_exe(exe: Path, *args: str, env: dict | None = None) -> subprocess.Popen:
    flags = 0x00000008 | 0x00000200        # detached, like the updater starts it
    return subprocess.Popen([str(exe), *args], env=env or dict(os.environ), creationflags=flags, close_fds=True,
                            cwd=str(exe.parent))


def end_all(folder: Path) -> None:
    from akeno.updater import apply
    apply.stop_processes(folder, grace=2)


def file_version(exe: Path) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Item -LiteralPath '{exe}').VersionInfo"
                          ".ProductVersion"], capture_output=True, text=True)
    return out.stdout.strip()


def runonce() -> str | None:
    import winreg
    from akeno.updater import apply
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, apply.RUNONCE_KEY) as key:
            return winreg.QueryValueEx(key, apply.RUNONCE_VALUE)[0]
    except OSError:
        return None


def display_version(target: Path) -> str | None:
    import winreg
    from akeno.updater import apply
    want = os.path.normcase(os.path.normpath(str(target)))
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, apply.UNINSTALL_KEY) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            with winreg.OpenKey(root, winreg.EnumKey(root, i)) as key:
                try:
                    if os.path.normcase(os.path.normpath(winreg.QueryValueEx(key, "InstallLocation")[0])) == want:
                        return winreg.QueryValueEx(key, "DisplayVersion")[0]
                except OSError:
                    continue
    return None


# -- user data -------------------------------------------------------------------------------------------------------
def seed_user_data() -> dict:
    from akeno.core.db import Database
    from akeno.core.settings import Settings
    from akeno.licensing.storage import SecureStore
    from akeno.paths import app_home, db_path
    settings = Settings(Database(db_path()))
    settings.set("theme_accent", "cyan")
    settings.set("language", "de")
    settings.set("update_background_download", True)
    SecureStore().save({"e2e": "license state kept", "remember": True})
    home = app_home()
    files = {"desktop/themes/e2e-theme.json": b'{"name": "e2e"}', "desktop/wallpapers/e2e.png": os.urandom(4096),
             "e2e-user-file.bin": os.urandom(1024)}
    for rel, data in files.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_bytes(data)
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}


def user_data_intact(report: Report, seeded: dict, when: str) -> None:
    from akeno.core.db import Database
    from akeno.core.settings import Settings
    from akeno.licensing.storage import SecureStore
    from akeno.paths import app_home, db_path
    home = app_home()
    files = all((home / rel).is_file() and hashlib.sha256((home / rel).read_bytes()).hexdigest() == h
                for rel, h in seeded.items())
    report.add(files, f"{when}: themes, wallpapers and user files unchanged", f"{len(seeded)} files, SHA-256")
    store = SecureStore()
    try:
        state = store._decode(store.path.read_bytes())
        report.add(state.get("e2e") == "license state kept", f"{when}: license state kept and decryptable (DPAPI)")
    except Exception as exc:
        report.add(False, f"{when}: license state kept and decryptable (DPAPI)", str(exc))
    s = Settings(Database(db_path()))
    report.add(s.get("theme_accent") == "cyan" and s.get("language") == "de", f"{when}: settings kept",
               f"theme {s.get('theme_accent')}, language {s.get('language')}")


# -- the scenarios ---------------------------------------------------------------------------------------------------
class E2E:
    def __init__(self, args, report: Report):
        from akeno.paths import sub
        self.args, self.report = args, report
        self.work = Path(args.work)
        self.target = self.work / "Programs" / "AKENO Gaming Suite"
        self.files = self.work / "served"
        self.files.mkdir(parents=True, exist_ok=True)
        self.key = Ed25519PrivateKey.generate()
        self.public = tool.public_of(self.key)
        self.kid = tool.kid_of(self.public)
        self.folder = sub("updates")
        self.server: ReleaseServer | None = None
        self.sequence = 0
        from akeno import __version__
        self.rc = __version__                                 # the release candidate the installer installs
        self.installed_version = self.rc

    def service(self):
        from akeno.licensing import crypto
        from akeno.updater import service
        service.install_dir = lambda: self.target          # the orchestrator plays the installed AKENO
        return service.UpdateService(None, keys={self.kid: crypto.public_key(self.public)},
                                     version=self.installed_version, folder=self.folder)

    def install(self) -> bool:
        r = self.report
        p = subprocess.run([str(self.args.installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
                            f"/DIR={self.target}", f"/LOG={self.work / 'setup.log'}"], timeout=600)
        if not r.add(p.returncode == 0 and (self.target / "AKENO.exe").is_file(), "installer (per user, silent)",
                     f"exit code {p.returncode}, {self.target}"):
            return False
        r.add((self.target / "unins000.exe").is_file(), "installer wrote its uninstaller into the program folder")
        r.add(file_version(self.target / "AKENO.exe") == self.rc, f"installed AKENO.exe is {self.rc}",
              file_version(self.target / "AKENO.exe"))
        keys = self.target / "_internal" / "akeno" / "data" / "release-keys.json"
        if not r.add(keys.is_file(), "release keys file inside the installed build", str(keys)):
            return False
        keys.write_text(json.dumps({"keys": [{"kid": self.kid, "public_key": self.public}]}), encoding="utf-8")
        r.add(None, "test installation only: release key replaced by a disposable test key", self.kid)
        self.rc_exe = hashlib.sha256((self.target / "AKENO.exe").read_bytes()).hexdigest()
        return True

    def publish(self, version: str, dist: Path, drop=(), extra=None, tamper: bool = False,
                portable_changes: dict | None = None) -> Path:
        self.sequence += 1
        pkg = package(dist, self.files, version, drop, extra)
        self.server.release = signed_release(self.key, version, self.sequence, pkg, portable_changes)
        self.server.tamper = {}
        if tamper:                              # one byte in the middle flipped: same size, other content
            data = bytearray(pkg.read_bytes())
            data[len(data) // 2] ^= 0xFF
            assert bytes(data) != pkg.read_bytes()
            self.server.tamper = {pkg.name: bytes(data)}
        return pkg

    def start_old(self, second: bool = True) -> list[subprocess.Popen]:
        procs = [run_exe(self.target / "AKENO.exe")]
        if second:
            procs.append(run_exe(self.target / "AKENO.exe"))   # a second window: the updater must end it too
        time.sleep(12)
        return procs

    def apply(self, version: str, **plan_extra) -> tuple[dict | None, dict]:
        from akeno.updater import staging
        svc = self.service()
        release = svc.check()
        if release is None or release.release != version:
            raise AssertionError(f"the service did not offer {version}: {release}")
        plan = svc.prepare(release)
        content = staging.load(plan["content"])
        procs = self.start_old()
        plan.update(pid=procs[0].pid, timeout=180)
        plan.update(plan_extra)                 # a scenario may set its own timeout
        result = Path(plan["result"])
        result.unlink(missing_ok=True)
        svc.launch(plan)
        procs[0].terminate()                    # the Updates page quits AKENO right after starting the updater
        end = time.monotonic() + 420
        while time.monotonic() < end and not result.exists():
            if plan_extra.get("test_crash_after") and self._updater_gone():
                break
            time.sleep(1)
        outcome = json.loads(result.read_text(encoding="utf-8")) if result.exists() else None
        return outcome, {"plan": plan, "content": content, "svc": svc}

    def _updater_gone(self) -> bool:
        import psutil
        time.sleep(2)
        for p in psutil.process_iter(["cmdline"]):
            try:
                if "--apply-update" in (p.info.get("cmdline") or []):
                    return False
            except psutil.Error:
                continue
        return True

    def program_is(self, content: dict, what: str) -> bool:
        from akeno.updater import staging
        try:
            staging.verify(self.target, content, extra_allowed=("unins000.exe", "unins000.dat", "unins000.msg"))
            return self.report.add(True, what, f"{len(content['files'])} files match SHA-256")
        except staging.StagingError as exc:
            return self.report.add(False, what, str(exc))

    def report_start(self, svc) -> None:
        """What the started suite does after an update: report the outcome (here without a license)."""
        svc.after_start()

    def run(self) -> None:
        r = self.report
        from akeno.updater import apply

        def v(n: int) -> str:
            return bump(self.rc, n)
        seeded = seed_user_data()
        if not self.install():
            return
        user_data_intact(r, seeded, "after installation")
        next_dist = Path(self.args.next)

        # 1. 3.0.0 -> 3.0.1 with a second window open
        # a file with a non-ASCII name (stored at the end, out of sorted order): content_sha256 by code point
        self.publish(v(1), next_dist, extra={"docs/e2e-ünïcode-日本.txt": "AKENO ✓".encode()})
        outcome, ctx = self.apply(v(1))
        ok = r.add(bool(outcome and outcome.get("ok")), f"update {self.rc} → {v(1)} installed and passed its health check",
                   json.dumps(outcome))
        phases = [p for p, _ in apply.Journal(self.folder / "journal.json").load().get("history", [])]
        r.add(phases[-8:] == ["prepared", "stopping", "copying", "swapping", "swapped", "health", "committing",
                              "committed"], "journal phases of the update", " → ".join(phases[-8:]))
        self.program_is(ctx["content"], f"program folder is exactly the verified {v(1)} package")
        r.add(file_version(self.target / "AKENO.exe") == v(1), f"installed AKENO.exe reports {v(1)}")
        r.add(bool(apply.processes_in(self.target)), f"{v(1)} is running after the update (started by the updater)")
        prev = self.folder / "previous" / "AKENO.exe"
        r.add(prev.is_file() and hashlib.sha256(prev.read_bytes()).hexdigest() == self.rc_exe,
              f"previous version {self.rc} kept for a rollback")
        r.add((self.target / "unins000.exe").is_file(), "uninstaller kept in the program folder")
        r.add(display_version(self.target) == v(1), f"Windows shows {v(1)} in Installed apps",
              str(display_version(self.target)))
        r.add(runonce() is None, "no recovery entry left in RunOnce")
        user_data_intact(r, seeded, "after the update")
        end_all(self.target)
        self.report_start(ctx["svc"])
        if not ok:
            return
        self.installed_version = v(1)
        good = ctx["content"]

        # 2a. a broken update: the new program's window cannot start (Qt missing); its updater still runs
        self.publish(v(2), next_dist, drop=("PySide6/Qt6Core.dll",))
        outcome, ctx = self.apply(v(2), timeout=90)
        r.add(bool(outcome and outcome.get("rolled_back")), f"broken {v(2)} (no window, no report) rolled back",
              (outcome or {}).get("error", ""))
        self.program_is(good, f"program folder is {v(1)} again, file by file")
        r.add(bool(apply.processes_in(self.target)), f"{v(1)} started again after the rollback")
        user_data_intact(r, seeded, "after the rollback")
        end_all(self.target)
        self.report_start(ctx["svc"])

        # 2b. a broken update that starts but fails its own checks (program data missing)
        self.publish(v(3), next_dist, drop=("akeno/data/licensing.json",))
        outcome, ctx = self.apply(v(3))
        r.add(bool(outcome and outcome.get("rolled_back") and "program_data" in outcome.get("error", "")),
              f"{v(3)} failing its own start checks is rolled back", (outcome or {}).get("error", ""))
        self.program_is(good, f"program folder is {v(1)} again")
        end_all(self.target)
        self.report_start(ctx["svc"])

        # 3. a wrong build: another version reports back
        self.publish(v(4), next_dist, extra={f"e2e-{v(4)}.txt": b"wrong build"})
        outcome, ctx = self.apply(v(4))
        r.add(bool(outcome and outcome.get("rolled_back") and "version" in outcome.get("error", "")),
              "a build that reports another version is rolled back", (outcome or {}).get("error", ""))
        self.program_is(good, f"program folder is {v(1)} again")
        end_all(self.target)
        self.report_start(ctx["svc"])

        # 4. a power cut right after the folder switch; the next start of AKENO finishes it
        self.publish(v(5), next_dist, extra={f"e2e-{v(5)}.txt": b"interrupted"})
        outcome, ctx = self.apply(v(5), test_crash_after="swapped")
        journal = apply.Journal(self.folder / "journal.json").load()
        r.add(outcome is None and journal.get("phase") == "swapped",
              "power cut simulated right after the switch (updater gone, journal says swapped)", journal.get("phase"))
        r.add(bool(runonce()), "a one-time recovery is registered for the next sign-in (RunOnce)", runonce() or "")
        end_all(self.target)
        run_exe(self.target / "AKENO.exe")       # the user starts AKENO again
        end = time.monotonic() + 240
        while time.monotonic() < end and apply.Journal(self.folder / "journal.json").load().get("phase") != "rolled_back":
            time.sleep(1)
        r.add(apply.Journal(self.folder / "journal.json").load().get("phase") == "rolled_back",
              "the next start handed over to the staged updater, which restored the previous version")
        time.sleep(5)
        self.program_is(good, f"program folder is {v(1)} again after the recovery")
        r.add(runonce() is None, "recovery entry removed")
        user_data_intact(r, seeded, "after the recovery")
        end_all(self.target)
        self.report_start(ctx["svc"])

        # 5. a manipulated download and a forged signature: nothing changes
        svc = self.service()
        self.publish(v(6), next_dist, tamper=True)
        try:
            svc.prepare(svc.check())
            r.add(False, "a manipulated package is refused")
        except Exception as exc:
            r.add("SHA-256" in str(exc), "a manipulated package is refused before unpacking", str(exc)[:160])
        forged = Ed25519PrivateKey.generate()
        self.sequence += 1
        self.server.release = signed_release(forged, v(7), self.sequence, self.files / next(
            p.name for p in self.files.glob(f"*{v(6)}*")))
        try:
            svc.check()
            r.add(False, "a release with another signature is refused")
        except Exception as exc:
            r.add("does not trust" in str(exc), "a release signed with another key is refused", str(exc)[:160])
        self.program_is(good, f"program folder still {v(1)}")

        # 5b. correct package, but its signed description does not match it: refused before anything is used
        for n, changes, expect, what in (
                (8, {"content_sha256": "0" * 64}, "content_sha256", "a wrong signed content_sha256"),
                (9, {"file_count": lambda real: real + 2}, "files", "a wrong signed file count"),
                (10, {"uncompressed_size_bytes": lambda real: real + 1}, "bytes", "a wrong signed unpacked size")):
            svc = self.service()
            self.publish(v(n), next_dist, extra={"docs/e2e-ünïcode-日本.txt": "AKENO ✓".encode()},
                         portable_changes=changes)
            try:
                svc.prepare(svc.check())
                r.add(False, f"{what} is refused")
            except Exception as exc:
                r.add(expect in str(exc), f"{what} is refused at staging (test-signed release)", str(exc)[:160])
            r.add(not (self.folder / "staging" / v(n)).exists(), f"{what}: nothing is left in the staging folder")
        self.program_is(good, f"program folder still {v(1)} after the refused releases")

        # 6. the manual rollback to the kept version
        svc = self.service()
        plan = svc.rollback_plan()
        plan.update(pid=None, timeout=180)
        result = Path(plan["result"])
        result.unlink(missing_ok=True)
        svc.launch(plan)
        end = time.monotonic() + 420
        while time.monotonic() < end and not result.exists():
            time.sleep(1)
        outcome = json.loads(result.read_text(encoding="utf-8")) if result.exists() else {}
        r.add(bool(outcome.get("ok")), f"manual rollback to {self.rc} passed its health check", json.dumps(outcome))
        r.add(hashlib.sha256((self.target / "AKENO.exe").read_bytes()).hexdigest() == self.rc_exe,
              "program folder holds the release candidate's AKENO.exe again")
        user_data_intact(r, seeded, "after the manual rollback")
        end_all(self.target)

        # 7. uninstalling keeps the user data
        p = subprocess.run([str(self.target / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                           timeout=300)
        end = time.monotonic() + 180            # the uninstaller continues from a copy of itself
        while time.monotonic() < end and (self.target / "AKENO.exe").exists():
            time.sleep(1)
        r.add(p.returncode == 0 and not (self.target / "AKENO.exe").exists(), "uninstaller removes the program",
              f"exit code {p.returncode}")
        user_data_intact(r, seeded, "after uninstalling")


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        print("this test runs on a disposable Windows machine only")
        return 2
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--installer", type=Path, required=True)
    ap.add_argument("--next", type=Path, required=True, help="program folder of the next version (3.0.1 build)")
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):        # the CI console is cp1252; the report has arrows
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args.work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    report = Report()
    ca = None
    try:
        ca, chain = prepare_machine(args.work)
        e2e = E2E(args, report)
        e2e.server = ReleaseServer(chain, e2e.files)
        e2e.run()
    except Exception as exc:                        # a crash of the test itself is a failed check, with its reason
        import traceback
        traceback.print_exc()
        report.add(False, "the test itself stopped", f"{type(exc).__name__}: {exc}")
    finally:
        restore_machine(ca)
    text = report.markdown()
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
