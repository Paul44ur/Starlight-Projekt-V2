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
import struct
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
MB = 1024**2
# The defaults of config web.port and viewer.port (config/starlight.yaml). A port changed in
# config/starlight.local.yaml is not seen here.
WEB_PORT = 8700  # the local web chat
VIEWER_PORT = 8765  # the statistics viewer

# Rough download sizes: listed before the first download and added up for the free-space
# check (the window's setup page adds up the same ones).
FIRST_RUN_SIZE = 1.6 * GB  # uv, Python 3.12 and the packages ~1.3 GB + Chromium ~0.3 GB
LLAMA_SIZE = 0.2 * GB  # the llama.cpp model server
VOICE_SIZE = 0.6 * GB  # whisper.cpp, Piper, the speech model and two voices (--voice)
SPARE_SPACE = GB  # kept free on top of the downloads


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
# llamacpp: Starlight downloads and runs llama.cpp itself; lmstudio: LM Studio's server;
# ollama: Ollama's; remote: any OpenAI-compatible server, e.g. on another PC in your network.
MODEL_SERVERS = ("llamacpp", "lmstudio", "ollama", "remote")
OLLAMA_URL = "http://127.0.0.1:11434"
# Answer styles: how freely the model picks its words. Balanced is Starlight's default.
STYLES: dict[str, dict[str, Any]] = {
    "precise": {"temperature": 0.1, "top_p": 0.8, "top_k": 20},
    "balanced": {"temperature": 0.3},
    "creative": {"temperature": 0.8, "top_p": 0.95, "top_k": 40},
}
# Context sizes offered, and the one used when it cannot be worked out ("auto" = 0).
CONTEXT_STEPS = (8192, 16384, 32768, 49152, 65536, 98304, 131072)
DEFAULT_CONTEXT = 32768


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
    ollama_url: str = OLLAMA_URL
    ollama_model: str = ""  # an Ollama model name, e.g. "qwen3:14b"
    remote_url: str = ""  # an OpenAI-compatible server, e.g. http://192.168.1.20:8080/v1
    remote_model: str = ""  # its model name, if it needs one
    style: str = "balanced"  # precise, balanced or creative (STYLES)
    models_dir: str = ""  # where models are kept; "" = the models folder in Starlight's
    viewer: bool = True
    web: bool = True  # open the browser chat; False: the terminal chat
    # Windows: the chat in Starlight's own window (`starlight app`), not in a browser tab
    app_window: bool = True
    # Windows: closing the launcher window leaves Starlight running next to the clock (the
    # tray icon, launcher_tray.py); tray_hint_shown: the first-time balloon was shown.
    tray: bool = True
    tray_hint_shown: bool = False
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
    links: bool = True  # starlight:// links open your notes (Windows: registered for you)
    first_page: str = ""  # a page to open instead of the chat ("/notes?path=..."): --open
    github_token: str | None = field(default=None, repr=False)

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def models(self) -> Path:
        return Path(self.models_dir).expanduser() if self.models_dir else self.root / "models"

    @property
    def model_file(self) -> Path:
        return self.models / _file_name(self.model_url)

    @property
    def uses_lmstudio(self) -> bool:
        return self.use_model and self.model_server == "lmstudio"

    @property
    def uses_ollama(self) -> bool:
        return self.use_model and self.model_server == "ollama"

    @property
    def uses_remote(self) -> bool:
        return self.use_model and self.model_server == "remote"

    @property
    def runs_llama(self) -> bool:
        """Starlight downloads llama.cpp and the model, and runs the model server itself."""
        return self.use_model and self.model_server == "llamacpp"


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
                    "app_window",
                    "tray",
                    "tray_hint_shown",
                    "both",
                    "tesseract",
                    "voice",
                    "think",
                    "whisper_url",
                    "whisper_model_url",
                    "piper_url",
                    "ollama_url",
                    "ollama_model",
                    "remote_url",
                    "remote_model",
                    "style",
                    "models_dir",
                    "links",
                ):
                    setattr(settings, key, value)
        except (OSError, ValueError) as exc:
            raise LauncherError(f"{saved} is not valid JSON: {exc}") from exc
    settings.first_page = str(getattr(args, "page", "") or "")
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
    if getattr(args, "browser", False):
        settings.app_window = False
    settings.use_model = not args.no_model
    if getattr(args, "lmstudio", False):
        settings.model_server = "lmstudio"
    if getattr(args, "lmstudio_model", None):
        settings.model_server = "lmstudio"
        settings.lmstudio_model = args.lmstudio_model.strip()
    if getattr(args, "ollama_model", None):
        settings.model_server = "ollama"
        settings.ollama_model = args.ollama_model.strip()
    if getattr(args, "remote_url", None):
        settings.model_server = "remote"
        settings.remote_url = args.remote_url.strip()
    if getattr(args, "style", None):
        settings.style = args.style
    if settings.model_server not in MODEL_SERVERS:
        raise LauncherError(
            f"model_server in config/launcher.json is {settings.model_server!r}; "
            f"it can be {', '.join(MODEL_SERVERS)}"
        )
    if settings.style not in STYLES:
        raise LauncherError(
            f"style in config/launcher.json is {settings.style!r}; it can be " + ", ".join(STYLES)
        )
    if settings.uses_remote and not str(settings.remote_url).startswith(("http://", "https://")):
        raise LauncherError(
            "remote_url in config/launcher.json must be the server's address, "
            "e.g. http://192.168.1.20:8080/v1"
        )
    settings.lmstudio_url = str(settings.lmstudio_url).rstrip("/").removesuffix("/v1")
    settings.ollama_url = str(settings.ollama_url).rstrip("/")
    settings.remote_url = str(settings.remote_url).rstrip("/")
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
        """One line of output. A text starting with "\\r" replaces the line that waiting()
        or progress() left open: ConsoleUi clears that line, the window drops the "\\r",
        and this plain version only moves the cursor back, so such a text is padded to
        cover the old line. A text starting with "\\n" keeps that line and goes below it."""
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

        std_output_handle = -11  # STD_OUTPUT_HANDLE
        enable_virtual_terminal_processing = 0x0004  # the console understands \x1b[ codes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.GetStdHandle(std_output_handle)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(
            kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing)
        )
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
        line = text.lstrip("\r")
        print(f"{self.CLEAR}{self.DIM}{line}{self.RESET}", end="", flush=True)


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
    """Do not follow redirects: urllib then raises HTTPError for the 3xx answer itself,
    whose headers (Hugging Face's X-Linked-Size and X-Linked-Etag) probe() reads."""

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


# Set while you paused the downloads (the launcher window's Pause button): the download in
# progress stops at the next megabyte and goes on from there when this is cleared.
DOWNLOADS_PAUSED = threading.Event()


def download(
    url: str,
    dest: Path,
    ui: Ui,
    sha256: str | None = None,
    token: str | None = None,
    attempts: int = 4,
) -> Path:
    """Download to ``dest``, resuming a previous ``.part`` file, and check the SHA-256.
    A pause (DOWNLOADS_PAUSED) closes the connection and resumes it later; it does not
    count as a failed attempt."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    attempt = 1
    while True:
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        paused = False
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
                        if DOWNLOADS_PAUSED.is_set():
                            paused = True
                            break
                if not paused:
                    ui.progress(done - have, (total - have) if total else None, started)
                    ui.say()
                    if total is not None and done < total:  # closed early, without an error
                        raise ConnectionError(
                            f"the connection closed after {done} of {total} bytes"
                        )
            if not paused:
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
        if paused:
            ui.say(f"   paused at {part.stat().st_size / GB:.2f} GB; Resume continues it")
            while DOWNLOADS_PAUSED.is_set():
                time.sleep(0.25)
            ui.say("   resuming the download ...")
            continue
        ui.say(f"   connection problem, resuming (try {attempt + 1} of {attempts}) ...")
        time.sleep(2 * attempt)
        attempt += 1
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
# Pairs of (folder name inside the copy, folder inside Starlight); the same names today.
SNAPSHOT_PARTS = (("config", "config"), ("data", "data"), ("memory", "memory"))
SNAPSHOT_SKIP = ("*.sqlite*", "*.db", "*.db-*", "*.lock", "*.tmp", "*.part", "web_cache*")


def folder_size(folder: Path) -> int:
    total = 0
    for item in folder.rglob("*"):
        with contextlib.suppress(OSError):
            if item.is_file():
                total += item.stat().st_size
    return total


def release_notes(root: Path) -> tuple[str, str] | None:
    """(the version, its notes) from docs/release-notes.md, which every release carries."""
    try:
        text = (root / "docs" / "release-notes.md").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^##\s+(.+?)\s*$", text, re.MULTILINE)
    if match is None:
        return None
    notes = text[match.end() :].strip()
    return match.group(1).strip(), notes


def whats_new(root: Path) -> tuple[str, str] | None:
    """The release notes, once, the first time a new version starts. A first install only
    takes note of its version (there is nothing "new" to tell yet)."""
    found = release_notes(root)
    if found is None:
        return None
    version, notes = found
    seen_file = root / "data" / "launcher" / "seen-version.txt"
    try:
        seen = seen_file.read_text(encoding="utf-8").strip()
    except OSError:
        seen = ""
    if seen == version:
        return None
    with contextlib.suppress(OSError):
        seen_file.parent.mkdir(parents=True, exist_ok=True)
        seen_file.write_text(version + "\n", encoding="utf-8")
    return (version, notes) if seen else None


def short_notes(notes: str, lines: int = 18) -> str:
    """The notes as plain text for a window or a console: no Markdown marks, not too long."""
    out: list[str] = []
    for line in notes.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"^#+\s*", "", line).replace("`", "")
        if line.strip() or (out and out[-1]):
            out.append(line.rstrip())
    if len(out) > lines:
        out = [*out[:lines], "…"]
    return "\n".join(out).strip()


def tell_whats_new(root: Path, ui: Ui) -> None:
    found = whats_new(root)
    if found is None:
        return
    version, notes = found
    show = getattr(ui, "news", None)
    if callable(show):  # the window shows it in a box of its own
        show(f"What's new in {version}", short_notes(notes))
        return
    ui.step(f"What's new in {version}")
    for line in short_notes(notes).splitlines():
        ui.say("   " + line)


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
    copies = sorted(root.glob("backups/update-*"))  # oldest first: the names hold the time
    for old in copies[:-BACKUPS_KEEP]:  # all but the newest BACKUPS_KEEP
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
    moved = set_aside(settings, folder, "uv") if settings.update else False
    try:
        extract_member(archive, exe("uv"), folder)
        (folder / "source.txt").write_text(f"{UV_DOWNLOAD}/{asset}\n", encoding="utf-8")
    except BaseException:  # also Ctrl+C: never leave tools/uv half replaced
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
    """The tag in a downloaded nightly-tag.txt, if it looks like a tag name (letters, digits,
    "." "_" "-"): the text goes into a URL next."""
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
            # No programs in this release: follow its nightly-tag.txt to the build release.
            # Without a usable pointer, the "no matching download" error above stands (a
            # LauncherError is not caught below, so the release page is not tried then).
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
        # The API is blocked or rate-limited (an HTTPError is a URLError), or its answer is
        # not JSON: work it out from the release page below.
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
        # .../releases/latest redirects to .../releases/tag/<tag>: the tag ends the final URL.
        with urllib.request.urlopen(_request(f"{LLAMA_RELEASES}/latest"), timeout=30) as r:
            tag = r.geturl().rstrip("/").split("/")[-1]
    name = f"llama-{tag}-{llama_asset_names(settings.variant)[0]}"
    return f"{LLAMA_RELEASES}/download/{tag}/{name}", None


def llama_binary(settings: Settings) -> Path:
    """Where ensure_llama puts llama-server for the chosen build."""
    return settings.tools / "llama.cpp" / settings.variant / exe("llama-server")


def ensure_llama(settings: Settings, ui: Ui) -> Path:
    binary = llama_binary(settings)
    target = binary.parent
    if binary.is_file() and not settings.update:
        return binary
    ui.step(f"Downloading the llama.cpp model server ({settings.variant} build)")
    url, sha = resolve_llama(settings)
    ui.say(f"   {url}")
    archive = download(url, settings.tools / url.split("/")[-1], ui, sha256=sha)
    name = f"llama.cpp-{settings.variant}"
    moved = set_aside(settings, target, name) if settings.update else False
    try:
        extract_member(archive, exe("llama-server"), target)
        (target / "source.txt").write_text(url + "\n", encoding="utf-8")
    except BaseException:  # also Ctrl+C: never leave the server folder half replaced
        if moved:
            put_back(settings, target, name)
        raise
    archive.unlink()
    return binary


def voice_downloads() -> bool:
    """Ready-made speech programs are published for Windows only."""
    return os.name == "nt"


def voice_files(settings: Settings) -> list[Path]:
    """The files ensure_voice installs: both programs, the speech model and the voices."""
    whisper = settings.tools / "whisper"
    piper = settings.tools / "piper"
    files = [
        whisper / exe("whisper-server"),
        whisper / "models" / _file_name(settings.whisper_model_url),
        piper / exe("piper"),
    ]
    for voice in VOICES:
        name = voice.split("/")[-1]
        files += [piper / "voices" / f"{name}{suffix}" for suffix in (".onnx", ".onnx.json")]
    return files


def voice_will_download(settings: Settings) -> bool:
    """Whether ensure_voice has something to download (for the size estimate)."""
    if not settings.voice or not voice_downloads():
        return False
    return not all(path.is_file() for path in voice_files(settings))


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


# -- how much context fits ---------------------------------------------------------------

GGUF_TYPES = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d",
}
GGUF_STRING, GGUF_ARRAY = 8, 9


def gguf_info(path: Path) -> dict[str, Any]:
    """The few facts of a GGUF model's header that decide how much memory its context
    needs: architecture, layers, attention heads (and key-value heads), the size of a head,
    and the context it was trained for. {} when the file is not a GGUF model."""
    wanted = ("block_count", "attention.head_count", "attention.head_count_kv",
              "embedding_length", "attention.key_length", "context_length")  # fmt: skip
    found: dict[str, Any] = {}

    def read(fmt: str) -> Any:
        size = struct.calcsize("<" + fmt)
        data = handle.read(size)
        if len(data) < size:
            raise EOFError
        return struct.unpack("<" + fmt, data)[0]

    def text() -> str:
        return handle.read(read("Q")).decode("utf-8", "replace")

    def value(kind: int) -> Any:
        if kind == GGUF_STRING:
            return text()
        if kind == GGUF_ARRAY:
            item, count = read("I"), read("Q")
            if item in GGUF_TYPES:
                if count <= 4096:  # a short list of numbers (a head count per layer): kept
                    return [read(GGUF_TYPES[item]) for _ in range(count)]
                handle.seek(struct.calcsize("<" + GGUF_TYPES[item]) * count, 1)
                return []
            for _ in range(count):  # strings (the tokenizer's words): read past
                value(item)
            return []
        return read(GGUF_TYPES[kind])

    try:
        with path.open("rb") as handle:
            if handle.read(4) != b"GGUF":
                return {}
            version = read("I")
            read("Q" if version >= 2 else "I")  # tensors
            entries = read("Q" if version >= 2 else "I")
            arch = ""
            for _ in range(min(entries, 100000)):
                key, kind = text(), read("I")
                if kind not in GGUF_TYPES and kind not in (GGUF_STRING, GGUF_ARRAY):
                    return {}
                got = value(kind)
                if key == "general.architecture":
                    arch = str(got)
                elif arch and key.startswith(arch + ".") and key[len(arch) + 1 :] in wanted:
                    found[key[len(arch) + 1 :]] = got
                if arch and all(k in found for k in wanted[:4]) and "context_length" in found:
                    break
    except (OSError, EOFError, struct.error, KeyError, ValueError):
        return found if found.get("block_count") else {}
    if arch:
        found["architecture"] = arch
    return found


def kv_bytes_per_token(info: dict[str, Any], cache: str = "q8_0") -> int:
    """The memory one token of context takes (keys and values in every layer); 0 if the
    header does not say enough."""

    def number(value: Any) -> int:
        if isinstance(value, list):
            return max((int(v) for v in value), default=0)
        return int(value or 0)

    layers, heads = number(info.get("block_count")), number(info.get("attention.head_count"))
    kv_heads = number(info.get("attention.head_count_kv")) or heads
    head_size = number(info.get("attention.key_length")) or (
        number(info.get("embedding_length")) // heads if heads else 0
    )
    per_value = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}.get(cache, 2.0)
    return int(2 * layers * kv_heads * head_size * per_value)


def context_fit(model: Path, vram: int, cache: str = "q8_0") -> int:
    """The largest of CONTEXT_STEPS that fits in ``vram`` bytes next to the model (and what
    llama.cpp needs on top), and that the model was trained for; 0 if it cannot tell."""
    info = gguf_info(model)
    per_token = kv_bytes_per_token(info, cache)
    if not per_token or not vram:
        return 0
    room = vram * 0.95 - model.stat().st_size - 0.8 * GB  # (the compute buffers, the desktop)
    trained = int(info.get("context_length") or CONTEXT_STEPS[-1])
    fitting = [c for c in CONTEXT_STEPS if c <= trained and c * per_token * 1.1 <= room]
    return fitting[-1] if fitting else min(CONTEXT_STEPS[0], trained)


def choose_context(settings: Settings, model: Path) -> int:
    """The context to run with: what you chose, or (context 0, "auto") what fits."""
    if settings.context:
        return settings.context
    gpu = detect_gpu() if settings.variant != "cpu" else None
    fit = context_fit(model, gpu.vram) if gpu is not None and gpu.vram else 0
    return fit or DEFAULT_CONTEXT


def write_launcher_config(settings: Settings, server: Path | None, model: Path | None) -> Path:
    """config/starlight.launcher.yaml: the model server for Starlight. JSON is valid YAML,
    and your own config/starlight.local.yaml still overrides it."""
    data: dict[str, Any]
    # Balanced is the model servers' own default: only the other styles are written down.
    style = dict(STYLES[settings.style]) if settings.style != "balanced" else {}
    temperature = {"temperature": style.pop("temperature")} if style else {}
    extra = {**style, **({} if settings.think else NO_THINKING)}
    if settings.uses_lmstudio:
        data = {"model": lmstudio_model_config(settings)}
        data["model"]["options"].update({**temperature, **({"extra": extra} if extra else {})})
    elif settings.uses_ollama:
        data = {
            "model": {
                "provider": "ollama",
                "options": {
                    "base_url": settings.ollama_url,
                    "model": settings.ollama_model or "qwen3:14b",
                    **temperature,
                    "num_ctx": settings.context or DEFAULT_CONTEXT,
                    **({"options": style} if style else {}),
                },
                "context_window": settings.context or DEFAULT_CONTEXT,
            }
        }
    elif settings.uses_remote:
        data = {
            "model": {
                "provider": "openai_compat",
                "options": {
                    "base_url": settings.remote_url,
                    "model": settings.remote_model or "local",
                    **temperature,
                    **({"extra": extra} if extra else {}),
                },
                "context_window": settings.context or DEFAULT_CONTEXT,
            }
        }
    elif settings.use_model and server is not None and model is not None:
        data = {
            "model": {
                "provider": "llamacpp",
                "options": {
                    "base_url": f"http://127.0.0.1:{settings.port}/v1",
                    "model": model.stem,
                    **temperature,
                    **({"extra": extra} if extra else {}),
                },
                "context_window": choose_context(settings, model),
                "server": {
                    "binary": str(server),
                    "model_path": str(model),
                    "port": settings.port,
                    # every layer on the GPU (also the core's default, starlight/core/config.py)
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
            # Its own process group (group id = its pid), so terminate() can end the whole
            # tree with os.killpg(pid). Windows uses taskkill /T and the job object instead.
            start_new_session=sys.platform != "win32",
        )
        self.processes.append(process)
        return process

    def terminate(self, process: subprocess.Popen[bytes]) -> None:
        """End one process and everything it started."""
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(  # /T: with everything it started, /F: forced
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


# Starlight's own window writes this file when an answer is ready while it is in the
# background (starlight/app_window.py: the same path and the same few lines). The launcher
# window's tray icon shows it as a notification. Anything in it may come from a web page, so
# it is only ever shown as plain, short text.
ATTENTION_FILE = Path("data") / "app-window" / "attention.json"
ATTENTION_LETTERS = 200


def plain_text(text: object, limit: int = ATTENTION_LETTERS) -> str:
    """Text without control characters and with white space squeezed, cut to ``limit``."""
    plain = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return plain[:limit].rstrip()


def write_attention(root: Path, text: str) -> int:
    """Say that something is ready (atomically: a reader never sees half a file). Returns
    the id of this message."""
    path = root / ATTENTION_FILE
    message_id = time.time_ns()
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps({"id": message_id, "text": plain_text(text)}), encoding="utf-8")
    replace_file(part, path)
    return message_id


def read_attention(root: Path, seen: int | None = None) -> tuple[int, str] | None:
    """(id, text) of the message in the file, unless it is the one with the id ``seen``
    already (or there is none, or the file is not a message)."""
    try:
        data = json.loads((root / ATTENTION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    message_id = data.get("id")
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id == seen:
        return None
    return message_id, plain_text(data.get("text", ""))


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
        # Event.wait() returns True once stop() or restart_now() has set the event.
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
        """The server ended by itself: start it again after a pause that grows with each
        restart. True: it runs again, keep watching. False: stop watching (``limit``
        restarts within ``window``, it could not be started, or stop() during the pause)."""
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
            if self._stopped.wait(self.backoff * len(self.restarts)):  # stop() during the pause
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
    # It only allows that: a program leaves only when it is started with
    # CREATE_BREAKAWAY_FROM_JOB (modules/desktop start_app does); others end with the job.
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
        try:
            with _local_open(url, 5) as response:
                ready = response.status == 200
        except (OSError, http.client.HTTPException):
            # Not listening yet, HTTPError 503 while loading (URLError, HTTPError and
            # TimeoutError are all OSErrors), or a garbled answer while it starts.
            ready = False
        if ready:
            # ljust: cover the longer "loading the model ..." line (see Ui.say)
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
        # Only what will really be downloaded (see ensure_llama and ensure_voice).
        llama = settings.runs_llama and (settings.update or not llama_binary(settings).is_file())
        voice = voice_will_download(settings)
        need = (
            (FIRST_RUN_SIZE if first_run else 0)
            + (LLAMA_SIZE if llama else 0)
            + (VOICE_SIZE if voice else 0)
            + model_size
        )
        free = shutil.disk_usage(settings.root).free
        ui.say("Starlight will download and set up, inside " + str(settings.root) + ":")
        if first_run:
            ui.say("   - uv, Python 3.12 and Starlight's packages      ~1.3 GB")
            ui.say("   - the Chromium browser for web pages and logins ~0.3 GB")
        if llama:
            ui.say(f"   - the llama.cpp model server ({settings.variant})         ~0.2 GB")
        if voice:
            ui.say("   - speech input and output (whisper.cpp, Piper)  ~0.6 GB")
        if remote is not None:
            ui.say(f"   - the model {settings.model_file.name:<34} {model_size / GB:.1f} GB")
        ui.say(f"   Needed: about {need / GB:.1f} GB. Free on this drive: {free / GB:.1f} GB.")
        if free < need + SPARE_SPACE:
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
    if settings.uses_ollama or settings.uses_remote:
        ui.step("Checking the model server")
        state, detail = other_server_state(settings)
        ui.say(f"   {detail}" if state == "ready" else f"   {detail} (Starlight starts anyway)")
    write_launcher_config(settings, installed.server, installed.model)
    ensure_links(settings, ui)
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


TESSERACT_EXE = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")  # where its installer puts it


def tesseract_found() -> bool:
    """Tesseract is on the PATH or in its usual folder (the window asks the same)."""
    return bool(shutil.which("tesseract")) or TESSERACT_EXE.exists()


def maybe_tesseract(settings: Settings, ui: Ui) -> None:
    """Screen reading (OCR) is optional and needs an installer with administrator rights,
    so it is only offered, never installed silently."""
    if os.name != "nt" or tesseract_found():
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


# The device class "Display adapters": one numbered subkey (0000, 0001 ...) per adapter
# driver, next to a "Properties" key.
DISPLAY_ADAPTERS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def detect_gpu() -> Gpu | None:
    """Name and video memory of the graphics card (Windows; from the driver's registry
    entries, which - unlike WMI's AdapterRAM - are right for cards over 4 GB)."""
    if sys.platform != "win32":
        return None
    import winreg

    entries: list[tuple[str, int | None]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_ADAPTERS) as root:
            for index in range(32):  # a cap; EnumKey raises OSError after the last subkey
                try:
                    sub_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                if not sub_name.isdigit():  # "Properties"
                    continue
                with contextlib.suppress(OSError), winreg.OpenKey(root, sub_name) as key:
                    name = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                    vram: int | None = None
                    # qwMemorySize (a QWORD, newer drivers) is right above 4 GB; MemorySize
                    # (a DWORD or REG_BINARY bytes) is the older fallback.
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


# -- starting with Windows ----------------------------------------------------------------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "Starlight"


def autostart_command(root: Path) -> str:
    """What Windows runs at logon: Starlight.exe, or this script with its Python."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --root "{root}"'


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except OSError:
        return False


# -- links to notes: starlight://note/... (docs/plan-notes-app.md, block G6) ------------------
# Windows opens a starlight:// link with Starlight.exe --open "<link>" (registered for your own
# account, HKCU\Software\Classes\starlight, by each start of Starlight.exe; "links": false in
# config/launcher.json takes it away). A link only ever opens a page of the web chat: it
# never asks Starlight anything or runs a tool, so a link in a web page or a mail can do no
# more than show one of your notes.

LINK_SCHEME = "starlight"
LINK_KEY = r"Software\Classes\starlight"


def link_target(link: str) -> str | None:
    """The page a starlight:// link opens, as a path of the web chat, or None when it is
    not one of these:

    starlight://note/Notes/Plan.md     the note at that path in the Starlight folder
    starlight://note/Plan              the note called Plan (by its name or an alias)
    starlight://note/Plan#Shopping     ... at that heading
    starlight://notes                  the Notes page
    starlight://chat                   the chat
    """
    try:
        parsed = urllib.parse.urlsplit(link.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() != LINK_SCHEME:
        return None
    kind = parsed.netloc.lower()
    name = urllib.parse.unquote(parsed.path).strip("/").strip()
    heading = urllib.parse.unquote(parsed.fragment).strip()
    if kind == "chat" and not name:
        return "/"
    if kind == "notes" and not name:
        return "/notes"
    if kind != "note":
        return None
    if not name:
        return "/notes"
    parts = name.split("/")
    if (
        len(name) > 500
        or len(heading) > 200
        or any(ord(c) < 32 or c in '\\<>|"?*' for c in name + heading)
        or re.match(r"^[A-Za-z]:", name)
        or any(part in ("", ".", "..") for part in parts)
    ):
        return None
    query = {"path": name} if name.lower().endswith(".md") else {"open": name}
    if heading:
        query["heading"] = heading
    return "/notes?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


def link_command(root: Path) -> str:
    """What Windows runs for a starlight:// link: Starlight.exe (or this script) with it."""
    return autostart_command(root) + ' --open "%1"'


def register_links(on: bool, command: str) -> bool:
    """Let starlight:// links open in Starlight, for your own account (HKCU), or take that
    away. Writes only what differs; True when something changed. Windows only."""
    if sys.platform != "win32":
        return False
    import winreg

    if not on:
        changed = False
        for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, LINK_KEY + sub)
                changed = True
        return changed
    wanted = {
        LINK_KEY: {"": "URL:Starlight", "URL Protocol": ""},
        LINK_KEY + r"\shell\open\command": {"": command},
    }
    changed = False
    for path, values in wanted.items():
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            for name, value in values.items():
                try:
                    if winreg.QueryValueEx(key, name)[0] == value:
                        continue
                except FileNotFoundError:
                    pass
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
                changed = True
    return changed


def ensure_links(settings: Settings, ui: Ui) -> None:
    """Set up starlight:// links (or take them away: "links": false), for Starlight.exe only:
    a script run, such as the tests, must not take them over. A problem is only said."""
    if not getattr(sys, "frozen", False):
        return
    try:
        register_links(settings.links, link_command(settings.root))
    except OSError as exc:
        ui.say(f"   starlight:// links could not be set up: {exc}")


def open_link_if_running(
    target: str, wait: float = 20, args: argparse.Namespace | None = None
) -> bool:
    """Starlight's web chat is running already: open ``target`` (once the chat answers, at
    most ``wait`` seconds) in Starlight's own window or the browser, and say True. False: it
    is not running."""
    if not port_in_use(WEB_PORT):
        return False
    end = time.monotonic() + wait
    while time.monotonic() < end and fetch_json(f"{chat_url()}/api/status") is None:
        time.sleep(0.5)
    settings: Settings | None = None
    if args is not None:
        with contextlib.suppress(LauncherError):
            settings = load_settings(args)
    uv = settings.tools / "uv" / exe("uv") if settings is not None else None
    if settings is not None and uv is not None and uv.is_file():
        ChatWindow(settings, uv).open(chat_url() + target, wait=True)
    else:
        _open_browser(chat_url() + target)
    return True


def set_autostart(on: bool, command: str) -> None:
    """Start Starlight when you log in to Windows (your own account's Run list), or not."""
    if sys.platform != "win32":
        raise LauncherError("starting with the computer is set up here on Windows only")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, command)
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, AUTOSTART_NAME)


# -- models from Hugging Face ------------------------------------------------------------

HF = "https://huggingface.co"
SPLIT_PART = re.compile(r"-\d{5}-of-\d{5}\.gguf$")  # one part of a model in several files


def _get_json(url: str, timeout: float = 20) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(8 * MB).decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise LauncherError(f"Hugging Face did not answer: {exc}") from exc


def search_hf(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """GGUF models on Hugging Face matching ``query``, most downloaded first."""
    query = query.strip()
    if not query:
        return []
    found = _get_json(
        f"{HF}/api/models?search={urllib.parse.quote(query)}&filter=gguf"
        f"&sort=downloads&direction=-1&limit={limit}"
    )
    return [
        {"repo": str(m["id"]), "downloads": int(m.get("downloads") or 0),
         "likes": int(m.get("likes") or 0)}
        for m in found if isinstance(m, dict) and m.get("id")
    ] if isinstance(found, list) else []  # fmt: skip


def hf_files(repo: str) -> list[dict[str, Any]]:
    """The model files of a Hugging Face repository that Starlight can run (one .gguf each;
    not the vision add-ons or models split over several files), smallest first."""
    if not re.fullmatch(r"[\w-][\w.-]*/[\w-][\w.-]*", repo):  # (no "..": not a path)
        raise LauncherError(f"not a Hugging Face repository: {repo}")
    listed = _get_json(f"{HF}/api/models/{repo}/tree/main?recursive=true")
    files = []
    for entry in listed if isinstance(listed, list) else []:
        path = str(entry.get("path", "")) if isinstance(entry, dict) else ""
        name = path.rsplit("/", 1)[-1].lower()
        if not name.endswith(".gguf") or "mmproj" in name or SPLIT_PART.search(name):
            continue
        size = int((entry.get("lfs") or {}).get("size") or entry.get("size") or 0)
        url = f"{HF}/{repo}/resolve/main/{urllib.parse.quote(path)}"
        files.append({"file": path, "size": size, "url": url})
    return sorted(files, key=lambda f: f["size"])


# -- what takes the disk space ---------------------------------------------------------------


def disk_report(settings: Settings) -> list[dict[str, Any]]:
    """Each downloaded model (the one in use marked) and the other big folders, with sizes."""
    report: list[dict[str, Any]] = []
    if settings.models.is_dir():
        for model in sorted(settings.models.glob("*.gguf")):
            report.append({
                "name": model.name, "path": model, "bytes": model.stat().st_size,
                "kind": "model", "in_use": model == settings.model_file and settings.runs_llama,
            })  # fmt: skip
    for name, folder in (
        ("llama.cpp, speech and other tools", settings.tools),
        ("Python and Starlight's packages", settings.root / ".venv"),
        ("download cache", settings.root / ".uv-cache"),
        ("logs", settings.root / "logs"),
        ("data (conversations, statistics, indexes)", settings.root / "data"),
        ("memory", settings.root / "memory"),
    ):
        if folder.is_dir():
            report.append({"name": name, "path": folder, "bytes": folder_size(folder),
                           "kind": "folder", "in_use": True})  # fmt: skip
    return report


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


def chat_url() -> str:
    return f"http://127.0.0.1:{WEB_PORT}"


def viewer_url() -> str:
    return f"http://127.0.0.1:{VIEWER_PORT}"


# What the error page suggests for problems it knows: (words in the message or the details,
# what to try). The window shows at most three; tr() has them in German.
ERROR_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("out of memory", "out of video memory", "outofdevicememory", "failed to allocate",
      "unable to allocate", "cudamalloc", "insufficient memory", "not enough video memory"),
     "The model does not fit into the graphics card's memory. In Settings, choose a smaller "
     "model or less context."),
    (("not enough free disk space", "no space left", "disk full", "errno 28", "winerror 112"),
     "The drive is full. Free some space (Disk space shows what Starlight keeps), or choose "
     "a smaller model."),
    (("network problem", "urlopen error", "getaddrinfo", "name or service not known",
      "temporary failure in name resolution", "timed out", "connection reset",
      "winerror 10060", "winerror 11001"),
     "Check the internet connection, then press Try again: finished downloads are kept."),
    (("address already in use", "only one usage of each socket address", "winerror 10048",
      "errno 98"),
     "Another program uses Starlight's port: close a Starlight that may still run, or "
     "restart the PC."),
    (("permission denied", "access is denied", "winerror 5]", "winerror 32",
      "being used by another process"),
     "Windows blocked a file: close programs that use Starlight's folder, and keep Starlight "
     "out of Program Files and OneDrive."),
    (("launcher.json is not valid",),
     "config/launcher.json has a mistake: fix it, or delete it to start with the defaults."),
    (("did not become ready",),
     "The model took too long to start. Press Try again; if it happens again, choose a "
     "smaller model in Settings."),
    (("checksum", "hash mismatch", "badzipfile", "bad zip", "is not a zip file"),
     "A download was damaged. Press Try again: that file is downloaded again."),
    (("lm studio",),
     "Start LM Studio and load a model there, or choose another model in Settings."),
)  # fmt: skip


def error_hints(text: str, limit: int = 3) -> list[str]:
    """What to try for a problem, from the words of its message and details (the error
    page shows them above the technical details)."""
    low = text.lower()
    return [hint for words, hint in ERROR_HINTS if any(w in low for w in words)][:limit]


def model_label(settings: Settings) -> str:
    """The model's name as the setup window shows it (\"Qwen2.5 14B, uncensored\"); its
    file's name for a model of your own."""
    if not settings.use_model:
        return ""
    for key, url in MODEL_PRESETS.items():
        if url == settings.model_url and key in MODEL_INFO:
            return MODEL_INFO[key][0]
    return settings.model_file.stem


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
        return load_in_lmstudio(url, key, settings.context or DEFAULT_CONTEXT)
    problem = unload_in_lmstudio(url, state.loaded)
    if problem:
        return problem
    return load_in_lmstudio(
        url, lmstudio_key(settings, state.loaded), settings.context or DEFAULT_CONTEXT
    )


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
            problem = load_in_lmstudio(url, key, self.settings.context or DEFAULT_CONTEXT)
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
        tokens = settings.context or DEFAULT_CONTEXT
        ui.say(f"   loading {key} in LM Studio with {tokens} tokens of context ...")
        problem = load_in_lmstudio(url, key, tokens)
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
    context = settings.context or DEFAULT_CONTEXT
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


OLLAMA_HINT = "Ollama does not answer: start Ollama (it runs in the background once installed)"


def other_server_state(settings: Settings) -> tuple[str, str]:
    """("ready" or "waiting", what to say) for Ollama or a server on another PC."""
    if settings.uses_ollama:
        tags = fetch_json(f"{settings.ollama_url}/api/tags", timeout=3)
        if tags is None:
            return "waiting", OLLAMA_HINT
        names = [str(m.get("name", "")) for m in tags.get("models") or [] if isinstance(m, dict)]
        wanted = settings.ollama_model or "qwen3:14b"
        if any(n == wanted or n.split(":")[0] == wanted for n in names):
            return "ready", f"{wanted} (Ollama)"
        return "waiting", f"Ollama has no {wanted}: run  ollama pull {wanted}"
    listed = fetch_json(f"{settings.remote_url}/models", timeout=3)
    if listed is None:
        return "waiting", f"the model server at {settings.remote_url} does not answer"
    names = [str(m.get("id", "")) for m in listed.get("data") or [] if isinstance(m, dict)]
    shown = settings.remote_model or (names[0] if names else "its model")
    return "ready", f"{shown} (at {urllib.parse.urlsplit(settings.remote_url).netloc})"


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
        elif settings.uses_ollama or settings.uses_remote:
            model, detail = other_server_state(settings)
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
            # /api/status (starlight/web/app.py): {"kill": {"engaged": bool, "reason": ...}}
            kill_switch = status.get("kill") or {}
            engaged = kill_switch.get("engaged")
            kill = None if engaged is None else bool(engaged)
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
    if ask is None:
        tell_whats_new(settings.root, ui)
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
            # -y: approve every confirmation (nobody is there to answer in a one-shot run)
            result = subprocess.run(starlight(uv, "ask", "-y", ask), cwd=settings.root, check=False)
            return result.returncode
        window = ChatWindow(settings, uv, group, ui)
        if settings.both:
            url = chat_url() + settings.first_page
            ui.say(f"\nOpening the chat in {chat_where(settings)}: {url}")
            ui.say("A terminal chat opens here too - both talk to the same Starlight.")
            ui.say("Type /quit to leave the terminal; close this window to stop everything.\n")
            group.start(starlight(uv, "web"), logs / "web.log", settings.root)
            wait_for_web(WEB_PORT, ui)
            threading.Timer(1.0, window.open, args=(url,)).start()
            return subprocess.run(
                starlight(uv, "connect"), cwd=settings.root, check=False
            ).returncode
        if settings.web:
            url = chat_url() + settings.first_page
            ui.say(f"\nOpening the chat in {chat_where(settings)}: {url}")
            ui.say("Close this window (or press Ctrl+C) to stop everything.\n")
            window.open_when_ready(url)  # (`starlight web` starts just below)
            return subprocess.run(starlight(uv, "web"), cwd=settings.root, check=False).returncode
        ui.say("Kill switch: Ctrl+Alt+J, a screen corner, or /kill. Leave with /quit.\n")
        return subprocess.run(starlight(uv, "run"), cwd=settings.root, check=False).returncode
    finally:
        if watchdog is not None:
            watchdog.stop()  # first: stopping the server on purpose is not a crash
        if lms_pause is not None:
            lms_pause.stop()
        group.stop()


APP_UNAVAILABLE = 3  # `starlight app` can show no window here (starlight/app_window.py)
APP_START = 15.0  # seconds: a window that ends this soon, not by hand, did not open


class ChatWindow:
    """The chat in Starlight's own window (`starlight app`: WebView2, no browser), when
    ``settings.app_window`` is on (Windows). Opening it again brings it to the front. When
    the window cannot open (no WebView2, an install without its package), the browser opens
    the chat instead, and ``note`` says why."""

    def __init__(
        self, settings: Settings, uv: Path, group: ProcessGroup | None = None,
        ui: Ui | None = None,
    ) -> None:  # fmt: skip
        self.settings, self.uv, self.group, self.ui = settings, uv, group, ui
        self.process: subprocess.Popen[bytes] | None = None
        self.note = ""  # why the browser opened instead (what the console says)
        self.reason = ""  # the window's own words for it (the launcher window shows them)
        self._log_from = 0  # where this start's lines begin in logs/app.log

    @property
    def wanted(self) -> bool:
        return self.settings.app_window and sys.platform == "win32"

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def open(self, url: str, wait: bool = False) -> None:
        """Show ``url``; ``wait``: only return once the window is surely open (or the
        browser opened instead), for a launcher that ends right after."""
        if not self.wanted or self.reason:  # (it could not open before: not again and again)
            _open_browser(url)
            return
        if self.running() and focus_app_window(self.settings.root):
            return
        command = starlight(self.uv, "app", "--url", url)
        log = self.settings.root / "logs" / "app.log"
        self._log_from = log.stat().st_size if log.is_file() else 0  # (this start's lines)
        if self.group is not None:
            process = self.group.start(command, log, self.settings.root)
        else:  # (a link opened while Starlight runs: this launcher ends, the window stays)
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("ab") as output:
                process = subprocess.Popen(
                    command, cwd=self.settings.root, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )  # fmt: skip
        self.process = process
        if wait:
            self._watch(process, url)
        else:
            threading.Thread(target=self._watch, args=(process, url), daemon=True).start()

    def open_when_ready(self, url: str, timeout: float = 180) -> None:
        """Open it once the web chat answers (in the background): started before the chat
        is up, the window would start a chat of its own on the same port."""

        def wait() -> None:
            end = time.monotonic() + timeout
            while time.monotonic() < end and fetch_json(f"{chat_url()}/api/status") is None:
                time.sleep(0.5)
            self.open(url)

        threading.Thread(target=wait, name="chat-window", daemon=True).start()

    def _watch(self, process: subprocess.Popen[bytes], url: str) -> None:
        try:
            code = process.wait(timeout=APP_START)
        except subprocess.TimeoutExpired:
            return  # it is open
        if code == 0:
            return  # closed again, by hand
        log = self.settings.root / "logs" / "app.log"
        self.reason = last_line(log, self._log_from) or "see logs/app.log"
        self.note = f"Starlight's own window could not open ({self.reason}): "
        self.note += "the chat opens in the browser."
        if self.ui is not None:
            self.ui.say(self.note)
        _open_browser(url)


def last_line(path: Path, start: int = 0) -> str:
    """The last line of a log that says something (for a short reason), from byte
    ``start`` on (what one run wrote)."""
    try:
        with path.open("rb") as log:
            log.seek(start)
            lines = log.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    said = [line.strip() for line in lines if line.strip()]
    return said[-1][:200] if said else ""


def focus_app_window(root: Path) -> bool:
    """Bring Starlight's own window to the front (Windows; its process wrote its id to
    data/app-window/window.pid). False: none is open."""
    if sys.platform != "win32":
        return False
    try:
        pid = int((root / "data" / "app-window" / "window.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")  # (its own handle: the argument types below stay here)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindow.argtypes, user32.GetWindow.restype = (
        [wintypes.HWND, wintypes.UINT],
        wintypes.HWND,
    )
    for name in ("IsWindowVisible", "IsIconic", "SetForegroundWindow"):
        getattr(user32, name).argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    found: list[int] = []

    def each(handle: int, _param: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        shown = user32.IsWindowVisible(handle) and not user32.GetWindow(handle, 4)  # GW_OWNER
        if owner.value == pid and shown:  # (a visible top-level window of that process)
            found.append(handle)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each)
    user32.EnumWindows(callback, 0)
    for handle in found:
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)  # SW_RESTORE
        user32.SetForegroundWindow(handle)
        return True
    return False


def chat_where(settings: Settings) -> str:
    """Where the chat opens, for what the launcher says."""
    return (
        "Starlight's own window"
        if settings.app_window and sys.platform == "win32"
        else "your browser"
    )


def _open_browser(url: str) -> None:
    with contextlib.suppress(Exception):
        import webbrowser

        webbrowser.open(url)


# -- the Absolution security tools (modules/absolution): report what is installed ------------
# The launcher never fetches these; it only says which are present, so you can install the
# rest yourself. Keep this list in step with modules/absolution/tools.py. The offensive
# cluster is not here on purpose (see that file's BLOCKED).
SECURITY_TOOLS: dict[str, tuple[str, ...]] = {
    "Nmap": ("nmap",),
    "Masscan": ("masscan",),
    "Netdiscover": ("netdiscover",),
    "theHarvester": ("theHarvester", "theharvester"),
    "Amass": ("amass",),
    "Subfinder": ("subfinder",),
    "Assetfinder": ("assetfinder",),
    "httpx": ("httpx",),
    "WhatWeb": ("whatweb",),
    "Nikto": ("nikto",),
    "Nuclei": ("nuclei",),
    "Gobuster": ("gobuster",),
    "Dirsearch": ("dirsearch",),
    "Ffuf": ("ffuf",),
    "Feroxbuster": ("feroxbuster",),
    "SQLmap": ("sqlmap",),
    "SearchSploit": ("searchsploit",),
    "Trivy": ("trivy",),
    "Checkov": ("checkov",),
    "kube-bench": ("kube-bench",),
    "Prowler": ("prowler",),
    "ScoutSuite": ("scout", "scoutsuite"),
    "Semgrep": ("semgrep",),
    "Bandit": ("bandit",),
    "OWASP Dependency-Check": ("dependency-check", "dependency-check.sh"),
    "tshark": ("tshark",),
    "Zeek": ("zeek",),
    "Suricata": ("suricata",),
    "Volatility": ("vol", "vol.py", "volatility3", "volatility"),
    "osquery": ("osqueryi",),
    "YARA": ("yara",),
    "Sigma": ("sigma",),
    "Burp Suite": ("burpsuite", "BurpSuiteCommunity"),
    "OWASP ZAP": ("zaproxy", "zap.sh", "ZAP"),
    "Wireshark": ("wireshark",),
    "Ghidra": ("ghidraRun", "ghidra"),
    "IDA": ("ida64", "ida"),
    "x64dbg": ("x64dbg", "x32dbg"),
}


def security_tools_report() -> list[tuple[str, str | None]]:
    """Each Absolution tool and the path it was found at (None when it is not installed),
    in name order."""
    found: list[tuple[str, str | None]] = []
    for name, exes in SECURITY_TOOLS.items():
        where = next((p for exe in exes if (p := shutil.which(exe))), None)
        found.append((name, where))
    return sorted(found, key=lambda row: row[0].lower())


def print_security_tools(ui: Ui) -> None:
    report = security_tools_report()
    have = [name for name, where in report if where]
    missing = [name for name, where in report if not where]
    ui.say("Absolution security tools (Starlight runs these only when you ask; it never")
    ui.say("installs them). Found on this PC:")
    ui.say("  " + (", ".join(have) if have else "none yet"))
    if missing:
        ui.say("\nNot found (install the ones you need yourself):")
        ui.say("  " + ", ".join(missing))
    ui.say(
        "\nOffensive tooling (credential theft, lateral movement, C2, evasion) is not part "
        "of Absolution and Starlight will not drive it."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Starlight", description="Set up and start Starlight, the local AI assistant."
    )
    parser.add_argument("--version", action="version", version=f"Starlight launcher {VERSION}")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask; use defaults")
    parser.add_argument("--setup-only", action="store_true", help="install, then exit")
    parser.add_argument(
        "--ask",
        metavar="TEXT",
        help="one request, print the answer, exit (actions that normally ask first are approved)",
    )
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
        "--ollama-model",
        metavar="NAME",
        help="use a model in Ollama (e.g. qwen3:14b) instead of downloading one",
    )
    parser.add_argument(
        "--remote-url",
        metavar="URL",
        help="use a model server on another PC (OpenAI-compatible, e.g. "
        "http://192.168.1.20:8080/v1) instead of running one here",
    )
    parser.add_argument(
        "--style", choices=list(STYLES), help="how freely the model words its answers"
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
    parser.add_argument(
        "--browser", action="store_true",
        help="open the chat in the browser, not in Starlight's own window",
    )  # fmt: skip
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
    parser.add_argument(
        "--context", type=int, help="context window in tokens (0: as much as fits the card)"
    )
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
    parser.add_argument(
        "--security-tools",
        action="store_true",
        help="list which Absolution security tools are installed, then exit",
    )
    parser.add_argument(
        "--open",
        metavar="LINK",
        help="open a starlight:// link (starlight://note/Plan): in the running Starlight, "
        "or start it with that page",
    )
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
    if os.environ.get("STARLIGHT_NO_RELAUNCH"):  # this is the copy
        return False
    env = {
        **os.environ,
        "STARLIGHT_NO_RELAUNCH": "1",  # so the copy does not start a copy of its own
        # A PyInstaller onefile exe starting itself: the copy unpacks its own temporary _MEI
        # folder instead of using ours, which is deleted when this process ends in a moment.
        "PYINSTALLER_RESET_ENVIRONMENT": "1",
    }
    # No console for the copy, and not in the process group of this console (its Ctrl+C).
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
    # Before any ProcessGroup exists: on Windows it puts this process into a job that ends
    # everything in it when this process ends, the copy included. --gui-selftest stays in
    # this process, so the CI checks the window itself (its "gui ok" and exit code).
    if not args.gui_selftest and relaunch_without_console():
        return 0
    # The module itself is handed over: in Starlight.exe it runs as __main__.
    return launcher_gui.run(sys.modules[__name__], args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.security_tools:  # list the Absolution tools and exit (installs nothing)
        print_security_tools(make_ui(assume_yes=True))
        return 0
    if args.open is not None:  # a starlight:// link (block G6)
        target = link_target(args.open)
        if target is None:
            print(f"Not a Starlight link: {args.open[:200]}", file=sys.stderr)
            return 2
        if open_link_if_running(target, args=args):
            return 0
        args.page = target  # not running: start it, with that page first
    if gui_wanted(args):
        window_code = run_window(args)
        if window_code is not None:
            return window_code
    ui = make_ui(args.yes)
    # What the finally below sees: it stays 1 unless a branch sets it, so errors, Ctrl+C
    # (which returns 130 = 128 + SIGINT) and unexpected exceptions all keep the window open.
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
