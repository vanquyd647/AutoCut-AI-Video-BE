"""Scene detection API routes."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from core import SceneDetector
from models.schemas import ProgressUpdate


router = APIRouter(tags=["scenes"])
logger = logging.getLogger(__name__)


@router.post("/scenes/detect/{project_id}")
async def detect_scenes(project_id: str, request: Request) -> dict:
    """Detect scene boundaries in uploaded project videos.
    
    Args:
        project_id: Project ID.
        
    Returns:
        Detected scenes with timecodes for each video.
    """
    store = request.app.state.project_store
    broker = request.app.state.progress_broker

    record = store.get_project(project_id)
    if record is None:
        logger.warning("Project not found: %s", project_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if not record.videos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No videos uploaded yet",
        )

    detector = request.app.state.scene_detector or SceneDetector()
    results = {}

    try:
        # Detect scenes for each video
        for video in record.videos:
            stored_name = video.stored_name
            video_path = Path(video.path)

            if not video_path.exists():
                logger.warning("Video file not found: %s", video_path)
                results[stored_name] = {
                    "video_name": video.name,
                    "stored_name": stored_name,
                    "error": "Video file not found",
                    "scenes": [],
                    "scene_count": 0,
                }
                continue

            # Run scene detection
            scenes = await detector.detect_scenes_async(str(video_path))
            results[stored_name] = {
                "video_name": video.name,
                "stored_name": stored_name,
                "scenes": scenes,
                "scene_count": len(scenes),
            }

            # Publish progress
            progress = ProgressUpdate(
                stage="scenes",
                progress=int((len(results) / len(record.videos)) * 100),
                message=f"Detected scenes in {stored_name}",
            )
            await broker.publish(project_id, progress)

        # Mark completion
        finished = ProgressUpdate(
            stage="scenes",
            progress=100,
            message=f"Scene detection complete: {len(results)} videos processed",
        )
        await broker.publish(project_id, finished)

        # Store scene data in project
        try:
            store.update_project(project_id, scenes=results)
        except Exception:
            logger.exception("Failed to persist scene detection result for project %s", project_id)

        return {
            "project_id": project_id,
            "scenes": results,
            "total_videos": len(record.videos),
        }

    except Exception as exc:
        logger.exception("Scene detection failed for project %s", project_id)
        error_msg = ProgressUpdate(
            stage="error",
            progress=100,
            message=f"Scene detection failed: {str(exc)}",
        )
        await broker.publish(project_id, error_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Scene detection failed",
        ) from exc
