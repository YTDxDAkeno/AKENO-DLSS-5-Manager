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
    return home


@pytest.fixture
def db(akeno_home):
    from akeno.core.db import Database
    d = Database(akeno_home / "test.sqlite3")
    yield d
    d.close()
