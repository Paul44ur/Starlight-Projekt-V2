# Setting up Starlight on your PC

For Windows 11 with an AMD RX 9060 XT (16 GB VRAM). Each section is optional: skip what
you do not use yet. To install Starlight itself, follow the
[installation guide in the README](../README.md#installation) first.

The commands below start with `uv run`. If you installed with `Starlight.exe`, uv is not
on your PATH: install it once with `winget install astral-sh.uv`, or type
`tools\uv\uv.exe run` instead of `uv run`.

## The short way: Starlight.exe

Double-click `Starlight.exe` in the Starlight folder. It does sections 1 and 2 below for
you (llama.cpp Vulkan build plus Qwen2.5-14B Uncensored Instruct) and offers Tesseract
from section 6. The model server and the model land in `tools\` and `models\` inside the
Starlight folder; Python and Chromium go to your user profile (`%APPDATA%\uv` and
`%LOCALAPPDATA%\ms-playwright`). The sections below are for doing it by hand, or for
the parts it does not cover (WhatsApp, servers, Docker).

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
Get-Content -Raw $HOME\.ssh\id_ed25519 | uv run starlight vault set ssh.homeserver
```

Use an ed25519 key: the Windows Credential Manager holds at most 1280 characters, too
little for an RSA 4096 key (for those, set `key_file` instead). Make sure the server's
host key is in `~/.ssh/known_hosts` (connect once with `ssh`).
Then ask: "Is my server up and what is its disk usage?"

## 6. Screen and input (Step 8)

1. Install Tesseract OCR (the UB Mannheim Windows installer). For German text, tick the
   German language data and set `ocr_languages: eng+deu` in `config/modules/desktop.yaml`.
2. Optional vision model: `ollama pull qwen2.5vl:7b` and the `vision:` block in
   `config/modules/desktop.yaml`. It must run on this computer: screenshots never leave it.
3. Try: "Open Notepad and type hello". Desktop input runs without asking out of the box
   (`desktop.*` in `permissions.auto_approve`); remove that line to be asked each time
   (answer `a` to allow a tool for the rest of the session).

**Stopping it:** Ctrl+Alt+J, moving the mouse into any screen corner, or
`uv run starlight kill` from another terminal. Then `uv run starlight resume`.

## 7. Code and skills (Step 9)

1. Install **Docker Desktop** so generated code runs in a container. Without Docker,
   Starlight uses a restricted separate process, which stops mistakes but is not a
   security boundary.
2. Optional coding model: `ollama pull qwen2.5-coder:14b-instruct-q4_K_M` and the
   `roles: code:` block in `config/starlight.yaml`.
3. Ask: "Write me a small script that renames my photos by date." The code is tested in
   the sandbox and saved in `Documents\Starlight\code\` (see section 10).
4. When the same kind of task works a second time, you get a draft skill:
   `uv run starlight skills list`, `skills show <name>`, `skills approve <name>`.
   A skill that turns out bad: `skills rollback <name>`.

## 8. WhatsApp (Step 10)

You need a Meta developer account, a WhatsApp Business app and a phone number for it
(a second number, not the one on your phone's WhatsApp).

1. At developers.facebook.com create an app of type *Business*, add *WhatsApp*, register
   the business number and note its **phone number ID**. Create a **permanent access
   token** (System User in Business Settings) and note the **app secret**.
2. Store the secrets:
   ```powershell
   uv run starlight vault set whatsapp.token
   uv run starlight vault set whatsapp.app_secret
   uv run starlight vault set whatsapp.verify_token    # any long random string
   ```
3. In `config/modules/whatsapp.yaml` set `owner_number` (your own number) and
   `phone_number_id`.
4. Expose only the webhook with a **Cloudflare Tunnel**: install `cloudflared`, create a
   tunnel, and use `config/cloudflared.example.yml` as its config (it routes only
   `/webhook/whatsapp` and answers everything else with 404). Run `cloudflared tunnel run`.
5. In the Meta dashboard set the webhook URL to
   `https://<your-hostname>/webhook/whatsapp`, the verify token from step 2, and
   subscribe to `messages`.
6. Start the core: `uv run starlight serve` (or `starlight service install`). Send
   "hello" from your phone. `/kill` stops everything; `/status` reports.

## 9. Statistics viewer and background start (Step 10)

- `uv run starlight viewer`, then open http://127.0.0.1:8765. It only reads
  `data/stats.db`, so it keeps working when the AI is stopped. Prometheus or Grafana can
  scrape http://127.0.0.1:8765/metrics.
- `uv run starlight service install` puts two shortcuts in your Startup folder that
  start the core and the viewer (without console windows) when you log in, and starts
  them now. No administrator rights needed. (Not a Windows service: services cannot see
  or control your desktop.) `service stop` asks both to shut down cleanly,
  `service start` starts them again, `service status` shows what runs, and
  `service uninstall` stops them and removes the shortcuts. Messages from a start
  without a console go to `logs/console.log`.
- `starlight run` works while the background core runs: it answers in your terminal, and
  the background core keeps the WhatsApp webhook and the server watcher (only one process
  runs those). If the background core stops, a running `starlight run` takes them over.
- `starlight kill` and `starlight resume` work from any terminal and affect every running
  Starlight process.
- Now and then: `uv run starlight logs scan` checks the logs for leaked secrets.

## 10. Where Starlight saves files

When you ask for something as a file ("save the letter", "make a CSV of the prices"),
Starlight saves it in the **Starlight** folder in your Documents and tells you the path.
It uses the real Documents folder, also when OneDrive has moved it.

- Saving into that folder needs no question. A name that is taken gets a number
  (`letter (2).txt`); replacing a file asks first.
- Anywhere else, a new file is saved without asking (`output: write_anywhere:`);
  replacing or adding to a file there, or saving a script type, asks first.
- Its own install folder (settings, data, memory, logs) is off-limits, also for reading.
- Word, Excel and PDF files are made by the office and pdf modules; everything else is
  text (`.txt`, `.md`, `.csv`, `.html`, code ...). CSV files get a byte-order mark so
  Excel shows umlauts correctly.

Another folder, in `config\starlight.local.yaml`:

```yaml
output:
  folder: D:/Starlight files
```

Folders where saving needs no question, and the other options, are in
`config\modules\files.yaml`:

```yaml
ask_before_replacing: true          # false: replace files in the folder without asking
trusted_folders: ["D:/Work/Reports"]
```

`uv run starlight status` shows the folder in use.
