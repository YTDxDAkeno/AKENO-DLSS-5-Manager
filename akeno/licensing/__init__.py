"""Licensing for the AKENO Gaming Suite: one license unlocks every module; its type only sets the duration.

This package is independent of the mod engine. It never changes game files, mods, backups or settings: when a
license ends, AKENO's modules are locked, and everything is still there when a code is redeemed.
"""

from akeno.licensing.manager import (UNLOCKED, DeviceLimitReached, LicenseManager, LicenseView, Status, manager,
                                     set_manager)

__all__ = ["UNLOCKED", "DeviceLimitReached", "LicenseManager", "LicenseView", "Status", "manager", "set_manager"]
