"""Append-only log observations with explicit initialization/evaluation and installation attribution."""
from akeno.diagnostics import neural


def setup(tmp_path):
    (tmp_path / "nvngx_dlssnr.dll").write_bytes(b"fixture runtime")
    (tmp_path / "renodx-dlss5.addon64").write_bytes(b"fixture consumer")
    exe = tmp_path / "FiveM_b3258_GTAProcess.exe"
    exe.write_bytes(b"fixture exe")
    return exe


def test_presence_creation_and_evaluation_are_separate(tmp_path):
    exe = setup(tmp_path)
    assert neural.inspect(tmp_path).state == "installed"
    log = tmp_path / "ReShade.log"
    log.write_text("DLSS Super Resolution created\n")
    assert neural.inspect(tmp_path).state == "installed"
    log.write_text("feature 18 created\n")
    assert neural.inspect(tmp_path).state == "initialized"
    log.write_text("feature 18 created\ninline feature 18 evaluation succeeded\n")
    assert neural.inspect(tmp_path).state == "running"  # historical, not attributed


def test_old_log_retouch_cannot_verify_new_installation(tmp_path):
    exe = setup(tmp_path)
    log = tmp_path / "ReShade.log"
    log.write_text("feature 18 created\ninline feature 18 evaluation succeeded\n")
    cursor = neural.begin("fivem:test", tmp_path, exe, ["nvngx_dlssnr.dll"])
    log.touch()
    assert neural.inspect(tmp_path, cursor, "fivem:test", [str(exe)]).state == "installed"


def test_fresh_append_plus_live_game_and_unchanged_artifacts_is_attributed(tmp_path):
    exe = setup(tmp_path)
    log = tmp_path / "ReShade.log"
    log.write_text("old header\n")
    observation = neural.begin("fivem:test", tmp_path, exe, ["nvngx_dlssnr.dll", "renodx-dlss5.addon64"])
    with log.open("a") as f:
        f.write("feature 18 created\ninline feature 18 evaluation succeeded\n")
    assert neural.inspect(tmp_path, observation, "fivem:test", [str(exe)]).state == "verified"
    assert neural.inspect(tmp_path, observation, "different-game", [str(exe)]).state == "stale"
    assert neural.inspect(tmp_path, observation, "fivem:test", []).state == "running"
    (tmp_path / "nvngx_dlssnr.dll").write_bytes(b"changed")
    assert neural.inspect(tmp_path, observation, "fivem:test", [str(exe)]).state == "stale"


def test_rotated_and_truncated_logs_require_new_observation(tmp_path):
    exe = setup(tmp_path)
    log = tmp_path / "ReShade.log"
    log.write_text("an existing longer prefix\n")
    obs = neural.begin("g", tmp_path, exe, ["nvngx_dlssnr.dll"])
    log.write_text("feature 18 created\ninline feature 18 evaluation succeeded\n")
    assert neural.inspect(tmp_path, obs, "g", [str(exe)]).state == "stale"


def test_init_and_eval_from_different_logs_are_not_combined(tmp_path):
    exe = setup(tmp_path)
    obs = neural.begin("g", tmp_path, exe, ["nvngx_dlssnr.dll"])
    (tmp_path / "OptiScaler.log").write_text("feature 18 created\n")
    (tmp_path / "ReShade.log").write_text("inline feature 18 evaluation succeeded\n")
    assert neural.inspect(tmp_path, obs, "g", [str(exe)]).state != "verified"


def test_latest_failure_and_reinit_reset_previous_success(tmp_path):
    exe = setup(tmp_path)
    log = tmp_path / "ReShade.log"
    log.write_text("feature 18 created\ninline feature 18 evaluation succeeded\nfeature 18 create failed\n")
    assert neural.inspect(tmp_path).state == "failed"
    log.write_text("feature 18 created\ninline feature 18 evaluation succeeded\nfeature 18 created\n")
    assert neural.inspect(tmp_path).state == "initialized"


def test_actual_optiscaler_build_marker_is_only_initialized(tmp_path):
    setup(tmp_path)
    (tmp_path / "OptiScaler.log").write_text("DLSS-NR: pass 1 built at 1280x720 (Natural), first evaluated next frame\n")
    assert neural.inspect(tmp_path).state == "initialized"
