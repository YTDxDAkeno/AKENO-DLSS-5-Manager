"""AKENO licensing server: accounts, activation codes, licenses, devices and signed offline licenses.

The server knows one generic entitlement, ``akeno-suite``. Which modules a license unlocks is decided by the
Windows app, so new AKENO versions never need a server update. Nothing else runs here: no game files, mods,
wallpapers, installers or synchronisation data.
"""

__version__ = "1.0.0"
API_VERSION = "v1"
ENTITLEMENT = "akeno-suite"
