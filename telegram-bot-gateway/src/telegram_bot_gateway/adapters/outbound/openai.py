"""An OpenAI-compatible Chat Completions endpoint, streamed."""

from __future__ import annotations

import json
from typing import Any

import httpx

from telegram_bot_gateway.application.ports import ReasoningSink


class EndpointError(Exception):
    """The endpoint refused or failed; the message is what to show the user."""


class OpenAIChat:
    def __init__(self, base_url: str, timeout: float, client: httpx.AsyncClient | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10))

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def models(self, api_key: str) -> list[str]:
        response = await self._client.get(f"{self._base}/models", headers=self._headers(api_key))
        if response.status_code >= 400:
            raise EndpointError(_error_text(response.status_code, response.text))
        return [m["id"] for m in response.json().get("data", [])]

    async def chat(
        self,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]],
        on_reasoning: ReasoningSink | None = None,
    ) -> dict[str, Any]:
        body = {"model": model, "messages": messages, "stream": True, **params}
        if tools:
            body["tools"] = tools
        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        reasoning: list[str] = []
        try:
            async with self._client.stream(
                "POST", f"{self._base}/chat/completions", json=body, headers=self._headers(api_key)
            ) as response:
                if response.status_code >= 400:
                    raise EndpointError(_error_text(response.status_code, (await response.aread()).decode(errors="replace")))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    event = json.loads(payload)
                    if "error" in event:
                        raise EndpointError(_error_text(0, json.dumps(event)))
                    for choice in event.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            content.append(delta["content"])
                        if delta.get("reasoning_content"):
                            reasoning.append(delta["reasoning_content"])
                            if on_reasoning:
                                await on_reasoning("".join(reasoning))
                        for piece in delta.get("tool_calls") or []:
                            call = calls.setdefault(piece.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            call["id"] = piece.get("id") or call["id"]
                            fn = piece.get("function") or {}
                            call["function"]["name"] += fn.get("name") or ""
                            call["function"]["arguments"] += fn.get("arguments") or ""
        except httpx.TimeoutException as exc:
            raise EndpointError("The endpoint took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise EndpointError(f"Could not reach the endpoint: {exc.__class__.__name__}") from exc
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content) or None}
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        return message


def _error_text(status: int, body: str) -> str:
    try:
        detail = json.loads(body).get("error", {})
        text = detail.get("message") if isinstance(detail, dict) else str(detail)
    except (ValueError, AttributeError):
        text = body.strip()
    return f"The endpoint answered {status}: {text or 'no details'}" if status else f"The endpoint failed: {text}"
