"""Database URL resolution must preserve SQLite connection semantics."""
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.config import Settings


@pytest.mark.parametrize("url", [
    "sqlite+aiosqlite:///:memory:",
    "sqlite+aiosqlite://",
    "sqlite+aiosqlite:///file:shared?mode=memory&cache=shared&uri=true",
])
def test_memory_and_uri_databases_are_preserved(url):
    assert Settings(_env_file=None, DATABASE_URL=url).effective_database_url == url


@pytest.mark.parametrize("filename", ["./traceaudit.db", ".cache/audit.db", "../audit.db"])
def test_relative_database_paths_preserve_directories_and_query(filename):
    import app.config as config

    settings = Settings(_env_file=None, DATABASE_URL=f"sqlite+aiosqlite:///{filename}?timeout=15")
    resolved = make_url(settings.effective_database_url)
    expected = (Path(config.__file__).resolve().parents[2] / filename).resolve().as_posix()
    assert resolved.database == expected
    assert resolved.query["timeout"] == "15"
