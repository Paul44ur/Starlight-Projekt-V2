"""LM Studio as the model server: the provider (the loaded model, LM Studio's request
format), the doctor and `model serve`, and the launcher (settings, nothing downloaded or
started for the model, the config it writes, the status, and the setup page's choice).
LM Studio itself is faked: its OpenAI API and its own /api/v0/models."""

from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.error
from email.message import Message as Headers
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core.config import EmbedderConfig, ModelConfig, StarlightConfig
from starlight.core.model import Message, create_provider
from starlight.doctor import check_lmstudio, check_model
from starlight.memory.embed import LMSTUDIO_EMBEDDER, OpenAICompatEmbedder, create_embedder
from starlight.models.lmstudio import STOP, LMStudioError, LMStudioProvider
from starlight.models.serve import ServeError, serve_command
from tests.test_cleanup_launcher import load_gui
from tests.test_launcher import L
from tests.test_launcher_window import has_display

LOADED = {
    "data": [
        {"id": "text-embedding-nomic", "type": "embeddings", "state": "loaded"},
        {"id": "qwen2.5-7b-instruct", "type": "llm", "state": "not-loaded"},
        {"id": "qwen2.5-14b-instruct", "type": "llm", "state": "loaded"},
    ]
}


def lm_studio(
    native: dict[str, Any] | None, listed: list[str], bodies: list[dict[str, Any]]
) -> httpx.MockTransport:
    """A fake LM Studio: ``native`` is what /api/v0/models says (None: not there)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(404) if native is None else httpx.Response(200, json=native)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": i} for i in listed]})
        if request.url.path == "/v1/chat/completions":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_the_loaded_model_is_used_and_requests_fit_lm_studio() -> None:
    bodies: list[dict[str, Any]] = []
    config = ModelConfig(
        provider="lmstudio", options={"transport": lm_studio(LOADED, ["a", "b"], bodies)}
    )
    provider = create_provider(config)
    assert isinstance(provider, LMStudioProvider)
    answer = await provider.chat([Message("user", "hi")], [])
    assert answer.content == "hello"
    body = bodies[0]
    assert body["model"] == "qwen2.5-14b-instruct"  # the loaded language model, not the embedder
    assert "cache_prompt" not in body  # a llama-server extension
    assert body["stop"] == STOP and len(body["stop"]) <= 4  # the OpenAI limit
    assert body["top_k"] == 20 and body["max_tokens"]  # sampling and the answer cap still sent


async def test_no_loaded_model_and_several_to_choose_from_is_a_clear_error() -> None:
    native = {
        "data": [
            {"id": "qwen", "type": "llm", "state": "not-loaded"},
            {"id": "llama", "type": "llm", "state": "not-loaded"},
        ]
    }
    provider = LMStudioProvider(transport=lm_studio(native, ["qwen", "llama"], []))
    with pytest.raises(LMStudioError, match=r"no language model loaded \(it has qwen, llama\)"):
        await provider.warmup()


async def test_without_its_own_api_one_listed_model_is_used_and_several_are_an_error() -> None:
    bodies: list[dict[str, Any]] = []
    single = LMStudioProvider(transport=lm_studio(None, ["text-embedding-x", "qwen"], bodies))
    await single.chat([Message("user", "hi")], [])
    assert bodies[0]["model"] == "qwen"
    several = LMStudioProvider(transport=lm_studio(None, ["qwen", "llama"], []))
    with pytest.raises(LMStudioError, match="lists 2 models"):
        await several.warmup()


async def test_a_named_model_is_used_as_it_is() -> None:
    bodies: list[dict[str, Any]] = []
    provider = LMStudioProvider(model="my-model", transport=lm_studio(LOADED, [], bodies))
    await provider.chat([Message("user", "hi")], [])
    assert bodies[0]["model"] == "my-model"


def test_doctor_and_serve_know_lm_studio(paths: Any) -> None:
    config = StarlightConfig.model_validate({"model": {"provider": "lmstudio"}})
    asked: list[str] = []

    def down(url: str) -> tuple[int | None, str]:
        asked.append(url)
        return None, "connection refused"

    checks = check_model(config, paths, get=down)
    assert asked == ["http://127.0.0.1:1234/v1/models"]
    assert checks[-1].status == "fail" and "LM Studio" in checks[-1].hint
    with pytest.raises(ServeError, match="LM Studio runs the model itself"):
        serve_command(config.model)


# -- the launcher ---------------------------------------------------------------------------


def starlight_root(tmp_path: Path, launcher_json: dict[str, Any] | None = None) -> Path:
    root = tmp_path / "Starlight"
    (root / "config").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "starlight"\n', encoding="utf-8")
    if launcher_json is not None:
        (root / "config" / "launcher.json").write_text(json.dumps(launcher_json))
    return root


def settings_for(
    root: Path, monkeypatch: pytest.MonkeyPatch, *flags: str
) -> Any:  # a launcher Settings
    monkeypatch.setenv("UV_CACHE_DIR", str(root / ".uv-cache"))
    return L.load_settings(L.build_parser().parse_args(["--root", str(root), *flags]))


def test_launcher_settings_choose_lm_studio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = starlight_root(
        tmp_path, {"model_server": "lmstudio", "lmstudio_url": "http://192.168.1.5:1234/v1/"}
    )
    settings = settings_for(root, monkeypatch)
    assert settings.uses_lmstudio and not settings.runs_llama
    assert settings.lmstudio_url == "http://192.168.1.5:1234"  # "/v1" is added where needed
    flagged = settings_for(starlight_root(tmp_path / "other"), monkeypatch, "--lmstudio")
    assert flagged.uses_lmstudio
    assert not settings_for(root, monkeypatch, "--no-model").uses_lmstudio  # the test model
    (root / "config" / "launcher.json").write_text(json.dumps({"model_server": "ollama"}))
    with pytest.raises(L.LauncherError, match="llamacpp or lmstudio"):
        settings_for(root, monkeypatch)


def test_setup_downloads_no_model_and_writes_the_lm_studio_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = starlight_root(tmp_path, {"model_server": "lmstudio", "context": 16384})
    settings = settings_for(root, monkeypatch)
    uv = settings.tools / "uv" / L.exe("uv")
    uv.parent.mkdir(parents=True)
    uv.write_text("")
    (settings.tools / ".chromium-installed").write_text("ok\n")

    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("nothing is downloaded for the model with LM Studio")

    for name in ("probe", "ensure_llama", "ensure_model"):
        monkeypatch.setattr(L, name, never)
    monkeypatch.setattr(L, "ensure_uv", lambda settings, ui: uv)
    monkeypatch.setattr(L, "run_checked", lambda *args, **kwargs: None)
    monkeypatch.setattr(L, "maybe_tesseract", lambda settings, ui: None)
    monkeypatch.setattr(L, "fetch_json", lambda *args, **kwargs: None)  # LM Studio is not running
    monkeypatch.setattr(L, "find_lms", lambda: None)  # and its `lms` tool is not installed
    installed = L.setup(settings, L.Ui(assume_yes=True))
    assert installed.server is None and installed.model is None
    written = (root / "config" / "starlight.launcher.yaml").read_text(encoding="utf-8")
    model = json.loads(written[written.index("{") :])["model"]
    assert model == {
        "provider": "lmstudio",
        "options": {"base_url": "http://127.0.0.1:1234/v1", "model": "auto"},
        "context_window": 16384,
    }


def test_the_status_says_what_lm_studio_has_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(starlight_root(tmp_path), monkeypatch, "--lmstudio")
    services = L.Services(settings, L.Ui(assume_yes=True), L.Installed(Path("uv")))
    answers: dict[str, Any] = {}
    monkeypatch.setattr(L, "fetch_json", lambda url, *args, **kwargs: answers.get(url))
    native = "http://127.0.0.1:1234/api/v0/models"

    answers[native] = LOADED
    status = services.poll()
    assert (status.model, status.model_detail) == ("ready", "qwen2.5-14b-instruct (LM Studio)")
    answers[native] = {"data": []}
    assert services.poll().model_detail.endswith("has no model loaded: load one")
    answers.clear()
    status = services.poll()
    assert status.model == "waiting" and "turn on its local server" in status.model_detail
    assert services.watchdog is None and services.restart_model() is False  # nothing of ours


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_setup_page_offers_lm_studio_and_saves_the_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = starlight_root(tmp_path)
    monkeypatch.setenv("UV_CACHE_DIR", str(root / ".uv-cache"))
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    try:
        monkeypatch.setattr(app, "begin", lambda: None)  # no installing in a test
        app.open_setup(False)
        assert gui.LMSTUDIO in app.cards
        app.on_lmstudio_found(L.LMStudioState(True, "qwen2.5-14b-instruct", 32768))
        assert app.cards[gui.LMSTUDIO]["meta"].cget("text") == "found: qwen2.5-14b-instruct"
        app.on_lmstudio_found(L.LMStudioState(False))
        assert "not running" in app.cards[gui.LMSTUDIO]["meta"].cget("text")
        app.select_model(gui.LMSTUDIO, touched=True)
        assert app.gpu_check.instate(["disabled"])  # LM Studio decides where the model runs
        assert "9" not in app.summary.cget("text").split("GB")[0]  # no model download counted
        app.on_setup_go()
        saved = json.loads((root / "config" / "launcher.json").read_text(encoding="utf-8"))
        assert saved["model_server"] == "lmstudio" and app.settings.uses_lmstudio
        app.on_gpu(L.Gpu("RX 9060 XT", 16 * L.GB))  # the big models are marked, not hidden

        def badges(key: str) -> list[str]:
            return [w.cget("text") for w in app.cards[key]["badges"].winfo_children()]

        assert "Too big for your graphics card" in badges("qwen3-32b")
        assert "Too big for your graphics card" not in badges("ministral14b")
        assert badges("qwen14b-uncensored") == ["Recommended for your PC"]
        app.select_model("qwen7b", touched=True)
        assert not app.gpu_check.instate(["disabled"])
        app.on_setup_go()
        saved = json.loads((root / "config" / "launcher.json").read_text(encoding="utf-8"))
        assert saved["model_server"] == "llamacpp" and saved["model"] == "qwen7b"
    finally:
        app.root.destroy()


# -- the second round: errors, speed, context, embeddings -----------------------------------


async def test_lm_studio_s_own_error_reaches_the_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=LOADED)
        error = {"error": {"message": "the prompt is longer than the loaded context (4096)"}}
        return httpx.Response(400, json=error)

    provider = LMStudioProvider(transport=httpx.MockTransport(handler))
    for on_token in (None, lambda text: None):  # without and with streaming
        with pytest.raises(httpx.HTTPStatusError, match="HTTP 400: the prompt is longer"):
            await provider.chat([Message("user", "hi")], [], on_token)


async def test_the_writing_speed_is_measured_when_the_server_gives_none() -> None:
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    ]
    stream = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=LOADED)
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    seen: list[str] = []
    provider = LMStudioProvider(transport=httpx.MockTransport(handler))
    answer = await provider.chat([Message("user", "hi")], [], seen.append)
    assert answer.content == "Hello" and "".join(seen) == "Hello"
    assert answer.usage["completion_tokens"] == 2 and answer.usage["tokens_per_second"] > 0


def test_the_doctor_says_which_model_is_loaded_and_if_its_context_is_enough() -> None:
    config = StarlightConfig.model_validate(
        {"model": {"provider": "lmstudio", "context_window": 32768}}
    )
    base = "http://127.0.0.1:1234"

    def loaded(length: int) -> dict[str, Any]:
        model = {"id": "qwen", "type": "llm", "state": "loaded", "loaded_context_length": length}
        return {"data": [model]}

    good = check_lmstudio(config, base, lambda url: loaded(32768))
    assert (good.status, good.detail) == ("ok", "qwen is loaded")
    short = check_lmstudio(config, base, lambda url: loaded(4096))
    assert short.status == "warn" and "4096" in short.detail and "32768" in short.hint
    none = check_lmstudio(config, base, lambda url: {"data": []})
    assert none.status == "fail" and "load one" in none.hint
    unknown = check_lmstudio(config, base, lambda url: None)
    assert unknown.status == "ok"  # an older LM Studio without its own API: it answers


def test_the_launcher_plans_with_the_context_lm_studio_has_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(starlight_root(tmp_path), monkeypatch, "--lmstudio")
    native: dict[str, Any] = {}
    monkeypatch.setattr(L, "fetch_json", lambda url, *args, **kwargs: native.get(url))
    model = L.lmstudio_model_config(settings)
    assert model["context_window"] == 32768 and "reserve_tokens" not in model  # not running
    entry = {"id": "qwen", "type": "llm", "state": "loaded", "loaded_context_length": 8192}
    native["http://127.0.0.1:1234/api/v0/models"] = {"data": [entry]}
    model = L.lmstudio_model_config(settings)
    assert model["context_window"] == 8192 and model["reserve_tokens"] == 2048
    entry["loaded_context_length"] = 131072
    assert L.lmstudio_model_config(settings)["context_window"] == 32768  # never more than asked


def test_lm_studio_can_hold_the_embedding_model_too() -> None:
    embedder = create_embedder(EmbedderConfig(provider="lmstudio"))
    assert isinstance(embedder, OpenAICompatEmbedder)
    assert embedder.model == LMSTUDIO_EMBEDDER
    assert embedder.base_url == "http://127.0.0.1:1234/v1"


# -- the third round: LM Studio's v1 API, loading a model, starting its server --------------


def v1_models(loaded: dict[str, int] | None = None, tools: bool = True) -> dict[str, Any]:
    """/api/v1/models with qwen and llama downloaded; ``loaded``: instance id -> context."""
    models: list[dict[str, Any]] = []
    for key in ("qwen2.5-14b-instruct", "llama-3.1-8b-instruct"):
        instances = [
            {"id": instance, "config": {"context_length": context}}
            for instance, context in (loaded or {}).items()
            if instance.split(":")[0] == key
        ]
        models.append(
            {
                "key": key,
                "type": "llm",
                "capabilities": {"trained_for_tool_use": tools, "vision": False},
                "loaded_instances": instances,
            }
        )
    models.append({"key": "text-embedding-nomic", "type": "embedding", "loaded_instances": []})
    return {"models": models}


def lm_studio_v1(models: dict[str, Any], log: list[tuple[str, Any]]) -> httpx.MockTransport:
    """A current LM Studio (v1 API) that can also load a model."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body: Any = json.loads(request.content) if request.content else None
        log.append((path, body))
        if path == "/api/v1/models":
            return httpx.Response(200, json=models)
        if path == "/api/v1/models/load":
            return httpx.Response(200, json={"instance_id": body["model"], "status": "loaded"})
        if path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_the_v1_api_names_the_loaded_instance() -> None:
    log: list[tuple[str, Any]] = []
    models = v1_models({"llama-3.1-8b-instruct:2": 32768})
    provider = LMStudioProvider(transport=lm_studio_v1(models, log))
    await provider.chat([Message("user", "hi")], [])
    assert log[-1][1]["model"] == "llama-3.1-8b-instruct:2"  # the instance, not just the key
    named = LMStudioProvider(model="llama-3.1-8b-instruct", transport=lm_studio_v1(models, log))
    await named.chat([Message("user", "hi")], [])
    assert log[-1][1]["model"] == "llama-3.1-8b-instruct:2"  # a named model's loaded instance


async def test_a_named_model_is_loaded_with_starlight_s_context() -> None:
    log: list[tuple[str, Any]] = []
    config = ModelConfig(
        provider="lmstudio",
        context_window=24576,
        options={"model": "qwen2.5-14b-instruct", "transport": lm_studio_v1(v1_models(), log)},
    )
    provider = create_provider(config)  # passes model.context_window to the provider
    await provider.chat([Message("user", "hi")], [])
    loads = [body for path, body in log if path == "/api/v1/models/load"]
    assert loads == [{"model": "qwen2.5-14b-instruct", "context_length": 24576}]


async def test_the_only_model_is_loaded_once_even_for_two_requests_at_once() -> None:
    log: list[tuple[str, Any]] = []
    models = {"models": v1_models()["models"][:1]}  # only qwen downloaded, nothing loaded

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models/load":
            instances = models["models"][0]["loaded_instances"]
            instances.append({"id": "qwen2.5-14b-instruct", "config": {"context_length": 8192}})
        return lm_studio_v1(models, log).handle_request(request)

    provider = LMStudioProvider(context_window=8192, transport=httpx.MockTransport(handler))
    await asyncio.gather(*(provider.chat([Message("user", "hi")], []) for _ in range(2)))
    assert [path for path, _ in log].count("/api/v1/models/load") == 1


def test_the_doctor_reads_the_v1_api_and_knows_what_starlight_will_load() -> None:
    def config(model: str = "auto") -> StarlightConfig:
        return StarlightConfig.model_validate(
            {"model": {"provider": "lmstudio", "options": {"model": model}}}
        )

    base = "http://127.0.0.1:1234"
    v1 = f"{base}/api/v1/models"

    def answering(models: dict[str, Any]) -> Any:
        return lambda url: models if url == v1 else None

    check = check_lmstudio(config(), base, answering(v1_models({"qwen2.5-14b-instruct": 4096})))
    assert check.status == "warn" and "4096 tokens of context" in check.detail
    check = check_lmstudio(
        config(), base, answering(v1_models({"qwen2.5-14b-instruct": 32768}, tools=False))
    )
    assert check.status == "warn" and "not trained for tool use" in check.detail
    check = check_lmstudio(config("llama-3.1-8b-instruct"), base, answering(v1_models()))
    assert check.status == "ok" and "Starlight loads llama-3.1-8b-instruct" in check.detail
    check = check_lmstudio(config(), base, answering(v1_models()))  # two, none loaded
    assert check.status == "fail" and "qwen2.5-14b-instruct, llama-3.1-8b-instruct" in check.detail


def test_the_launcher_reads_the_v1_api(monkeypatch: pytest.MonkeyPatch) -> None:
    models = v1_models({"qwen2.5-14b-instruct": 16384}, tools=False)
    monkeypatch.setattr(
        L, "fetch_json", lambda url, *a, **k: models if url.endswith("/api/v1/models") else None
    )
    state = L.lmstudio_state("http://127.0.0.1:1234")
    assert (state.loaded, state.context, state.tools) == ("qwen2.5-14b-instruct", 16384, False)
    assert state.available == ("qwen2.5-14b-instruct", "llama-3.1-8b-instruct")


def test_the_launcher_starts_lm_studio_s_server_and_loads_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = starlight_root(tmp_path, {"model_server": "lmstudio", "context": 32768})
    settings = settings_for(root, monkeypatch)
    running: dict[str, Any] = {"server": False, "models": {"models": v1_models()["models"][:1]}}
    started: list[list[str]] = []
    loaded: list[tuple[str, str, int]] = []

    def fetch(url: str, *args: Any, **kwargs: Any) -> Any:
        if not running["server"]:
            return None
        return running["models"] if url.endswith("/api/v1/models") else None

    def run(command: list[str], **kwargs: Any) -> Any:
        started.append(command)
        running["server"] = True

    def load(url: str, model: str, context: int, timeout: float = 600) -> str:
        loaded.append((url, model, context))
        running["models"]["models"][0]["loaded_instances"] = [
            {"id": model, "config": {"context_length": context}}
        ]
        return ""

    lms = tmp_path / "lms"
    monkeypatch.setattr(L, "fetch_json", fetch)
    monkeypatch.setattr(L, "find_lms", lambda: lms)
    monkeypatch.setattr(L.subprocess, "run", run)
    monkeypatch.setattr(L, "load_in_lmstudio", load)
    monkeypatch.setattr(L.time, "sleep", lambda seconds: None)
    state = L.ensure_lmstudio(settings, L.Ui(assume_yes=True))
    assert started == [[str(lms), "server", "start"]]
    assert loaded == [("http://127.0.0.1:1234", "qwen2.5-14b-instruct", 32768)]
    assert (state.loaded, state.context) == ("qwen2.5-14b-instruct", 32768)

    # LM Studio on another PC is never started from here
    started.clear()
    running["server"] = False
    settings.lmstudio_url = "http://192.168.1.5:1234"
    assert not L.ensure_lmstudio(settings, L.Ui(assume_yes=True), wait=0).answers
    assert started == []


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_setup_page_lets_you_pick_which_lm_studio_model_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = starlight_root(tmp_path)
    monkeypatch.setenv("UV_CACHE_DIR", str(root / ".uv-cache"))
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    try:
        monkeypatch.setattr(app, "begin", lambda: None)
        app.open_setup(False)
        names = ("qwen3-14b", "gemma-4-12b-it", "mistral-small-3.2")
        app.on_lmstudio_found(L.LMStudioState(True, "", 0, None, names))
        assert not app.lms_row.winfo_manager()  # LM Studio not chosen: no list
        app.select_model(gui.LMSTUDIO, touched=True)
        assert app.lms_row.winfo_manager()
        assert list(app.lms_combo.cget("values")) == [gui.LMS_AUTO, *names]
        meta = app.cards[gui.LMSTUDIO]["meta"]
        assert meta.cget("text") == "found, but no model is loaded: pick one below"
        app.lms_model_var.set("gemma-4-12b-it")
        app._refresh_lms_choice()
        assert meta.cget("text") == "found: gemma-4-12b-it is loaded when Starlight starts"
        app.on_setup_go()
        saved = json.loads((root / "config" / "launcher.json").read_text(encoding="utf-8"))
        assert saved["lmstudio_model"] == "gemma-4-12b-it" and saved["model_server"] == "lmstudio"
        assert L.lmstudio_model_config(app.settings)["options"]["model"] == "gemma-4-12b-it"
        app.lms_model_var.set(gui.LMS_AUTO)
        app.on_setup_go()
        saved = json.loads((root / "config" / "launcher.json").read_text(encoding="utf-8"))
        assert saved["lmstudio_model"] == ""  # back to whichever is loaded
    finally:
        app.root.destroy()


def test_lmstudio_model_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = starlight_root(tmp_path)
    args = L.build_parser().parse_args(["--root", str(root), "--lmstudio-model", " qwen3-14b "])
    settings = L.load_settings(args)
    assert settings.uses_lmstudio and settings.lmstudio_model == "qwen3-14b"


# -- an API token (LM Studio's "Require Authentication") ---------------------------------------


def locked_lm_studio(token: str, seen: list[str | None]) -> httpx.MockTransport:
    """A fake LM Studio that answers only with this API token."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        if request.headers.get("Authorization") != f"Bearer {token}":
            return httpx.Response(401, json={"error": "invalid api token"})
        if request.url.path == "/api/v1/models":
            loaded = [{"id": "qwen3-14b", "config": {"context_length": 32768}}]
            models = [{"key": "qwen3-14b", "type": "llm", "loaded_instances": loaded}]
            return httpx.Response(200, json={"models": models})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    return httpx.MockTransport(handler)


async def test_lm_studio_asking_for_a_token_says_how_to_give_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlight.models.lmstudio import NEEDS_TOKEN, LMStudioError

    monkeypatch.delenv("LM_API_TOKEN", raising=False)
    seen: list[str | None] = []
    options = {"transport": locked_lm_studio("s3cret", seen)}
    provider = create_provider(ModelConfig(provider="lmstudio", options=options))
    with pytest.raises(LMStudioError) as refused:
        await provider.chat([Message("user", "hi")], [])
    assert str(refused.value) == NEEDS_TOKEN and "LM_API_TOKEN" in NEEDS_TOKEN

    monkeypatch.setenv("LM_API_TOKEN", " s3cret ")  # (as setx may leave it)
    provider = create_provider(ModelConfig(provider="lmstudio", options=options))
    answer = await provider.chat([Message("user", "hi")], [])
    assert answer.content == "hello" and seen[-1] == "Bearer s3cret"


async def test_the_token_can_also_come_from_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlight.core import Kernel, Paths
    from starlight.core.logging import redact
    from starlight.core.vault import MemoryBackend

    monkeypatch.delenv("LM_API_TOKEN", raising=False)
    backend = MemoryBackend()
    backend.values["lmstudio.token"] = "from-the-vault"
    kernel = Kernel(Paths(tmp_path), vault_backend=backend)
    config = ModelConfig(provider="lmstudio", options={"base_url": "http://127.0.0.1:1234/v1"})
    assert kernel._with_token(config).options["api_key"] == "from-the-vault"
    assert "from-the-vault" not in redact("token from-the-vault")  # never in a log line
    assert kernel._with_token(ModelConfig(provider="llamacpp")).options == {}  # only LM Studio
    mine = ModelConfig(provider="lmstudio", options={"api_key": "set-in-config"})
    assert kernel._with_token(mine).options["api_key"] == "set-in-config"


def test_the_launcher_and_the_doctor_name_a_missing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def answer(request: Any, timeout: float) -> Any:
        headers = dict(request.header_items())
        calls.append((request.full_url, headers))
        if headers.get("Authorization") != "Bearer s3cret":
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", Headers(), None)
        raise urllib.error.URLError("no more")  # (the rest is not under test)

    monkeypatch.setattr(L, "_local_open", answer)
    monkeypatch.delenv("LM_API_TOKEN", raising=False)
    state = L.lmstudio_state("http://127.0.0.1:1234")
    assert state.answers and state.locked
    root = starlight_root(tmp_path)
    settings = settings_for(root, monkeypatch, "--lmstudio")
    services = L.Services(settings, L.Ui(assume_yes=True), L.Installed(Path("uv")))
    assert services.poll().model_detail == L.LMSTUDIO_TOKEN_HINT
    assert "LM_API_TOKEN" in L.load_in_lmstudio("http://127.0.0.1:1234", "qwen3-14b", 32768)
    monkeypatch.setenv("LM_API_TOKEN", "s3cret")
    assert not L.lmstudio_state("http://127.0.0.1:1234").locked
    assert calls[-1][1]["Authorization"] == "Bearer s3cret"

    config = StarlightConfig.model_validate({"model": {"provider": "lmstudio"}})

    def refused(url: str) -> tuple[int | None, str]:
        return 401, "Unauthorized"

    from starlight.core import Paths

    checks = check_model(config, Paths(tmp_path), get=refused, get_json=lambda url: None)
    assert checks[-1].status == "fail" and "API token" in checks[-1].detail


def test_restart_model_reloads_it_in_lm_studio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(starlight_root(tmp_path), monkeypatch, "--lmstudio")
    unloaded: list[Any] = []
    loaded: list[tuple[str, str, int]] = []

    def opened(request: Any, timeout: float) -> Any:
        unloaded.append((request.full_url, json.loads(request.data)))
        return contextlib.nullcontext()

    monkeypatch.setattr(L, "_local_open", opened)

    def load(*args: Any) -> str:
        loaded.append(args)
        return ""

    monkeypatch.setattr(L, "load_in_lmstudio", load)
    state = L.LMStudioState(True, "qwen3-14b:2", 32768)
    monkeypatch.setattr(L, "lmstudio_state", lambda url: state)
    assert L.reload_in_lmstudio(settings) == ""
    assert unloaded == [
        ("http://127.0.0.1:1234/api/v1/models/unload", {"instance_id": "qwen3-14b:2"})
    ]
    assert loaded == [("http://127.0.0.1:1234", "qwen3-14b", settings.context)]

    monkeypatch.setattr(L, "lmstudio_state", lambda url: L.LMStudioState(True))
    assert "no model loaded" in L.reload_in_lmstudio(settings)
    monkeypatch.setattr(L, "lmstudio_state", lambda url: L.LMStudioState(True, locked=True))
    assert L.reload_in_lmstudio(settings) == L.LMSTUDIO_TOKEN_HINT
    monkeypatch.setattr(L, "lmstudio_state", lambda url: L.LMStudioState())
    assert L.reload_in_lmstudio(settings) == L.LMSTUDIO_HINT
