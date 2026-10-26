"""Security Center service (no Qt): status, Defender scans, file checks, download check, process review."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from akeno.i18n import msg
from akeno.system.commands import CommandUnavailable, QueryError
from akeno.system.model import Finding, Level, Report
from akeno.system.securitycenter import defender, filecheck, processes
from akeno.system.securitycenter.defender import ScanResult
from akeno.system.securitycenter.providers import WindowsSecurity
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

ENGINE = "Microsoft Defender"
DEFENDER_NAMES = ("microsoft defender", "windows defender")
URIS = {"threat": "windowsdefender://threat/", "network": "windowsdefender://network/",
        "history": "windowsdefender://history/", "settings": "windowsdefender://threatsettings/",
        "home": "windowsdefender://"}


def is_defender(name: str | None) -> bool:
    return bool(name) and any(n in name.lower() for n in DEFENDER_NAMES)


class SecurityService:
    def __init__(self, store, provider=None):
        self.store = store
        self.provider = provider or WindowsSecurity()
        self._status: dict[str, Any] | None = None
        self._status_at = 0.0

    # -- state ----------------------------------------------------------------------------------------------------
    def state(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        """Everything Windows reports, each part with its own error (one missing interface hides nothing else)."""
        ctx = context(ctx)
        out: dict[str, Any] = {"errors": {}}

        def part(key, fn):
            ctx.check()
            try:
                out[key] = fn()
            except (Cancelled, TimedOut):
                raise
            except (CommandUnavailable, QueryError, OSError) as exc:
                out[key] = None
                out["errors"][key] = str(exc)

        part("health", self.provider.health)
        part("antivirus", lambda: self._products("antivirus", out))
        part("firewall_products", lambda: self._products("firewall", out))
        part("firewall", self.provider.firewall_profiles)
        part("defender", lambda: self.provider.defender_status(ctx))
        part("history", lambda: self.provider.history(ctx))
        self._status, self._status_at = out.get("defender"), time.monotonic()
        return out

    def _products(self, area: str, out: dict) -> list[dict[str, Any]]:
        items, errors = self.provider.products(area)
        if errors and not items:
            out["errors"][area] = "; ".join(errors)
        return items

    def defender_ready(self, st: dict[str, Any] | None = None, ctx: TaskContext | None = None) -> tuple[bool, str]:
        if st is None:
            if self._status is None or time.monotonic() - self._status_at > 60:
                try:
                    self._status = self.provider.defender_status(ctx)
                except (CommandUnavailable, QueryError, OSError):
                    self._status = None
                self._status_at = time.monotonic()
            st = self._status
        return defender.usable(st)

    def overview(self, ctx: TaskContext | None = None) -> Report:
        start = time.monotonic()
        st = self.state(ctx)
        rep = Report("security", overview_findings(st), [f"{k}: {v}" for k, v in st["errors"].items()],
                     duration_s=time.monotonic() - start)
        rep.data = {"state": st, "downloads": self.store.history("security.download", 10)}
        self.store.add_history("security.overview", {"worst": rep.worst.value}, keep=30)
        return rep

    # -- Defender actions (only after the user's click) ---------------------------------------------------------
    def scan(self, kind: str, target: str | None = None, ctx: TaskContext | None = None) -> ScanResult:
        ok, reason = self.defender_ready(ctx=ctx)
        if not ok:
            return ScanResult(kind, target, "unavailable", detail=reason)
        res = self.provider.scan(kind, target, False, ctx)
        self.store.add_history("security.scan", res.to_dict(), keep=50)
        return res

    def update_signatures(self, ctx: TaskContext | None = None) -> str:
        res = self.provider.update_signatures(ctx)
        self._status = None
        return res

    def history(self, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
        return self.provider.history(ctx)

    def scans(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.history("security.scan", limit)

    # -- checks (nothing on the PC changes) ---------------------------------------------------------------------
    def verdict(self, path: str, ctx: TaskContext | None = None) -> dict[str, Any]:
        """Defender's report-only verdict for a file or folder: clean | threats | not_checked | error."""
        ok, reason = self.defender_ready(ctx=ctx)
        if not ok:
            return {"status": "not_checked", "engine": "", "threats": [], "detail": reason}
        res = self.provider.scan("custom", path, True, ctx)
        if res.status in ("cancelled",):
            raise Cancelled("Cancelled.")
        if res.status in ("clean", "threats"):
            return {"status": res.status, "engine": ENGINE, "threats": res.threats, "found": res.found,
                    "detail": ""}
        return {"status": "error" if res.status != "unavailable" else "not_checked", "engine": ENGINE,
                "threats": [], "detail": res.detail}

    def check_files(self, paths: list[str], ctx: TaskContext | None = None, minecraft: bool = False
                    ) -> tuple[list[filecheck.FileReport], list[str]]:
        reports, errors = filecheck.check(paths, self.verdict, self.provider.signatures, ctx, minecraft)
        self.store.add_history("security.files", {
            "paths": [str(p) for p in paths][:20], "files": len(reports),
            "warnings": sum(1 for r in reports if r.level in (Level.WARN, Level.PROBLEM)),
            "threats": sum(1 for r in reports if (r.av or {}).get("status") == "threats")}, keep=30)
        return reports, errors

    def check_download(self, folder: str, token=None, label: str = "") -> dict[str, Any]:
        """Before AKENO installs a download: Defender's report-only verdict on the unpacked files. A detection
        stops the installation; a missing antivirus does not (the checksum and signature checks stay in force)."""
        ctx = TaskContext(token=token, timeout=300.0)
        try:
            v = self.verdict(folder, ctx)
        except (Cancelled, TimedOut):
            raise
        except Exception as exc:                                    # the antivirus never breaks an installation
            v = {"status": "error", "engine": ENGINE, "threats": [], "detail": str(exc)}
        entry = {"label": label, "status": v["status"], "engine": v.get("engine"), "detail": str(v.get("detail")),
                 "threats": [t.get("name") for t in v.get("threats", [])]}
        self.store.add_history("security.download", entry, keep=50)
        return v

    def processes(self, ctx: TaskContext | None = None) -> Report:
        ctx = context(ctx)
        start = time.monotonic()
        procs = self.provider.processes(ctx)
        try:
            detections = self.provider.history(ctx)
        except (CommandUnavailable, QueryError, OSError):
            detections = []
        rep = processes.analyse(procs, self.provider.signatures, detections=detections, ctx=ctx)
        rep.duration_s = time.monotonic() - start
        return rep


# -- the overview's findings -----------------------------------------------------------------------------------
def _days_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def overview_findings(st: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    health = st.get("health") or {}
    avs = st.get("antivirus") or []
    on = [p for p in avs if p.get("state") == "on"]
    third = [p for p in on if not is_defender(p.get("name"))]
    dstat = st.get("defender")
    defender_active = bool(dstat) and dstat.get("mode") == "normal" and dstat.get("antivirus")

    if st.get("health") is None and dstat is None:
        out.append(Finding("sec.unavailable", "security", Level.UNKNOWN,
                           msg("Windows Security Center does not answer"),
                           msg("Neither Windows Security Center nor Microsoft Defender could be queried: {error}",
                               error=st["errors"].get("health") or st["errors"].get("defender") or "-"),
                           msg("Open Windows Security to see the protection status."))
                   .link("security", msg("Open Windows Security"), "uri:home"))
        return out

    # -- virus protection ------------------------------------------------------------------------------------
    av_health = health.get("antivirus")
    names = ", ".join(p.get("name") or "?" for p in on) or (ENGINE if defender_active else "")
    if defender_active and not dstat.get("realtime"):
        out.append(Finding("sec.av.realtime", "security", Level.PROBLEM,
                           msg("Real-time protection of Microsoft Defender is off"),
                           msg("Files are only checked when a scan runs."),
                           msg("Turn it on in Windows Security under Virus & threat protection. AKENO never "
                               "changes this setting."))
                   .measured(msg("RealTimeProtectionEnabled = False"), "Get-MpComputerStatus")
                   .link("security", msg("Open virus protection settings"), "uri:settings"))
    elif av_health == "good" or (av_health is None and (defender_active or third)):
        out.append(Finding("sec.av.on", "security", Level.OK, msg("Virus protection is on"),
                           msg("Active: {names}", names=names or "?"))
                   .measured(msg("Windows Security Center: antivirus {state}", state=av_health or "-"),
                             "WscGetSecurityProviderHealth"))
    elif av_health == "snoozed":
        out.append(Finding("sec.av.snoozed", "security", Level.WARN, msg("Virus protection is paused"),
                           msg("Windows reports the antivirus as snoozed: {names}", names=names or "-"),
                           msg("Resume it in the antivirus or in Windows Security."))
                   .measured(msg("Windows Security Center: antivirus snoozed"), "WscGetSecurityProviderHealth")
                   .link("security", msg("Open Windows Security"), "uri:threat"))
    elif av_health in ("poor", "not_monitored"):
        states = ", ".join(f"{p.get('name')}: {p.get('state')}" for p in avs) or msg("no antivirus registered")
        out.append(Finding("sec.av.poor", "security", Level.PROBLEM,
                           msg("Windows reports a problem with virus protection"),
                           msg("Registered products: {states}", states=states),
                           msg("Open Windows Security and turn virus protection on or update it. AKENO does not "
                               "change protection settings."))
                   .measured(msg("Windows Security Center: antivirus {state}", state=av_health),
                             "WscGetSecurityProviderHealth")
                   .link("security", msg("Open Windows Security"), "uri:threat"))
    if third:
        out.append(Finding("sec.av.third_party", "security", Level.INFO,
                           msg("{name} protects this PC", name=", ".join(p.get("name") or "?" for p in third)),
                           msg("Windows Security Center lists it as the active antivirus."),
                           msg("Scans, quarantine and threat history are managed in {name}. AKENO shows the status "
                               "Windows reports and does not control other antivirus products.",
                               name=third[0].get("name") or "?")))
    if len(on) > 1 and third and defender_active:
        out.append(Finding("sec.av.two", "security", Level.INFO,
                           msg("Two antivirus products report real-time protection"),
                           ", ".join(p.get("name") or "?" for p in on),
                           msg("Usually one of them steps back automatically. If the PC feels slow, check in "
                               "Windows Security which one is active.")))

    # -- Microsoft Defender details ----------------------------------------------------------------------------
    if dstat:
        if dstat.get("mode") == "passive":
            out.append(Finding("sec.defender.passive", "security", Level.INFO,
                               msg("Microsoft Defender is in passive mode"),
                               msg("Another antivirus is responsible for real-time protection; Defender can still "
                                   "run scans you start.")))
        if defender_active:
            age = dstat.get("signature_age_days")
            if dstat.get("signatures_out_of_date") or (age is not None and age >= 7):
                out.append(Finding("sec.defender.signatures", "security", Level.WARN,
                                   msg("Virus definitions are out of date"),
                                   msg("Last update {days} days ago (version {version}).", days=age if age is not None
                                       else "?", version=dstat.get("signature_version") or "?"),
                                   msg("Update the definitions here or in Windows Security."))
                           .measured(msg("AntivirusSignatureAge = {days}", days=age), "Get-MpComputerStatus")
                           .link("security", msg("Update definitions"), "update"))
            if dstat.get("tamper") is False:
                out.append(Finding("sec.defender.tamper", "security", Level.WARN,
                                   msg("Tamper Protection is off"),
                                   msg("Programs can change Defender's settings without Windows Security."),
                                   msg("Windows recommends turning it on in Virus & threat protection settings. "
                                       "AKENO never changes it."))
                           .measured(msg("IsTamperProtected = False"), "Get-MpComputerStatus")
                           .link("security", msg("Open virus protection settings"), "uri:settings"))
            pending = {"reboot": msg("a restart"), "full_scan": msg("a full scan"),
                       "manual_steps": msg("manual steps in Windows Security"),
                       "offline_scan": msg("an offline scan"), "critical_failure": msg("attention (critical error)")}
            for state in dstat.get("computer_state") or []:
                out.append(Finding("sec.defender.pending." + state, "security", Level.WARN,
                                   msg("Microsoft Defender is waiting for {what}", what=pending.get(state, state)),
                                   msg("Defender needs this to finish handling a threat."),
                                   msg("Open Windows Security and follow its instructions."))
                           .measured(msg("ComputerState: {state}", state=state), "Get-MpComputerStatus")
                           .link("security", msg("Open Windows Security"), "uri:threat"))
            quick = dstat.get("quick_scan_age_days")
            if quick is None or quick > 14:
                out.append(Finding("sec.defender.scan_age", "security", Level.INFO,
                                   msg("No quick scan in the last two weeks") if quick is not None else
                                   msg("No quick scan recorded"),
                                   msg("Defender normally scans during automatic maintenance."),
                                   msg("Start a quick scan if you like; it runs in the background."))
                           .link("security", msg("Quick scan"), "scan:quick"))

    # -- firewall ----------------------------------------------------------------------------------------------
    fw = health.get("firewall")
    profiles = st.get("firewall") or []
    off = [p.get("name") for p in profiles if not p.get("enabled")]
    if fw == "poor" or (fw is None and off):
        out.append(Finding("sec.firewall.off", "security", Level.PROBLEM,
                           msg("The firewall is off") if fw == "poor" else msg("A firewall profile is off"),
                           msg("Off: {profiles}", profiles=", ".join(off)) if off else
                           msg("Windows reports the firewall as not protecting."),
                           msg("Turn it on in Windows Security under Firewall & network protection. AKENO never "
                               "switches the firewall."))
                   .measured(msg("Windows Security Center: firewall {state}", state=fw or "-"),
                             "WscGetSecurityProviderHealth, Get-NetFirewallProfile")
                   .link("security", msg("Open firewall settings"), "uri:network"))
    elif fw == "good":
        fwp = [p.get("name") for p in st.get("firewall_products") or [] if p.get("state") == "on"]
        out.append(Finding("sec.firewall.on", "security", Level.OK, msg("The firewall is on"),
                           msg("Active: {names}", names=", ".join(fwp) or "Windows Defender Firewall"))
                   .measured(msg("Windows Security Center: firewall good"), "WscGetSecurityProviderHealth"))
        if off:
            out.append(Finding("sec.firewall.profile", "security", Level.INFO,
                               msg("Some Windows Firewall profiles are off"), ", ".join(off),
                               msg("Fine when another firewall protects the PC; otherwise turn them on in Windows "
                                   "Security.")))

    # -- threats ------------------------------------------------------------------------------------------------
    hist = st.get("history") or []
    open_ = [h for h in hist if h.get("unresolved")]
    if open_:
        out.append(Finding("sec.threats.open", "security", Level.PROBLEM,
                           msg("Detected threats that still need attention: {n}", n=len(open_)),
                           ", ".join(sorted({h.get("name") or "?" for h in open_}))[:300],
                           msg("Open Protection history in Windows Security and follow the actions Defender "
                               "offers there."))
                   .measured(msg("Unresolved detections"), "Get-MpThreatDetection")
                   .link("security", msg("Open protection history"), "history"))
    recent = [h for h in hist if not h.get("unresolved") and (_days_since(h.get("time")) or 999) <= 30]
    if recent:
        out.append(Finding("sec.threats.recent", "security", Level.INFO,
                           msg("Threats Microsoft Defender handled in the last 30 days: {n}", n=len(recent)),
                           ", ".join(sorted({h.get("name") or "?" for h in recent}))[:300],
                           msg("Nothing to do; details are in the history."))
                   .link("security", msg("Show history"), "history"))
    return out
