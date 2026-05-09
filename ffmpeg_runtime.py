from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg


_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_SIZE_PATTERN = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")
_AUDIO_STREAM_PATTERN = re.compile(r"Audio:")


def resolve_ffmpeg_path(configured_path: str | Path | None = None) -> Path | None:
    if configured_path:
        candidate = Path(configured_path)
        if candidate.exists():
            return candidate

    system_binary = shutil.which("ffmpeg")
    if system_binary:
        return Path(system_binary)

    try:
        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return None


def ffmpeg_available(configured_path: str | Path | None = None) -> bool:
    return resolve_ffmpeg_path(configured_path) is not None


def collect_media_info(ffmpeg_path: Path, media_path: Path) -> dict[str, object]:
    result = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-i", str(media_path)],
        capture_output=True,
        text=True,
    )
    output = (result.stderr or "") + "\n" + (result.stdout or "")

    duration = _parse_duration(output)
    width, height = _parse_video_size(output)
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": _AUDIO_STREAM_PATTERN.search(output) is not None,
    }


def _parse_duration(output: str) -> float:
    match = _DURATION_PATTERN.search(output)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_video_size(output: str) -> tuple[int, int]:
    match = _VIDEO_SIZE_PATTERN.search(output)
    if not match:
        return 0, 0
    width, height = match.groups()
    return int(width), int(height)