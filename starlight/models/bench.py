"""Benchmark the model: time to first token, tokens per second and VRAM use. Results are
written to data/stats.db for the statistics viewer."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from typing import Any

from starlight.core.model import Message, ModelProvider
from starlight.core.stats import Stats

PROMPT = (
    "Write a detailed, practical explanation (about 300 words) of how a home server "
    "backup strategy should work, with numbered steps."
)


def _run(cmd: list[str], timeout: float = 10.0) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _numbers(row: str) -> list[float]:
    return [float(v) for v in re.findall(r'"([\d.]+)"', row)]


def gpu_status() -> dict[str, Any] | None:
    """VRAM in use (and total, and utilization in %, where known), from whichever tool
    this machine has: rocm-smi, nvidia-smi or the Windows performance counters."""
    out = _run(["rocm-smi", "--showuse", "--showmeminfo", "vram", "--json"])
    if out:
        try:
            for card in json.loads(out).values():
                use = card.get("GPU use (%)")
                return {
                    "source": "rocm-smi",
                    "used_bytes": int(card.get("VRAM Total Used Memory (B)", 0)),
                    "total_bytes": int(card.get("VRAM Total Memory (B)", 0)),
                    "utilization": float(use) if use is not None else None,
                }
        except (ValueError, AttributeError):
            pass
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if out:
        used, total, util = (float(x) for x in out.splitlines()[0].split(","))
        return {
            "source": "nvidia-smi",
            "used_bytes": int(used * 2**20),
            "total_bytes": int(total * 2**20),
            "utilization": util,
        }
    if sys.platform == "win32":
        # Windows performance counters work for AMD, NVIDIA and Intel GPUs.
        out = _run([
            "typeperf", r"\GPU Adapter Memory(*)\Dedicated Usage",
            r"\GPU Engine(*engtype_3D)\Utilization Percentage", "-sc", "1",
        ])  # fmt: skip
        if out:
            rows = [r for r in out.splitlines() if r.startswith('"')]
            if len(rows) >= 2:
                header = re.findall(r'"([^"]*)"', rows[0])[1:]
                values = _numbers(rows[-1])
                memory = [v for h, v in zip(header, values, strict=False) if "Memory" in h]
                engine = [v for h, v in zip(header, values, strict=False) if "Engine" in h]
                return {
                    "source": "typeperf",
                    "used_bytes": int(max(memory)) if memory else None,
                    "utilization": round(min(100.0, sum(engine)), 1) if engine else None,
                }
    return None


gpu_memory = gpu_status  # older name


async def benchmark(
    provider: ModelProvider, runs: int = 3, stats: Stats | None = None, prompt: str = PROMPT
) -> dict[str, Any]:
    streams = getattr(provider, "supports_streaming", False)
    results: list[dict[str, float]] = []
    for _ in range(runs):
        first: list[float] = []
        chunks = 0

        def on_token(_: str, first: list[float] = first) -> None:
            nonlocal chunks
            chunks += 1
            if not first:
                first.append(time.perf_counter())

        started = time.perf_counter()
        if streams:
            response = await provider.chat(  # type: ignore[call-arg]
                [Message("user", prompt)], [], on_token=on_token
            )
        else:
            response = await provider.chat([Message("user", prompt)], [])
        ended = time.perf_counter()
        tokens = float(response.usage.get("completion_tokens") or chunks or 0)
        ttft = (first[0] - started) if first else (ended - started)
        tps = response.usage.get("tokens_per_second") or (
            tokens / (ended - (first[0] if first else started)) if tokens else 0.0
        )
        results.append({"ttft_s": ttft, "tokens_per_second": float(tps), "tokens": tokens})

    report: dict[str, Any] = {
        "provider": provider.name,
        "runs": runs,
        "ttft_s": round(sum(r["ttft_s"] for r in results) / runs, 3),
        "tokens_per_second": round(sum(r["tokens_per_second"] for r in results) / runs, 2),
        "tokens": int(sum(r["tokens"] for r in results) / runs),
    }
    vram_of = getattr(provider, "vram", None)  # Ollama: how much of the model is in VRAM
    if vram_of is not None:
        try:
            report["model_vram"] = await vram_of()
        except Exception:
            report["model_vram"] = None
    fraction = (report.get("model_vram") or {}).get("gpu_fraction")
    report["full_gpu_offload"] = None if fraction is None else fraction >= 0.999
    report["gpu_memory"] = await asyncio.to_thread(gpu_status)

    if stats is not None:
        for key in ("ttft_s", "tokens_per_second"):
            stats.record(f"model.bench.{key}", report[key], provider=provider.name)
        if report["gpu_memory"] and report["gpu_memory"].get("used_bytes") is not None:
            stats.record("gpu.vram_used_bytes", report["gpu_memory"]["used_bytes"])
        if fraction is not None:
            stats.record("model.bench.gpu_fraction", fraction, provider=provider.name)
    return report
