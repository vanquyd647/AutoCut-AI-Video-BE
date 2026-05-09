from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from models.schemas import EditRequest, EditResponse, ProgressUpdate


router = APIRouter(tags=["edit"])


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

    plan = await planner.create_plan(
        analyses=record.analyses,
        style=payload.style,
        target_duration=payload.target_duration,
        aspect_ratio=payload.aspect_ratio,
        api_key=api_key,
        model=payload.model,
    )

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
        await on_progress("error", 100, str(exc))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    finished = ProgressUpdate(stage="export", progress=100, message="Export ready")
    output_url = f"/api/export/{payload.project_id}"
    store.update_project(
        payload.project_id,
        plan=plan,
        output_video=output_url,
        status="completed",
        progress=finished,
    )
    await broker.publish(payload.project_id, finished)
    return EditResponse(
        plan=plan,
        output_video_url=output_url,
        progress_ws_url=f"/ws/progress/{payload.project_id}",
    )