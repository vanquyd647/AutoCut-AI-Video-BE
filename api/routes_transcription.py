"""Transcription API routes."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from core import TranscriptionService
from models.schemas import ProgressUpdate


router = APIRouter(tags=["transcription"])
logger = logging.getLogger(__name__)


@router.post("/transcribe/{project_id}/{video_name}")
async def transcribe_video(
    project_id: str,
    video_name: str,
    request: Request,
    language: str | None = None,
) -> dict:
    """Transcribe a video using Whisper.
    
    Args:
        project_id: Project ID.
        video_name: Name of the uploaded video file.
        language: Optional language code (e.g., 'en', 'vi').
        
    Returns:
        Transcription result with text, segments, and metadata.
    """
    store = request.app.state.project_store
    broker = request.app.state.progress_broker

    record = store.get_project(project_id)
    if record is None:
        logger.warning("Project not found: %s", project_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Find the video in the project
    video = None
    for v in record.videos:
        if v.name == video_name or v.stored_name == video_name:
            video = v
            break

    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found in project",
        )

    video_path = Path(video.path)

    if not video_path.exists():
        logger.warning("Video file not found: %s", video_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video file not found",
        )

    service = request.app.state.transcription_service
    if service is None:
        # Lazy-load on first use
        service = TranscriptionService(model_name="base")
        request.app.state.transcription_service = service

    try:
        # Publish progress start
        progress = ProgressUpdate(
            stage="transcribe",
            progress=5,
            message=f"Starting transcription of {video_name}...",
        )
        await broker.publish(project_id, progress)

        # Run transcription asynchronously
        async def report_progress(pct: int, msg: str):
            update = ProgressUpdate(stage="transcribe", progress=pct, message=msg)
            await broker.publish(project_id, update)

        result = await service.transcribe_async(str(video_path), progress_callback=report_progress, language=language)

        # Publish completion
        finished = ProgressUpdate(
            stage="transcribe",
            progress=100,
            message=f"Transcription complete: {len(result['segments'])} segments",
        )
        await broker.publish(project_id, finished)

        # Store transcription in project
        transcriptions = dict(record.transcriptions)
        transcriptions[video.stored_name] = result
        try:
            store.update_project(project_id, transcriptions=transcriptions)
        except Exception:
            logger.exception("Failed to persist transcription for project %s", project_id)

        return {
            "project_id": project_id,
            "video_name": video_name,
            "transcription": result,
        }

    except Exception as exc:
        logger.exception("Transcription failed for project %s, video %s", project_id, video_name)
        error_msg = ProgressUpdate(
            stage="error",
            progress=100,
            message=f"Transcription failed: {str(exc)}",
        )
        await broker.publish(project_id, error_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transcription failed",
        ) from exc
