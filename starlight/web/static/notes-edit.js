// Writing (B6), the journal strip (C4), Ask Starlight (C3), links (C1), voice memos (D2), earlier versions, and the start.
// Part of the Notes page (notes.html), which loads its parts in this order: notes-page.js,
// notes-tasks.js, notes-info.js, notes-views.js, notes-find.js, notes-edit.js. They are
// plain scripts: what one declares at the top, the others use.

// -- editing (block B6) ---------------------------------------------------------------------
// The editor (notes-editor.js, on the shipped CodeMirror bundle) is loaded on the first edit.
// A change is saved one second after you stop typing, with the stamp the note was read
// with: if it changed elsewhere meanwhile, nothing is overwritten and you choose.

let kit = null;
const edit = { editor: null, dirty: false, timer: null, saving: null, first: true, quiet: false };
const TOOLS = [
  ["bold", "B", "Bold (Ctrl+B)", "b"], ["italic", "I", "Italic (Ctrl+I)", "i"], ["heading", "H", "Heading", ""],
  ["list", "•", "Bullet list", ""], ["task", "", "Task (Ctrl+Enter ticks it)", "", "tasks"],
  ["link", "[[ ]]", "Link to a note", ""], ["picture", "", "Picture", "", "picture"],
];

// Live preview (block G4): the Markdown marks hide on the lines the cursor is not on. On
// unless switched off with the toolbar's eye button (remembered in this browser).
function previewOn(set) {
  try {
    if (set !== undefined) localStorage.setItem("starlight.editor.preview", set ? "on" : "off");
    return localStorage.getItem("starlight.editor.preview") !== "off";
  } catch (e) { return set ?? true; }
}

function previewButton() {
  const button = el("button", "tool live-preview"); setIcon(button, "eye");
  button.type = "button";
  button.dataset.tool = "preview";
  const show = () => {
    const on = previewOn();
    button.setAttribute("aria-pressed", String(on));
    button.title = t(on ? "Live preview: the marks show only on the line you are on (click for all)"
      : "All the Markdown marks show (click for live preview)");
  };
  button.setAttribute("aria-label", t("Live preview"));
  button.onmousedown = (event) => event.preventDefault();  // (the text keeps its selection)
  button.onclick = () => { const on = !previewOn(); previewOn(on); edit.editor.preview(on); show(); };
  show();
  return button;
}

function showSaveState(text, bad = false) {
  const shown = document.getElementById("save-state");
  if (!shown) return;
  shown.textContent = t(text);
  shown.classList.toggle("bad", bad);
}

async function startEditing() {
  const note = state.current;
  if (!note || edit.editor) return;
  kit ||= await import("/notes-editor.js");
  const input = el("input", "title-input");
  input.value = note.title;
  input.setAttribute("aria-label", t("Title"));
  input.setAttribute("translate", "no");
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); edit.editor.focus(); }
    else if (event.key === "Escape") { input.value = state.current.title; edit.editor.focus(); }
  });
  input.addEventListener("change", () => rename(input));
  state.parts.title.replaceWith(input);
  state.parts.title = input;

  const bar = el("div", "toolbar");
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", t("Formatting"));
  for (const [name, label, tip, cls, picture] of TOOLS) {
    const button = el("button", "tool " + cls, label);
    if (picture) setIcon(button, picture);  // (a line icon where a letter would not do)
    button.type = "button"; button.title = t(tip); button.setAttribute("aria-label", t(tip));
    button.dataset.tool = name;
    button.onmousedown = (event) => event.preventDefault();  // (the text keeps its selection)
    button.onclick = () => (name === "picture" ? pickPicture() : edit.editor.run(name));
    bar.appendChild(button);
  }
  bar.append(el("span", "sep"), previewButton());
  const saved = bar.appendChild(el("span", "save-state"));
  saved.id = "save-state";
  saved.setAttribute("role", "status");
  const box = el("div", "editor");
  box.setAttribute("translate", "no");
  state.parts.body.replaceWith(bar, box);
  state.parts.body = box;
  state.parts.bar = bar;

  edit.first = true; edit.dirty = false;
  edit.editor = kit.createEditor(box, {
    text: note.text,
    onChange: changed,
    pictures: addPictures,
    pickPicture,
    notice: toast,
    say: t,
    pictureSrc: notePictureSrc,  // (render.js: a picture's address inside the Starlight folder)
    placeholder: t("Write here. Type / for headings, tasks, tables and more."),
    preview: previewOn(),
    noteNames: () => state.notes.flatMap(n => [
      { name: n.name, detail: folderOf(n.path) },
      ...n.aliases.map(alias => ({ name: alias, detail: "→ " + n.name })),
    ]),
    tagNames: () => (state.tags || []).map(entry => ({ name: entry.tag, count: entry.count })),
    propertyNames: () => (state.propertyNames || []).map(p => ({ name: p.name, count: p.count })),
  });
  document.getElementById("edit-toggle").textContent = t("✓ Done");
  document.body.classList.add("editing");
  showSaveState("Saved");
  edit.editor.focus();
}

function changed() {
  if (edit.quiet) return;
  edit.dirty = true;
  showSaveState("Editing…");
  clearTimeout(edit.timer);
  edit.timer = setTimeout(save, 1000);
}

// Save now (if something changed). True when everything is saved.
async function save() {
  clearTimeout(edit.timer);
  while (edit.saving) await edit.saving;
  if (!edit.dirty || !edit.editor) return !edit.dirty;
  const note = state.current, text = edit.editor.text(), mark = edit.editor.mark();
  edit.dirty = false;
  showSaveState("Saving…");
  edit.saving = (async () => {
    try {
      const response = await fetch("/api/note?" + new URLSearchParams({ path: note.path }), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, stamp: note.stamp, first: edit.first }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 409 && data.detail && typeof data.detail === "object") {
        edit.dirty = true;
        showSaveState("Not saved: changed elsewhere", true);
        conflict(data.detail);
        return false;
      }
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
      note.stamp = data.stamp; note.text = text; edit.first = false;
      if (typeof data.text === "string") {  // saved with ISO dates, a repeating task's next one
        note.text = data.text;
        if (edit.editor) {  // (also when typing went on: else the next save would undo them)
          edit.quiet = true;
          try {
            if (edit.editor.text() === text) edit.editor.update(data.text);
            else edit.editor.rebase(mark, data.text);
          } finally { edit.quiet = false; }
        }
      }
      showSaveState(edit.dirty ? "Editing…" : "Saved");
      return true;
    } catch (e) {
      edit.dirty = true;
      showSaveState(t("Not saved:") + " " + e.message, true);
      return false;
    }
  })();
  const ok = await edit.saving;
  edit.saving = null;
  return ok;
}

// Leave the editor, saving first. False (staying) when it could not be saved.
async function stopEditing() {
  if (state.board) {  // (a board's last change is saved before leaving it)
    if (state.board.dirty && !(await state.board.save())) {
      toast(t("The board is not saved yet. Try again in a moment."));
      return false;
    }
    state.board = null;
    document.body.classList.remove("board-open");
  }
  if (!edit.editor) return true;
  if (!(await save())) {
    toast(t("Not saved yet: your text is still here. Try again in a moment."));
    return false;
  }
  edit.editor.destroy();
  edit.editor = null;
  document.body.classList.remove("editing");
  return true;
}

async function toggleEdit() {
  if (!state.current) return;
  if (!edit.editor) return startEditing();
  if (await stopEditing()) await openNote(state.current.path);
}

function conflict(detail) {
  const dialog = document.getElementById("conflict");
  document.getElementById("theirs").textContent = detail.theirs;
  dialog.hidden = false;
  const replace = (text) => { edit.quiet = true; edit.editor.setText(text); edit.quiet = false; };
  const close = () => { dialog.hidden = true; edit.editor.focus(); };
  document.getElementById("keep-mine").onclick = () => {
    state.current.stamp = detail.stamp; edit.dirty = true; close(); save();
  };
  document.getElementById("take-theirs").onclick = () => {
    state.current.stamp = detail.stamp; state.current.text = detail.theirs;
    replace(detail.theirs); edit.dirty = false; close(); showSaveState("Saved");
  };
  document.getElementById("keep-both").onclick = () => {
    state.current.stamp = detail.stamp;
    replace(detail.theirs.replace(/\s+$/, "") + "\n\n---\n\n" + edit.editor.text());
    edit.dirty = true; close(); save();
  };
  document.getElementById("keep-mine").focus();
}

async function rename(input) {
  const note = state.current, title = input.value.trim();
  if (!title || title === note.title) { input.value = note.title; return; }
  if (!(await save())) return;
  const others = new Set(note.backlinks.map(b => b.from).filter(p => p !== note.path)).size;
  // (Esc, or "Leave the links": the note is renamed, the links in other notes stay.)
  const fixLinks = !others || await askYesNo(t("Also change the links to this note in other notes? Notes that change:")
    + " " + others + ". " + t("Each keeps its earlier version."), { yes: "Change the links", no: "Leave the links" });
  try {
    const answer = await api("/api/note/rename",
      { method: "POST", body: { path: note.path, title, fix_links: fixLinks } });
    if (answer.updated && answer.updated.length) {
      toast(t("Renamed. Links changed in other notes:") + " " + answer.updated.length);
    }
    note.path = answer.path; note.title = answer.title; note.stamp = answer.stamp;
    input.value = answer.title;
    history.replaceState({ path: note.path }, "", "/notes?" + new URLSearchParams({ path: note.path }));
    document.title = note.title + " · Starlight";
    remember(note.path);
    await loadList();
    markCurrent();
  } catch (e) {
    toast(e.message);
    input.value = note.title;
  }
}

async function newNote(template = "") {
  if (!(await stopEditing())) return;
  const folder = state.current ? folderOf(state.current.path) : "";
  const title = template ? template + " " + today() : t("Untitled");
  try {
    const made = await api("/api/note", { method: "POST", body: {
      folder: folder && folder !== "Journal" ? folder : "Notes", title, template,
      lang: window.starlightLanguage || "en" } });
    await loadList();
    history.pushState({ path: made.path }, "", "/notes?" + new URLSearchParams({ path: made.path }));
    await openNote(made.path, null, true);
    const input = document.querySelector(".title-input");
    if (input) { input.focus(); input.select(); }
  } catch (e) { toast(e.message); }
}

async function deleteNote() {
  const note = state.current;
  if (!note || !(await askYesNo(t("Move this note to the trash?") + "\n\n" + t("You can bring it back from the trash."),
    { yes: "Move to the trash" }))) return;
  if (edit.editor) {
    if (!(await save())) {  // the last changes first: never delete over unsaved text
      toast(t("Not saved yet: your text is still here. Try again in a moment."));
      return;
    }
    edit.editor.destroy(); edit.editor = null;
    document.body.classList.remove("editing");
  }
  try {
    await api("/api/note?" + new URLSearchParams({ path: note.path }), { method: "DELETE" });
  } catch (e) { toast(e.message); return; }
  toast(t("Moved to the trash"));
  await loadList();
  go({ view: "trash" });
}

async function showTrash() {
  clearInfo(); state.view = "trash"; markCurrent();
  column.replaceChildren(el("h1", "title", t("Trash")));
  const data = await api("/api/notes/trash");
  if (!data.notes.length) {
    column.appendChild(el("div", "soon", t("The trash is empty. Deleted notes wait here until you bring them back.")));
    return;
  }
  column.appendChild(el("p", "dim", t("Deleted notes. Bring one back as it was.")));
  const list = column.appendChild(el("ul", "cards"));
  for (const item of data.notes) {
    const row = list.appendChild(el("li", "trash-row"));
    const card = row.appendChild(el("div", "card-note"));
    card.setAttribute("translate", "no");
    card.append(el("div", "t", nameOf(item.path)), el("div", "w", folderOf(item.path) + " · " + item.when));
    const back = row.appendChild(el("button", "", "Bring back"));
    back.type = "button";
    back.onclick = async () => {
      try {
        const done = await api("/api/notes/trash/restore", { method: "POST", body: { path: item.path } });
        toast(t("It is back"));
        await loadList();
        go({ path: done.restored });
      } catch (e) { toast(e.message); }
    };
  }
}

async function addPictures(files, at) {
  for (const file of files) {
    try {
      const response = await fetch("/api/notes/pictures?" + new URLSearchParams({ name: file.name || "picture" }),
        { method: "POST", body: file });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
      edit.editor.insertAt(at, data.embed + "\n");
      at += data.embed.length + 1;
    } catch (e) { toast(e.message); }
  }
}

function pickPicture() {
  const input = document.getElementById("picture-file");
  input.onchange = () => {
    const files = [...input.files];
    input.value = "";
    if (files.length && edit.editor) addPictures(files, edit.editor.view.state.selection.main.head);
  };
  input.click();
}

// -- the journal: a calendar strip and a day's page (block C4) -------------------------------

function isoDay(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

// Two weeks of days: those with a journal page are marked; a click opens the day (or writes it).
function calendarStrip(shown) {
  const strip = el("nav", "strip");
  strip.setAttribute("aria-label", t("Journal days"));
  const now = new Date(today() + "T12:00:00");
  const anchor = new Date(shown + "T12:00:00");
  const end = anchor > now || (now - anchor) / 864e5 < 11 ? now : new Date(anchor.getTime() + 3 * 864e5);
  const names = window.starlightLanguage === "de" ? ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"] : ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
  for (let back = 13; back >= 0; back--) {
    const date = new Date(end.getTime() - back * 864e5);
    const day = isoDay(date);
    const has = state.notes.some(n => n.path === "Journal/" + day + ".md");
    const cell = strip.appendChild(el("button", "day" + (has ? " has" : "") + (day === today() ? " today" : "") + (day === shown ? " open" : "")));
    cell.type = "button";
    cell.dataset.day = day;
    cell.title = day + (has ? "" : " · " + t("no page yet"));
    cell.append(el("span", "wd", names[date.getDay()]), el("span", "dn", String(date.getDate())));
    cell.onclick = () => (has ? go({ path: "Journal/" + day + ".md" }) : writeDay(day));
  }
  return strip;
}

async function writeDay(day) {
  if (!(await stopEditing())) return;
  try {
    const made = await api("/api/notes/today", { method: "POST", body: { day, lang: window.starlightLanguage || "en" } });
    await loadList();
    history.pushState({ path: made.path }, "", "/notes?" + new URLSearchParams({ path: made.path }));
    await openNote(made.path, null, true);
  } catch (e) { toast(e.message); }
}

// -- "Ask Starlight" on a note (block C3) ---------------------------------------------------
// Opens the chat with the request written out (in the page's language) in a new
// conversation; Starlight reads the note with notes.read. Nothing is sent before Send.

const ASKS = {
  en: {
    summary: ["Summarise", (n, p) => `Summarise my note "${n}" (${p}) in a few sentences.`],
    tasks: ["Find the tasks", (n, p) => `List the tasks in my note "${n}" (${p}): what is still open, and when it is due.`],
    links: ["Suggest links and tags", (n, p) => `Suggest links to my other notes and tags for my note "${n}" (${p}). Don't change anything yet.`],
    translate: ["Translate", (n, p) => `Translate my note "${n}" (${p}) into German.`],
  },
  de: {
    summary: ["Zusammenfassen", (n, p) => `Fasse meine Notiz „${n}“ (${p}) in ein paar Sätzen zusammen.`],
    tasks: ["Aufgaben finden", (n, p) => `Liste die Aufgaben in meiner Notiz „${n}“ (${p}) auf: Was ist noch offen, und bis wann?`],
    links: ["Links und Schlagwörter vorschlagen", (n, p) => `Schlag Links zu meinen anderen Notizen und Schlagwörter für meine Notiz „${n}“ (${p}) vor. Ändere noch nichts.`],
    translate: ["Übersetzen", (n, p) => `Übersetze meine Notiz „${n}“ (${p}) ins Englische.`],
  },
};

function askMenu(button) {
  const open = document.querySelector(".ask-menu");
  if (open) { open.remove(); return; }
  const note = state.current;
  const asks = ASKS[window.starlightLanguage === "de" ? "de" : "en"];
  const menu = el("div", "ask-menu");
  menu.setAttribute("role", "menu");
  for (const [kind, [label, request]] of Object.entries(asks)) {
    const item = menu.appendChild(el("button", "", label));
    item.type = "button"; item.setAttribute("role", "menuitem"); item.dataset.ask = kind;
    item.onclick = async () => {
      menu.remove();
      if (!(await stopEditing())) return;
      location.href = "/?" + new URLSearchParams({ ask: request(note.title, note.path.replace(/\.md$/i, "")) });
    };
  }
  button.after(menu);
  menu.querySelector("button").focus();
  const close = (event) => { if (!menu.contains(event.target)) { menu.remove(); document.removeEventListener("click", close); } };
  setTimeout(() => document.addEventListener("click", close));
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { menu.remove(); button.focus(); } });
}

// Export (block G1): the note as one HTML file, as a PDF, or printed (the HTML file opened
// in a new tab, which prints itself). The editor's last change is saved first.
function exportMenu(button) {
  const open = document.querySelector(".ask-menu");
  if (open) { open.remove(); return; }
  const note = state.current;
  const menu = el("div", "ask-menu export-menu");
  menu.setAttribute("role", "menu");
  const address = (format, show) => "/api/note/export?" + new URLSearchParams(
    { path: note.path, format, ...(show ? { show: "true" } : {}), lang: window.starlightLanguage || "en" });
  for (const [label, format, show] of [["HTML file", "html", false], ["PDF file", "pdf", false], ["Print", "html", true]]) {
    const item = menu.appendChild(el("button", "", t(label)));
    item.type = "button"; item.setAttribute("role", "menuitem"); item.dataset.format = format + (show ? "-print" : "");
    item.onclick = async () => {
      menu.remove();
      if (!(await save())) return;
      if (show) { window.open(address(format, true), "_blank", "noopener"); return; }
      const a = el("a");
      a.href = address(format, false); a.download = "";
      document.body.appendChild(a); a.click(); a.remove();
    };
  }
  button.after(menu);
  menu.querySelector("button").focus();
  const close = (event) => { if (!menu.contains(event.target)) { menu.remove(); document.removeEventListener("click", close); } };
  setTimeout(() => document.addEventListener("click", close));
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { menu.remove(); button.focus(); } });
}

// -- links between notes (block C1) --------------------------------------------------------

function linkTarget(a) {
  try { return new URL(a.href, location.href).searchParams.get("open") || ""; } catch (e) { return ""; }
}

// Links to notes that do not exist yet look paler; a click offers to write them.
function markMissing(body, links) {
  const missing = new Set(links.filter(l => !l.to).map(l => l.target.toLowerCase()));
  body.querySelectorAll("a.note-link").forEach(a => {
    if (missing.has(linkTarget(a).toLowerCase())) {
      a.classList.add("missing");
      a.title = t("Not written yet: click to write it");
    }
  });
}

function showMissing(name) {
  clearInfo();
  const box = el("div", "missing-note");
  box.appendChild(el("div", "hello")).appendChild(el("div", "big", "📝"));
  const title = box.appendChild(el("h1", "title", name));
  title.setAttribute("translate", "no");
  box.appendChild(el("p", "dim", t("There is no note with this name yet.")));
  const make = box.appendChild(el("button", "ok", "Write it"));
  make.type = "button";
  make.onclick = async () => {
    const folder = state.current ? folderOf(state.current.path) : "";
    try {
      const made = await api("/api/note", { method: "POST", body: { folder: folder || "Notes", title: name } });
      await loadList();
      history.replaceState({ path: made.path }, "", "/notes?" + new URLSearchParams({ path: made.path }));
      await openNote(made.path, null, true);
    } catch (e) { toast(e.message); }
  };
  column.replaceChildren(box);
}

// ![[Note]] and ![[Note#Heading]]: the note (or that part of it) inside this one. A note
// that is already shown higher up is not shown again (a loop), and at most three deep.
async function showEmbeds(body, source, shown, depth) {
  for (const chip of body.querySelectorAll("a.note-embed")) {
    if (chip.closest(".embed-body") !== body.closest(".embed-body")) continue;  // (deeper ones later)
    const url = new URL(chip.href, location.href);
    const name = url.searchParams.get("open") || "", heading = url.searchParams.get("heading") || "";
    let part;
    try { part = await api("/api/notes/embed?" + new URLSearchParams({ name, source, heading })); }
    catch (e) { chip.classList.add("missing"); chip.title = e.message; continue; }
    if (shown.has(part.path) || depth >= 3) {
      chip.replaceWith(el("span", "embed-note", t("(shown above:") + " " + part.title + ")"));
      continue;
    }
    const box = el("div", "embed");
    const head = box.appendChild(el("div", "embed-title"));
    const link = head.appendChild(el("a", "", part.title + (heading ? " › " + heading : "")));
    link.href = "/notes?" + new URLSearchParams({ path: part.path });
    link.onclick = (event) => { event.preventDefault(); go({ path: part.path }); };
    const inner = box.appendChild(el("div", "md embed-body"));
    renderMarkdown(inner, part.text);
    (chip.closest("p") && chip.closest("p").textContent.trim() === chip.textContent.trim()
      ? chip.closest("p") : chip).replaceWith(box);
    await showEmbeds(inner, part.path, new Set([...shown, part.path]), depth + 1);
  }
}

// Resting the pointer on a link shows a preview of that note.
const previews = new Map();
let previewTimer = null, previewBox = null;
function hidePreview() { clearTimeout(previewTimer); if (previewBox) { previewBox.remove(); previewBox = null; } }
column.addEventListener("mouseover", (event) => {
  const a = event.target.closest("a.note-link:not(.missing):not(.note-embed)");
  if (!a || !column.contains(a)) return;
  hidePreview();
  previewTimer = setTimeout(async () => {
    const name = linkTarget(a), heading = new URL(a.href, location.href).searchParams.get("heading") || "";
    const key = name + "#" + heading;
    try {
      if (!previews.has(key)) {
        previews.set(key, await api("/api/notes/embed?" + new URLSearchParams(
          { name, heading, source: state.current ? state.current.path : "" })));
      }
    } catch (e) { return; }
    if (!a.matches(":hover")) return;
    const part = previews.get(key);
    previewBox = el("div", "preview");
    previewBox.setAttribute("translate", "no");
    previewBox.appendChild(el("h4", "", part.title + (heading ? " › " + heading : "")));
    renderMarkdown(previewBox.appendChild(el("div", "md")), part.text.slice(0, 1500));
    document.body.appendChild(previewBox);
    const at = a.getBoundingClientRect();
    const top = at.bottom + 8 + previewBox.offsetHeight > innerHeight ? at.top - previewBox.offsetHeight - 8 : at.bottom + 8;
    previewBox.style.top = Math.max(8, top) + "px";
    previewBox.style.left = Math.max(8, Math.min(at.left, innerWidth - previewBox.offsetWidth - 8)) + "px";
  }, 450);
});
column.addEventListener("mouseout", (event) => {
  if (event.target.closest("a.note-link")) hidePreview();
});

// -- voice memos (block D2): spoken, written down, kept as text ------------------------------
// Into the inbox, or into the journal day that is open (not while editing it). The sound
// itself is never kept.

let memoRecorder = null;
document.getElementById("memo").onclick = async () => {
  const button = document.getElementById("memo");
  if (!memoRecorder) {
    if (!navigator.mediaDevices) { toast(t("The microphone needs https:// or this PC")); return; }
    try {
      memoRecorder = await startRecording();
      button.classList.add("on"); setIcon(button, "stop");
      toast(t("Recording… press the button again to stop"));
    } catch (e) { toast(t("No microphone access: ") + e.message); }
    return;
  }
  button.classList.remove("on"); setIcon(button, "mic");
  const audio = await memoRecorder.stop();
  memoRecorder = null;
  try {
    const heard = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: audio });
    const data = await heard.json().catch(() => ({}));
    if (!heard.ok) { toast(data.detail || t("Could not understand that")); return; }
    if (!(data.text || "").trim()) { toast(t("Nothing was understood")); return; }
    const journalDay = state.current && !edit.editor && /^Journal\/\d{4}-\d{2}-\d{2}\.md$/.test(state.current.path)
      ? nameOf(state.current.path) : "";
    const kept = await api("/api/notes/memo", { method: "POST",
      body: { text: data.text, to: journalDay ? "journal" : "inbox", day: journalDay } });
    toast(t(kept.kept_in === "journal" ? "Memo kept in the journal" : "Memo kept in the inbox") + ": " + kept.text);
    await loadList();
    if (journalDay) await openNote(state.current.path);
    else if (state.view === "inbox") showView("inbox");
  } catch (e) { toast(e.message); }
};

// -- earlier versions ------------------------------------------------------------------------

const REASONS = {
  "notes.page": "changed here", "notes.page rename": "before renaming", "notes.page delete": "before deleting",
  "before restore": "before putting a version back", "notes.append": "before Starlight added to it",
  "notes.note": "before Starlight rewrote it", "journal.write": "before a journal entry",
  "journal.week": "before the weekly review", "recipes.save": "before Starlight changed it",
  "notes.delete": "before deleting", "undo": "before an undo",
};

async function showHistory() {
  const note = state.current;
  if (!note) return;
  await save();
  const dialog = document.getElementById("history");
  const list = document.getElementById("versions");
  const view = document.getElementById("version-view");
  const restore = document.getElementById("restore-version");
  restore.hidden = true;
  list.replaceChildren();
  let data;
  try { data = await api("/api/note/versions?" + new URLSearchParams({ path: note.path })); }
  catch (e) { toast(e.message); return; }
  view.replaceChildren(el("p", "dim", t(data.versions.length
    ? "Choose a version on the left to see what changed since then."
    : "No earlier versions yet. Starlight keeps one before each change.")));
  dialog.hidden = false;
  for (const version of data.versions) {
    const button = el("button", "", version.when);
    button.type = "button";
    button.setAttribute("aria-pressed", "false");
    button.appendChild(el("small", "", t(REASONS[version.reason] || version.reason)));
    button.onclick = async () => {
      list.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b === button)));
      const shown = await api("/api/note/diff?" + new URLSearchParams({ path: note.path, version: version.id }));
      const pre = el("pre");
      const changes = shown.diff.split("\n").filter(line => !line.startsWith("---") && !line.startsWith("+++"));
      if (!changes.join("").trim()) pre.appendChild(el("span", "at", t("No differences: the note is the same as then.")));
      for (const line of changes) {
        const cls = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "at" : "";
        pre.appendChild(el("span", cls, (line.startsWith("@@") ? "⋯" : line) + "\n"));
      }
      view.replaceChildren(el("p", "dim small", t("Green: added since then. Red: there then, gone now.")), pre);
      restore.hidden = false;
      restore.onclick = async () => {
        try {
          await api("/api/note/restore", { method: "POST", body: { path: note.path, version: version.id } });
        } catch (e) { toast(e.message); return; }
        dialog.hidden = true;
        toast(t("The earlier version is back. The text before it was kept too."));
        const wasEditing = !!edit.editor;
        if (edit.editor) { edit.editor.destroy(); edit.editor = null; edit.dirty = false; document.body.classList.remove("editing"); }
        await openNote(note.path, null, wasEditing);
      };
    };
    list.appendChild(el("li")).appendChild(button);
  }
}
document.getElementById("close-history").onclick = () => { document.getElementById("history").hidden = true; };
document.getElementById("history").addEventListener("click", (event) => {
  if (event.target.id === "history") event.target.hidden = true;
});

// Closing the page with changes not yet saved: send them on the way out.
window.addEventListener("beforeunload", () => {
  if (!edit.editor || !edit.dirty || !state.current) return;
  fetch("/api/note?" + new URLSearchParams({ path: state.current.path }), {
    method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: edit.editor.text(), stamp: state.current.stamp, first: edit.first }),
  });
});

// -- start ----------------------------------------------------------------------------------

(async () => {
  let welcome = null;
  try {
    welcome = (await api("/api/notes/welcome", { method: "POST", body: { lang: window.starlightLanguage || "en" } })).path;
    await loadList();
  } catch (e) {
    column.replaceChildren(el("p", "notice", e.message));
    return;
  }
  const params = Object.fromEntries(new URLSearchParams(location.search));
  if (welcome && !Object.keys(params).length) return go({ path: welcome }, false);
  history.replaceState(params, "", location.href);
  route(params);
})();
