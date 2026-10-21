"""Opt-in FiveM Legacy integration; intentionally not registered in the launcher UI.

Discovery is read-only. No backend is approved by default. See docs/FIVEM_LEGACY.md.
"""

from .discovery import discover, inspect_installation
from .models import Edition, FiveMInstallation, ServerPolicy

__all__ = ["Edition", "FiveMInstallation", "ServerPolicy", "discover", "inspect_installation"]
