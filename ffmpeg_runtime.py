from __future__ import annotations

from functools import lru_cache
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


@lru_cache(maxsize=8)
def _encoders_output(ffmpeg_binary: str) -> str:
    result = subprocess.run(
        [ffmpeg_binary, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
    )
    return ((result.stdout or "") + "\n" + (result.stderr or "")).lower()


def ffmpeg_supports_encoder(ffmpeg_path: Path, encoder_name: str) -> bool:
    if not encoder_name:
        return False
    try:
        return encoder_name.lower() in _encoders_output(str(ffmpeg_path))
    except Exception:
        return False


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