from __future__ import annotations

from fastapi.testclient import TestClient


class FailingEditor:
    async def render(self, project_id, videos, plan, on_progress=None):
        raise RuntimeError("FFmpeg is required to render videos")


def test_frontend_backend_smoke_flow(client: TestClient, uploaded_project: dict[str, object]) -> None:
    project_id = uploaded_project["project_id"]

    analyze_response = client.post(
        "/api/analyze",
        json={
            "project_id": project_id,
            "api_key": "test-key",
            "model": "gemini-2.5-flash",
        },
    )
    assert analyze_response.status_code == 200
    analyze_payload = analyze_response.json()
    assert len(analyze_payload["analyses"]) == 2

    edit_response = client.post(
        "/api/edit",
        json={
            "project_id": project_id,
            "api_key": "test-key",
            "style": "tiktok",
            "target_duration": 30,
            "aspect_ratio": "9:16",
            "model": "gemini-2.5-flash",
        },
    )
    assert edit_response.status_code == 200
    edit_payload = edit_response.json()
    assert edit_payload["output_video_url"] == f"/api/export/{project_id}"
    assert edit_payload["progress_ws_url"] == f"/ws/progress/{project_id}"
    assert edit_payload["plan"]["clips"][0]["source_video"] == "clip1.mp4"

    export_response = client.get(edit_payload["output_video_url"])
    assert export_response.status_code == 200
    assert export_response.content == b"fake-mp4-data"

    project_response = client.get(f"/api/project/{project_id}")
    assert project_response.status_code == 200
    project_payload = project_response.json()
    assert project_payload["status"] == "completed"
    assert project_payload["output_video"] == f"/api/export/{project_id}"
    assert project_payload["progress"]["stage"] == "export"


def test_edit_returns_service_unavailable_when_render_fails(
    client: TestClient,
    uploaded_project: dict[str, object],
) -> None:
    project_id = uploaded_project["project_id"]

    analyze_response = client.post(
        "/api/analyze",
        json={
            "project_id": project_id,
            "api_key": "test-key",
            "model": "gemini-2.5-flash",
        },
    )
    assert analyze_response.status_code == 200

    original_editor = client.app.state.video_editor
    client.app.state.video_editor = FailingEditor()
    try:
        edit_response = client.post(
            "/api/edit",
            json={
                "project_id": project_id,
                "api_key": "test-key",
                "style": "tiktok",
                "target_duration": 30,
                "aspect_ratio": "9:16",
                "model": "gemini-2.5-flash",
            },
        )
    finally:
        client.app.state.video_editor = original_editor

    assert edit_response.status_code == 503
    assert edit_response.json()["detail"] == "FFmpeg is required to render videos"

    project_response = client.get(f"/api/project/{project_id}")
    assert project_response.status_code == 200
    project_payload = project_response.json()
    assert project_payload["status"] == "error"
    assert project_payload["progress"]["message"] == "FFmpeg is required to render videos"