from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    video_acceleration: str = "auto"
    video_threads: int = 0
    video_crf: int = 12
    nvenc_preset: str = "p5"
    nvenc_cq: int = 21
    max_video_size_mb: int = 500
    inline_video_limit_mb: int = 18
    analysis_segment_seconds: int = 25
    auto_ping_enabled: bool = False
    auto_ping_url: str | None = None
    auto_ping_interval_seconds: int = 240
    auto_ping_timeout_seconds: float = 10.0
    auto_ping_initial_delay_seconds: int = 30
    allowed_extensions: list[str] = [".mp4", ".mov", ".avi", ".mkv", ".webm"]
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://localhost:5174"]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def projects_dir(self) -> Path:
        return self.temp_dir / "projects"

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def split_csv_values(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        if value is None:
            return ["*"]

        if isinstance(value, str):
            raw_value = value.strip()
            if not raw_value:
                return ["*"]
            if raw_value == "*":
                return ["*"]

            if raw_value.startswith("["):
                try:
                    parsed_value = json.loads(raw_value)
                except json.JSONDecodeError:
                    parsed_value = None
                else:
                    if isinstance(parsed_value, list):
                        return cls._normalize_cors_origins(parsed_value)

            return cls._normalize_cors_origins(raw_value.split(","))

        if isinstance(value, (list, tuple, set)):
            return cls._normalize_cors_origins(value)

        return value

    @field_validator("video_acceleration", mode="before")
    @classmethod
    def normalize_video_acceleration(cls, value: Any) -> str:
        if value is None:
            return "auto"
        normalized = str(value).strip().lower()
        if normalized in {"auto", "cpu", "nvenc"}:
            return normalized
        return "auto"

    @field_validator("video_threads", mode="before")
    @classmethod
    def normalize_video_threads(cls, value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(parsed, 0)

    @field_validator("video_crf", mode="before")
    @classmethod
    def normalize_video_crf(cls, value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 12
        return min(max(parsed, 0), 51)

    @field_validator("nvenc_preset", mode="before")
    @classmethod
    def normalize_nvenc_preset(cls, value: Any) -> str:
        allowed = {
            "default",
            "slow",
            "medium",
            "fast",
            "p1",
            "p2",
            "p3",
            "p4",
            "p5",
            "p6",
            "p7",
        }
        if value is None:
            return "p5"
        normalized = str(value).strip().lower()
        if normalized in allowed:
            return normalized
        return "p5"

    @field_validator("nvenc_cq", mode="before")
    @classmethod
    def normalize_nvenc_cq(cls, value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 21
        return min(max(parsed, 1), 51)

    @staticmethod
    def _normalize_cors_origins(values: Any) -> list[str]:
        normalized: list[str] = []
        for value in values:
            origin = str(value).strip().strip("\"").strip("'").rstrip("/")
            if not origin:
                continue
            if origin == "*":
                return ["*"]
            normalized.append(origin)
        return normalized or ["*"]

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

    def resolve_auto_ping_url(self) -> str:
        if self.auto_ping_url:
            return self.auto_ping_url
        port = os.getenv("PORT", "8000")
        return f"http://127.0.0.1:{port}/api/health"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings