from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from models.schemas import ProgressUpdate, UploadResponse, VideoInfo


router = APIRouter(tags=["upload"])


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_videos(request: Request, files: list[UploadFile] = File(...)) -> UploadResponse:
    settings = request.app.state.settings
    store = request.app.state.project_store
    broker = request.app.state.progress_broker

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files uploaded")

    project_id = uuid4().hex
    project_dir = settings.upload_dir / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    max_size_bytes = settings.max_video_size_mb * 1024 * 1024
    allowed_extensions = {extension.lower() for extension in settings.allowed_extensions}

    videos: list[VideoInfo] = []
    for index, upload in enumerate(files):
        original_name = upload.filename or f"video-{index + 1}.mp4"
        suffix = Path(original_name).suffix.lower()
        if suffix not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported extension for {original_name}",
            )

        stored_name = f"{index:02d}_{uuid4().hex[:8]}{suffix}"
        target_path = project_dir / stored_name
        file_size = 0
        with target_path.open("wb") as target_file:
            while chunk := await upload.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > max_size_bytes:
                    target_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"{original_name} exceeds {settings.max_video_size_mb} MB",
                    )
                target_file.write(chunk)

        await upload.close()
        videos.append(
            VideoInfo(
                name=original_name,
                stored_name=stored_name,
                path=str(target_path),
                size_bytes=file_size,
            )
        )

    store.create_project(project_id=project_id, videos=videos)
    progress = ProgressUpdate(stage="upload", progress=100, message="Upload complete")
    store.set_progress(project_id, progress)
    await broker.publish(project_id, progress)
    return UploadResponse(project_id=project_id, videos=videos)