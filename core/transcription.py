"""Speech transcription service using OpenAI Whisper."""
import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import whisper

from ffmpeg_runtime import collect_media_info, resolve_ffmpeg_path


logger = logging.getLogger(__name__)


class TranscriptionService:
    """Transcribe audio/video using OpenAI Whisper."""

    def __init__(self, model_name: str = "base"):
        """Initialize transcription service.
        
        Args:
            model_name: Whisper model size (tiny, base, small, medium, large).
                       Smaller = faster, larger = more accurate.
        """
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        """Lazy load Whisper model."""
        if self._model is None:
            logger.info(f"Loading Whisper {self.model_name} model...")
            self._model = whisper.load_model(self.model_name)
        return self._model

    @staticmethod
    def _configure_whisper_ffmpeg(ffmpeg_binary: str) -> None:
        """Patch Whisper audio loader to use an explicit ffmpeg executable path."""
        import whisper.audio as whisper_audio

        def load_audio_with_binary(file: str, sr: int = whisper_audio.SAMPLE_RATE):
            cmd = [
                ffmpeg_binary,
                "-nostdin",
                "-threads",
                "0",
                "-i",
                file,
                "-f",
                "s16le",
                "-ac",
                "1",
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(sr),
                "-",
            ]
            try:
                out = subprocess.run(cmd, capture_output=True, check=True).stdout
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"Failed to load audio: {exc.stderr.decode()}") from exc
            except FileNotFoundError as exc:
                raise RuntimeError(f"FFmpeg binary not found: {ffmpeg_binary}") from exc

            return whisper_audio.np.frombuffer(out, whisper_audio.np.int16).flatten().astype(whisper_audio.np.float32) / 32768.0

        whisper_audio.load_audio = load_audio_with_binary

    async def transcribe_async(
        self,
        video_path: str,
        progress_callback: Callable[[int, str], Any] | None = None,
        language: str | None = None,
    ) -> dict:
        """Transcribe video asynchronously.
        
        Args:
            video_path: Path to video/audio file.
            progress_callback: Optional callback(progress_percent, message).
            language: Language code (e.g., 'en', 'vi'). Auto-detect if None.
            
        Returns:
            Transcription result with text, segments, and metadata.
        """

        loop = asyncio.get_running_loop()

        def report_progress_threadsafe(pct: int, msg: str) -> None:
            if not progress_callback:
                return

            # Whisper runs in a worker thread, so progress callbacks must be
            # marshaled back onto the running event loop.
            loop.call_soon_threadsafe(
                asyncio.create_task,
                self._report_progress(progress_callback, pct, msg),
            )

        def run_transcription():
            report_progress_threadsafe(10, "Loading model...")

            ffmpeg_path = resolve_ffmpeg_path()
            if ffmpeg_path is not None:
                self._configure_whisper_ffmpeg(str(ffmpeg_path))
                ffmpeg_dir = str(ffmpeg_path.parent)
                current_path = os.environ.get("PATH", "")
                if ffmpeg_dir not in current_path.split(os.pathsep):
                    os.environ["PATH"] = (
                        f"{ffmpeg_dir}{os.pathsep}{current_path}" if current_path else ffmpeg_dir
                    )

                media_info = collect_media_info(ffmpeg_path, Path(video_path))
                if not bool(media_info.get("has_audio")):
                    report_progress_threadsafe(95, "No audio stream detected. Returning empty transcription.")
                    return {
                        "text": "",
                        "language": language or "unknown",
                        "segments": [],
                        "duration": float(media_info.get("duration", 0.0)),
                    }

            model = self._get_model()

            report_progress_threadsafe(30, "Transcribing...")

            # Transcribe with language detection
            result = model.transcribe(
                video_path,
                language=language,
                verbose=False,
            )

            report_progress_threadsafe(95, "Finalizing...")

            return {
                "text": result.get("text", ""),
                "language": result.get("language", "unknown"),
                "segments": [
                    {
                        "id": seg.get("id"),
                        "start": seg.get("start"),
                        "end": seg.get("end"),
                        "text": seg.get("text", "").strip(),
                    }
                    for seg in result.get("segments", [])
                ],
                "duration": result.get("duration", 0),
            }

        return await loop.run_in_executor(None, run_transcription)

    async def _report_progress(self, callback: Callable[[int, str], Any], pct: int, msg: str):
        """Report progress via callback."""
        if asyncio.iscoroutinefunction(callback):
            await callback(pct, msg)
        else:
            callback(pct, msg)

    def transcribe(
        self,
        video_path: str,
        language: str | None = None,
    ) -> dict:
        """Synchronous transcription (blocking).
        
        Args:
            video_path: Path to video/audio file.
            language: Language code (e.g., 'en', 'vi'). Auto-detect if None.
            
        Returns:
            Transcription result with text, segments, and metadata.
        """
        logger.info(f"Transcribing {video_path}...")
        model = self._get_model()

        result = model.transcribe(
            video_path,
            language=language,
            verbose=False,
        )

        return {
            "text": result.get("text", ""),
            "language": result.get("language", "unknown"),
            "segments": [
                {
                    "id": seg.get("id"),
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "text": seg.get("text", "").strip(),
                }
                for seg in result.get("segments", [])
            ],
            "duration": result.get("duration", 0),
        }
