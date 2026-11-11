"""Choosing the model on the Settings page: the lists, the saved choice, the restart request
(starlight/core/modelchoice.py) and the endpoints (starlight/web/model_api.py)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from starlight.core import modelchoice as mc
from starlight.core.paths import Paths
from tests.test_remote import LOCAL, client, home, pair, phone


def fake_server(routes: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        found = routes.get(request.url.path)
        if found is None:
            return httpx.Response(404)
        return httpx.Response(200, json=found)

    return httpx.MockTransport(handler)


def models(root: Path, *names: str) -> None:
    (root / "models").mkdir(exist_ok=True)
    for name in names:
        (root / "models" / name).write_bytes(b"x" * 2048)


def launcher(root: Path, data: dict[str, Any] | str) -> None:
    (root / "config").mkdir(exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    (root / "config" / "launcher.json").write_text(text, encoding="utf-8")


def running(provider: str = "echo", **extra: Any) -> Any:
    server = SimpleNamespace(model_path=extra.pop("model_path", ""))
    return SimpleNamespace(provider=provider, options=extra, context_window=32768, server=server)


# -- the lists ---------------------------------------------------------------------------------


def test_the_model_files_skip_vision_add_ons_and_models_in_several_parts(tmp_path: Path) -> None:
    models(
        tmp_path,
        "b-model.gguf",
        "a-model.gguf",
        "mmproj-x.gguf",
        "big-00001-of-00002.gguf",
        "big-00002-of-00002.gguf",
        "notes.txt",
    )
    (tmp_path / "models" / "folder.gguf").mkdir()
    found = mc.gguf_models(tmp_path / "models")
    assert [m["id"] for m in found] == ["a-model.gguf", "b-model.gguf"]
    assert found[0]["size"] == 2048 and found[0]["label"] == "a-model"


def test_lm_studio_ollama_and_a_remote_server_list_their_names() -> None:
    lms = fake_server(
        {
            "/api/v1/models": {
                "models": [{"key": "qwen", "type": "llm"}, {"key": "emb", "type": "embedding"}]
            }
        }
    )
    assert [m["id"] for m in mc.server_models("lmstudio", {}, lms)] == ["qwen"]
    old = fake_server({"/api/v0/models": {"data": [{"id": "llama", "type": "llm"}]}})
    assert [m["id"] for m in mc.server_models("lmstudio", {}, old)] == ["llama"]
    ollama = fake_server({"/api/tags": {"models": [{"name": "qwen3:14b"}, {"name": "qwen3:14b"}]}})
    assert [m["id"] for m in mc.server_models("ollama", {}, ollama)] == ["qwen3:14b"]
    remote = fake_server({"/v1/models": {"data": [{"id": "big"}]}})
    saved = {"remote_url": "http://pc.local:8080/v1"}
    assert [m["id"] for m in mc.server_models("remote", saved, remote)] == ["big"]


def test_a_server_that_does_not_answer_gives_an_empty_list_and_says_why(tmp_path: Path) -> None:
    down = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("no")))
    listed, why = mc.options(tmp_path, {}, "ollama", down)  # type: ignore[arg-type]
    assert listed == [] and why == "Ollama does not answer, so there is no list of models."
    listed, why = mc.options(Paths(tmp_path), {}, "lmstudio", down)
    assert listed == [] and "LM Studio does not answer" in why
    listed, why = mc.options(Paths(tmp_path), {}, "remote", down)
    assert "address" in why
    listed, why = mc.options(Paths(tmp_path), {}, "llamacpp")
    assert listed == [] and "no model file" in why


# -- saving ------------------------------------------------------------------------------------


def test_a_saved_choice_keeps_the_other_keys_and_an_earlier_copy(tmp_path: Path) -> None:
    models(tmp_path, "a.gguf", "b.gguf")
    launcher(tmp_path, {"model_server": "llamacpp", "port": 8123, "model": "x", "voice": True})
    paths = Paths(tmp_path)
    changes = mc.save_choice(paths, running(), "b.gguf", 16384)
    assert changes == {"model_file": "b.gguf", "context": 16384}
    saved = json.loads((tmp_path / "config" / "launcher.json").read_text())
    assert saved == {
        "model_server": "llamacpp",
        "port": 8123,
        "model": "x",
        "voice": True,
        "model_file": "b.gguf",
        "context": 16384,
    }
    copies = list((tmp_path / "config" / "history").glob("launcher.json.*"))
    assert len(copies) == 1 and "b.gguf" not in copies[0].read_text()
    for _ in range(12):  # (only the last ten are kept)
        mc.save_choice(paths, running(), "a.gguf", None)
    assert len(list((tmp_path / "config" / "history").glob("launcher.json.*"))) == mc.KEEP_COPIES


def test_a_choice_that_was_not_offered_is_never_saved(tmp_path: Path) -> None:
    models(tmp_path, "a.gguf")
    launcher(tmp_path, {"model_server": "llamacpp"})
    paths = Paths(tmp_path)
    for bad in ("../a.gguf", "other.gguf", "", "mmproj-a.gguf"):
        with pytest.raises(mc.ModelChoiceError):
            mc.save_choice(paths, running(), bad, None)
    with pytest.raises(mc.ModelChoiceError, match="context"):
        mc.save_choice(paths, running(), "a.gguf", 12345)
    assert json.loads((tmp_path / "config" / "launcher.json").read_text()) == {
        "model_server": "llamacpp"
    }


def test_a_launcher_json_with_a_mistake_is_never_overwritten(tmp_path: Path) -> None:
    models(tmp_path, "a.gguf")
    launcher(tmp_path, '{"model_server": "llamacpp",')
    with pytest.raises(mc.ModelChoiceError, match="mistake"):
        mc.save_choice(Paths(tmp_path), running(), "a.gguf", None)
    assert (tmp_path / "config" / "launcher.json").read_text() == '{"model_server": "llamacpp",'
    assert not (tmp_path / "config" / "history").exists()


def test_a_server_model_goes_under_its_own_key(tmp_path: Path) -> None:
    launcher(tmp_path, {"model_server": "ollama", "ollama_url": "http://x:1"})
    ollama = fake_server({"/api/tags": {"models": [{"name": "qwen3:14b"}]}})
    changes = mc.save_choice(Paths(tmp_path), running(), "qwen3:14b", None, ollama)
    assert changes == {"ollama_model": "qwen3:14b"}
    with pytest.raises(mc.ModelChoiceError):
        mc.save_choice(Paths(tmp_path), running(), "llama3", None, ollama)


def test_a_file_that_vanishes_is_refused(tmp_path: Path) -> None:
    models(tmp_path, "a.gguf")
    launcher(tmp_path, {"model_server": "llamacpp"})
    paths = Paths(tmp_path)
    real = mc.gguf_models
    try:
        mc.gguf_models = lambda folder: [{"id": "gone.gguf", "label": "gone", "size": 1}]  # type: ignore[assignment]
        with pytest.raises(mc.ModelChoiceError, match="gone"):
            mc.save_choice(paths, running(), "gone.gguf", None)
    finally:
        mc.gguf_models = real  # type: ignore[assignment]


# -- the view ----------------------------------------------------------------------------------


def test_the_view_marks_a_choice_that_differs_from_what_runs_as_pending(tmp_path: Path) -> None:
    models(tmp_path, "a.gguf", "b.gguf")
    launcher(tmp_path, {"model_server": "llamacpp"})
    paths = Paths(tmp_path)
    run = running("llamacpp", model_path=str(tmp_path / "models" / "a.gguf"))
    first = mc.view(paths, run)
    assert first["current"]["model"] == "a.gguf" and first["pending"] is False
    assert first["running"]["model"] == "a.gguf" and first["server"] == "llamacpp"
    mc.save_choice(paths, run, "b.gguf", None)
    after = mc.view(paths, run)
    assert after["current"]["model"] == "b.gguf" and after["pending"] is True
    # a changed context alone is pending too, once Starlight has noted how it started
    start = {"server": "llamacpp", "model": "a.gguf", "context": 32768}
    mc.save_choice(paths, run, "a.gguf", 8192)
    assert mc.view(paths, run, start)["pending"] is True


def test_the_view_without_a_launcher_json_or_with_a_broken_one(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    plain = mc.view(paths, running())
    assert plain["server"] == "none" and plain["options"] == [] and "test model" in plain["problem"]
    launcher(tmp_path, "{nope")
    broken = mc.view(paths, running())
    assert "mistake" in broken["problem"] and broken["options"] == []
    (tmp_path / "config" / "starlight.local.yaml").write_text("model:\n  provider: echo\n")
    assert mc.view(paths, running())["overridden"] is True


# -- the two files between the page and the launcher -------------------------------------------


def test_a_launcher_is_running_while_its_heartbeat_is_young(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    assert mc.launcher_alive(paths) is False
    (tmp_path / "data").mkdir()
    beat = tmp_path / "data" / "launcher.alive.json"
    beat.write_text(json.dumps({"time": time.time() - 3}))
    assert mc.launcher_alive(paths) is True
    beat.write_text(json.dumps({"time": time.time() - 40}))
    assert mc.launcher_alive(paths) is False
    for junk in ("", "[]", '{"time": "x"}', "{"):
        beat.write_text(junk)
        assert mc.launcher_alive(paths) is False, junk


def test_the_restart_request_is_a_small_file_in_data(tmp_path: Path) -> None:
    mc.request_restart(Paths(tmp_path), "web")
    data = json.loads((tmp_path / "data" / "launcher.restart.json").read_text())
    assert data["by"] == "web" and data["since"] > 0
    assert list((tmp_path / "data").iterdir()) == [tmp_path / "data" / "launcher.restart.json"]


# -- the endpoints -----------------------------------------------------------------------------


def alive(root: Path) -> None:
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "launcher.alive.json").write_text(json.dumps({"time": time.time()}))


def test_a_paired_phone_may_look_but_not_change_or_restart(tmp_path: Path) -> None:
    root = home(tmp_path)
    models(root, "a.gguf", "b.gguf")
    launcher(root, {"model_server": "llamacpp"})
    alive(root)
    with client(root) as c:
        token = pair(c)
        seen = c.get("/api/model", headers=phone(token))
        assert seen.status_code == 200
        assert [o["id"] for o in seen.json()["options"]] == ["a.gguf", "b.gguf"]
        refused = c.post("/api/model", headers=phone(token), json={"model": "b.gguf"})
        assert refused.status_code == 403
        assert c.post("/api/model/restart", headers=phone(token), json={}).status_code == 403
    assert json.loads((root / "config" / "launcher.json").read_text()) == {
        "model_server": "llamacpp"
    }
    assert not (root / "data" / "launcher.restart.json").exists()


def test_at_the_pc_a_choice_is_saved_audited_and_pending(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = home(tmp_path)
    models(root, "a.gguf", "b.gguf")
    launcher(root, {"model_server": "llamacpp", "model_file": "a.gguf"})
    with client(root) as c:
        before = c.get("/api/model", headers=LOCAL).json()
        assert before["pending"] is False and before["current"]["model"] == "a.gguf"
        bad = c.post("/api/model", headers=LOCAL, json={"model": "other.gguf"})
        assert bad.status_code == 400
        assert c.post("/api/model", headers=LOCAL, json={"model": 5}).status_code == 400
        done = c.post("/api/model", headers=LOCAL, json={"model": "b.gguf", "context": 16384})
        assert done.status_code == 200, done.text
        view = done.json()
        assert view["pending"] is True and view["current"] == {"model": "b.gguf", "context": 16384}
        assert c.get("/api/model", headers=LOCAL).json()["pending"] is True
    assert json.loads((root / "config" / "launcher.json").read_text())["model_file"] == "b.gguf"
    assert any("web.model" in r.getMessage() and "b.gguf" in r.getMessage() for r in caplog.records)


def test_the_restart_needs_a_launcher_and_asks_first_while_an_answer_is_written(
    tmp_path: Path,
) -> None:
    from starlight.web import app as web

    root = home(tmp_path)
    request = root / "data" / "launcher.restart.json"
    with client(root) as c:
        none = c.post("/api/model/restart", headers=LOCAL, json={})
        assert none.status_code == 400 and not request.exists()  # (no launcher window)
        alive(root)
        assert c.get("/api/model", headers=LOCAL).json()["restart"]["possible"] is True
        web.ANSWERING.count = 1
        try:
            busy = c.post("/api/model/restart", headers=LOCAL, json={})
            assert busy.status_code == 409 and not request.exists()
            forced = c.post("/api/model/restart", headers=LOCAL, json={"force": True})
            assert forced.status_code == 200 and request.is_file()
        finally:
            web.ANSWERING.count = 0
        request.unlink()
        assert c.post("/api/model/restart", headers=LOCAL, json={}).status_code == 200
        assert request.is_file()
