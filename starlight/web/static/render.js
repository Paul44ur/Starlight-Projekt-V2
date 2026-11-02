// Notes in Starlight's Markdown (docs/plan-notes-app.md, block B4). Fills in markdown.js's
// hooks, for chat answers and the Notes page alike:
//   [[Note]], [[Note|shown text]], [[Note#Heading]]  a link to the note on the Notes page
//   ![[photo.png]], ![[photo.png|300]]                a picture from the notebook folder
//   ![[Note]]                                         an embedded note (a link for now)
//   #tag, #work/projects                              a tag
//   > [!tip] Title  (note, tip, warning, quote, todo) a callout; [!tip]- folded, [!tip]+ open
//   - [ ] milk, - [x] bread                           a task box (read-only here; the
//                                                     Notes page makes it tick)
//   - [ ] Call Tom due: 2026-11-05 priority: high     its fields as chips: the due date red
//                                                     when overdue, amber when today
//   ```query  where: status is open  ...  ```         a live table of notes (on the Notes
//                                                     page only: it asks /api/notes/query)
// As in markdown.js, the text only ever becomes textContent. Links lead only to the Notes
// page, pictures only come from /api/notes/file/ inside the notebook folder.

const RENDER_TAG = /(?<![\p{L}\p{N}_#&/\\])#([\p{L}\p{N}_\-/]+)/gu;
const RENDER_PICTURE = /\.(png|jpe?g|gif|webp|svg)$/i;
const RENDER_TASK = /^\[(.)\](?:\s+([\s\S]*))?$/;
const RENDER_CALLOUT = /^\s*\[!([\w-]+)\]([+-]?)\s*(.*)$/;
// a callout's kind (other names lead to these five) and its icon
const CALLOUT_KINDS = {
  note: "note", info: "note", abstract: "note", summary: "note",
  tip: "tip", hint: "tip", success: "tip", check: "tip",
  warning: "warning", caution: "warning", attention: "warning", important: "warning",
  danger: "warning", error: "warning", bug: "warning", failure: "warning",
  quote: "quote", cite: "quote",
  todo: "todo", task: "todo", question: "todo", faq: "todo",
};
const CALLOUT_ICONS = { note: "ℹ", tip: "💡", warning: "⚠", quote: "❝", todo: "☑" };

// [[target#heading|shown]] in its parts ("\|" is how a table writes "|")
function noteLinkParts(inner) {
  const bar = inner.search(/(?<!\\)\|/);
  let target = (bar >= 0 ? inner.slice(0, bar) : inner).replace(/\\$/, "").trim();
  const shown = bar >= 0 ? inner.slice(bar + 1).trim() : "";
  let heading = "";
  const hash = target.indexOf("#");
  if (hash >= 0) { heading = target.slice(hash + 1).replace(/^\^/, "").trim(); target = target.slice(0, hash).trim(); }
  return { target: target.replace(/\.md$/i, ""), heading, shown };
}

// The Notes page, opening a note (and a heading in it)
function noteHref(target, heading) {
  const query = new URLSearchParams({ open: target });
  if (heading) query.set("heading", heading);
  return "/notes?" + query.toString();
}

// A picture's address in the notebook folder; a bare name is in Notes/Attachments, where
// the Notes page puts pictures. Nothing outside: no "..", no full paths, no addresses.
function notePictureSrc(target) {
  const parts = target.replace(/\\/g, "/").split("/");
  if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith("/")
      || parts.some(p => !p || p === "." || p === ".." || p.startsWith("."))) return null;
  const path = parts.length > 1 ? parts : ["Notes", "Attachments", ...parts];
  return "/api/notes/file/" + path.map(encodeURIComponent).join("/");
}

mdHooks.wikilink = (inner, embed) => {
  const link = noteLinkParts(inner);
  if (embed && RENDER_PICTURE.test(link.target)) {
    const src = notePictureSrc(link.target);
    if (src) {
      const img = el("img", "note-picture");
      img.src = src; img.alt = /^\d+$/.test(link.shown) ? link.target : (link.shown || link.target);
      img.loading = "lazy";
      if (/^\d+$/.test(link.shown)) img.width = Math.min(parseInt(link.shown, 10), 2000);
      return img;
    }
  }
  const label = link.shown || (link.target || "") + (link.heading ? (link.target ? " › " : "") + link.heading : "");
  if (!link.target) return el("span", "note-link", label);  // [[#Heading]]: a part of the same note
  const a = el("a", embed ? "note-link note-embed" : "note-link", label);
  a.href = noteHref(link.target, link.heading);
  a.title = link.target + (link.heading ? " › " + link.heading : "");
  return a;
};

mdHooks.text = (parent, line) => {
  let last = 0;
  for (const m of line.matchAll(RENDER_TAG)) {
    const name = m[1].replace(/[/-]+$/, "");
    if (!name || /^[\d/-]+$/.test(name)) continue;  // "#1" is a number, not a tag
    if (m.index > last) parent.appendChild(document.createTextNode(line.slice(last, m.index)));
    const tag = el("a", "tag", "#" + name);
    tag.href = "/notes?" + new URLSearchParams({ tag: name }).toString();
    parent.appendChild(tag);
    last = m.index + 1 + name.length;
  }
  if (last < line.length) parent.appendChild(document.createTextNode(line.slice(last)));
};

mdHooks.quote = (parent, quoted) => {
  const m = quoted[0].match(RENDER_CALLOUT);
  if (!m) return false;
  const kind = CALLOUT_KINDS[m[1].toLowerCase()] || "note";
  const folds = m[2] !== "";
  const box = el(folds ? "details" : "div", "callout callout-" + kind);
  if (m[2] === "+") box.open = true;
  const title = el(folds ? "summary" : "div", "callout-title");
  title.appendChild(el("span", "callout-icon", CALLOUT_ICONS[kind]));
  const words = m[3] || m[1].charAt(0).toUpperCase() + m[1].slice(1).toLowerCase();
  mdInline(title.appendChild(el("span", "callout-text")), words);
  box.appendChild(title);
  const rest = quoted.slice(1);
  if (rest.some(line => line.trim())) mdBlocks(box.appendChild(el("div", "callout-body")), rest);
  parent.appendChild(box);
  return true;
};

// A task's fields in plain words, as starlight/notebook/markdown.py reads them (English or
// German keys; a date of more than one word as typed, "next friday"); none inside code.
const TASK_KEYS = {
  due: "due", "fällig": "due", faellig: "due", start: "start", beginn: "start",
  done: "done", erledigt: "done", priority: "priority", "priorität": "priority",
  prioritaet: "priority", repeat: "repeat", wiederholen: "repeat", wiederholung: "repeat",
  status: "stage",
};
const TASK_FIELD = new RegExp("(?<!\\S)(" + Object.keys(TASK_KEYS).join("|") + "):[ \\t]*", "giu");
const TASK_PHRASE = /^(?:(?:in|nach)[ \t]+\d{1,3}[ \t]+\S+|(?:next|nächste[nrs]?|naechste[nrs]?|kommende[nrs]?|this|diese[nrs]?)[ \t]+\S+)/iu;
const TASK_PRIORITY = { high: "high", hoch: "high", low: "low", niedrig: "low" };
const TASK_DOING = /^(doing|läuft|laeuft|aktiv|active|wip|started|begonnen|in\s+arbeit|in\s+progress)$/i;

function taskFields(text) {
  const masked = text.replace(/(`+)[\s\S]*?\1/g, (code) => " ".repeat(code.length));
  const matches = [...masked.matchAll(TASK_FIELD)], fields = {}, cuts = [];
  matches.forEach((m, n) => {
    const name = TASK_KEYS[m[1].toLowerCase()];
    const start = m.index + m[0].length;
    const rest = text.slice(start, n + 1 < matches.length ? matches[n + 1].index : text.length);
    const phrase = name === "stage" ? rest.match(/^in[ \t]+(?:arbeit|progress)\b/i)
      : name !== "priority" && name !== "repeat" && rest.match(TASK_PHRASE);
    const value = name === "repeat" ? rest.split(/(?<!\S)[#^]\S/)[0].trimEnd()
      : phrase ? phrase[0] : (rest.match(/^\S*/) || [""])[0];
    if (!value || fields[name] !== undefined) return;
    fields[name] = value;
    cuts.push([m.index, start + value.length]);
  });
  let shown = text;
  for (const [a, b] of cuts.reverse()) shown = shown.slice(0, a) + shown.slice(b);
  return { fields, text: shown.replace(/[ \t]{2,}/g, " ").trim() };
}

function isoToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// A date as people read it ("5 Nov", "5. Nov."; with the year when it is not this year)
function shownDate(iso) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const day = new Date(iso + "T12:00:00");
  const german = window.starlightLanguage === "de";
  const options = { day: "numeric", month: "short" };
  if (day.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return day.toLocaleDateString(german ? "de-DE" : "en-GB", options);
}

const taskWords = (words) => (typeof t === "function" ? t(words) : words);

// A chip for one of a task's fields; the due date is red when overdue, amber when today
function taskChip(name, value, open = true, today = isoToday()) {
  const icons = { due: "📅", start: "🛫", done: "✅", repeat: "🔁" };
  const titles = { due: "Due", start: "Starts", done: "Done on", repeat: "Repeats" };
  if (name === "stage") {  // status: doing (the task board's middle column); "open" says nothing
    if (!TASK_DOING.test(value.trim())) return null;
    const chip = el("span", "task-chip task-stage", "▶ " + taskWords("Doing"));
    chip.title = taskWords("Doing");
    return chip;
  }
  if (name === "priority") {
    const level = TASK_PRIORITY[value.toLowerCase()];
    if (!level) return null;
    const chip = el("span", "task-chip task-priority " + level, level === "high" ? "⏫" : "🔽");
    chip.title = taskWords(level === "high" ? "High priority" : "Low priority");
    return chip;
  }
  const chip = el("span", "task-chip task-" + name, icons[name] + " " + (name === "repeat" ? value : shownDate(value)));
  chip.title = taskWords(titles[name]) + ": " + value;
  if (name !== "repeat") chip.dataset.date = value;
  if (name === "due" && open && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    if (value < today) chip.classList.add("overdue");
    else if (value === today) chip.classList.add("today");
  }
  return chip;
}

mdHooks.item = (li, text) => {
  const m = text.match(RENDER_TASK);
  if (!m) return text;
  const box = el("input", "task-box");
  box.type = "checkbox"; box.disabled = true; box.checked = m[1] !== " ";
  box.setAttribute("aria-label", (m[2] || "").split("\n")[0] || "task");
  const mark = m[1].toLowerCase();
  li.classList.add("task");
  li.dataset.status = mark === " " ? "open" : mark === "x" ? "done" : mark === "-" ? "cancelled" : "other";
  li.appendChild(box);
  if (li.parentElement) li.parentElement.classList.add("task-list");
  const { fields, text: words } = taskFields(m[2] || "");
  if (!Object.keys(fields).length) return m[2] || "";
  // the words, then the fields as chips (the rest of the item goes on below them)
  mdInline(li.appendChild(el("span", "task-text")), words);
  for (const name of ["stage", "due", "start", "repeat", "priority", "done"]) {
    const chip = fields[name] && (name !== "stage" || mark === " ") && taskChip(name, fields[name], mark === " ");
    if (chip) li.appendChild(chip);
  }
  return "";
};

// -- query blocks (block E4) ---------------------------------------------------------------
// ```query
// where: status is open and tag is work
// sort: due
// fields: status, due
// ```
// becomes a table of the notes it finds, fetched again each time the note is shown (and by
// its ↻ button). A click on a column sorts by it; a second click turns the order round. Only
// the Notes page sets window.starlightQueryTables: elsewhere the block stays as code.

function queryCell(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "✓" : "–";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const queryEmpty = (v) => v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length);

function queryCompare(a, b) {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return queryCell(a).localeCompare(queryCell(b), undefined, { numeric: true, sensitivity: "base" });
}

function queryTable(text) {
  const box = el("div", "query-box");
  box.dataset.query = text;
  let data = null, sortBy = null, down = false;
  const draw = () => {
    box.replaceChildren();
    const columns = ["name", ...data.fields.filter(f => f.toLowerCase() !== "name")];
    const rows = [...data.notes];
    if (sortBy) {  // (empty cells last, either way round)
      rows.sort((a, b) => (queryEmpty(a[sortBy]) - queryEmpty(b[sortBy]))
        || (down ? -1 : 1) * queryCompare(a[sortBy], b[sortBy]));
    }
    const table = el("table", "md-table query-table");
    const head = table.createTHead().insertRow();
    for (const column of columns) {
      const th = head.appendChild(el("th"));
      const button = th.appendChild(el("button", "query-sort", column === "name" ? taskWords("Note") : column));
      button.type = "button";
      button.title = taskWords("Sort by this column");
      if (sortBy === column) button.appendChild(el("span", "query-arrow", down ? " ▼" : " ▲"));
      th.setAttribute("aria-sort", sortBy === column ? (down ? "descending" : "ascending") : "none");
      button.onclick = () => { down = sortBy === column ? !down : false; sortBy = column; draw(); };
    }
    const tbody = table.createTBody();
    for (const note of rows) {
      const row = tbody.insertRow();
      for (const column of columns) {
        const cell = row.insertCell();
        if (column === "name") {
          const a = cell.appendChild(el("a", "note-link", note.name));
          a.href = noteHref(note.path.replace(/\.md$/i, ""), "");
        } else {
          cell.textContent = queryCell(note[column]);
        }
      }
    }
    const scroller = el("div", "md-table-wrap");
    scroller.appendChild(table);
    box.appendChild(scroller);
    const foot = box.appendChild(el("div", "query-foot"));
    foot.appendChild(el("span", "", data.count === 1 ? taskWords("1 note") : data.count + " " + taskWords("notes")));
    const again = foot.appendChild(el("button", "query-again", "↻"));
    again.type = "button";
    again.title = taskWords("Look again");
    again.setAttribute("aria-label", taskWords("Look again"));
    again.onclick = load;
  };
  async function load() {
    try {
      data = await api("/api/notes/query", { method: "POST", body: { text } });
    } catch (e) {
      box.replaceChildren(el("div", "query-error", taskWords("This table could not be made:") + " " + e.message));
      return;
    }
    draw();
  }
  box.appendChild(el("div", "query-foot", "…"));
  load();
  return box;
}

mdHooks.code = (lang, text) => (lang === "query" && window.starlightQueryTables ? queryTable(text) : null);
