from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from models.schemas import ProjectRecord


router = APIRouter(tags=["project"])


@router.get("/project/{project_id}", response_model=ProjectRecord)
async def get_project(project_id: str, request: Request) -> ProjectRecord:
    store = request.app.state.project_store
    record = store.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return record


@router.delete("/project/{project_id}")
async def delete_project(project_id: str, request: Request) -> dict[str, str]:
    store = request.app.state.project_store
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    store.delete_project(project_id)
    return {"status": "deleted", "project_id": project_id}