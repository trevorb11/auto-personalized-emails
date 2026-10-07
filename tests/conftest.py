import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import data.database as database


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Fresh SQLite DB per test, dry-run on, no outbound API keys."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(config, "EMAIL_LLM_PERSONALIZE", False)
    database.init_db()
    yield
