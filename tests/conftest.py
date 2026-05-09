from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import Settings
from core.export import ExportService
from core.progress import ProgressBroker
from core.project_store import ProjectStore
from main import app
from models.schemas import EditPlan, ProgressUpdate, VideoAnalysis


class StubAnalyzer:
    async def analyze_batch(
        self,
        videos: list[tuple[str, Path]],
        api_key: str | None,
        model: str | None = None,
    ) -> list[VideoAnalysis]:
        return [
            VideoAnalysis(
                video_name=video_name,
                scenes=[
                    {
                        "start": 0,
                        "end": 3,
                        "description": f"Scene from {video_name}",
                        "mood": "focused",
                        "quality_score": 8,
                    }
                ],
                highlights=[{"timestamp": 0.5, "reason": "Opening hook", "confidence": 0.9}],
                pacing="fast",
                color_mood="vibrant",
                audio_energy="steady",
                suggested_cuts=[0.5, 2.5],
                content_type="vertical-short",
                summary=f"Stub analysis for {video_name}",
            )
            for video_name, _video_path in videos
        ]


class StubPlanner:
    async def create_plan(
        self,
        analyses: list[VideoAnalysis],
        style: str,
        target_duration: int,
        aspect_ratio: str,
        api_key: str | None,
        model: str | None = None,
    ) -> EditPlan:
        first_video = analyses[0].video_name
        return EditPlan.model_validate(
            {
                "style": style,
                "aspect_ratio": aspect_ratio,
                "target_duration": target_duration,
                "clips": [
                    {
                        "source_video": first_video,
                        "start": 0,
                        "end": 3,
                        "order": 0,
                        "rationale": "Open with the strongest hook",
                    }
                ],
                "transitions": [],
                "text_overlays": [
                    {
                        "text": "AutoCut",
                        "start": 0.0,
                        "end": 1.8,
                        "position": "top-left",
                        "animation": "fade",
                    }
                ],
                "color_grading": [{"preset": "vibrant"}],
                "speed_effects": [],
                "music_suggestion": "test beat",
            }
        )


class StubEditor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def render(self, project_id: str, videos, plan, on_progress=None):
        output_path = self.settings.output_dir / f"{project_id}.mp4"
        output_path.write_bytes(b"fake-mp4-data")
        if on_progress is not None:
            await on_progress("render", 80, "Rendered in test stub")
            await on_progress("export", 100, "Exported in test stub")
        return output_path


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    test_settings = Settings(
        project_root=tmp_path,
        storage_dir=tmp_path / "storage",
        upload_dir=tmp_path / "storage" / "input",
        output_dir=tmp_path / "storage" / "output",
        temp_dir=tmp_path / "storage" / "temp",
        assets_dir=tmp_path / "assets",
        cors_origins=["*"],
    )
    test_settings.ensure_directories()

    with TestClient(app) as test_client:
        app.state.settings = test_settings
        app.state.project_store = ProjectStore(test_settings)
        app.state.progress_broker = ProgressBroker()
        app.state.export_service = ExportService(test_settings)
        app.state.video_analyzer = StubAnalyzer()
        app.state.edit_planner = StubPlanner()
        app.state.video_editor = StubEditor(test_settings)
        yield test_client


@pytest.fixture()
def uploaded_project(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/upload",
        files=[
            ("files", ("clip1.mp4", b"video-one", "video/mp4")),
            ("files", ("clip2.mp4", b"video-two", "video/mp4")),
        ],
    )
    assert response.status_code == 201
    return response.json()