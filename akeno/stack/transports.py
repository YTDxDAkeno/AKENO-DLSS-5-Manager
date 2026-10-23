"""How ReShade (and with it the Feeder) gets into a game, per graphics API and bitness.

DLSS5-Feeder's own installer (``tools/Install-DLSS5Feeder.ps1``) and README are the reference:

* Direct3D 10/11/12: ReShade as ``dxgi.dll`` beside the executable.
* Direct3D 8/9: dgVoodoo2 (``MS\\x86\\D3D9.dll`` or ``D3D8.dll``) translates to Direct3D 11 first; ReShade then goes
  in as ``dxgi.dll``, never ``d3d9.dll`` (dgVoodoo owns that name).
* OpenGL: ReShade as ``opengl32.dll`` beside the executable.
* Vulkan, including games that reach Vulkan through DXVK: ReShade as a Vulkan layer. ReShade's setup registers
  it machine-wide; AKENO registers the same layer for the current Windows user only (no administrator rights),
  with the layer DLL in the game folder. ReShade's ``DllMain`` only activates where a ``ReShade.ini`` sits next to
  the executable, so other Vulkan programs load nothing.

Only one transport is ever used for a game, chosen from its actual API, so incompatible stacks are never combined.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from akeno.i18n import msg

log = logging.getLogger(__name__)

LAYER_KEY = r"Software\Khronos\Vulkan\ImplicitLayers"
MACHINE_LAYER_KEY = r"SOFTWARE\Khronos\Vulkan\ImplicitLayers"     # 32-bit view: WOW6432Node
LAYER_NAME = "VK_LAYER_reshade"


@dataclass(frozen=True)
class Transport:
    id: str                     # dxgi | opengl | dgvoodoo | vulkan_layer | fivem
    api: str
    arch: str                   # x64 | x86
    reshade_name: str | None    # local DLL name; None when ReShade comes in as a Vulkan layer
    translator: str | None = None          # dgVoodoo2 DLL name (D3D9.dll / D3D8.dll)
    machine_layer: str | None = None       # an existing machine-wide ReShade layer manifest that is reused

    @property
    def layer(self) -> bool:
        return self.id == "vulkan_layer"

    @property
    def reshade_dll(self) -> str:
        return "ReShade32.dll" if self.arch == "x86" else "ReShade64.dll"

    @property
    def manifest(self) -> str:
        return "AKENO-" + self.reshade_dll.replace(".dll", ".json")

    def describe(self) -> str:
        if self.id == "dgvoodoo":
            return msg("dgVoodoo2 translates Direct3D {n} to Direct3D 11; ReShade then loads as dxgi.dll.",
                       n=self.api[3:])
        if self.id == "opengl":
            return msg("OpenGL: ReShade loads as opengl32.dll beside the game.")
        if self.layer and self.machine_layer:
            return msg("Vulkan: your machine-wide ReShade layer is used; AKENO enables it for this game only.")
        if self.layer:
            return msg("Vulkan: ReShade is registered as a Vulkan layer for your Windows user (no administrator "
                       "rights) and only activates in this game.")
        return msg("ReShade loads as {name} beside the game.", name=self.reshade_name or "dxgi.dll")


def choose(api: str, arch: str, folder: str | Path | None = None) -> Transport:
    """The single transport for this API and bitness (``folder``: the executable's folder)."""
    arch = "x86" if arch == "x86" else "x64"
    if api == "vulkan":
        return Transport("vulkan_layer", api, arch, None, machine_layer=machine_layer(arch))
    if api == "opengl":
        return Transport("opengl", api, arch, "opengl32.dll")
    if api in ("d3d9", "d3d8"):
        if api == "d3d8" and arch == "x64":
            raise ValueError("dgVoodoo2 has no 64-bit Direct3D 8 build.")
        return Transport("dgvoodoo", api, arch, "dxgi.dll", translator="D3D9.dll" if api == "d3d9" else "D3D8.dll")
    return Transport("dxgi", api, arch, "dxgi.dll")


def layer_manifest(dll_name: str, arch: str) -> str:
    """ReShade's own layer manifest (``setup/ReShade32.json`` / ``ReShade64.json`` of ReShade 6.8.0)."""
    return json.dumps({
        "file_format_version": "1.0.0",
        "layer": {
            "name": LAYER_NAME,
            "type": "GLOBAL",
            "library_path": ".\\" + dll_name,
            "api_version": "1.3.268",
            "implementation_version": "1",
            "description": "crosire's ReShade post-processing injector for " + ("32-bit" if arch == "x86" else "64-bit"),
            "device_extensions": [{"name": "VK_EXT_tooling_info", "spec_version": "1",
                                   "entrypoints": ["vkGetPhysicalDeviceToolPropertiesEXT"]}],
            "disable_environment": {"DISABLE_VK_LAYER_reshade_1": "1"},
        },
    }, indent=1) + "\n"


# -- registry -------------------------------------------------------------------------------------------------------
class _MemoryRegistry:
    """Stand-in outside Windows (tests, Linux development): the same calls, kept in memory."""

    def __init__(self):
        self.user: dict[str, int] = {}
        self.machine: dict[str, dict[str, int]] = {"x64": {}, "x86": {}}

    def user_values(self) -> dict[str, int]:
        return dict(self.user)

    def set_user(self, name: str) -> None:
        self.user[name] = 0

    def delete_user(self, name: str) -> None:
        self.user.pop(name, None)

    def machine_values(self, arch: str) -> dict[str, int]:
        return dict(self.machine.get(arch, {}))


class _WinRegistry:
    def _values(self, root, path: str, view: int = 0) -> dict[str, int]:
        import winreg
        out = {}
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    out[name] = value
                    i += 1
        except OSError:
            pass
        return out

    def user_values(self) -> dict[str, int]:
        import winreg
        return self._values(winreg.HKEY_CURRENT_USER, LAYER_KEY)

    def set_user(self, name: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, LAYER_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0)

    def delete_user(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYER_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def machine_values(self, arch: str) -> dict[str, int]:
        import winreg
        return self._values(winreg.HKEY_LOCAL_MACHINE, MACHINE_LAYER_KEY,
                            winreg.KEY_WOW64_32KEY if arch == "x86" else winreg.KEY_WOW64_64KEY)


REGISTRY = _WinRegistry() if os.name == "nt" else _MemoryRegistry()


def machine_layer(arch: str) -> str | None:
    """Manifest path of an enabled machine-wide ReShade Vulkan layer for this bitness (ReShade's own setup)."""
    for name, value in REGISTRY.machine_values(arch).items():
        if value == 0 and Path(name.replace("\\", "/")).name.casefold().startswith("reshade"):
            return name
    return None


def machine_layer_dll(manifest: str) -> Path | None:
    try:
        data = json.loads(Path(manifest).read_text(encoding="utf-8"))
        lib = data["layer"]["library_path"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    win = PureWindowsPath(lib)                 # manifests use Windows paths (".\\ReShade32.dll")
    path = Path(lib) if win.is_absolute() else Path(manifest).parent.joinpath(*[x for x in win.parts if x != "."])
    return path if path.is_file() else None


def register_user_layer(manifest: str | Path) -> None:
    REGISTRY.set_user(str(manifest))
    log.info("Vulkan layer registered for this user: %s", manifest)


def unregister_user_layer(manifest: str | Path) -> None:
    REGISTRY.delete_user(str(manifest))
    log.info("Vulkan layer unregistered: %s", manifest)


def user_layer_registered(manifest: str | Path) -> bool:
    return str(manifest) in REGISTRY.user_values()
