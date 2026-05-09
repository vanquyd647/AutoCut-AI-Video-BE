from __future__ import annotations

import json
from typing import Any

from core.effects import COLOR_PRESETS, TRANSITIONS
from core.gemini_client import GeminiClient
from models.schemas import (
    ClipSegment,
    ColorGrade,
    EditPlan,
    SpeedEffect,
    TransitionSpec,
    VideoAnalysis,
)


class EditPlanner:
    def __init__(self, gemini_client: GeminiClient) -> None:
        self.gemini_client = gemini_client

    async def create_plan(
        self,
        analyses: list[VideoAnalysis],
        style: str,
        target_duration: int,
        aspect_ratio: str,
        api_key: str | None,
        model: str | None = None,
    ) -> EditPlan:
        if api_key:
            try:
                payload = await self.gemini_client.request_json(
                    prompt=self._build_prompt(analyses, style, target_duration, aspect_ratio),
                    api_key=api_key,
                    model=model,
                )
                plan = self._coerce_plan(payload, analyses, style, target_duration, aspect_ratio)
                if plan.clips:
                    return plan
            except Exception:
                pass
        return self._fallback_plan(analyses, style, target_duration, aspect_ratio)

    def _build_prompt(
        self,
        analyses: list[VideoAnalysis],
        style: str,
        target_duration: int,
        aspect_ratio: str,
    ) -> str:
        serialised = json.dumps([analysis.model_dump(mode="json") for analysis in analyses])
        return (
            "Create an edit plan in strict JSON with keys: clips, transitions, color_grading, "
            "speed_effects, music_suggestion. "
            "Do not add text overlays or any text marks into the output plan. "
            f"Style: {style}. Target duration: {target_duration}. Aspect ratio: {aspect_ratio}. "
            f"Analyses: {serialised}"
        )

    def _coerce_plan(
        self,
        payload: dict[str, Any],
        analyses: list[VideoAnalysis],
        style: str,
        target_duration: int,
        aspect_ratio: str,
    ) -> EditPlan:
        known_videos = {analysis.video_name for analysis in analyses}
        clips = [
            ClipSegment(
                source_video=str(item.get("source_video", analyses[0].video_name if analyses else "")),
                start=float(item.get("start", 0)),
                end=float(item.get("end", 0)),
                order=int(item.get("order", index)),
                rationale=str(item.get("rationale", "AI selected segment")),
            )
            for index, item in enumerate(payload.get("clips", []))
            if str(item.get("source_video", analyses[0].video_name if analyses else "")) in known_videos
        ]

        if not clips:
            return self._fallback_plan(analyses, style, target_duration, aspect_ratio)

        transitions = [
            TransitionSpec(
                at_clip_index=int(item.get("at_clip_index", index)),
                type=str(item.get("type", "cut")),
                duration=float(item.get("duration", 0.3)),
            )
            for index, item in enumerate(payload.get("transitions", []))
        ]
        color_grading = [ColorGrade(preset=str(item.get("preset", "vibrant"))) for item in payload.get("color_grading", [])]
        speed_effects = [
            SpeedEffect(
                clip_index=int(item.get("clip_index", 0)),
                rate=float(item.get("rate", 1.0)),
                start=float(item.get("start", 0)),
                end=float(item.get("end", 0)),
            )
            for item in payload.get("speed_effects", [])
        ]

        return EditPlan(
            style=style,
            aspect_ratio=aspect_ratio,
            target_duration=target_duration,
            clips=sorted(clips, key=lambda clip: clip.order),
            transitions=transitions,
            text_overlays=[],
            color_grading=color_grading or [ColorGrade(preset="vibrant")],
            speed_effects=speed_effects,
            music_suggestion=str(payload.get("music_suggestion", "Energetic pop with clean downbeats")),
        )

    def _fallback_plan(
        self,
        analyses: list[VideoAnalysis],
        style: str,
        target_duration: int,
        aspect_ratio: str,
    ) -> EditPlan:
        if not analyses:
            return EditPlan(style=style, aspect_ratio=aspect_ratio, target_duration=target_duration)

        target_clip_count = min(max(len(analyses) * 2, 2), 8)
        per_clip = max(round(target_duration / target_clip_count, 2), 2.0)
        clips: list[ClipSegment] = []
        order = 0
        for analysis in analyses:
            sources = analysis.highlights or []
            if not sources:
                sources = [type("HighlightStub", (), {"timestamp": scene.start, "reason": scene.description}) for scene in analysis.scenes[:2]]
            for highlight in sources[:2]:
                clips.append(
                    ClipSegment(
                        source_video=analysis.video_name,
                        start=round(float(highlight.timestamp), 2),
                        end=round(float(highlight.timestamp) + per_clip, 2),
                        order=order,
                        rationale=str(getattr(highlight, "reason", "Selected highlight")),
                    )
                )
                order += 1
                if len(clips) >= target_clip_count:
                    break
            if len(clips) >= target_clip_count:
                break

        transitions = [
            TransitionSpec(
                at_clip_index=index,
                type=("cut" if style == "youtube" else TRANSITIONS[(index + 1) % len(TRANSITIONS)]),
                duration=0.25 if style == "tiktok" else 0.4,
            )
            for index in range(max(len(clips) - 1, 0))
        ]
        color_grading = [ColorGrade(preset=COLOR_PRESETS[3 if style == "youtube" else 4])]
        speed_effects = [SpeedEffect(clip_index=0, rate=1.1 if style == "tiktok" else 1.0, start=0, end=per_clip)]

        return EditPlan(
            style=style,
            aspect_ratio=aspect_ratio,
            target_duration=target_duration,
            clips=clips,
            transitions=transitions,
            text_overlays=[],
            color_grading=color_grading,
            speed_effects=speed_effects,
            music_suggestion=(
                "Fast electronic beat with clear drops" if style == "tiktok" else "Cinematic groove with steady pulse"
            ),
        )