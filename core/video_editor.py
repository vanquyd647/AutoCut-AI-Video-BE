from __future__ import annotations

import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

from config import Settings
from ffmpeg_runtime import collect_media_info
from models.schemas import EditPlan, SpeedEffect, TextOverlay, TransitionSpec, VideoInfo


ProgressCallback = Callable[[str, float, str], Awaitable[None]]


class VideoEditor:
    FOUR_K_WIDTH = 3840
    FOUR_K_HEIGHT = 2160

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def render(
        self,
        project_id: str,
        videos: list[VideoInfo],
        plan: EditPlan,
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        if self.settings.resolve_ffmpeg_path() is None:
            raise RuntimeError("FFmpeg is required to render videos")

        video_lookup = {video.name: video for video in videos}
        work_dir = self.settings.temp_dir / project_id
        work_dir.mkdir(parents=True, exist_ok=True)

        trimmed_files: list[Path] = []
        total_clips = max(len(plan.clips), 1)
        for index, clip in enumerate(plan.clips):
            source = video_lookup.get(clip.source_video)
            if source is None:
                continue
            source_path = Path(source.path)
            source_duration = self._probe_duration(source_path)
            trim_start, trim_end = self._safe_trim_window(source_duration, clip.start, clip.end)
            if trim_end - trim_start <= 0.0:
                continue

            trimmed_path = work_dir / f"clip_{index:02d}.mp4"
            self.trim_clip(source_path, trimmed_path, trim_start, trim_end)
            self._ensure_audio_stream(trimmed_path)

            processed_path = trimmed_path
            speed_effect = self._speed_effect_for_clip(index, plan.speed_effects)
            if speed_effect is not None and abs(speed_effect.rate - 1.0) > 0.01:
                speed_path = work_dir / f"clip_{index:02d}_speed.mp4"
                self.speed_ramp(processed_path, speed_path, speed_effect.rate)
                processed_path = speed_path

            framed_path = work_dir / f"clip_{index:02d}_framed.mp4"
            self.resize_crop(processed_path, framed_path, plan.aspect_ratio)
            self._ensure_audio_stream(framed_path)
            trimmed_files.append(framed_path)
            if on_progress is not None:
                await on_progress(
                    "render",
                    20 + ((index + 1) / total_clips) * 45,
                    f"Rendered clip {index + 1}/{total_clips}",
                )

        if not trimmed_files:
            raise RuntimeError("No clips were produced for rendering")

        merged_path = work_dir / "merged.mp4"
        self.merge_clips(trimmed_files, merged_path, plan.transitions)
        current_output = merged_path

        if plan.color_grading:
            graded_path = work_dir / "graded.mp4"
            self.apply_color_grade(current_output, graded_path, plan.color_grading[0].preset)
            current_output = graded_path
            if on_progress is not None:
                await on_progress("render", 78, "Applied color grading")

        if plan.text_overlays and on_progress is not None:
            # Product requirement: do not burn text marks into output videos.
            await on_progress("render", 88, "Skipped text overlays by policy")

        music_asset = self._resolve_music_asset(plan.music_suggestion)
        if music_asset is not None:
            mixed_path = work_dir / "mixed.mp4"
            self.add_background_music(current_output, mixed_path, music_asset)
            current_output = mixed_path
            if on_progress is not None:
                await on_progress("render", 94, "Mixed background music")

        final_path = self.settings.output_dir / f"{project_id}.mp4"
        shutil.copyfile(current_output, final_path)
        if on_progress is not None:
            await on_progress("export", 100, "Video render complete")
        return final_path

    def trim_clip(self, source: Path, target: Path, start: float, end: float) -> None:
        duration = max(end - start, 0.5)
        self._run_ffmpeg(
            [
                "-y",
                "-ss",
                f"{start}",
                "-i",
                str(source),
                "-t",
                f"{duration}",
                *self._video_encode_args(),
                "-c:a",
                "aac",
                str(target),
            ]
        )

    def merge_clips(
        self,
        clips: list[Path],
        target: Path,
        transitions: list[TransitionSpec] | None = None,
    ) -> None:
        if len(clips) == 1:
            shutil.copyfile(clips[0], target)
            return

        transition_lookup = {transition.at_clip_index: transition for transition in (transitions or [])}
        current = clips[0]
        temp_files: list[Path] = []
        for index in range(1, len(clips)):
            transition = transition_lookup.get(index - 1)
            output_path = target if index == len(clips) - 1 else target.with_name(f"{target.stem}_{index:02d}.mp4")
            self.add_transition(
                current,
                clips[index],
                output_path,
                transition_type=transition.type if transition else "cut",
                duration=transition.duration if transition else 0.3,
            )
            if current not in clips and current.exists():
                current.unlink(missing_ok=True)
            if output_path != target:
                temp_files.append(output_path)
            current = output_path

        for temp_file in temp_files[:-1]:
            temp_file.unlink(missing_ok=True)

    def add_transition(
        self,
        source: Path,
        next_source: Path,
        target: Path,
        transition_type: str,
        duration: float = 0.3,
    ) -> None:
        self._ensure_audio_stream(source)
        self._ensure_audio_stream(next_source)
        if transition_type in {"cut", "hard_cut"}:
            self._concat_pair(source, next_source, target)
            return

        transition_name = self._transition_filter_name(transition_type)
        source_duration = self._probe_duration(source)
        next_duration = self._probe_duration(next_source)
        transition_duration = min(max(duration, 0.1), max(source_duration - 0.1, 0.1), max(next_duration - 0.1, 0.1))
        offset = max(source_duration - transition_duration, 0)
        try:
            self._run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(source),
                    "-i",
                    str(next_source),
                    "-filter_complex",
                    (
                        "[0:v]fps=30,settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p,setsar=1[v0];"
                        "[1:v]fps=30,settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p,setsar=1[v1];"
                        f"[v0][v1]xfade=transition={transition_name}:duration={transition_duration}:offset={offset}[v];"
                        "[0:a]aresample=44100,asetpts=PTS-STARTPTS[a0];"
                        "[1:a]aresample=44100,asetpts=PTS-STARTPTS[a1];"
                        f"[a0][a1]acrossfade=d={transition_duration}[a]"
                    ),
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                    *self._video_encode_args(),
                    "-c:a",
                    "aac",
                    str(target),
                ]
            )
        except RuntimeError:
            self._concat_pair(source, next_source, target)

    def _concat_pair(self, source: Path, next_source: Path, target: Path) -> None:
        self._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-i",
                str(next_source),
                "-filter_complex",
                "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                *self._video_encode_args(),
                "-c:a",
                "aac",
                str(target),
            ]
        )

    def add_text_overlay(self, source: Path, target: Path, overlay: TextOverlay) -> None:
        alpha_expr = self._overlay_alpha_expression(overlay.start, overlay.end, overlay.animation)
        x_expr, y_expr = self._overlay_position(overlay.position)
        text = self._escape_drawtext(overlay.text)
        self._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-vf",
                (
                    "drawtext="
                    f"text='{text}':"
                    "fontcolor=white:fontsize=46:borderw=2:bordercolor=black@0.45:"
                    f"x={x_expr}:y={y_expr}:alpha='{alpha_expr}':"
                    f"enable='between(t,{overlay.start},{overlay.end})'"
                ),
                *self._video_encode_args(),
                "-c:a",
                "copy",
                str(target),
            ]
        )

    def apply_color_grade(self, source: Path, target: Path, preset: str) -> None:
        filter_name = self._color_grade_filter(preset)
        self._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-vf",
                filter_name,
                *self._video_encode_args(),
                "-c:a",
                "copy",
                str(target),
            ]
        )

    def speed_ramp(self, source: Path, target: Path, rate: float) -> None:
        clamped_rate = min(max(rate, 0.5), 2.0)
        self._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-filter_complex",
                (
                    f"[0:v]setpts={1 / clamped_rate}*PTS[v];"
                    f"[0:a]{self._atempo_filter(clamped_rate)}[a]"
                ),
                "-map",
                "[v]",
                "-map",
                "[a]",
                *self._video_encode_args(),
                "-c:a",
                "aac",
                str(target),
            ]
        )

    def resize_crop(self, source: Path, target: Path, aspect_ratio: str) -> None:
        source_info = collect_media_info(self._ffmpeg_path(), source)
        source_width = int(source_info.get("width", 0) or 0)
        source_height = int(source_info.get("height", 0) or 0)
        if self._is_4k_source(source_width, source_height):
            self._run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(source),
                    *self._video_encode_args(),
                    "-c:a",
                    "aac",
                    str(target),
                ]
            )
            return

        width, height = self._resolution_for_ratio(aspect_ratio)
        self._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-vf",
                (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
                ),
                *self._video_encode_args(),
                "-c:a",
                "aac",
                str(target),
            ]
        )

    def add_background_music(self, source: Path, target: Path, music_path: Path) -> None:
        duration = self._probe_duration(source)
        self._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-stream_loop",
                "-1",
                "-i",
                str(music_path),
                "-filter_complex",
                (
                    f"[1:a]atrim=0:{duration},volume=0.18[bg];"
                    "[0:a]volume=1.0[main];"
                    "[main][bg]amix=inputs=2:duration=first:dropout_transition=2[a]"
                ),
                "-map",
                "0:v",
                "-map",
                "[a]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                str(target),
            ]
        )

    def _speed_effect_for_clip(self, clip_index: int, effects: list[SpeedEffect]) -> SpeedEffect | None:
        for effect in effects:
            if effect.clip_index == clip_index:
                return effect
        return None

    def _transition_filter_name(self, transition_type: str) -> str:
        lookup = {
            "fade": "fade",
            "cross_dissolve": "fade",
            "slide_left": "slideleft",
            "slide_right": "slideright",
            "zoom_in": "zoomin",
            "zoom_out": "fadeblack",
            "wipe": "wipeleft",
        }
        return lookup.get(transition_type, "fade")

    def _overlay_alpha_expression(self, start: float, end: float, animation: str) -> str:
        fade_window = min(0.4, max(end - start, 0.1) / 2)
        if animation in {"fade", "slide_up", "typewriter"}:
            return (
                f"if(lt(t,{start}),0,"
                f"if(lt(t,{start + fade_window}),(t-{start})/{fade_window},"
                f"if(lt(t,{end - fade_window}),1,"
                f"if(lt(t,{end}),({end}-t)/{fade_window},0))))"
            )
        return "1"

    def _overlay_position(self, position: str) -> tuple[str, str]:
        lookup = {
            "top-left": ("48", "48"),
            "top-center": ("(w-text_w)/2", "48"),
            "center": ("(w-text_w)/2", "(h-text_h)/2"),
            "bottom-center": ("(w-text_w)/2", "h-(h*0.16)"),
            "bottom-left": ("48", "h-(h*0.16)"),
        }
        x_expr, y_expr = lookup.get(position, lookup["bottom-center"])
        if position == "bottom-center":
            return x_expr, y_expr
        return x_expr, y_expr

    def _escape_drawtext(self, text: str) -> str:
        escaped = text.replace("\\", "\\\\")
        for char in (":", "'", ",", "[", "]"):
            escaped = escaped.replace(char, f"\\{char}")
        return escaped

    def _color_grade_filter(self, preset: str) -> str:
        lookup = {
            "warm": "eq=saturation=1.10:contrast=1.05:brightness=0.03,colorbalance=rs=0.04:gs=0.02:bs=-0.03",
            "cool": "eq=saturation=1.00:contrast=1.04:brightness=0.01,colorbalance=rs=-0.03:gs=0.01:bs=0.05",
            "vintage": "eq=saturation=0.82:contrast=0.94:brightness=0.02,colorbalance=rs=0.07:gs=0.02:bs=-0.04",
            "cinematic": "eq=saturation=1.05:contrast=1.12:brightness=-0.01",
            "vibrant": "eq=saturation=1.22:contrast=1.08:brightness=0.01",
            "bw": "hue=s=0",
        }
        return lookup.get(preset, lookup["vibrant"])

    def _atempo_filter(self, rate: float) -> str:
        stages: list[str] = []
        remaining = rate
        while remaining > 2.0:
            stages.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            stages.append("atempo=0.5")
            remaining /= 0.5
        stages.append(f"atempo={remaining:.4f}")
        return ",".join(stages)

    def _resolve_music_asset(self, suggestion: str) -> Path | None:
        music_dir = self.settings.assets_dir / "music"
        if not music_dir.exists():
            return None
        candidates = [path for path in music_dir.iterdir() if path.suffix.lower() in {".mp3", ".wav", ".m4a"}]
        if not candidates:
            return None

        terms = {token for token in suggestion.lower().replace("-", " ").split() if len(token) > 2}
        for candidate in candidates:
            candidate_name = candidate.stem.lower()
            if any(term in candidate_name for term in terms):
                return candidate
        return candidates[0]

    def _probe_duration(self, path: Path) -> float:
        info = collect_media_info(self._ffmpeg_path(), path)
        duration = float(info.get("duration", 0.0) or 0.0)
        if duration <= 0:
            raise RuntimeError("Unable to probe clip duration")
        return max(duration, 0.1)

    def _has_audio_stream(self, path: Path) -> bool:
        info = collect_media_info(self._ffmpeg_path(), path)
        return bool(info.get("has_audio"))

    def _ensure_audio_stream(self, path: Path) -> None:
        if self._has_audio_stream(path):
            return

        duration = self._probe_duration(path)
        target = path.with_name(f"{path.stem}_audio{path.suffix}")
        self._run_ffmpeg(
            [
                "-y",
                "-i",
                str(path),
                "-f",
                "lavfi",
                "-t",
                f"{duration}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-shortest",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                str(target),
            ]
        )
        target.replace(path)

    def _resolution_for_ratio(self, aspect_ratio: str) -> tuple[int, int]:
        lookup = {
            "9:16": (1080, 1920),
            "16:9": (1920, 1080),
            "1:1": (1080, 1080),
        }
        return lookup.get(aspect_ratio, (1920, 1080))

    def _safe_trim_window(self, source_duration: float, requested_start: float, requested_end: float) -> tuple[float, float]:
        if source_duration <= 0:
            return 0.0, 0.0

        min_window = min(0.5, source_duration)
        max_start = max(source_duration - min_window, 0.0)
        start = min(max(requested_start, 0.0), max_start)
        end = min(max(requested_end, start + min_window), source_duration)
        if end <= start:
            end = min(source_duration, start + min_window)
        return round(start, 3), round(end, 3)

    def _video_encode_args(self) -> list[str]:
        # Favor visual quality over speed, especially for 4K sources.
        return ["-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p"]

    def _is_4k_source(self, width: int, height: int) -> bool:
        return width >= self.FOUR_K_WIDTH or height >= self.FOUR_K_HEIGHT

    def _run_ffmpeg(self, args: list[str]) -> None:
        command = [str(self._ffmpeg_path()), *args]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "FFmpeg command failed")

    def _ffmpeg_path(self) -> Path:
        ffmpeg_path = self.settings.resolve_ffmpeg_path()
        if ffmpeg_path is None:
            raise RuntimeError("FFmpeg is required to render videos")
        return ffmpeg_path