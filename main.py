from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import ROUTERS
from config import get_settings
from core import EditPlanner, ExportService, GeminiClient, KeepAlivePinger, ProgressBroker, ProjectStore, VideoAnalyzer, VideoEditor
from models.schemas import ProgressUpdate


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = settings
    app.state.project_store = ProjectStore(settings)
    app.state.progress_broker = ProgressBroker()
    app.state.analyze_jobs = {}
    gemini_client = GeminiClient(settings)
    app.state.video_analyzer = VideoAnalyzer(settings, gemini_client)
    app.state.edit_planner = EditPlanner(gemini_client)
    app.state.video_editor = VideoEditor(settings)
    app.state.export_service = ExportService(settings)
    app.state.ffmpeg_available = settings.ffmpeg_available()
    app.state.keep_alive_pinger = KeepAlivePinger(
        enabled=settings.auto_ping_enabled,
        url=settings.resolve_auto_ping_url(),
        interval_seconds=settings.auto_ping_interval_seconds,
        timeout_seconds=settings.auto_ping_timeout_seconds,
        initial_delay_seconds=settings.auto_ping_initial_delay_seconds,
    )
    app.state.keep_alive_pinger.start()
    try:
        yield
    finally:
        analyze_jobs = list(app.state.analyze_jobs.values())
        for job in analyze_jobs:
            job.cancel()
        if analyze_jobs:
            await asyncio.gather(*analyze_jobs, return_exceptions=True)
        await app.state.keep_alive_pinger.stop()


app = FastAPI(title="AutoCut AI Video API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/storage/output", StaticFiles(directory=str(settings.output_dir)), name="output")

for router in ROUTERS:
    app.include_router(router, prefix="/api")


@app.get("/api/health")
async def healthcheck() -> dict[str, object]:
    return {
        "status": "ok",
        "ffmpeg_available": settings.ffmpeg_available(),
        "gemini_model": settings.gemini_model,
    }


@app.websocket("/ws/progress/{project_id}")
async def progress_socket(websocket: WebSocket, project_id: str) -> None:
    broker: ProgressBroker = app.state.progress_broker
    await broker.connect(project_id, websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
    finally:
        broker.disconnect(project_id, websocket)


@app.post("/api/debug/progress/{project_id}")
async def emit_debug_progress(project_id: str) -> dict[str, str]:
    update = ProgressUpdate(stage="debug", progress=100, message="Debug event emitted")
    await app.state.progress_broker.publish(project_id, update)
    return {"status": "sent"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)