// The board view on the Notes page (docs/plan-notes-app.md, block F4): a .canvas board
// (JSON Canvas) as cards and arrows on an endless surface. Loaded only when a board is
// opened (notes.html imports it then).
//
//   cards: text (Markdown, shown as text only), a note (its title and beginning), a picture,
//          a web link (http and https only), a group (a frame around cards)
//   drag a card to move it (a group takes its cards along), its corner to resize it, a dot
//   on its edge to another card to draw an arrow; drag the background to move around, the
//   wheel to zoom; double-click a text card to write in it, the background for a new card;
//   Delete removes what is selected. Places snap to a 20-point grid (hold Alt for free).
//   Every change is saved a moment later with the stamp the board was read with: a board
//   changed elsewhere meanwhile is never overwritten.

const GRID = 20;
// The six preset colours of a .canvas board are tokens, so they suit the night and the day look.
// (A colour written as #rrggbb in the file is the user's own and stays as it is.)
const COLORS = { 1: "var(--bad)", 2: "var(--warn)", 3: "var(--hl-v)", 4: "var(--ok)", 5: "var(--hl-f)", 6: "var(--hl-k)" };
const PICTURE = /\.(png|jpe?g|gif|webp|svg)$/i;
const SVGNS = "http://www.w3.org/2000/svg";

function svgEl(tag, attributes = {}) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

function newId(data) {
  const taken = new Set([...data.nodes.map(n => n.id), ...data.edges.map(e => e.id)]);
  for (;;) {
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    const id = [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
    if (!taken.has(id)) return id;
  }
}

function colorOf(value) {
  if (!value) return "";
  if (COLORS[value]) return COLORS[value];
  return /^#[0-9a-f]{6}$/i.test(value) ? value : "";
}

// Where an arrow leaves or reaches a card: the middle of that side.
function sidePoint(card, side) {
  const { x, y, width: w, height: h } = card;
  return { top: [x + w / 2, y], bottom: [x + w / 2, y + h], left: [x, y + h / 2], right: [x + w, y + h / 2] }[side]
    || [x + w / 2, y + h / 2];
}

function nearestSides(a, b) {
  const ac = [a.x + a.width / 2, a.y + a.height / 2], bc = [b.x + b.width / 2, b.y + b.height / 2];
  const dx = bc[0] - ac[0], dy = bc[1] - ac[1];
  if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? ["right", "left"] : ["left", "right"];
  return dy > 0 ? ["bottom", "top"] : ["top", "bottom"];
}

export function openBoard(parent, board, options) {
  const { api, el, t, renderMarkdown, toast } = options;
  const data = board.board;
  data.nodes ||= []; data.edges ||= [];
  let stamp = board.stamp, first = true, dirty = false, saving = null, timer = null;
  const view = { k: 1, x: 40, y: 40 };
  let selected = null;  // { kind: "node" | "edge", id }

  const bar = parent.appendChild(el("div", "board-bar"));
  const stage = parent.appendChild(el("div", "board-stage"));
  stage.tabIndex = 0;
  stage.setAttribute("aria-label", t("Board"));
  const world = stage.appendChild(el("div", "board-world"));
  const arrows = world.appendChild(svgEl("svg", { class: "board-arrows", width: 1, height: 1 }));
  const defs = arrows.appendChild(svgEl("defs"));
  const head = defs.appendChild(svgEl("marker", { id: "board-head", viewBox: "0 0 10 10", refX: 9, refY: 5,
    markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }));
  head.appendChild(svgEl("path", { d: "M0,0 L10,5 L0,10 z", class: "board-head" }));
  const status = el("span", "save-state board-state");
  status.setAttribute("role", "status");
  const cardEls = new Map();

  // -- saving ------------------------------------------------------------------------------
  const say = (text, bad = false) => { status.textContent = t(text); status.classList.toggle("bad", bad); };
  function changed() {
    dirty = true; say("Editing…");
    clearTimeout(timer);
    timer = setTimeout(save, 700);
  }
  async function save() {
    clearTimeout(timer);
    while (saving) await saving;
    if (!dirty) return true;
    dirty = false; say("Saving…");
    saving = (async () => {
      const response = await fetch("/api/board?" + new URLSearchParams({ path: board.path }), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ board: data, stamp, first }),
      });
      const answer = await response.json().catch(() => ({}));
      if (response.status === 409) {
        dirty = true;
        say("Not saved: changed elsewhere", true);
        const again = el("button", "board-reload", "Load it again");
        again.type = "button";
        again.textContent = t("Load it again");
        again.onclick = () => options.reopen();
        status.after(again);
        return false;
      }
      if (!response.ok) {
        dirty = true;
        say(t("Not saved:") + " " + (typeof answer.detail === "string" ? answer.detail : response.statusText), true);
        return false;
      }
      stamp = answer.stamp; first = false;
      say(dirty ? "Editing…" : "Saved");
      return true;
    })();
    const ok = await saving;
    saving = null;
    return ok;
  }

  // -- drawing -----------------------------------------------------------------------------
  const node = (id) => data.nodes.find(n => n.id === id);
  function place(card) {
    const box = cardEls.get(card.id);
    if (!box) return;
    Object.assign(box.style, { left: card.x + "px", top: card.y + "px", width: card.width + "px", height: card.height + "px" });
  }
  function drawArrows() {
    [...arrows.querySelectorAll("g.board-edge")].forEach(g => g.remove());
    for (const edge of data.edges) {
      const from = node(edge.fromNode), to = node(edge.toNode);
      if (!from || !to) continue;
      const [fs, ts] = nearestSides(from, to);
      const [x1, y1] = sidePoint(from, edge.fromSide || fs), [x2, y2] = sidePoint(to, edge.toSide || ts);
      const g = arrows.appendChild(svgEl("g", { class: "board-edge" + (selected?.id === edge.id ? " selected" : "") }));
      g.dataset.id = edge.id;
      const path = `M${x1},${y1} L${x2},${y2}`;
      g.appendChild(svgEl("path", { d: path, class: "board-hit" }));
      const line = g.appendChild(svgEl("path", { d: path, class: "board-line" }));
      const color = colorOf(edge.color);
      if (color) line.style.stroke = color;
      if ((edge.toEnd || "arrow") === "arrow") line.setAttribute("marker-end", "url(#board-head)");
      if (edge.fromEnd === "arrow") line.setAttribute("marker-start", "url(#board-head)");
      if (edge.label) {
        const label = g.appendChild(svgEl("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 6, "text-anchor": "middle", class: "board-label" }));
        label.textContent = edge.label;
      }
      g.addEventListener("pointerdown", (event) => { event.stopPropagation(); select({ kind: "edge", id: edge.id }); });
    }
  }
  function content(card, box) {
    const body = box.appendChild(el("div", "board-body md"));
    if (card.type === "text") {
      renderMarkdown(body, String(card.text || ""));
    } else if (card.type === "file") {
      const file = String(card.file || "");
      const title = body.appendChild(el("a", "board-file", file.split("/").pop().replace(/\.md$/i, "")));
      title.href = "/notes?" + new URLSearchParams({ path: file });
      title.onclick = (event) => { event.preventDefault(); options.openNote(file); };
      if (PICTURE.test(file)) {
        const img = body.appendChild(el("img", "board-picture"));
        img.alt = file.split("/").pop(); img.draggable = false;
        img.src = "/api/notes/file/" + file.split("/").map(encodeURIComponent).join("/");
      } else if (/\.md$/i.test(file)) {
        const preview = body.appendChild(el("div", "board-preview"));
        api("/api/note?" + new URLSearchParams({ path: file }))
          .then(note => renderMarkdown(preview, note.body.slice(0, 800)))
          .catch(() => preview.appendChild(el("p", "dim", t("This note is not there any more."))));
      }
    } else if (card.type === "link") {
      const url = String(card.url || "");
      if (/^https?:\/\//i.test(url)) {
        const a = body.appendChild(el("a", "board-link", url));
        a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
        try { body.appendChild(el("div", "dim small", new URL(url).hostname)); } catch (e) { /* (shown as is) */ }
      } else {
        body.appendChild(el("span", "dim", url));  // (anything else stays text)
      }
    } else if (card.type === "group") {
      box.classList.add("board-group");
      body.remove();
      box.appendChild(el("div", "board-group-label", String(card.label || "")));
    } else {
      body.appendChild(el("span", "dim", "(" + card.type + ")"));
    }
  }
  function drawCard(card) {
    cardEls.get(card.id)?.remove();
    const box = el("div", "board-card board-" + card.type + (selected?.id === card.id ? " selected" : ""));
    box.dataset.id = card.id;
    const color = colorOf(card.color);
    if (color) box.style.setProperty("--card", color);
    content(card, box);
    for (const side of ["top", "right", "bottom", "left"]) {
      const dot = box.appendChild(el("span", "board-dot board-dot-" + side));
      dot.dataset.side = side;
      dot.title = t("Drag to another card to draw an arrow");
    }
    const handle = box.appendChild(el("span", "board-resize"));
    handle.title = t("Drag to resize");
    cardEls.set(card.id, box);
    // groups first (behind), then the others in their order
    if (card.type === "group") world.insertBefore(box, arrows.nextSibling); else world.appendChild(box);
    place(card);
  }
  function drawAll() {
    cardEls.forEach(box => box.remove());
    cardEls.clear();
    for (const card of data.nodes.filter(n => n.type === "group")) drawCard(card);
    for (const card of data.nodes.filter(n => n.type !== "group")) drawCard(card);
    drawArrows();
  }
  function applyView() {
    world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
  }
  function select(next) {
    selected = next;
    cardEls.forEach((box, id) => box.classList.toggle("selected", next?.kind === "node" && next.id === id));
    drawArrows();
  }

  // -- changing it -------------------------------------------------------------------------
  const snap = (value, free) => (free ? Math.round(value) : Math.round(value / GRID) * GRID);
  function addCard(type, fields, at) {
    const middle = at || [(stage.clientWidth / 2 - view.x) / view.k - 130, (stage.clientHeight / 2 - view.y) / view.k - 70];
    const card = { id: newId(data), type, x: snap(middle[0]), y: snap(middle[1]),
                   width: type === "group" ? 420 : 260, height: type === "group" ? 300 : 140, ...fields };
    data.nodes.push(card);
    drawCard(card);
    if (type === "group") drawAll();
    select({ kind: "node", id: card.id });
    changed();
    return card;
  }
  function remove() {
    if (!selected) return;
    if (selected.kind === "edge") {
      data.edges = data.edges.filter(e => e.id !== selected.id);
    } else {
      data.nodes = data.nodes.filter(n => n.id !== selected.id);
      data.edges = data.edges.filter(e => e.fromNode !== selected.id && e.toNode !== selected.id);
      cardEls.get(selected.id)?.remove();
      cardEls.delete(selected.id);
    }
    selected = null;
    drawArrows();
    changed();
  }
  function editText(card) {
    const box = cardEls.get(card.id);
    if (!box || card.type !== "text") return;
    const area = el("textarea", "board-edit");
    area.value = card.text || "";
    area.setAttribute("aria-label", t("Card text"));
    box.querySelector(".board-body").replaceWith(area);
    area.focus();
    const done = () => {
      if (card.text !== area.value) { card.text = area.value; changed(); }
      drawCard(card);
      drawArrows();
    };
    area.addEventListener("blur", done, { once: true });
    area.addEventListener("keydown", (event) => { if (event.key === "Escape") area.blur(); event.stopPropagation(); });
  }
  const inside = (card, group) => group.x <= card.x && group.y <= card.y
    && card.x + card.width <= group.x + group.width && card.y + card.height <= group.y + group.height;

  // -- the pointer ---------------------------------------------------------------------------
  const toBoard = (event) => {
    const box = stage.getBoundingClientRect();
    return [(event.clientX - box.left - view.x) / view.k, (event.clientY - box.top - view.y) / view.k];
  };
  let held = null;
  let ghost = null;
  stage.addEventListener("pointerdown", (event) => {
    if (event.target.closest("textarea, a")) return;
    const box = event.target.closest(".board-card");
    const [px, py] = toBoard(event);
    stage.focus({ preventScroll: true });
    if (!box) {
      select(null);
      held = { kind: "pan", x: event.clientX, y: event.clientY };
    } else {
      const card = node(box.dataset.id);
      select({ kind: "node", id: card.id });
      if (event.target.classList.contains("board-resize")) {
        held = { kind: "resize", card, start: [px, py], size: [card.width, card.height] };
      } else if (event.target.classList.contains("board-dot")) {
        held = { kind: "connect", card, side: event.target.dataset.side };
        ghost = arrows.appendChild(svgEl("path", { class: "board-line board-ghost" }));
      } else {
        const riders = card.type === "group" ? data.nodes.filter(n => n !== card && inside(n, card)) : [];
        held = { kind: "move", card, start: [px, py], from: [card.x, card.y], riders: riders.map(n => [n, n.x, n.y]) };
      }
    }
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener("pointermove", (event) => {
    if (!held) return;
    const [px, py] = toBoard(event);
    const free = event.altKey;
    if (held.kind === "pan") {
      view.x += event.clientX - held.x; view.y += event.clientY - held.y;
      held.x = event.clientX; held.y = event.clientY;
      applyView();
    } else if (held.kind === "move") {
      const dx = snap(held.from[0] + px - held.start[0], free) - held.card.x;
      const dy = snap(held.from[1] + py - held.start[1], free) - held.card.y;
      if (!dx && !dy) return;
      held.card.x += dx; held.card.y += dy;
      for (const [rider] of held.riders) { rider.x += dx; rider.y += dy; place(rider); }
      place(held.card);
      held.moved = true;
      drawArrows();
    } else if (held.kind === "resize") {
      held.card.width = Math.max(80, snap(held.size[0] + px - held.start[0], free));
      held.card.height = Math.max(40, snap(held.size[1] + py - held.start[1], free));
      place(held.card);
      held.moved = true;
      drawArrows();
    } else if (held.kind === "connect") {
      const [x1, y1] = sidePoint(held.card, held.side);
      ghost.setAttribute("d", `M${x1},${y1} L${px},${py}`);
    }
  });
  stage.addEventListener("pointerup", (event) => {
    if (!held) return;
    if (held.kind === "connect") {
      ghost?.remove(); ghost = null;
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest(".board-card");
      const to = target && node(target.dataset.id);
      if (to && to !== held.card) {
        const [, toSide] = nearestSides(held.card, to);
        data.edges.push({ id: newId(data), fromNode: held.card.id, fromSide: held.side, toNode: to.id, toSide });
        drawArrows();
        changed();
      }
    } else if (held.moved) {
      changed();
    }
    held = null;
  });
  stage.addEventListener("dblclick", (event) => {
    const box = event.target.closest(".board-card");
    if (box) { editText(node(box.dataset.id)); return; }
    const [px, py] = toBoard(event);
    editText(addCard("text", { text: "" }, [px - 130, py - 70]));
  });
  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    const box = stage.getBoundingClientRect();
    const cx = event.clientX - box.left, cy = event.clientY - box.top;
    const k = Math.min(Math.max(view.k * Math.exp(-event.deltaY * 0.0015), 0.1), 4);
    view.x = cx - (cx - view.x) * (k / view.k); view.y = cy - (cy - view.y) * (k / view.k);
    view.k = k;
    applyView();
  }, { passive: false });
  stage.addEventListener("keydown", (event) => {
    if (event.target.closest("textarea")) return;
    if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); remove(); }
  });

  // -- the bar -------------------------------------------------------------------------------
  const button = (label, tip, act) => {
    const b = bar.appendChild(el("button", "tool", label));
    b.type = "button"; b.title = t(tip); b.setAttribute("aria-label", t(tip));
    b.onclick = act;
    return b;
  };
  button("＋ " + t("Text"), "Add a text card", () => editText(addCard("text", { text: "" })));
  button("＋ " + t("Note"), "Add a note card", async () => {
    const name = ((await askText(t("Which note? (its name)"), "", { yes: "Add" })) || "").trim();
    if (!name) return;
    try {
      const found = await api("/api/notes/resolve?" + new URLSearchParams({ name }));
      addCard("file", { file: found.path, height: 220 });
    } catch (e) { toast(t("There is no note called") + " " + name); }
  });
  button("＋ " + t("Web link"), "Add a web link", async () => {
    const url = ((await askText(t("The web address (https://…)"), "", { yes: "Add", placeholder: "https://" })) || "").trim();
    if (!url) return;
    if (!/^https?:\/\/\S+$/i.test(url)) { toast(t("A link card needs a web address that starts with https://")); return; }
    addCard("link", { url, height: 100 });
  });
  button("＋ " + t("Group"), "Add a group", () => addCard("group", { label: t("Group") }));
  setIcon(button("", "Zoom out", () => { view.k = Math.max(view.k / 1.25, 0.1); applyView(); }), "minus");
  setIcon(button("", "Zoom in", () => { view.k = Math.min(view.k * 1.25, 4); applyView(); }), "plus");
  bar.appendChild(status);
  say("Saved");

  if (data.nodes.length) {  // the cards' top left corner in view (a group's label above it too)
    view.x = 40 - Math.min(...data.nodes.map(n => n.x));
    view.y = 50 - Math.min(...data.nodes.map(n => n.y));
  }
  drawAll();
  applyView();
  return { save, get dirty() { return dirty; }, data };
}
