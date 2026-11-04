// Finding a note (Ctrl+K), the keys and a new note.
// Part of the Notes page (notes.html), which loads its parts in this order: notes-page.js,
// notes-tasks.js, notes-info.js, notes-views.js, notes-find.js, notes-edit.js. They are
// plain scripts: what one declares at the top, the others use.

// -- finding a note: Ctrl+K -----------------------------------------------------------------

const switcher = document.getElementById("switcher");
const switchInput = document.getElementById("switch-input");
const switchList = document.getElementById("switch-list");
let choices = [], chosen = 0;

// How well "query" fits "text": every letter in order; a match at the start or at the start
// of a word counts more, and letters next to each other more still. -1: no match.
function fit(query, text) {
  const q = query.toLowerCase(), s = text.toLowerCase();
  let score = 0, at = -1, run = 0;
  for (const ch of q) {
    const next = s.indexOf(ch, at + 1);
    if (next < 0) return -1;
    run = next === at + 1 ? run + 1 : 0;
    score += 1 + run * 2 + (next === 0 || /[\s\-_/(]/.test(s[next - 1]) ? 3 : 0);
    at = next;
  }
  return score - s.length / 100;
}

function drawChoices() {
  const q = switchInput.value.trim();
  if (!q) {
    const last = recent().map(p => state.notes.find(n => n.path === p)).filter(Boolean);
    choices = [...last, ...state.notes.filter(n => !last.includes(n))].slice(0, 20).map(n => ({ note: n }));
  } else {
    choices = state.notes.map(n => {
      const names = [titleOf(n), n.name, ...n.aliases];
      const best = Math.max(...names.map(name => fit(q, name)));
      const alias = n.aliases.find(a => fit(q, a) === best && best > fit(q, titleOf(n)));
      return { note: n, score: best, alias };
    }).filter(c => c.score >= 0).sort((a, b) => b.score - a.score).slice(0, 20);
  }
  chosen = Math.min(chosen, Math.max(choices.length - 1, 0));
  switchList.replaceChildren();
  choices.forEach((c, i) => {
    const li = el("li");
    li.id = "choice-" + i;
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", String(i === chosen));
    li.appendChild(el("span", "", titleOf(c.note) + (c.alias ? " (" + c.alias + ")" : "")));
    li.appendChild(el("span", "w", folderOf(c.note.path)));
    li.onmousemove = () => { if (chosen !== i) { chosen = i; drawChoices(); } };
    li.onclick = () => pick(i);
    switchList.appendChild(li);
  });
  if (!choices.length) switchList.appendChild(el("li", "dim", t("No note with that name")));
  switchInput.setAttribute("aria-activedescendant", choices.length ? "choice-" + chosen : "");
}

function openSwitcher() {
  switcher.hidden = false;
  switchInput.value = "";
  chosen = 0;
  drawChoices();
  switchInput.focus();
}
function closeSwitcher() { switcher.hidden = true; }
function pick(i) {
  const choice = choices[i];
  closeSwitcher();
  if (choice) go({ path: choice.note.path });
}

switchInput.addEventListener("input", () => { chosen = 0; drawChoices(); });
switchInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") { chosen = Math.min(chosen + 1, choices.length - 1); drawChoices(); event.preventDefault(); }
  else if (event.key === "ArrowUp") { chosen = Math.max(chosen - 1, 0); drawChoices(); event.preventDefault(); }
  else if (event.key === "Enter") { pick(chosen); event.preventDefault(); }
  else if (event.key === "Escape") closeSwitcher();
});
switcher.addEventListener("click", (event) => { if (event.target === switcher) closeSwitcher(); });
document.getElementById("find").onclick = openSwitcher;
document.addEventListener("keydown", (event) => {
  const key = event.key.toLowerCase(), mod = event.ctrlKey || event.metaKey;  // (the ? window: common.js)
  if (mod && key === "k") { event.preventDefault(); openSwitcher(); }
  else if (mod && key === "e" && state.current) { event.preventDefault(); toggleEdit(); }
  else if (mod && key === "s") { event.preventDefault(); if (edit.editor) save(); }
  else if (mod && key === "n" && !event.shiftKey) { event.preventDefault(); newNote(); }
  else if (event.key === "Escape" && document.body.classList.contains("show-info")) document.body.classList.remove("show-info");
  else if (state.study && state.view === "cards" && !mod && !event.altKey && state.study(event)) event.preventDefault();
});
// ＋: an empty note, or one from a template (Meeting, Daily, Project and your own)
document.getElementById("new-note").onclick = async (event) => {
  event.stopPropagation();
  const open = document.querySelector(".new-menu");
  if (open) { open.remove(); return; }
  let names = [];
  try { names = (await api("/api/notes/templates?" + new URLSearchParams({ lang: window.starlightLanguage || "en" }))).templates; }
  catch (e) { /* an empty note still works */ }
  const menu = el("div", "ask-menu new-menu");
  menu.setAttribute("role", "menu");
  const item = (label, template) => {
    const b = menu.appendChild(el("button", "", label));
    b.type = "button"; b.setAttribute("role", "menuitem");
    if (template) { b.dataset.template = template; b.setAttribute("translate", "no"); }
    b.onclick = () => { menu.remove(); newNote(template); };
  };
  item(t("Empty note"), "");
  if (names.length) menu.appendChild(el("div", "menu-label", t("From a template")));
  names.forEach(name => item(name, name));
  document.querySelector(".side .top-row").appendChild(menu);
  menu.querySelector("button").focus();
  const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", close); } };
  setTimeout(() => document.addEventListener("click", close));
  menu.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.remove(); });
};


// The keys of this page, for the ? window (common.js).
window.SHORTCUTS = [
  ["Ctrl+K", "Find a note"], ["Ctrl+N", "A new note"], ["Ctrl+E", "Edit or read the note"],
  ["Ctrl+S", "Save"], ["Esc", "Close the side panel or the finder"],
  ["Space", "Flashcards: show the answer"], ["1–4", "Flashcards: how well you knew it"],
  ["?", "These shortcuts (Ctrl+/ while typing in a box)"],
];
