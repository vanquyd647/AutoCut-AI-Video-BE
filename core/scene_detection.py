"""Scene detection service using PySceneDetect."""
import asyncio
import json
from pathlib import Path
from typing import Optional

import cv2
from scenedetect import AdaptiveDetector, detect


class SceneDetector:
    """Detect scene cuts in video using adaptive luminance detection."""

    def __init__(self, threshold: float = 27.0):
        """Initialize detector.
        
        Args:
            threshold: Luminance change threshold (1-100). Higher = fewer detections.
        """
        self.threshold = threshold

    async def detect_scenes_async(
        self, video_path: str, progress_callback: Optional[callable] = None
    ) -> list[dict]:
        """Detect scenes in video asynchronously.
        
        Args:
            video_path: Path to video file.
            progress_callback: Optional callback(progress_percent, message) for progress updates.
            
        Returns:
            List of scene boundaries with timecodes.
        """

        def run_detection():
            if progress_callback:
                asyncio.create_task(self._report_progress(progress_callback, 10, "Loading video..."))

            # Use adaptive detection for robust scene boundary detection
            scenes = detect(
                video_path,
                AdaptiveDetector(luma_only=False),
                start_in_scene=True,
            )

            if progress_callback:
                asyncio.create_task(self._report_progress(progress_callback, 90, "Finalizing..."))

            return [
                {
                    "timecode": str(scene[0].get_seconds()),
                    "timestamp_ms": int(scene[0].get_seconds() * 1000),
                    "frame_number": scene[0].get_frames(),
                }
                for scene in scenes
            ]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, run_detection)

    async def _report_progress(self, callback: callable, pct: int, msg: str):
        """Report progress via callback."""
        if asyncio.iscoroutinefunction(callback):
            await callback(pct, msg)
        else:
            callback(pct, msg)

    def detect_scenes(self, video_path: str) -> list[dict]:
        """Synchronous scene detection (blocking).
        
        Args:
            video_path: Path to video file.
            
        Returns:
            List of scene boundaries with timecodes.
        """
        scenes = detect(
            video_path,
            AdaptiveDetector(luma_only=False),
            start_in_scene=True,
        )

        return [
            {
                "timecode": str(scene[0].get_seconds()),
                "timestamp_ms": int(scene[0].get_seconds() * 1000),
                "frame_number": scene[0].get_frames(),
            }
            for scene in scenes
        ]

    @staticmethod
    def get_video_duration(video_path: str) -> Optional[float]:
        """Get video duration in seconds using OpenCV."""
        try:
            cap = cv2.VideoCapture(video_path)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            cap.release()

            if fps > 0:
                return frame_count / fps
            return None
        except Exception:
            return None
