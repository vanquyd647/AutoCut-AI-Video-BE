from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from models.schemas import EditRequest, EditResponse, ProgressUpdate


router = APIRouter(tags=["edit"])
logger = logging.getLogger(__name__)


async def _mark_failed(store, broker, project_id: str, message: str) -> None:
    failed = ProgressUpdate(stage="error", progress=100, message=message)
    try:
        store.set_progress(project_id, failed)
    except Exception:
        logger.exception("Failed to persist edit error progress for project %s", project_id)

    try:
        await broker.publish(project_id, failed)
    except Exception:
        logger.exception("Failed to publish edit error progress for project %s", project_id)


@router.post("/edit", response_model=EditResponse)
async def edit_project(payload: EditRequest, request: Request) -> EditResponse:
    settings = request.app.state.settings
    store = request.app.state.project_store
    broker = request.app.state.progress_broker
    planner = request.app.state.edit_planner
    editor = request.app.state.video_editor

    record = store.get_project(payload.project_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not record.analyses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Analyze the project before editing")

    api_key = settings.resolve_api_key(payload.api_key)

    planning = ProgressUpdate(stage="plan", progress=10, message="Creating edit plan")
    store.set_progress(payload.project_id, planning)
    await broker.publish(payload.project_id, planning)

    try:
        plan = await planner.create_plan(
            analyses=record.analyses,
            style=payload.style,
            target_duration=payload.target_duration,
            aspect_ratio=payload.aspect_ratio,
            api_key=api_key,
            model=payload.model,
        )
    except RuntimeError as exc:
        message = str(exc)
        await _mark_failed(store, broker, payload.project_id, message)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message) from exc
    except Exception as exc:
        logger.exception("Unexpected planning failure for project %s", payload.project_id)
        message = "Failed to create edit plan"
        await _mark_failed(store, broker, payload.project_id, message)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message) from exc

    async def on_progress(stage: str, progress: float, message: str) -> None:
        update = ProgressUpdate(stage=stage, progress=progress, message=message)
        store.set_progress(payload.project_id, update)
        await broker.publish(payload.project_id, update)

    await on_progress("render", 25, "Rendering clips")
    try:
        await editor.render(
            project_id=payload.project_id,
            videos=record.videos,
            plan=plan,
            on_progress=on_progress,
        )
    except RuntimeError as exc:
        message = str(exc)
        await _mark_failed(store, broker, payload.project_id, message)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message) from exc
    except Exception as exc:
        logger.exception("Unexpected render failure for project %s", payload.project_id)
        message = "Video rendering failed unexpectedly"
        await _mark_failed(store, broker, payload.project_id, message)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message) from exc

    finished = ProgressUpdate(stage="export", progress=100, message="Export ready")
    output_url = f"/api/export/{payload.project_id}"
    try:
        store.update_project(
            payload.project_id,
            plan=plan,
            output_video=output_url,
            status="completed",
            progress=finished,
        )
    except Exception as exc:
        logger.exception("Failed to persist edit result for project %s", payload.project_id)
        message = "Failed to save edit result"
        await _mark_failed(store, broker, payload.project_id, message)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message) from exc

    await broker.publish(payload.project_id, finished)
    return EditResponse(
        plan=plan,
        output_video_url=output_url,
        progress_ws_url=f"/ws/progress/{payload.project_id}",
    )