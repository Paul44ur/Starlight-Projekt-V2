# The overlay: test on the Windows PC

Nine checks on the real PC (Windows 11, two 1080p monitors at 100 %). They cover what the
tests in the container cannot: click-through, focus, hiding from screenshots, scaling,
CPU, the real microphone, real clicks and Windows' UI Automation (the COM fix).
Write each result (ok, or what happened instead) into section 9 of `docs/plan-overlay.md`.

**Before you start**

- Starlight runs from the newest `starlight-v2.5`, started with Starlight.exe (it
  installs the overlay's packages).
- Settings → Voice: "Hey Starlight" is on, the microphone works.
- Keep `data/modules/overlay/overlay.log` and `logs/` at hand if something goes wrong.

| # | Do this | What should happen |
|---|---|---|
| 1 | Settings → Overlay: turn on **Show the overlay**. Then click, scroll and type in a few windows, also right under the orb. | A dim orb at the top of monitor 1. Every click goes to the window under it. No taskbar entry, no Alt+Tab entry. **Show a test point** draws a line to the page's heading for 3 s. |
| 2 | Open Explorer on monitor 1. Say "Hey Starlight, wo sind die Einstellungen?" (or another button you see). | Within about 2 s: a line from the orb to the right spot, a solid frame, "Hier ist es" spoken and shown. A solid frame means UI Automation found it (the COM fix works). |
| 3 | The same with the window on monitor 2. Then, only if you want to check scaling: set monitor 2 to 125 % (Settings → Display), restart Starlight, ask again, and set it back to 100 %. | The line goes across to monitor 2 and the frame sits exactly on the control, also at 125 %. |
| 4 | Open Chrome's ⋮ menu and ask for something only drawn as an icon (for example "wo ist das Zahnrad?" on a page with a gear icon). Note how long it takes. | A dashed frame and "Vielleicht hier" (OCR or the vision model found it). Write down the seconds. |
| 5 | Open Notepad and keep typing. Ask "wo ist Datei?", then "Klick drauf". When the card and the question appear, say "nein". Then the same again and say "ja". Last, once more and click **Ja** on the card. | Your typing stays in Notepad when the card appears (the card never takes the focus). "nein": nothing is clicked. "ja" and the click on **Ja**: it clicks the centre of the frame, the red banner shows. If it clicks **without** asking, that is allowed (the model decides, section 9); note which it did. |
| 6 | Ask for a point, and press **Ctrl+Alt+J** while the line shows. | Line, frame and caption disappear at once, the orb goes to sleep. "Wo ist …" is refused until you resume. |
| 7 | While a line shows, take a screenshot with **Win+Shift+S**. | The line, frame and orb are not in the screenshot. |
| 8 | Start a game in exclusive fullscreen, then leave it. | The orb is not drawn over the game (expected). Nothing freezes; the orb is back afterwards. |
| 9 | Task Manager → Details: find the `python`/`pythonw` process of the overlay while the orb is still for a minute. | About 0 % CPU. Write down the memory it uses. |
