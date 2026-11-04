// The page's state and helpers, the list on the left, moving around (the address), one note.
// Part of the Notes page (notes.html), which loads its parts in this order: notes-page.js,
// notes-tasks.js, notes-info.js, notes-views.js, notes-find.js, notes-edit.js. They are
// plain scripts: what one declares at the top, the others use.

window.starlightQueryTables = true;  // ```query blocks become live tables here (block E4)
window.starlightDiagrams = true;  // ```mermaid blocks become diagrams here (block G5)
// The Notes page (docs/plan-notes-app.md, block B5): find and read your notes. Reading
// comes first; editing follows in a later update. Notes are your own text: it is drawn by
// markdown.js and render.js (only textContent) and never translated.
const RECENT = "starlight-notes-recent";
const state = { notes: [], folders: [], pinned: [], current: null };
const column = document.getElementById("column");
const t = (text) => (window.starlightTranslate ? window.starlightTranslate(text) : text);

function titleOf(note) { return note.title || note.name; }
function folderOf(path) { return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ""; }
function nameOf(path) { return path.slice(path.lastIndexOf("/") + 1).replace(/\.md$/i, ""); }
function when(seconds) {
  const date = new Date(seconds * 1000);
  return date.toLocaleDateString(window.starlightLanguage === "de" ? "de-DE" : undefined,
    { day: "numeric", month: "long", year: "numeric" });
}
function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function recent() { try { return JSON.parse(localStorage.getItem(RECENT) || "[]"); } catch (e) { return []; } }
function remember(path) {
  try { localStorage.setItem(RECENT, JSON.stringify([path, ...recent().filter(p => p !== path)].slice(0, 12))); }
  catch (e) { /* a private window */ }
}

// -- the list on the left ------------------------------------------------------------------

async function loadList() {
  const [data, facets] = await Promise.all([api("/api/notes"), api("/api/notes/facets")]);
  state.notes = data.notes; state.folders = data.folders; state.pinned = data.pinned;
  state.tags = facets.tags; state.propertyNames = facets.properties;
  drawSide();
  drawTags();
  if (data.capped) toast("Only the first 20,000 notes are listed");
}

// Tags (block C2): every tag, nested ones under their parent, with how many notes have it.
function drawTags() {
  const box = document.getElementById("tags-box");
  box.replaceChildren();
  for (const entry of state.tags || []) {
    const b = el("button", "item tag-item");
    b.type = "button";
    b.style.paddingLeft = 8 + entry.depth * 14 + "px";
    b.appendChild(el("span", "name", "#" + entry.tag.split("/").pop()));
    b.appendChild(el("span", "count", String(entry.count)));
    b.title = "#" + entry.tag;
    b.dataset.tag = entry.tag;
    b.onclick = () => go({ tag: entry.tag });
    box.appendChild(el("li")).appendChild(b);
  }
  document.getElementById("tags-section").hidden = !box.children.length;
}

function noteButton(note) {
  const b = el("button", "item");
  b.type = "button";
  b.dataset.path = note.path;
  b.title = note.path;
  b.appendChild(el("span", "name", titleOf(note)));
  b.onclick = () => go({ path: note.path });
  if (state.current && state.current.path === note.path) b.setAttribute("aria-current", "page");
  return b;
}

function drawSide() {
  const pinnedBox = document.getElementById("pinned-box");
  const pinned = document.getElementById("pinned");
  pinned.replaceChildren();
  for (const path of state.pinned) {
    const note = state.notes.find(n => n.path === path);
    if (note) pinned.appendChild(el("li")).appendChild(noteButton(note));
  }
  pinnedBox.hidden = !pinned.children.length;

  const tree = {};  // folder path -> notes
  for (const note of state.notes) (tree[folderOf(note.path)] ||= []).push(note);
  const folders = document.getElementById("folders");
  const open = new Set([...folders.querySelectorAll("details[open]")].map(d => d.dataset.folder));
  const first = !folders.children.length;
  folders.replaceChildren();
  const tops = [...state.folders, ...(tree[""] ? [""] : [])];
  for (const top of tops) folders.appendChild(folderBox(top, tree, open, first));
}

function folderBox(folder, tree, open, first) {
  const box = el("details", "folder");
  box.dataset.folder = folder;
  const inside = Object.keys(tree).filter(f => f === folder || f.startsWith(folder + "/"));
  const count = inside.reduce((n, f) => n + tree[f].length, 0);
  const current = state.current && (folderOf(state.current.path) + "/").startsWith(folder + "/");
  box.open = open.has(folder) || current || (first && folder === "Notes");
  const summary = el("summary");
  const label = el("span", "name", folder ? nameOf(folder) : t("Starlight folder"));
  if (!folder) label.removeAttribute("translate");
  summary.appendChild(label);
  summary.appendChild(el("span", "count", String(count)));
  box.appendChild(summary);
  const subfolders = [...new Set(inside.filter(f => f !== folder)
    .map(f => folder + "/" + f.slice(folder.length + 1).split("/")[0]))].sort();
  for (const sub of subfolders) box.appendChild(folderBox(sub, tree, open, false));
  const list = el("ul", "plain");
  const notes = [...(tree[folder] || [])];
  // the journal: the newest day first; elsewhere A to Z
  notes.sort((a, b) => folder === "Journal" ? b.name.localeCompare(a.name)
    : titleOf(a).localeCompare(titleOf(b), undefined, { sensitivity: "base" }));
  for (const note of notes) list.appendChild(el("li")).appendChild(noteButton(note));
  if (!count) box.appendChild(el("div", "empty", t("No notes here yet")));
  box.appendChild(list);
  return box;
}

function markCurrent() {
  document.querySelectorAll(".side .item").forEach(b => {
    if (state.current && b.dataset.path === state.current.path) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".quick button").forEach(b => b.classList.toggle("on", b.dataset.view === state.view));
}

// -- moving around: /notes?path=… | ?open=Name&heading=… | ?tag=… | ?q=… | ?view=… --------

async function go(params, push = true) {
  if (!(await stopEditing())) return;
  const query = new URLSearchParams(params).toString();
  if (push) history.pushState(params, "", "/notes" + (query ? "?" + query : ""));
  return route(params);
}

async function route(params) {
  state.view = params.view || null;
  if (params.board) return openBoardView(params.board);
  if (params.path) return openNote(params.path, params.heading);
  if (params.open) {
    try {
      const found = await api("/api/notes/resolve?" + new URLSearchParams(
        { name: params.open, source: state.current ? state.current.path : "" }));
      const next = { path: found.path, ...(params.heading ? { heading: params.heading } : {}) };
      history.replaceState(next, "", "/notes?" + new URLSearchParams(next));
      return openNote(found.path, params.heading);
    } catch (e) {
      return showMissing(params.open);
    }
  }
  if (params.tag) return showList("#" + params.tag, "/api/notes?" + new URLSearchParams({ tag: params.tag }));
  if (params.q) return showSearch(params.q);
  if (params.view) return showView(params.view);
  return showHome();
}

window.addEventListener("popstate", async () => {
  if (!(await stopEditing())) {  // not saved yet: stay on the note (its text is still there)
    history.pushState({ path: state.current.path }, "", "/notes?" + new URLSearchParams({ path: state.current.path }));
    return;
  }
  route(Object.fromEntries(new URLSearchParams(location.search)));
});

// A link in a note: [[Other note]] and #tags stay on this page.
column.addEventListener("click", (event) => {
  const link = event.target.closest("a.note-link, a.tag");
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const url = new URL(link.href, location.href);
  if (url.pathname !== "/notes") return;
  event.preventDefault();
  go(Object.fromEntries(url.searchParams));
});

// -- one note ------------------------------------------------------------------------------

async function openNote(path, heading, editing = false) {
  let note;
  try { note = await api("/api/note?" + new URLSearchParams({ path })); }
  catch (e) { toast(e.message); return showHome(); }
  state.current = note;
  state.view = null;
  remember(note.path);
  document.title = note.title + " · Starlight";
  column.replaceChildren();
  const crumbs = column.appendChild(el("div", "crumbs"));
  crumbs.appendChild(el("span", "", folderOf(note.path) || t("Starlight folder")))
    .setAttribute("translate", folderOf(note.path) ? "no" : "yes");
  const info = (state.notes.find(n => n.path === note.path) || {});
  if (info.modified) crumbs.appendChild(el("span", "", "· " + t("changed") + " " + when(info.modified)));
  const actions = crumbs.appendChild(el("span", "actions"));
  const pin = el("button", "icon", note.pinned ? "★ Pinned" : "☆ Pin");
  pin.type = "button";
  pin.title = note.pinned ? t("Unpin this note") : t("Pin this note to the top of the list");
  pin.onclick = async () => {
    const answer = await api("/api/notes/pins", { method: "POST", body: { path: note.path, pinned: !note.pinned } });
    state.pinned = answer.pinned;
    note.pinned = !note.pinned;
    pin.textContent = t(note.pinned ? "★ Pinned" : "☆ Pin");
    drawSide();
  };
  const toggle = el("button", "icon"); setIcon(toggle, "info");
  toggle.id = "info-toggle"; toggle.type = "button";
  toggle.title = t("About this note"); toggle.setAttribute("aria-label", t("About this note"));
  toggle.onclick = () => document.body.classList.toggle("show-info");
  const editButton = el("button", "icon", "✎ Edit");
  editButton.id = "edit-toggle"; editButton.type = "button";
  editButton.title = t("Edit or read this note (Ctrl+E)");
  editButton.onclick = () => toggleEdit();
  const past = el("button", "icon"); setIcon(past, "history");
  past.type = "button"; past.title = t("Earlier versions"); past.setAttribute("aria-label", t("Earlier versions"));
  past.onclick = () => showHistory();
  const ask = el("button", "icon", "✨ Ask");
  ask.type = "button"; ask.title = t("Ask Starlight about this note"); ask.setAttribute("aria-haspopup", "menu");
  ask.onclick = (event) => { event.stopPropagation(); askMenu(ask); };
  const out = el("button", "icon"); setIcon(out, "download");
  out.type = "button"; out.title = t("Export: HTML, PDF or print"); out.setAttribute("aria-label", t("Export: HTML, PDF or print"));
  out.setAttribute("aria-haspopup", "menu"); out.id = "export-toggle";
  out.onclick = (event) => { event.stopPropagation(); exportMenu(out); };
  const share = el("button", "icon"); setIcon(share, "link");  // a link that opens it from other programs (G6)
  share.type = "button"; share.id = "link-copy";
  share.title = t("Copy a link to this note (it opens the note from other programs)");
  share.setAttribute("aria-label", t("Copy a link to this note (it opens the note from other programs)"));
  share.onclick = () => copyLink(note.path);
  const bin = el("button", "icon"); setIcon(bin, "trash");
  bin.type = "button"; bin.title = t("Move to the trash"); bin.setAttribute("aria-label", t("Move to the trash"));
  bin.onclick = () => deleteNote();
  actions.append(editButton, ask, pin, past, out, share, bin, toggle);

  const title = column.appendChild(el("h1", "title", note.title));
  title.setAttribute("translate", "no");
  const chips = column.appendChild(el("div", "chips"));
  chips.setAttribute("translate", "no");
  // the note's own tags (its tags property); tags in the text show where they are written
  const own = note.properties.tags ?? note.properties.tag ?? [];
  const tags = (Array.isArray(own) ? own : String(own).split(/[\s,]+/))
    .map(tag => String(tag).replace(/^#/, "").trim()).filter(Boolean);
  for (const tag of tags) {
    const a = el("a", "tag", "#" + tag);
    a.href = "/notes?" + new URLSearchParams({ tag });
    a.onclick = (event) => { event.preventDefault(); go({ tag }); };
    chips.appendChild(a);
  }
  for (const [key, value] of Object.entries(note.properties)) {
    if (["tags", "tag", "aliases", "alias", "title"].includes(key)) continue;
    const chip = chips.appendChild(el("span", "chip"));
    const shown = typeof value === "boolean" ? t(value ? "yes" : "no") : Array.isArray(value) ? value.join(", ") : String(value);
    chip.append(el("span", "", key + ": "), el("b", "", shown));
  }
  chips.classList.add("md");  // (the tag pills' look)
  if (note.error) column.appendChild(el("p", "notice", t("This note's properties could not be read:") + " " + note.error));
  const body = column.appendChild(el("div", "md note-body"));
  body.setAttribute("translate", "no");
  renderMarkdown(body, note.body);
  if (!note.body.trim()) body.appendChild(el("p", "dim", t("This note is empty.")));
  enableTasks(body, note);

  state.parts = { title, body };
  if (/^Journal\/\d{4}-\d{2}-\d{2}\.md$/.test(note.path)) column.prepend(calendarStrip(nameOf(note.path)));
  markMissing(body, note.links);
  showEmbeds(body, note.path, new Set([note.path]), 0);
  drawInfo(note, body);
  markCurrent();
  document.getElementById("note").scrollTop = 0;
  if (heading) showHeading(body, note.headings, heading);
  if (editing) await startEditing();
}

