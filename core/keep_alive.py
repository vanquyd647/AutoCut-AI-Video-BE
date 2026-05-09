from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import httpx


logger = logging.getLogger(__name__)


class KeepAlivePinger:
    def __init__(
        self,
        *,
        enabled: bool,
        url: str,
        interval_seconds: int,
        timeout_seconds: float,
        initial_delay_seconds: int,
    ) -> None:
        self.enabled = enabled
        self.url = url
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self.initial_delay_seconds = initial_delay_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="autocut-keep-alive")
        logger.info("Keep-alive ping task started: %s", self.url)

    async def stop(self) -> None:
        if self._task is None:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Keep-alive ping task stopped")

    async def _run(self) -> None:
        timeout = httpx.Timeout(self.timeout_seconds)
        await asyncio.sleep(self.initial_delay_seconds)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            while True:
                try:
                    response = await client.get(self.url, headers={"User-Agent": "autocut-keepalive/1.0"})
                    if response.status_code >= 400:
                        logger.warning("Keep-alive ping returned status %s from %s", response.status_code, self.url)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Keep-alive ping failed for %s: %s", self.url, exc)

                await asyncio.sleep(self.interval_seconds)