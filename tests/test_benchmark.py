import pytest

from akeno.benchmark import stats
from akeno.benchmark.service import BenchmarkService


def _csv(times, app="game.exe", extra_rows=""):
    rows = ["Application,ProcessID,PresentRuntime,FrameType,MsBetweenPresents"]
    rows += [f"{app},1,DXGI,Application,{t}" for t in times]
    return "\n".join(rows) + "\n" + extra_rows


def test_parse_presentmon_csv_filters_process_and_generated_frames():
    text = _csv([16.6] * 50) + "other.exe,2,DXGI,Application,5\n" + "game.exe,1,DXGI,NVIDIA DLSS-G,8\n" + "game.exe,1,DXGI,Application,NA\n"
    p = stats.parse_presentmon_csv(text, "game.exe")
    assert len(p.frametimes_ms) == 50 and p.column == "MsBetweenPresents"


def test_parse_rejects_non_presentmon_csv():
    with pytest.raises(stats.CsvError):
        stats.parse_presentmon_csv("a,b\n1,2\n")
    with pytest.raises(stats.CsvError):
        stats.parse_presentmon_csv("")


def test_stats_math():
    ft = [10.0] * 990 + [40.0] * 10
    s = stats.compute(ft)
    assert s.frames == 1000
    assert s.avg_fps == pytest.approx(1000 * 1000 / (9900 + 400), rel=1e-3)
    assert s.low_1pct_fps == pytest.approx(25.0)
    assert s.low_01pct_fps == pytest.approx(25.0)
    assert s.median_frametime_ms == 10.0
    assert s.stutter_ratio == pytest.approx(0.01)
    assert stats.consistency_label(s) in ("fair", "poor")
    smooth = stats.compute([16.0] * 200)
    assert stats.consistency_label(smooth) == "good" and smooth.low_01pct_fps is None
    with pytest.raises(ValueError):
        stats.compute([16.0] * 5)


def test_record_import_export_and_comparability(db, tmp_path):
    svc = BenchmarkService(db, None)
    p = tmp_path / "pm.csv"
    p.write_text(_csv([16.0] * 300))
    s = svc.import_csv("steam:1", str(p), "baseline", {"gpu": "RTX 3080", "resolution": "2560x1440"})
    assert s.id and s.source == "csv-import" and s.stats.avg_fps == pytest.approx(62.5)
    p2 = tmp_path / "pm2.csv"
    p2.write_text(_csv([20.0] * 300))
    svc.import_csv("steam:1", str(p2), "quality", {"gpu": "RTX 4070", "resolution": "2560x1440"})
    sessions = svc.sessions("steam:1")
    assert len(sessions) == 2
    assert "different GPU" in svc.comparable(sessions[0], sessions[1])[0]
    svc.export_csv(sessions, str(tmp_path / "out.csv"))
    svc.export_json(sessions, str(tmp_path / "out.json"))
    assert "avg_fps" in (tmp_path / "out.csv").read_text()
    assert len(svc.frametimes(sessions[0])) == 300
    bad = tmp_path / "bad.csv"
    bad.write_text(_csv([16.0] * 5))
    with pytest.raises(stats.CsvError):
        svc.import_csv("steam:1", str(bad), "baseline", {})


def test_run_without_presentmon_raises(db):
    from akeno.benchmark.service import BenchmarkError
    with pytest.raises(BenchmarkError):
        BenchmarkService(db, None).run("g", "game.exe", "baseline", {}, 5, 0)
