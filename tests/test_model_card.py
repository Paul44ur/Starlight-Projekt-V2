"""The Settings page's model card in a real browser, with a stand-in for the server's answers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM, answer
from tests.test_chat_page import static_site as static_site  # (the fixture)

STUB = """
window.posted = [];
window.local = %(local)s;
window.busy = %(busy)s;
window.model = {
  server: "llamacpp",
  current: {model: "qwen-8b.gguf", context: 32768},
  running: {model: "qwen-8b.gguf", context: 32768},
  options: [
    {id: "qwen-8b.gguf", label: "qwen-8b", size: 5200000000},
    {id: "gemma-12b.gguf", label: "gemma-12b", size: 7800000000},
  ],
  contexts: [0, 8192, 16384, 32768, 65536],
  problem: "", pending: false, overridden: false, restart: {possible: %(launcher)s},
};
window.fetch = async (url, init) => {
  const json = (data, status = 200) => new Response(JSON.stringify(data),
    {status, headers: {"content-type": "application/json"}});
  if (init && init.method === "POST") {
    const body = JSON.parse(init.body);
    window.posted.push([url, body]);
    if (url === "/api/model") {
      window.model.current = {model: body.model, context: body.context};
      window.model.pending = true;
      return json(window.model);
    }
    if (url === "/api/model/restart") {
      if (window.busy && !body.force) return json({detail: "an answer is being written"}, 409);
      return json({requested: true});
    }
    return json({});
  }
  if (url === "/api/model") return json(window.model);
  if (url === "/api/status") return json({local: window.local});
  if (url === "/api/devices") return json({remote: {enabled: false, hosts: []}, devices: []});
  return json({local: window.local, tools: [], modules: [], rules: {}});
};
"""


def stub(local: bool = True, busy: bool = False, launcher: bool = True) -> str:
    return STUB % {
        "local": str(local).lower(),
        "busy": str(busy).lower(),
        "launcher": str(launcher).lower(),
    }


async def open_card(playwright: Any, site: str, script: str, width: int = 1100) -> Any:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(viewport={"width": width, "height": 900})
    await page.add_init_script(script)
    await page.goto(f"{site}/settings.html")
    await page.wait_for_selector("#model-card:not([hidden])")
    return browser, page


async def test_the_card_shows_the_model_saves_a_choice_and_asks_before_restarting(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_card(playwright, static_site, stub())
        try:
            card = page.locator("#model-card")
            assert await card.locator("h2").inner_text() == "Model"
            assert await card.locator("#model-now").inner_text() == "qwen-8b.gguf"
            assert await card.locator("#model-now").get_attribute("translate") == "no"
            options = await page.locator("#model-pick option").all_inner_texts()
            assert options == ["qwen-8b (5.2 GB)", "gemma-12b (7.8 GB)"]
            assert await page.locator("#model-pick").input_value() == "qwen-8b.gguf"
            assert await page.locator("#model-pending").is_hidden()
            assert await page.locator("#model-restart-row").is_hidden()
            # the card sits directly before the Safety limits card
            before = await page.evaluate(
                "document.getElementById('safety-card').previousElementSibling.id"
            )
            assert before == "model-card"
            await page.select_option("#model-pick", "gemma-12b.gguf")
            await page.select_option("#model-context", "16384")
            await page.locator("#model-save").click()
            await page.wait_for_selector("#model-pending:not([hidden])")
            assert await page.evaluate("window.posted") == [
                ["/api/model", {"model": "gemma-12b.gguf", "context": 16384}]
            ]
            assert (
                "applies after Starlight restarts"
                in await page.locator("#model-pending").inner_text()
            )
            await page.locator("#model-restart").click()
            question = await answer(page, yes=False)
            assert question.startswith("Restart Starlight now?")
            assert len(await page.evaluate("window.posted")) == 1  # (no: nothing sent)
            await page.locator("#model-restart").click()
            await answer(page)
            await page.wait_for_function("window.posted.length == 2")
            assert (await page.evaluate("window.posted"))[1] == ["/api/model/restart", {}]
        finally:
            await browser.close()


async def test_a_restart_while_an_answer_is_written_asks_again_and_then_forces_it(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        script = stub(busy=True).replace("pending: false", "pending: true")
        browser, page = await open_card(playwright, static_site, script)
        try:
            await page.locator("#model-restart").click()
            await page.locator("dialog.ask button[type=submit]").click()
            await page.wait_for_function(
                "(document.querySelector('dialog.ask .ask-q') || {}).textContent === 'Restart now?'"
            )
            second = await answer(page)
            assert second.startswith("Restart now?")
            await page.wait_for_function("window.posted.length == 2")
            assert await page.evaluate("window.posted") == [
                ["/api/model/restart", {}],
                ["/api/model/restart", {"force": True}],
            ]
        finally:
            await browser.close()


async def test_without_a_launcher_the_page_says_to_restart_by_hand(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        script = stub(launcher=False).replace("pending: false", "pending: true")
        browser, page = await open_card(playwright, static_site, script)
        try:
            assert await page.locator("#model-restart").is_hidden()
            note = page.locator("#model-restart-note")
            assert "restart Starlight yourself" in await note.inner_text()
        finally:
            await browser.close()


async def test_a_server_that_does_not_answer_turns_saving_off(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        script = stub() + (
            "window.model.options = []; window.model.current.model = 'x';"
            "window.model.problem = 'Ollama does not answer, so there is no list of models.';"
        )
        browser, page = await open_card(playwright, static_site, script)
        try:
            assert "Ollama does not answer" in await page.locator("#model-problem").inner_text()
            assert await page.locator("#model-save").is_disabled()
        finally:
            await browser.close()


async def test_another_device_only_looks_and_the_card_fits_a_phone(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_card(playwright, static_site, stub(local=False), width=360)
        try:
            await page.wait_for_selector("body.remote")
            assert await page.locator("#model-now").inner_text() == "qwen-8b.gguf"
            assert await page.locator("#model-edit").is_hidden()
            assert await page.locator("#model-remote").is_visible()
            assert "changed at the PC" in await page.locator("#model-remote").inner_text()
            assert await page.evaluate("document.documentElement.scrollWidth") <= 360
        finally:
            await browser.close()
        browser, page = await open_card(playwright, static_site, stub(), width=360)
        try:
            assert await page.locator("#model-remote").is_hidden()
            assert await page.evaluate("document.documentElement.scrollWidth") <= 360
        finally:
            await browser.close()


async def test_the_card_speaks_german(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        script = "localStorage.setItem('starlight-lang', 'de');" + stub().replace(
            "pending: false", "pending: true"
        )
        browser, page = await open_card(playwright, static_site, script)
        try:
            text = await page.locator("#model-card").inner_text()
            for german in (
                "Gerade im Einsatz",
                "Kontextgröße",
                "Gespeichert. Es gilt nach einem Neustart von Starlight.",
                "Starlight neu starten",
                "Neue Modelle werden im Starlight-Fenster geladen",
                "Kontext 32768",
            ):
                assert german in text, german
            assert "qwen-8b.gguf" in text  # (names are never translated)
            await page.locator("#model-restart").click()
            question = await answer(page, yes=False)
            assert question.startswith("Starlight jetzt neu starten?")
        finally:
            await browser.close()
