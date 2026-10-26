"""Digital signatures of many files at once (Startup Analyzer, Suspicious Process Analyzer, Mod Security Scanner).

Windows' ``Get-AuthenticodeSignature`` checks both kinds of signature: one embedded in the file and the catalog
signatures most Windows components use. Checking only embedded signatures would call those components unsigned.
When PowerShell cannot answer, the embedded check (``WinVerifyTrust``, ``akeno.security.authenticode``) is used and
the result says that a catalog signature was not checked.

A missing signature is not evidence of malware: many legitimate tools and most mods are unsigned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from akeno.i18n import msg
from akeno.paths import is_windows
from akeno.system.commands import CommandUnavailable, QueryError, as_list, powershell_json, ps_quote
from akeno.system.tasks import TaskContext

BATCH = 60


@dataclass
class Signature:
    status: str              # valid | unsigned | invalid | unknown
    signer: str | None = None
    kind: str = ""           # embedded | catalog | ""
    detail: str = ""

    @property
    def valid(self) -> bool:
        return self.status == "valid"

    def to_dict(self) -> dict:
        return {"status": self.status, "signer": self.signer, "kind": self.kind, "detail": str(self.detail)}


def subject_name(subject: str | None) -> str | None:
    """"CN=NVIDIA Corporation, O=NVIDIA Corporation, L=Santa Clara..." -> "NVIDIA Corporation"."""
    if not subject:
        return None
    for part in subject.split(","):
        part = part.strip()
        if part.upper().startswith("CN="):
            return part[3:].strip().strip('"') or None
    return subject


def check(paths: list[str], ctx: TaskContext | None = None) -> dict[str, Signature]:
    unique = list(dict.fromkeys(p for p in paths if p))
    out: dict[str, Signature] = {}
    if not unique:
        return out
    if not is_windows():
        return {p: Signature("unknown", detail=msg("Signature checks need Windows.")) for p in unique}
    missing = [p for p in unique if not os.path.isfile(p)]
    for p in missing:
        out[p] = Signature("unknown", detail=msg("File not found."))
    todo = [p for p in unique if p not in out]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            out.update(_powershell(chunk, ctx))
        except (CommandUnavailable, QueryError, OSError):
            out.update({p: _embedded(p) for p in chunk})
    return out


def _powershell(paths: list[str], ctx) -> dict[str, Signature]:
    items = ",".join(ps_quote(p) for p in paths)
    script = ("$r=@(); foreach ($p in @(" + items + ")) { try { $s = Get-AuthenticodeSignature -LiteralPath $p "
              "-ErrorAction Stop; $r += [pscustomobject]@{path=$p; status=[string]$s.Status; "
              "type=[string]$s.SignatureType; subject=$(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } "
              "else { $null }); message=$s.StatusMessage} } catch { $r += [pscustomobject]@{path=$p; status='Error'; "
              "message=$_.Exception.Message} } }; $r")
    data = as_list(powershell_json(script, 30 + 2 * len(paths), ctx))
    out: dict[str, Signature] = {}
    for row in data:
        path = row.get("path")
        status = row.get("status")
        kind = {"Authenticode": "embedded", "Catalog": "catalog"}.get(row.get("type") or "", "")
        signer = subject_name(row.get("subject"))
        if status == "Valid":
            out[path] = Signature("valid", signer, kind)
        elif status == "NotSigned":
            out[path] = Signature("unsigned", None, "", msg("No digital signature."))
        elif status in ("HashMismatch", "NotTrusted", "Incompatible"):
            out[path] = Signature("invalid", signer, kind, row.get("message") or status)
        else:
            out[path] = Signature("unknown", signer, kind, row.get("message") or str(status))
    for p in paths:
        out.setdefault(p, _embedded(p))
    return out


def _embedded(path: str) -> Signature:
    from akeno.security import authenticode
    info = authenticode.verify(path)
    if not info.checked:
        return Signature("unknown", detail=info.detail)
    if info.valid:
        return Signature("valid", info.signer, "embedded")
    if "not signed" in info.detail.lower():
        return Signature("unsigned", detail=msg("No embedded signature (a catalog signature was not checked)."))
    return Signature("invalid", info.signer, "embedded", info.detail)
