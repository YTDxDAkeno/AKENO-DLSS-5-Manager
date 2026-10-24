"""The suite's side of Desktop Studio: start the desktop host when something is switched on, tell it about games,
ask for its state, stop it."""

from __future__ import annotations

import logging

from PySide6.QtCore import QProcess
from PySide6.QtNetwork import QLocalSocket

from akeno.desktop import config as cfgmod
from akeno.desktop import ipc
from akeno.desktop.host import self_command

log = logging.getLogger(__name__)


def wanted(cfg: dict) -> bool:
    if not cfg.get("enabled", True):
        return False
    return any(w.get("enabled") and w.get("playlist") for w in cfg.get("wallpapers", {}).values()) or \
        bool(cfg.get("dock", {}).get("enabled")) or bool(cfg.get("widgets"))


class DesktopClient:
    def __init__(self, server_name: str | None = None):
        self.name = server_name or ipc.server_name()
        self.channel: ipc.Channel | None = None

    def _connect(self) -> ipc.Channel | None:
        if self.channel is not None and \
                self.channel.socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            return self.channel
        self.channel = ipc.Channel.open(self.name, {"role": "app"}, timeout_ms=300)
        return self.channel

    def running(self) -> bool:
        return self._connect() is not None

    def start(self) -> bool:
        """Start the host in its own process (it outlives the suite's window)."""
        if self.running():
            return True
        program, args = self_command("--desktop")
        ok, _pid = QProcess.startDetached(program, args)
        if not ok:
            log.warning("could not start the desktop host")
        return bool(ok)

    def ensure(self, cfg: dict | None = None) -> bool:
        """Start the host if the configuration switches anything on; otherwise let it end."""
        cfg = cfg if cfg is not None else cfgmod.load()
        if wanted(cfg):
            if self.running():
                self.send({"cmd": "reload"})
                return True
            return self.start()
        self.send({"cmd": "reload"})          # the host ends itself when nothing is on
        return False

    def send(self, message: dict) -> bool:
        ch = self._connect()
        if ch is None:
            return False
        ch.send(message)
        return True

    def status(self) -> dict | None:
        ch = self._connect()
        if ch is None:
            return None
        answer = ch.request({"cmd": "status"}, timeout_ms=1500)
        return answer if answer and answer.get("cmd") == "status" else None

    def game(self, running: bool) -> None:
        self.send({"cmd": "game", "running": bool(running)})

    def stop(self) -> None:
        self.send({"cmd": "quit"})
        if self.channel is not None:
            self.channel.close()
            self.channel = None
