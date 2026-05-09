from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from models.schemas import AnalyzeRequest, AnalyzeResponse, ProgressUpdate


router = APIRouter(tags=["analyze"])


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_project(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    settings = request.app.state.settings
    store = request.app.state.project_store
    broker = request.app.state.progress_broker
    analyzer = request.app.state.video_analyzer
    record = store.get_project(payload.project_id)
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
        failed = ProgressUpdate(stage="error", progress=100, message=str(exc))
        store.set_progress(payload.project_id, failed)
        await broker.publish(payload.project_id, failed)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        # Keep the client response stable while preserving detailed diagnostics in logs.
        failed = ProgressUpdate(stage="error", progress=100, message="Analysis failed unexpectedly")
        store.set_progress(payload.project_id, failed)
        await broker.publish(payload.project_id, failed)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis failed unexpectedly",
        ) from exc

    finished = ProgressUpdate(stage="analyze", progress=100, message="Analysis complete")
    store.update_project(payload.project_id, analyses=analyses, status="analyzed", progress=finished)
    await broker.publish(payload.project_id, finished)
    return AnalyzeResponse(analyses=analyses)