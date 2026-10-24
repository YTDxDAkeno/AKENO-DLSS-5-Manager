"""Messages between the suite, the desktop host and the wallpaper renderers: JSON lines over a local socket
(a named pipe on Windows) that only the current Windows user can open."""

from __future__ import annotations

import getpass
import json
import logging
import os
import re
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def server_name() -> str:
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser() or "user")
    suffix = os.environ.get("AKENO_DESKTOP_SUFFIX", "")          # tests run their own host
    return f"AKENO-Desktop-{user}{suffix}"


class Channel(QObject):
    message = Signal(dict)
    closed = Signal()

    def __init__(self, socket: QLocalSocket):
        super().__init__()
        self.socket = socket
        socket.setParent(self)                 # the socket goes with its channel
        self.hello: dict = {}
        self._buf = b""
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._gone)      # a slot of the channel: Qt drops it with the channel

    @classmethod
    def open(cls, name: str, hello: dict | None = None, timeout_ms: int = 1500) -> "Channel | None":
        sock = QLocalSocket()
        sock.connectToServer(name)
        if not sock.waitForConnected(timeout_ms):
            return None
        ch = cls(sock)
        if hello is not None:
            ch.send(dict(hello, cmd="hello"))
        return ch

    def send(self, message: dict) -> None:
        if self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self.socket.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            self.socket.flush()

    def request(self, message: dict, timeout_ms: int = 2000) -> dict | None:
        """Send and wait for the answer with the same command (the suite asks the host for its status)."""
        answers: list[dict] = []
        collect = answers.append
        self.message.connect(collect)
        try:
            self.send(message)
            deadline = time.monotonic() + timeout_ms / 1000
            while not any(a.get("cmd") == message.get("cmd") for a in answers):
                left = int((deadline - time.monotonic()) * 1000)
                if left <= 0 or not self.socket.waitForReadyRead(left):
                    break
        finally:
            self.message.disconnect(collect)
        return next((a for a in answers if a.get("cmd") == message.get("cmd")), None)

    def _read(self) -> None:
        self._buf += bytes(self.socket.readAll())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                if msg.get("cmd") == "hello":
                    self.hello = msg
                self.message.emit(msg)

    def _gone(self) -> None:
        self.closed.emit()

    def close(self) -> None:
        self.socket.disconnectFromServer()


class Server(QObject):
    connected = Signal(object)                 # Channel

    def __init__(self, name: str | None = None):
        super().__init__()
        self.name = name or server_name()
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self.channels: list[Channel] = []

    def listen(self) -> bool:
        """False when another host already answers on this name. Qt would let a second server take the name
        (a second pipe instance on Windows, a replaced socket file elsewhere), so it is asked first."""
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(500):
            probe.disconnectFromServer()
            return False
        if self.server.listen(self.name):
            return True
        QLocalServer.removeServer(self.name)   # a stale name left by a crash
        return self.server.listen(self.name)

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            ch = Channel(self.server.nextPendingConnection())
            ch.setParent(self)                 # Qt owns it: Python never deletes it while a signal is running
            self.channels.append(ch)
            ch.closed.connect(lambda c=ch: self._drop(c))
            self.connected.emit(ch)

    def _drop(self, ch: Channel) -> None:
        if ch in self.channels:
            self.channels.remove(ch)
            ch.deleteLater()

    def close(self) -> None:
        self.server.close()
