from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from config import Settings
from models.schemas import ProgressUpdate, ProjectRecord, VideoInfo


class ProjectStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_directories()

    def _project_file(self, project_id: str) -> Path:
        return self.settings.projects_dir / f"{project_id}.json"

    def create_project(self, project_id: str, videos: list[VideoInfo]) -> ProjectRecord:
        record = ProjectRecord(project_id=project_id, videos=videos)
        self.save_project(record)
        return record

    def get_project(self, project_id: str) -> ProjectRecord | None:
        project_file = self._project_file(project_id)
        if not project_file.exists():
            return None
        return ProjectRecord.model_validate_json(project_file.read_text(encoding="utf-8"))

    def save_project(self, record: ProjectRecord) -> ProjectRecord:
        record.updated_at = datetime.now(UTC)
        self._project_file(record.project_id).write_text(
            record.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return record

    def update_project(self, project_id: str, **changes: object) -> ProjectRecord:
        record = self.get_project(project_id)
        if record is None:
            raise FileNotFoundError(project_id)
        updated = record.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        self.save_project(updated)
        return updated

    def set_progress(self, project_id: str, progress: ProgressUpdate) -> ProjectRecord:
        return self.update_project(project_id, progress=progress, status=progress.stage)

    def delete_project(self, project_id: str) -> None:
        project_file = self._project_file(project_id)
        if project_file.exists():
            project_file.unlink()

        for directory in (
            self.settings.upload_dir / project_id,
            self.settings.temp_dir / project_id,
        ):
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)

        output_file = self.settings.output_dir / f"{project_id}.mp4"
        if output_file.exists():
            output_file.unlink()