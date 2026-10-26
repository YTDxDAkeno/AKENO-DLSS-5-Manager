"""Restoring what AKENO changed without an active license: the welcome screen's "Restore game files" can only undo
(original game files, FPS Booster settings, paused programs) and never unlocks a feature."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from akeno.recovery import Recovery, resume_paused_processes


class FakeStack:
    def __init__(self):
        self.calls = []
        self.txs = [{"game_id": "g1", "game_name": "Cyber Game", "target_dir": "C:\\Games\\Cyber\\bin",
                     "backend_id": "stack", "finished_at": "2026-10-20T10:11:12"},
                    {"game_id": "g2", "game_name": "Old Game", "target_dir": "C:\\Games\\Old",
                     "backend_id": "dlss_swap", "created_at": "2025-01-02T03:04:05"}]

    def installed(self):
        return list(self.txs)

    def active(self, game_id):
        return {"id": 1} if game_id == "g1" else None

    def uninstall(self, game_id, force=False):
        self.calls.append(("uninstall", game_id, force))
        self.txs = [t for t in self.txs if t["game_id"] != game_id]
        return SimpleNamespace(ok=True, conflicts=[], messages=["Original files restored."])

    def remove_legacy(self, game_id):
        self.calls.append(("remove_legacy", game_id))
        self.txs = [t for t in self.txs if t["game_id"] != game_id]
        return SimpleNamespace(ok=True, conflicts=[], messages=["Restored."])


class FakeBoost:
    def __init__(self):
        self.state = {"games": {"g1": {"persistent": {}}}, "sessions": {"gone": {"name": "Removed Game"}}}
        self.restored = []

    def restore(self, game):
        self.restored.append(game.id)
        self.state["games"].pop(game.id, None)
        self.state["sessions"].pop(game.id, None)
        return ["power_plan"]


@pytest.fixture
def services(akeno_home):
    from akeno.system.store import SystemStore
    store = SystemStore(akeno_home / "system.sqlite3")
    games = {"g1": SimpleNamespace(id="g1", name="Cyber Game")}
    gaming = SimpleNamespace(restore=lambda: {"resumed": [{"pid": 1}], "gone": []})
    s = SimpleNamespace(stack=FakeStack(), boost=FakeBoost(), library=SimpleNamespace(get=games.get),
                        system_tools=SimpleNamespace(store=store, network=SimpleNamespace(gaming=gaming)))
    yield s
    store.close()


def test_items_and_undo_only(services):
    rec = Recovery(services)
    items = rec.items()
    assert [i["game"] for i in items["installs"]] == ["Cyber Game", "Old Game"]
    assert items["installs"][0]["installed"] == "2026-10-20 10:11"
    assert {b["game"] for b in items["boost"]} == {"Cyber Game", "Removed Game"}
    rec.restore_install("g1")
    rec.restore_install("g2")
    assert services.stack.calls == [("uninstall", "g1", False), ("remove_legacy", "g2")]
    assert rec.restore_boost("gone") == ["power_plan"]          # a game no longer in the library still goes back
    assert rec.resume_paused()["resumed"]
    # by construction there is nothing that installs, applies or starts anything
    public = {n for n in dir(Recovery) if not n.startswith("_")}
    assert public == {"items", "restore_install", "restore_boost", "resume_paused"}


def test_resume_paused_processes_at_start(akeno_home):
    from akeno.system.store import SystemStore
    store = SystemStore()
    store.set("network.gaming.paused", [{"pid": 999_999, "created": 1.0, "name": "ghost.exe"}])
    store.close()
    out = resume_paused_processes()
    assert out["gone"] and not out["resumed"]                   # never touches a different process
    store = SystemStore()
    assert store.get("network.gaming.paused") == []
    store.close()
    assert resume_paused_processes() == {}


def test_welcome_screen_restore_dialog(services, qtbot, monkeypatch):
    from akeno.ui.recovery_dialog import RecoveryDialog
    monkeypatch.setattr("akeno.ui.recovery_dialog.confirm", lambda *a, **k: True)
    d = RecoveryDialog(lambda: services)
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: not d.busy and d.rec is not None, timeout=5000)
    assert "Choose what to put back" in d.status.text()
    d._install("g1", "Cyber Game")
    qtbot.waitUntil(lambda: ("uninstall", "g1", False) in services.stack.calls and not d.busy, timeout=5000)
    qtbot.waitUntil(lambda: not d.busy, timeout=5000)
    assert [i["game_id"] for i in d.rec.items()["installs"]] == ["g2"]


def test_gate_offers_restore_only_with_recovery(env, qtbot):  # noqa: F811
    from akeno.ui.license_gate import LicenseGate
    from test_licensing_client import FakeApi
    make, validate, _ = env
    FakeApi.answers["/licenses/validate"] = validate(result="no_license") | {"license": {"status": "none"}}
    g = LicenseGate(make(), recovery=lambda: None)
    qtbot.addWidget(g)
    g.show()
    assert g.restore_link.isVisible()
    plain = LicenseGate(make())
    qtbot.addWidget(plain)
    plain.show()
    assert not plain.restore_link.isVisible()


from test_licensing_client import env  # noqa: E402,F401  (the fixture)
