"""The VPS update path without GitHub access: bundle.sh builds a checksummed bundle of server/ and deploy/ only,
update.sh --bundle installs it (keeping .env, backups and the previous version), --rollback goes back, and
tampered or foreign bundles are refused. Docker is replaced by a recorder; the scripts themselves run for real."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="needs bash and git")


def vps(tmp_path: Path) -> tuple[Path, dict]:
    """An installed server folder as on the VPS (a copy of server/ and deploy/), with its own .env and backup."""
    root = tmp_path / "opt" / "akeno-licensing"
    shutil.copytree(REPO / "server", root / "server", ignore=shutil.ignore_patterns("__pycache__", "*.sqlite3"))
    shutil.copytree(REPO / "deploy", root / "deploy", ignore=shutil.ignore_patterns(".env", "backups"))
    (root / "deploy" / ".env").write_text("POSTGRES_PASSWORD=secret-of-this-vps\n")
    (root / "deploy" / "backups").mkdir()
    (root / "deploy" / "backups" / "db-1.sql.gz").write_text("dump")
    (root / "server" / "OLD_MARKER").write_text("old server")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    (bin_dir / "docker").write_text(f"#!/usr/bin/env bash\necho \"$*\" >> {log}\n"
                                    "case \"$*\" in *last-backup.json*) echo '{\"database\": \"db.sql.gz\", "
                                    "\"keys\": \"keys.tar.gz\"}' ;; esac\nexit 0\n")
    (bin_dir / "docker").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return root, {"env": env, "log": log}


def make_bundle(tmp_path: Path, extra: dict | None = None, name: str = "akeno-licensing-1.0.0-test") -> Path:
    src = tmp_path / "src" / name
    shutil.copytree(REPO / "server", src / "server", ignore=shutil.ignore_patterns("__pycache__", "*.sqlite3"))
    shutil.copytree(REPO / "deploy", src / "deploy", ignore=shutil.ignore_patterns(".env", "backups"))
    (src / "server" / "NEW_MARKER").write_text("new server")
    for rel, data in (extra or {}).items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(data)
    out = tmp_path / f"{name}.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        t.add(src, arcname=name)
    return out


def run(root: Path, ctx: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(root / "deploy" / "update.sh"), *args], env=ctx["env"],
                          capture_output=True, text=True, timeout=120)


def test_bundle_sh_contains_only_server_and_deploy(tmp_path):
    out = subprocess.run(["bash", str(REPO / "deploy" / "bundle.sh")], capture_output=True, text=True, timeout=120,
                         cwd=REPO)
    assert out.returncode == 0, out.stderr
    bundle = REPO / out.stdout.splitlines()[0]
    try:
        with tarfile.open(bundle) as t:
            tops = {Path(m.name).parts[1] for m in t.getmembers() if len(Path(m.name).parts) > 1}
            names = [m.name for m in t.getmembers()]
        assert tops == {"server", "deploy"}, tops
        assert not any(n.endswith("/.env") or "/backups/" in n or n.endswith(".pem") for n in names)
        expected = (bundle.parent / f"{bundle.name}.sha256").read_text().split()[0]
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() == expected
    finally:
        bundle.unlink(missing_ok=True)
        (bundle.parent / f"{bundle.name}.sha256").unlink(missing_ok=True)


def test_update_from_bundle_keeps_vps_data_and_can_roll_back(tmp_path):
    root, ctx = vps(tmp_path)
    bundle = make_bundle(tmp_path)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    out = run(root, ctx, "--bundle", str(bundle), "--sha256", digest)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (root / "server" / "NEW_MARKER").exists() and not (root / "server" / "OLD_MARKER").exists()
    assert (root / "deploy" / ".env").read_text() == "POSTGRES_PASSWORD=secret-of-this-vps\n"
    assert (root / "deploy" / "backups" / "db-1.sql.gz").exists()
    calls = ctx["log"].read_text()
    assert "compose build" in calls and "compose up -d" in calls and "backup-loop.sh once" in calls
    out = run(root, ctx, "--rollback")
    assert out.returncode == 0, out.stdout + out.stderr
    assert (root / "server" / "OLD_MARKER").exists() and not (root / "server" / "NEW_MARKER").exists()
    assert (root / "deploy" / ".env").read_text() == "POSTGRES_PASSWORD=secret-of-this-vps\n"


def test_tampered_or_foreign_bundles_are_refused(tmp_path):
    root, ctx = vps(tmp_path)
    bundle = make_bundle(tmp_path)
    out = run(root, ctx, "--bundle", str(bundle), "--sha256", "0" * 64)
    assert out.returncode != 0 and "does not match" in out.stdout
    out = run(root, ctx, "--bundle", str(bundle))
    assert out.returncode != 0 and "no SHA-256" in out.stdout
    foreign = make_bundle(tmp_path / "f", {"etc/cron.d/evil": "* * * * * root curl evil"}, name="akeno-x")
    out = run(root, ctx, "--bundle", str(foreign), "--sha256", hashlib.sha256(foreign.read_bytes()).hexdigest())
    assert out.returncode != 0 and "refused bundle" in out.stdout
    assert (root / "server" / "OLD_MARKER").exists(), "nothing changed"
    assert not ctx["log"].exists() or "compose build" not in ctx["log"].read_text()


def test_compose_publishes_no_database_port():
    text = (REPO / "deploy" / "docker-compose.yml").read_text()
    db = text.split("akeno-database:", 1)[1].split("\n  akeno-", 1)[0]
    assert "ports:" not in db, "the database is reachable only inside the compose network"
    assert "127.0.0.1" in text
