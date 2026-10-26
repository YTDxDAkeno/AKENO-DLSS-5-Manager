"""Network Dashboard: the active adapter, connection type, addresses, gateway, DNS servers, link speed, Wi-Fi
signal, current throughput and error counters.

psutil gives the adapters, their addresses, link state and speed, byte and error counters on every platform.
On Windows, ``Get-NetIPConfiguration`` / ``Get-NetAdapter`` add the gateway, the DNS servers and the media type, and
the WLAN API (``WlanQueryInterface``) the Wi-Fi signal quality. Windows 11 24H2 and later may require location access
for Wi-Fi details; then the signal is reported as unavailable with that reason.

The public IP address is fetched only when the user asks for it (one HTTPS request to api.ipify.org); AKENO never
looks up a location.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

from akeno.i18n import msg
from akeno.paths import is_windows
from akeno.system.commands import CommandUnavailable, QueryError, as_list, powershell_json
from akeno.system.tasks import TaskContext, context

VPN_HINTS = ("wireguard", "openvpn", "tap-windows", "wintun", "vpn", "nordlynx", "tailscale", "zerotier", "proton")
VIRTUAL_HINTS = ("vethernet", "hyper-v", "virtualbox", "vmware", "loopback", "bluetooth", "teredo", "isatap",
                 "wsl", "docker", "npcap")
PUBLIC_IP_URL = "https://api.ipify.org"


@dataclass
class Adapter:
    name: str
    description: str = ""
    up: bool = False
    kind: str = "unknown"                 # ethernet | wifi | vpn | virtual | loopback | unknown
    speed_mbps: int | None = None
    mtu: int | None = None
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    mac: str | None = None
    gateway: list[str] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)
    bytes_sent: int | None = None
    bytes_recv: int | None = None
    errin: int | None = None
    errout: int | None = None
    dropin: int | None = None
    dropout: int | None = None
    interface_index: int | None = None
    wifi: dict[str, Any] = field(default_factory=dict)

    @property
    def apipa(self) -> bool:
        return any(a.startswith("169.254.") for a in self.ipv4)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def classify(name: str, description: str = "", media: str = "") -> str:
    text = f"{name} {description}".lower()
    m = (media or "").lower()
    if "loopback" in text or name == "lo":
        return "loopback"
    if any(h in text for h in VPN_HINTS):
        return "vpn"
    if any(h in text for h in VIRTUAL_HINTS):
        return "virtual"
    if "802.11" in m or "wireless" in m or "wi-fi" in text or "wifi" in text or "wlan" in text or "wireless" in text:
        return "wifi"
    if "802.3" in m or "ethernet" in text or "lan" in text.split() or text.startswith("en") or text.startswith("eth"):
        return "ethernet"
    return "unknown"


def adapters(ctx: TaskContext | None = None, windows: bool | None = None) -> tuple[list[Adapter], list[str]]:
    """Every adapter with what psutil and (on Windows) PowerShell report; errors are returned, not raised."""
    errors: list[str] = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    try:
        io = psutil.net_io_counters(pernic=True)
    except (OSError, RuntimeError):
        io = {}
    out: dict[str, Adapter] = {}
    for name, items in addrs.items():
        a = Adapter(name)
        for x in items:
            fam = getattr(x.family, "name", str(x.family))
            if fam == "AF_INET":
                a.ipv4.append(x.address)
            elif fam == "AF_INET6":
                a.ipv6.append(x.address.split("%")[0])
            elif fam in ("AF_LINK", "AF_PACKET") or x.family == psutil.AF_LINK:
                a.mac = x.address
        st = stats.get(name)
        if st:
            a.up, a.mtu = bool(st.isup), st.mtu
            a.speed_mbps = st.speed or None
        c = io.get(name)
        if c:
            a.bytes_sent, a.bytes_recv = c.bytes_sent, c.bytes_recv
            a.errin, a.errout, a.dropin, a.dropout = c.errin, c.errout, c.dropin, c.dropout
        a.kind = "loopback" if a.ipv4 and all(x.startswith("127.") for x in a.ipv4) else classify(name)
        out[name] = a
    if windows if windows is not None else is_windows():
        try:
            for row in windows_details(ctx):
                a = out.get(row.get("alias") or "")
                if a is None:
                    continue
                a.description = row.get("description") or ""
                a.interface_index = row.get("index")
                a.gateway = [g for g in as_list(row.get("gateway")) if g]
                a.dns = [d for d in as_list(row.get("dns")) if d]
                a.kind = classify(a.name, a.description, row.get("media") or "")
                if row.get("virtual"):
                    a.kind = "vpn" if a.kind == "vpn" else "virtual"
        except CommandUnavailable as exc:
            errors.append(str(exc))
        except (QueryError, OSError) as exc:
            errors.append(str(msg("Windows network configuration: {e}", e=str(exc))))
        try:
            for w in wlan_interfaces():
                for a in out.values():
                    if a.kind == "wifi" and (not w.get("description") or w["description"] == a.description):
                        a.wifi = w
        except OSError as exc:
            errors.append(str(msg("Wi-Fi details: {e}", e=str(exc))))
    else:
        gw = default_gateway_posix()
        if gw:
            for a in out.values():
                if a.up and a.kind not in ("loopback",) and a.ipv4:
                    a.gateway = [gw]
                    break
    return sorted(out.values(), key=_rank), errors


def _rank(a: Adapter):
    return (not a.up, not a.gateway, a.kind in ("virtual", "loopback", "vpn"), a.name.lower())


def active(adapters_: list[Adapter]) -> Adapter | None:
    """The adapter that carries the default route: up, with a gateway, not virtual (a VPN counts when it has the
    route)."""
    for a in adapters_:
        if a.up and a.gateway and a.kind not in ("virtual", "loopback"):
            return a
    for a in adapters_:
        if a.up and a.ipv4 and a.kind not in ("virtual", "loopback") and not all(x.startswith("127.") for x in a.ipv4):
            return a
    return None


def windows_details(ctx: TaskContext | None = None) -> list[dict[str, Any]]:
    script = r"""
$rows = @()
foreach ($c in @(Get-NetIPConfiguration -ErrorAction Stop)) {
  $ad = $null; try { $ad = Get-NetAdapter -InterfaceIndex $c.InterfaceIndex -ErrorAction Stop } catch {}
  $rows += [pscustomobject]@{alias=$c.InterfaceAlias; index=$c.InterfaceIndex; description=$c.InterfaceDescription;
    gateway=@($c.IPv4DefaultGateway | ForEach-Object { $_.NextHop }) + @($c.IPv6DefaultGateway | ForEach-Object { $_.NextHop });
    dns=@($c.DNSServer | ForEach-Object { $_.ServerAddresses }); media=$(if ($ad) { [string]$ad.PhysicalMediaType } else { '' });
    virtual=$(if ($ad) { [bool]$ad.Virtual } else { $false }); status=$(if ($ad) { [string]$ad.Status } else { '' })}
}
$rows
"""
    return as_list(powershell_json(script, 45, ctx))


def wlan_interfaces() -> list[dict[str, Any]]:
    """Wi-Fi interfaces with their connection state and signal quality (0-100) from the WLAN API."""
    if not is_windows():
        return []
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    class WLAN_INTERFACE_INFO(ctypes.Structure):
        _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", ctypes.c_wchar * 256),
                    ("isState", ctypes.c_uint32)]

    class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
        _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                    ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]

    class DOT11_SSID(ctypes.Structure):
        _fields_ = [("uSSIDLength", ctypes.c_ulong), ("ucSSID", ctypes.c_ubyte * 32)]

    class WLAN_ASSOCIATION_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("dot11Ssid", DOT11_SSID), ("dot11BssType", ctypes.c_uint32), ("dot11Bssid", ctypes.c_ubyte * 6),
                    ("dot11PhyType", ctypes.c_uint32), ("uDot11PhyIndex", ctypes.c_ulong),
                    ("wlanSignalQuality", ctypes.c_ulong), ("ulRxRate", ctypes.c_ulong), ("ulTxRate", ctypes.c_ulong)]

    class WLAN_SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("bSecurityEnabled", wintypes.BOOL), ("bOneXEnabled", wintypes.BOOL),
                    ("dot11AuthAlgorithm", ctypes.c_uint32), ("dot11CipherAlgorithm", ctypes.c_uint32)]

    class WLAN_CONNECTION_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("isState", ctypes.c_uint32), ("wlanConnectionMode", ctypes.c_uint32),
                    ("strProfileName", ctypes.c_wchar * 256), ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES),
                    ("wlanSecurityAttributes", WLAN_SECURITY_ATTRIBUTES)]

    try:
        api = ctypes.WinDLL("wlanapi")
    except OSError:
        return []
    handle = wintypes.HANDLE()
    version = wintypes.DWORD()
    if api.WlanOpenHandle(2, None, ctypes.byref(version), ctypes.byref(handle)) != 0:
        return []
    out = []
    try:
        plist = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        if api.WlanEnumInterfaces(handle, None, ctypes.byref(plist)) != 0:
            return []
        try:
            n = plist.contents.dwNumberOfItems
            infos = ctypes.cast(ctypes.addressof(plist.contents.InterfaceInfo),
                                ctypes.POINTER(WLAN_INTERFACE_INFO * n)).contents if n else []
            for info in infos:
                row = {"description": info.strInterfaceDescription, "connected": info.isState == 1}
                size = wintypes.DWORD()
                data = ctypes.c_void_p()
                rc = api.WlanQueryInterface(handle, ctypes.byref(info.InterfaceGuid), 7, None, ctypes.byref(size),
                                            ctypes.byref(data), None)
                if rc == 0 and data:
                    attr = ctypes.cast(data, ctypes.POINTER(WLAN_CONNECTION_ATTRIBUTES)).contents
                    assoc = attr.wlanAssociationAttributes
                    row["signal_percent"] = int(assoc.wlanSignalQuality)
                    row["rx_mbps"] = int(assoc.ulRxRate) // 1000
                    row["tx_mbps"] = int(assoc.ulTxRate) // 1000
                    row["phy"] = int(assoc.dot11PhyType)
                    api.WlanFreeMemory(data)
                elif rc == 5:
                    row["note"] = str(msg("Windows denied the Wi-Fi details (location access may be required)."))
                out.append(row)
        finally:
            api.WlanFreeMemory(plist)
    finally:
        api.WlanCloseHandle(handle, None)
    return out


def default_gateway_posix() -> str | None:
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    return socket.inet_ntoa(int(parts[2], 16).to_bytes(4, "little"))
    except OSError:
        pass
    return None


def throughput(sample_s: float = 1.0, ctx: TaskContext | None = None) -> dict[str, dict[str, float]]:
    """Bytes per second sent and received per adapter over the sample."""
    ctx = context(ctx)
    a = psutil.net_io_counters(pernic=True)
    t0 = time.monotonic()
    ctx.sleep(sample_s)
    b = psutil.net_io_counters(pernic=True)
    dt = max(0.001, time.monotonic() - t0)
    out = {}
    for name, end in b.items():
        start = a.get(name)
        if start is not None:
            out[name] = {"up": max(0, end.bytes_sent - start.bytes_sent) / dt,
                         "down": max(0, end.bytes_recv - start.bytes_recv) / dt,
                         "errors": (end.errin - start.errin) + (end.errout - start.errout),
                         "drops": (end.dropin - start.dropin) + (end.dropout - start.dropout)}
    return out


def public_ip(timeout: float = 6.0) -> str:
    """One HTTPS request, only after the user asked for it."""
    import ssl
    import urllib.request
    req = urllib.request.Request(PUBLIC_IP_URL, headers={"User-Agent": "AKENO-NetworkLab"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        text = resp.read(64).decode("ascii", errors="replace").strip()
    ipaddress.ip_address(text)                 # refuses anything that is not an address
    return text


def is_private(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr.split("%")[0]).is_private
    except ValueError:
        return False
