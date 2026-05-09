from __future__ import annotations

from fastapi.testclient import TestClient

from config import Settings
from main import settings as app_settings


def test_upload_get_and_delete_project(client: TestClient, uploaded_project: dict[str, object]) -> None:
    project_id = uploaded_project["project_id"]

    project_response = client.get(f"/api/project/{project_id}")
    assert project_response.status_code == 200
    project_payload = project_response.json()
    assert project_payload["project_id"] == project_id
    assert len(project_payload["videos"]) == 2

    delete_response = client.delete(f"/api/project/{project_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"

    missing_response = client.get(f"/api/project/{project_id}")
    assert missing_response.status_code == 404


def test_healthcheck_route(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "gemini_model" in payload


def test_healthcheck_preflight_options(client: TestClient) -> None:
    origin = app_settings.cors_origins[0] if app_settings.cors_origins and app_settings.cors_origins[0] != "*" else "https://frontend.example.com"

    response = client.options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_settings_parses_json_cors_origins() -> None:
    settings = Settings(cors_origins='["https://frontend.example.com/", "https://admin.example.com"]')
    assert settings.cors_origins == ["https://frontend.example.com", "https://admin.example.com"]


def test_settings_parses_plain_env_cors_origins(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://auto-cut-ai-video-fe.vercel.app")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["https://auto-cut-ai-video-fe.vercel.app"]