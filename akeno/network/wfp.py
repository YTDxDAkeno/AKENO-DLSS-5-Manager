"""Relay-only transport lock for AKENO LAN Play: AKENO-owned Windows Filtering Platform filters.

Why not Windows Defender Firewall rules: WireGuardNT owns its UDP socket in the kernel and the official WireGuard
tunnel service installs its own WFP session that hard-permits the service's traffic at the ALE layers. Defender rules
live in a lower-priority sublayer of the same ALE layers, so a room member who knew this PC's address could reach
the tunnel socket directly and make WireGuard roam to that address (observed in CI: peer endpoint changed from the
relay to the attacker). The transport layers are evaluated per packet by the TCP/IP stack for every socket, kernel
sockets included, and WireGuard installs nothing there.

Policy for one LAN tunnel with UDP listen port P and relay R:R_port, at INBOUND/OUTBOUND_TRANSPORT_V4:
  permit  UDP, local port P, remote address R, remote port R_port       (weight 15)
  block   UDP, local port P                                             (weight 8)
and at INBOUND/OUTBOUND_TRANSPORT_V6 (the relay is IPv4 only):
  block   UDP, local port P                                             (weight 8)

Properties:
* atomic: every add/replace/remove is one BFE transaction; a failure aborts and leaves the previous state;
* persistent: filters survive a crash of the elevated helper (and a reboot) until recovery removes them, so a
  tunnel left running by a crash can still only talk to the relay;
* owned: provider, sublayer and filter keys are fixed AKENO GUIDs or derived from the tunnel name (uuid5); only
  objects carrying the AKENO provider key are ever deleted. Nothing else in the Windows firewall is touched.

Runs only in the explicit elevated AKENO helper.
"""
from __future__ import annotations

import ctypes
import ipaddress
import re
import sys
import uuid

from akeno.network.windows import NativeUnavailable

PROVIDER = uuid.UUID('6b1c8f3e-4a5d-4c8e-9f21-a7e0d3b54c01')
SUBLAYER = uuid.UUID('6b1c8f3e-4a5d-4c8e-9f21-a7e0d3b54c02')
NAMESPACE = uuid.UUID('6b1c8f3e-4a5d-4c8e-9f21-a7e0d3b54c00')
TUNNEL = re.compile(r'AkenoLAN[a-f0-9]{24}')

LAYERS = {
    'in4': uuid.UUID('5926dfc8-e3cf-4426-a283-dc393f5d0f9d'),    # FWPM_LAYER_INBOUND_TRANSPORT_V4
    'out4': uuid.UUID('09e61aea-d214-46e2-9b21-b26b0b2f28c8'),   # FWPM_LAYER_OUTBOUND_TRANSPORT_V4
    'in6': uuid.UUID('634a869f-fc23-4b90-b0c1-bf620a36ae6f'),    # FWPM_LAYER_INBOUND_TRANSPORT_V6
    'out6': uuid.UUID('e1735bde-013f-4655-b351-a49e15762df0'),   # FWPM_LAYER_OUTBOUND_TRANSPORT_V6
}
CONDITION_PROTOCOL = uuid.UUID('3971ef2b-623e-4f9a-8cb1-6e79b806b9a7')        # FWPM_CONDITION_IP_PROTOCOL
CONDITION_LOCAL_PORT = uuid.UUID('0c1ba1af-5765-453f-af22-a8f791ac775b')      # FWPM_CONDITION_IP_LOCAL_PORT
CONDITION_REMOTE_ADDRESS = uuid.UUID('b235ae9a-1d64-49b8-a44c-5ff3d9095045')  # FWPM_CONDITION_IP_REMOTE_ADDRESS
CONDITION_REMOTE_PORT = uuid.UUID('c35a604d-d22b-4e1a-91b4-68f674ee674b')     # FWPM_CONDITION_IP_REMOTE_PORT

FWP_UINT8, FWP_UINT16, FWP_UINT32 = 1, 2, 3
FWP_MATCH_EQUAL = 0
FWP_ACTION_BLOCK, FWP_ACTION_PERMIT = 0x1001, 0x1002
FWPM_FILTER_FLAG_PERSISTENT = 0x1
FWPM_PROVIDER_FLAG_PERSISTENT = 0x1
FWPM_SUBLAYER_FLAG_PERSISTENT = 0x1
RPC_C_AUTHN_WINNT = 10
FWP_E_FILTER_NOT_FOUND = 0x80320003
FWP_E_PROVIDER_NOT_FOUND = 0x80320005
FWP_E_SUBLAYER_NOT_FOUND = 0x80320007
FWP_E_ALREADY_EXISTS = 0x80320009
PERMIT_WEIGHT, BLOCK_WEIGHT = 15, 8


class TransportLockError(NativeUnavailable):
    """A native failure: the helper reports it and LAN Play stays off (never a reason to continue unlocked)."""


class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                ('Data4', ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, value: uuid.UUID) -> 'GUID':
        raw = value.bytes_le
        return cls(int.from_bytes(raw[0:4], 'little'), int.from_bytes(raw[4:6], 'little'),
                   int.from_bytes(raw[6:8], 'little'), (ctypes.c_ubyte * 8)(*raw[8:16]))

    def uuid(self) -> uuid.UUID:
        return uuid.UUID(bytes_le=self.Data1.to_bytes(4, 'little') + self.Data2.to_bytes(2, 'little') +
                         self.Data3.to_bytes(2, 'little') + bytes(self.Data4))


class DISPLAY_DATA(ctypes.Structure):
    _fields_ = [('name', ctypes.c_wchar_p), ('description', ctypes.c_wchar_p)]


class BYTE_BLOB(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.c_void_p)]


class SESSION(ctypes.Structure):
    _fields_ = [('sessionKey', GUID), ('displayData', DISPLAY_DATA), ('flags', ctypes.c_uint32),
                ('txnWaitTimeoutInMSec', ctypes.c_uint32), ('processId', ctypes.c_uint32), ('sid', ctypes.c_void_p),
                ('username', ctypes.c_wchar_p), ('kernelMode', ctypes.c_int)]


class PROVIDER_(ctypes.Structure):
    _fields_ = [('providerKey', GUID), ('displayData', DISPLAY_DATA), ('flags', ctypes.c_uint32),
                ('providerData', BYTE_BLOB), ('serviceName', ctypes.c_wchar_p)]


class SUBLAYER_(ctypes.Structure):
    _fields_ = [('subLayerKey', GUID), ('displayData', DISPLAY_DATA), ('flags', ctypes.c_uint32),
                ('providerKey', ctypes.POINTER(GUID)), ('providerData', BYTE_BLOB), ('weight', ctypes.c_uint16)]


class _ValueUnion(ctypes.Union):
    _fields_ = [('uint8', ctypes.c_uint8), ('uint16', ctypes.c_uint16), ('uint32', ctypes.c_uint32),
                ('pointer', ctypes.c_void_p)]


class VALUE(ctypes.Structure):
    """FWP_VALUE0 and FWP_CONDITION_VALUE0 share this layout: a type tag and an 8-byte union."""
    _fields_ = [('type', ctypes.c_uint32), ('value', _ValueUnion)]


class FILTER_CONDITION(ctypes.Structure):
    _fields_ = [('fieldKey', GUID), ('matchType', ctypes.c_uint32), ('conditionValue', VALUE)]


class ACTION(ctypes.Structure):
    _fields_ = [('type', ctypes.c_uint32), ('filterType', GUID)]


class _ContextUnion(ctypes.Union):
    _fields_ = [('rawContext', ctypes.c_uint64), ('providerContextKey', GUID)]


class FILTER(ctypes.Structure):
    _fields_ = [('filterKey', GUID), ('displayData', DISPLAY_DATA), ('flags', ctypes.c_uint32),
                ('providerKey', ctypes.POINTER(GUID)), ('providerData', BYTE_BLOB), ('layerKey', GUID),
                ('subLayerKey', GUID), ('weight', VALUE), ('numFilterConditions', ctypes.c_uint32),
                ('filterCondition', ctypes.POINTER(FILTER_CONDITION)), ('action', ACTION),
                ('context', _ContextUnion), ('reserved', ctypes.POINTER(GUID)), ('filterId', ctypes.c_uint64),
                ('effectiveWeight', VALUE)]


def _condition(field: uuid.UUID, kind: int, value: int) -> FILTER_CONDITION:
    condition = FILTER_CONDITION(GUID.of(field), FWP_MATCH_EQUAL, VALUE(kind))
    setattr(condition.conditionValue.value, {FWP_UINT8: 'uint8', FWP_UINT16: 'uint16', FWP_UINT32: 'uint32'}[kind],
            value)
    return condition


def filter_key(tunnel: str, rule: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f'{tunnel}/{rule}')


def plan(tunnel: str, listen_port: int, relay: tuple[str, int]) -> list[dict]:
    """The exact filter set for one tunnel: validated values only, no strings reach Windows except display names."""
    if not TUNNEL.fullmatch(tunnel or ''):
        raise TransportLockError('Invalid LAN tunnel name.')
    address = ipaddress.ip_address(relay[0])
    if address.version != 4 or address.is_unspecified or address.is_multicast or type(listen_port) is not int \
            or not 1024 <= listen_port <= 65535 or type(relay[1]) is not int or not 1 <= relay[1] <= 65535:
        raise TransportLockError('Invalid relay transport boundary.')
    udp = (CONDITION_PROTOCOL, FWP_UINT8, 17)
    local = (CONDITION_LOCAL_PORT, FWP_UINT16, listen_port)
    rules = []
    for layer in ('in4', 'out4'):
        rules.append({'rule': f'{layer}-relay', 'layer': layer, 'action': FWP_ACTION_PERMIT, 'weight': PERMIT_WEIGHT,
                      'conditions': [udp, local, (CONDITION_REMOTE_ADDRESS, FWP_UINT32, int(address)),
                                     (CONDITION_REMOTE_PORT, FWP_UINT16, relay[1])]})
    for layer in ('in4', 'out4', 'in6', 'out6'):
        rules.append({'rule': f'{layer}-block', 'layer': layer, 'action': FWP_ACTION_BLOCK, 'weight': BLOCK_WEIGHT,
                      'conditions': [udp, local]})
    for rule in rules:
        rule['key'] = filter_key(tunnel, rule['rule'])
    return rules


RULES = ('in4-relay', 'out4-relay', 'in4-block', 'out4-block', 'in6-block', 'out6-block')


class _Api:
    """fwpuclnt.dll entry points with explicit prototypes. Every call returns a Win32/FWP error code."""

    def __init__(self):
        if sys.platform != 'win32':
            raise TransportLockError('The relay-only transport lock requires Windows.')
        dll = ctypes.WinDLL('fwpuclnt.dll')
        handle = ctypes.c_void_p
        proto = {
            'FwpmEngineOpen0': [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(SESSION),
                                ctypes.POINTER(handle)],
            'FwpmEngineClose0': [handle],
            'FwpmTransactionBegin0': [handle, ctypes.c_uint32],
            'FwpmTransactionCommit0': [handle],
            'FwpmTransactionAbort0': [handle],
            'FwpmProviderAdd0': [handle, ctypes.POINTER(PROVIDER_), ctypes.c_void_p],
            'FwpmSubLayerAdd0': [handle, ctypes.POINTER(SUBLAYER_), ctypes.c_void_p],
            'FwpmFilterAdd0': [handle, ctypes.POINTER(FILTER), ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)],
            'FwpmFilterDeleteByKey0': [handle, ctypes.POINTER(GUID)],
            'FwpmFilterGetByKey0': [handle, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.POINTER(FILTER))],
            'FwpmProviderGetByKey0': [handle, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.POINTER(PROVIDER_))],
            'FwpmSubLayerGetByKey0': [handle, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.POINTER(SUBLAYER_))],
            'FwpmFreeMemory0': [ctypes.POINTER(ctypes.c_void_p)],
        }
        for name, argtypes in proto.items():
            function = getattr(dll, name)
            function.argtypes = argtypes
            function.restype = None if name == 'FwpmFreeMemory0' else ctypes.c_uint32
            setattr(self, name, function)


class TransportLock:
    """Install, verify and remove the relay-only filters of one LAN tunnel."""

    def __init__(self, tunnel: str, api=None):
        if not TUNNEL.fullmatch(tunnel or ''):
            raise TransportLockError('Invalid LAN tunnel name.')
        self.tunnel = tunnel
        self._api = api

    @property
    def api(self):
        if self._api is None:
            self._api = _Api()
        return self._api

    def _open(self):
        session = SESSION()
        session.displayData = DISPLAY_DATA('AKENO LAN Play transport lock', None)
        session.txnWaitTimeoutInMSec = 10000
        engine = ctypes.c_void_p()
        code = self.api.FwpmEngineOpen0(None, RPC_C_AUTHN_WINNT, None, ctypes.byref(session), ctypes.byref(engine))
        if code:
            raise TransportLockError(f'Windows Filtering Platform is unavailable (error 0x{code:08x}).')
        return engine

    def _transaction(self, work):
        engine = self._open()
        try:
            code = self.api.FwpmTransactionBegin0(engine, 0)
            if code:
                raise TransportLockError(f'Cannot start a firewall transaction (error 0x{code:08x}).')
            try:
                result = work(engine)
                code = self.api.FwpmTransactionCommit0(engine)
                if code:
                    raise TransportLockError(f'Cannot commit the firewall transaction (error 0x{code:08x}).')
                return result
            except BaseException:
                self.api.FwpmTransactionAbort0(engine)
                raise
        finally:
            self.api.FwpmEngineClose0(engine)

    def _exists(self, engine, getter, key, kind):
        """Look up a persistent AKENO base object first, so no call inside the transaction is expected to fail."""
        found = ctypes.POINTER(kind)()
        code = getter(engine, ctypes.byref(GUID.of(key)), ctypes.byref(found))
        if code in (FWP_E_PROVIDER_NOT_FOUND, FWP_E_SUBLAYER_NOT_FOUND):
            return None
        if code:
            raise TransportLockError(f'Cannot read the AKENO firewall registration (error 0x{code:08x}).')
        try:
            if kind is SUBLAYER_:
                owner = found.contents.providerKey
                return bool(owner) and owner.contents.uuid() == PROVIDER
            return True
        finally:
            self.api.FwpmFreeMemory0(ctypes.byref(ctypes.cast(found, ctypes.c_void_p)))

    def _base_objects(self, engine):
        provider_key = GUID.of(PROVIDER)
        if not self._exists(engine, self.api.FwpmProviderGetByKey0, PROVIDER, PROVIDER_):
            provider = PROVIDER_(GUID.of(PROVIDER), DISPLAY_DATA('AKENO Gaming Suite', 'AKENO LAN Play transport lock'),
                                 FWPM_PROVIDER_FLAG_PERSISTENT)
            code = self.api.FwpmProviderAdd0(engine, ctypes.byref(provider), None)
            if code:
                raise TransportLockError(f'Cannot register the AKENO firewall provider (error 0x{code:08x}).')
        owned = self._exists(engine, self.api.FwpmSubLayerGetByKey0, SUBLAYER, SUBLAYER_)
        if owned is False:
            raise TransportLockError('The AKENO firewall sublayer key is used by another provider; left untouched.')
        if owned is None:
            sublayer = SUBLAYER_(GUID.of(SUBLAYER), DISPLAY_DATA('AKENO LAN Play', 'Relay-only transport lock'),
                                 FWPM_SUBLAYER_FLAG_PERSISTENT, ctypes.pointer(provider_key))
            sublayer.weight = 0xFFFF
            code = self.api.FwpmSubLayerAdd0(engine, ctypes.byref(sublayer), None)
            if code:
                raise TransportLockError(f'Cannot register the AKENO firewall sublayer (error 0x{code:08x}).')

    def _delete_owned(self, engine):
        """Delete this tunnel's filters by their derived keys, after confirming the AKENO provider owns each one."""
        removed = 0
        for rule in RULES:
            key = GUID.of(filter_key(self.tunnel, rule))
            found = ctypes.POINTER(FILTER)()
            code = self.api.FwpmFilterGetByKey0(engine, ctypes.byref(key), ctypes.byref(found))
            if code == FWP_E_FILTER_NOT_FOUND:
                continue
            if code:
                raise TransportLockError(f'Cannot read an AKENO firewall filter (error 0x{code:08x}).')
            try:
                owner = found.contents.providerKey
                owned = bool(owner) and owner.contents.uuid() == PROVIDER
            finally:
                self.api.FwpmFreeMemory0(ctypes.byref(ctypes.cast(found, ctypes.c_void_p)))
            if not owned:
                raise TransportLockError('A firewall filter with an AKENO key is not owned by AKENO; left untouched.')
            code = self.api.FwpmFilterDeleteByKey0(engine, ctypes.byref(key))
            if code and code != FWP_E_FILTER_NOT_FOUND:
                raise TransportLockError(f'Cannot remove an AKENO firewall filter (error 0x{code:08x}).')
            removed += 1
        return removed

    def install(self, listen_port: int, relay: tuple[str, int]):
        """Atomically replace this tunnel's filters (if any) with the relay-only set for ``listen_port``/``relay``."""
        rules = plan(self.tunnel, listen_port, relay)

        def work(engine):
            self._base_objects(engine)
            self._delete_owned(engine)
            keep = []                                   # ctypes buffers must outlive FwpmFilterAdd0
            provider_key = GUID.of(PROVIDER)
            for rule in rules:
                conditions = (FILTER_CONDITION * len(rule['conditions']))(
                    *[_condition(*condition) for condition in rule['conditions']])
                item = FILTER()
                item.filterKey = GUID.of(rule['key'])
                item.displayData = DISPLAY_DATA(f'AKENO LAN Play {self.tunnel} {rule["rule"]}',
                                                'Relay-only transport lock (removed when the LAN session ends)')
                item.flags = FWPM_FILTER_FLAG_PERSISTENT
                item.providerKey = ctypes.pointer(provider_key)
                item.layerKey = GUID.of(LAYERS[rule['layer']])
                item.subLayerKey = GUID.of(SUBLAYER)
                item.weight = VALUE(FWP_UINT8)
                item.weight.value.uint8 = rule['weight']
                item.numFilterConditions = len(rule['conditions'])
                item.filterCondition = ctypes.cast(conditions, ctypes.POINTER(FILTER_CONDITION))
                item.action = ACTION(rule['action'])
                identifier = ctypes.c_uint64()
                keep.append((conditions, item))
                code = self.api.FwpmFilterAdd0(engine, ctypes.byref(item), None, ctypes.byref(identifier))
                if code:
                    raise TransportLockError(f'Cannot install the relay-only transport lock (error 0x{code:08x}, '
                                             f'{rule["rule"]}).')
            return len(rules)
        return self._transaction(work)

    def verify(self) -> bool:
        """True only when every filter of this tunnel exists, is AKENO-owned and persistent."""
        engine = self._open()
        try:
            for rule in RULES:
                key = GUID.of(filter_key(self.tunnel, rule))
                found = ctypes.POINTER(FILTER)()
                if self.api.FwpmFilterGetByKey0(engine, ctypes.byref(key), ctypes.byref(found)):
                    return False
                try:
                    item = found.contents
                    owner = item.providerKey
                    if not owner or owner.contents.uuid() != PROVIDER or not item.flags & FWPM_FILTER_FLAG_PERSISTENT \
                            or item.subLayerKey.uuid() != SUBLAYER:
                        return False
                finally:
                    self.api.FwpmFreeMemory0(ctypes.byref(ctypes.cast(found, ctypes.c_void_p)))
            return True
        finally:
            self.api.FwpmEngineClose0(engine)

    def remove(self) -> int:
        """Atomically remove this tunnel's filters. Returns how many existed."""
        return self._transaction(self._delete_owned)
