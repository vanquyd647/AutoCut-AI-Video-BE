from __future__ import annotations

from fastapi.testclient import TestClient


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