"""LM Studio as the model server: the provider (the loaded model, LM Studio's request
format), the doctor and `model serve`, and the launcher (settings, nothing downloaded or
started for the model, the config it writes, the status, and the setup page's choice).
LM Studio itself is faked: its OpenAI API and its own /api/v0/models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core.config import ModelConfig, StarlightConfig
from starlight.core.model import Message, create_provider
from starlight.doctor import check_model
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


async def test_no_loaded_model_is_a_clear_error() -> None:
    native = {"data": [{"id": "qwen", "type": "llm", "state": "not-loaded"}]}
    provider = LMStudioProvider(transport=lm_studio(native, ["qwen"], []))
    with pytest.raises(LMStudioError, match="no language model loaded: load one"):
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
        app.select_model(gui.LMSTUDIO, touched=True)
        assert "9" not in app.summary.cget("text").split("GB")[0]  # no model download counted
        app.on_setup_go()
        saved = json.loads((root / "config" / "launcher.json").read_text(encoding="utf-8"))
        assert saved["model_server"] == "lmstudio" and app.settings.uses_lmstudio
        app.select_model("qwen7b", touched=True)
        app.on_setup_go()
        saved = json.loads((root / "config" / "launcher.json").read_text(encoding="utf-8"))
        assert saved["model_server"] == "llamacpp" and saved["model"] == "qwen7b"
    finally:
        app.root.destroy()
