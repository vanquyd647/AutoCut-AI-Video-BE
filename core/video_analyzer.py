from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from config import Settings
from core.gemini_client import GeminiClient
from ffmpeg_runtime import collect_media_info
from models.schemas import Highlight, Scene, VideoAnalysis


class VideoAnalyzer:
    def __init__(self, settings: Settings, gemini_client: GeminiClient) -> None:
        self.settings = settings
        self.gemini_client = gemini_client

    async def analyze(
        self,
        video_path: Path,
        api_key: str | None,
        model: str | None = None,
        video_name: str | None = None,
    ) -> VideoAnalysis:
        resolved_name = video_name or video_path.name
        metadata = self._probe_video(video_path)
        if api_key:
            try:
                raw = await self._request_analysis(video_path, metadata, api_key=api_key, model=model)
                return self._coerce_analysis(resolved_name, raw, metadata)
            except Exception:
                pass
        return self._fallback_analysis(resolved_name, metadata)

    async def analyze_batch(
        self,
        videos: list[tuple[str, Path]],
        api_key: str | None,
        model: str | None = None,
    ) -> list[VideoAnalysis]:
        tasks = [
            self.analyze(video_path, api_key=api_key, model=model, video_name=video_name)
            for video_name, video_path in videos
        ]
        return await asyncio.gather(*tasks)

    def _build_prompt(self, video_path: Path, metadata: dict[str, Any]) -> str:
        return (
            "Analyze this video for an automated social media edit. "
            "Return strict JSON with keys: scenes, highlights, pacing, color_mood, "
            "audio_energy, suggested_cuts, content_type, summary. "
            f"Video name: {video_path.name}. Metadata: {json.dumps(metadata)}"
        )

    def _build_segment_prompt(
        self,
        video_path: Path,
        metadata: dict[str, Any],
        segment_start: float,
        segment_end: float,
        segment_index: int,
        segment_count: int,
    ) -> str:
        return (
            f"{self._build_prompt(video_path, metadata)} "
            "This is one chunk of a larger video. "
            f"Chunk {segment_index + 1} of {segment_count}. "
            f"This chunk covers original timeline {segment_start:.2f}s to {segment_end:.2f}s. "
            "Keep all timestamps relative to the chunk itself."
        )

    async def _request_analysis(
        self,
        video_path: Path,
        metadata: dict[str, Any],
        api_key: str,
        model: str | None,
    ) -> dict[str, Any]:
        inline_limit_bytes = self.settings.inline_video_limit_mb * 1024 * 1024
        if video_path.stat().st_size <= inline_limit_bytes:
            return await self.gemini_client.analyze_video(
                video_path=video_path,
                api_key=api_key,
                model=model,
                prompt=self._build_prompt(video_path, metadata),
            )

        if self.settings.resolve_ffmpeg_path() is None:
            raise RuntimeError("FFmpeg is required for chunked Gemini analysis")

        return await self._analyze_in_segments(video_path, metadata, api_key=api_key, model=model)

    async def _analyze_in_segments(
        self,
        video_path: Path,
        metadata: dict[str, Any],
        api_key: str,
        model: str | None,
    ) -> dict[str, Any]:
        duration = float(metadata.get("duration", 0) or 0)
        segment_length = max(self.settings.analysis_segment_seconds, 10)
        if duration <= 0:
            raise RuntimeError("Cannot segment a video without duration metadata")

        ranges = self._segment_ranges(duration, segment_length)
        segment_results: list[tuple[float, float, dict[str, Any]]] = []

        with tempfile.TemporaryDirectory(prefix="autocut-analysis-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            for index, (segment_start, segment_end) in enumerate(ranges):
                segment_path = temp_dir / f"segment_{index:03d}.mp4"
                self._render_segment(video_path, segment_path, segment_start, segment_end)
                raw = await self.gemini_client.analyze_video(
                    video_path=segment_path,
                    api_key=api_key,
                    model=model,
                    prompt=self._build_segment_prompt(
                        video_path=video_path,
                        metadata=metadata,
                        segment_start=segment_start,
                        segment_end=segment_end,
                        segment_index=index,
                        segment_count=len(ranges),
                    ),
                )
                segment_results.append((segment_start, segment_end, raw))

        return self._merge_segment_payloads(segment_results, metadata)

    def _segment_ranges(self, duration: float, segment_length: int) -> list[tuple[float, float]]:
        ranges: list[tuple[float, float]] = []
        current = 0.0
        while current < duration:
            end = min(duration, current + segment_length)
            ranges.append((round(current, 2), round(end, 2)))
            current = end
        return ranges or [(0.0, max(duration, 0.5))]

    def _render_segment(self, source: Path, target: Path, start: float, end: float) -> None:
        command = [
            str(self._ffmpeg_path()),
            "-y",
            "-ss",
            f"{start}",
            "-i",
            str(source),
            "-t",
            f"{max(end - start, 0.5)}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(target),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "FFmpeg segment render failed")

    def _merge_segment_payloads(
        self,
        segment_results: list[tuple[float, float, dict[str, Any]]],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        scenes: list[dict[str, Any]] = []
        highlights: list[dict[str, Any]] = []
        suggested_cuts: list[float] = []
        pacing_votes: list[str] = []
        color_votes: list[str] = []
        energy_votes: list[str] = []
        type_votes: list[str] = []
        summaries: list[str] = []

        for segment_start, _segment_end, payload in segment_results:
            for scene in payload.get("scenes") or []:
                start = round(segment_start + float(scene.get("start", 0)), 2)
                end = round(segment_start + float(scene.get("end", scene.get("timestamp", 0) or 0)), 2)
                scenes.append(
                    {
                        "start": start,
                        "end": end,
                        "description": scene.get("description", "Scene segment"),
                        "mood": scene.get("mood", "balanced"),
                        "quality_score": scene.get("quality_score", 7),
                    }
                )

            for highlight in payload.get("highlights") or []:
                highlights.append(
                    {
                        "timestamp": round(segment_start + float(highlight.get("timestamp", 0)), 2),
                        "reason": highlight.get("reason", "Highlight"),
                        "confidence": highlight.get("confidence", 0.75),
                    }
                )

            suggested_cuts.extend(
                round(segment_start + float(value), 2) for value in payload.get("suggested_cuts", [])
            )
            pacing_votes.append(str(payload.get("pacing", "balanced")))
            color_votes.append(str(payload.get("color_mood", "neutral")))
            energy_votes.append(str(payload.get("audio_energy", "steady")))
            type_votes.append(str(payload.get("content_type", self._content_type(metadata))))

            summary = str(payload.get("summary", "")).strip()
            if summary:
                summaries.append(summary)

        if not scenes and segment_results:
            first_payload = segment_results[0][2]
            return first_payload

        return {
            "scenes": scenes,
            "highlights": highlights,
            "suggested_cuts": sorted(set(suggested_cuts)),
            "pacing": self._majority_vote(pacing_votes, "balanced"),
            "color_mood": self._majority_vote(color_votes, "neutral"),
            "audio_energy": self._majority_vote(energy_votes, "steady"),
            "content_type": self._majority_vote(type_votes, self._content_type(metadata)),
            "summary": " ".join(summaries[:3]) if summaries else "Chunked analysis completed.",
        }

    def _majority_vote(self, values: list[str], default: str) -> str:
        if not values:
            return default
        counts: dict[str, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return max(counts, key=counts.get)

    def _coerce_analysis(
        self,
        video_name: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> VideoAnalysis:
        raw_scenes = payload.get("scenes") or []
        scenes = [
            Scene(
                start=float(scene.get("start", 0)),
                end=float(scene.get("end", scene.get("timestamp", 0) or 0)),
                description=str(scene.get("description", "Scene segment")),
                mood=str(scene.get("mood", "balanced")),
                quality_score=int(scene.get("quality_score", 7)),
            )
            for scene in raw_scenes
        ]
        scenes = [scene for scene in scenes if scene.end >= scene.start]

        raw_highlights = payload.get("highlights") or []
        highlights = [
            Highlight(
                timestamp=float(item.get("timestamp", 0)),
                reason=str(item.get("reason", "Highlight")),
                confidence=float(item.get("confidence", 0.75)),
            )
            for item in raw_highlights
        ]

        if not scenes:
            return self._fallback_analysis(video_name, metadata)

        return VideoAnalysis(
            video_name=video_name,
            scenes=scenes,
            highlights=highlights,
            pacing=str(payload.get("pacing", "balanced")),
            color_mood=str(payload.get("color_mood", "neutral")),
            audio_energy=str(payload.get("audio_energy", "steady")),
            suggested_cuts=[float(value) for value in payload.get("suggested_cuts", [])],
            content_type=str(payload.get("content_type", self._content_type(metadata))),
            summary=str(payload.get("summary", f"Analysis generated for {video_name}")),
        )

    def _fallback_analysis(self, video_name: str, metadata: dict[str, Any]) -> VideoAnalysis:
        duration = float(metadata.get("duration", 12.0) or 12.0)
        segment = max(duration / 3, 1.5)
        scenes = []
        for index in range(3):
            start = round(index * segment, 2)
            end = round(min(duration, (index + 1) * segment), 2)
            scenes.append(
                Scene(
                    start=start,
                    end=max(start + 0.5, end),
                    description=f"Segment {index + 1} from {video_name}",
                    mood=("energetic" if index == 1 else "focused"),
                    quality_score=7 + (1 if index == 1 else 0),
                )
            )

        highlights = [
            Highlight(timestamp=scene.start, reason=f"Suggested entry for {scene.description}")
            for scene in scenes[:2]
        ]

        return VideoAnalysis(
            video_name=video_name,
            scenes=scenes,
            highlights=highlights,
            pacing="fast" if duration <= 30 else "balanced",
            color_mood="vibrant",
            audio_energy="steady",
            suggested_cuts=[scene.start for scene in scenes],
            content_type=self._content_type(metadata),
            summary=f"Fallback analysis created from metadata for {video_name}.",
        )

    def _content_type(self, metadata: dict[str, Any]) -> str:
        width = int(metadata.get("width", 0) or 0)
        height = int(metadata.get("height", 0) or 0)
        if height > width:
            return "vertical-short"
        if width > height:
            return "horizontal-story"
        return "square-or-unknown"

    def _probe_video(self, video_path: Path) -> dict[str, Any]:
        try:
            info = collect_media_info(self._ffmpeg_path(), video_path)
            return {
                "duration": float(info.get("duration", 0.0) or 0.0),
                "width": int(info.get("width", 0) or 0),
                "height": int(info.get("height", 0) or 0),
                "size_bytes": video_path.stat().st_size,
            }
        except Exception:
            return {"duration": 12.0, "width": 0, "height": 0, "size_bytes": video_path.stat().st_size}

    def _ffmpeg_path(self) -> Path:
        ffmpeg_path = self.settings.resolve_ffmpeg_path()
        if ffmpeg_path is None:
            raise RuntimeError("FFmpeg is required for chunked Gemini analysis")
        return ffmpeg_path