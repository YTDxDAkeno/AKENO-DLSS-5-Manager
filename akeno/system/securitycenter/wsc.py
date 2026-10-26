"""Windows Security Center (documented in wscapi.h).

* ``WscGetSecurityProviderHealth``: the overall health Windows shows per area (firewall, antivirus, ...).
* ``IWSCProductList`` / ``IWscProduct`` (Windows 8+): every registered antivirus, antispyware and firewall product
  with its name, state (on, off, snoozed, expired), signature status and whether it is the default.

If the COM interface is unavailable, the product names come from the ``root/SecurityCenter2`` CIM classes, without a
state (their ``productState`` field is undocumented and AKENO does not decode it).
"""

from __future__ import annotations

import ctypes
import logging
from typing import Any

from akeno.paths import is_windows
from akeno.system.commands import CommandUnavailable, QueryError, as_list, powershell_json

log = logging.getLogger(__name__)

PROVIDERS = {"firewall": 0x1, "autoupdate": 0x2, "antivirus": 0x4, "antispyware": 0x8, "internet": 0x10,
             "uac": 0x20, "service": 0x40}
HEALTH = {0: "good", 1: "not_monitored", 2: "poor", 3: "snoozed"}
PRODUCT_STATE = {0: "on", 1: "off", 2: "snoozed", 3: "expired"}
SIGNATURE = {0: "out_of_date", 1: "up_to_date"}

CLSID_WSCProductList = "{17072F7B-9ABE-4A74-A261-1EB76B55107A}"
IID_IWSCProductList = "{722A338C-6E8E-4E72-AC27-1417FB0C81C2}"


def provider_health() -> dict[str, str]:
    """area -> good | not_monitored | poor | snoozed | unknown."""
    if not is_windows():
        raise CommandUnavailable("Windows Security Center needs Windows.")
    wsc = ctypes.WinDLL("wscapi")
    fn = wsc.WscGetSecurityProviderHealth
    fn.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_int)]
    fn.restype = ctypes.c_long
    out = {}
    for name, flag in PROVIDERS.items():
        value = ctypes.c_int(-1)
        hr = fn(flag, ctypes.byref(value))
        out[name] = HEALTH.get(value.value, "unknown") if hr == 0 else "unknown"
    return out


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ole = ctypes.WinDLL("ole32")
    ole.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_GUID)]
    if ole.CLSIDFromString(text, ctypes.byref(g)) != 0:
        raise OSError("invalid GUID " + text)
    return g


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def _bstr(fn, obj) -> str | None:
    p = ctypes.c_void_p()
    if fn(obj, ctypes.byref(p)) != 0 or not p.value:
        return None
    try:
        return ctypes.wstring_at(p.value)
    finally:
        ctypes.WinDLL("oleaut32").SysFreeString(p)


def products(provider: str = "antivirus") -> list[dict[str, Any]]:
    """Registered products of one area through IWSCProductList (IDispatch-based; methods after IDispatch's 7)."""
    if not is_windows():
        raise CommandUnavailable("Windows Security Center needs Windows.")
    ole = ctypes.WinDLL("ole32")
    hr = ole.CoInitializeEx(None, 0)                     # COINIT_MULTITHREADED on this worker thread
    initialised = hr in (0, 1)                           # S_OK / S_FALSE; RPC_E_CHANGED_MODE keeps the caller's
    try:
        clsid, iid = _guid(CLSID_WSCProductList), _guid(IID_IWSCProductList)
        lst = ctypes.c_void_p()
        ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        hr = ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(lst))
        if hr != 0 or not lst.value:
            raise OSError(f"IWSCProductList unavailable (0x{hr & 0xFFFFFFFF:08X})")
        try:
            initialize = _method(lst, 7, ctypes.c_uint32)
            get_count = _method(lst, 8, ctypes.POINTER(ctypes.c_long))
            get_item = _method(lst, 9, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
            hr = initialize(lst, PROVIDERS[provider])
            if hr != 0:
                raise OSError(f"IWSCProductList::Initialize failed (0x{hr & 0xFFFFFFFF:08X})")
            n = ctypes.c_long()
            get_count(lst, ctypes.byref(n))
            out = []
            for i in range(max(0, min(n.value, 32))):
                prod = ctypes.c_void_p()
                if get_item(lst, i, ctypes.byref(prod)) != 0 or not prod.value:
                    continue
                try:
                    name = _bstr(_method(prod, 7, ctypes.POINTER(ctypes.c_void_p)), prod)
                    state, sig, default = ctypes.c_int(-1), ctypes.c_int(-1), ctypes.c_int(0)
                    _method(prod, 8, ctypes.POINTER(ctypes.c_int))(prod, ctypes.byref(state))
                    _method(prod, 9, ctypes.POINTER(ctypes.c_int))(prod, ctypes.byref(sig))
                    stamp = _bstr(_method(prod, 11, ctypes.POINTER(ctypes.c_void_p)), prod)
                    has_default = _method(prod, 13, ctypes.POINTER(ctypes.c_int))(prod, ctypes.byref(default)) == 0
                    out.append({"name": name, "state": PRODUCT_STATE.get(state.value, "unknown"),
                                "signature": SIGNATURE.get(sig.value, "unknown"), "timestamp": stamp,
                                "default": bool(default.value) if has_default else None, "source": "IWscProduct"})
                finally:
                    _method(prod, 2)(prod)            # IUnknown::Release
            return out
        finally:
            _method(lst, 2)(lst)
    finally:
        if initialised:
            ole.CoUninitialize()


def products_cim(provider: str = "antivirus") -> list[dict[str, Any]]:
    cls = {"antivirus": "AntiVirusProduct", "antispyware": "AntiSpywareProduct",
           "firewall": "FirewallProduct"}[provider]
    rows = as_list(powershell_json("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName " + cls +
                                   " -ErrorAction Stop | Select-Object displayName,pathToSignedProductExe", 30))
    return [{"name": r.get("displayName"), "state": "unknown", "signature": "unknown", "timestamp": None,
             "default": None, "source": "SecurityCenter2"} for r in rows]


def all_products(provider: str = "antivirus") -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    try:
        return products(provider), errors
    except CommandUnavailable:
        raise
    except Exception as exc:                           # noqa: BLE001 (COM can fail in many ways)
        errors.append(f"IWscProduct: {exc}")
    try:
        return products_cim(provider), errors
    except (QueryError, OSError, CommandUnavailable) as exc:
        errors.append(f"SecurityCenter2: {exc}")
    return [], errors


def firewall_profiles() -> list[dict[str, Any]]:
    """The Windows Firewall profiles (Domain, Private, Public) and whether each is on (read-only)."""
    rows = as_list(powershell_json("Get-NetFirewallProfile -ErrorAction Stop | Select-Object Name,"
                                   "@{n='Enabled';e={[string]$_.Enabled}}", 30))
    return [{"name": r.get("Name"), "enabled": str(r.get("Enabled")).lower() == "true"} for r in rows]
