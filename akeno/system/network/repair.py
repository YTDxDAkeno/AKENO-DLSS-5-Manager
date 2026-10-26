"""Connection Repair: what is wrong with the adapter, and the safe fixes for it.

Diagnosis (read-only): adapter down, no address from DHCP (169.254.x.x), no gateway, no DNS server, error or drop
counters, a wired link that negotiated 100 Mbit/s or less (often a damaged cable or port).

Actions, each explained and confirmed first:
* clear the DNS cache (``ipconfig /flushdns``): drops cached name answers, keeps the connection;
* request a new address (``ipconfig /renew``) only when the adapter has *no* valid address anyway, so no working
  connection is interrupted;
* Windows' own network troubleshooter and *Network reset* are opened, never run by AKENO (a reset reinstalls the
  adapters and drops every connection).
"""

from __future__ import annotations

from typing import Any

from akeno.i18n import msg
from akeno.paths import is_windows
from akeno.system.commands import CommandUnavailable, run, system32
from akeno.system.model import Finding, Level, Report
from akeno.system.network.info import Adapter

SLOW_LINK = 100


def diagnose(adapters: list[Adapter], active: Adapter | None) -> Report:
    rep = Report("network")
    physical = [a for a in adapters if a.kind in ("ethernet", "wifi")]
    if active is None:
        f = rep.add(Finding("repair.no_active", "network", Level.PROBLEM, msg("No active network connection"),
                            msg("No adapter is connected with an address and a route to the internet."),
                            msg("Plug in the network cable or connect to Wi-Fi. If the adapter is missing entirely, "
                                "its driver may need to be reinstalled.")))
        for a in physical:
            f.measured(msg("{name}: {state}", name=a.name, state=msg("connected") if a.up else msg("disconnected")),
                       "psutil")
        return rep
    a = active
    if a.apipa:
        f = rep.add(Finding("repair.apipa", "network", Level.PROBLEM, msg("No address from the router"),
                            msg("{name} has the automatic address {ip}: the router's DHCP did not answer.",
                                name=a.name, ip=next(x for x in a.ipv4 if x.startswith("169.254."))),
                            msg("Restart the router, or request a new address. The adapter has no working connection, "
                                "so requesting one interrupts nothing.")))
        f.measured(msg("Address 169.254.x.x (APIPA)"), "psutil")
        f.data["action"] = "renew"
    if not a.gateway:
        f = rep.add(Finding("repair.no_gateway", "network", Level.WARN, msg("No default gateway"),
                            msg("{name} has no route to the internet configured.", name=a.name),
                            msg("Check the router, or the adapter's IP settings if they were set by hand.")))
        f.measured(msg("Gateway: none"), "Get-NetIPConfiguration")
    if is_windows() and not a.dns:
        rep.add(Finding("repair.no_dns", "network", Level.WARN, msg("No DNS server configured"),
                        msg("{name} has no DNS server, so names cannot be resolved.", name=a.name),
                        msg("Usually the router assigns one. The DNS benchmark helps choosing a server."))
                ).link("network", msg("Run the DNS benchmark"), "dns")
    errs = (a.errin or 0) + (a.errout or 0)
    drops = (a.dropin or 0) + (a.dropout or 0)
    if errs or drops > 1000:
        f = rep.add(Finding("repair.errors", "network", Level.WARN if errs else Level.INFO,
                            msg("The adapter counts transmission errors"),
                            msg("{e} errors and {d} dropped packets since the adapter started.", e=errs, d=drops),
                            msg("A few are normal. Many errors on a cable point to the cable or port; on Wi-Fi to "
                                "interference or a weak signal.")))
        f.measured(msg("Errors in/out: {i}/{o}, drops in/out: {di}/{do}", i=a.errin, o=a.errout, di=a.dropin,
                       do=a.dropout), "psutil")
    if a.kind == "ethernet" and a.speed_mbps and a.speed_mbps <= SLOW_LINK:
        f = rep.add(Finding("repair.slow_link", "network", Level.WARN, msg("The cable connection runs at {s} Mbit/s",
                                                                           s=a.speed_mbps),
                            msg("Gigabit adapters fall back to 100 Mbit/s or less when a wire in the cable or a port "
                                "contact is damaged."),
                            msg("Try another cable (Cat 5e or better) and another router port.")))
        f.measured(msg("Link speed {s} Mbit/s", s=a.speed_mbps), "psutil")
    if a.kind == "wifi" and a.wifi.get("signal_percent") is not None and a.wifi["signal_percent"] < 50:
        f = rep.add(Finding("repair.weak_wifi", "network", Level.WARN, msg("Weak Wi-Fi signal"),
                            msg("Signal quality {p} %.", p=a.wifi["signal_percent"]),
                            msg("Move closer to the router, remove obstacles, or use a cable for gaming.")))
        f.measured(msg("Signal quality {p} %", p=a.wifi["signal_percent"]), "WLAN API")
    if not rep.findings:
        rep.add(Finding("repair.ok", "network", Level.OK, msg("The adapter looks fine"),
                        msg("{name}: connected, address, gateway and DNS server present, no error counters.",
                            name=a.name)))
    return rep


def flush_dns() -> dict[str, Any]:
    """``ipconfig /flushdns``: clears cached name answers; the connection stays."""
    if not is_windows():
        raise CommandUnavailable(msg("This repair needs Windows."))
    res = run([system32("ipconfig.exe"), "/flushdns"], timeout=30)
    return {"ok": res.ok, "text": (res.stdout or res.stderr).strip()[:400]}


def renew(adapter: Adapter) -> dict[str, Any]:
    """Only for an adapter without a valid address (APIPA): nothing that works can be interrupted."""
    if not adapter.apipa:
        raise PermissionError(msg("The adapter has a working address; AKENO does not interrupt it."))
    if not is_windows():
        raise CommandUnavailable(msg("This repair needs Windows."))
    res = run([system32("ipconfig.exe"), "/renew", adapter.name], timeout=90)
    return {"ok": res.ok, "text": (res.stdout or res.stderr).strip()[-400:]}


SETTINGS_PAGES = {"status": "ms-settings:network-status", "troubleshoot": "ms-settings:troubleshoot",
                  "advanced": "ms-settings:network-advancedsettings"}
