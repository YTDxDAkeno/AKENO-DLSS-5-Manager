import pytest

from akeno.backends.base import BackendContext
from akeno.backends.optiscaler_nr import OptiScalerNrAdapter
from akeno.compat.engine import CompatDatabase, CompatDbError, evaluate
from akeno.core.models import (CompatibilityResult, CompatStatus, DetectedFile, FrameStats, Game, GpuInfo,
                               GraphicsApi, AntiCheatRisk)
from akeno.hardware import gpu_db
from akeno.recommend.engine import recommend
from helpers import rtx, system_with

RT = {"stored_path": "x", "sha256": "0" * 64}


def game(name="Control Resonant", **kw):
    g = Game(id="steam:1", name=name, install_dir="C:/g", launcher="manual", exe_path="C:/g/game.exe",
             api=GraphicsApi.D3D12, architecture="x64", upscaler_inputs=["dlss"],
             existing_mods=[DetectedFile("nvngx_dlss.dll", "C:/g/nvngx_dlss.dll", "dlss")],
             anti_cheat=AntiCheatRisk.NONE_FOUND)
    for k, v in kw.items():
        setattr(g, k, v)
    return g


def ctx(gpu):
    return BackendContext(system=system_with(gpu), gpu=gpu, runtime_lookup=lambda n: RT)


def test_bundled_db_is_valid_and_every_entry_has_evidence():
    db = CompatDatabase.load()
    assert db.data["games"]
    for g in db.data["games"]:
        assert g["evidence"]["source"] and g["evidence"]["url"].startswith("https://")


def test_db_validation_rejects_entries_without_evidence():
    bad = {"schema": 1, "version": "1", "games": [{"name": "x", "status": "confirmed", "backend": "b", "match": {},
                                                    "evidence": {}}]}
    with pytest.raises(CompatDbError):
        CompatDatabase.validate(bad)
    with pytest.raises(CompatDbError):
        CompatDatabase.validate({"schema": 2})


def test_statuses():
    db = CompatDatabase.load()
    a = OptiScalerNrAdapter()
    r40 = rtx("NVIDIA GeForce RTX 4070", 0x2786, 12)
    assert evaluate(db, game(), a, ctx(r40)).status == CompatStatus.CONFIRMED
    r30 = rtx()
    res = evaluate(db, game(), a, ctx(r30))
    assert res.status == CompatStatus.EXPERIMENTAL and any(f.code == "other_gpu" for f in res.findings)
    assert evaluate(db, game("Unknown Indie"), a, ctx(r40)).status == CompatStatus.INSUFFICIENT
    gtx = gpu_db.classify(GpuInfo("NVIDIA GeForce GTX 1660", vendor_id=0x10DE, device_id=0x2184))
    assert evaluate(db, game(), a, ctx(gtx)).status == CompatStatus.INCOMPATIBLE
    ac = evaluate(db, game("ELDEN RING", launcher="steam", launcher_id="1245620"), a, ctx(r40))
    assert ac.status == CompatStatus.BLOCKED
    assert evaluate(db, game(api=GraphicsApi.OPENGL), a, ctx(r40)).status == CompatStatus.INCOMPATIBLE


def fs(fps, low=None, stutter=0.0):
    ft = 1000 / fps
    return {"stats": FrameStats(1000, 60, fps, ft, ft, ft, ft, low or fps * 0.85, None, 0.5, stutter).__dict__}


def sess(label, fps, i, **kw):
    d = {"id": i, "game_id": "steam:1", "label": label, "gpu": "NVIDIA GeForce RTX 3080", "resolution": "2560x1440"}
    d.update(fs(fps, **kw))
    return d


def _rec(sessions, target=60, gpu=None, compat_status=CompatStatus.EXPERIMENTAL, g=None):
    gpu = gpu or rtx()
    return recommend(g or game(), system_with(gpu), gpu, OptiScalerNrAdapter(),
                     CompatibilityResult(compat_status, "optiscaler_nr"), sessions, target)


def test_recommend_without_measurements_is_provisional():
    r = _rec([])
    assert r.provisional and r.basis == "hardware" and r.profile_id in ("performance", "balanced", "quality")
    assert any("Not measured yet" in x for x in r.reasons)
    assert all(o.measured is None and o.estimated_fps is None for o in r.outlooks)


def test_recommend_from_measurements_picks_best_quality_meeting_target():
    r = _rec([sess("baseline", 100, 1), sess("quality", 55, 2), sess("balanced", 70, 3), sess("performance", 85, 4)])
    assert r.basis == "measured" and not r.provisional and r.profile_id == "balanced"
    r2 = _rec([sess("quality", 40, 1), sess("balanced", 45, 2), sess("performance", 50, 3)])
    assert r2.profile_id == "disabled" and "too large" in r2.headline


def test_recommend_suggests_cheaper_untested_profile():
    r = _rec([sess("quality", 40, 1)])
    assert r.profile_id == "performance" and r.provisional


def test_recommend_estimate_from_baseline_is_labelled():
    r = _rec([sess("baseline", 120, 1)])
    assert r.basis == "estimated" and r.provisional
    assert all(o.estimated_fps for o in r.outlooks) and any("Estimated" in x for x in r.reasons)


def test_recommend_ignores_other_gpu_sessions_and_blocked():
    other = sess("quality", 200, 1)
    other["gpu"] = "Other GPU"
    assert _rec([other]).basis == "hardware"
    blocked = _rec([], compat_status=CompatStatus.BLOCKED)
    assert blocked.profile_id == "disabled" and not blocked.provisional


def test_profiles_are_api_specific_and_params_validate():
    a = OptiScalerNrAdapter()
    dx11 = game(api=GraphicsApi.D3D11)
    assert all("DlssNr.PreSr" not in p.params for p in a.profiles(dx11))
    assert "DlssNr.PreSr" not in [s.key for s in a.param_specs(dx11)]
    spec = next(s for s in a.param_specs() if s.key == "DlssNr.WorkingScale")
    assert spec.coerce("0.75") == 0.75
    with pytest.raises(ValueError):
        spec.coerce(5)
