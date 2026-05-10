from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from models.schemas import AnalyzeRequest, AnalyzeResponse, ProgressUpdate, VideoAnalysis


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


async def _run_analysis_job(
    store,
    broker,
    analyzer,
    project_id: str,
    videos: list[tuple[str, Path]],
    api_key: str | None,
    model: str,
) -> tuple[list[VideoAnalysis] | None, str | None]:
    try:
        analyses = await analyzer.analyze_batch(videos=videos, api_key=api_key, model=model)
    except RuntimeError as exc:
        message = str(exc)
        await _mark_failed(store, broker, project_id, message)
        return None, message
    except Exception:
        logger.exception("Unexpected analysis failure for project %s", project_id)
        message = "Analysis failed unexpectedly"
        await _mark_failed(store, broker, project_id, message)
        return None, message

    finished = ProgressUpdate(stage="analyze", progress=100, message="Analysis complete")
    try:
        store.update_project(project_id, analyses=analyses, status="analyzed", progress=finished)
    except Exception:
        logger.exception("Failed to persist analysis result for project %s", project_id)
        message = "Failed to save analysis result"
        await _mark_failed(store, broker, project_id, message)
        return None, message

    await broker.publish(project_id, finished)
    return analyses, None


def _cleanup_finished_job(jobs: dict[str, asyncio.Task[Any]], project_id: str) -> None:
    existing = jobs.get(project_id)
    if existing is not None and existing.done():
        jobs.pop(project_id, None)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_project(payload: AnalyzeRequest, request: Request, response: Response) -> AnalyzeResponse:
    settings = request.app.state.settings
    store = request.app.state.project_store
    broker = request.app.state.progress_broker
    analyzer = request.app.state.video_analyzer
    jobs: dict[str, asyncio.Task[tuple[list[VideoAnalysis] | None, str | None]]] = request.app.state.analyze_jobs

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

    if record.status in {"analyzed", "completed"} and record.analyses:
        return AnalyzeResponse(
            analyses=record.analyses,
            status="completed",
            message="Analysis already available",
        )

    _cleanup_finished_job(jobs, payload.project_id)
    job = jobs.get(payload.project_id)

    if job is None:
        starting = ProgressUpdate(stage="analyze", progress=5, message="Starting video analysis")
        try:
            store.set_progress(payload.project_id, starting)
        except Exception as exc:
            logger.exception("Failed to persist start progress for project %s", payload.project_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to start analysis",
            ) from exc

        try:
            await broker.publish(payload.project_id, starting)
        except Exception:
            logger.exception("Failed to publish start progress for project %s", payload.project_id)

        job = asyncio.create_task(
            _run_analysis_job(
                store=store,
                broker=broker,
                analyzer=analyzer,
                project_id=payload.project_id,
                videos=[(video.name, Path(video.path)) for video in record.videos],
                api_key=settings.resolve_api_key(payload.api_key),
                model=payload.model,
            )
        )
        jobs[payload.project_id] = job

    response.status_code = status.HTTP_202_ACCEPTED
    return AnalyzeResponse(
        analyses=record.analyses,
        status="processing",
        message=(
            "Analysis is running in the background. Poll /api/project/"
            f"{payload.project_id} for completion status."
        ),
    )
