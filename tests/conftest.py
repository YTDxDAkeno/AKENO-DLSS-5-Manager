import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def akeno_home(tmp_path, monkeypatch):
    home = tmp_path / "akeno_home"
    home.mkdir()
    monkeypatch.setenv("AKENO_HOME", str(home))
    # each test talks to its own (or no) Desktop Studio host, never to one of the user's
    monkeypatch.setenv("AKENO_DESKTOP_SUFFIX", f"-pytest-{os.getpid()}-{tmp_path.name}"[:60])
    # test downloads are not sent to the runner's real antivirus (tests/test_system_security.py covers the hook
    # with stand-ins, tools/system_check.py runs it against Microsoft Defender on the Windows runner)
    monkeypatch.setattr("akeno.services.AppServices._antivirus_check",
                        lambda self, path, token=None: {"status": "not_checked", "engine": "", "threats": [],
                                                        "detail": "test run"})
    return home


@pytest.fixture
def db(akeno_home):
    from akeno.core.db import Database
    d = Database(akeno_home / "test.sqlite3")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def finish_background_tasks():
    """No worker of a UI test may outlive it. pytest-qt closes the test's widgets before fixture teardown; a closed
    page cancels its operation, so this normally returns at once. A task still running into the next test's setup
    caused a segfault in the Linux CI run (a worker reporting to a page that was being destroyed)."""
    yield
    workers = sys.modules.get("akeno.ui.workers")
    if workers is None:
        return
    from PySide6.QtCore import QCoreApplication
    if QCoreApplication.instance() is not None:
        assert workers.drain(60000), "a background task outlived its test"
