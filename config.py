from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ffmpeg_runtime import ffmpeg_available, resolve_ffmpeg_path


class Settings(BaseSettings):
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    project_root: Path = Path(__file__).resolve().parent
    storage_dir: Path = project_root / "storage"
    upload_dir: Path = storage_dir / "input"
    output_dir: Path = storage_dir / "output"
    temp_dir: Path = storage_dir / "temp"
    assets_dir: Path = project_root / "assets"
    ffmpeg_path: Path | None = None
    max_video_size_mb: int = 500
    inline_video_limit_mb: int = 18
    analysis_segment_seconds: int = 25
    allowed_extensions: list[str] = [".mp4", ".mov", ".avi", ".mkv", ".webm"]
    cors_origins: list[str] = ["http://localhost:5173"]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def projects_dir(self) -> Path:
        return self.temp_dir / "projects"

    @field_validator("allowed_extensions", "cors_origins", mode="before")
    @classmethod
    def split_csv_values(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def ensure_directories(self) -> None:
        for path in (
            self.upload_dir,
            self.output_dir,
            self.temp_dir,
            self.projects_dir,
            self.assets_dir / "fonts",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_api_key(self, override: str | None) -> str | None:
        return override or self.gemini_api_key

    def resolve_ffmpeg_path(self) -> Path | None:
        return resolve_ffmpeg_path(self.ffmpeg_path)

    def ffmpeg_available(self) -> bool:
        return ffmpeg_available(self.ffmpeg_path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings