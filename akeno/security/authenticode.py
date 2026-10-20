"""Authenticode signature checks for user-supplied NVIDIA runtime files (Windows only).

``WinVerifyTrust`` answers whether the embedded signature is valid and chains to a trusted root;
the signer's subject name is read with ``CryptQueryObject``/``CertGetNameStringW``. A file counts as
an official NVIDIA binary only when both hold: valid signature *and* signer "NVIDIA Corporation".
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from akeno.paths import is_windows


@dataclass
class SignatureInfo:
    checked: bool          # False when the platform cannot check signatures
    valid: bool
    signer: str | None
    detail: str

    @property
    def is_nvidia(self) -> bool:
        return self.valid and bool(self.signer) and "nvidia" in self.signer.lower()


def verify(path: str) -> SignatureInfo:
    if not is_windows():
        return SignatureInfo(False, False, None, "Signature checks need Windows.")
    if not os.path.isfile(path):
        return SignatureInfo(True, False, None, "File not found.")
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR),
                    ("hFile", wintypes.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                    ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                    ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)), ("dwStateAction", wintypes.DWORD),
                    ("hWVTStateData", wintypes.HANDLE), ("pwszURLReference", wintypes.LPCWSTR),
                    ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
                    ("pSignatureSettings", ctypes.c_void_p)]

    action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    fi = WINTRUST_FILE_INFO(ctypes.sizeof(WINTRUST_FILE_INFO), path, None, None)
    wd = WINTRUST_DATA()
    wd.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    wd.dwUIChoice = 2            # WTD_UI_NONE
    wd.fdwRevocationChecks = 0   # WTD_REVOKE_NONE (offline friendly)
    wd.dwUnionChoice = 1         # WTD_CHOICE_FILE
    wd.pFile = ctypes.pointer(fi)
    wd.dwStateAction = 1         # WTD_STATEACTION_VERIFY
    wd.dwProvFlags = 0x10        # WTD_CACHE_ONLY_URL_RETRIEVAL
    wintrust = ctypes.WinDLL("wintrust")
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    hr = wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(wd))
    wd.dwStateAction = 2         # WTD_STATEACTION_CLOSE
    wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(wd))
    signer = _signer_name(path)
    code = hr & 0xFFFFFFFF
    if hr == 0:
        return SignatureInfo(True, True, signer, "Valid Authenticode signature.")
    reasons = {0x800B0100: "The file is not signed.", 0x80096010: "The signature is invalid (file was modified).",
               0x800B0109: "The signature does not chain to a trusted root.",
               0x800B0101: "The signing certificate has expired."}
    return SignatureInfo(True, False, signer, reasons.get(code, f"Signature check failed (0x{code:08X})."))


def _signer_name(path: str) -> str | None:
    from ctypes import wintypes
    crypt32 = ctypes.WinDLL("crypt32")
    h_store = wintypes.HANDLE()
    h_msg = wintypes.HANDLE()
    enc = wintypes.DWORD()
    ctype = wintypes.DWORD()
    fmt = wintypes.DWORD()
    ok = crypt32.CryptQueryObject(1, ctypes.c_wchar_p(path), 0x400, 0x2, 0, ctypes.byref(enc), ctypes.byref(ctype),
                                  ctypes.byref(fmt), ctypes.byref(h_store), ctypes.byref(h_msg), None)
    # CERT_QUERY_OBJECT_FILE, CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED, CERT_QUERY_FORMAT_FLAG_BINARY
    if not ok:
        return None
    try:
        crypt32.CertEnumCertificatesInStore.restype = ctypes.c_void_p
        crypt32.CertEnumCertificatesInStore.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                               wintypes.LPWSTR, wintypes.DWORD]
        names = []
        ctx = crypt32.CertEnumCertificatesInStore(h_store, None)
        while ctx:
            buf = ctypes.create_unicode_buffer(256)
            crypt32.CertGetNameStringW(ctx, 4, 0, None, buf, 256)  # CERT_NAME_SIMPLE_DISPLAY_TYPE
            if buf.value:
                names.append(buf.value)
            ctx = crypt32.CertEnumCertificatesInStore(h_store, ctx)
        # The leaf (signer) is usually the publisher; prefer a name that is not a CA.
        for n in names:
            if "nvidia" in n.lower() and "ca" not in n.lower().split():
                return n
        leaf = [n for n in names if "root" not in n.lower() and " ca" not in n.lower()]
        return leaf[0] if leaf else (names[0] if names else None)
    finally:
        crypt32.CertCloseStore(h_store, 0)
        crypt32.CryptMsgClose(h_msg)
