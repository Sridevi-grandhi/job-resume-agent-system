"""Tests for app/services/schema_upgrades.py.

ensure_ats_schema() talks to app.database.engine directly (module-level import,
bound to the real Postgres URL). Every test here runs it against a disposable
SQLite engine via monkeypatch — never the real database — except for the
DROP NOT NULL branch, which is Postgres-only syntax and is verified by
asserting the exact SQL text that would be sent, via a mocked connection.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

import app.services.schema_upgrades as schema_upgrades
from app.database import Base


@pytest.fixture()
def sqlite_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


def test_ensure_ats_schema_creates_missing_tables(monkeypatch, sqlite_engine):
    monkeypatch.setattr(schema_upgrades, "engine", sqlite_engine)
    monkeypatch.setattr(schema_upgrades, "Base", Base)

    schema_upgrades.ensure_ats_schema()

    tables = set(inspect(sqlite_engine).get_table_names())
    assert "ats_optimization_workflows" in tables
    assert "job_requirement_analyses" in tables
    assert "user_confirmed_skills" in tables


def test_ensure_ats_schema_is_idempotent(monkeypatch, sqlite_engine):
    monkeypatch.setattr(schema_upgrades, "engine", sqlite_engine)
    monkeypatch.setattr(schema_upgrades, "Base", Base)

    schema_upgrades.ensure_ats_schema()
    schema_upgrades.ensure_ats_schema()  # must not raise on second run

    tables = set(inspect(sqlite_engine).get_table_names())
    assert "user_confirmed_skills" in tables


def test_ensure_ats_schema_adds_missing_columns_to_existing_table(monkeypatch, sqlite_engine):
    monkeypatch.setattr(schema_upgrades, "engine", sqlite_engine)
    monkeypatch.setattr(schema_upgrades, "Base", Base)

    with sqlite_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE match_scores ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, resume_id INTEGER, job_id INTEGER, "
            "match_percentage INTEGER)"
        ))

    schema_upgrades.ensure_ats_schema()

    columns = {c["name"] for c in inspect(sqlite_engine).get_columns("match_scores")}
    assert "resume_version" in columns
    assert "optimized_resume_id" in columns
    assert "score_breakdown" in columns
    assert "suggestions" in columns


def test_ensure_ats_schema_skips_columns_that_already_exist(monkeypatch, sqlite_engine):
    monkeypatch.setattr(schema_upgrades, "engine", sqlite_engine)
    monkeypatch.setattr(schema_upgrades, "Base", Base)

    with sqlite_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE match_scores ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, resume_id INTEGER, job_id INTEGER, "
            "match_percentage INTEGER, resume_version VARCHAR(20) DEFAULT 'original')"
        ))

    # Must not attempt to re-add resume_version (would raise "duplicate column").
    schema_upgrades.ensure_ats_schema()

    columns = {c["name"] for c in inspect(sqlite_engine).get_columns("match_scores")}
    assert "resume_version" in columns


def test_ensure_ats_schema_backfills_null_resume_version(monkeypatch):
    """Python's stdlib sqlite3 driver implicitly commits around DDL in a way that
    breaks a single manually-managed SQLAlchemy transaction mixing ALTER TABLE with
    UPDATE (a pysqlite-only quirk, irrelevant to the real Postgres target), so this
    verifies the exact backfill SQL sent via a mocked connection instead of relying
    on SQLite to persist it end-to-end."""
    executed_sql: list[str] = []

    mock_connection = MagicMock()
    mock_connection.execute.side_effect = lambda stmt: executed_sql.append(str(stmt))

    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_connection
    mock_ctx.__exit__.return_value = False

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_ctx

    mock_inspector = MagicMock()
    mock_inspector.get_table_names.return_value = ["match_scores"]
    mock_inspector.get_columns.return_value = [
        {"name": "resume_version"}, {"name": "optimized_resume_id"},
        {"name": "score_breakdown"}, {"name": "suggestions"},
    ]

    module_inspect = schema_upgrades.inspect
    try:
        schema_upgrades.inspect = lambda _engine: mock_inspector
        schema_upgrades.engine = mock_engine
        schema_upgrades.Base = Base
        schema_upgrades.ensure_ats_schema()
    finally:
        schema_upgrades.inspect = module_inspect

    joined = " ".join(executed_sql)
    assert "UPDATE match_scores SET resume_version = 'original'" in joined
    assert "resume_version IS NULL OR resume_version = ''" in joined


def test_ensure_ats_schema_preserves_existing_data(monkeypatch, sqlite_engine):
    monkeypatch.setattr(schema_upgrades, "engine", sqlite_engine)
    monkeypatch.setattr(schema_upgrades, "Base", Base)

    with sqlite_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE match_scores ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, resume_id INTEGER, job_id INTEGER, "
            "match_percentage INTEGER, resume_version VARCHAR(20) DEFAULT 'original')"
        ))
        conn.execute(text(
            "INSERT INTO match_scores (id, user_id, resume_id, job_id, match_percentage, resume_version) "
            "VALUES (1, 7, 8, 9, 42, 'optimized')"
        ))

    schema_upgrades.ensure_ats_schema()

    with sqlite_engine.connect() as conn:
        row = conn.execute(text("SELECT user_id, resume_id, job_id, match_percentage, resume_version FROM match_scores WHERE id = 1")).one()
    assert tuple(row) == (7, 8, 9, 42, "optimized")


def test_ensure_ats_schema_handles_missing_table_gracefully(monkeypatch, sqlite_engine):
    monkeypatch.setattr(schema_upgrades, "engine", sqlite_engine)
    monkeypatch.setattr(schema_upgrades, "Base", Base)

    # No match_scores table pre-created; ensure_ats_schema must not raise.
    schema_upgrades.ensure_ats_schema()
    tables = set(inspect(sqlite_engine).get_table_names())
    assert "match_scores" not in tables


def test_ensure_ats_schema_propagates_database_exceptions(monkeypatch):
    broken_engine = MagicMock()
    broken_engine.begin.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(schema_upgrades, "engine", broken_engine)
    monkeypatch.setattr(schema_upgrades, "Base", Base)

    # Propagate so lifespan can log SCHEMA_UPGRADE_FAILED (and still continue).
    with pytest.raises(RuntimeError, match="connection refused"):
        schema_upgrades.ensure_ats_schema()


def test_ensure_ats_schema_drops_not_null_on_legacy_verification_status_column():
    """DROP NOT NULL is Postgres syntax (unsupported by SQLite's ALTER COLUMN), so
    this branch is verified against a mocked connection/inspector instead of a
    real database — asserting the exact DDL text sent for the legacy column."""
    executed_sql: list[str] = []

    mock_connection = MagicMock()
    mock_connection.execute.side_effect = lambda stmt: executed_sql.append(str(stmt))

    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_connection
    mock_ctx.__exit__.return_value = False

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_ctx

    mock_inspector = MagicMock()
    mock_inspector.get_table_names.return_value = ["match_scores", "user_confirmed_skills"]

    def get_columns(table_name):
        if table_name == "match_scores":
            return [{"name": "resume_version"}, {"name": "optimized_resume_id"},
                     {"name": "score_breakdown"}, {"name": "suggestions"}]
        return [{"name": "verification_status"}, {"name": "evidence_status"}]

    mock_inspector.get_columns.side_effect = get_columns

    import app.services.schema_upgrades as module

    module_inspect = module.inspect
    try:
        module.inspect = lambda _engine: mock_inspector
        module.engine = mock_engine
        module.Base = Base
        module.ensure_ats_schema()
    finally:
        module.inspect = module_inspect

    joined = " ".join(executed_sql)
    assert "ALTER COLUMN verification_status DROP NOT NULL" in joined
    assert "UPDATE user_confirmed_skills SET evidence_status" in joined
