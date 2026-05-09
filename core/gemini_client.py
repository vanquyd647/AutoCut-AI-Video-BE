from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from config import Settings


class GeminiClient:
    API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def request_json(
        self,
        prompt: str,
        api_key: str,
        model: str | None = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        max_output_tokens: int = 2048,
    ) -> dict[str, Any]:
        return await self.request_json_with_parts(
            parts=[{"text": prompt}],
            api_key=api_key,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_output_tokens=max_output_tokens,
        )

    async def request_json_with_parts(
        self,
        parts: list[dict[str, Any]],
        api_key: str,
        model: str | None = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        max_output_tokens: int = 2048,
    ) -> dict[str, Any]:
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "topP": top_p,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        resolved_model = model or self.settings.gemini_model
        url = self.API_URL_TEMPLATE.format(model=resolved_model, api_key=api_key)

        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

        texts: list[str] = []
        data = response.json()
        for candidate in data.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                text = part.get("text")
                if text:
                    texts.append(text)

        if not texts:
            raise RuntimeError("Gemini returned no text parts")

        return self._parse_json_response("\n".join(texts))

    async def analyze_video(
        self,
        video_path: Path,
        prompt: str,
        api_key: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        parts = [{"text": prompt}, *self._video_to_base64_parts(video_path)]
        return await self.request_json_with_parts(parts=parts, api_key=api_key, model=model)

    def _parse_json_response(self, raw: str) -> dict[str, Any]:
        candidate = raw.strip()
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            return {"items": parsed}
        except json.JSONDecodeError:
            pass

        match = re.search(r"(\{.*\}|\[.*\])", candidate, flags=re.DOTALL)
        if not match:
            raise ValueError("Unable to parse Gemini JSON response")

        parsed = json.loads(match.group(1))
        if isinstance(parsed, dict):
            return parsed
        return {"items": parsed}

    def _video_to_base64_parts(self, path: Path) -> list[dict[str, Any]]:
        size_limit = self.settings.inline_video_limit_mb * 1024 * 1024
        file_size = path.stat().st_size
        if file_size > size_limit:
            raise ValueError(
                f"{path.name} exceeds the inline Gemini limit of {self.settings.inline_video_limit_mb} MB"
            )

        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "video/mp4"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return [{"inline_data": {"mime_type": mime_type, "data": encoded}}]