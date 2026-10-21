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
        return self.checked and self.valid and " ".join((self.signer or "").casefold().split()) == "nvidia corporation"


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
    wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
    hr = wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(wd))
    try:
        signer = _trusted_signer_name(wd.hWVTStateData) if hr == 0 else None
    finally:
        wd.dwStateAction = 2         # WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(wd))
    code = hr & 0xFFFFFFFF
    if hr == 0:
        return SignatureInfo(True, True, signer, "Valid Authenticode signature.")
    reasons = {0x800B0100: "The file is not signed.", 0x80096010: "The signature is invalid (file was modified).",
               0x800B0109: "The signature does not chain to a trusted root.",
               0x800B0101: "The signing certificate has expired."}
    return SignatureInfo(True, False, signer, reasons.get(code, f"Signature check failed (0x{code:08X})."))


def _trusted_signer_name(state) -> str | None:
    """Read the actual verified signature's leaf, never an arbitrary embedded certificate."""
    from ctypes import wintypes
    if not state:
        return None
    class PROVIDER_CERT(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pCert", ctypes.c_void_p)]
    class PROVIDER_SIGNER(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("sftVerifyAsOf", wintypes.FILETIME),
                    ("csCertChain", wintypes.DWORD), ("pasCertChain", ctypes.POINTER(PROVIDER_CERT))]
    wintrust, crypt32 = ctypes.WinDLL("wintrust"), ctypes.WinDLL("crypt32")
    wintrust.WTHelperProvDataFromStateData.argtypes = [wintypes.HANDLE]
    wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.POINTER(PROVIDER_SIGNER)
    data = wintrust.WTHelperProvDataFromStateData(state)
    signer = wintrust.WTHelperGetProvSignerFromChain(data, 0, False, 0) if data else None
    if not signer or not signer.contents.csCertChain or not signer.contents.pasCertChain:
        return None
    certificate = signer.contents.pasCertChain[0].pCert
    crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.LPWSTR, wintypes.DWORD]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD
    length = crypt32.CertGetNameStringW(certificate, 4, 0, None, None, 0)
    if length < 2 or length > 4096:
        return None
    name = ctypes.create_unicode_buffer(length)
    crypt32.CertGetNameStringW(certificate, 4, 0, None, name, length)
    return name.value or None
