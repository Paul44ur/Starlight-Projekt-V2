# Plan: improvements and additions, round 2

The user asked for more improvements and additions after the Night Sky look was finished
(2026-11-14), with a plan first and helper models doing the work. This plan has three
clean-ups that were left open, six additions, and a review. One block is one commit. The lead
writes each brief, reviews the diff, runs the full suite and merges.

## Status

| Block | What | Needs | State |
|---|---|---|---|
| R1 | German for what is still English: Activity entries, automation templates, skill cards | – | **done** (ce2bd7a) |
| R2 | Phone width (360 px): three layout faults | R1 | **done** (744b925) |
| R3 | The statistics page in the Night Sky look, and in German | – | **done** (dc6c9dd) |
| R4 | Drafts: the message box keeps what you typed, per conversation | – | **done** (61bb42c) |
| R5 | Search shows where it found your words, and jumps there | – | **done** (fc126ca) |
| R6 | The conversation list grouped by time (Today, Yesterday, …) | R5 | **done** (658978f) |
| R7 | Starter suggestions on a new conversation | R4 | **done** (6ed95a6) |
| R8 | Automations page: when each rule last ran, and how it went | R1 | **done** (e8ec17c) |
| R9 | A Today page: your day at a glance | R1-R8 | **done** (2f4e07f) |
| R10 | Review: full suite, screenshots, README, release notes | R1-R9 | **done** (release notes and review, 2026-11-14) |

Blocks without an open "Needs" run side by side: first R1, R3, R4, R5; then R2, R6, R7, R8;
then R9 alone (it adds a link to every page); then R10.

## 1. The clean-ups

**R1. German leftovers.** With German chosen, these still show English:
- **Activity:** the kind of an entry (the small pill: tool, refused, approved, failed, …)
  and the entry's text, which the server builds.
- **Automations:** the names and descriptions of the ready-made rules.
- **Memory:** a skill card's state (approved, draft, rejected) and its buttons.

The rule: what is on disk stays as it is. The activity log is the user's record and stays in
English. The page translates what it shows, in `i18n.js`: fixed words in `DE`, texts with a
changing part in `PATTERNS`. Names of tools, files and servers, and anything the user typed,
are never translated. `tests/test_german_everywhere.py` gets these states.

**R2. Phone width.** Three faults at 360 px, found in the Night Sky review:
- Automations: the hint text inside the fields is cut off.
- Settings: the switches do not line up with their labels.
- Notes: a row of the list is cut in half at the bottom.

Each gets a test at 360 px that fails before the fix.

**R3. The statistics page.** The page at http://127.0.0.1:8765 (`starlight/viewer`) is the
last one in the old look, and it is English only. It gets the Night Sky look from the same
`style.css` (served by the viewer itself, so there is one source), the star and the heading
font, a night/day button, numbers in `.num`, chart colours from the tokens, and German
through `i18n.js`. What it shows and how it is fetched stay the same.

## 2. The additions

**R4. Drafts.** Text you typed and did not send is kept for its conversation. It comes back
when you open the conversation again, also after a restart. Sending or emptying the box
removes it; deleting the conversation removes it too. It is kept in the browser
(`localStorage`), not on the server. A private conversation's draft is only kept in the
page's memory, never on disk. Attached files are not part of a draft.

**R5. Search hits.** The search already looks into the messages, but the list only shows
the title. A conversation found by its content now shows one line of that message under the
title, with your words marked. Opening it scrolls to that message and marks it for a moment.
A conversation found by its title looks as before.

**R6. Time groups.** Under **Pinned**, the conversations are grouped as **Today**,
**Yesterday**, **Last 7 days** and **Older**, in place of the single **Recent**. Folders,
the archive and search results keep working as they do now.

**R7. Starter suggestions.** A new, empty conversation shows up to four things to ask, taken
from the modules that are on ("How does my day look?", "What's the weather this week?",
"Remind me in 2 hours to …"). Pressing one puts it in the message box; it is not sent, so it
can be changed first. With German chosen, the suggestions are German.

**R8. Automation runs.** Each rule on the Automations page shows when it last ran and how it
went (done, failed with the reason, or waiting for your OK). **History** on a rule opens its
last ten runs. The module already keeps this (`history.jsonl`); the page does not show it yet.

**R9. A Today page.** A new page, **Today**, shows your day as cards: what waits for your
OK, the agenda, to-dos that are due or overdue, today's reminders and the weather. A module
that is off has no card. Nothing on it changes anything; a card links to where the thing
lives. One gold button puts "How does my day look?" in the chat's message box (not sent).
The page is linked from every page and from the quick switcher. New mail is left out:
opening a page should not log in to the mail server (the briefing still reads it). A card
that needs the network (the weather) goes through the dispatcher, so the kill switch, the
limits and the activity log apply, and is kept for half an hour.

## 3. Rules for every block

- Read `docs/guide-for-helpers.md` first, and `docs/plan-ui-2.md` for the look.
- A behaviour change gets a test that fails on the old code. Existing tests pass unchanged,
  unless the block changes what they check; say so in the report.
- New texts need German entries, in ONE block per page at the place the brief names.
- Colours, spacing and radius only through the tokens. Gold only for the one main action.
- Screenshots of what changed: night, day, German, and 360 px wide.
- Safety first: nothing here changes the user's files; a draft and a suggestion never send
  anything by themselves; outside content stays untrusted; settings change only at the PC.
- No new dependencies.
