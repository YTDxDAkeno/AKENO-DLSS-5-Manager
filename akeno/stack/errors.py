"""User-facing errors of the mod stack engine.

Every failure the UI can show carries a readable message and, where one exists, the next thing the
user can do. Raw Python exceptions are converted with :func:`explain` before they reach a dialog.
"""

from __future__ import annotations

from akeno.i18n import msg

import errno
import urllib.error

from akeno.security.archive import ArchiveError
from akeno.security.downloads import Cancelled, DownloadError, VerificationError


class StackError(Exception):
    """An expected, explainable failure."""

    def __init__(self, message: str, remedy: str = "", code: str = "error"):
        super().__init__(message)
        self.message = message
        self.remedy = remedy
        self.code = code

    def __str__(self) -> str:
        return f"{self.message} {self.remedy}".strip()


class MissingDependency(StackError):
    def __init__(self, what: str, remedy: str = ""):
        super().__init__(msg("Required dependency is missing: {what}.", what=what), remedy, "missing_dependency")


class GameRunning(StackError):
    def __init__(self, names: str = ""):
        text = msg("The game is running ({names}).", names=names) if names else msg("The game is running.")
        super().__init__(text, "Close it completely and try again.", "game_running")


def explain(exc: BaseException) -> StackError:
    """Turn any exception into a message a player can act on."""
    if isinstance(exc, StackError):
        return exc
    if isinstance(exc, Cancelled):
        return StackError("Cancelled. Game files were not changed.", code="cancelled")
    if isinstance(exc, VerificationError):
        return StackError("A downloaded file does not match the reviewed release (SHA-256 mismatch) and was discarded.",
                          "The upstream file may have been replaced. Choose another version or try again later.",
                          "hash_mismatch")
    if isinstance(exc, ArchiveError):
        text = str(exc)
        if "Expected file missing" in text:
            return StackError(msg("The selected release does not contain the expected files: {files}",
                                  files=text.split(": ", 1)[-1]),
                              "Upstream changed the release layout. Choose another version.", "layout_changed")
        if "Hash mismatch" in text:
            return StackError("A file inside the release does not match the reviewed version.",
                              "Choose another version or clear the download cache.", "member_mismatch")
        return StackError(msg("The downloaded archive is damaged or unsafe: {reason}", reason=text),
                          "Delete it from Downloads and download it again.", "bad_archive")
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return StackError("The release file no longer exists upstream (HTTP 404).",
                              "Choose another version; a cached copy can still be reinstalled.", "not_found")
        if exc.code in (403, 429):
            return StackError("GitHub refused the request (rate limit or access denied).",
                              "Wait a few minutes, or add a GitHub token in Settings.", "rate_limited")
        return StackError(msg("The download server answered HTTP {code}.", code=exc.code), "Try again later.", "http")
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return StackError("No connection to the download server.",
                          "Check your internet connection; cached versions still work offline.", "offline")
    if isinstance(exc, DownloadError):
        return StackError(str(exc), "Check your connection and try again.", "download")
    if isinstance(exc, PermissionError):
        return StackError(msg("Windows denied access to a file: {file}", file=exc.filename or str(exc)),
                          "Close the game and any tool using the folder. AKENO does not need administrator rights "
                          "for games in user-writable folders.", "access_denied")
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return StackError("The disk is full.", "Free some space and try again.", "disk_full")
    if isinstance(exc, OSError):
        return StackError(msg("A file operation failed: {reason}", reason=str(exc.strerror or exc)),
                          "Check that the game folder exists and is writable.", "os_error")
    return StackError(msg("Unexpected error: {reason}", reason=str(exc)), "See the log in Settings for details.",
                      "unexpected")
