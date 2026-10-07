# Starlight

**A private AI assistant that runs entirely on your own PC.**

Starlight uses a language model on your own graphics card, with no account, no
subscription and no token limits. It remembers what you tell it and can browse the web.
It can check your servers, read the screen, use mouse and keyboard, write and test code,
and answer you on WhatsApp. Anything risky asks you first, and one key press stops it.

- [What it can do](#what-it-can-do)
- [What you need](#what-you-need)
- [Installation](#installation)
- [Using Starlight](#using-starlight)
- [Optional extras](#optional-extras)
- [Safety and privacy](#safety-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Command reference](#command-reference)
- [For developers](#for-developers)

---

## What it can do

| Feature | What it does |
| --- | --- |
| **Chat in a browser or the terminal** | A clean web chat (the default) or a colored terminal chat. Both stream answers and ask before risky actions. |
| **Chat with a local model** | Runs a model such as Qwen2.5-14B on your GPU. Long conversations are summarized, not cut off. |
| **Remember** | Keeps what it learns as plain text files in the `memory` folder. You can read, edit or delete them. |
| **Use the web** | Searches, reads pages and logs in to your accounts. It makes at most 5 requests per second and respects robots.txt. |
| **Check your servers** | Pings your servers, checks disk space and services over SSH, and alerts you when one goes down. |
| **Scan your network** | On hosts you own or are authorized to test: open ports, services, TLS certs, HTTP headers, DNS. Asks first; recon only, never attacks. |
| **Look up vulnerabilities** | Checks a service or product against the public CVE database (NVD). Pairs with a scan. Free, no key. |
| **Check certificates** | Reads your servers' TLS certificates and warns when one is about to expire. |
| **Check passwords** | Tells you if a password is in known breaches — the password never leaves your PC (only a short hash prefix is sent). |
| **Check your PC** | CPU, memory, disk space, battery, uptime and running programs. Can close a program (asks first). |
| **Use the clipboard** | Reads what you copied and copies things for you. |
| **Weather** | Current weather and a short forecast for any place — free, no account or API key. |
| **News** | Headlines and topic search from free news feeds (RSS). No account or API key. |
| **Prices** | Stock, currency and crypto prices from free sources. No API key. |
| **Find files** | Searches your PC for files by name or by what's inside them. Read-only. |
| **Reminders** | "Remind me in 2 hours …" or "every day at 8 …" — reminders that come back to you, even after a restart. |
| **Desktop notifications** | Pops a notification on your screen to get your attention. |
| **Email** | Reads your inbox and sends mail from your own account (asks before sending). |
| **PDFs** | Reads text from PDF files and makes simple PDFs from text. |
| **Use your desktop** | Reads the screen, then clicks and types for you, with a red banner on screen while it does. |
| **Save files** | Saves letters, notes, tables (CSV) and web pages it makes for you in `Documents\Starlight`. |
| **Write code** | Writes small programs, tests them in a sandbox and fixes them until the tests pass. |
| **Learn skills** | Turns tasks that worked into reusable skills, but only after you approve them. |
| **Manage access from chat** | Add servers, logins and API keys, and set what runs without asking — all by typing, no files to edit. |
| **Answer on WhatsApp** | Lets you send tasks from your phone and stop everything with `/kill`. |
| **Show statistics** | Shows tasks, errors, model speed and GPU use in a separate viewer in your browser. |

## What you need

| Requirement | Details |
| --- | --- |
| **Computer** | Windows 11, 64-bit (Windows 10 should work but is untested). Linux works too, see [Linux](#linux). |
| **Graphics card** | AMD, NVIDIA or Intel, with an up-to-date driver. The default model needs **16 GB of video memory** (e.g. an RX 9060 XT 16 GB). With less, choose a [smaller model](#a-different-model). Without one it runs on the CPU, but slowly. |
| **Disk space** | About **12 GB** free: ~2 GB for the programs and ~9 GB for the model. |
| **Internet** | For the first setup and for web features. |

Nothing has to be installed beforehand, and no administrator rights are needed.

---

## Installation

### 1. Download

Open the [Releases page](https://github.com/Paul44ur/Starlight-Projekt-V2/releases) and
download `Starlight-<version>-windows.zip`. It contains the Starlight folder with
`Starlight.exe` already inside.

> [!NOTE]
> **No release yet?** Download the code with the green **Code** button, then
> **Download ZIP**. Then get `Starlight.exe`:
> 1. Open [Actions → Launcher](https://github.com/Paul44ur/Starlight-Projekt-V2/actions/workflows/launcher.yml).
> 2. Open the newest green run.
> 3. Download the **Starlight-windows** file at the bottom.
> 4. Unzip it.

### 2. Unpack

Unpack the zip into a folder of your own, for example `C:\Starlight`.

- **Avoid `C:\Program Files`.** Starlight needs to write inside its folder, and Program
  Files needs administrator rights.
- **Avoid OneDrive folders such as Desktop or Documents.** OneDrive would try to upload
  the 9 GB model.

If you downloaded the two files separately, put `Starlight.exe` into the unpacked folder,
next to `pyproject.toml`.

### 3. Start Starlight.exe

Double-click `Starlight.exe`.

Windows may show **"Windows protected your PC"**, because the program isn't signed yet.
Click **More info**, then **Run anyway**. You only need to do this once.

### 4. Let it set everything up

The first start lists what it will download and asks once:

```
Starlight will download and set up, inside C:\Starlight:
   - uv, Python 3.12 and Starlight's packages      ~1.3 GB
   - the Chromium browser for web pages and logins ~0.3 GB
   - the llama.cpp model server (vulkan)         ~0.2 GB
   - the model Qwen2.5-14B_Uncensored_Instruct-Q4_K_M.gguf 8.4 GB
   Needed: about 10.2 GB. Free on this drive: 250.0 GB.
Continue? [Y/n]
```

Press **Enter**. How long this takes depends on your internet connection, mostly for the
model.
- Starlight's packages, the model server and the model go into the Starlight folder
  (`.venv`, `tools`, `models`). Python and the Chromium browser go into your user
  profile, where uv and Playwright keep them (`%APPDATA%\uv\python` and
  `%LOCALAPPDATA%\ms-playwright`). Nothing needs administrator rights.
- If the download is interrupted, start `Starlight.exe` again. It continues where it
  stopped.
- Every download that publishes a checksum is checked against it.

At the end it offers **Tesseract**, which lets Starlight read text on the screen. This
one is a normal Windows installer and asks for administrator approval. You can say no
and add it later.

### 5. Chat

Starlight loads the model onto your graphics card, starts the statistics viewer
(http://127.0.0.1:8765), and **opens the chat in your web browser**
(http://127.0.0.1:8700). Type in the browser window and press Send. Actions that change
something show Approve / Deny buttons.

Prefer the keyboard? Start `Starlight.exe --terminal` for a colored terminal chat in the
window instead.

That's it. **Next time, just double-click `Starlight.exe` again.** It skips the
downloads and goes straight to opening the chat. Closing the black window stops
everything (the model server, the chat and the viewer). The emergency stop is
**Ctrl+Alt+J** or moving the mouse into a screen corner.

### Options

Run `Starlight.exe --help` in a terminal to see every option. The most useful ones:

| Option | What it does |
| --- | --- |
| `--model-url <link>` | Use another model (a direct download link to a `.gguf` file). |
| `--context 16384` | A smaller context window: uses less video memory. |
| `--variant cpu` | Run without a graphics card (slow). |
| `--terminal` | Chat in the window instead of opening the browser. |
| `--no-viewer` | Don't start the statistics viewer. |
| `--update` | Download the newest uv and llama.cpp again. |
| `--setup-only` | Download and set up, then exit without starting. |

To use options every time, put them into `config\launcher.json`, for example:

```json
{ "context": 16384, "viewer": false }
```

#### A different model

Any GGUF model with tool calling works, such as Qwen2.5 or Llama 3.1 Instruct. As a rule
of thumb:

| Video memory | Model size at Q4_K_M |
| --- | --- |
| 16 GB | up to 14B (the default) |
| 8–12 GB | 7B to 8B |
| 6 GB or less | 3B, or `--variant cpu` |

On [Hugging Face](https://huggingface.co), open the model's *Files* tab, copy the
download link of the `.gguf` file and start `Starlight.exe --model-url <link>`. Or put
`"model_url": "<link>"` into `config\launcher.json`.

### Other ways to install

<details>
<summary><b>Manual installation on Windows (PowerShell)</b></summary>

For running the model with Ollama, or setting things up yourself:

```powershell
cd C:\Starlight
powershell -ExecutionPolicy Bypass -File scripts\install.ps1    # add -Service to start at logon
uv run starlight run
```

The script installs [uv](https://docs.astral.sh/uv/) with winget, then Python 3.12,
Starlight's packages and the browser. Out of the box Starlight uses a test model that
only echoes text. Connect your real model as described in
[docs/setup-windows.md, section 2](docs/setup-windows.md#2-the-model-on-the-gpu-step-4).
</details>

<details id="linux">
<summary><b>Linux</b></summary>

The launcher works on Linux too. It needs Python 3.10 or newer:

```bash
cd Starlight
python3 launcher/starlight_launcher.py          # same as Starlight.exe; --help for options
```

Or install by hand with `bash scripts/install.sh` (add `--service` to start at logon),
then connect a model as in [docs/setup-windows.md](docs/setup-windows.md#2-the-model-on-the-gpu-step-4).
</details>

### Updating

1. Download the new version.
2. Unpack it over your Starlight folder.
3. Start `Starlight.exe`. It installs whatever changed.

Your `memory`, `skills`, `data`, `logs`, `models` and `tools` folders and your
`config\starlight.local.yaml` aren't part of the download, so they stay as they are. Keep
a copy of any changes you made to the other files in `config`, because those files are
overwritten. Files Starlight saved for you are in `Documents\Starlight`, outside the
Starlight folder, so updates never touch them.

### Uninstalling

1. If you turned on starting at logon, run `starlight service uninstall` first (see
   [Command reference](#command-reference) for how to run commands).
2. Delete the Starlight folder.

Passwords and keys you stored are in the Windows Credential Manager under "starlight".
Remove them there, or with `starlight vault delete <key>` before deleting the folder. Files
Starlight saved for you stay in `Documents\Starlight` until you delete that folder too.

Python, the Chromium browser and the download cache live in your user profile. To remove
them as well, delete these folders. Paste each one into the Explorer address bar to find
it. Skip any that another program of yours still uses.
- `%APPDATA%\uv`: Python
- `%LOCALAPPDATA%\uv`: the download cache
- `%LOCALAPPDATA%\ms-playwright`: Chromium

---

## Using Starlight

**Two ways to chat.** When you start `Starlight.exe`, it opens a **web chat in your
browser** (http://127.0.0.1:8700): a normal chat window where answers stream in, actions
you need to approve appear with **Approve / Deny** buttons, and a sidebar shows what's
allowed. Prefer the keyboard? Start it with `--terminal` (or run `starlight run`) for a
colored terminal chat. Both use the same assistant, settings and safeguards.

Just write what you want, for example:

```
Remember that my home server is called nas01.
Is nas01 up, and how full is its disk?
Search the web for the latest llama.cpp release and summarize the changes.
Scan 192.168.1.10 and tell me what's open.
Write a cover letter for the job at the bakery and save it as a file.
Open Notepad and type a shopping list: milk, bread, coffee.
```

Some of these need a [one-time setup](#optional-extras), for example servers and web
search.

**Give it access as you go.** You never have to edit project files. Just ask, and
Starlight adds it after you confirm:

```
Let me access my server at 192.168.1.10.        -> added to the allow-list
Set up my GitHub login.                          -> username and password go to the keyring
Save my Brave Search API key.                    -> stored as a secret, never shown again
Always allow pinging my servers without asking.  -> a permission rule it remembers
Show me what you're allowed to do.               -> servers, logins, secrets, permissions
```

**Scanning and your servers.** Starlight only reaches hosts you've allowed, and a scan
first asks you to confirm you own the host or are authorized to test it. It gathers
information (ports, services, certificates); it never attacks. A host you scan or ping
can't flood your machine back — responses are capped.

**Seeing what went wrong.** Ask "show me recent errors" (or run `starlight logs show`) to
read Starlight's own problems in plain language, without opening a log file.

**When Starlight asks first.** Actions that could change something ask before they run:
typing, clicking, running code, commands that change a server, and deleting a memory:

```
Starlight wants to run desktop.type_text({"text": "milk, bread, coffee"})
Allow? [y]es / [n]o / [a]lways this session:
```

**Stopping it.** Any of these stops everything at once, including typing in the middle of
a word:
- press **Ctrl+Alt+J**;
- move the mouse into any **corner of the screen**;
- type `/kill`;
- run `starlight kill` in another window;
- send `/kill` on WhatsApp.

Continue with `/resume`.

**Stopping just one answer.** Press **Ctrl+C** while Starlight is writing: that answer
stops, and you can ask the next thing. Very long answers stop by themselves after about
3,000 words (`model: reserve_tokens:` in the config).

**Pasting.** Paste several lines at once and they count as one request. Text you typed
before Starlight asks "Allow?" never counts as the answer.

**Chat commands.** `/help` lists them all. The main ones:

| Command | What it does |
| --- | --- |
| `/clear` | Start a new conversation. |
| `/tools` | Show what Starlight can use. |
| `/modules` | Show which modules are on or off. |
| `/kill`, `/resume` | Stop everything, and continue. |
| `/quit` | Leave. |

**Statistics.** Open http://127.0.0.1:8765 while Starlight runs. It shows tasks, errors,
model speed, GPU use and memory size.

**Memory.** Everything Starlight remembers is a text file in the `memory` folder. Open,
correct or delete any of them; Starlight picks up the change.

**Files.** Ask Starlight to save what it made ("save it as a file", "make a CSV of
that"), and it saves it in the **Starlight** folder in your Documents, then tells you
where:
- **No question in that folder.** Saving there just happens. Programs it writes go
  into its `code` subfolder.
- **Never overwritten silently.** A second `letter.txt` becomes `letter (2).txt`, and
  replacing a file asks you first.
- **Elsewhere, it asks.** "Save it on my Desktop" works, but Starlight asks first and
  shows the full path.
- **Text formats only.** It saves `.txt`, `.md`, `.csv`, `.html`, code and similar. For a
  Word document, ask for `.html`: Word opens it, and you can save it as `.docx` there.
  For a spreadsheet, ask for `.csv`: Excel opens it.

To use another folder, set `output: folder:` in `config\starlight.local.yaml`. Folders
where it may save without asking go under `trusted_folders` in
`config\modules\files.yaml`.

## Optional extras

Each of these is set up once. The step-by-step instructions are in
[docs/setup-windows.md](docs/setup-windows.md).

| Extra | What it needs |
| --- | --- |
| [Web search](docs/setup-windows.md#4-web-search-and-logins-step-6) | A local SearXNG (Docker) or a Brave Search API key. Reading web pages works without it. |
| [Logins to your accounts](docs/setup-windows.md#4-web-search-and-logins-step-6) | The site's login form in `config\modules\login.yaml`, and your password in the vault. |
| [Your servers](docs/setup-windows.md#5-your-servers-step-7) | A list in `config\servers.yaml`, and an SSH key. |
| [Reading the screen](docs/setup-windows.md#6-screen-and-input-step-8) | Tesseract (offered during setup). Optionally a vision model. |
| [Safer code sandbox](docs/setup-windows.md#7-code-and-skills-step-9) | Docker Desktop. Without it, code runs in a restricted separate process. |
| [Skill history](docs/setup-windows.md#7-code-and-skills-step-9) | [Git for Windows](https://git-scm.com/download/win), so you can undo a bad skill. |
| [WhatsApp](docs/setup-windows.md#8-whatsapp-step-10) | A WhatsApp Business account with its own phone number, and a Cloudflare Tunnel. |
| [Start at logon](docs/setup-windows.md#9-statistics-viewer-and-background-start-step-10) | `starlight service install`. No administrator rights needed. |

## Safety and privacy

- **Everything runs on your PC.** The model, your conversations, your memory and
  screenshots never leave it. Only web pages you ask for, and WhatsApp if you set it up,
  use the internet.
- **You decide what runs.** Every action is *allowed*, *asks first* or *blocked*. You can
  change this in `config\starlight.yaml` under `permissions`.
- **Passwords stay out of files.** Passwords and keys are kept in the Windows Credential
  Manager, never in config files. They are hidden in every log line, and
  `starlight logs scan` checks for leaks.
- **Servers are on an allow-list.** Starlight only talks to, scans or pings hosts you
  listed. Read-only commands such as `df` and `uptime` run freely; anything else asks
  first. A scan of a host you haven't allowed asks you to confirm you're authorized.
- **Nothing can flood your machine.** When Starlight fetches, pings or scans, responses
  are capped in size and time and connections are limited, so a hostile server can't
  turn a request into a flood against you. Scanning is reconnaissance only — it never
  attacks, and it does one host at a time.
- **Files stay where you expect.** Starlight saves on its own only into its folder in
  your Documents. Anywhere else, and replacing an existing file, it asks first. Its own
  install folder, which holds its settings and data, is off-limits, so it can't change
  its own permissions.
- **The model is yours, unchanged.** Starlight sends your messages to the local model
  you chose and returns its answer as-is — it adds no filter of its own. How the model
  behaves is down to the model file, which runs entirely on your PC.
- **Everything is logged.** Every action Starlight takes is recorded in
  `logs\audit.log`.

## Troubleshooting

<details>
<summary><b>"No Starlight folder found"</b></summary>

`Starlight.exe` must be inside the Starlight folder, next to `pyproject.toml`. Move it
there and start it again.
</details>

<details>
<summary><b>Windows or the antivirus blocks Starlight.exe</b></summary>

The exe isn't code-signed yet. In the SmartScreen window click **More info**, then
**Run anyway**. If your antivirus quarantined it, restore it and allow it.
</details>

<details>
<summary><b>"not enough free disk space"</b></summary>

Free up space on that drive, or move the Starlight folder to another drive. Downloads
that already finished are kept.
</details>

<details>
<summary><b>A download stopped, or "network problem"</b></summary>

Start `Starlight.exe` again. Finished downloads are kept, and an interrupted download
continues where it stopped.
</details>

<details>
<summary><b>"the model server stopped" or "did not become ready"</b></summary>

The last lines of `logs\llama-server.log` are shown, and they usually say why:
- **Out of memory:** use a smaller model or a smaller context, e.g. `--context 16384`.
- **No usable GPU:** update your graphics driver (AMD Adrenalin, NVIDIA or Intel). The
  Vulkan support comes with the driver. As a last resort, use `--variant cpu`.
</details>

<details>
<summary><b>Answers are slow</b></summary>

Check that the model runs on the graphics card. `logs\llama-server.log` should name
your GPU, and the statistics viewer shows tokens per second. A model that doesn't fit
into video memory is much slower: choose a smaller one.
</details>

<details>
<summary><b>"something already listens on port 8080"</b></summary>

Another program uses that port. Start with `--port 8081`, or add `"port": 8081` to
`config\launcher.json`.
</details>

<details>
<summary><b>Ctrl+Alt+J does nothing</b></summary>

The hotkey only works in a normal desktop session. The screen corners, `/kill` and
`starlight kill` always work.
</details>

<details>
<summary><b>"uv" is not recognized</b></summary>

`Starlight.exe` uses its own copy of uv in `tools\uv`. See
[Command reference](#command-reference) for how to run commands.
</details>

## Command reference

Open PowerShell in the Starlight folder and type the commands with `uv run` in front,
for example `uv run starlight status`.

If you installed with `Starlight.exe`, uv isn't on your PATH. Either install it once with
`winget install astral-sh.uv`, or use its full path, for example
`tools\uv\uv.exe run starlight status`.

| Command | What it does |
| --- | --- |
| `starlight web` | The browser chat (what `Starlight.exe` opens by default). |
| `starlight run` | The terminal chat (`Starlight.exe --terminal`). |
| `starlight ask "..."` | One request: prints the answer and exits (`-y` approves everything). |
| `starlight kill [reason]` / `resume` | Stop everything, and continue. Works from any window. |
| `starlight status` | Settings, kill switch and modules at a glance. |
| `starlight tools` | Everything Starlight can use, and whether it asks first. |
| `starlight modules list / enable / disable` | Turn modules on and off. |
| `starlight model info / serve / bench` | The model: settings, start its server, measure its speed. |
| `starlight memory list / search / add / forget` | What Starlight remembers. |
| `starlight vault set / list / delete KEY` | Passwords and keys, in the Windows Credential Manager. |
| `starlight skills list / show / approve / reject / rollback` | Learned skills. |
| `starlight serve` | Run in the background: WhatsApp, server alerts, kill hotkey. |
| `starlight viewer` | The statistics viewer on its own. |
| `starlight service install / start / stop / status / uninstall` | Start Starlight and the viewer at logon. |
| `starlight logs show` / `logs scan` | Show recent errors in plain language / check the logs for leaked passwords. |

## For developers

- [docs/architecture.md](docs/architecture.md): how a request flows, the folder layout,
  and what each step of the build plan added.
- [docs/writing-modules.md](docs/writing-modules.md): add your own module
  (`starlight modules new <name>`).
- Tests and checks:

  ```bash
  uv run pytest
  uv run ruff check . && uv run ruff format --check .
  uv run mypy && uv run mypy modules
  ```
