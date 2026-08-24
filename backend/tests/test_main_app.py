"""Tests for app/main.py: app wiring, health endpoints, CORS, lifespan."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import app.main as main_module


def test_app_imports_successfully():
    assert main_module.app is not None
    assert main_module.app.title == "AI Job Search and Resume Optimization API"
    assert main_module.app.version == "1.0.0"


def test_required_routers_are_registered():
    prefixes = set(main_module.app.openapi()["paths"].keys())
    for expected_prefix in (
        "/api/auth/signup",
        "/api/auth/login",
        "/api/preferences",
        "/api/resume/upload",
        "/api/jobs/{user_id}",
        "/api/match/{user_id}",
        "/api/email/generate/{user_id}",
        "/api/scheduler/run/{user_id}",
        "/api/ats-optimizer/generate/{user_id}/{job_id}",
        "/api/ats/analyze-optimize/{user_id}/{job_id}",
    ):
        assert expected_prefix in prefixes, f"missing route {expected_prefix}"


def test_health_endpoint_returns_200(client, monkeypatch):
    monkeypatch.setattr(main_module, "test_database_connection", lambda: 1)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "Backend is running"
    assert payload["database"] == "connected"
    assert set(payload["llm"]) >= {"configured", "provider", "model", "reachable"}
    assert payload["llm"]["reachable"] is None  # /health must not probe Ollama
    assert "api_key" not in payload["llm"]
    assert "LLM_API_KEY" not in str(payload)


def test_home_endpoint_returns_welcome_message(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()


def test_cors_middleware_is_configured():
    middleware_classes = [m.cls.__name__ for m in main_module.app.user_middleware]
    assert "CORSMiddleware" in middleware_classes


def test_cors_origins_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com,https://foo.com")
    reloaded = importlib.reload(main_module)
    try:
        middleware_classes = [m.cls.__name__ for m in reloaded.app.user_middleware]
        assert "CORSMiddleware" in middleware_classes
    finally:
        importlib.reload(main_module)


def test_openapi_schema_includes_major_endpoints(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/health" in schema["paths"]
    assert "/api/auth/signup" in schema["paths"]
    assert "/api/jobs/search/{user_id}" in schema["paths"]


def test_swagger_docs_reachable(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_lifespan_starts_and_stops_scheduler(monkeypatch, test_engine):
    """Startup creates tables/schema and starts the scheduler exactly once;
    shutdown stops it. All real side effects (Postgres DDL, ATS schema upgrade,
    APScheduler) are mocked so this never touches the dev database or a thread."""
    start_calls = []
    stop_calls = []
    schema_calls = []

    monkeypatch.setattr(main_module, "engine", test_engine)
    monkeypatch.setattr(main_module, "ensure_ats_schema", lambda: schema_calls.append(1))
    monkeypatch.setattr(main_module, "start_daily_scheduler", lambda: start_calls.append(1))
    monkeypatch.setattr(main_module, "stop_daily_scheduler", lambda: stop_calls.append(1))

    with TestClient(main_module.app) as _client:
        assert start_calls == [1]
        assert schema_calls == [1]
        assert stop_calls == []

    assert stop_calls == [1]


def test_db_health_check_reports_connected(client, monkeypatch):
    monkeypatch.setattr(main_module, "test_database_connection", lambda: 1)
    response = client.get("/db-health")
    assert response.status_code == 200
    assert response.json() == {"database": "connected"}


def test_db_health_check_reports_not_connected(client, monkeypatch):
    monkeypatch.setattr(main_module, "test_database_connection", lambda: 0)
    response = client.get("/db-health")
    assert response.status_code == 503
    assert response.json()["database"] == "not connected"
    assert response.json()["error_code"] == "DATABASE_UNAVAILABLE"


def test_health_returns_503_json_when_db_unavailable(client, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "test_database_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    response = client.get("/health")
    assert response.status_code == 503
    payload = response.json()
    assert payload["error_code"] == "DATABASE_UNAVAILABLE"
    assert payload["database"] == "not connected"
    assert "llm" in payload


def test_unhandled_exception_returns_500_json(test_engine, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.database import get_db
    import app.database as database_module

    @main_module.app.get("/__test_boom__")
    def _boom():
        raise RuntimeError("intentional test failure")

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    main_module.app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(database_module, "SessionLocal", TestingSessionLocal)
    # raise_server_exceptions=False so the handler response is returned (production behavior)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    try:
        response = client.get("/__test_boom__")
        assert response.status_code == 500
        payload = response.json()
        assert payload == {
            "detail": "Internal server error",
            "error_code": "INTERNAL_ERROR",
        }
        assert response.content
        assert response.headers.get("content-type", "").startswith("application/json")
    finally:
        main_module.app.dependency_overrides.clear()


def test_exception_handler_never_returns_none():
    import asyncio

    result = asyncio.run(
        main_module.unhandled_exception_handler(
            request=type("R", (), {"url": type("U", (), {"path": "/x"})()})(),
            exc=RuntimeError("x"),
        )
    )
    assert result is not None
    assert result.status_code == 500
    assert b"INTERNAL_ERROR" in result.body


def test_init_db_endpoint_creates_tables(client, monkeypatch, test_engine):
    calls = []
    monkeypatch.setattr(main_module, "engine", test_engine)
    monkeypatch.setattr(
        main_module.Base.metadata, "create_all", lambda bind: calls.append(bind)
    )
    response = client.post("/init-db")
    assert response.status_code == 200
    assert calls == [test_engine]
