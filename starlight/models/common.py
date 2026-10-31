"""Helpers shared by providers."""

from __future__ import annotations

import base64
import json
from typing import Any

from starlight.core.model import Message, ToolCall

# Tool names are "module.tool"; many chat templates only allow [A-Za-z0-9_-].
SEPARATOR = "__"


def encode_name(name: str) -> str:
    return name.replace(".", SEPARATOR)


def decode_name(name: str) -> str:
    return name.replace(SEPARATOR, ".", 1) if "." not in name else name


def function_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": encode_name(t["name"]),
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool-call arguments arrive as a dict or a JSON string. Anything unparsable is
    passed on as ``_raw`` so argument validation tells the model what went wrong."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": raw}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def image_mime(data: bytes) -> str:
    """A picture's type by its first bytes (the web chat takes JPEG, PNG, GIF and WebP);
    anything else is sent as PNG."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        item: dict[str, Any] = {"role": m.role}
        if m.images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
            parts += [
                {"type": "image_url", "image_url": {"url": f"data:{image_mime(i)};base64,{b64(i)}"}}
                for i in m.images
            ]
            item["content"] = parts
        else:
            item["content"] = m.content
        if m.tool_calls:
            item["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": encode_name(c.name), "arguments": json.dumps(c.arguments)},
                }
                for c in m.tool_calls
            ]
        if m.role == "tool":
            item["tool_call_id"] = m.tool_call_id
        out.append(item)
    return out


def ollama_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        item: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.images:
            item["images"] = [b64(i) for i in m.images]
        if m.tool_calls:
            item["tool_calls"] = [
                {"function": {"name": encode_name(c.name), "arguments": c.arguments}}
                for c in m.tool_calls
            ]
        if m.role == "tool" and m.name:
            item["tool_name"] = encode_name(m.name)
        out.append(item)
    return out


def tool_call(name: str, arguments: Any, call_id: str | None = None) -> ToolCall:
    call = ToolCall(decode_name(name), parse_arguments(arguments))
    if call_id:
        call.id = call_id
    return call
