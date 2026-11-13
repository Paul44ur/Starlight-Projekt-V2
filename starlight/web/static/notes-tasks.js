// Tasks in notes (block D4) and the task board (block G2).
// Part of the Notes page (notes.html), which loads its parts in this order: notes-page.js,
// notes-tasks.js, notes-info.js, notes-views.js, notes-find.js, notes-edit.js. They are
// plain scripts: what one declares at the top, the others use.

// -- tasks (block D4) -------------------------------------------------------------------------
// A task's box ticks it in the note (with "done:" and the day), or opens it again. The boxes
// are matched to the note's tasks in order; when they do not match (a task in a quote, say)
// they stay read-only. The server refuses when the line changed meanwhile.

function enableTasks(body, note) {
  const boxes = [...body.querySelectorAll("li.task > .task-box")]
    .filter(box => !box.closest("blockquote, .callout, .embed"));
  const tasks = note.tasks || [];
  if (boxes.length !== tasks.length || boxes.some((box, n) => box.checked !== (tasks[n].status !== " "))) return;
  boxes.forEach((box, n) => {
    if (![" ", "x", "X"].includes(tasks[n].status)) return;  // (cancelled and so on: as written)
    box.disabled = false;
    box.title = t(box.checked ? "Open it again" : "Tick it off");
    box.onchange = () => tickInNote(box, note, tasks[n]);
  });
}

async function tickInNote(box, note, task) {
  box.disabled = true;
  const scroller = document.getElementById("note"), at = scroller.scrollTop;
  try {
    await api("/api/note/task", { method: "POST", body: { path: note.path, line: task.line, expected: task.raw, done: box.checked } });
  } catch (e) {
    toast(e.message);
  }
  await openNote(note.path);  // as it is now: its done date, a repeating task's next one
  scroller.scrollTop = at;
}

// The Tasks view: the open tasks of all notes, overdue first
// The Tasks view shows a list (by date) or columns (open, doing, done: block G2); the
// choice is remembered in this browser.
function tasksLayout(set) {
  try {
    if (set) localStorage.setItem("starlight.tasks.layout", set);
    return localStorage.getItem("starlight.tasks.layout") || "list";
  } catch (e) { return set || "list"; }
}

async function showTasks() {
  clearInfo(); state.view = "tasks"; markCurrent();
  column.replaceChildren(el("h1", "title", t("Tasks")));
  const layout = tasksLayout();
  const switcher = column.appendChild(el("div", "layout-switch"));
  switcher.setAttribute("role", "group");
  switcher.setAttribute("aria-label", t("Show the tasks as"));
  for (const [key, label] of [["list", "List"], ["columns", "Columns"]]) {
    const b = switcher.appendChild(el("button", key === layout ? "on" : "", t(label)));
    b.type = "button"; b.dataset.layout = key;
    b.setAttribute("aria-pressed", String(key === layout));
    b.onclick = () => { tasksLayout(key); showTasks(); };
  }
  if (layout === "columns") return showKanban();
  let data;
  try { data = await api("/api/notes/tasks"); }
  catch (e) { toast(e.message); return; }
  if (state.view !== "tasks") return;  // (another view was chosen meanwhile)
  if (!data.count) {
    column.appendChild(el("div", "soon", t("No open tasks. Write one in any note, for example: - [ ] Call Tom due: friday")));
    return;
  }
  const groups = [["overdue", "Overdue"], ["today", "Today"], ["upcoming", "Upcoming"], ["none", "No date"]];
  for (const [key, label] of groups) {
    if (!data[key].length) continue;
    const section = column.appendChild(el("section", "task-group"));
    section.dataset.group = key;
    const heading = section.appendChild(el("h3", "", t(label)));
    heading.appendChild(el("span", "count num", String(data[key].length)));
    const list = section.appendChild(el("ul", "task-rows md"));
    for (const task of data[key]) list.appendChild(taskRow(task, data.date));
  }
}

// -- the task board (block G2): open, doing, done; drag a card or use its buttons ----------

const KANBAN = [["open", "Open", "↺"], ["doing", "Doing", "▶"], ["done", "Done", "✓"]];
let dragged = null;

async function showKanban() {
  let data;
  try { data = await api("/api/notes/kanban"); }
  catch (e) { toast(e.message); return; }
  if (state.view !== "tasks") return;
  const board = column.appendChild(el("div", "kanban"));
  for (const [stage, label] of KANBAN) {
    const lane = board.appendChild(el("section", "kanban-col"));
    lane.dataset.stage = stage;
    const heading = lane.appendChild(el("h3", "", t(label)));
    heading.appendChild(el("span", "count num", String(data[stage].length)));
    const cards = lane.appendChild(el("ul", "kanban-cards md"));
    for (const card of data[stage]) cards.appendChild(kanbanCard(card, stage, data));
    if (!data[stage].length) cards.appendChild(el("li", "kanban-empty", t("Nothing here")));
    lane.addEventListener("dragover", (event) => { if (dragged) { event.preventDefault(); lane.classList.add("over"); } });
    lane.addEventListener("dragleave", () => lane.classList.remove("over"));
    lane.addEventListener("drop", (event) => {
      event.preventDefault(); lane.classList.remove("over");
      if (dragged && dragged.stage !== stage) moveCard(dragged.card, stage);
      dragged = null;
    });
  }
}

function kanbanCard(card, stage, data) {
  const item = el("li", "kanban-card");
  item.draggable = true;
  item.dataset.id = card.id;
  const words = item.appendChild(el("div", "kanban-text"));
  words.setAttribute("translate", "no");
  mdInline(words, card.text);
  const facts = item.appendChild(el("div", "kanban-facts"));
  for (const name of ["due", "priority"]) {
    const chip = card[name] && (name !== "priority" || card.priority !== "normal")
      && taskChip(name, card[name], stage !== "done");
    if (chip) facts.appendChild(chip);
  }
  if (card.kind === "note") {
    const where = facts.appendChild(el("a", "task-note", card.note));
    where.setAttribute("translate", "no");
    where.href = "/notes?" + new URLSearchParams({ path: card.path });
    where.onclick = (event) => { event.preventDefault(); go({ path: card.path }); };
  } else {
    facts.appendChild(el("span", "task-note", "☑ " + (card.list || ""))).setAttribute("translate", "no");
  }
  const moves = item.appendChild(el("div", "kanban-moves"));
  for (const [to, label, icon] of KANBAN) {
    if (to === stage) continue;
    const b = moves.appendChild(el("button", "icon", icon));
    b.type = "button"; b.dataset.to = to;
    b.title = t("Move to") + " " + t(label); b.setAttribute("aria-label", b.title);
    b.onclick = () => moveCard(card, to);
  }
  item.addEventListener("dragstart", (event) => {
    dragged = { card, stage };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.id);
  });
  item.addEventListener("dragend", () => { dragged = null; });
  return item;
}

// starlight://note/Notes/Plan.md: Windows opens it with Starlight (block G6), and the chat
// and notes show it as a link to the note.
function deepLink(path) {
  return "starlight://note/" + path.split("/").map(encodeURIComponent).join("/");
}

async function copyLink(path) {
  const link = deepLink(path);
  try {
    await navigator.clipboard.writeText(link);
    toast(t("Link copied:") + " " + link);
  } catch (e) {
    askText(t("The link to this note:"), link, { yes: "Close", only: true, readonly: true });
  }
}

async function moveCard(card, to) {
  try {
    await api("/api/notes/kanban/move", { method: "POST",
      body: { id: card.id, to, ...(card.kind === "note" ? { path: card.path } : {}) } });
  } catch (e) { toast(e.message); }
  if (state.view === "tasks") showTasks();
}

function taskRow(task, today) {
  const row = el("li", "task-row");
  row.dataset.id = task.id;
  const box = row.appendChild(el("input", "task-box"));
  box.type = "checkbox";
  box.title = t("Tick it off");
  box.setAttribute("aria-label", task.text);
  const words = row.appendChild(el("span", "task-text"));
  words.setAttribute("translate", "no");
  mdInline(words, task.text);
  for (const name of ["due", "start", "repeat", "priority"]) {
    const chip = task[name] && (name !== "priority" || task.priority !== "normal") && taskChip(name, task[name], true, today);
    if (chip) row.appendChild(chip);
  }
  const where = row.appendChild(el("a", "task-note", task.note));
  where.setAttribute("translate", "no");
  where.href = "/notes?" + new URLSearchParams({ path: task.path });
  where.onclick = (event) => { event.preventDefault(); go({ path: task.path }); };
  box.onchange = async () => {
    box.disabled = true;
    try {
      await api("/api/note/task", { method: "POST", body: { path: task.path, id: task.id, done: box.checked } });
      row.classList.toggle("done", box.checked);  // (it stays in the list until you look again)
    } catch (e) {
      box.checked = !box.checked;
      toast(e.message);
    }
    box.disabled = false;
  };
  return row;
}

function showHeading(body, headings, wanted) {
  const name = wanted.split("#").pop().trim().toLowerCase();
  const index = headings.findIndex(h => h.text.toLowerCase() === name);
  const target = index >= 0 ? body.querySelectorAll(".md-h")[index] : null;
  if (!target) return;
  target.scrollIntoView({ block: "start" });
  target.classList.add("flash");
  setTimeout(() => target.classList.remove("flash"), 1300);
}

function drawInfo(note, body) {
  const outline = document.getElementById("outline");
  outline.replaceChildren();
  const rendered = body.querySelectorAll(".md-h");
  const top = Math.min(...note.headings.map(h => h.level), 6);
  note.headings.forEach((h, i) => {
    const b = el("button", "lvl-" + (h.level - top + 1), h.text || "…");
    b.type = "button";
    b.onclick = () => { if (rendered[i]) rendered[i].scrollIntoView({ block: "start", behavior: "smooth" }); };
    outline.appendChild(el("li")).appendChild(b);
  });
  if (!note.headings.length) outline.appendChild(el("li", "none", t("No headings")));

  const back = document.getElementById("backlinks");
  back.replaceChildren();
  const seen = new Set();
  for (const link of note.backlinks) {
    if (seen.has(link.from)) continue;
    seen.add(link.from);
    const b = el("button", "back", nameOf(link.from));
    b.type = "button";
    b.appendChild(el("small", "", folderOf(link.from)));
    if (link.text) { const line = b.appendChild(el("span", "line", link.text)); line.title = link.text; }
    b.onclick = () => go({ path: link.from });
    back.appendChild(el("li")).appendChild(b);
  }
  if (!seen.size) back.appendChild(el("li", "none", t("No other note links here yet")));

  drawProps(note);
  drawRelated(note);
  drawGraph(note);
}

