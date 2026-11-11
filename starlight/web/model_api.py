"""The web API of the Settings page's model card (starlight/core/modelchoice.py).

GET  /api/model          the model in use, the choice saved, the models to choose from
POST /api/model          {model, context}: save the choice (PC only; applies after a restart)
POST /api/model/restart  {force}: ask the launcher window to restart Starlight (PC only;
                          409 while an answer is being written, unless ``force`` is true)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from starlight.core import modelchoice
from starlight.core.paths import Paths


def add_model_api(
    app: FastAPI,
    paths: Paths,
    kernel: Callable[[], Any],
    viewer: Callable[[Request], Any],
    local_only: Callable[[Request], Any],
    body: Callable[[Request], Awaitable[dict[str, Any]]],
    answering: Callable[[], bool],
) -> None:
    """Add the endpoints to ``app``. ``answering``: an answer is being written right now."""
    state: dict[str, Any] = {}  # "start": the choice when Starlight started

    def current() -> dict[str, Any]:
        model = kernel().config.model
        if "start" not in state:
            try:
                state["start"] = modelchoice.saved_choice(
                    paths, modelchoice.running_model(model), str(model.provider)
                )
            except modelchoice.ModelChoiceError:
                state["start"] = None
        found: dict[str, Any] = modelchoice.view(paths, model, state["start"])
        return found

    @app.get("/api/model")
    def model_choice(request: Request) -> dict[str, Any]:
        viewer(request)  # other devices may look, not change
        return current()

    @app.post("/api/model")
    async def set_model_choice(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        model = data.get("model")
        context = data.get("context")
        if not isinstance(model, str) or not model:
            raise HTTPException(status_code=400, detail="choose a model")
        if context is not None and (not isinstance(context, int) or isinstance(context, bool)):
            raise HTTPException(status_code=400, detail="the context size is a number")
        k = kernel()
        current()  # (remembers what Starlight started with, before the file changes)
        try:
            saved = modelchoice.save_choice(paths, k.config.model, model, context)
        except modelchoice.ModelChoiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        k.audit.record("web.model", model=model, context=saved.get("context"))
        return current()

    @app.post("/api/model/restart")
    async def restart_starlight(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        if not modelchoice.launcher_alive(paths):
            raise HTTPException(
                status_code=400, detail="no Starlight window is running: restart Starlight yourself"
            )
        if answering() and data.get("force") is not True:
            raise HTTPException(status_code=409, detail="an answer is being written")
        modelchoice.request_restart(paths, "web")
        kernel().audit.record("web.model_restart", forced=data.get("force") is True)
        return {"requested": True}
