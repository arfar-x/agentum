"""The upstream OpenAI-compatible model, called over HTTP."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agent_gateway.application.models import Completion, LlmDelta, LlmRequest
from agent_gateway.domain.errors import UpstreamRejected, UpstreamUnavailable
from agent_gateway.domain.messages import ChatMessage, Usage

TRANSIENT = {408, 409, 429, 500, 502, 503, 504}


def _usage(raw: dict[str, Any] | None) -> Usage:
    raw = raw or {}
    return Usage(prompt_tokens=raw.get("prompt_tokens", 0) or 0, completion_tokens=raw.get("completion_tokens", 0) or 0,
                 total_tokens=raw.get("total_tokens", 0) or 0)


class HttpLlm:
    def __init__(self, client: httpx.AsyncClient, *, retries: int = 3, backoff: float = 0.5) -> None:
        self._client = client
        self._retries = retries
        self._backoff = backoff

    def _body(self, request: LlmRequest, stream: bool) -> dict[str, Any]:
        # Reasoning a client stored and sent back is not conversation content; models misread it.
        drop = {"reasoning", "reasoning_content", "reasoning_details"}
        body: dict[str, Any] = {"model": request.model,
                                "messages": [m.model_dump(exclude_none=True, exclude=drop) for m in request.messages]}
        if request.tools:
            body["tools"] = [t.model_dump(exclude_none=True) for t in request.tools]
        body.update(request.params)
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def _post(self, body: dict[str, Any], api_key: str | None) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = await self._client.post("/chat/completions", json=body, headers=self._headers(api_key))
            except httpx.TransportError as exc:
                last = exc
            else:
                if response.status_code not in TRANSIENT:
                    return response
                last = UpstreamUnavailable(f"upstream answered {response.status_code}")
                if attempt == self._retries:
                    return response
            if attempt < self._retries:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise UpstreamUnavailable(f"upstream unreachable: {last}")

    @staticmethod
    def _rejection(status: int, raw: bytes, api_key: str | None) -> UpstreamRejected:
        """The upstream's refusal, with the key removed from whatever it echoes back."""
        text = raw.decode(errors="replace")
        if api_key:
            text = text.replace(api_key, "[removed]")
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        return UpstreamRejected(status, body, text[:200])

    @classmethod
    def _raise_for(cls, response: httpx.Response, api_key: str | None) -> None:
        if response.status_code >= 400:
            raise cls._rejection(response.status_code, response.content, api_key)

    async def complete(self, request: LlmRequest, api_key: str | None = None) -> Completion:
        response = await self._post(self._body(request, stream=False), api_key)
        self._raise_for(response, api_key)
        data = response.json()
        choice = data["choices"][0]
        message = dict(choice["message"])
        reasoning = message.pop("reasoning_content", None) or message.pop("reasoning", None) or ""
        return Completion(message=ChatMessage.model_validate(message), finish_reason=choice.get("finish_reason") or "stop",
                          usage=_usage(data.get("usage")), reasoning=reasoning or "",
                          system_fingerprint=data.get("system_fingerprint"))

    async def stream(self, request: LlmRequest, api_key: str | None = None) -> AsyncIterator[LlmDelta]:
        body = self._body(request, stream=True)
        for attempt in range(self._retries + 1):
            try:
                async with self._client.stream("POST", "/chat/completions", json=body, headers=self._headers(api_key)) as response:
                    if response.status_code in TRANSIENT and attempt < self._retries:
                        await asyncio.sleep(self._backoff * (2**attempt))
                        continue
                    if response.status_code >= 400:
                        raise self._rejection(response.status_code, await response.aread(), api_key)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return
                        chunk = json.loads(payload)
                        usage = _usage(chunk["usage"]) if chunk.get("usage") else None
                        for choice in chunk.get("choices") or [{}]:
                            delta = choice.get("delta") or {}
                            yield LlmDelta(
                                content=delta.get("content") or "",
                                reasoning=delta.get("reasoning_content") or delta.get("reasoning") or "",
                                tool_calls=delta.get("tool_calls") or [],
                                finish_reason=choice.get("finish_reason"),
                                usage=usage,
                            )
                            usage = None
                    return
            except httpx.TransportError as exc:
                if attempt == self._retries:
                    raise UpstreamUnavailable(f"upstream unreachable: {exc}") from exc
                await asyncio.sleep(self._backoff * (2**attempt))
