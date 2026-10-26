"""Gaming & Mod Security Scanner: what a file is, where it came from and what the antivirus says about it.

Per file: the real type from its first bytes (not the name), SHA-256, the vendor fields of a Windows program, its
digital signature, the download origin Windows recorded (Mark of the Web), the antivirus verdict and, for archives,
the programs and scripts inside. Nothing is opened in a program, extracted, run, changed or uploaded.

What counts as a warning has a stated reason: a detection by the antivirus, a broken signature, a program that
pretends to be a document ("readme.txt.exe", a ".jar" that is a Windows program), a shortcut that starts a
command interpreter, and script or screensaver files inside a mod archive. A missing signature is *not* a
warning: most mods and many good tools are unsigned.
"""

from __future__ import annotations

import configparser
import hashlib
import ntpath
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from akeno.i18n import msg
from akeno.paths import is_windows
from akeno.system import fs, lnk
from akeno.system.mods.archive import ArchiveError, open_archive
from akeno.system.model import Finding, Level, human_bytes
from akeno.system.tasks import Cancelled, LimitReached, TaskContext, TimedOut, context

MAX_FILES = 3000                 # files looked at below one folder
MAX_HASH = 4 * 2 ** 30           # larger files are reported without a hash
MAX_LISTED = 40                  # archive entries shown per file

MAX_HASH_TOTAL = 16 * 2 ** 30    # hashing budget for one folder check
EXEC_EXT = {".exe", ".dll", ".asi", ".sys", ".scr", ".com", ".cpl", ".ocx", ".ax", ".drv", ".efi", ".node", ".pyd",
            ".xll", ".pif"}
SCRIPT_EXT = {".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".reg"}
INSTALLER_EXT = {".msi", ".msp", ".msix", ".appx", ".appxbundle", ".msixbundle"}
ARCHIVE_EXT = {".zip", ".jar", ".7z", ".rar", ".mrpack"}      # not Unreal .pak files: game data
NATIVE_EXT = {".dll", ".so", ".dylib", ".jnilib"}
# File types people open without thinking: a program dressed up as one of them is a classic trick
DECOY_EXT = {".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".rtf", ".jpg", ".jpeg", ".png", ".gif", ".bmp",
             ".webp", ".mp3", ".mp4", ".avi", ".mkv", ".mov", ".wav", ".zip", ".rar", ".7z", ".jar", ".json",
             ".cfg", ".ini", ".log", ".md", ".html", ".htm"}
LAUNCH_EXT = {".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".hta",
              ".lnk", ".ps1", ".msi"}
RISKY_IN_ARCHIVE = {".scr", ".com", ".pif", ".lnk", ".hta", ".vbs", ".vbe", ".jse", ".wsf", ".wsh"}
PROGRAMS_IN_ARCHIVE = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".reg"}
INTERPRETERS = {"cmd.exe", "powershell.exe", "pwsh.exe", "mshta.exe", "wscript.exe", "cscript.exe", "rundll32.exe",
                "regsvr32.exe", "certutil.exe", "bitsadmin.exe", "msiexec.exe", "conhost.exe"}
ZONES = {0: "this computer", 1: "the local network", 2: "trusted sites", 3: "the internet", 4: "restricted sites"}


@dataclass
class FileReport:
    path: str
    size: int = 0
    kind: str = "data"           # program | library | archive | java_archive | script | installer | shortcut | data
    sha256: str | None = None
    pe: dict[str, Any] = field(default_factory=dict)
    signature: dict[str, Any] = field(default_factory=dict)
    origin: dict[str, Any] | None = None
    av: dict[str, Any] = field(default_factory=dict)        # {"status", "engine", "threats", "detail"}
    contents: list[dict[str, Any]] = field(default_factory=list)
    shortcut: dict[str, Any] | None = None
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return ntpath.basename(self.path)

    @property
    def level(self) -> Level:
        return max((f.level for f in self.findings), key=lambda lv: lv.rank, default=Level.OK)

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in ("path", "size", "kind", "sha256", "pe", "signature", "origin", "av",
                                            "contents", "shortcut")}
        d["findings"] = [f.to_dict() for f in self.findings]
        d["errors"] = [str(e) for e in self.errors]
        return d


KIND_TEXT = {"program": "Windows program", "library": "Windows library (DLL)", "archive": "Archive",
             "java_archive": "Java archive (JAR)", "script": "Script", "installer": "Installer package",
             "shortcut": "Shortcut", "data": "Data file", "folder": "Folder"}


def ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def sniff(path: str) -> tuple[str, bytes]:
    """The type from the first bytes; scripts and shortcuts by name when the content has no signature."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        head = b""
    ext = ext_of(path)
    if head[:2] == b"MZ":
        return "pe", head
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return ("java_archive" if ext == ".jar" else "archive"), head
    if head[:6] == b"7z\xbc\xaf\x27\x1c" or head[:7] == b"Rar!\x1a\x07":
        return "archive_other", head
    if head[:4] == b"L\x00\x00\x00" and head[4:20] == lnk.LINK_CLSID:
        return "shortcut", head
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return ("installer" if ext in INSTALLER_EXT else "ole"), head
    if ext in SCRIPT_EXT:
        return "script", head
    if ext in INSTALLER_EXT:
        return "installer", head
    return "data", head


def sha256(path: str, ctx: TaskContext) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            ctx.check()
    return h.hexdigest()


def zone(path: str) -> dict[str, Any] | None:
    """The Mark of the Web Windows stores beside a downloaded file (``Zone.Identifier`` stream)."""
    if not is_windows():
        return None
    try:
        with open(path + ":Zone.Identifier", "r", encoding="utf-8", errors="replace") as f:
            text = f.read(16384)
    except OSError:
        return None
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(text)
    except configparser.Error:
        return None
    sec = cp["ZoneTransfer"] if cp.has_section("ZoneTransfer") else {}
    try:
        zone_id = int(sec.get("ZoneId", ""))
    except ValueError:
        zone_id = None
    return {"zone": zone_id, "host_url": sec.get("HostUrl"), "referrer": sec.get("ReferrerUrl")}


def double_extension(name: str) -> bool:
    """"readme.txt.exe", "photo.jpg .scr": a launchable type behind a decoy type."""
    base, last = os.path.splitext(name.lower().rstrip(" ."))
    if last not in LAUNCH_EXT:
        return False
    inner = os.path.splitext(base.rstrip(" "))[1]
    return inner in DECOY_EXT


def _pe_info(path: str) -> dict[str, Any]:
    from akeno.games import pe
    info = pe.parse(path)
    if not info.valid:
        return {"valid": False, "error": info.error}
    s = info.strings
    return {"valid": True, "machine": info.machine, "dll": info.is_dll, "version": info.file_version,
            "company": s.get("CompanyName"), "product": s.get("ProductName"),
            "description": s.get("FileDescription"), "original_name": s.get("OriginalFilename")}


def _archive_contents(path: str, kind: str) -> tuple[list[dict[str, Any]], list[str]]:
    out, errors = [], []
    try:
        with open_archive(path) as a:
            for name in a.names:
                if name.endswith("/"):
                    continue
                base = name.rsplit("/", 1)[-1]
                ext = ext_of(base)
                if ext in RISKY_IN_ARCHIVE or ext in PROGRAMS_IN_ARCHIVE or ext in NATIVE_EXT or \
                        ext in {".asi", ".sys", ".jar"} or double_extension(base):
                    out.append({"name": name, "ext": ext, "double": double_extension(base)})
    except ArchiveError as exc:
        errors.append(msg("Archive not readable: {error}", error=str(exc)))
    return out, errors


def inspect(path: str, ctx: TaskContext | None = None, hash_limit: int = MAX_HASH) -> FileReport:
    """Everything except the signature and the antivirus verdict, which are batched across files."""
    ctx = context(ctx)
    rep = FileReport(path)
    try:
        st = os.lstat(path)
    except OSError as exc:
        rep.errors.append(str(exc.strerror or exc))
        return rep
    rep.size = st.st_size
    if fs.is_reparse(path, st):
        rep.errors.append(msg("Link or junction: not followed."))
        return rep
    sniffed, _ = sniff(path)
    ext = ext_of(path)
    if sniffed == "pe":
        rep.pe = _pe_info(path)
        rep.kind = "library" if rep.pe.get("dll") or ext in {".dll", ".asi"} else "program"
    elif sniffed in ("archive", "java_archive"):
        rep.kind = sniffed
        rep.contents, errs = _archive_contents(path, sniffed)
        rep.errors += errs
    elif sniffed == "archive_other":
        rep.kind = "archive"
        rep.errors.append(msg("7z and RAR archives are not listed; the antivirus still checks them."))
    elif sniffed == "shortcut":
        rep.kind = "shortcut"
        sc = lnk.read(path)
        if sc is not None:
            rep.shortcut = {"target": sc.target, "arguments": sc.arguments[:500]}
    elif sniffed in ("installer", "ole") and ext in INSTALLER_EXT:
        rep.kind = "installer"
    elif sniffed == "script":
        rep.kind = "script"
    if st.st_size <= hash_limit:
        try:
            rep.sha256 = sha256(path, ctx)
        except OSError as exc:
            rep.errors.append(msg("Not readable: {error}", error=str(exc.strerror or exc)))
    else:
        rep.errors.append(msg("Too large to hash in this check ({size}).", size=human_bytes(st.st_size)))
    rep.origin = zone(path)
    return rep


def wants_signature(rep: FileReport) -> bool:
    return rep.kind in ("program", "library", "installer") or (rep.kind == "script" and ext_of(rep.path) in
                                                                  {".ps1", ".psm1", ".vbs", ".js", ".wsf"})


def judge(rep: FileReport, minecraft: bool = False) -> list[Finding]:
    """The findings for one inspected file (signature and antivirus verdict already filled in)."""
    out: list[Finding] = []
    name, ext = rep.name, ext_of(rep.name)
    fid = "file." + (rep.sha256 or name)[:16]

    av = rep.av or {}
    if av.get("status") == "threats":
        names = ", ".join(t.get("name", "?") for t in av.get("threats", [])) or msg("a threat")
        out.append(Finding(fid + ".av", "security", Level.PROBLEM,
                           msg("{engine} reports {threat} in {file}", engine=av.get("engine", "Antivirus"),
                               threat=names, file=name),
                           msg("The antivirus recognised this file as harmful."),
                           msg("Do not run or install it. Let Microsoft Defender handle it with a normal scan "
                               "(it then appears in Protection history). AKENO does not delete or move files."))
                   .measured(msg("Verdict of {engine} (check only, nothing changed)", engine=av.get("engine", "")),
                             "MpCmdRun -DisableRemediation")
                   .link("security", msg("Scan with Microsoft Defender"), "defender-file:" + rep.path))

    sig = rep.signature or {}
    if sig.get("status") == "invalid":
        out.append(Finding(fid + ".sig", "security", Level.WARN,
                           msg("{file} has a broken digital signature", file=name),
                           msg("The file was changed after it was signed, or the signature is not trusted: {detail}",
                               detail=sig.get("detail") or "-"),
                           msg("Get the file again from its original source and compare it with the vendor's "
                               "checksum."))
                   .measured(msg("Signature check by Windows"), "Get-AuthenticodeSignature"))

    if rep.kind in ("program", "library") and ext in DECOY_EXT:
        out.append(Finding(fid + ".decoy", "security", Level.WARN,
                           msg("{file} is a Windows program, not a {ext} file", file=name, ext=ext),
                           msg("The name suggests a harmless file type, the content is an executable program."),
                           msg("Do not open it. Check where it came from."))
                   .measured(msg("File content starts with a Windows program header (MZ/PE)"), "file header"))

    if double_extension(name):
        out.append(Finding(fid + ".double", "security", Level.WARN,
                           msg("{file} hides its real type behind a second extension", file=name),
                           msg("Windows shows only the first part when extensions are hidden, so it looks like "
                               "a document or picture."),
                           msg("Do not open it unless you know exactly what it is.")))

    if rep.shortcut:
        target = rep.shortcut.get("target") or ""
        if ntpath.basename(target).lower() in INTERPRETERS:
            out.append(Finding(fid + ".lnk", "security", Level.WARN,
                               msg("The shortcut {file} starts {program}", file=name,
                                   program=ntpath.basename(target)),
                               msg("Arguments: {args}", args=(rep.shortcut.get("arguments") or "-")[:200]),
                               msg("Shortcuts in downloads that start a command interpreter are a common way to "
                                   "run hidden commands. Do not open it unless you made it yourself."))
                       .measured(msg("Shortcut target read from the file"), "MS-SHLLINK"))

    risky = [c for c in rep.contents if c["ext"] in RISKY_IN_ARCHIVE or c["double"]]
    programs = [c for c in rep.contents if c["ext"] in PROGRAMS_IN_ARCHIVE and c not in risky]
    native = [c for c in rep.contents if c["ext"] in NATIVE_EXT]
    if risky:
        out.append(Finding(fid + ".risky", "security", Level.WARN,
                           msg("{file} contains files that start programs directly", file=name),
                           ", ".join(c["name"] for c in risky[:8]),
                           msg("Screensavers, shortcuts, HTML applications and Windows scripts have no place in "
                               "a mod. Do not extract or open them; check the source of the archive."))
                   .measured(msg("Such entries in the archive: {n}", n=len(risky)), "archive listing"))
    if programs:
        level = Level.WARN if rep.kind == "java_archive" or minecraft else Level.INFO
        out.append(Finding(fid + ".programs", "security", level,
                           msg("{file} contains programs or scripts", file=name),
                           ", ".join(c["name"] for c in programs[:8]),
                           msg("A Minecraft mod contains Java classes and resources, not Windows programs: check "
                               "where it comes from.") if level == Level.WARN else
                           msg("Common for tools and installers; AKENO does not run them. Make sure the archive "
                               "comes from the mod's official page."))
                   .measured(msg("Entries of this kind in the archive: {n}", n=len(programs)), "archive listing"))
    if native and rep.kind == "java_archive":
        out.append(Finding(fid + ".native", "security", Level.INFO,
                           msg("{file} brings native libraries", file=name),
                           ", ".join(c["name"] for c in native[:6]),
                           msg("Some mods need them (for example Discord status or controller support). Without "
                               "such a feature, ask where the mod comes from.")))

    origin = rep.origin or {}
    if origin.get("zone") == 4:
        out.append(Finding(fid + ".zone", "security", Level.WARN,
                           msg("{file} came from a site in the restricted zone", file=name),
                           origin.get("host_url") or "", msg("Windows marked the source as untrusted.")))
    return out


def summary_rows(rep: FileReport) -> list[tuple[str, str]]:
    """Label / value pairs for the detail view."""
    sig = rep.signature or {}
    sig_text = {"valid": msg("Valid: {signer}", signer=sig.get("signer") or "?"),
                "unsigned": msg("Not signed (common for mods; not a sign of malware by itself)"),
                "invalid": msg("Broken: {detail}", detail=sig.get("detail") or "-"),
                "unknown": msg("Not checked: {detail}", detail=sig.get("detail") or "-")}.get(sig.get("status"),
                                                                                              msg("Not applicable"))
    av = rep.av or {}
    av_text = {"clean": msg("{engine}: nothing found", engine=av.get("engine", "")),
               "threats": msg("{engine}: {threats}", engine=av.get("engine", ""),
                              threats=", ".join(t.get("name", "?") for t in av.get("threats", []))),
               }.get(av.get("status"), msg("Not checked: {reason}", reason=av.get("detail") or "-"))
    origin = rep.origin
    if origin is None:
        origin_text = msg("No download origin recorded")
    else:
        where = ZONES.get(origin.get("zone"), "?")
        origin_text = msg("From {zone}", zone=msg(where)) + (f" ({origin['host_url']})" if origin.get("host_url")
                                                              else "")
    rows = [(msg("Type"), msg(KIND_TEXT.get(rep.kind, rep.kind))), (msg("Size"), human_bytes(rep.size)),
            ("SHA-256", rep.sha256 or msg("not calculated")), (msg("Signature"), sig_text),
            (msg("Antivirus"), av_text), (msg("Origin"), origin_text)]
    pe = rep.pe or {}
    if pe.get("valid"):
        rows.insert(2, (msg("Vendor"), pe.get("company") or msg("not stated")))
        rows.insert(3, (msg("Product"), " ".join(x for x in (pe.get("product"), pe.get("version")) if x) or "-"))
        rows.insert(4, (msg("Architecture"), pe.get("machine") or "-"))
    if rep.shortcut:
        rows.append((msg("Shortcut target"), rep.shortcut.get("target") or "-"))
    if rep.contents:
        rows.append((msg("Programs and libraries inside"), ", ".join(c["name"] for c in rep.contents[:MAX_LISTED])))
    return rows


Scanner = Callable[[str, TaskContext], dict[str, Any]]
Signer = Callable[[list[str], TaskContext], dict[str, Any]]


def expand(paths: list[str], ctx: TaskContext, errors: list[str]) -> list[str]:
    """Files to inspect: given files as they are; below a folder the programs, libraries, archives, scripts,
    installers and shortcuts (data files such as textures are left to the antivirus scan of the folder)."""
    out: list[str] = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
            continue
        try:
            for e in fs.walk(p, ctx, max_files=MAX_FILES * 20, errors=errors):
                ext = ext_of(e.name)
                if ext in EXEC_EXT or ext in SCRIPT_EXT or ext in INSTALLER_EXT or ext in ARCHIVE_EXT or \
                        ext in LAUNCH_EXT or double_extension(e.name) or sniff(e.path)[0] in ("pe", "shortcut"):
                    out.append(e.path)
                    if len(out) >= MAX_FILES:
                        raise LimitReached(msg("More than {n} files; the check stopped there.", n=MAX_FILES))
        except LimitReached as exc:
            errors.append(str(exc))
    return out


def check(paths: list[str], scanner: Scanner, signer: Signer, ctx: TaskContext | None = None,
          minecraft: bool = False) -> tuple[list[FileReport], list[str]]:
    """Inspect, sign-check and antivirus-check ``paths`` (files or folders). Returns the reports, worst first."""
    ctx = context(ctx)
    errors: list[str] = []
    files = expand(paths, ctx, errors)
    reports: list[FileReport] = []
    budget = MAX_HASH_TOTAL
    for i, f in enumerate(files):
        ctx.check()
        ctx.report(msg("Inspecting {file}", file=ntpath.basename(f)), i, len(files) + 2)
        rep = inspect(f, ctx, min(MAX_HASH, budget))
        budget -= rep.size if rep.sha256 else 0
        reports.append(rep)
    ctx.report(msg("Checking digital signatures"), len(files), len(files) + 2)
    to_sign = [r.path for r in reports if wants_signature(r)]
    sigs = signer(to_sign, ctx) if to_sign else {}
    for r in reports:
        s = sigs.get(r.path)
        if s is not None:
            r.signature = s.to_dict() if hasattr(s, "to_dict") else dict(s)
    ctx.report(msg("Asking the antivirus"), len(files) + 1, len(files) + 2)
    by_path = {os.path.normcase(os.path.normpath(r.path)): r for r in reports}
    for p in paths:
        try:
            verdict = scanner(p, ctx)
        except (Cancelled, TimedOut):
            raise
        except Exception as exc:                                   # one failed check never hides the others
            verdict = {"status": "error", "engine": "", "threats": [], "detail": str(exc)}
        _apply(verdict, p, by_path, reports)
    for r in reports:
        r.findings = judge(r, minecraft)
    reports.sort(key=lambda r: (-r.level.rank, r.name.lower()))
    return reports, errors


def _apply(verdict: dict[str, Any], root: str, by_path: dict[str, FileReport], reports: list[FileReport]) -> None:
    """Spread one scan's verdict over the files below ``root``; detections land on the file they name."""
    root_n = os.path.normcase(os.path.normpath(root))
    below = [r for k, r in by_path.items() if k == root_n or k.startswith(root_n.rstrip("\\/") + os.sep)]
    status = verdict.get("status")
    hits: dict[str, list[dict]] = {}
    for t in verdict.get("threats", []):
        for res in t.get("resources") or []:
            container = os.path.normcase(os.path.normpath(str(res).split("->")[0]))
            hits.setdefault(container, []).append(t)
    for r in below:
        k = os.path.normcase(os.path.normpath(r.path))
        if status == "threats":
            mine = hits.get(k, [])
            if not mine and not hits and len(below) == 1:
                mine = verdict.get("threats") or [{"name": msg("a threat")}]
            r.av = {"status": "threats" if mine else "clean", "engine": verdict.get("engine", ""),
                    "threats": mine, "detail": verdict.get("detail", "")}
        else:
            r.av = {"status": status, "engine": verdict.get("engine", ""), "threats": [],
                    "detail": verdict.get("detail", "")}
    # a detection in a file the listing skipped (a texture, an unknown type) still has to show up
    for k, threats in hits.items():
        if k not in by_path and (k == root_n or k.startswith(root_n.rstrip("\\/") + os.sep)):
            extra = FileReport(threats[0]["resources"][0].split("->")[0])
            extra.av = {"status": "threats", "engine": verdict.get("engine", ""), "threats": threats}
            by_path[k] = extra
            reports.append(extra)
