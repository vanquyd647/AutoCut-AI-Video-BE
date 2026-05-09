from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse


router = APIRouter(tags=["export"])


@router.get("/export/{project_id}")
async def export_project(project_id: str, request: Request) -> FileResponse:
    export_service = request.app.state.export_service
    output_path = export_service.get_output_path(project_id)
    if output_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export not found")
    return FileResponse(output_path, media_type="video/mp4", filename=output_path.name)