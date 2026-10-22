"""GPU-generation support, runtime verdicts and log-based verification (never inferred)."""

import os
import time

from akeno.core.models import HardwareSupport
from akeno.hardware import support
from helpers import rtx


def test_official_feature_matrix_keeps_technologies_apart():
    assert all(support.OFFICIAL[g]["sr"] and support.OFFICIAL[g]["rr"] for g in support.GENERATIONS)
    assert support.OFFICIAL["RTX 40"]["fg"] and not support.OFFICIAL["RTX 30"]["fg"]
    assert support.OFFICIAL["RTX 50"]["mfg"] and not support.OFFICIAL["RTX 40"]["mfg"]
    assert [k for k, _, _ in support.FEATURES] == ["sr", "rr", "fg", "mfg", "nr"]
    nr_text = dict((k, d) for k, _, d in support.FEATURES)["nr"]
    assert "lowers FPS" in nr_text, "NR must not be presented as a performance feature"


def test_runtime_verdicts_follow_documented_evidence():
    assert support.runtime_verdict("310.8.0.0", "RTX 50").state == "unknown"
    for g in ("RTX 20", "RTX 30", "RTX 40"):
        v = support.runtime_verdict("310.8.0.0", g)
        assert v.state == "unknown" and "exact runtime hash" in v.text and v.evidence["url"].endswith("/131")
    assert support.runtime_verdict("311.2.0.0", "RTX 40").state == "unknown", "newer runtime: no claim either way"
    assert support.runtime_verdict(None, "RTX 50", present=False).state == "none"


def test_gpu_support_report_per_generation():
    r50 = support.gpu_support(rtx("NVIDIA GeForce RTX 5090", device=0x2B85, vram_gb=32), "310.8.0.0", True)
    assert r50.community_state == "ok" and r50.hardware == HardwareSupport.COMMUNITY
    assert "Not an official" in r50.official
    r40 = support.gpu_support(rtx("NVIDIA GeForce RTX 4070", device=0x2786, vram_gb=12), "310.8.0.0", True)
    assert r40.community_state == "warn", "version-only reports cannot establish exact-build failure"
    assert any("signed runtime hash" in x for x in r40.limitations)
    r30_no_rt = support.gpu_support(rtx(), None, False)
    assert r30_no_rt.community_state == "warn" and r30_no_rt.backends
    none = support.gpu_support(None, None, False)
    assert none.community_state == "fail" and not none.backends


def _write(path, text, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime:
        os.utime(path, (mtime, mtime))


def test_verification_requires_explicit_success_lines(tmp_path):
    assert support.verify_from_logs(str(tmp_path)).state == "none"
    _write(tmp_path / "OptiScaler.log", "[info] OptiScaler loaded\n[info] DLSS upscaler created\n")
    assert support.verify_from_logs(str(tmp_path)).state == "none", "an upscaler running is not Neural Rendering"
    _write(tmp_path / "dlss5-feed.log", "NGX calls are routed through OptiScaler DLSS-NR (winmm.dll)\n"
                                         "feature 18 created\n")
    v = support.verify_from_logs(str(tmp_path))
    assert v.state == "initialized" and v.file == "dlss5-feed.log" and "feature 18" in v.line


def test_verification_reports_failure_and_latest_file_wins(tmp_path):
    now = time.time()
    _write(tmp_path / "ReShade.log", "feature 18 created\ninline feature 18 evaluation succeeded\n", now - 100)
    _write(tmp_path / "host64" / "dlss5-feed-host.log", "feature 18 create failed with 0xbad00001\n", now)
    v = support.verify_from_logs(str(tmp_path))
    assert v.state == "failed" and "dlss5-feed-host.log" in v.file


def test_verification_ignores_logs_from_before_the_install(tmp_path):
    _write(tmp_path / "dlss5-feed.log", "neural model (feature 18) loaded\n", time.time() - 3600)
    assert support.verify_from_logs(str(tmp_path), since=time.time() - 60).state == "none"


def test_exact_runtime_hash_is_required_for_known_verdict():
    data = [{"version": "310.9.16.0", "sha256": ["a" * 64], "works_on": ["RTX 40"],
             "evidence": {"source": "synthetic reviewed test"}}]
    assert support.runtime_verdict("310.9.16.0", "RTX 40", data).state == "unknown"
    assert support.runtime_verdict("310.9.16.0", "RTX 40", data, build_sha256="b" * 64).state == "unknown"
    assert support.runtime_verdict("310.9.16.0", "RTX 40", data, build_sha256="a" * 64).state == "works"


def test_reported_5090_driver_61088_gets_explicit_warning():
    gpu = rtx("NVIDIA GeForce RTX 5090", device=0x2B85, vram_gb=32)
    gpu.driver_version = "610.88"
    report = support.gpu_support(gpu, None, False)
    assert any("610.88" in text and "616.56" in text for text in report.limitations)
