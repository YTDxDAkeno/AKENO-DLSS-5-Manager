"""When wallpapers pause: a game runs, a fullscreen program is in front, the PC runs on battery, a remote desktop
session is active, the desktop is covered by a maximised window, or the CPU is busy. Each rule can be switched
off. The checks read the Windows state (desktop/winapi.py); this module only decides."""

from __future__ import annotations

from dataclasses import dataclass, field

from akeno.i18n import msg


@dataclass
class State:
    game_running: bool = False
    fullscreen_app: bool = False
    on_battery: bool = False
    remote_session: bool = False
    covered: bool = False
    cpu_percent: float = 0.0
    user_paused: bool = False


@dataclass
class Decision:
    paused: bool
    reasons: list = field(default_factory=list)


def decide(rules: dict, state: State) -> Decision:
    reasons = []
    if state.user_paused:
        reasons.append(msg("paused from the tray menu"))
    if rules.get("game", True) and state.game_running:
        reasons.append(msg("a game is running"))
    if rules.get("fullscreen", True) and state.fullscreen_app:
        reasons.append(msg("a fullscreen program is in front"))
    if rules.get("battery", True) and state.on_battery:
        reasons.append(msg("the PC runs on battery"))
    if rules.get("remote", True) and state.remote_session:
        reasons.append(msg("a remote desktop session is active"))
    if rules.get("covered", True) and state.covered:
        reasons.append(msg("the desktop is covered by a maximised window"))
    limit = rules.get("cpu_percent")
    if limit and state.cpu_percent >= float(limit):
        reasons.append(msg("the CPU is busy ({p} %)", p=round(state.cpu_percent)))
    return Decision(bool(reasons), reasons)
