"""Start the model server with the settings from Step 4: every layer on the GPU, flash
attention, a q8 KV cache and the configured context length."""

from __future__ import annotations

import os
import shutil

from starlight.core.config import ModelConfig


class ServeError(Exception):
    pass


def _binary(configured: str, *names: str) -> str:
    if configured:
        return configured
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise ServeError(f"{names[0]} not found on PATH; set model.server.binary in the config")


def llama_server_command(config: ModelConfig) -> list[str]:
    s = config.server
    if not s.model_path:
        raise ServeError("set model.server.model_path to your .gguf file")
    cmd = [
        _binary(s.binary, "llama-server", "llama-server.exe"),
        "--model",
        s.model_path,
        "--host",
        s.host,
        "--port",
        str(s.port or 8080),
        "--ctx-size",
        str(config.context_window),
        "--n-gpu-layers",
        str(s.gpu_layers),
        "--parallel",
        str(s.parallel),
        "--threads",
        str(s.threads or os.cpu_count() or 4),  # all cores by default (slow when left at 1)
        "--jinja",  # chat template with tool calling
    ]
    if s.flash_attention:
        cmd += ["--flash-attn", "on"]
    if s.kv_cache_type:
        cmd += ["--cache-type-k", s.kv_cache_type, "--cache-type-v", s.kv_cache_type]
    return cmd + list(s.extra_args)


def ollama_command(config: ModelConfig) -> tuple[list[str], dict[str, str]]:
    s = config.server
    env = {
        "OLLAMA_HOST": f"{s.host}:{s.port or 11434}",
        "OLLAMA_FLASH_ATTENTION": "1" if s.flash_attention else "0",
        "OLLAMA_KV_CACHE_TYPE": s.kv_cache_type or "f16",
        "OLLAMA_KEEP_ALIVE": "-1",
        "OLLAMA_CONTEXT_LENGTH": str(config.context_window),
        "OLLAMA_NUM_PARALLEL": str(s.parallel),
    }
    return [_binary(s.binary, "ollama", "ollama.exe"), "serve", *s.extra_args], env


def serve_command(config: ModelConfig) -> tuple[list[str], dict[str, str]]:
    """Command and extra environment for the configured provider."""
    provider = config.provider
    if provider == "router":
        main = ModelConfig.model_validate(config.options.get("main", {}))
        return serve_command(main.model_copy(update={"server": config.server}))
    if provider == "llamacpp":
        return llama_server_command(config), {}
    if provider == "ollama":
        return ollama_command(config)
    raise ServeError(f"`model serve` supports llamacpp and ollama, not '{provider}'")


def run_server(config: ModelConfig) -> int:
    import subprocess

    cmd, extra_env = serve_command(config)
    env = {**os.environ, **extra_env}
    return subprocess.call(cmd, env=env)
