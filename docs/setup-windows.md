# Setting up Starlight on your PC

For Windows 11 with an AMD RX 9060 XT (16 GB VRAM). Each section is optional: skip what
you do not use yet.

## 1. Starlight itself

```powershell
winget install astral-sh.uv
cd Starlight-Projekt-V2
uv sync --extra all
uv run playwright install chromium
uv run starlight status
```

## 2. The model on the GPU (Step 4)

**What fits in 16 GB.** A 14B model at Q4_K_M is about 9 GB; with a 32k context and the
q8 KV cache add about 3 GB, so about 12 to 13 GB in total. A 7B to 8B model leaves room for
a second small model (router) or the vision model. Ollama swaps models in and out when they
do not all fit.

### Option A: Ollama (simplest)

1. Install Ollama for Windows and pull models:
   ```powershell
   ollama pull qwen2.5:14b-instruct-q4_K_M
   ollama pull qwen2.5:3b-instruct      # optional: fast model for the router
   ollama pull nomic-embed-text         # optional: memory embeddings on the GPU
   ```
2. In `config/starlight.yaml`, use the Ollama block under `model:`.
3. Start the server with flash attention and the q8 KV cache:
   `uv run starlight model serve` (quit any Ollama already running in the tray first).
4. Check everything is on the GPU: `uv run starlight model bench`. The report shows
   `"full_gpu_offload": true` and the tokens per second.

If the report says the model is not fully on the GPU (or Ollama falls back to the CPU),
your Ollama build may not support this card yet. Use option B.

### Option B: llama.cpp with Vulkan

1. Download the Windows Vulkan build of llama.cpp from its GitHub releases
   (`llama-...-bin-win-vulkan-x64.zip`) and unpack it, e.g. to `C:\llama.cpp`.
2. Download a GGUF model, e.g. `Qwen2.5-14B-Instruct-Q4_K_M.gguf`.
3. In `config/starlight.yaml`, use the llama.cpp block and set `binary` and `model_path`.
4. `uv run starlight model serve` in one terminal, `uv run starlight run` in another.
5. `uv run starlight model bench` for tokens per second. VRAM use comes from the Windows
   performance counters.

## 3. Memory on the GPU (Step 5)

The built-in index matches shared words. For meaning ("graphics card" finds "GPU"), set:

```yaml
memory:
  embedder:
    provider: ollama
    model: nomic-embed-text
```

Then `uv run starlight memory reindex`. Your memories are the Markdown files in `memory/`:
open, fix or delete them by hand at any time.

## 4. Web search and logins (Step 6)

**Search.** Run SearXNG locally (Docker Desktop):

```powershell
docker run -d --name searxng -p 8888:8080 searxng/searxng
```

Enable JSON output: in the container's `settings.yml`, under `search: formats:`, add
`json`, then restart it. Alternatively use the Brave Search API: set
`search_backend: brave` in `config/modules/web.yaml` and
`uv run starlight vault set web.brave_api_key`.

**Logins.** Add the site to `config/modules/login.yaml` (selectors for the form fields),
then store the credentials in the Windows Credential Manager:

```powershell
uv run starlight vault set login.github.username
uv run starlight vault set login.github.password
```

The first login to each site asks you; 2FA codes are asked in the terminal.

## 5. Your servers (Step 7)

List them in `config/servers.yaml`. Put the SSH private key in the vault, or point
`key_file` at it:

```powershell
Get-Content $HOME\.ssh\id_ed25519 | uv run starlight vault set ssh.homeserver
```

Make sure the server's host key is in `~/.ssh/known_hosts` (connect once with `ssh`).
Then ask: "Is my server up and what is its disk usage?"

## 6. Screen and input (Step 8)

1. Install Tesseract OCR (the UB Mannheim Windows installer). For German text, tick the
   German language data and set `ocr_languages: eng+deu` in `config/modules/desktop.yaml`.
2. Optional vision model: `ollama pull qwen2.5vl:7b` and the `vision:` block in
   `config/modules/desktop.yaml`. It must run on this computer: screenshots never leave it.
3. Try: "Open Notepad and type hello". Every input action asks first; answer `a` to
   allow that tool for the rest of the session, or add `desktop.*` to
   `permissions.auto_approve` once you trust it.

**Stopping it:** Ctrl+Alt+K, moving the mouse into any screen corner, or
`uv run starlight kill` from another terminal. Then `uv run starlight resume`.
