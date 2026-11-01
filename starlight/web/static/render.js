// Notes in Starlight's Markdown (docs/plan-notes-app.md, block B4). Fills in markdown.js's
// hooks, for chat answers and the Notes page alike:
//   [[Note]], [[Note|shown text]], [[Note#Heading]]  a link to the note on the Notes page
//   ![[photo.png]], ![[photo.png|300]]                a picture from the notebook folder
//   ![[Note]]                                         an embedded note (a link for now)
//   #tag, #work/projects                              a tag
//   > [!tip] Title  (note, tip, warning, quote, todo) a callout; [!tip]- folded, [!tip]+ open
//   - [ ] milk, - [x] bread                           a task box (read-only here)
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
  return m[2] || "";
};
