"""Speech transcription service using OpenAI Whisper."""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import whisper


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

    async def transcribe_async(
        self,
        video_path: str,
        progress_callback: Optional[callable] = None,
        language: Optional[str] = None,
    ) -> dict:
        """Transcribe video asynchronously.
        
        Args:
            video_path: Path to video/audio file.
            progress_callback: Optional callback(progress_percent, message).
            language: Language code (e.g., 'en', 'vi'). Auto-detect if None.
            
        Returns:
            Transcription result with text, segments, and metadata.
        """

        def run_transcription():
            if progress_callback:
                asyncio.create_task(self._report_progress(progress_callback, 10, "Loading model..."))

            model = self._get_model()

            if progress_callback:
                asyncio.create_task(self._report_progress(progress_callback, 30, "Transcribing..."))

            # Transcribe with language detection
            result = model.transcribe(
                video_path,
                language=language,
                verbose=False,
            )

            if progress_callback:
                asyncio.create_task(self._report_progress(progress_callback, 95, "Finalizing..."))

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

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, run_transcription)

    async def _report_progress(self, callback: callable, pct: int, msg: str):
        """Report progress via callback."""
        if asyncio.iscoroutinefunction(callback):
            await callback(pct, msg)
        else:
            callback(pct, msg)

    def transcribe(
        self,
        video_path: str,
        language: Optional[str] = None,
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
