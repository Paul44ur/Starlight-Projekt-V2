"""Starlight launcher: downloads what Starlight needs, then starts the AI.

Compiled into Starlight.exe (see .github/workflows/launcher.yml), so it uses only the
Python standard library. Put Starlight.exe in the Starlight folder and double-click it:

* first run: shows what it will download and how big it is, asks once, then installs
  uv, Python 3.12 with every package, the Chromium browser, the llama.cpp model server
  (Vulkan build: AMD, NVIDIA and Intel GPUs) and the model into the Starlight folder
  (uv keeps Python, and Playwright keeps Chromium, in the user profile); no administrator
  rights needed
* every run: starts the model server on the GPU, the statistics viewer, and the chat;
  closing the window stops everything it started

Downloads are checked against published SHA-256 checksums when the source offers them,
and interrupted downloads resume where they stopped.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
USER_AGENT = f"StarlightLauncher/{VERSION}"

UV_DOWNLOAD = "https://github.com/astral-sh/uv/releases/latest/download"
LLAMA_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
LLAMA_RELEASES = "https://github.com/ggml-org/llama.cpp/releases"

# 16 GB VRAM: a 14B model at Q4_K_M (~9 GB) plus a 32k context with q8 KV cache.
DEFAULT_MODEL_URL = (
    "https://huggingface.co/bartowski/Qwen2.5-14B_Uncensored_Instruct-GGUF/resolve/main/"
    "Qwen2.5-14B_Uncensored_Instruct-Q4_K_M.gguf"
)
# --model NAME (or "model" in config/launcher.json) picks one of these; --model-url any other.
MODEL_PRESETS = {
    "qwen14b-uncensored": DEFAULT_MODEL_URL,  # the default
    # The original Qwen2.5-14B-Instruct: same size, more careful, cleaner turn endings.
    "qwen14b": (
        "https://huggingface.co/bartowski/Qwen2.5-14B-Instruct-GGUF/resolve/main/"
        "Qwen2.5-14B-Instruct-Q4_K_M.gguf"
    ),
    # The next Qwen generation: thinks before it answers (llama-server keeps the thinking
    # out of the reply), so it is better at hard problems and slower.
    "qwen3-14b": (
        "https://huggingface.co/MaziyarPanahi/Qwen3-14B-GGUF/resolve/main/Qwen3-14B.Q4_K_M.gguf"
    ),
    # Mistral's 14B of December 2025, made for tool use; strong in German and French.
    "ministral14b": (
        "https://huggingface.co/unsloth/Ministral-3-14B-Instruct-2512-GGUF/resolve/main/"
        "Ministral-3-14B-Instruct-2512-Q4_K_M.gguf"
    ),
    # OpenAI's open model: 21B weights, 3.6B used per token, so quick; reasons first.
    "gpt-oss-20b": (
        "https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-mxfp4.gguf"
    ),
    # Mistral and NVIDIA's 12B: a little smaller and quicker, good in many languages.
    "mistral-nemo": (
        "https://huggingface.co/bartowski/Mistral-Nemo-Instruct-2407-GGUF/resolve/main/"
        "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf"
    ),
    # Google's Gemma 4 at 12B: a good writer, knows 140 languages.
    "gemma4-12b": (
        "https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/resolve/main/gemma-4-12b-it-Q4_K_M.gguf"
    ),
    # Tuned for code: writes and fixes programs better, everyday chat less well.
    "qwen14b-coder": (
        "https://huggingface.co/bartowski/Qwen2.5-Coder-14B-Instruct-GGUF/resolve/main/"
        "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
    ),
    # The newest Qwen that fits 8 GB cards; thinks before it answers.
    "qwen3.5-9b": (
        "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
    ),
    # Half the video memory (~5 GB) and about twice as fast, for smaller graphics cards.
    "qwen7b": (
        "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/"
        "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    ),
    "qwen3-8b": (
        "https://huggingface.co/MaziyarPanahi/Qwen3-8B-GGUF/resolve/main/Qwen3-8B.Q4_K_M.gguf"
    ),
    # Google's small model (8B weights, 4B of them used per token), for 6-8 GB cards.
    "gemma4-e4b": (
        "https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/"
        "gemma-4-E4B-it-Q4_K_M.gguf"
    ),
    # For graphics cards with 24 to 32 GB (see LARGE): 24B to 35B models.
    "mistral-small-24b": (
        "https://huggingface.co/bartowski/mistralai_Mistral-Small-3.2-24B-Instruct-2506-GGUF/"
        "resolve/main/mistralai_Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf"
    ),
    "gemma4-26b": (
        "https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/resolve/main/"
        "gemma-4-26B-A4B-it-Q4_K_M.gguf"
    ),
    "qwen3.8-27b": (
        "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-UD-Q4_K_M.gguf"
    ),
    "qwen3-30b-a3b": (
        "https://huggingface.co/bartowski/Qwen_Qwen3-30B-A3B-Instruct-2507-GGUF/resolve/main/"
        "Qwen_Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
    ),
    "qwen3-coder-30b": (
        "https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/resolve/main/"
        "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
    ),
    "granite4-small": (
        "https://huggingface.co/bartowski/ibm-granite_granite-4.0-h-small-GGUF/resolve/main/"
        "ibm-granite_granite-4.0-h-small-Q4_K_M.gguf"
    ),
    "gemma4-31b": (
        "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
    ),
    "qwen3.5-35b-a3b": (
        "https://huggingface.co/bartowski/Qwen_Qwen3.5-35B-A3B-GGUF/resolve/main/"
        "Qwen_Qwen3.5-35B-A3B-Q4_K_M.gguf"
    ),
    "qwen3-32b": (
        "https://huggingface.co/bartowski/Qwen_Qwen3-32B-GGUF/resolve/main/Qwen_Qwen3-32B-Q4_K_M.gguf"
    ),
    # Changed by others (see MODIFIED): "abliterated" models have the direction in the
    # network that makes them refuse removed; the others are fine-tuned on new data.
    "qwen3-14b-abliterated": (
        "https://huggingface.co/bartowski/mlabonne_Qwen3-14B-abliterated-GGUF/resolve/main/"
        "mlabonne_Qwen3-14B-abliterated-Q4_K_M.gguf"
    ),
    "josiefied-qwen3-14b": (
        "https://huggingface.co/bartowski/Goekdeniz-Guelmez_Josiefied-Qwen3-14B-abliterated-v3-GGUF/"
        "resolve/main/Goekdeniz-Guelmez_Josiefied-Qwen3-14B-abliterated-v3-Q4_K_M.gguf"
    ),
    "hermes4-14b": (
        "https://huggingface.co/bartowski/NousResearch_Hermes-4-14B-GGUF/resolve/main/"
        "NousResearch_Hermes-4-14B-Q4_K_M.gguf"
    ),
    "qwen3-8b-abliterated": (
        "https://huggingface.co/bartowski/mlabonne_Qwen3-8B-abliterated-GGUF/resolve/main/"
        "mlabonne_Qwen3-8B-abliterated-Q4_K_M.gguf"
    ),
    "dolphin3-8b": (
        "https://huggingface.co/bartowski/Dolphin3.0-Llama3.1-8B-GGUF/resolve/main/"
        "Dolphin3.0-Llama3.1-8B-Q4_K_M.gguf"
    ),
    "gpt-oss-20b-abliterated": (
        "https://huggingface.co/bartowski/huihui-ai_Huihui-gpt-oss-20b-BF16-abliterated-GGUF/"
        "resolve/main/huihui-ai_Huihui-gpt-oss-20b-BF16-abliterated-MXFP4_MOE.gguf"
    ),
    "dolphin-mistral-24b": (
        "https://huggingface.co/bartowski/"
        "cognitivecomputations_Dolphin-Mistral-24B-Venice-Edition-GGUF/resolve/main/"
        "cognitivecomputations_Dolphin-Mistral-24B-Venice-Edition-Q4_K_M.gguf"
    ),
    "qwen3.8-27b-abliterated": (
        "https://huggingface.co/OBLITERATUS/Qwen3.8-27B-OBLITERATED/resolve/main/"
        "Qwen3.8-27B-OBLITERATED-Q4_K_M.gguf"
    ),
    "qwen3-30b-a3b-abliterated": (
        "https://huggingface.co/mradermacher/Qwen3-30B-A3B-abliterated-GGUF/resolve/main/"
        "Qwen3-30B-A3B-abliterated.Q4_K_M.gguf"
    ),
}
# Presets for graphics cards with 24 to 32 GB (the setup window lists them apart).
LARGE = (
    "mistral-small-24b",
    "gemma4-26b",
    "qwen3.8-27b",
    "qwen3-30b-a3b",
    "qwen3-coder-30b",
    "granite4-small",
    "gemma4-31b",
    "qwen3.5-35b-a3b",
    "qwen3-32b",
)
# Presets that are other people's changes of a model (the setup window lists them apart).
# The default, qwen14b-uncensored, is one too, but it stays at the top as the default.
MODIFIED = (
    "qwen3-14b-abliterated",
    "josiefied-qwen3-14b",
    "hermes4-14b",
    "qwen3-8b-abliterated",
    "dolphin3-8b",
    "gpt-oss-20b-abliterated",
    "dolphin-mistral-24b",
    "qwen3.8-27b-abliterated",
    "qwen3-30b-a3b-abliterated",
)
# What each of the tags below means (the setup window shows it when you point at a tag).
TAG_INFO = {
    "quick": "Few of its weights work on each word, or it is small: it answers fast.",
    "thinks first": "It reasons before it answers: better at hard questions, a slower reply.",
    "code": "Tuned for writing and fixing programs.",
    "tools": "Made for calling tools, which Starlight's tasks are built on.",
    "languages": "Strong beyond English: German, French and many more.",
    "uncensored": "Changed by others so that it refuses (almost) nothing.",
}
# Short tags the setup window shows next to a model's name, to tell the 30 apart at a glance.
MODEL_TAGS: dict[str, tuple[str, ...]] = {
    "qwen14b-uncensored": ("uncensored",),
    "qwen3-14b": ("thinks first",),
    "ministral14b": ("tools", "languages"),
    "gpt-oss-20b": ("quick", "thinks first"),
    "mistral-nemo": ("quick", "languages"),
    "gemma4-12b": ("languages",),
    "qwen14b-coder": ("code",),
    "qwen3.5-9b": ("thinks first",),
    "qwen7b": ("quick",),
    "qwen3-8b": ("thinks first",),
    "gemma4-e4b": ("quick", "languages"),
    "mistral-small-24b": ("tools", "languages"),
    "gemma4-26b": ("quick", "languages"),
    "qwen3.8-27b": ("thinks first", "code"),
    "qwen3-30b-a3b": ("quick",),
    "qwen3-coder-30b": ("quick", "code"),
    "granite4-small": ("tools",),
    "gemma4-31b": ("languages",),
    "qwen3.5-35b-a3b": ("quick", "thinks first"),
    "qwen3-32b": ("thinks first",),
    "qwen3-14b-abliterated": ("uncensored", "thinks first"),
    "josiefied-qwen3-14b": ("uncensored", "tools"),
    "hermes4-14b": ("uncensored",),
    "qwen3-8b-abliterated": ("uncensored", "thinks first"),
    "dolphin3-8b": ("uncensored", "quick"),
    "gpt-oss-20b-abliterated": ("uncensored", "thinks first"),
    "dolphin-mistral-24b": ("uncensored", "languages"),
    "qwen3.8-27b-abliterated": ("uncensored", "thinks first"),
    "qwen3-30b-a3b-abliterated": ("uncensored", "quick"),
}
# What the setup window tells about each preset: (title, description, video memory in GB).
MODEL_INFO = {
    "qwen14b-uncensored": (
        "Qwen2.5 14B, uncensored",
        "The default. Answers anything you ask; the most capable choice here.",
        10.0,
    ),
    "qwen14b": (
        "Qwen2.5 14B, original",
        "The same size with its safety training: more careful, cleaner endings.",
        10.0,
    ),
    "qwen3-14b": (
        "Qwen3 14B",
        "Newer; thinks before it answers: better at hard problems, slower to reply.",
        10.0,
    ),
    "ministral14b": (
        "Ministral 3 14B",
        "Mistral's newest 14B, made for using tools; strong in German and French.",
        11.0,
    ),
    "gpt-oss-20b": (
        "gpt-oss 20B",
        "OpenAI's open model: quick (only 3.6B of its 21B work per word), reasons first.",
        14.0,
    ),
    "mistral-nemo": (
        "Mistral Nemo 12B",
        "A little smaller and quicker than the 14B models; good in many languages.",
        10.0,
    ),
    "gemma4-12b": (
        "Gemma 4 12B",
        "Google's mid-size model: writes well, knows 140 languages.",
        10.0,
    ),
    "qwen14b-coder": (
        "Qwen2.5 Coder 14B",
        "Tuned for writing and fixing code; less good at everyday conversation.",
        10.0,
    ),
    "qwen3.5-9b": (
        "Qwen3.5 9B",
        "The newest Qwen for 8 GB cards; thinks before it answers.",
        7.0,
    ),
    "qwen7b": (
        "Qwen2.5 7B",
        "About twice as fast and half the video memory; a little less capable.",
        5.0,
    ),
    "qwen3-8b": (
        "Qwen3 8B",
        "The newer Qwen for 8 GB cards; thinks before it answers, so slower to reply.",
        7.0,
    ),
    "gemma4-e4b": (
        "Gemma 4 E4B",
        "Google's small, quick model for 6 to 8 GB cards; knows 140 languages.",
        6.0,
    ),
    "mistral-small-24b": (
        "Mistral Small 3.2 24B",
        "Mistral's 24B: sure with tools, strong in German and French; no thinking first.",
        18.0,
    ),
    "gemma4-26b": (
        "Gemma 4 26B A4B",
        "Google's mixture of experts: 4B of 26B work per word, so quick for its size.",
        19.0,
    ),
    "qwen3.8-27b": (
        "Qwen3.8 27B",
        "The strongest Qwen that fits 24 GB: reasoning, code and tools; thinks first.",
        20.0,
    ),
    "qwen3-30b-a3b": (
        "Qwen3 30B A3B 2507",
        "Only 3B of 30B work per word: very quick, answers without thinking first.",
        21.0,
    ),
    "qwen3-coder-30b": (
        "Qwen3 Coder 30B A3B",
        "The quick 30B tuned for code and for working through tasks with tools.",
        21.0,
    ),
    "granite4-small": (
        "Granite 4.0 Small 32B",
        "IBM's 32B (9B work per word), made for tools and long documents.",
        21.0,
    ),
    "gemma4-31b": (
        "Gemma 4 31B",
        "Google's largest Gemma 4: the best writer here, 140 languages.",
        22.0,
    ),
    "qwen3.5-35b-a3b": (
        "Qwen3.5 35B A3B",
        "The newer quick mixture of experts (3B of 35B per word); thinks first.",
        24.0,
    ),
    "qwen3-32b": (
        "Qwen3 32B",
        "The largest here: all 32B work on every word. Thorough and slower; thinks first.",
        25.0,
    ),
    "qwen3-14b-abliterated": (
        "Qwen3 14B, abliterated",
        "Qwen3 14B with its refusals removed (by mlabonne); thinks before it answers.",
        10.0,
    ),
    "josiefied-qwen3-14b": (
        "Josiefied Qwen3 14B",
        "Abliterated, then fine-tuned to stay good at tools and following instructions.",
        10.0,
    ),
    "hermes4-14b": (
        "Hermes 4 14B",
        "Nous Research's retraining of Qwen3 14B: follows you, not a fixed persona.",
        10.0,
    ),
    "qwen3-8b-abliterated": (
        "Qwen3 8B, abliterated",
        "The 8B with its refusals removed, for 8 GB cards; thinks before it answers.",
        7.0,
    ),
    "dolphin3-8b": (
        "Dolphin 3.0 8B",
        "An uncensored retraining of Llama 3.1 8B for chat, code and tools.",
        7.0,
    ),
    "gpt-oss-20b-abliterated": (
        "gpt-oss 20B, abliterated",
        "OpenAI's open model with its refusals removed (by huihui-ai); reasons first.",
        14.0,
    ),
    "dolphin-mistral-24b": (
        "Dolphin Mistral 24B Venice",
        "Mistral Small 24B retrained to be as uncensored as possible (Venice Uncensored).",
        18.0,
    ),
    "qwen3.8-27b-abliterated": (
        "Qwen3.8 27B, abliterated",
        "Qwen3.8 27B with its refusals removed (by OBLITERATUS); thinks first.",
        20.0,
    ),
    "qwen3-30b-a3b-abliterated": (
        "Qwen3 30B A3B, abliterated",
        "The quick 30B with its refusals removed (by mlabonne); thinks first.",
        21.0,
    ),
}
# Voice (optional, --voice): speech-to-text with whisper.cpp and text-to-speech with Piper,
# both running on this PC. The URLs can be changed in config/launcher.json.
WHISPER_URL = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-bin-x64.zip"
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
PIPER_URL = "https://github.com/rhasspy/piper/releases/latest/download/piper_windows_amd64.zip"
PIPER_VOICES = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES = (
    "de/de_DE/thorsten/medium/de_DE-thorsten-medium",
    "en/en_US/lessac/medium/en_US-lessac-medium",
)
GB = 1024**3
WEB_PORT = 8700  # the local web chat (matches config web.port)
MB = 1024**2


class LauncherError(Exception):
    """A problem the user can act on; shown without a traceback."""


class MissingDownload(LauncherError):
    """The server answers, but the file is not there (moved, renamed or taken down)."""

    def __init__(self, url: str, code: int) -> None:
        super().__init__(
            f"the download link does not work (HTTP {code}): {url}. The file was moved or "
            "taken down: pick another model (Settings in the window, or --model <name>), or "
            "set model_url in config/launcher.json"
        )
        self.url = url
        self.code = code


# -- settings --------------------------------------------------------------------------


LMSTUDIO_URL = "http://127.0.0.1:1234"  # LM Studio's local server (its Developer tab)
MODEL_SERVERS = ("llamacpp", "lmstudio")


@dataclass
class Settings:
    root: Path
    model_url: str = DEFAULT_MODEL_URL
    context: int = 32768
    variant: str = "vulkan"  # llama.cpp build: vulkan (any GPU) or cpu
    llama_release: str = "latest"  # or a tag such as "b6500"
    port: int = 8080
    # "llamacpp": Starlight downloads llama.cpp and the model and runs them. "lmstudio": LM
    # Studio runs the model (its local server); nothing is downloaded for it.
    model_server: str = "llamacpp"
    lmstudio_url: str = LMSTUDIO_URL
    lmstudio_model: str = ""  # empty: whichever model LM Studio has loaded
    viewer: bool = True
    web: bool = True  # open the browser chat; False: the terminal chat
    both: bool = False  # open the browser chat AND a terminal connected to the same backend
    use_model: bool = True  # False: Starlight's built-in test model, nothing downloaded
    assume_yes: bool = False
    update: bool = False
    tesseract: bool | None = None  # None: ask (Windows)
    voice: bool = False  # install speech-to-text and text-to-speech (about 0.6 GB)
    think: bool = True  # models that can think before they answer do so (see NO_THINKING)
    whisper_url: str = WHISPER_URL
    whisper_model_url: str = WHISPER_MODEL_URL
    piper_url: str = PIPER_URL
    github_token: str | None = field(default=None, repr=False)

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def model_file(self) -> Path:
        return self.models / _file_name(self.model_url)

    @property
    def uses_lmstudio(self) -> bool:
        return self.use_model and self.model_server == "lmstudio"

    @property
    def runs_llama(self) -> bool:
        """Starlight downloads llama.cpp and the model, and runs the model server itself."""
        return self.use_model and self.model_server != "lmstudio"


def _file_name(url: str) -> str:
    return url.rstrip("/").split("/")[-1].split("?")[0]


def find_root(start: Path) -> Path:
    """The Starlight folder: where pyproject.toml names the project "starlight"."""
    for folder in (start, *start.parents):
        project = folder / "pyproject.toml"
        if project.is_file() and re.search(
            r'^name\s*=\s*"starlight"', project.read_text(encoding="utf-8"), re.MULTILINE
        ):
            return folder
    raise LauncherError(
        f"No Starlight folder found at or above {start}. Put Starlight.exe into the "
        "Starlight folder (next to pyproject.toml) and start it from there."
    )


def preset_url(name: str) -> str:
    """The download link of a model preset (see MODEL_PRESETS)."""
    url = MODEL_PRESETS.get(name.strip().lower())
    if url is None:
        raise LauncherError(f"unknown model '{name}' (known: {', '.join(MODEL_PRESETS)})")
    return url


def load_settings(args: argparse.Namespace) -> Settings:
    if args.root:
        start = Path(args.root).resolve()
    elif getattr(sys, "frozen", False):  # Starlight.exe
        start = Path(sys.executable).resolve().parent
    else:
        start = Path(__file__).resolve().parent
    settings = Settings(root=find_root(start))
    saved = settings.root / "config" / "launcher.json"  # optional, for your own defaults
    if saved.is_file():
        try:
            data = json.loads(saved.read_text(encoding="utf-8-sig"))  # Notepad may add a BOM
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            if "model" in data and "model_url" not in data:  # a preset; model_url wins
                settings.model_url = preset_url(str(data["model"]))
            for key, value in data.items():
                if key in (
                    "model_url",
                    "context",
                    "variant",
                    "llama_release",
                    "port",
                    "model_server",
                    "lmstudio_url",
                    "lmstudio_model",
                    "viewer",
                    "web",
                    "both",
                    "tesseract",
                    "voice",
                    "think",
                    "whisper_url",
                    "whisper_model_url",
                    "piper_url",
                ):
                    setattr(settings, key, value)
        except (OSError, ValueError) as exc:
            raise LauncherError(f"{saved} is not valid JSON: {exc}") from exc
    if getattr(args, "model", None):
        settings.model_url = preset_url(args.model)
    for key in ("model_url", "context", "variant", "llama_release", "port"):
        value = getattr(args, key)
        if value is not None:
            setattr(settings, key, value)
    settings.viewer = settings.viewer and not args.no_viewer
    if getattr(args, "no_think", False):
        settings.think = False
    if args.terminal:
        settings.web = False
    if args.both:
        settings.both = True
    settings.use_model = not args.no_model
    if getattr(args, "lmstudio", False):
        settings.model_server = "lmstudio"
    if getattr(args, "lmstudio_model", None):
        settings.model_server = "lmstudio"
        settings.lmstudio_model = args.lmstudio_model.strip()
    if settings.model_server not in MODEL_SERVERS:
        raise LauncherError(
            f"model_server in config/launcher.json is {settings.model_server!r}; "
            "it can be llamacpp or lmstudio"
        )
    settings.lmstudio_url = str(settings.lmstudio_url).rstrip("/").removesuffix("/v1")
    settings.assume_yes = args.yes
    settings.update = args.update
    if args.tesseract:
        settings.tesseract = True
    if args.voice:
        settings.voice = True
    settings.github_token = os.environ.get("GITHUB_TOKEN") or None
    # Keep uv's cache on the same drive as the install, so it can hardlink packages into .venv
    # instead of copying them (which is slow and prints "Failed to hardlink files" when the
    # cache is on another drive, e.g. the default C:\Users\...\AppData while Starlight is on D:).
    # The cache then lives inside the Starlight folder. A user-set UV_CACHE_DIR is respected.
    os.environ.setdefault("UV_CACHE_DIR", str(settings.root / ".uv-cache"))
    return settings


# -- output ----------------------------------------------------------------------------


class Ui:
    """How the launcher talks to the user. This plain version prints lines (it is what log
    files and the CI see); ConsoleUi adds colors and a progress bar, and the window
    (launcher_gui.py) has its own."""

    #: True: output of the programs the launcher runs (uv, see run_checked) goes to
    #: run_output() instead of the console, so a window can show it.
    captures_output = False

    def __init__(self, assume_yes: bool) -> None:
        self.assume_yes = assume_yes

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def step(self, text: str) -> None:
        print(f"\n== {text}", flush=True)

    def progress(self, done: int, total: int | None, started: float) -> None:
        speed = done / max(time.monotonic() - started, 1e-6) / MB
        if total:
            line = f"   {done / MB:8.1f} / {total / MB:.1f} MB  ({speed:.1f} MB/s)"
        else:
            line = f"   {done / MB:8.1f} MB  ({speed:.1f} MB/s)"
        print("\r" + line, end="", flush=True)

    def waiting(self, text: str) -> None:
        """A status line that is replaced by the next one ("loading the model ... 12 s")."""
        print("\r" + text, end="", flush=True)

    def run_output(self, line: str) -> None:
        print(line, flush=True)

    def ask(self, question: str, default: bool) -> bool:
        if self.assume_yes:
            return default
        hint = "[Y/n]" if default else "[y/N]"
        try:
            answer = input(f"{question} {hint} ").strip().lower()
        except EOFError:
            return default
        return default if not answer else answer in ("y", "yes", "j", "ja")


def format_size(size: float) -> str:
    """12345678 -> "11.8 MB"; 9e9 -> "8.4 GB"."""
    if size >= GB:
        return f"{size / GB:.1f} GB"
    if size >= MB:
        return f"{size / MB:.0f} MB" if size >= 100 * MB else f"{size / MB:.1f} MB"
    return f"{max(size, 0) / 1024:.0f} KB"


def format_duration(seconds: float) -> str:
    """75 -> "1 min 15 s"; 5000 -> "1 h 23 min"."""
    seconds = int(max(seconds, 0) + 0.5)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def transfer_text(done: int, total: int | None, started: float) -> tuple[float, str]:
    """(fraction 0..1 or -1 if the size is unknown, "4.2 GB of 9.0 GB - 41 MB/s - 2 min left")."""
    elapsed = max(time.monotonic() - started, 1e-6)
    speed = done / elapsed
    pace = f"{speed / MB:.1f} MB/s" if speed < 10 * MB else f"{speed / MB:.0f} MB/s"
    if not total:
        return -1.0, f"{format_size(done)} - {pace}"
    left = (total - done) / speed if speed > 0 else 0
    text = f"{format_size(done)} of {format_size(total)} - {pace} - {format_duration(left)} left"
    return min(done / total, 1.0), text


def enable_ansi() -> bool:
    """Turn on colors in the Windows console (Windows 10 and later). Always true elsewhere."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


class ConsoleUi(Ui):
    """The terminal version: colored steps, a progress bar with speed and time left, one
    status line for the model loading. Used only on a real terminal."""

    BOLD, DIM, CYAN, GREEN, RESET = "\x1b[1m", "\x1b[2m", "\x1b[36m", "\x1b[32m", "\x1b[0m"
    CLEAR = "\x1b[2K\r"

    def __init__(self, assume_yes: bool) -> None:
        super().__init__(assume_yes)
        encoding = (getattr(sys.stdout, "encoding", None) or "ascii").lower()
        try:
            "\u2588\u2591\u25b8".encode(encoding)
            self.full, self.empty, self.arrow = "\u2588", "\u2591", "\u25b8"
        except (UnicodeEncodeError, LookupError):
            self.full, self.empty, self.arrow = "#", "-", ">"

    def step(self, text: str) -> None:
        print(f"\n{self.CYAN}{self.arrow}{self.RESET} {self.BOLD}{text}{self.RESET}", flush=True)

    def say(self, text: str = "") -> None:
        print(self.CLEAR + text if text.startswith("\r") else text, flush=True)

    def bar(self, fraction: float, width: int) -> str:
        filled = round(fraction * width)
        return (
            self.GREEN + self.full * filled + self.DIM + self.empty * (width - filled) + self.RESET
        )

    def progress(self, done: int, total: int | None, started: float) -> None:
        fraction, text = transfer_text(done, total, started)
        width = max(10, min(30, shutil.get_terminal_size((80, 20)).columns - len(text) - 12))
        if fraction < 0:
            print(f"{self.CLEAR}   {text}", end="", flush=True)
        else:
            bar = self.bar(fraction, width)
            print(f"{self.CLEAR}   {bar} {fraction * 100:3.0f}%  {text}", end="", flush=True)

    def waiting(self, text: str) -> None:
        print(f"{self.CLEAR}{self.DIM}{text.lstrip(chr(13))}{self.RESET}", end="", flush=True)


def make_ui(assume_yes: bool) -> Ui:
    """Colors and a progress bar on a terminal; plain lines when output goes to a file."""
    on_terminal = sys.stdout is not None and sys.stdout.isatty() and "NO_COLOR" not in os.environ
    if on_terminal and enable_ansi():
        return ConsoleUi(assume_yes)
    return Ui(assume_yes)


# -- downloads -------------------------------------------------------------------------


def _request(
    url: str, token: str | None = None, method: str = "GET", **headers: str
) -> urllib.request.Request:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **headers}, method=method
    )
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
    return request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


# Sent with every request when thinking is turned off (settings.think): llama-server hands
# these to the model's chat template. Qwen3 and its relatives answer without a <think> part;
# gpt-oss cannot skip it, so it thinks as little as it can. Other templates ignore them.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False, "reasoning_effort": "low"}}

# What a download server answers for a file that is not there. Hugging Face answers 401 for a
# repository that does not exist (or is private), 404 for a missing file in one that does.
GONE = (401, 404, 410)


@dataclass
class RemoteFile:
    size: int | None
    sha256: str | None


def probe(url: str) -> RemoteFile:
    """Size and checksum without downloading. Hugging Face tells both on the first
    (redirecting) response: X-Linked-Size and X-Linked-Etag (the SHA-256)."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = _request(url, method="HEAD")
    try:
        with opener.open(request, timeout=30) as response:
            headers = response.headers
    except urllib.error.HTTPError as exc:
        if exc.code in GONE:
            raise MissingDownload(url, exc.code) from exc
        if exc.code not in (301, 302, 303, 307, 308):
            raise LauncherError(f"{url}: HTTP {exc.code}") from exc
        headers = exc.headers
        if not headers.get("X-Linked-Size"):  # a plain redirect: ask the target
            location = headers.get("Location")
            if location:
                with urllib.request.urlopen(_request(location, method="HEAD"), timeout=30) as r:
                    return RemoteFile(_int(r.headers.get("Content-Length")), None)
    size = _int(headers.get("X-Linked-Size") or headers.get("Content-Length"))
    etag = (headers.get("X-Linked-Etag") or "").strip('"').lower()
    return RemoteFile(size, etag if re.fullmatch(r"[0-9a-f]{64}", etag) else None)


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * MB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_file(source: Path, target: Path) -> None:
    """``os.replace``, waiting out a lock for a moment: on Windows an antivirus scan or
    OneDrive may hold the file for a second, and a 9 GB download must not fail on that."""
    delays = (0.1, 0.2, 0.5, 1.0, 1.0, 1.0, 2.0)
    for wait in delays:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != "nt":
                raise
        time.sleep(wait)
    os.replace(source, target)


def download(
    url: str,
    dest: Path,
    ui: Ui,
    sha256: str | None = None,
    token: str | None = None,
    attempts: int = 4,
) -> Path:
    """Download to ``dest``, resuming a previous ``.part`` file, and check the SHA-256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with urllib.request.urlopen(_request(url, token, **headers), timeout=60) as response:
                if have and response.status != 206:  # the server ignored the range
                    have = 0
                length = _int(response.headers.get("Content-Length"))
                total = have + length if length is not None else None
                started = time.monotonic()
                done = have
                last = 0.0
                with part.open("ab" if have else "wb") as out:
                    while chunk := response.read(MB):
                        out.write(chunk)
                        done += len(chunk)
                        if time.monotonic() - last > 0.5:
                            ui.progress(done - have, (total - have) if total else None, started)
                            last = time.monotonic()
                ui.progress(done - have, (total - have) if total else None, started)
                ui.say()
                if total is not None and done < total:  # closed early, without an error
                    raise ConnectionError(f"the connection closed after {done} of {total} bytes")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:  # already complete
                break
            if exc.code in GONE:
                raise MissingDownload(url, exc.code) from exc
            if exc.code in (401, 403, 404) or attempt == attempts:
                raise LauncherError(f"download failed: {url} (HTTP {exc.code})") from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.HTTPException,  # e.g. IncompleteRead: a chunked answer cut short
        ) as exc:
            if attempt == attempts:
                raise LauncherError(f"download failed: {url} ({exc})") from exc
        ui.say(f"   connection problem, resuming (try {attempt + 1} of {attempts}) ...")
        time.sleep(2 * attempt)
    if sha256:
        ui.say("   checking the SHA-256 checksum ...")
        actual = sha256_of(part)
        if actual != sha256.lower():
            part.unlink(missing_ok=True)
            raise LauncherError(
                f"checksum mismatch for {dest.name}: expected {sha256}, got {actual}. "
                "The broken file was deleted; start again to re-download it."
            )
    replace_file(part, dest)
    return dest


def extract_member(archive: Path, name: str, target: Path, keep: tuple[str, ...] = ()) -> Path:
    """Unpack the folder of the file called ``name`` (e.g. llama-server.exe) into
    ``target``, flattening whatever folder structure the archive has. The program files
    are replaced as a whole; the subfolders named in ``keep`` (downloaded models or voices)
    stay as they are."""
    with tempfile.TemporaryDirectory(dir=archive.parent) as tmp:
        tmp_path = Path(tmp) / "unpacked"  # only the archive's files in here
        try:
            if archive.name.endswith(".zip"):
                with zipfile.ZipFile(archive) as zf:
                    _safe_extract_zip(zf, tmp_path)
            else:
                with tarfile.open(archive) as tf:
                    tf.extractall(tmp_path, filter="data")
        except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
            archive.unlink(missing_ok=True)  # a broken download: fetch it again next time
            raise LauncherError(
                f"{archive.name} is not a complete archive ({exc}); start again to download "
                "it again"
            ) from exc
        found = next((p for p in tmp_path.rglob(name) if p.is_file()), None)
        if found is None:
            raise LauncherError(f"{archive.name} does not contain {name}")
        # The kept folders wait next to the target (same drive, outside the temporary
        # folder), so they survive even if replacing the program fails half way - e.g.
        # Windows refusing to delete a whisper-server.exe that is still running.
        kept: list[tuple[Path, Path]] = []
        try:
            if target.exists():
                for folder in keep:
                    if (target / folder).is_dir():
                        aside = target.with_name(f"{target.name}.keep-{folder}")
                        if aside.exists():
                            shutil.rmtree(aside)
                        replace_file(target / folder, aside)
                        kept.append((aside, target / folder))
                shutil.rmtree(target)
            shutil.copytree(found.parent, target)
        finally:
            for aside, back in kept:
                back.parent.mkdir(parents=True, exist_ok=True)
                if back.exists():  # the archive has its own: yours win
                    shutil.rmtree(back)
                replace_file(aside, back)
    binary = target / name
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | 0o111)
    return binary


def _safe_extract_zip(archive: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for member in archive.infolist():
        path = (dest / member.filename).resolve()
        if not path.is_relative_to(root):
            raise LauncherError(f"unsafe path in archive: {member.filename}")
    archive.extractall(dest)


# -- what to install -------------------------------------------------------------------


def exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def uv_asset() -> str:
    machine = platform.machine().lower()
    if os.name == "nt":
        return (
            "uv-aarch64-pc-windows-msvc.zip"
            if "arm" in machine
            else ("uv-x86_64-pc-windows-msvc.zip")
        )
    if sys.platform == "darwin":
        return (
            "uv-aarch64-apple-darwin.tar.gz"
            if machine == "arm64"
            else ("uv-x86_64-apple-darwin.tar.gz")
        )
    return (
        "uv-aarch64-unknown-linux-gnu.tar.gz"
        if machine in ("aarch64", "arm64")
        else ("uv-x86_64-unknown-linux-gnu.tar.gz")
    )


# -- updating without a way back ------------------------------------------------------------

BACKUPS_KEEP = 3  # copies of settings and data taken before an --update
SNAPSHOT_LIMIT = 2 * GB  # a bigger folder is left out (with a note) rather than copied

# What a copy before an update holds: your settings, your conversations and lists, your
# memory. (The databases and caches are rebuilt by Starlight itself.)
SNAPSHOT_PARTS = (("config", "config"), ("data", "data"), ("memory", "memory"))
SNAPSHOT_SKIP = ("*.sqlite*", "*.db", "*.db-*", "*.lock", "*.tmp", "*.part", "web_cache*")


def folder_size(folder: Path) -> int:
    total = 0
    for item in folder.rglob("*"):
        with contextlib.suppress(OSError):
            if item.is_file():
                total += item.stat().st_size
    return total


def snapshot_before_update(settings: Settings, ui: Ui) -> Path | None:
    """Copy config/, data/ and memory/ to backups/update-<time>/ before --update changes
    anything, and keep only the newest BACKUPS_KEEP of these copies."""
    root = settings.root
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = root / "backups" / f"update-{stamp}"
    for number in range(2, 100):  # two in the same second
        if not target.exists():
            break
        target = root / "backups" / f"update-{stamp}-{number}"
    copied: list[str] = []
    for name, relative in SNAPSHOT_PARTS:
        source = root / relative
        if not source.is_dir():
            continue
        if folder_size(source) > SNAPSHOT_LIMIT:
            ui.say(
                f"   {relative}/ is over {SNAPSHOT_LIMIT // GB} GB: not copied before the update"
            )
            continue
        shutil.copytree(source, target / name, ignore=shutil.ignore_patterns(*SNAPSHOT_SKIP))
        copied.append(name)
    for old in sorted(root.glob("backups/update-*"))[: -BACKUPS_KEEP or None]:
        if old != target and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    if not copied:
        return None
    ui.say(f"   Copied {', '.join(copied)} to {target.relative_to(root)} (for --rollback)")
    return target


def previous_folder(settings: Settings, name: str) -> Path:
    return settings.tools / "previous" / name


def set_aside(settings: Settings, folder: Path, name: str) -> bool:
    """Before an update replaces a program folder: move it to tools/previous/<name>, so the
    old version is still there if the new one does not work (or the download fails)."""
    if not folder.is_dir():
        return False
    kept = previous_folder(settings, name)
    if kept.exists():
        shutil.rmtree(kept)
    kept.parent.mkdir(parents=True, exist_ok=True)
    try:
        replace_file(folder, kept)
    except OSError as exc:
        raise LauncherError(
            f"{folder.name} is in use: close Starlight (and its model server) and try again ({exc})"
        ) from exc
    return True


def put_back(settings: Settings, folder: Path, name: str) -> None:
    """An update failed half way: the old program folder returns."""
    kept = previous_folder(settings, name)
    if kept.is_dir():
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        replace_file(kept, folder)


def installed_folders(settings: Settings) -> dict[str, Path]:
    """The program folders an update replaces, by the name they are kept under."""
    return {
        "uv": settings.tools / "uv",
        f"llama.cpp-{settings.variant}": settings.tools / "llama.cpp" / settings.variant,
    }


def restore_snapshot(settings: Settings, snapshot: Path, ui: Ui) -> None:
    """Put the files of a copy back over the current ones (anything newer that the copy
    does not have stays). A fresh copy of the current state is taken first."""
    snapshot_before_update(settings, ui)
    for name, relative in SNAPSHOT_PARTS:
        if (snapshot / name).is_dir():
            shutil.copytree(snapshot / name, settings.root / relative, dirs_exist_ok=True)


def rollback(settings: Settings, ui: Ui) -> int:
    """Go back to the uv and llama.cpp that were installed before the last --update (and,
    if you say so, to the settings and data from then). Running it again goes forward."""
    swapped: list[str] = []
    for name, folder in installed_folders(settings).items():
        kept = previous_folder(settings, name)
        if not kept.is_dir():
            continue
        parked = kept.with_name(kept.name + ".swap")
        if parked.exists():
            shutil.rmtree(parked)
        try:
            if folder.exists():
                replace_file(folder, parked)
            replace_file(kept, folder)
            if parked.exists():
                replace_file(parked, kept)
        except OSError as exc:
            raise LauncherError(
                f"could not switch {name}: close Starlight (and its model server) and try again"
                f" ({exc})"
            ) from exc
        source = folder / "source.txt"
        what = source.read_text(encoding="utf-8").strip() if source.is_file() else "the older copy"
        swapped.append(f"{name}: {what}")
    if not swapped:
        raise LauncherError("there is no earlier version to go back to (--update keeps one)")
    ui.say("Went back to the previous version of:")
    for line in swapped:
        ui.say(f"   {line}")
    ui.say("(Run --rollback again to return to the newer one.)")
    snapshots = sorted(settings.root.glob("backups/update-*"))
    if snapshots and ui.ask(
        f"Also put back your settings and data from {snapshots[-1].name}?", default=False
    ):
        restore_snapshot(settings, snapshots[-1], ui)
        ui.say("   Settings, data and memory from before the update are back.")
    return 0


def ensure_uv(settings: Settings, ui: Ui) -> Path:
    """Starlight's own copy of uv in tools/uv (it installs Python and the packages)."""
    binary = settings.tools / "uv" / exe("uv")
    if binary.is_file() and not settings.update:
        return binary
    ui.step("Downloading uv (installs Python and Starlight's packages)")
    asset = uv_asset()
    with urllib.request.urlopen(_request(f"{UV_DOWNLOAD}/{asset}.sha256"), timeout=30) as r:
        expected = r.read().decode().split()[0]
    archive = download(f"{UV_DOWNLOAD}/{asset}", settings.tools / asset, ui, sha256=expected)
    folder = settings.tools / "uv"
    moved = settings.update and set_aside(settings, folder, "uv")
    try:
        extract_member(archive, exe("uv"), folder)
        (folder / "source.txt").write_text(f"{UV_DOWNLOAD}/{asset}\n", encoding="utf-8")
    except BaseException:
        if moved:
            put_back(settings, folder, "uv")
        raise
    archive.unlink()
    return binary


def llama_asset_names(variant: str) -> tuple[str, ...]:
    """The end of the llama.cpp release file name for this computer and build, newest
    naming first (the Linux builds moved from .zip to .tar.gz)."""
    if os.name == "nt":
        return (f"bin-win-{variant}-x64.zip",)
    stem = "bin-ubuntu-x64" if variant == "cpu" else f"bin-ubuntu-{variant}-x64"
    return (f"{stem}.tar.gz", f"{stem}.zip")


def pick_llama_asset(release: dict[str, Any], variant: str) -> tuple[str, str | None]:
    """(download URL, SHA-256 or None) of the right file in a GitHub release."""
    wanted = llama_asset_names(variant)
    for asset in release.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.startswith("llama-") and name.endswith(wanted):
            digest = str(asset.get("digest") or "")
            sha = digest.removeprefix("sha256:") if digest.startswith("sha256:") else None
            return str(asset["browser_download_url"]), sha
    names = ", ".join(str(a.get("name")) for a in release.get("assets") or [])
    raise LauncherError(f"no llama.cpp download matching *{wanted[0]} (found: {names or 'none'})")


def _read_text(url: str, token: str | None = None) -> str:
    with urllib.request.urlopen(_request(url, token), timeout=30) as response:
        return str(response.read().decode("utf-8", "replace"))


def _nightly_tag(text: str) -> str | None:
    tag = text.strip()
    return tag if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tag) else None


def resolve_llama(settings: Settings) -> tuple[str, str | None]:
    """Ask GitHub's API for the release; if that is blocked or rate-limited, fall back
    to the release page and the known file naming.

    llama.cpp's versioned releases (the one GitHub calls "latest") carry no programs,
    only nightly-tag.txt: the name of the b#### build release that has them."""
    tag, token = settings.llama_release, settings.github_token
    try:
        api = f"{LLAMA_API}/latest" if tag == "latest" else f"{LLAMA_API}/tags/{tag}"
        release = json.loads(_read_text(api, token))
        try:
            return pick_llama_asset(release, settings.variant)
        except LauncherError:
            pointer = next(
                (a for a in release.get("assets") or [] if a.get("name") == "nightly-tag.txt"),
                None,
            )
            if pointer is None:
                raise
            nightly = _nightly_tag(_read_text(str(pointer["browser_download_url"])))
            if nightly is None:
                raise
            build = json.loads(_read_text(f"{LLAMA_API}/tags/{nightly}", token))
            return pick_llama_asset(build, settings.variant)
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass
    folder = "latest/download" if tag == "latest" else f"download/{tag}"
    try:
        nightly = _nightly_tag(_read_text(f"{LLAMA_RELEASES}/{folder}/nightly-tag.txt"))
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        nightly = None
    if nightly:
        tag = nightly
    elif tag == "latest":  # an older release that has the programs itself
        with urllib.request.urlopen(_request(f"{LLAMA_RELEASES}/latest"), timeout=30) as r:
            tag = r.geturl().rstrip("/").split("/")[-1]
    name = f"llama-{tag}-{llama_asset_names(settings.variant)[0]}"
    return f"{LLAMA_RELEASES}/download/{tag}/{name}", None


def ensure_llama(settings: Settings, ui: Ui) -> Path:
    target = settings.tools / "llama.cpp" / settings.variant
    binary = target / exe("llama-server")
    if binary.is_file() and not settings.update:
        return binary
    ui.step(f"Downloading the llama.cpp model server ({settings.variant} build)")
    url, sha = resolve_llama(settings)
    ui.say(f"   {url}")
    archive = download(url, settings.tools / url.split("/")[-1], ui, sha256=sha)
    name = f"llama.cpp-{settings.variant}"
    moved = settings.update and set_aside(settings, target, name)
    try:
        extract_member(archive, exe("llama-server"), target)
        (target / "source.txt").write_text(url + "\n", encoding="utf-8")
    except BaseException:
        if moved:
            put_back(settings, target, name)
        raise
    archive.unlink()
    return binary


def voice_downloads() -> bool:
    """Ready-made speech programs are published for Windows only."""
    return os.name == "nt"


def ensure_voice(settings: Settings, ui: Ui) -> None:
    """whisper.cpp (speech to text) and Piper (text to speech) with a German and an English
    voice, into tools/whisper and tools/piper, where the voice module finds them. Optional:
    if a download fails, Starlight still starts, just without voice."""
    if not settings.voice:
        return
    if not voice_downloads():
        ui.say(
            "   Voice: install whisper.cpp (whisper-server) and Piper yourself on Linux and "
            "set their paths in config/modules/voice.yaml."
        )
        return
    whisper = settings.tools / "whisper"
    piper = settings.tools / "piper"
    try:
        if not (whisper / exe("whisper-server")).is_file() or settings.update:
            ui.step("Downloading whisper.cpp (speech to text)")
            archive = download(settings.whisper_url, settings.tools / "whisper.zip", ui)
            extract_member(archive, exe("whisper-server"), whisper, keep=("models",))
            archive.unlink()
        model = whisper / "models" / _file_name(settings.whisper_model_url)
        if not model.is_file():
            ui.step(f"Downloading the speech model {model.name}")
            download(settings.whisper_model_url, model, ui)
        if not (piper / exe("piper")).is_file() or settings.update:
            ui.step("Downloading Piper (text to speech)")
            archive = download(settings.piper_url, settings.tools / "piper.zip", ui)
            extract_member(archive, exe("piper"), piper, keep=("voices",))
            archive.unlink()
        for voice in VOICES:
            name = voice.split("/")[-1]
            for suffix in (".onnx", ".onnx.json"):
                target = piper / "voices" / f"{name}{suffix}"
                if not target.is_file():
                    ui.step(f"Downloading the voice {name}{suffix}")
                    download(f"{PIPER_VOICES}/{voice}{suffix}", target, ui)
    except LauncherError as exc:
        ui.say(f"   Voice could not be installed ({exc}); Starlight works without it.")


def ensure_model(settings: Settings, ui: Ui, remote: RemoteFile | None) -> Path:
    dest = settings.model_file
    if dest.is_file():  # (a download only becomes the model file once it is complete)
        return dest
    ui.step(f"Downloading the model {dest.name}")
    return download(settings.model_url, dest, ui, sha256=remote.sha256 if remote else None)


def remove_old_draft(settings: Settings, ui: Ui) -> None:
    """Earlier versions downloaded a small "draft" model for speculative decoding into
    models/draft. It is no longer used: delete it to free the space."""
    folder = settings.models / "draft"
    if not folder.is_dir():
        return
    size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    shutil.rmtree(folder, ignore_errors=True)
    if not folder.exists():
        ui.say(f"   removed the old draft model (no longer used), freed {size / GB:.1f} GB")


def write_launcher_config(settings: Settings, server: Path | None, model: Path | None) -> Path:
    """config/starlight.launcher.yaml: the model server for Starlight. JSON is valid YAML,
    and your own config/starlight.local.yaml still overrides it."""
    data: dict[str, Any]
    if settings.uses_lmstudio:
        data = {"model": lmstudio_model_config(settings)}
    elif settings.use_model and server is not None and model is not None:
        data = {
            "model": {
                "provider": "llamacpp",
                "options": {
                    "base_url": f"http://127.0.0.1:{settings.port}/v1",
                    "model": model.stem,
                    **({} if settings.think else {"extra": NO_THINKING}),
                },
                "context_window": settings.context,
                "server": {
                    "binary": str(server),
                    "model_path": str(model),
                    "port": settings.port,
                    "gpu_layers": 999,
                },
            }
        }
    else:
        data = {"model": {"provider": "echo"}}
    path = settings.root / "config" / "starlight.launcher.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Written by Starlight.exe on every start. Put your own changes into\n"
        "# config/starlight.local.yaml, which overrides this file.\n"
        + json.dumps(data, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


# -- running ---------------------------------------------------------------------------


class ProcessGroup:
    """Everything the launcher starts ends when the launcher ends, however it ends.
    On Windows a job object kills the whole process tree when the window is closed."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen[bytes]] = []
        self._logs: list[Any] = []
        self._job: Any = None
        if sys.platform == "win32":
            self._job = _create_kill_on_close_job()

    def start(self, command: list[str], log: Path | None, cwd: Path) -> subprocess.Popen[bytes]:
        output: Any = subprocess.DEVNULL
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            output = log.open("ab")
            self._logs.append(output)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )
        self.processes.append(process)
        return process

    def terminate(self, process: subprocess.Popen[bytes]) -> None:
        """End one process and everything it started."""
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            import signal

            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(10)

    def stop(self) -> None:
        for process in reversed(self.processes):
            self.terminate(process)
        for handle in self._logs:
            handle.close()
        self._logs.clear()


# Game mode: Starlight writes this file to free the graphics card (a game started, or you
# said so), and removes it to get the model back. Same as starlight/core/modelpause.py.
PAUSE_FILE = "model.pause.json"


def pause_file(root: Path) -> Path:
    return root / "data" / PAUSE_FILE


def read_pause(root: Path) -> dict[str, Any] | None:
    """The pause in force ({"reason", "by", "since"}), or None."""
    try:
        data = json.loads(pause_file(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else {"reason": "paused"}


def end_pause(root: Path) -> bool:
    """Remove the pause file (Resume now). True if there was one."""
    try:
        pause_file(root).unlink()
    except OSError:  # not there, or held open for a moment
        return False
    return True


class ModelServerWatchdog:
    """Starts the model server again when it stops by itself (a graphics driver hiccup, out
    of video memory), so a crash costs a minute instead of the whole session. A server that
    cannot run is not restarted forever: after ``limit`` restarts within ``window``
    seconds it stays down, and the log says why. While ``pause`` exists (game mode) the
    server is stopped on purpose, and started again when it goes."""

    def __init__(
        self,
        group: ProcessGroup,
        command: list[str],
        process: subprocess.Popen[bytes],
        log: Path,
        cwd: Path,
        ui: Ui,
        limit: int = 4,
        window: float = 600.0,
        poll: float = 2.0,
        backoff: float = 3.0,
        pause: Path | None = None,
    ) -> None:
        self.group, self.command, self.process = group, command, process
        self.log, self.cwd, self.ui = log, cwd, ui
        self.limit, self.window, self.poll, self.backoff = limit, window, poll, backoff
        self.pause = pause
        self.restarts: list[float] = []
        self.gave_up = False
        self.restarting = False  # it stopped by itself and is being started again
        self.paused = False  # stopped for game mode
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="model-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Call before the processes are stopped on purpose, or it would restart them."""
        self._stopped.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def restart_now(self) -> None:
        """The user asked for a restart: end the server, start it again, count from zero.
        A game-mode pause ends with it."""
        self._stopped.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self.group.terminate(self.process)
        if self.pause is not None:
            with contextlib.suppress(OSError):
                self.pause.unlink(missing_ok=True)
        self.paused = False
        self.restarts.clear()
        self.gave_up = False
        self.restarting = True
        self._stopped = threading.Event()
        try:
            self.process = self.group.start(self.command, self.log, self.cwd)
        except OSError as exc:
            self.ui.say(f"   could not start it: {exc}")
            self.gave_up = True
        finally:
            self.restarting = False
        self._thread = threading.Thread(target=self._watch, name="model-watchdog", daemon=True)
        self._thread.start()

    def _watch(self) -> None:
        while not self._stopped.wait(self.poll):
            if self._follow_pause():
                continue
            if self.process.poll() is not None and not self._restart():
                return

    def _follow_pause(self) -> bool:
        """Stop the server when a pause begins, start it when it ends. True while paused
        or just resumed (nothing else to check this round)."""
        wanted = self.pause is not None and self.pause.exists()
        if wanted == self.paused:
            return wanted
        self.paused = wanted
        if wanted:
            self.ui.say("\nGame mode: the model server stops to free the graphics card.")
            self.group.terminate(self.process)
            return True
        self.ui.say("\nGame mode ended: starting the model server again ...")
        self.restarts.clear()
        self.gave_up = False
        self.restarting = True
        try:
            self.process = self.group.start(self.command, self.log, self.cwd)
        except OSError as exc:
            self.ui.say(f"   could not start it: {exc}")
            self.gave_up = True
        finally:
            self.restarting = False
        return True

    def _restart(self) -> bool:
        now = time.monotonic()
        self.restarts = [t for t in self.restarts if now - t < self.window]
        code = self.process.returncode
        if len(self.restarts) >= self.limit:
            self.gave_up = True
            self.ui.say(
                f"\nThe model server stopped again (exit code {code}) and was restarted "
                f"{self.limit} times in {self.window / 60:.0f} minutes, so it stays down. "
                f"See {self.log}."
            )
            return False
        self.restarts.append(now)
        self.ui.say(
            f"\nThe model server stopped (exit code {code}); starting it again "
            f"({len(self.restarts)} of {self.limit}) ..."
        )
        self.restarting = True
        try:
            if self._stopped.wait(self.backoff * len(self.restarts)):
                return False
            try:
                self.process = self.group.start(self.command, self.log, self.cwd)
            except OSError as exc:
                self.ui.say(f"   could not start it: {exc}")
                self.gave_up = True
                return False
            return True
        finally:
            self.restarting = False


def _create_kill_on_close_job() -> Any:
    """A job object that the launcher itself joins: every process it starts afterwards
    belongs to it, and Windows ends them all when the launcher's last handle closes."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kill_on_close, breakaway_ok = 0x2000, 0x0800
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    # BREAKAWAY_OK: apps Starlight opens for you (Notepad ...) may leave the job and stay.
    limits.BasicLimitInformation.LimitFlags = kill_on_close | breakaway_ok
    extended_limit_information = 9
    ok = kernel32.SetInformationJobObject(
        wintypes.HANDLE(job),
        extended_limit_information,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    if not ok or not kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job), wintypes.HANDLE(kernel32.GetCurrentProcess())
    ):
        return None  # e.g. already in a job that forbids nesting: fall back to taskkill
    return job


def port_in_use(port: int) -> bool:
    import socket

    with socket.socket() as probe_socket:
        probe_socket.settimeout(0.5)
        return probe_socket.connect_ex(("127.0.0.1", port)) == 0


def _local_open(request: urllib.request.Request | str, timeout: float) -> Any:
    """Open a URL on this PC. A system or company proxy is not used for those: it cannot
    reach 127.0.0.1, and urllib would send the request to it all the same."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        request, timeout=timeout
    )


def wait_for_web(port: int, ui: Ui, timeout: float = 60) -> None:
    """Wait until the web backend is accepting connections before the terminal connects."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if port_in_use(port):
            return
        time.sleep(0.5)
    ui.say(f"   the web backend did not open on port {port} in time; the terminal may not connect")


def wait_for_model(
    port: int, process: subprocess.Popen[bytes] | None, log: Path, ui: Ui, timeout: float = 900
) -> None:
    """llama-server answers /health with 503 while loading and 200 when ready."""
    started = time.monotonic()
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() - started < timeout:
        if process is not None and process.poll() is not None:
            tail = log.read_text(encoding="utf-8", errors="replace")[-2500:] if log.exists() else ""
            raise LauncherError(
                f"the model server stopped (exit code {process.returncode}). Last lines of "
                f"{log}:\n{tail}"
            )
        with (
            contextlib.suppress(urllib.error.URLError, ConnectionError, TimeoutError, OSError),
            _local_open(url, 5) as response,
        ):
            if response.status == 200:
                ui.say(f"\r   model ready after {time.monotonic() - started:.0f} s".ljust(40))
                return
        ui.waiting(f"   loading the model ... {time.monotonic() - started:4.0f} s")
        time.sleep(1)
    raise LauncherError(f"the model server did not become ready within {timeout:.0f} s")


def starlight(uv: Path, *args: str) -> list[str]:
    return [str(uv), "run", "--no-sync", "starlight", *args]


@dataclass
class Installed:
    uv: Path
    server: Path | None = None
    model: Path | None = None


def setup(settings: Settings, ui: Ui) -> Installed:
    """Install whatever is missing."""
    if settings.update:
        snapshot_before_update(settings, ui)
    first_run = not (settings.tools / "uv" / exe("uv")).is_file()
    remote = None
    if settings.runs_llama and not settings.model_file.is_file():
        remote = probe(settings.model_url)
    if first_run or (remote is not None):
        model_size = (remote.size or 0) if remote else 0
        need = (
            (1.6 * GB if first_run else 0) + (0.2 * GB if settings.runs_llama else 0) + model_size
        )
        free = shutil.disk_usage(settings.root).free
        ui.say("Starlight will download and set up, inside " + str(settings.root) + ":")
        if first_run:
            ui.say("   - uv, Python 3.12 and Starlight's packages      ~1.3 GB")
            ui.say("   - the Chromium browser for web pages and logins ~0.3 GB")
        if settings.runs_llama:
            ui.say(f"   - the llama.cpp model server ({settings.variant})         ~0.2 GB")
        if remote is not None:
            ui.say(f"   - the model {settings.model_file.name:<34} {model_size / GB:.1f} GB")
        ui.say(f"   Needed: about {need / GB:.1f} GB. Free on this drive: {free / GB:.1f} GB.")
        if free < need + GB:
            raise LauncherError("not enough free disk space for the download")
        if not ui.ask("Continue?", default=True):
            raise LauncherError("cancelled; nothing was changed")

    uv = ensure_uv(settings, ui)
    ui.step("Setting up Python and Starlight's packages")
    run_checked(
        [str(uv), "sync", "--extra", "all", "--locked", "--python", "3.12"], settings.root, ui
    )
    marker = settings.tools / ".chromium-installed"
    if not marker.exists() or settings.update:
        ui.step("Installing the Chromium browser")
        run_checked(
            [str(uv), "run", "--no-sync", "playwright", "install", "chromium"], settings.root, ui
        )
        marker.write_text("ok\n", encoding="utf-8")
    installed = Installed(uv)
    if settings.runs_llama:
        installed.server = ensure_llama(settings, ui)
        installed.model = ensure_model(settings, ui, remote)
    remove_old_draft(settings, ui)
    maybe_tesseract(settings, ui)
    ensure_voice(settings, ui)
    if settings.uses_lmstudio:
        ui.step("Getting LM Studio ready")
        ensure_lmstudio(settings, ui)
    write_launcher_config(settings, installed.server, installed.model)
    return installed


def run_checked(command: list[str], cwd: Path, ui: Ui | None = None) -> None:
    """Run a program and wait. With a window (ui.captures_output) its output is shown there."""
    if ui is None or not ui.captures_output:
        result = subprocess.run(command, cwd=cwd, check=False)
        returncode = result.returncode
    else:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        with subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                if line.strip():
                    ui.run_output(line.rstrip())
        returncode = process.returncode
    if returncode != 0:
        raise LauncherError(f"command failed ({returncode}): {' '.join(command)}")


def maybe_tesseract(settings: Settings, ui: Ui) -> None:
    """Screen reading (OCR) is optional and needs an installer with administrator rights,
    so it is only offered, never installed silently."""
    if (
        os.name != "nt"
        or shutil.which("tesseract")
        or Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists()
    ):
        return
    wanted = settings.tesseract
    if wanted is None:
        if settings.assume_yes:
            return
        wanted = ui.ask(
            "Install Tesseract OCR for reading the screen? Windows will ask for "
            "administrator approval.",
            default=False,
        )
    if not wanted:
        return
    if shutil.which("winget") is None:
        ui.say(
            "   winget is not available; install Tesseract from "
            "https://github.com/UB-Mannheim/tesseract/wiki"
        )
        return
    install_tesseract(ui)


def install_tesseract(ui: Ui) -> None:
    """Install Tesseract with winget (it shows its own progress and the administrator
    question). Optional: a failure is reported, and the setup goes on."""
    result = subprocess.run(
        [
            "winget",
            "install",
            "-e",
            "--id",
            "UB-Mannheim.TesseractOCR",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ],
        check=False,
    )
    if result.returncode != 0:
        ui.say(
            f"   Tesseract was not installed (winget exit code {result.returncode}); install it "
            "from https://github.com/UB-Mannheim/tesseract/wiki to read the screen."
        )


@dataclass
class Gpu:
    name: str
    vram: int | None  # bytes of video memory, if the driver tells


def pick_gpu(entries: list[tuple[str, int | None]]) -> Gpu | None:
    """The graphics card that Starlight will use: the one with the most video memory,
    ignoring Windows' own placeholder adapters."""
    real = [
        (name, vram)
        for name, vram in entries
        if name and not any(w in name.lower() for w in ("basic", "remote", "virtual", "mirror"))
    ]
    if not real:
        return None
    name, vram = max(real, key=lambda entry: entry[1] or 0)
    return Gpu(name, vram)


def detect_gpu() -> Gpu | None:
    """Name and video memory of the graphics card (Windows; from the driver's registry
    entries, which - unlike WMI's AdapterRAM - are right for cards over 4 GB)."""
    if sys.platform != "win32":
        return None
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    entries: list[tuple[str, int | None]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for index in range(32):
                try:
                    sub_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                if not sub_name.isdigit():
                    continue
                with contextlib.suppress(OSError), winreg.OpenKey(root, sub_name) as key:
                    name = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                    vram: int | None = None
                    for value in (
                        "HardwareInformation.qwMemorySize",
                        "HardwareInformation.MemorySize",
                    ):
                        with contextlib.suppress(OSError):
                            raw = winreg.QueryValueEx(key, value)[0]
                            vram = (
                                int.from_bytes(raw, "little")
                                if isinstance(raw, bytes)
                                else int(raw)
                            )
                            break
                    entries.append((name, vram))
    except OSError:
        return None
    return pick_gpu(entries)


def recommend_model(gpu: Gpu | None) -> tuple[str, str]:
    """(preset, why) for this graphics card."""
    default = "qwen14b-uncensored"
    if gpu is None or gpu.vram is None:
        return (
            default,
            "The 14B models need about 10 GB of video memory.",
        )
    gigabytes = gpu.vram / GB
    if gigabytes >= 23.5:
        return (
            "qwen3.8-27b",
            "That is room for a 27B model, the strongest that fits.",
        )
    if gigabytes >= 12:
        return (
            default,
            "That is plenty for a 14B model.",
        )
    return "qwen7b", "The 7B model fits it comfortably."


def save_choices(root: Path, choices: dict[str, Any], drop: tuple[str, ...] = ()) -> Path:
    """Remember the setup window's choices in config/launcher.json (other keys are kept)."""
    path = root / "config" / "launcher.json"
    data: dict[str, Any] = {}
    if path.is_file():
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
    for key in drop:
        data.pop(key, None)
    data.update(choices)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    replace_file(part, path)
    return path


VIEWER_PORT = 8765


def chat_url() -> str:
    return f"http://127.0.0.1:{WEB_PORT}"


def viewer_url() -> str:
    return f"http://127.0.0.1:{VIEWER_PORT}"


@dataclass
class Status:
    """What the status window shows."""

    # "off", "loading", "ready", "restarting", "down", "waiting" (LM Studio) or "paused"
    model: str
    model_detail: str = ""
    chat: bool = False
    viewer: bool | None = None  # None: not used
    kill: bool | None = None  # None: unknown (the chat is not up yet)
    seconds: float = 0.0  # since start


def fetch_json(
    url: str,
    method: str = "GET",
    timeout: float = 2.0,
    origin: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """A local JSON answer, or None. (POST sends the page's own Origin, as a browser would.)"""
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(
        url, method=method, headers=headers, data=b"" if method == "POST" else None
    )
    try:
        with _local_open(request, timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return data if isinstance(data, dict) else {}


def health_status(port: int) -> int | None:
    """HTTP status of the model server's /health (200 ready, 503 loading), None: no answer."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health", headers={"User-Agent": USER_AGENT}
    )
    try:
        with _local_open(request, 2) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, http.client.HTTPException):
        return None


LMSTUDIO_HINT = (
    "LM Studio does not answer: start it, load a model and turn on its local server (Developer tab)"
)
# LM Studio 0.4 can ask for an API token ("Require Authentication" in its server settings).
# Its own tools read it from this environment variable; so do the launcher and Starlight.
LMSTUDIO_TOKEN_ENV = "LM_API_TOKEN"
LMSTUDIO_TOKEN_HINT = (
    "LM Studio asks for an API token (Require Authentication is on in its Developer tab): "
    f"set the environment variable {LMSTUDIO_TOKEN_ENV} to it (Windows: setx "
    f"{LMSTUDIO_TOKEN_ENV} <token>, then start Starlight again), or turn the setting off"
)


def _lmstudio_headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get(LMSTUDIO_TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _lmstudio_refuses(url: str) -> bool:
    """LM Studio answers, but refuses: it wants an API token we do not have (or a wrong one)."""
    request = urllib.request.Request(f"{url}/v1/models", headers=_lmstudio_headers())
    try:
        with _local_open(request, 2.0):
            return False
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403)
    except (OSError, http.client.HTTPException):
        return False


@dataclass(frozen=True)
class LMStudioState:
    """What LM Studio's local server says about itself."""

    answers: bool = False
    loaded: str = ""  # the loaded language model; "" when none (or its API does not say)
    context: int = 0  # the context length it is loaded with; 0 when LM Studio does not say
    tools: bool | None = None  # the loaded model is trained for tool use; None: not said
    available: tuple[str, ...] = ()  # the language models it could load (downloaded)
    locked: bool = False  # it answers, but asks for an API token we do not have (HTTP 401)


LANGUAGE_MODELS = ("llm", "vlm")  # model types that chat ("vlm": vision models in the v0 API)


def _number(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _lmstudio_v1(data: dict[str, Any]) -> LMStudioState | None:
    """GET /api/v1/models: {"models": [{"key", "type", "capabilities",
    "loaded_instances": [{"id", "config": {"context_length"}}]}]}."""
    models = data.get("models")
    if not isinstance(models, list):
        return None
    available: list[str] = []
    first: LMStudioState | None = None
    for model in models:
        if not isinstance(model, dict) or model.get("type") not in LANGUAGE_MODELS:
            continue
        key = str(model.get("key") or "")
        available.append(key)
        capabilities = model.get("capabilities")
        tools = capabilities.get("trained_for_tool_use") if isinstance(capabilities, dict) else None
        for instance in model.get("loaded_instances") or []:
            if first is None and isinstance(instance, dict):
                config = instance.get("config")
                context = _number(config.get("context_length")) if isinstance(config, dict) else 0
                kind = tools if isinstance(tools, bool) else None
                first = LMStudioState(True, str(instance.get("id") or key), context, kind)
    if first is None:
        return LMStudioState(True, available=tuple(available))
    return LMStudioState(True, first.loaded, first.context, first.tools, tuple(available))


def _lmstudio_v0(data: dict[str, Any]) -> LMStudioState | None:
    """GET /api/v0/models (older LM Studio): {"data": [{"id", "type", "state",
    "loaded_context_length"}]}."""
    entries = data.get("data")
    if not isinstance(entries, list):
        return None
    available: list[str] = []
    loaded, context = "", 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") not in LANGUAGE_MODELS:
            continue
        name = str(entry.get("id") or "")
        available.append(name)
        if not loaded and entry.get("state") == "loaded":
            loaded, context = name, _number(entry.get("loaded_context_length"))
    return LMStudioState(True, loaded, context, None, tuple(available))


def lmstudio_state(url: str) -> LMStudioState:
    """Ask LM Studio at ``url``: its REST API (v1, else the older v0) says which model is
    loaded; without either, an answer from the OpenAI model list only says that it runs."""
    auth = _lmstudio_headers()
    for path, parse in (("/api/v1/models", _lmstudio_v1), ("/api/v0/models", _lmstudio_v0)):
        data = fetch_json(url + path, headers=auth)
        state = parse(data) if data is not None else None
        if state is not None:
            return state
    if fetch_json(f"{url}/v1/models", headers=auth) is not None:
        return LMStudioState(answers=True)
    if _lmstudio_refuses(url):
        return LMStudioState(answers=True, locked=True)
    return LMStudioState()


def find_lms() -> Path | None:
    """LM Studio's command-line tool, which it installs in ~/.lmstudio/bin."""
    found = shutil.which("lms")
    if found:
        return Path(found)
    home = Path.home() / ".lmstudio" / "bin" / exe("lms")
    return home if home.is_file() else None


def load_in_lmstudio(url: str, model: str, context: int, timeout: float = 600) -> str:
    """Load a model in LM Studio with this context length. "" on success, else the reason."""
    body = json.dumps({"model": model, "context_length": context}).encode("utf-8")
    request = urllib.request.Request(
        f"{url}/api/v1/models/load",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **_lmstudio_headers()},
    )
    try:
        with _local_open(request, timeout):
            return ""
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return LMSTUDIO_TOKEN_HINT
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return f"HTTP {exc.code}: {detail}".strip()
    except (OSError, http.client.HTTPException) as exc:
        return str(exc)


def reload_in_lmstudio(settings: Settings) -> str:
    """Unload the model LM Studio has loaded and load it again with Starlight's context (a
    model that got slow or stuck starts fresh). "" when done, else what went wrong."""
    url = settings.lmstudio_url
    state = lmstudio_state(url)
    if state.locked:
        return LMSTUDIO_TOKEN_HINT
    if not state.answers:
        return LMSTUDIO_HINT
    if not state.loaded:
        key = settings.lmstudio_model
        if not key:
            return "LM Studio has no model loaded: load one there, or choose one in Settings"
        return load_in_lmstudio(url, key, settings.context)
    problem = unload_in_lmstudio(url, state.loaded)
    if problem:
        return problem
    return load_in_lmstudio(url, lmstudio_key(settings, state.loaded), settings.context)


def lmstudio_key(settings: Settings, instance: str) -> str:
    """The model to load again: the instance id is the model's key, or the key and ":2"
    for a second copy of it."""
    return settings.lmstudio_model or instance.rsplit(":", 1)[0]


def unload_in_lmstudio(url: str, instance: str) -> str:
    """Unload one loaded model from LM Studio. "" when done, else what went wrong."""
    body = json.dumps({"instance_id": instance}).encode("utf-8")
    request = urllib.request.Request(
        f"{url}/api/v1/models/unload",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **_lmstudio_headers()},
    )
    try:
        with _local_open(request, 120):
            return ""
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # an older LM Studio without the v1 API
            return "this LM Studio cannot be told to unload: do it in LM Studio"
        return f"LM Studio did not unload it: HTTP {exc.code}"
    except (OSError, http.client.HTTPException) as exc:
        return f"LM Studio did not unload it: {exc}"


class LMStudioPause:
    """Game mode with LM Studio on this PC: while the pause file exists, the model is
    unloaded (its video memory is free for the game), and loaded again when it goes."""

    def __init__(self, settings: Settings, ui: Ui, poll: float = 2.0) -> None:
        self.settings, self.ui, self.poll = settings, ui, poll
        self.pause = pause_file(settings.root)
        self.paused = False
        self.unloaded = ""  # the model that was unloaded, to load it again
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="lmstudio-pause", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _watch(self) -> None:
        while not self._stopped.wait(self.poll):
            self.check()

    def check(self) -> None:
        wanted = self.pause.exists()
        if wanted == self.paused:
            return
        self.paused = wanted
        url = self.settings.lmstudio_url
        if wanted:
            state = lmstudio_state(url)
            if not state.loaded:
                return
            self.ui.say(f"\nGame mode: unloading {state.loaded} from LM Studio.")
            problem = unload_in_lmstudio(url, state.loaded)
            if problem:
                self.ui.say(f"   {problem}")
            else:
                self.unloaded = lmstudio_key(self.settings, state.loaded)
        elif self.unloaded:
            key, self.unloaded = self.unloaded, ""
            self.ui.say(f"\nGame mode ended: loading {key} in LM Studio again ...")
            problem = load_in_lmstudio(url, key, self.settings.context)
            if problem:
                self.ui.say(f"   LM Studio could not load it: {problem}")


def lmstudio_on_this_pc(url: str) -> bool:
    return urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1")


def ensure_lmstudio(settings: Settings, ui: Ui, wait: float = 30) -> LMStudioState:
    """Get LM Studio ready for Starlight, as far as it can be done from here: start its
    server with `lms` when it does not answer, and load the model when none is loaded and
    it is clear which (lmstudio_model, or the only one LM Studio has), with Starlight's
    context length. What is left is said, and Starlight starts anyway."""
    url = settings.lmstudio_url
    state = lmstudio_state(url)
    address = urllib.parse.urlsplit(url)
    lms = find_lms() if lmstudio_on_this_pc(url) else None  # on another PC: started there
    if not state.answers and lms is not None:
        ui.say("   LM Studio's server is off: starting it (lms server start)")
        command = [str(lms), "server", "start"]
        if address.port and address.port != 1234:
            command += ["--port", str(address.port)]
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                command, capture_output=True, timeout=60, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )  # fmt: skip
        deadline = time.monotonic() + wait
        while not state.answers and time.monotonic() < deadline:
            time.sleep(1)
            state = lmstudio_state(url)
    if not state.answers:
        ui.say("   " + LMSTUDIO_HINT)
        return state
    if state.locked:
        ui.say("   " + LMSTUDIO_TOKEN_HINT)
        return state
    if not state.loaded:
        candidates = state.available
        key = settings.lmstudio_model or (candidates[0] if len(candidates) == 1 else "")
        if not key:
            names = ", ".join(candidates[:5]) or "none downloaded"
            ui.say(f"   LM Studio has no model loaded ({names}): load one in LM Studio")
            return state
        ui.say(f"   loading {key} in LM Studio with {settings.context} tokens of context ...")
        problem = load_in_lmstudio(url, key, settings.context)
        if problem:
            ui.say(f"   LM Studio could not load it: {problem}")
        state = lmstudio_state(url)
    if state.loaded:
        context = f" (context {state.context})" if state.context else ""
        ui.say(f"   LM Studio has {state.loaded} loaded{context}")
        if state.tools is False:
            ui.say(
                "   note: this model is not trained for tool use; Starlight's tools may not work"
            )
    return state


SHORT_CONTEXT = 16384  # below this, less room is kept for the answer (reserve_tokens)


def lmstudio_model_config(settings: Settings) -> dict[str, Any]:
    """The model section for LM Studio. Starlight plans its prompts with context_window, so
    it is never more than LM Studio has loaded: a longer prompt would be refused."""
    context = settings.context
    loaded = lmstudio_state(settings.lmstudio_url).context
    if loaded:
        context = min(context, loaded)
    model: dict[str, Any] = {
        "provider": "lmstudio",
        "options": {
            "base_url": f"{settings.lmstudio_url}/v1",
            "model": settings.lmstudio_model or "auto",
        },
        "context_window": context,
    }
    if context < SHORT_CONTEXT:  # the default 4096 would leave too little for the prompt
        model["reserve_tokens"] = max(256, context // 4)
    return model


class Services:
    """The running Starlight: the model server (watched, restarted if it dies), the web
    chat and the statistics viewer. ``start`` returns at once; ``poll`` says how it is
    doing, so a window can show it while the model loads."""

    def __init__(
        self, settings: Settings, ui: Ui, installed: Installed, group: ProcessGroup | None = None
    ) -> None:
        self.settings, self.ui, self.installed = settings, ui, installed
        self.group = group or ProcessGroup()
        self.logs = settings.root / "logs"
        self.watchdog: ModelServerWatchdog | None = None
        self.lmstudio_pause: LMStudioPause | None = None
        self.started = time.monotonic()

    def start(self) -> None:
        settings, uv = self.settings, self.installed.uv
        self.started = time.monotonic()
        if settings.runs_llama and not port_in_use(settings.port):  # else: a server runs already
            serve = starlight(uv, "model", "serve")
            log = self.logs / "llama-server.log"
            process = self.group.start(serve, log, settings.root)
            self.watchdog = ModelServerWatchdog(
                self.group, serve, process, log, settings.root, self.ui,
                pause=pause_file(settings.root),
            )  # fmt: skip
            self.watchdog.start()
        if settings.uses_lmstudio and lmstudio_on_this_pc(settings.lmstudio_url):
            self.lmstudio_pause = LMStudioPause(settings, self.ui)
            self.lmstudio_pause.start()
        if settings.viewer and not port_in_use(VIEWER_PORT):
            self.group.start(starlight(uv, "viewer"), self.logs / "viewer.log", settings.root)
        if not port_in_use(WEB_PORT):
            self.group.start(starlight(uv, "web"), self.logs / "web.log", settings.root)

    def model_log_problem(self) -> str:
        """The last line of the model server's log that looks like a real problem."""
        try:
            text = (self.logs / "llama-server.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        words = ("error", "failed", "out of memory", "cannot", "unable", "abort")
        lines = [
            ln.strip() for ln in text[-6000:].splitlines() if any(w in ln.lower() for w in words)
        ]
        return lines[-1][:200] if lines else ""

    def poll(self) -> Status:
        settings, dog = self.settings, self.watchdog
        elapsed = time.monotonic() - self.started
        pause = read_pause(settings.root) if settings.use_model else None
        if not settings.use_model:
            model, detail = "off", "the built-in test model"
        elif pause is not None:
            model, detail = "paused", f"game mode: {pause.get('reason') or 'paused'}"
        elif settings.uses_lmstudio:
            lms = lmstudio_state(settings.lmstudio_url)
            if lms.loaded:
                note = "; not trained for tool use" if lms.tools is False else ""
                model, detail = "ready", f"{lms.loaded} (LM Studio{note})"
            elif lms.locked:
                model, detail = "waiting", LMSTUDIO_TOKEN_HINT
            elif lms.answers:
                model, detail = "waiting", "LM Studio answers but has no model loaded: load one"
            else:
                model, detail = "waiting", LMSTUDIO_HINT
        else:
            code = health_status(settings.port)
            if code == 200:
                model, detail = "ready", ""
            elif dog is not None and dog.gave_up:
                model, detail = "down", self.model_log_problem() or "it stopped and did not restart"
            elif dog is not None and (dog.restarting or dog.process.poll() is not None):
                model, detail = "restarting", ""
            else:
                model, detail = "loading", ""
        chat = port_in_use(WEB_PORT)
        status = fetch_json(f"{chat_url()}/api/status") if chat else None
        kill = None
        if status is not None:
            engaged = (status.get("kill") or {}).get("engaged")
            kill = bool(engaged) if engaged is not None else None
        return Status(
            model=model,
            model_detail=detail,
            chat=chat,
            viewer=port_in_use(VIEWER_PORT) if settings.viewer else None,
            kill=kill,
            seconds=elapsed,
        )

    def restart_model(self) -> bool:
        """Restart the model server. False if this launcher did not start it."""
        if self.watchdog is None:
            return False
        self.watchdog.restart_now()
        self.started = time.monotonic()
        return True

    def resume_model(self) -> bool:
        """End a game-mode pause: the watchers bring the model back. True if paused."""
        resumed = end_pause(self.settings.root)
        if resumed:
            self.started = time.monotonic()
        return resumed

    def set_kill_switch(self, engaged: bool) -> bool:
        """Engage or release the kill switch through the chat backend. True on success."""
        path = "/api/kill" if engaged else "/api/resume"
        answer = fetch_json(chat_url() + path, method="POST", origin=chat_url())
        return answer is not None

    def stop(self) -> None:
        if self.watchdog is not None:
            self.watchdog.stop()
        if self.lmstudio_pause is not None:
            self.lmstudio_pause.stop()
        self.group.stop()


def start(settings: Settings, ui: Ui, installed: Installed, ask: str | None) -> int:
    uv = installed.uv
    group = ProcessGroup()
    logs = settings.root / "logs"
    watchdog: ModelServerWatchdog | None = None
    lms_pause: LMStudioPause | None = None
    try:
        if settings.uses_lmstudio and ask is None and lmstudio_on_this_pc(settings.lmstudio_url):
            lms_pause = LMStudioPause(settings, ui)
            lms_pause.start()
        if settings.runs_llama:
            ui.step("Starting the model server on the GPU")
            log = logs / "llama-server.log"
            if port_in_use(settings.port):
                ui.say(f"   something already listens on port {settings.port}; using it")
                server_process = None
            else:
                serve = starlight(uv, "model", "serve")
                server_process = group.start(serve, log, settings.root)
            wait_for_model(settings.port, server_process, log, ui)
            if server_process is not None and ask is None:
                watchdog = ModelServerWatchdog(
                    group, serve, server_process, log, settings.root, ui,
                    pause=pause_file(settings.root),
                )  # fmt: skip
                watchdog.start()
        if settings.viewer and ask is None:
            if not port_in_use(VIEWER_PORT):
                group.start(starlight(uv, "viewer"), logs / "viewer.log", settings.root)
            ui.say(f"\nStatistics viewer: {viewer_url()}")
        if ask is not None:
            result = subprocess.run(starlight(uv, "ask", "-y", ask), cwd=settings.root, check=False)
            return result.returncode
        if settings.both:
            url = chat_url()
            ui.say(f"\nOpening the chat in your browser: {url}")
            ui.say("A terminal chat opens here too - both talk to the same Starlight.")
            ui.say("Type /quit to leave the terminal; close this window to stop everything.\n")
            group.start(starlight(uv, "web"), logs / "web.log", settings.root)
            wait_for_web(WEB_PORT, ui)
            threading.Timer(1.0, _open_browser, args=(url,)).start()
            return subprocess.run(
                starlight(uv, "connect"), cwd=settings.root, check=False
            ).returncode
        if settings.web:
            url = chat_url()
            ui.say(f"\nOpening the chat in your browser: {url}")
            ui.say("Close this window (or press Ctrl+C) to stop everything.\n")
            threading.Timer(2.5, _open_browser, args=(url,)).start()
            return subprocess.run(starlight(uv, "web"), cwd=settings.root, check=False).returncode
        ui.say("Kill switch: Ctrl+Alt+J, a screen corner, or /kill. Leave with /quit.\n")
        return subprocess.run(starlight(uv, "run"), cwd=settings.root, check=False).returncode
    finally:
        if watchdog is not None:
            watchdog.stop()  # first: stopping the server on purpose is not a crash
        if lms_pause is not None:
            lms_pause.stop()
        group.stop()


def _open_browser(url: str) -> None:
    with contextlib.suppress(Exception):
        import webbrowser

        webbrowser.open(url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Starlight", description="Set up and start Starlight, the local AI assistant."
    )
    parser.add_argument("--version", action="version", version=f"Starlight launcher {VERSION}")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask; use defaults")
    parser.add_argument("--setup-only", action="store_true", help="install, then exit")
    parser.add_argument("--ask", metavar="TEXT", help="one request, print the answer, exit")
    parser.add_argument("--update", action="store_true", help="download uv and llama.cpp again")
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="go back to the uv and llama.cpp from before the last --update",
    )
    parser.add_argument(
        "--lmstudio",
        action="store_true",
        help="use the model loaded in LM Studio (its local server) instead of downloading one",
    )
    parser.add_argument(
        "--lmstudio-model",
        metavar="NAME",
        help="with LM Studio: the model Starlight loads and uses (its name in LM Studio), "
        "instead of whichever is loaded",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="use Starlight's built-in test model; download no model",
    )
    parser.add_argument("--no-viewer", action="store_true", help="do not start the viewer")
    parser.add_argument(
        "--no-think",
        action="store_true",
        help="models that think before they answer (Qwen3, gpt-oss) answer at once: "
        "quicker, a little less careful with hard questions",
    )
    parser.add_argument(
        "--terminal", action="store_true", help="chat in this window instead of the browser"
    )
    parser.add_argument(
        "--both", action="store_true", help="open the browser chat and a terminal chat together"
    )
    parser.add_argument("--tesseract", action="store_true", help="install Tesseract OCR")
    parser.add_argument(
        "--voice", action="store_true", help="install speech input and output (about 0.6 GB)"
    )
    parser.add_argument(
        "--model",
        choices=list(MODEL_PRESETS),
        help="a ready-made model choice (default qwen14b-uncensored)",
    )
    parser.add_argument("--model-url", help="GGUF model to use (a direct download link)")
    # Accepted so older shortcuts keep working; the draft model was removed.
    parser.add_argument("--no-draft", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--draft-url", help=argparse.SUPPRESS)
    parser.add_argument("--context", type=int, help="context window in tokens")
    parser.add_argument("--variant", choices=["vulkan", "cpu"], help="llama.cpp build")
    parser.add_argument("--llama-release", help='llama.cpp release tag (default "latest")')
    parser.add_argument("--port", type=int, help="model server port (default 8080)")
    parser.add_argument("--root", help="the Starlight folder (default: where Starlight.exe is)")
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="no window: set up and start in this text window (the default with --yes, --ask, "
        "--terminal, --both, --setup-only or --update)",
    )
    parser.add_argument(
        "--gui", action="store_true", help="always show the window, even with those options"
    )
    parser.add_argument("--gui-selftest", action="store_true", help=argparse.SUPPRESS)
    return parser


def gui_wanted(args: argparse.Namespace) -> bool:
    """The window is the default when Starlight.exe is started plainly (double-click); the
    text version stays for scripts and for the options that need a terminal."""
    if args.no_gui:
        return False
    if args.gui or args.gui_selftest:
        return True
    return not (
        args.yes
        or args.ask
        or args.terminal
        or args.both
        or args.setup_only
        or args.update
        or args.rollback
    )


def relaunch_without_console() -> bool:
    """Starlight.exe is a console program, so a double-click opens a black window. For the
    window version, start a copy without one and let this one end (its console closes)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    if os.environ.get("STARLIGHT_NO_RELAUNCH"):
        return False
    env = {**os.environ, "STARLIGHT_NO_RELAUNCH": "1", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [sys.executable, *sys.argv[1:]],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError:
        return False
    return True


def run_window(args: argparse.Namespace) -> int | None:
    """Show the window. None: it cannot be shown here (no tkinter, no screen), so the
    caller falls back to the text version."""
    if sys.platform != "win32" and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return None
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import tkinter  # noqa: F401

        import launcher_gui
    except ImportError:
        return None
    if not args.gui_selftest and relaunch_without_console():
        return 0
    return launcher_gui.run(sys.modules[__name__], args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if gui_wanted(args):
        window_code = run_window(args)
        if window_code is not None:
            return window_code
    ui = make_ui(args.yes)
    code = 1
    try:
        settings = load_settings(args)
        ui.say(f"Starlight launcher {VERSION} - {settings.root}")
        if args.rollback:
            code = rollback(settings, ui)
            return code
        installed = setup(settings, ui)
        if args.setup_only:
            ui.say("\nSetup complete. Start Starlight.exe again to begin.")
            code = 0
        else:
            code = start(settings, ui, installed, args.ask)
        return code
    except LauncherError as exc:
        ui.say(f"\nStarlight could not start: {exc}")
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        ui.say(f"\nStarlight could not start: network problem ({exc}). Check the internet")
        ui.say("connection and start again; finished downloads are kept.")
        return 1
    except KeyboardInterrupt:
        ui.say("\nStopped.")
        return 130
    finally:
        # Double-clicked Starlight.exe: keep the window open on errors so they can be read.
        if getattr(sys, "frozen", False) and code != 0 and not args.yes and sys.stdin.isatty():
            with contextlib.suppress(EOFError):
                input("\nPress Enter to close this window.")


if __name__ == "__main__":
    raise SystemExit(main())
