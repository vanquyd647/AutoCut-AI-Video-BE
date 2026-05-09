from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from models.schemas import AnalyzeRequest, AnalyzeResponse, ProgressUpdate


router = APIRouter(tags=["analyze"])
logger = logging.getLogger(__name__)


async def _mark_failed(store, broker, project_id: str, message: str) -> None:
    failed = ProgressUpdate(stage="error", progress=100, message=message)
    try:
        store.set_progress(project_id, failed)
    except Exception:
        logger.exception("Failed to persist error progress for project %s", project_id)

    try:
        await broker.publish(project_id, failed)
    except Exception:
        logger.exception("Failed to publish error progress for project %s", project_id)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_project(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    settings = request.app.state.settings
    store = request.app.state.project_store
    broker = request.app.state.progress_broker
    analyzer = request.app.state.video_analyzer

    try:
        record = store.get_project(payload.project_id)
    except Exception as exc:
        logger.exception("Failed to load project %s", payload.project_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project data is unavailable",
        ) from exc

    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    api_key = settings.resolve_api_key(payload.api_key)
    starting = ProgressUpdate(stage="analyze", progress=5, message="Starting video analysis")
    store.set_progress(payload.project_id, starting)
    await broker.publish(payload.project_id, starting)

    try:
        analyses = await analyzer.analyze_batch(
            videos=[(video.name, Path(video.path)) for video in record.videos],
            api_key=api_key,
            model=payload.model,
        )
    except RuntimeError as exc:
        await _mark_failed(store, broker, payload.project_id, str(exc))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected analysis failure for project %s", payload.project_id)
        await _mark_failed(store, broker, payload.project_id, "Analysis failed unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis failed unexpectedly",
        ) from exc

    finished = ProgressUpdate(stage="analyze", progress=100, message="Analysis complete")
    try:
        store.update_project(payload.project_id, analyses=analyses, status="analyzed", progress=finished)
    except Exception as exc:
        logger.exception("Failed to persist analysis result for project %s", payload.project_id)
        await _mark_failed(store, broker, payload.project_id, "Failed to save analysis result")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to save analysis result",
        ) from exc

    await broker.publish(payload.project_id, finished)
    return AnalyzeResponse(analyses=analyses)