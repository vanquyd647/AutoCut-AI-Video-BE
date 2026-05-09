from __future__ import annotations

from collections import defaultdict

from fastapi import WebSocket

from models.schemas import ProgressUpdate


class ProgressBroker:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.latest: dict[str, ProgressUpdate] = {}

    async def connect(self, project_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[project_id].add(websocket)
        if project_id in self.latest:
            await websocket.send_json(self.latest[project_id].model_dump(mode="json"))

    def disconnect(self, project_id: str, websocket: WebSocket) -> None:
        sockets = self.connections.get(project_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self.connections.pop(project_id, None)

    async def publish(self, project_id: str, update: ProgressUpdate) -> None:
        self.latest[project_id] = update
        sockets = list(self.connections.get(project_id, set()))
        stale: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(update.model_dump(mode="json"))
            except Exception:
                stale.append(websocket)

        for websocket in stale:
            self.disconnect(project_id, websocket)