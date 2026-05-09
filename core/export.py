from __future__ import annotations

from pathlib import Path

from config import Settings


class ExportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_output_path(self, project_id: str) -> Path | None:
        path = self.settings.output_dir / f"{project_id}.mp4"
        if path.exists():
            return path
        return None