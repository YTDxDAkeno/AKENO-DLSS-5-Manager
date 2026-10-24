"""The playback level of the default audio device, for audio-reactive wallpapers and the visualizer widget.

Windows' documented endpoint meter (``IAudioMeterInformation``, Core Audio) reports the current peak level of what
is playing, per channel. It needs no capture, no driver and no permission. It is a level, not a frequency
spectrum: the 128 values handed to web wallpapers (``livelyAudioListener`` / ``akenoAudio``) are the recent level
history, newest last, scaled 0..1.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid
from collections import deque

log = logging.getLogger(__name__)

CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioMeterInformation = "C02216F6-8C67-4B5B-9D00-D008E73E0064"
CLSCTX_ALL = 0x17


def _guid(text: str):
    return (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)


def _method(obj, index: int, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


class AudioMeter:
    def __init__(self, history: int = 128):
        self.history = deque([0.0] * history, maxlen=history)
        self._meter = None
        self._failed = False

    def _open(self) -> bool:
        if self._meter is not None:
            return True
        if self._failed or sys.platform != "win32":
            return False
        try:
            ole32 = ctypes.WinDLL("ole32")
            ole32.CoInitializeEx(None, 0)          # already initialised by Qt is fine (RPC_E_CHANGED_MODE)
            enum = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(ctypes.byref(_guid(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
                                        ctypes.byref(_guid(IID_IMMDeviceEnumerator)), ctypes.byref(enum))
            if hr != 0 or not enum:
                raise OSError(hr, "MMDeviceEnumerator")
            device = ctypes.c_void_p()
            get_default = _method(enum, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
            hr = get_default(enum, 0, 1, ctypes.byref(device))          # eRender, eMultimedia
            if hr != 0 or not device:
                raise OSError(hr, "no playback device")
            meter = ctypes.c_void_p()
            activate = _method(device, 3, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_void_p))
            hr = activate(device, ctypes.byref(_guid(IID_IAudioMeterInformation)), CLSCTX_ALL, None,
                          ctypes.byref(meter))
            if hr != 0 or not meter:
                raise OSError(hr, "IAudioMeterInformation")
            self._meter = meter
            return True
        except (OSError, AttributeError) as exc:
            log.info("audio level unavailable: %s", exc)
            self._failed = True
            return False

    def peak(self) -> float | None:
        """The current playback peak (0..1), or None without a playback device."""
        if not self._open():
            return None
        value = ctypes.c_float()
        hr = _method(self._meter, 3, ctypes.POINTER(ctypes.c_float))(self._meter, ctypes.byref(value))
        if hr != 0:
            self._meter = None                      # device changed (headphones plugged in): reopen next time
            return None
        level = max(0.0, min(1.0, float(value.value)))
        self.history.append(level)
        return level

    def values(self) -> list[float]:
        return list(self.history)
