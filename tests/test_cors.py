"""Which origins the API accepts (app/core/config.py's cors_allowed_origins,
app/main.py's CORSMiddleware wiring) — no database needed, create_app()
itself never touches one."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module

PRODUCTION_SAFE = {
    "secret_key": "x" * 40,
    "credential_encryption_key": "some-key",
}


def _client(monkeypatch, **overrides) -> TestClient:
    from app.core.config import Settings

    settings = Settings(_env_file=None, **overrides)
    monkeypatch.setattr(main_module, "settings", settings)
    return TestClient(main_module.create_app())


def test_local_environment_defaults_to_localhost_3000(monkeypatch) -> None:
    client = _client(monkeypatch, environment="local")
    response = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_local_environment_rejects_a_different_origin(monkeypatch) -> None:
    client = _client(monkeypatch, environment="local")
    response = client.get("/healthz", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_production_stays_closed_without_explicit_origins(monkeypatch) -> None:
    """The failure mode this guards: a real deployment whose panel isn't
    localhost:3000 would otherwise get a silent, confusing default rather
    than an explicit "you have to configure this"."""
    client = _client(monkeypatch, environment="production", **PRODUCTION_SAFE)
    response = client.get("/healthz", headers={"Origin": "http://panel.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_explicit_cors_allowed_origins_overrides_the_default(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        environment="production",
        cors_allowed_origins=["http://panel.example.com"],
        **PRODUCTION_SAFE,
    )
    response = client.get("/healthz", headers={"Origin": "http://panel.example.com"})
    assert response.headers.get("access-control-allow-origin") == "http://panel.example.com"

    other = client.get("/healthz", headers={"Origin": "http://someone-else.example.com"})
    assert "access-control-allow-origin" not in other.headers


def test_comma_separated_env_value_is_split_into_multiple_origins(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        environment="production",
        cors_allowed_origins="http://a.example.com,http://b.example.com",
        **PRODUCTION_SAFE,
    )
    for origin in ("http://a.example.com", "http://b.example.com"):
        response = client.get("/healthz", headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") == origin
