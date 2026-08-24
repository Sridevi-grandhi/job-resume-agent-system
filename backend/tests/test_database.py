"""Tests for app/database.py: engine/session wiring, get_db lifecycle.

Never touches the real DATABASE_URL from .env — module-level env/import
behavior is exercised via subprocess so app.database is never reloaded
in-process (reloading would desync Base/engine from every already-imported
model module).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_snippet(snippet: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_database_url_is_read_from_environment():
    result = _run_snippet(
        """
        import app.database as db
        assert str(db.engine.url).startswith("postgresql")
        print("OK")
        """,
        {"DATABASE_URL": "postgresql+psycopg://user:pass@localhost:5432/testdb"},
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_missing_database_url_raises_configuration_error():
    # DATABASE_URL is pre-set to "" (empty) in the subprocess environment so that
    # python-dotenv's override=False leaves it alone instead of loading the real
    # value from backend/.env, which sits alongside app/database.py regardless of cwd.
    result = _run_snippet(
        """
        import app.database  # noqa
        """,
        {"DATABASE_URL": ""},
    )
    assert result.returncode != 0
    assert "DATABASE_URL is missing" in result.stderr


def test_engine_uses_configured_url():
    assert database_module.engine is not None
    assert str(database_module.engine.url).split("://")[0].startswith("postgresql")


def test_session_factory_is_created():
    assert database_module.SessionLocal is not None
    session = database_module.SessionLocal.kw
    assert session["bind"] is database_module.engine


def _sqlite_sessionmaker():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return sessionmaker(autocommit=False, autoflush=False, bind=engine), engine


def test_get_db_yields_a_working_session(monkeypatch):
    test_session_local, engine = _sqlite_sessionmaker()
    monkeypatch.setattr(database_module, "SessionLocal", test_session_local)

    generator = database_module.get_db()
    db = next(generator)
    result = db.execute(text("SELECT 1")).scalar()
    assert result == 1

    with pytest.raises(StopIteration):
        next(generator)
    assert not db.is_active or db.in_transaction() is False
    engine.dispose()


def test_get_db_closes_session_after_use(monkeypatch):
    test_session_local, engine = _sqlite_sessionmaker()
    monkeypatch.setattr(database_module, "SessionLocal", test_session_local)

    closed = []
    generator = database_module.get_db()
    db = next(generator)
    original_close = db.close

    def spy_close():
        closed.append(True)
        original_close()

    monkeypatch.setattr(db, "close", spy_close)
    with pytest.raises(StopIteration):
        next(generator)
    assert closed == [True]
    engine.dispose()


def test_get_db_closes_session_even_when_route_code_raises(monkeypatch):
    test_session_local, engine = _sqlite_sessionmaker()
    monkeypatch.setattr(database_module, "SessionLocal", test_session_local)

    closed = []
    generator = database_module.get_db()
    db = next(generator)
    original_close = db.close

    def spy_close():
        closed.append(True)
        original_close()

    monkeypatch.setattr(db, "close", spy_close)

    with pytest.raises(ValueError):
        generator.throw(ValueError("route handler failed"))
    assert closed == [True]
    engine.dispose()


def test_test_database_connection_returns_scalar_result(monkeypatch):
    test_session_local, engine = _sqlite_sessionmaker()
    monkeypatch.setattr(database_module, "engine", engine)
    result = database_module.test_database_connection()
    assert result == 1
    engine.dispose()


def test_no_production_database_used_by_test_fixtures(db_session):
    bind = db_session.get_bind()
    assert str(bind.url).startswith("sqlite")
