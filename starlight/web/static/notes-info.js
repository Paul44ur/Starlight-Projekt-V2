// What belongs to a note on the right (small graph, related notes, mentions, properties) and the lists and start page.
// Part of the Notes page (notes.html), which loads its parts in this order: notes-page.js,
// notes-tasks.js, notes-info.js, notes-views.js, notes-find.js, notes-edit.js. They are
// plain scripts: what one declares at the top, the others use.

// -- the small graph: this note and the notes it links with (block F1) --------------------
// Laid out by d3-force (/vendor/graph.js, loaded on first use), drawn as SVG. A click on a
// point (or Enter on it) opens that note.

let graphKit = null;
const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attributes = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

async function drawGraph(note) {
  const box = document.getElementById("mini-graph");
  box.replaceChildren();
  let data;
  try { data = await api("/api/notes/graph?" + new URLSearchParams({ around: note.path, depth: 1 })); }
  catch (e) { return; }
  if (state.current !== note) return;
  if (data.nodes.length < 2) { box.appendChild(el("p", "none", t("No links yet"))); return; }
  try { graphKit ||= await import("/vendor/graph.js"); }
  catch (e) { return; }
  if (state.current !== note) return;
  const nodes = data.nodes.map(n => ({ ...n })), links = data.links.map(l => ({ ...l }));
  const simulation = graphKit.forceSimulation(nodes)
    .force("link", graphKit.forceLink(links).id(n => n.id).distance(46))
    .force("charge", graphKit.forceManyBody().strength(-140))
    .force("center", graphKit.forceCenter(0, 0))
    .force("collide", graphKit.forceCollide(16))
    .stop();
  for (let i = 0; i < 300; i++) simulation.tick();
  const W = 240, H = 180, PAD = 22;
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = Math.min((W - 2 * PAD) / Math.max(x1 - x0, 1), (H - 2 * PAD) / Math.max(y1 - y0, 1), 1.6);
  const X = (n) => W / 2 + (n.x - (x0 + x1) / 2) * scale, Y = (n) => H / 2 + (n.y - (y0 + y1) / 2) * scale;
  const picture = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "group" });
  picture.setAttribute("aria-label", t("The notes linked with this one"));
  for (const link of links) {
    picture.appendChild(svg("line", { class: "edge", x1: X(link.source), y1: Y(link.source), x2: X(link.target), y2: Y(link.target) }));
  }
  for (const n of nodes) {
    const point = picture.appendChild(svg("g", { class: n.id === note.path ? "node here" : "node", tabindex: "0", role: "link" }));
    point.dataset.path = n.id;
    point.setAttribute("aria-label", n.title);
    point.appendChild(svg("title")).textContent = n.title;
    point.appendChild(svg("circle", { cx: X(n), cy: Y(n), r: n.id === note.path ? 7 : 4 + Math.min(Math.sqrt(n.links), 4) }));
    const label = point.appendChild(svg("text", { x: X(n), y: Y(n) + 16, "text-anchor": "middle" }));
    label.textContent = n.title.length > 18 ? n.title.slice(0, 17) + "…" : n.title;
    const open = () => { if (n.id !== note.path) go({ path: n.id }); };
    point.addEventListener("click", open);
    point.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
  }
  box.appendChild(picture);
}

// -- related notes and mentions without a link (block E2) ----------------------------------

async function drawRelated(note) {
  const list = document.getElementById("related"), named = document.getElementById("mentions");
  let found, said;
  try {
    [found, said] = await Promise.all([
      api("/api/note/related?" + new URLSearchParams({ path: note.path })),
      api("/api/note/mentions?" + new URLSearchParams({ path: note.path })),
    ]);
  } catch (e) { return; }
  if (state.current !== note) return;  // (another note was opened meanwhile)
  list.replaceChildren();
  for (const other of found.related) {
    const b = el("button", "back", other.title);
    b.type = "button";
    b.appendChild(el("small", "", other.why.map(w => w.startsWith("#") ? w : t(w)).join(" · ")));
    b.onclick = () => go({ path: other.path });
    list.appendChild(el("li")).appendChild(b);
  }
  if (!found.related.length) list.appendChild(el("li", "none", t("No related notes yet")));
  named.replaceChildren();
  for (const m of said.mentions) {
    const item = named.appendChild(el("li", "mention"));
    const b = item.appendChild(el("button", "back", m.title));
    b.type = "button";
    const line = b.appendChild(el("span", "line"));
    line.append(m.text.slice(0, m.start).trimStart(), el("mark", "", m.text.slice(m.start, m.end)), m.text.slice(m.end));
    line.title = m.text;
    b.onclick = () => go({ path: m.path });
    const link = item.appendChild(el("button", "link-it", t("Link")));
    link.type = "button";
    link.title = t("Make it a link to this note");
    link.onclick = async () => {
      link.disabled = true;
      try {
        await api("/api/note/link-mention", { method: "POST", body: { path: note.path, source: m.path, line: m.line, expected: m.text } });
        toast(t("Linked"));
      } catch (e) { toast(e.message); }
      await openNote(note.path);  // (it is in "Linked from" now)
    };
  }
  if (!said.mentions.length) named.appendChild(el("li", "none", t("No mentions without a link")));
}

// -- properties (block C2): change, add and remove them here; only their lines change -------

const TYPES = [["text", "Text"], ["number", "Number"], ["date", "Date"], ["list", "List"], ["yesno", "Yes or no"]];
function typeOf(value) {
  if (typeof value === "boolean") return "yesno";
  if (typeof value === "number") return "number";
  if (Array.isArray(value)) return "list";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return "date";
  return "text";
}
function fromInput(type, input) {
  if (type === "yesno") return input.checked;
  if (type === "number") return input.value === "" ? null : Number(input.value);
  if (type === "list") return input.value.split(",").map(v => v.trim()).filter(Boolean);
  return input.value.trim() === "" ? null : input.value.trim();
}
function propInput(type, value) {
  const input = el("input", "prop-value");
  if (type === "yesno") { input.type = "checkbox"; input.checked = !!value; }
  else if (type === "number") { input.type = "number"; input.step = "any"; input.value = value ?? ""; }
  else if (type === "date") { input.type = "date"; input.value = value || ""; }
  else { input.type = "text"; input.value = Array.isArray(value) ? value.join(", ") : (value ?? ""); }
  return input;
}

async function setProp(note, name, value) {
  if (edit.editor) { toast(t("Finish editing first (Done), then change properties here.")); return; }
  try {
    await api("/api/note/property", { method: "POST", body: { path: note.path, name, value, stamp: note.stamp } });
  } catch (e) { toast(e.message); }
  await loadList();
  await openNote(note.path);
}

function drawProps(note) {
  const props = document.getElementById("props");
  props.replaceChildren();
  for (const [key, value] of Object.entries(note.properties)) {
    const type = typeOf(value);
    const dd = el("dd", "prop");
    const input = dd.appendChild(propInput(type, value));
    input.setAttribute("aria-label", key);
    input.addEventListener("change", () => setProp(note, key, fromInput(type, input)));
    const remove = dd.appendChild(el("button", "icon prop-x")); setIcon(remove, "close");
    remove.type = "button";
    remove.title = t("Remove this property"); remove.setAttribute("aria-label", t("Remove this property") + ": " + key);
    remove.onclick = () => setProp(note, key, null);
    props.append(el("dt", "", key), dd);
  }
  if (!props.children.length) props.appendChild(el("dd", "none", t("None")));
  const add = el("button", "icon add-prop", "＋ Add a property");
  add.type = "button";
  add.onclick = () => {
    const form = el("form", "prop-form");
    const name = form.appendChild(el("input", "prop-name"));
    name.type = "text";
    name.placeholder = t("Name"); name.setAttribute("aria-label", t("Name")); name.required = true;
    name.setAttribute("list", "prop-names");
    const list = form.appendChild(el("datalist"));
    list.id = "prop-names";
    for (const known of state.propertyNames || []) list.appendChild(el("option")).value = known.name;
    const kind = form.appendChild(el("select"));
    kind.setAttribute("aria-label", t("Kind"));
    for (const [value, label] of TYPES) { const o = kind.appendChild(el("option", "", t(label))); o.value = value; }
    let input = form.appendChild(propInput("text", ""));
    input.setAttribute("aria-label", t("Value"));
    kind.onchange = () => { const next = propInput(kind.value, ""); next.setAttribute("aria-label", t("Value")); input.replaceWith(next); input = next; };
    const ok = form.appendChild(el("button", "ok", "Add"));
    ok.type = "submit";
    form.onsubmit = (event) => {
      event.preventDefault();
      const value = fromInput(kind.value, input);
      if (!name.value.trim() || value === null || (Array.isArray(value) && !value.length)) { toast(t("Give the property a name and a value.")); return; }
      setProp(note, name.value.trim(), value);
    };
    add.replaceWith(form);
    name.focus();
  };
  props.after(add);
  document.querySelectorAll(".info .add-prop").forEach(b => { if (b !== add) b.remove(); });
  document.querySelectorAll(".info .prop-form").forEach(f => f.remove());
}

// -- lists and the start page ----------------------------------------------------------------

function clearInfo() {
  state.current = null;
  ["outline", "backlinks", "mini-graph", "related", "mentions", "props"].forEach(id => document.getElementById(id).replaceChildren());
  document.title = t("Starlight notes");
  markCurrent();
}

// text with the parts at marks ([start, end] pairs) marked, as search hits are
function marked(cls, text, marks = []) {
  const box = el("div", cls);
  let at = 0;
  for (const [start, end] of marks) {
    if (start < at || end > text.length) continue;
    box.append(text.slice(at, start), el("mark", "", text.slice(start, end)));
    at = end;
  }
  box.append(text.slice(at));
  return box;
}

function card(path, snippet, hit) {
  const note = state.notes.find(n => n.path === path);
  const b = el("button", "card-note");
  b.type = "button";
  b.setAttribute("translate", "no");
  const title = note ? titleOf(note) : nameOf(path);
  b.appendChild(hit && hit.title === title ? marked("t", title, hit.title_marks) : el("div", "t", title));
  b.appendChild(el("div", "w", folderOf(path)));
  if (snippet) b.appendChild(hit ? marked("s", snippet, hit.marks) : el("div", "s", snippet));
  b.onclick = () => go({ path });
  return b;
}

function listOf(paths, snippets = {}, hits = {}) {
  const list = el("ul", "cards");
  for (const path of paths) list.appendChild(el("li")).appendChild(card(path, snippets[path], hits[path]));
  return list;
}

async function showList(heading, url) {
  clearInfo();
  const data = await api(url);
  column.replaceChildren(el("h1", "title", heading));
  column.firstChild.setAttribute("translate", "no");
  column.appendChild(el("p", "dim", data.notes.length === 1 ? t("1 note") : data.notes.length + " " + t("notes")));
  column.appendChild(listOf(data.notes.map(n => n.path)));
}

async function showSearch(q) {
  clearInfo();
  document.getElementById("search").value = q;
  const data = await api("/api/notes/search?" + new URLSearchParams({ q }));
  column.replaceChildren(el("h1", "title", "“" + q + "”"));
  column.firstChild.setAttribute("translate", "no");
  const tips = el("p", "dim small search-tips", t("Search tips:") + " ");
  tips.appendChild(el("code", "", 'tag:#work  path:Journal  "exact words"  -word'));
  tips.lastChild.setAttribute("translate", "no");
  if (!data.hits.length) {
    column.append(el("p", "dim", t("Nothing found. Try fewer or other words.")), tips);
    return;
  }
  column.appendChild(el("p", "dim", data.hits.length === 1 ? t("1 note") : data.hits.length + " " + t("notes")));
  const byPath = Object.fromEntries(data.hits.map(h => [h.path, h]));
  column.appendChild(listOf(data.hits.map(h => h.path), Object.fromEntries(data.hits.map(h => [h.path, h.snippet])), byPath));
  column.appendChild(tips);
}

function showHome() {
  clearInfo();
  const hello = el("div", "hello");
  hello.appendChild(el("div", "big", "🗒"));
  if (!state.notes.length) {
    hello.appendChild(el("h1", "", t("No notes yet")));
    hello.appendChild(el("p", "dim", t("Ask Starlight in the chat to write one, for example: “Make a note: buy milk”.")));
    column.replaceChildren(hello);
    return;
  }
  hello.appendChild(el("h1", "", t("Your notes")));
  hello.appendChild(el("p", "dim", t("Choose a note on the left, or press Ctrl+K to find one.")));
  column.replaceChildren(hello);
  const last = recent().filter(p => state.notes.some(n => n.path === p)).slice(0, 6);
  if (last.length) {
    column.appendChild(el("h3", "dim small", t("Opened lately")));
    column.appendChild(listOf(last));
  }
}

function showView(view) {
  if (view === "today") {
    const path = "Journal/" + today() + ".md";
    if (state.notes.some(n => n.path === path)) return go({ path }, false);
    clearInfo(); state.view = "today"; markCurrent();
    column.replaceChildren(calendarStrip(today()), el("h1", "title", t("Today")));
    const box = column.appendChild(el("div", "soon"));
    box.appendChild(el("p", "", t("No journal page for today yet.")));
    const write = box.appendChild(el("button", "ok", "Write today's page"));
    write.type = "button";
    write.onclick = () => writeDay(today());
    return;
  }
  if (view === "inbox") {
    clearInfo(); state.view = "inbox"; markCurrent();
    column.replaceChildren(el("h1", "title", t("Inbox")));
    const inbox = state.notes.filter(n => folderOf(n.path) === "Inbox");
    if (!inbox.length) {
      column.appendChild(el("div", "soon", t("The inbox is empty. Notes written without a title land here; clipped pages go to Clippings.")));
      return;
    }
    column.appendChild(el("p", "dim", t("Notes written without a title wait here. Starlight can file them into your notes; it asks before moving anything.")));
    const sort = column.appendChild(el("button", "ok", "Sort my inbox"));
    sort.type = "button";
    sort.onclick = () => {
      const ask = window.starlightLanguage === "de" ? "Sortiere meinen Eingang in meine Notizen." : "Sort my inbox into my notes.";
      location.href = "/?" + new URLSearchParams({ ask });
    };
    column.appendChild(listOf(inbox.map(n => n.path)));
    return;
  }
  if (view === "trash") return showTrash();
  if (view === "tasks") return showTasks();
  if (view === "boards") return showBoards();
  if (view === "cards") return showCards();
  return showGraph();
}

