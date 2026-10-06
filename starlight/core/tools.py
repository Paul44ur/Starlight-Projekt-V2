"""Tools: typed functions a module exposes to the AI.

    class Hello(Module):
        @tool
        def greet(self, name: str) -> str:
            "Greet someone by name."
            return f"Hello, {name}!"

Arguments are validated with a Pydantic model built from the signature, and the same model
gives the JSON schema the language model sees.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints, overload

from pydantic import BaseModel, ConfigDict, create_model

from starlight.core.permissions import Permission

TOOL_ATTR = "__starlight_tool__"


@dataclass(frozen=True)
class ToolSpec:
    name: str | None
    description: str | None
    permission: Permission


@overload
def tool[F: Callable[..., Any]](func: F) -> F: ...
@overload
def tool[F: Callable[..., Any]](
    *,
    name: str | None = None,
    description: str | None = None,
    permission: Permission = Permission.SAFE,
) -> Callable[[F], F]: ...
def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    permission: Permission = Permission.SAFE,
) -> Any:
    """Mark a method as a tool the AI may call."""

    def mark(f: Callable[..., Any]) -> Callable[..., Any]:
        setattr(f, TOOL_ATTR, ToolSpec(name, description, permission))
        return f

    return mark(func) if func is not None else mark


def build_args_model(func: Callable[..., Any], model_name: str) -> type[BaseModel]:
    hints = get_type_hints(func)
    fields: dict[str, Any] = {}
    for param in inspect.signature(func).parameters.values():
        if param.name == "self" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = hints.get(param.name, Any)
        default = ... if param.default is param.empty else param.default
        fields[param.name] = (annotation, default)
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)


@dataclass
class Tool:
    module: str
    name: str
    description: str
    permission: Permission
    func: Callable[..., Any]
    args_model: type[BaseModel]

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.name}"

    def json_schema(self) -> dict[str, Any]:
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        return {
            "name": self.qualified_name,
            "description": self.description,
            "parameters": params,
        }

    async def invoke(self, arguments: dict[str, Any]) -> Any:
        """Validate arguments and run the tool. Blocking tools run in a worker thread."""
        validated = self.args_model.model_validate(arguments)
        kwargs = {name: getattr(validated, name) for name in type(validated).model_fields}
        if inspect.iscoroutinefunction(self.func):
            return await self.func(**kwargs)
        return await asyncio.to_thread(self.func, **kwargs)


def make_tool(
    module: str,
    func: Callable[..., Any],
    *,
    name: str | None = None,
    description: str | None = None,
    permission: Permission = Permission.SAFE,
) -> Tool:
    tool_name = name or func.__name__
    doc = description or inspect.getdoc(func) or ""
    return Tool(
        module=module,
        name=tool_name,
        description=doc.strip(),
        permission=permission,
        func=func,
        args_model=build_args_model(func, f"{module}_{tool_name}_args"),
    )


def collect_tools(obj: object, module: str) -> list[Tool]:
    """Every method of ``obj`` marked with @tool."""
    tools: list[Tool] = []
    for attr in dir(type(obj)):
        raw = getattr(type(obj), attr, None)
        spec: ToolSpec | None = getattr(raw, TOOL_ATTR, None)
        if spec is None:
            continue
        tools.append(
            make_tool(
                module,
                getattr(obj, attr),
                name=spec.name,
                description=spec.description,
                permission=spec.permission,
            )
        )
    return tools
