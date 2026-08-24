"""Connectivity / empty-response hardening: health, 404 JSON, startup without Ollama."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models.user_model import User


def test_preferences_missing_user_returns_json_404(client, db_session):
    response = client.get("/api/preferences/999999")
    assert response.status_code in (404, 403)
    payload = response.json()
    assert "detail" in payload
    assert response.content
    assert response.headers.get("content-type", "").startswith("application/json")


def test_resume_latest_missing_returns_json_404(client, db_session):
    user = User(name="Conn Test", email="conn-resume@example.com", password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = client.get(f"/api/resume/latest/{user.id}")
    assert response.status_code == 404
    payload = response.json()
    assert "detail" in payload
    assert response.content
    assert response.headers.get("content-type", "").startswith("application/json")


def test_startup_without_ollama_succeeds(monkeypatch, test_engine):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")

    start_calls = []
    stop_calls = []
    monkeypatch.setattr(main_module, "engine", test_engine)
    monkeypatch.setattr(main_module, "ensure_ats_schema", lambda: None)
    monkeypatch.setattr(main_module, "start_daily_scheduler", lambda: start_calls.append(1))
    monkeypatch.setattr(main_module, "stop_daily_scheduler", lambda: stop_calls.append(1))

    with TestClient(main_module.app) as client:
        monkeypatch.setattr(main_module, "test_database_connection", lambda: 1)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["llm"]["configured"] is False

    assert start_calls == [1]
    assert stop_calls == [1]


def test_startup_with_db_unavailable_raises(monkeypatch, test_engine):
    """DATABASE_INIT must fail loudly rather than hang with empty responses."""

    class BoomEngine:
        def begin(self):
            raise RuntimeError("postgres unavailable")

        def connect(self):
            raise RuntimeError("postgres unavailable")

        def dispose(self):
            return None

    monkeypatch.setattr(main_module, "engine", BoomEngine())
    monkeypatch.setattr(
        main_module.Base.metadata,
        "create_all",
        lambda bind: (_ for _ in ()).throw(RuntimeError("postgres unavailable")),
    )
    monkeypatch.setattr(main_module, "ensure_ats_schema", lambda: None)
    monkeypatch.setattr(main_module, "start_daily_scheduler", lambda: None)
    monkeypatch.setattr(main_module, "stop_daily_scheduler", lambda: None)

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        with TestClient(main_module.app):
            pass


def test_global_exception_handler_returns_json_response():
    response = asyncio.run(
        main_module.unhandled_exception_handler(
            request=type("R", (), {"url": type("U", (), {"path": "/boom"})()})(),
            exc=ValueError("hidden"),
        )
    )
    assert response is not None
    assert response.status_code == 500
    assert b"INTERNAL_ERROR" in response.body
    assert b"ValueError" not in response.body
    assert b"hidden" not in response.body


def test_schema_upgrade_inspects_connection_not_engine(monkeypatch, test_engine):
    """Regression: inspect(engine) inside engine.begin() deadlocks Postgres."""
    from app.services import schema_upgrades
    from sqlalchemy import text

    inspected = []

    real_inspect = schema_upgrades.inspect

    def tracking_inspect(target):
        inspected.append(type(target).__name__)
        return real_inspect(target)

    monkeypatch.setattr(schema_upgrades, "engine", test_engine)
    monkeypatch.setattr(schema_upgrades, "Base", main_module.Base)
    monkeypatch.setattr(schema_upgrades, "inspect", tracking_inspect)

    # match_scores may already exist from metadata.create_all; ensure columns path runs.
    schema_upgrades.ensure_ats_schema()
    assert inspected
    # Connection-bound inspect (not Engine) is required to avoid dual-connection deadlock.
    assert any("Connection" in name for name in inspected)
