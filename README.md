<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/starlight.svg">
    <img src="assets/logo/starlight-light.svg" alt="Starlight logo: a gold sparkle with purple rays" width="128">
  </picture>
</p>

# Starlight

**A private AI assistant that runs entirely on your own PC.**

Starlight uses a language model on your own graphics card, with no account, no
subscription and no token limits. It remembers what you tell it and can browse the web.
It can check your servers, read the screen, use mouse and keyboard, write and test code,
and answer you on WhatsApp. It will ask you before it does anything risky. And one key press stops it.

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
| **Remember** | Keeps what it learns as plain text files in the `memory` folder. You can read, edit or delete them. Knows your name, language and home town from your profile, tells you what it knows about you, and tidies up memories that say the same thing (asks first). |
| **Use the web** | Searches, reads pages and logs in to your accounts. It makes at most 10 requests per second, with a short pause between pages of the same site. A page you ask for is read like a browser would; crawling several pages (research, page watches) respects robots.txt. |
| **Check your servers** | Pings and checks any server, runs commands over SSH, and alerts you when one of yours goes down. |
| **Scan your network** | On hosts you own or are authorized to test: open ports, services, TLS certs, HTTP headers, DNS. Asks first; recon only, never attacks. |
| **Look up vulnerabilities** | Checks a service or product against the public CVE database (NVD). Pairs with a scan. Free, no key. |
| **Check certificates** | Reads your servers' TLS certificates and warns when one is about to expire. |
| **Check passwords** | Tells you if a password is in known breaches — the password never leaves your PC (only a short hash prefix is sent). |
| **Check your PC** | CPU, memory, disk space, battery, uptime and running programs. Can close a program, and lock, sleep, shut down or restart the PC, now or in a few minutes (asks first, except locking; the kill switch also cancels a planned shutdown). |
| **Focus mode** | "Focus for 90 minutes" — holds back reminders, alerts and messages while you work, then gives you one summary. Urgent things (an event about to start, a server down, an unknown device on your WiFi) still come through. |
| **Watch pages and prices** | "Tell me when this kettle costs less than 50 €", "… when 'sold out' is gone", "… when this page changes" — checks the page every so often (at least 5 minutes apart, robots.txt respected) and tells you on every channel. |
| **Use the clipboard** | Reads what you copied and copies things for you. |
| **Weather** | Current weather and a short forecast for any place — free, no account or API key. |
| **News** | Headlines and topic search from free news feeds (RSS). No account or API key. |
| **Prices** | Stock, currency and crypto prices from free sources. No API key. |
| **Find files** | Searches your PC for files by name or by what's inside them. Read-only. |
| **Reminders** | "Remind me in 2 hours …", "every weekday at 7:30 …", "every month on the 1st …" — reminders that come back to you on the desktop, in the web chat and on WhatsApp, even after a restart. |
| **To-do lists** | "Add 'file taxes' for Friday, high priority" — lists, due dates and priorities; shows what's due and overdue. |
| **Calendar** | Add events, see your agenda and free time, get an alert before something starts. Subscribe to your Google/Outlook/Nextcloud calendar's iCal link (read-only). |
| **Daily briefing** | "How does my day look?" — events, to-dos, reminders, weather, headlines, new mail and alerts in one short message. |
| **Automations** | "Send me the briefing every weekday at 7:30", "when the NAS goes down, tell me" — rules that run on a schedule or when something happens. Anything that normally asks first runs only if you approved it for that rule: approving a rule's own step covers exactly that step; for the model to use a tool in an "ask" step, allow it with `automations.allow`. |
| **Exact maths** | Calculations, unit conversion (km/mi, °C/°F, GB/MB, …) and date maths ("days until 24.12.") worked out exactly, not guessed. |
| **Translate** | Translates text with your own model — offline, nothing leaves the PC. |
| **Desktop notifications** | Pops a notification on your screen to get your attention. |
| **Email** | Reads your inbox and sends mail from your own account. Shows what's unread without marking it read, sorts it into important / can wait / probably spam, drafts replies for you to check, and replies in the same thread (sending runs without asking out of the box; remove `mail.send` and `mail.reply` from `permissions: auto_approve:` to be asked). Can tell you right away when someone you choose writes. The mail server's certificate is checked; for a server with a self-signed certificate set `verify_certificate: false` in `config\modules\mail.yaml`. |
| **PDFs** | Reads text from PDF files and makes simple PDFs from text. |
| **Open programs** | "Open Spotify", "open my budget sheet", "bring Outlook to the front" — starts any installed program, opens files and links, and arranges or closes windows. |
| **Music and volume** | Play/pause, next/previous track and volume, like the media keys. |
| **Your own commands** | Name your own scripts once ("backup-photos", "restart the game server") and run them by name; they ask first unless you say otherwise. Starlight can also run any command line (PowerShell on Windows) — it shows you the exact command and asks every time, and never from the phone. |
| **Word and Excel** | Reads .docx and .xlsx files and makes new ones (letters, lists, tables). |
| **Developer tools** | Git status/log/diff/commit, Docker containers and their logs, and which program uses which network port. Changes ask first. |
| **Research** | "Find out whether …" — searches, reads several pages and writes a short report with numbered sources you can check. |
| **Your own knowledge base** | "What does my rental contract say about pets?" — answers from folders you choose (text, Word, Excel, PDF), naming the file and page. Everything stays on your PC. |
| **Notes** | Quick notes as plain files ("note: …", "add eggs to my shopping note"), searchable and part of your knowledge base. |
| **YouTube summaries** | Summarizes a video or answers a question about it from its captions, with the times it was said. |
| **Talk to it** | Speak instead of typing and hear the answers — 🎤 and 🔊 in the web chat (on your phone too), `/talk` in the terminal. Speech recognition (whisper.cpp) and the voices (Piper, German and English) run on your PC. Install with `Starlight.exe --voice`. |
| **Smart home** | "Turn off the living room lights", "is the front door locked?" — through your Home Assistant. Lights, plugs and scenes switch at once; locks, alarms, doors and heating ask first. |
| **Your home network** | Which devices are on your WiFi (with names you give them), and a warning when an unknown one joins. Only ever looks at your own network. |
| **Backups** | Versioned backups of folders you choose (e.g. to an external drive), checked with checksums; restoring asks first and never overwrites without your say. Schedule them with an automation. |
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

### 4. Choose and let it set everything up

Double-clicking opens the **Starlight window** (no black console window). On the first
start it shows a welcome page:

- **Model.** Three ready-made choices with what each needs. Starlight reads your graphics
  card and marks the one that fits it ("Recommended for your PC"), with the download size
  next to each. Models you already have are marked "Downloaded".
- **Options.** Run the model on the graphics card, speech (talk to it and hear answers),
  the statistics page - and, on Windows, Tesseract for reading text on the screen.
- A line at the bottom says how much space it needs and how much the drive has.

Press **Install and start**. A progress page shows each step, the download with speed and
time left, and a "Show details" box with the output of the installers. How long it takes
depends on your internet connection, mostly for the model.
- Starlight's packages, the model server and the model go into the Starlight folder
  (`.venv`, `tools`, `models`). Python and the Chromium browser go into your user
  profile, where uv and Playwright keep them (`%APPDATA%\uv\python` and
  `%LOCALAPPDATA%\ms-playwright`). Nothing needs administrator rights, except the
  optional Tesseract installer, which asks once.
- If the download is interrupted, start `Starlight.exe` again. It continues where it
  stopped.
- Every download that publishes a checksum is checked against it.
- Your choices are saved in `config\launcher.json`.

### 5. Chat

When the model is loaded, the window changes to **"Starlight is running"** and the chat
opens in your web browser (http://127.0.0.1:8700). Type in the browser window and press
Send. Actions that change something show Approve / Deny buttons.

The window stays as the control panel. It shows live whether the model, the chat, the
statistics page and the kill switch are up, and has buttons for **Open chat**,
**Statistics**, **Stop everything** (the kill switch; the button becomes **Resume**),
**Restart model server**, **Check everything** (the same as `starlight doctor`),
**Open logs folder** and **Settings** (change the model or options; it restarts
Starlight). If something fails, the window says what and offers **Try again**, **Open
logs folder** and **Copy details**. There is a light and a dark look, following Windows.

Prefer the keyboard? Start `Starlight.exe --terminal` for a colored terminal chat in the
window instead.

That's it. **Next time, just double-click `Starlight.exe` again.** It skips the
downloads and goes straight to starting. Closing the Starlight window stops everything
(the model server, the chat and the viewer). The emergency stop is
**Ctrl+Alt+J** or moving the mouse into a screen corner.

The window is only the default when you start `Starlight.exe` plainly. Any option that
is meant for scripts or a terminal (`--yes`, `--ask`, `--terminal`, `--both`,
`--setup-only`, `--update`) uses the text version, which has colors, a progress bar with
speed and time left, and a single "loading the model ..." line. Use `--no-gui` to get the
text version always, or `--gui` to force the window.

### Options

Run `Starlight.exe --help` in a terminal to see every option. The most useful ones:

| Option | What it does |
| --- | --- |
| `--model qwen7b` | Use a ready-made model choice instead of the default (see [A different model](#a-different-model)). |
| `--model-url <link>` | Use another model (a direct download link to a `.gguf` file). |
| `--context 16384` | A smaller context window: uses less video memory. |
| `--variant cpu` | Run without a graphics card (slow). |
| `--terminal` | Chat in the terminal instead of opening the browser. |
| `--no-gui` | Don't open the Starlight window; use the text version. (`--gui` forces the window.) |
| `--both` | Open the browser chat **and** a terminal chat together — both talk to the same Starlight (one conversation, one kill switch). |
| `--voice` | Also install speech: talk to Starlight and hear its answers (about 0.6 GB, Windows; `"voice": true` in `launcher.json` keeps it). |
| `--no-viewer` | Don't start the statistics viewer. |
| `--update` | Download the newest uv and llama.cpp again (after copying your settings and data to `backups`). |
| `--rollback` | Go back to the uv and llama.cpp from before the last `--update` (and, if you say yes, your settings and data). |
| `--setup-only` | Download and set up, then exit without starting. |

To use options every time, put them into `config\launcher.json`, for example:

```json
{ "context": 16384, "viewer": false }
```

#### A different model

Thirty models are ready to pick in the setup window, with `--model <name>`, or with
`"model": "<name>"` in `config\launcher.json`. Each is downloaded the first time it is used.
The setup window recommends one for your graphics card and folds away those too big for it
("Also show ..." brings them back). Tags next to each name tell them apart: *thinks first*,
*quick*, *code*, *tools*, *languages*, *uncensored*. Up and Down move between models. A model
whose download link no longer works is flagged before you pick it, and a downloaded model you
are not using has a *Remove* link to free its space.

For graphics cards with up to 16 GB:

| Name | Model | Download | Video memory |
| --- | --- | --- | --- |
| `qwen14b-uncensored` | Qwen2.5-14B Instruct, uncensored (the default) | 9 GB | about 10 GB |
| `qwen14b` | Qwen2.5-14B Instruct, the original: more careful, cleaner turn endings | 9 GB | about 10 GB |
| `qwen3-14b` | Qwen3-14B: thinks before it answers, better at hard problems, slower to reply | 9 GB | about 10 GB |
| `ministral14b` | Ministral 3 14B Instruct (Mistral, 2025): made for tool use, strong in German and French | 8.2 GB | about 11 GB |
| `gpt-oss-20b` | gpt-oss 20B (OpenAI's open model): quick, only 3.6B of 21B work per word; reasons first | 12.1 GB | about 14 GB |
| `mistral-nemo` | Mistral Nemo 12B Instruct: a little smaller and quicker, good in many languages | 7.5 GB | about 10 GB |
| `gemma4-12b` | Gemma 4 12B (Google): writes well, knows 140 languages | 7.5 GB | about 10 GB |
| `qwen14b-coder` | Qwen2.5-Coder-14B Instruct: best for writing and fixing code | 9 GB | about 10 GB |
| `qwen3.5-9b` | Qwen3.5-9B: the newest Qwen for 8 GB cards, thinks before it answers | 5.7 GB | about 7 GB |
| `qwen7b` | Qwen2.5-7B Instruct: about twice as fast, a little less capable | 4.7 GB | about 5 GB |
| `qwen3-8b` | Qwen3-8B: the newer Qwen for 8 GB cards, thinks before it answers | 5 GB | about 7 GB |
| `gemma4-e4b` | Gemma 4 E4B (Google): small and quick, for 6 to 8 GB cards | 5 GB | about 6 GB |

For graphics cards with 24 to 32 GB ("A3B": only 3B of the weights work on each word, so
these answer about as quickly as a much smaller model):

| Name | Model | Download | Video memory |
| --- | --- | --- | --- |
| `mistral-small-24b` | Mistral Small 3.2 24B: sure with tools, strong in German and French | 14.3 GB | about 18 GB |
| `gemma4-26b` | Gemma 4 26B A4B (Google): quick mixture of experts, 140 languages | 16.8 GB | about 19 GB |
| `qwen3.8-27b` | Qwen3.8 27B: the strongest that fits 24 GB (recommended there); thinks first | 16.5 GB | about 20 GB |
| `qwen3-30b-a3b` | Qwen3 30B A3B Instruct 2507: very quick, answers without thinking first | 18.6 GB | about 21 GB |
| `qwen3-coder-30b` | Qwen3 Coder 30B A3B: the quick 30B for code and tasks with tools | 18.6 GB | about 21 GB |
| `granite4-small` | Granite 4.0 H Small (IBM, 32B, 9B per word): made for tools and long documents | 19.5 GB | about 21 GB |
| `gemma4-31b` | Gemma 4 31B (Google): the best writer here | 18.3 GB | about 22 GB |
| `qwen3.5-35b-a3b` | Qwen3.5 35B A3B: the newer quick mixture of experts; thinks first | 21.5 GB | about 24 GB |
| `qwen3-32b` | Qwen3 32B: all 32B work on every word, thorough and slower; thinks first | 19.8 GB | about 25 GB |

Versions changed by others. *Abliterated* models have the part of the network that makes
them refuse removed; the others are retrained on new data. They refuse less, and they are
not tested by the original makers:

| Name | Model | Download | Video memory |
| --- | --- | --- | --- |
| `qwen3-14b-abliterated` | Qwen3-14B abliterated by mlabonne; thinks before it answers | 9 GB | about 10 GB |
| `josiefied-qwen3-14b` | Josiefied Qwen3-14B v3: abliterated, then fine-tuned to keep tool use | 9 GB | about 10 GB |
| `hermes4-14b` | Hermes 4 14B (Nous Research): Qwen3-14B retrained to follow you | 9 GB | about 10 GB |
| `qwen3-8b-abliterated` | Qwen3-8B abliterated by mlabonne, for 8 GB cards | 5 GB | about 7 GB |
| `dolphin3-8b` | Dolphin 3.0 (Llama 3.1 8B): uncensored retraining for chat, code and tools | 4.9 GB | about 7 GB |
| `gpt-oss-20b-abliterated` | gpt-oss 20B abliterated by huihui-ai; reasons first | 12.1 GB | about 14 GB |
| `dolphin-mistral-24b` | Dolphin Mistral 24B Venice Edition: Mistral Small retrained to be uncensored | 14.3 GB | about 18 GB |
| `qwen3.8-27b-abliterated` | Qwen3.8 27B abliterated by OBLITERATUS; thinks first | 17 GB | about 20 GB |
| `qwen3-30b-a3b-abliterated` | Qwen3 30B A3B abliterated by mlabonne; thinks first | 18.6 GB | about 21 GB |

The video memory includes the 32k-token context; the sizes are approximate. The models that
think or reason first (Qwen3, Qwen3.5, Qwen3.8, gpt-oss): llama.cpp keeps that thinking out
of the answer, but you wait for it, and it is not shown.

Any other GGUF model with tool calling works too, such as Llama 3.1 Instruct. As a rule
of thumb:

| Video memory | Model size at Q4_K_M |
| --- | --- |
| 24–32 GB | up to 32B, or a 30B–35B "A3B" mixture of experts |
| 16 GB | up to 14B (the default), or gpt-oss 20B |
| 8–12 GB | 7B to 8B |
| 6 GB or less | 3B, or `--variant cpu` |

On [Hugging Face](https://huggingface.co), open the model's *Files* tab, copy the
download link of the `.gguf` file and start `Starlight.exe --model-url <link>`. Or put
`"model_url": "<link>"` into `config\launcher.json` (a link wins over a `model` name).
Models you no longer use stay in `models\`; delete their `.gguf` files to free the space.

#### Using LM Studio instead

If you already run models in [LM Studio](https://lmstudio.ai), Starlight can use the
model loaded there. Then nothing is downloaded for the model and Starlight does not start
its own model server.

1. In LM Studio, download a model with tool calling (Qwen2.5 Instruct, for example).
2. Choose **LM Studio** on the setup page, or start `Starlight.exe --lmstudio`. The setup
   page shows whether LM Studio is running and which model it has loaded.

That is usually all. When Starlight starts it gets LM Studio ready:

- **Its server.** If LM Studio's local server is off and LM Studio's `lms` tool is
  installed (LM Studio sets it up), Starlight starts the server. Otherwise turn it on in
  LM Studio's Developer tab (port 1234).
- **The model.** If no model is loaded, Starlight loads the one you named
  (`"lmstudio_model"`), or the only one LM Studio has, with 32768 tokens of context
  (`"context"` in `config\launcher.json`). With several models and none named, load one
  yourself: Starlight then uses whichever is loaded.

The choice is saved in `config\launcher.json` as `"model_server": "lmstudio"`.
`"lmstudio_url"` changes the address (default `http://127.0.0.1:1234`), and
`"lmstudio_model"` names a model to use instead of whichever is loaded. Only one program
should hold the model in video memory: with LM Studio, Starlight's own model server stays
off, and LM Studio decides whether the model runs on the graphics card.

What Starlight does for you:

- **The model you load is used.** Starlight asks LM Studio which model is loaded on every
  request, so switching models in LM Studio needs no restart.
- **The context fits.** At every start, Starlight plans its prompts with no more context
  than LM Studio has loaded (when LM Studio reports it), so prompts are not refused.
- **Problems are named.** If LM Studio refuses a request, its own reason is shown (for
  example, no model loaded). The status window, and **Check everything** (`starlight
  doctor`), say whether LM Studio answers, which model is loaded, whether its context is
  long enough, and whether the model is trained for tool use (Starlight's tools need it).
- **Old and new LM Studio.** Starlight reads LM Studio's current REST API, and the older
  one of versions before it.
- **Memories can use it too.** For memory search by meaning, load LM Studio's
  `nomic-embed-text` embedding model and set `memory: embedder: provider: lmstudio` in
  `config\starlight.local.yaml`.

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

Your `memory`, `skills`, `data`, `logs`, `models` and `tools` folders, your
`config\starlight.local.yaml` and the permission rules you set from the chat
(`config\permissions.chat.yaml`) aren't part of the download, so they stay as they are.
The other files in `config` are overwritten. That includes what Starlight saves when you set
something up from the chat: `config\servers.yaml` and the files in `config\modules` (your
mail account, backup sets, document folders, named commands, calendar feeds, logins, news
feeds, Home Assistant address, automations). Copy the `config` folder before you unpack, and
put your entries back into the new files afterwards. Settings of your own, such as the
model or the output folder, belong in `config\starlight.local.yaml`, which an update never
touches. Files Starlight saved for you are in `Documents\Starlight`, outside the Starlight
folder, so updates never touch them.

**A way back.** `Starlight.exe --update` (new uv and llama.cpp) first copies `config`, `data`
and `memory` to `backups\update-<date and time>` (the newest three copies are kept), and keeps
the old uv and llama.cpp in `tools\previous`. If the new download fails, the old one stays in
place. If the new one doesn't work, run `Starlight.exe --rollback`: it switches back and offers
to put your settings and data back from the copy too (it asks; the default is no). Running it
again goes forward again.

### Uninstalling

1. If you turned on starting at logon, run `starlight service uninstall` first (see
   [Command reference](#command-reference) for how to run commands).
2. Delete the Starlight folder.

Passwords and keys you stored are in the Windows Credential Manager under "starlight".
Remove them there, or with `starlight vault delete <key>` before deleting the folder. Files
Starlight saved for you stay in `Documents\Starlight` until you delete that folder too.

Python and the Chromium browser live in your user profile (the package cache sits inside the
Starlight folder as `.uv-cache`, so it goes when that folder does). To remove the rest, delete
these folders. Paste each one into the Explorer address bar to find it. Skip any that another
program of yours still uses.
- `%APPDATA%\uv`: Python
- `%LOCALAPPDATA%\ms-playwright`: Chromium

---

## Using Starlight

**Two ways to chat.** When you start `Starlight.exe`, it opens a **web chat in your
browser** (http://127.0.0.1:8700): a normal chat window where answers stream in, actions
you need to approve appear with **Approve / Deny** buttons, and a sidebar shows what's
allowed. Prefer the keyboard? Start it with `--terminal` (or run `starlight run`) for a
colored terminal chat. Both use the same assistant, settings and safeguards.

**The web chat.** Your conversations are saved: the list on the left reopens, renames,
searches and deletes them (the terminal continues the most recent one). Drop pictures or
documents (PDF, Word, Excel, text) onto the chat, or press 📎, to send them with your
message. Every answer has **Copy**, and the latest one **Regenerate** (asks the same
question again and replaces that answer); under it you see how fast it was written
(tokens per second) and how long it took. ⤓ next to a conversation downloads it as a
Markdown file. **Settings** shows what each tool may do
(change it with one click), turns modules on and off, and pairs your phone. **Memory**
lets you read, correct and delete what Starlight remembers, and approve or undo the skills
it learned. ◐ switches between light, dark and your system's theme.

**On your phone.** See [Use Starlight from your phone](#use-starlight-from-your-phone).

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

**Scanning and your servers.** Starlight can ping and check any host (`any_host: true` in
`config\modules\network.yaml`; set it to `false` to limit it to `config\servers.yaml`), and
a scan first asks you to confirm you own the host or are authorized to test it. It gathers
information (ports, services, certificates); it never attacks. A host you scan or ping
can't flood your machine back — responses are capped.

**Seeing what went wrong.** Ask "show me recent errors" (or run `starlight logs show`) to
read Starlight's own problems in plain language, without opening a log file.

**When Starlight asks first.** Actions that are not in `permissions: auto_approve:` ask
before they run - out of the box: any command line, shutdown, restart and sleep, your
named commands, replacing a file, and granting access (see *Safety and privacy*):

```
Starlight wants to run commands.shell({"command": "Get-ChildItem D:\\Fotos"})
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
| `/new` (or `/clear`) | Start a new conversation (the old one stays saved). |
| `/talk [seconds]` | Say your request into the microphone (needs voice, see `--voice`). |
| `/tools` | Show what Starlight can use. |
| `/modules` | Show which modules are on or off. |
| `/kill`, `/resume` | Stop everything, and continue. |
| `/quit` | Leave. |

**Statistics.** Open http://127.0.0.1:8765 while Starlight runs. It shows tasks, errors,
model speed, GPU use and memory size.

**Memory.** Everything Starlight remembers is a text file in the `memory` folder. Open,
correct or delete any of them; Starlight picks up the change. `memory/profile.md` is your
profile: your name, the language you want answers in, where you live, your time zone and
preferences. Starlight fills it in when you mention these ("I'm Paul, I live in Vienna"),
and every request uses it, together with today's date and time (so "the weather" means
the weather at home).

Starlight tidies up after itself, so it can run for months: old statistics (30 days),
cached web pages (a day) and, beyond the newest 60, the automatic conversation summaries
are deleted. The 60 is `memory.summary_keep` in `config/starlight.yaml` (`0` keeps all);
what you told it to remember, and your profile, are never deleted.

**Files.** Ask Starlight to save what it made ("save it as a file", "make a CSV of
that"), and it saves it in the **Starlight** folder in your Documents, then tells you
where:
- **No question in that folder.** Saving there just happens. Programs it writes go
  into its `code` subfolder.
- **Never overwritten silently.** A second `letter.txt` becomes `letter (2).txt`, and
  replacing a file asks you first.
- **Elsewhere, too.** "Save it on my Desktop" saves a new file there without asking
  (`output: write_anywhere:`); replacing or adding to a file there asks first, and so does
  a script type (`.bat`, `.ps1`, ...).
- **Word, Excel and PDF.** Ask for a Word document, an Excel sheet or a PDF and it makes a
  real `.docx`, `.xlsx` or `.pdf`. Other files are text: `.txt`, `.md`, `.csv`, `.html`,
  code and similar.

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

## Use Starlight from your phone

Starlight can be used from your phone or laptop anywhere, privately, through
**[Tailscale](https://tailscale.com)** (free for personal use). Tailscale connects your
devices in a private network: **no port is opened on your router**, and nobody outside
your own devices can even reach the page. On top of that, each device has to be **paired**
once with a code shown on your PC.

1. Install Tailscale on this PC and on your phone, and sign in on both with the same
   account. In the Tailscale admin console, under **DNS**, turn on **MagicDNS** and
   **HTTPS certificates**.
2. On the PC, run `starlight remote enable`. It prints your private address, like
   `https://my-pc.tail1234.ts.net/`. Restart Starlight.
3. On the PC, open **Settings → Show a pairing code** (or run `starlight remote pair`).
4. On the phone, open that address and enter the code. Add the page to your home screen
   to use it like an app.

What is different on the phone:
- Actions ask on the phone exactly when they would ask at the PC. To make some always ask
  on the phone, list them under `web: remote: confirm:` in the config (for example
  `["desktop.*", "mail.send"]`).
- Some things only change **on the PC**: permission rules, modules, paired devices,
  skills, and releasing the kill switch (the phone can engage it). From the phone,
  Starlight also won't loosen its own rules, add secrets, servers, logins, commands,
  automations, backups or knowledge-base folders — even if asked (`web: remote: blocked:`).
- The chat always listens on this PC only (`127.0.0.1`), and no other website can show it
  inside a frame. Tailscale is the only way in from outside.
- A wrong code is counted; after 5 wrong tries that device waits 15 minutes. A code works
  once and for 5 minutes. Pairing a new device sends you a notice everywhere.

Remove a device in **Settings** (or `starlight remote revoke ID`), and turn the whole thing
off with `starlight remote disable`.

## Safety and privacy

- **Everything runs on your PC.** The model, your conversations, your memory and
  screenshots never leave it. Only web pages you ask for, and WhatsApp if you set it up,
  use the internet.
- **You decide what runs.** Every action is *allowed*, *asks first* or *blocked*. You can
  change this in `config\starlight.yaml` under `permissions`. *Blocked* always wins; a tool
  you allow by its exact name (`desktop.click`) runs even while a broader pattern
  (`desktop.*`) asks.
- **What runs without asking, out of the box.** Desktop clicks and typing, running code
  (in the sandbox), closing programs, network tasks (HTTP, SSH, TCP and WebSocket to any
  server), sending or answering email, and saving new files in any folder. These are
  listed under `permissions: auto_approve:` (and `output: write_anywhere:`) — delete a
  line and that action asks again. Still asking: any command line (it shows you the exact
  command), shutdown, restart and sleep, your named commands, replacing, adding to or
  reading a file outside the Starlight folder, and granting access. **Be aware:** a web page or an email the model reads could try to talk
  it into one of these actions, and nothing stops to ask. The kill switch (Ctrl+Alt+J or
  a screen corner) and `logs\audit.log` remain.
- **Passwords stay out of files.** Passwords and keys are kept in the Windows Credential
  Manager, never in config files. They are hidden in every log line, and
  `starlight logs scan` checks for leaks.
- **Any server, your logins.** Starlight may ping, check and contact any host
  (`any_host: true` in `config\modules\network.yaml`; `false` limits it to the servers in
  `config\servers.yaml` again). SSH works with the logins kept there. A scan of a host
  you haven't allowed still asks you to confirm you're authorized.
- **Nothing can flood your machine.** When Starlight fetches, pings or scans, responses
  are capped in size and time and connections are limited, so a hostile server can't
  turn a request into a flood against you. Scanning is reconnaissance only — it never
  attacks, and it does one host at a time.
- **Files stay where you expect.** Starlight saves new files where you say, in its folder
  in your Documents by default. Replacing or adding to an existing file outside that
  folder, and reading one, asks first. Its own install folder, which holds its settings
  and data, is off-limits, so it can't change its own permissions that way (a command
  line could, which is why every command line asks and the phone can't run them).
- **The model is yours, unchanged.** Starlight sends your messages to the local model
  you chose and returns its answer as-is — it adds no filter of its own. How the model
  behaves is down to the model file, which runs entirely on your PC.
- **Your phone, privately.** Remote use goes only through Tailscale (no open router
  port), every device is paired with a one-time code, and the rules can only be changed
  at the PC.
  See [Use Starlight from your phone](#use-starlight-from-your-phone).
- **Everything is logged.** Every action Starlight takes is recorded in
  `logs\audit.log`.

## Troubleshooting

**First, run `starlight doctor`** (or `uv run starlight doctor`). It checks the model and
its server, the settings files, free disk space, whether Starlight can write its data
folders, the password store and the modules, and says in plain words what is wrong and
what to try. It changes nothing.

**A mistake in a settings file** is explained with the file, the line and the setting, for
example `config\starlight.yaml, line 5: web.prot - 'prot' is not a setting that exists here -
did you mean 'port'?`. Fix that line (or delete it to use the default). A YAML slip such as
a tab instead of spaces shows the line and a hint.

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

If the model server stops while you are chatting (a graphics driver hiccup, out of video
memory), Starlight.exe starts it again by itself - up to four times in ten minutes - and
says so in its window; the chat works again once the model is loaded. If the server stops
sending in the middle of an answer for 90 seconds, you get the part that came, with a note.

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
<summary><b>Answers run on, invent your next message, or show raw <code>```json</code> tool calls</b></summary>

Some model files (often merged or "uncensored" fine-tunes) don't mark their end-of-turn
token as a stop, or write tool calls as text. Starlight handles both: it ends the answer at
the end-of-turn marker, and runs a tool call written as a ```json block or `<tool_call>` tag
instead of showing it. If a model still rambles, try another model file, or lower
`temperature` in `config\starlight.yaml` under `model: options:`.
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
| `starlight connect` | A terminal chat that joins a running `starlight web` backend — the same brain as the browser (`Starlight.exe --both` opens both together). |
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
| `starlight doctor` | Check that everything works and say what to fix. |
| `starlight logs show` / `logs scan` | Show recent errors in plain language / check the logs for leaked passwords. |
| `starlight remote enable / disable / status` | Use the web chat from your phone through Tailscale. |
| `starlight remote pair / devices / revoke ID` | Pair a device with a one-time code, list them, remove one. |

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
