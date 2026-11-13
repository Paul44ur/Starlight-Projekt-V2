// The views of their own: boards (F4), flashcards (G3) and the whole graph (F2).
// Part of the Notes page (notes.html), which loads its parts in this order: notes-page.js,
// notes-tasks.js, notes-info.js, notes-views.js, notes-find.js, notes-edit.js. They are
// plain scripts: what one declares at the top, the others use.

// -- boards (block F4) ---------------------------------------------------------------------
// The Boards view lists the .canvas boards; a board opens in board.js (loaded on first use).

async function showBoards() {
  clearInfo(); state.view = "boards"; markCurrent();
  column.replaceChildren(el("h1", "title", t("Boards")));
  const make = column.appendChild(el("button", "primary", "＋ New board"));
  make.type = "button";
  make.onclick = async () => {
    const title = ((await askText(t("What should the board be called?"), t("Board"), { yes: "Create" })) || "").trim();
    if (!title) return;
    try {
      const made = await api("/api/board", { method: "POST", body: { title } });
      go({ board: made.path });
    } catch (e) { toast(e.message); }
  };
  let found;
  try { found = await api("/api/boards"); } catch (e) { toast(e.message); return; }
  if (state.view !== "boards") return;
  if (!found.boards.length) {
    column.appendChild(el("div", "soon", t("No boards yet. A board holds cards and arrows: ideas, notes, links, side by side.")));
    return;
  }
  const list = column.appendChild(el("ul", "cards"));
  for (const board of found.boards) {
    const b = list.appendChild(el("li")).appendChild(el("button", "card-note"));
    b.type = "button";
    b.setAttribute("translate", "no");
    b.append(el("div", "t", board.title), el("div", "w", board.path.split("/").slice(0, -1).join("/")));
    b.onclick = () => go({ board: board.path });
  }
}

// -- flashcards (block G3) -----------------------------------------------------------------
// Lines "Question::Answer" in notes tagged #flashcards. The Flashcards view lists the decks;
// studying shows one card at a time: Space shows the answer, 1 to 4 grade it. A card not
// known ("Again") comes back at the end of the round, and the server keeps when each comes
// back (data/notebook/flashcards.json).

const GRADES = [["again", "Again"], ["hard", "Hard"], ["good", "Good"], ["easy", "Easy"]];

function inDays(days) {
  if (days <= 0) return t("today");
  if (days < 31) return t(days === 1 ? "in 1 day" : `in ${days} days`);
  if (days < 365) {
    const months = Math.round(days / 30);
    return t(months === 1 ? "in 1 month" : `in ${months} months`);
  }
  const years = Math.round(days / 365);
  return t(years === 1 ? "in 1 year" : `in ${years} years`);
}

async function showCards() {
  clearInfo(); state.view = "cards"; state.study = null; markCurrent();
  column.replaceChildren(el("h1", "title", t("Flashcards")));
  let data;
  try { data = await api("/api/notes/flashcards"); }
  catch (e) { if (state.view === "cards") column.appendChild(el("div", "soon", e.message)); return; }
  if (state.view !== "cards") return;
  if (!data.count) {
    const box = column.appendChild(el("div", "soon"));
    box.appendChild(el("p", "", t("No flashcards yet. Write lines like this in a note tagged #flashcards:")));
    box.appendChild(el("p")).appendChild(el("code", "", "Hola::Hello"));
    box.appendChild(el("p", "", t("Question:::Answer asks both ways; #flashcards/spanish puts the cards in a deck called spanish.")));
    return;
  }
  const head = column.appendChild(el("div", "deck-head"));
  head.appendChild(el("span", "dim", t(`Today: ${data.due} due, ${data.new} new`)));
  const all = head.appendChild(el("button", "primary", t("Study all")));
  all.type = "button";
  all.disabled = !(data.due + data.new);
  all.onclick = () => studyCards("");
  const list = column.appendChild(el("ul", "deck-list"));
  for (const deck of data.decks) {
    const row = list.appendChild(el("li", "deck-row"));
    row.dataset.deck = deck.deck;
    const words = row.appendChild(el("div", "deck-words"));
    words.appendChild(el("div", "deck-name", deck.deck)).setAttribute("translate", "no");
    const cards = deck.cards === 1 ? "1 card" : `${deck.cards} cards`;
    words.appendChild(el("div", "deck-facts num", t(`${cards} · ${deck.due} due · ${deck.new} new`)));
    const study = row.appendChild(el("button", "", t("Study")));
    study.type = "button";
    study.disabled = !(deck.due + deck.new);
    study.onclick = () => studyCards(deck.deck);
  }
}

async function studyCards(deck) {
  let data;
  try { data = await api("/api/notes/flashcards/due?" + new URLSearchParams({ deck, limit: 200 })); }
  catch (e) { toast(e.message); return; }
  if (state.view !== "cards") return;
  const queue = data.cards.slice();
  const round = { total: queue.length, known: 0, again: 0 };
  const title = el("h1", "title", deck || t("All decks"));
  if (deck) title.setAttribute("translate", "no");
  column.replaceChildren(title);
  const box = column.appendChild(el("div", "study"));
  const progress = box.appendChild(el("div", "study-progress"));
  const bar = progress.appendChild(el("div", "study-bar")).appendChild(el("span"));
  const count = progress.appendChild(el("span", "dim"));
  const face = box.appendChild(el("div", "study-card"));
  face.setAttribute("aria-live", "polite");
  const controls = box.appendChild(el("div", "study-controls"));
  box.appendChild(el("p", "study-hint", t("Space: show the answer · 1 to 4: grade it")));
  const back = column.appendChild(el("button", "icon study-back", "← " + t("Back to the decks")));
  back.type = "button";
  back.onclick = () => showCards();
  let step = 0, busy = false;

  const side = (text, card, cls) => {
    const part = face.appendChild(el("div", "md " + cls));
    part.setAttribute("translate", "no");
    renderMarkdown(part, text);
    showEmbeds(part, card.path, new Set([card.path]), 0);
  };
  function ask() {
    if (state.view !== "cards") return;
    if (!queue.length) return finish();
    const card = queue[0], now = ++step;
    bar.style.width = (100 * round.known / Math.max(1, round.total)) + "%";
    count.textContent = t(`${round.known} of ${round.total}`);
    face.replaceChildren();
    side(card.question, card, "study-q");
    const from = face.appendChild(el("a", "study-from", "↗ " + card.note));
    from.setAttribute("translate", "no");
    from.href = "/notes?" + new URLSearchParams({ path: card.path });
    from.onclick = (event) => { event.preventDefault(); go({ path: card.path }); };
    controls.replaceChildren();
    const reveal = controls.appendChild(el("button", "primary", t("Show answer")));
    reveal.type = "button";
    reveal.onclick = () => answer(card, now);
    state.study = (event) => {  // (a focused button takes Space and Enter itself)
      if (![" ", "Enter"].includes(event.key) || document.activeElement?.tagName === "BUTTON") return false;
      answer(card, now);
      return true;
    };
    reveal.focus();
  }
  function answer(card, now) {
    if (now !== step || face.querySelector(".study-a")) return;
    const from = face.querySelector(".study-from");
    from.before(el("hr"));
    side(card.answer, card, "study-a");
    face.appendChild(from);  // (the note's name stays at the bottom)
    controls.replaceChildren();
    GRADES.forEach(([grade, label], i) => {
      const b = controls.appendChild(el("button", "grade grade-" + grade));
      b.type = "button";
      b.dataset.grade = grade;
      b.append(el("span", "", t(label)), el("small", "", inDays(card.in_days[grade])));
      b.title = t(label) + " (" + (i + 1) + ")";
      b.onclick = () => mark(card, grade, now);
    });
    state.study = (event) => {
      const i = ["1", "2", "3", "4"].indexOf(event.key);
      if (i < 0) return false;
      mark(card, GRADES[i][0], now);
      return true;
    };
    controls.querySelector('[data-grade="good"]').focus();
  }
  async function mark(card, grade, now) {
    if (busy || now !== step) return;
    busy = true;
    let result;
    try { result = await api("/api/notes/flashcards/grade", { method: "POST", body: { id: card.id, grade } }); }
    catch (e) { toast(e.message); return; }
    finally { busy = false; }
    queue.shift();
    if (grade === "again") {
      round.again++;
      queue.push({ ...card, in_days: result.next_in_days || card.in_days });
    } else round.known++;
    ask();
  }
  function finish() {
    state.study = null;
    bar.style.width = "100%";
    count.textContent = t(`${round.known} of ${round.total}`);
    face.replaceChildren(el("p", "study-done", t("Done for now.")));
    const missed = round.again ? `, ${round.again} again` : "";
    if (round.total) face.appendChild(el("p", "dim", t(`${round.known} known${missed}`)));
    box.querySelector(".study-hint")?.remove();
    controls.replaceChildren();
    const more = controls.appendChild(el("button", "primary", t("Back to the decks")));
    more.type = "button";
    more.onclick = () => showCards();
    back.remove();
    more.focus();
  }
  ask();
}

let boardKit = null;
async function openBoardView(path) {
  clearInfo(); state.view = "boards"; markCurrent();
  let board;
  try { board = await api("/api/board?" + new URLSearchParams({ path })); }
  catch (e) { column.replaceChildren(el("p", "notice", e.message)); return; }
  try { boardKit ||= await import("/board.js"); }
  catch (e) { toast(e.message); return; }
  document.title = board.title + " · Starlight";
  document.body.classList.add("board-open");
  const title = el("h1", "title", board.title);
  title.setAttribute("translate", "no");
  column.replaceChildren(title);
  const host = column.appendChild(el("div", "board-host"));
  state.board = boardKit.openBoard(host, board, {
    api, el, t, renderMarkdown, toast,
    openNote: (file) => go({ path: file }),
    reopen: () => { state.board = null; openBoardView(path); },
  });
}

// -- the whole graph (block F2) ------------------------------------------------------------
// Every note as a point, coloured by its folder, bigger with more links; the lines are links.
// Drag the background to move it, the wheel (or + and −) to zoom, a point to move that point;
// a click on a point opens its note. Names show when zoomed in. Up to 300 points the picture
// is SVG (points can be reached with Tab and opened with Enter), beyond that a canvas.

// One colour per folder, taken from the page's tokens (so they suit the night and the day look).
const GRAPH_COLOURS = ["--accent", "--ok", "--warn", "--hl-f", "--bad", "--hl-v", "--hl-k", "--dim"];
const graphVar = (i) => "var(" + GRAPH_COLOURS[i % GRAPH_COLOURS.length] + ")";
// The canvas cannot use var(): it reads the tokens when it draws. When the look changes
// (the theme button, or the system's day and night) the picture is drawn again.
let graphLookWatch = null;
function watchLook(redraw) {
  graphLookWatch?.();
  const root = document.documentElement;
  const seen = new MutationObserver(redraw);
  seen.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  const media = window.matchMedia("(prefers-color-scheme: light)");
  media.addEventListener("change", redraw);
  graphLookWatch = () => { seen.disconnect(); media.removeEventListener("change", redraw); graphLookWatch = null; };
}
const graphFilter = { folder: "", tag: "", memories: false };

async function showGraph() {
  clearInfo(); state.view = "graph"; markCurrent();
  column.replaceChildren(el("h1", "title", t("Graph")));
  const bar = column.appendChild(el("div", "graph-bar"));
  const folders = bar.appendChild(el("select"));
  folders.setAttribute("aria-label", t("Folder"));
  folders.setAttribute("translate", "no");  // (folder names stay as they are)
  folders.appendChild(new Option(t("All folders"), ""));
  for (const name of [...new Set(state.notes.map(n => (n.path.includes("/") ? n.path.split("/")[0] : "")).filter(Boolean))].sort()) {
    folders.appendChild(new Option(name, name));
  }
  folders.value = graphFilter.folder;
  const tags = bar.appendChild(el("select"));
  tags.setAttribute("aria-label", t("Tag"));
  tags.setAttribute("translate", "no");
  tags.appendChild(new Option(t("All tags"), ""));
  for (const entry of state.tags || []) tags.appendChild(new Option("#" + entry.tag, entry.tag));
  tags.value = graphFilter.tag;
  const remember = bar.appendChild(el("label", "graph-check"));
  const box = remember.appendChild(el("input"));
  box.type = "checkbox"; box.checked = graphFilter.memories;
  remember.append(" " + t("Memories"));
  const counted = bar.appendChild(el("span", "dim small graph-count num"));
  const zoom = bar.appendChild(el("span", "graph-zoom"));
  const stage = column.appendChild(el("div", "graph-stage"));
  const legend = column.appendChild(el("div", "graph-legend"));
  legend.setAttribute("translate", "no");
  const load = async () => {
    Object.assign(graphFilter, { folder: folders.value, tag: tags.value, memories: box.checked });
    const query = { limit: 1500 };
    if (graphFilter.folder) query.folder = graphFilter.folder;
    if (graphFilter.tag) query.tag = graphFilter.tag;
    if (graphFilter.memories) query.memories = "true";
    let data;
    try { data = await api("/api/notes/graph?" + new URLSearchParams(query)); }
    catch (e) { toast(e.message); return; }
    if (state.view !== "graph") return;
    try { graphKit ||= await import("/vendor/graph.js"); }
    catch (e) { toast(e.message); return; }
    counted.textContent = data.nodes.length + " " + t(data.nodes.length === 1 ? "note" : "notes") + " · "
      + data.links.length + " " + t("links") + (data.capped ? " · " + t("only the ones with the most links") : "");
    drawWhole(stage, legend, zoom, data);
  };
  folders.onchange = tags.onchange = box.onchange = load;
  await load();
}

function drawWhole(stage, legend, zoom, data) {
  stage.replaceChildren(); legend.replaceChildren(); zoom.replaceChildren();
  if (!data.nodes.length) { stage.appendChild(el("div", "soon", t("No notes to show."))); return; }
  const nodes = data.nodes.map(n => ({ ...n })), links = data.links.map(l => ({ ...l }));
  const groups = [...new Set(nodes.map(n => n.folder.split("/")[0] || "—"))].sort();
  const colourIndex = (n) => groups.indexOf(n.folder.split("/")[0] || "—");
  const radius = (n) => Math.min(3.5 + Math.sqrt(n.links) * 2, 16);
  const simulation = graphKit.forceSimulation(nodes)
    .force("link", graphKit.forceLink(links).id(n => n.id).distance(42))
    .force("charge", graphKit.forceManyBody().strength(-70))
    .force("x", graphKit.forceX(0).strength(0.05))
    .force("y", graphKit.forceY(0).strength(0.05))
    .force("collide", graphKit.forceCollide(n => radius(n) + 3))
    .stop();
  for (let i = 0; i < 300; i++) simulation.tick();

  const W = Math.max(stage.clientWidth, 320), H = Math.max(380, Math.min(window.innerHeight - 260, 720));
  const view = { k: 1, x: 0, y: 0 };
  const fit = () => {
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    view.k = Math.min((W - 60) / Math.max(x1 - x0, 1), (H - 60) / Math.max(y1 - y0, 1), 2.5);
    view.x = W / 2 - (x0 + x1) / 2 * view.k; view.y = H / 2 - (y0 + y1) / 2 * view.k;
  };
  fit();
  const named = () => view.k >= 1.3 || nodes.length <= 25;
  const canvasMode = nodes.length > 300;
  let redraw;
  if (canvasMode) {
    const canvas = stage.appendChild(el("canvas", "whole-graph"));
    const ratio = window.devicePixelRatio || 1;
    canvas.width = W * ratio; canvas.height = H * ratio;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", t("A map of how your notes link together"));
    const ctx = canvas.getContext("2d");
    redraw = () => {
      const styles = getComputedStyle(document.documentElement);
      const token = (name) => styles.getPropertyValue(name).trim();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(ratio * view.k, 0, 0, ratio * view.k, ratio * view.x, ratio * view.y);
      ctx.strokeStyle = token("--border");
      ctx.lineWidth = 1 / view.k;
      ctx.beginPath();
      for (const l of links) { ctx.moveTo(l.source.x, l.source.y); ctx.lineTo(l.target.x, l.target.y); }
      ctx.stroke();
      for (const n of nodes) {
        ctx.beginPath(); ctx.arc(n.x, n.y, radius(n), 0, 2 * Math.PI);
        ctx.fillStyle = token(GRAPH_COLOURS[colourIndex(n) % GRAPH_COLOURS.length]); ctx.fill();
      }
      if (named()) {
        ctx.fillStyle = token("--text");
        ctx.font = `${11 / view.k}px system-ui, sans-serif`; ctx.textAlign = "center";
        for (const n of nodes) ctx.fillText(n.title, n.x, n.y + radius(n) + 12 / view.k);
      }
      stage.classList.toggle("named", named());
    };
  } else {
    const picture = stage.appendChild(svg("svg", { width: W, height: H, class: "whole-graph" }));
    picture.setAttribute("aria-label", t("A map of how your notes link together"));
    const layer = picture.appendChild(svg("g"));
    const lines = links.map(l => layer.appendChild(svg("line", { class: "edge" })));
    const points = nodes.map(n => {
      const point = layer.appendChild(svg("g", { class: "node", tabindex: "0", role: "link" }));
      point.dataset.path = n.id;
      point.setAttribute("aria-label", n.title);
      point.appendChild(svg("title")).textContent = n.title;
      point.appendChild(svg("circle", { r: radius(n), style: "fill:" + graphVar(colourIndex(n)) }));
      point.appendChild(svg("text", { "text-anchor": "middle" })).textContent = n.title;
      point.addEventListener("keydown", (event) => { if (event.key === "Enter") openPoint(n); });
      return point;
    });
    redraw = () => {
      layer.setAttribute("transform", `translate(${view.x} ${view.y}) scale(${view.k})`);
      links.forEach((l, i) => {
        lines[i].setAttribute("x1", l.source.x); lines[i].setAttribute("y1", l.source.y);
        lines[i].setAttribute("x2", l.target.x); lines[i].setAttribute("y2", l.target.y);
        lines[i].setAttribute("stroke-width", 1.2 / view.k);
      });
      nodes.forEach((n, i) => {
        const [circle, label] = [points[i].children[1], points[i].children[2]];
        circle.setAttribute("cx", n.x); circle.setAttribute("cy", n.y);
        label.setAttribute("x", n.x); label.setAttribute("y", n.y + radius(n) + 12 / view.k);
        label.setAttribute("font-size", 11 / view.k);
      });
      stage.classList.toggle("named", named());
    };
  }
  const openPoint = (n) => {
    if (n.id.startsWith("memory:")) { location.href = "/memory"; return; }
    go({ path: n.id });
  };

  // moving, zooming, dragging a point, opening one
  const at = (event) => {
    const box = stage.getBoundingClientRect();
    return [(event.clientX - box.left - view.x) / view.k, (event.clientY - box.top - view.y) / view.k];
  };
  const hit = ([x, y]) => {
    let best = null, distance = Infinity;
    for (const n of nodes) {
      const d = Math.hypot(n.x - x, n.y - y);
      if (d <= radius(n) + 4 / view.k && d < distance) { best = n; distance = d; }
    }
    return best;
  };
  let held = null;
  stage.onpointerdown = (event) => {
    const point = hit(at(event));
    held = { point, x: event.clientX, y: event.clientY, moved: false };
    stage.setPointerCapture(event.pointerId);
  };
  stage.onpointermove = (event) => {
    if (!held) return;
    const dx = event.clientX - held.x, dy = event.clientY - held.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) held.moved = true;
    if (!held.moved) return;
    if (held.point) { held.point.x += dx / view.k; held.point.y += dy / view.k; }
    else { view.x += dx; view.y += dy; }
    held.x = event.clientX; held.y = event.clientY;
    redraw();
  };
  stage.onpointerup = () => {
    if (held && held.point && !held.moved) openPoint(held.point);
    held = null;
  };
  const zoomBy = (factor, cx = W / 2, cy = H / 2) => {
    const k = Math.min(Math.max(view.k * factor, 0.05), 12);
    view.x = cx - (cx - view.x) * (k / view.k); view.y = cy - (cy - view.y) * (k / view.k);
    view.k = k;
    redraw();
  };
  stage.onwheel = (event) => {
    event.preventDefault();
    const box = stage.getBoundingClientRect();
    zoomBy(Math.exp(-event.deltaY * 0.0015), event.clientX - box.left, event.clientY - box.top);
  };
  for (const [name, tip, act] of [["plus", "Zoom in", () => zoomBy(1.4)], ["minus", "Zoom out", () => zoomBy(1 / 1.4)],
                                  ["fit", "Fit", () => { fit(); redraw(); }]]) {
    const button = zoom.appendChild(el("button", "icon")); setIcon(button, name);
    button.type = "button"; button.title = t(tip); button.setAttribute("aria-label", t(tip));
    button.onclick = act;
  }
  redraw();
  if (canvasMode) watchLook(redraw); else graphLookWatch?.();
  for (const group of groups) {
    const item = legend.appendChild(el("span", "graph-key"));
    item.appendChild(el("span", "swatch")).style.background = graphVar(groups.indexOf(group));
    item.append(group === "Memories" ? t("Memories") : group);
  }
  stage.dataset.mode = canvasMode ? "canvas" : "svg";
}

document.querySelectorAll(".quick button").forEach(b => b.onclick = () => go({ view: b.dataset.view }));

let searchTimer = null;
document.getElementById("search").addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  const q = event.target.value.trim();
  searchTimer = setTimeout(() => (q ? go({ q }) : go({})), 250);
});

