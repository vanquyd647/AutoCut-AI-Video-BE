from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class AppModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class VideoInfo(AppModel):
    name: str
    stored_name: str
    path: str
    size_bytes: int = 0
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None


class Scene(AppModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    description: str
    mood: str
    quality_score: int = Field(default=7, ge=1, le=10)


class Highlight(AppModel):
    timestamp: float = Field(ge=0)
    reason: str
    confidence: float = Field(default=0.75, ge=0, le=1)


class VideoAnalysis(AppModel):
    video_name: str
    scenes: list[Scene] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    pacing: str = "balanced"
    color_mood: str = "neutral"
    audio_energy: str = "steady"
    suggested_cuts: list[float] = Field(default_factory=list)
    content_type: str = "general"
    summary: str = ""


class ClipSegment(AppModel):
    source_video: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    order: int = Field(ge=0)
    rationale: str = ""


class TransitionSpec(AppModel):
    at_clip_index: int = Field(ge=0)
    type: str
    duration: float = Field(default=0.3, ge=0)


class TextOverlay(AppModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    position: str = "bottom-center"
    animation: str = "fade"


class ColorGrade(AppModel):
    preset: str = "vibrant"


class SpeedEffect(AppModel):
    clip_index: int = Field(ge=0)
    rate: float = Field(default=1.0, gt=0)
    start: float = Field(default=0, ge=0)
    end: float = Field(default=0, ge=0)


class EditPlan(AppModel):
    style: str
    aspect_ratio: str
    target_duration: int = Field(gt=0)
    clips: list[ClipSegment] = Field(default_factory=list)
    transitions: list[TransitionSpec] = Field(default_factory=list)
    text_overlays: list[TextOverlay] = Field(default_factory=list)
    color_grading: list[ColorGrade] = Field(default_factory=list)
    speed_effects: list[SpeedEffect] = Field(default_factory=list)
    music_suggestion: str = "Upbeat electronic pulse"


class ProgressUpdate(AppModel):
    stage: str
    progress: float = Field(default=0, ge=0, le=100)
    message: str


class ProjectRecord(AppModel):
    project_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "uploaded"
    videos: list[VideoInfo] = Field(default_factory=list)
    analyses: list[VideoAnalysis] = Field(default_factory=list)
    plan: EditPlan | None = None
    output_video: str | None = None
    progress: ProgressUpdate | None = None


class UploadResponse(AppModel):
    project_id: str
    videos: list[VideoInfo]


class AnalyzeRequest(AppModel):
    project_id: str
    api_key: str | None = None
    model: str = "gemini-2.5-flash"


class AnalyzeResponse(AppModel):
    analyses: list[VideoAnalysis]


class EditRequest(AppModel):
    project_id: str
    api_key: str | None = None
    style: str
    target_duration: int = Field(gt=0)
    aspect_ratio: str
    model: str = "gemini-2.5-flash"


class EditResponse(AppModel):
    plan: EditPlan
    output_video_url: str
    progress_ws_url: str