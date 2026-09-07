"""Small, dependency-free protocol adapters around the canonical chat contract."""

from __future__ import annotations

from typing import Any


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(value or "")


def anthropic_to_openai(payload: dict[str, Any]) -> dict[str, Any]:
    messages = []
    system = payload.get("system")
    if system:
        messages.append({"role": "system", "content": _text_content(system)})
    for message in payload.get("messages") or []:
        messages.append({"role": message.get("role", "user"), "content": message.get("content", "")})
    result = {"model": payload.get("model", ""), "messages": messages}
    mapping = {"max_tokens": "max_tokens", "temperature": "temperature", "top_p": "top_p", "stop_sequences": "stop"}
    for source, target in mapping.items():
        if source in payload:
            result[target] = payload[source]
    if payload.get("stream") is not None:
        result["stream"] = payload["stream"]
    return result


def openai_to_anthropic(response: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = response.get("usage") or {}
    return {
        "id": response.get("id"),
        "type": "message",
        "role": "assistant",
        "model": model or response.get("model"),
        "content": [{"type": "text", "text": _text_content(message.get("content", ""))}],
        "stop_reason": choice.get("finish_reason"),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
    }


def gemini_to_openai(payload: dict[str, Any], model: str) -> dict[str, Any]:
    contents = []
    for item in payload.get("contents") or []:
        role = "assistant" if item.get("role") in {"model", "assistant"} else "user"
        parts = item.get("parts") or []
        contents.append({"role": role, "content": "".join(_text_content(part.get("text", part)) for part in parts)})
    result: dict[str, Any] = {"model": model, "messages": contents}
    config = payload.get("generationConfig") or {}
    for source, target in (("maxOutputTokens", "max_tokens"), ("temperature", "temperature"), ("topP", "top_p"), ("stopSequences", "stop")):
        if source in config:
            result[target] = config[source]
    return result


def openai_to_gemini(response: dict[str, Any]) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = response.get("usage") or {}
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": _text_content(message.get("content", ""))}]}, "finishReason": str(choice.get("finish_reason", "STOP")).upper()}],
        "usageMetadata": {"promptTokenCount": usage.get("prompt_tokens", 0), "candidatesTokenCount": usage.get("completion_tokens", 0), "totalTokenCount": usage.get("total_tokens", 0)},
    }


def responses_to_openai(payload: dict[str, Any]) -> dict[str, Any]:
    input_value = payload.get("input", "")
    if isinstance(input_value, list):
        messages = [{"role": item.get("role", "user"), "content": item.get("content", "")} for item in input_value if isinstance(item, dict)]
    else:
        messages = [{"role": "user", "content": str(input_value)}]
    result = {"model": payload.get("model", ""), "messages": messages}
    for source, target in (("max_output_tokens", "max_completion_tokens"), ("temperature", "temperature"), ("top_p", "top_p"), ("tools", "tools"), ("tool_choice", "tool_choice"), ("stream", "stream")):
        if source in payload:
            result[target] = payload[source]
    return result


def openai_to_responses(response: dict[str, Any]) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = response.get("usage") or {}
    text = _text_content(message.get("content", ""))
    return {"id": response.get("id"), "object": "response", "model": response.get("model"), "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}], "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0), "total_tokens": usage.get("total_tokens", 0)}}
