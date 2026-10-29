"""Embedding models for the memory index.

* ``hash``: built in, no model needed. Hashes words and character trigrams into a vector,
  so it finds memories that share words. Good for tests and as a fallback.
* ``ollama``: e.g. ``nomic-embed-text`` or ``bge-m3`` on the GPU via Ollama.
* ``openai_compat``: llama-server started with ``--embedding``, or another local server.
* ``lmstudio``: an embedding model in LM Studio (``nomic-embed-text`` ships with it).
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import httpx
import numpy as np
import numpy.typing as npt

from starlight.core.config import EmbedderConfig

type Vector = npt.NDArray[np.float32]


class Embedder(Protocol):
    @property
    def id(self) -> str:
        """Changes when vectors are not comparable (other model or size): forces a re-index."""
        ...

    async def embed(self, texts: list[str]) -> list[Vector]: ...


def normalize(vector: npt.NDArray[np.floating]) -> Vector:
    v: Vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return (v / norm).astype(np.float32) if norm else v


WORD = re.compile(r"\w+", re.UNICODE)


class HashEmbedder:
    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    @property
    def id(self) -> str:
        return f"hash-{self.dimensions}"

    def _vector(self, text: str) -> Vector:
        v = np.zeros(self.dimensions, dtype=np.float32)
        words = [w.casefold() for w in WORD.findall(text)]
        features = [f"w:{w}" for w in words]
        for w in words:
            padded = f" {w} "
            features += [f"c:{padded[i : i + 3]}" for i in range(len(padded) - 2)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            v[index] += sign * (2.0 if feature.startswith("w:") else 0.5)
        return normalize(v)

    async def embed(self, texts: list[str]) -> list[Vector]:
        return [self._vector(t) for t in texts]


class OllamaEmbedder:
    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://127.0.0.1:11434",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=10.0), transport=transport
        )

    @property
    def id(self) -> str:
        return f"ollama-{self.model}"

    async def embed(self, texts: list[str]) -> list[Vector]:
        response = await self._client.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts, "keep_alive": -1},
        )
        response.raise_for_status()
        return [normalize(np.array(v)) for v in response.json()["embeddings"]]


class OpenAICompatEmbedder:
    def __init__(
        self,
        model: str = "local",
        base_url: str = "http://127.0.0.1:8081/v1",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=10.0), transport=transport
        )

    @property
    def id(self) -> str:
        return f"openai-{self.model}"

    async def embed(self, texts: list[str]) -> list[Vector]:
        response = await self._client.post(
            f"{self.base_url}/embeddings", json={"model": self.model, "input": texts}
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda d: d["index"])
        return [normalize(np.array(d["embedding"])) for d in data]


# The embedding model LM Studio ships with.
LMSTUDIO_EMBEDDER = "text-embedding-nomic-embed-text-v1.5"


def create_embedder(config: EmbedderConfig) -> Embedder:
    if config.provider == "hash":
        return HashEmbedder(config.dimensions)
    if config.provider == "ollama":
        return OllamaEmbedder(
            config.model or "nomic-embed-text", config.base_url or "http://127.0.0.1:11434"
        )
    if config.provider == "openai_compat":
        return OpenAICompatEmbedder(
            config.model or "local", config.base_url or "http://127.0.0.1:8081/v1"
        )
    if config.provider == "lmstudio":  # an embedding model in LM Studio (loaded on first use)
        return OpenAICompatEmbedder(
            config.model or LMSTUDIO_EMBEDDER, config.base_url or "http://127.0.0.1:1234/v1"
        )
    raise ValueError(
        f"unknown embedder '{config.provider}' (hash, ollama, openai_compat, lmstudio)"
    )
