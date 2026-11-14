## Starlight V2 1.00.00

The first public version of Starlight: a private AI assistant that runs entirely on your own
PC. It uses a language model on your own graphics card, with no account, no subscription and
no token limits. It remembers what you tell it, browses the web, checks your servers, reads
the screen, writes and tests code, and answers on WhatsApp. It asks before anything risky,
and one key press (Ctrl+Alt+J) stops it.

### Get started (Windows 11)
1. Download the **`Starlight-...-windows.zip`** file below and unpack it into a folder of your own,
   for example `C:\Starlight`. Avoid `C:\Program Files` and OneDrive folders.
2. Double-click **`Starlight.exe`**. Windows may say "Windows protected your PC" because the
   program is not signed yet: **More info**, then **Run anyway**.
3. Pick a model (the one that fits your graphics card is marked) and press **Install and
   start**. The first setup downloads about 12 GB; no administrator rights are needed.
4. The chat opens in Starlight's own window. If that window cannot open on your PC, the chat
   opens in your browser at http://127.0.0.1:8700 instead.

The default model needs 16 GB of video memory; smaller models are offered during setup. See the
[README](https://github.com/Paul44ur/Starlight-Projekt-V2#readme) for everything else.

### The chat window
- Starlight's own window has its own name and icon in the taskbar, with no address bar and no
  tabs. It uses WebView2, which comes with Windows 11, and it remembers its size.
- A tray icon is new: closing the launcher window keeps Starlight running next to the clock. Its
  menu has Open, Kill switch and Quit, and a notification shows there when an answer is ready.
  So far it has been tested with stand-ins only, not on many real PCs. You can turn it off in
  the launcher's settings.
- The chat is laid out like a desktop app. The sidebar on the left has **New conversation**, the
  pages (Today, Notes, Memory, Activity, Automations, Settings) and your conversations: pinned
  ones first, then by day (Today, Yesterday, Last 7 days, Older). You can hide the sidebar.
- What you typed and did not send stays with its conversation and is there when you come back.
  A new conversation offers a few things to ask; pressing one puts it in the message box.
- A search also looks into what was said. It shows the line with your words and jumps to that
  message.
- The top bar shows the name of the open conversation. Its menu renames, pins, files, archives,
  downloads or deletes the conversation.
- The **Context** meter shows how full the model's context is for the conversation.
- Questions such as "Delete this conversation?" appear in Starlight's own look, not as browser
  boxes.

### Also new
- **A new look for every page:** Night Sky. Dark by default, with an equal day look; gold
  only for the star and the one main action; a details pane in the chat; keys shown next to
  their buttons. Nothing about how the pages work has changed.
- **When an answer is ready** and Starlight is in the background, its taskbar button lights up
  and a notification shows. Settings has the switch.
- **Ctrl+K** opens a box to jump to a conversation, a note, a page or a command.
- **Settings, Model:** pick another model that is already installed, and the context size. It
  applies after a restart; new models are still downloaded in the Starlight window.
- **Settings, Backup and restore:** saves your conversations, notes, memory, skills and settings
  into one file. Passwords and paired devices are not in it, and the file is not encrypted. A
  restore replaces what you have; a copy of it is saved first, and it is applied when
  Starlight starts next.

- **The logo is flat now:** a gold star on the night colour, without the purple gradient and
  the light rays. Starlight.exe, the windows, the tray and a phone's home screen show it.
- **A Today page** shows your day as cards: what waits for your OK, the agenda, to-dos that
  are due, today's reminders and the weather. Nothing on it changes anything.
- **Automations:** each rule shows when it last ran and how it went, and **History** opens its
  last ten runs. A ready-made rule you add on a German page is saved in German.
- **Statistics** has the same look as the other pages and is in German too.
- **German:** the Activity page, the ready-made automations and the skill cards no longer show
  English.
- **On a phone:** the Notes card shows whole rows, the Automations form's hints can be read,
  and the switches in Settings line up.

### Good to know
- It asks before risky actions. Some, such as sending mail or clicking on the desktop, run
  without asking out of the box; the README lists them and how to turn the questions back on.
- Mail checks the server's certificate. For a server with a self-signed certificate set
  `verify_certificate: false` in `config\modules\mail.yaml`.
- The setup window, the logo and the update/rollback buttons have so far been tested on a clean
  Windows machine in CI, not on many real PCs. Reports are welcome.
- Starlight's own window is new and has so far been tested with stand-ins, not on many real
  PCs. If a browser opens instead, the launcher says why. To always use the browser, turn off
  **Open the chat in Starlight's own window** in the launcher's settings. Reports are welcome.
- `Starlight.exe --update` keeps a snapshot of your settings, data and memory, and
  `Starlight.exe --rollback` returns to the previous version.
