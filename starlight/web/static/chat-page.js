// The ready sign, the status and context meter, what is shared into the page, start-up.
// Part of the chat page (chat.html), which loads its parts in this order: chat-core.js,
// chat-messages.js, chat-list.js, chat-input.js, chat-page.js. They are plain scripts:
// what one declares at the top, the others use.
// Needs every file before it; it starts the page last.

// -- a sign when an answer is ready ------------------------------------------------------------
// You may have gone to another program or tab: the title gets a dot, Starlight's own window
// lights up its taskbar button, and a browser shows a notification if you allowed it. The sign
// never carries the answer's words (a notification can show on a locked screen).
const away = () => document.hidden || !document.hasFocus();
const plainTitle = document.title;
let readyNote = null;
function signalReady(text) {
  if (!away() || !readySignOn()) return;
  if (!document.title.startsWith("● ")) document.title = "● " + plainTitle;
  const said = window.starlightTranslate ? window.starlightTranslate(text) : text;
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.attention) {
      window.pywebview.api.attention(said);
      return;
    }
    if (window.Notification && Notification.permission === "granted") {
      if (readyNote) readyNote.close();
      readyNote = new Notification("Starlight", { body: said, icon: "/icons/icon-192.png", tag: "starlight-ready" });
      readyNote.onclick = () => { window.focus(); clearReady(); };
    }
  } catch (e) { /* a sign that cannot show is no reason to fail */ }
}
function clearReady() {
  document.title = plainTitle;
  try { if (readyNote) readyNote.close(); } catch (e) { /* already closed */ }
  readyNote = null;
}
window.addEventListener("focus", clearReady);
document.addEventListener("visibilitychange", () => { if (!document.hidden) clearReady(); });

// -- status ----------------------------------------------------------------------------------
// A new conversation: a calm start instead of an empty page. It goes with the first message.
function showEmpty(isPrivate) {
  const box = el("div", "empty");
  const logo = el("img", "empty-logo");
  logo.src = "/icon.svg"; logo.alt = "";
  box.append(logo,
    el("div", "empty-title", isPrivate ? "Private conversation" : "New conversation. Ask anything."),
    el("div", "empty-text", isPrivate
      ? "It is never saved and nothing is learned from it. It is gone when Starlight stops."
      : "Starlight runs on this PC: nothing you say leaves it."),
    el("div", "empty-hint", "Type / for commands. Drop pictures or documents here to add them."),
    el("div", "empty-keys", "Press Ctrl+/ for the keyboard shortcuts."));
  log.appendChild(box);
}
// How full the model's context is for this conversation: what it read and wrote for the last
// answer, of what it can take (older turns are summarized beyond it).
let contextWindow = 0, contextTokens = null;
function showContext(tokens) {
  contextTokens = tokens;  // (shown again when the window size arrives, see refresh)
  const used = document.getElementById("context-used"), of = document.getElementById("context-of");
  const bar = document.getElementById("context-bar"), note = document.getElementById("context-note");
  const line = document.getElementById("meta-context");
  const top = document.getElementById("bar-context");  // (the thin meter in the bar)
  top.hidden = !tokens || !contextWindow;
  if (!tokens || !contextWindow) {
    used.textContent = "–"; of.textContent = ""; bar.className = "context-bar";
    bar.firstElementChild.style.width = "0";
    note.textContent = "Shows after the next answer."; line.textContent = "";
    return;
  }
  const percent = Math.min(100, Math.round(tokens / contextWindow * 100));
  const point = document.documentElement.lang === "de" ? "," : ".";  // (21,4k in German)
  const k = (n) => n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, "").replace(".", point) + "k" : String(n);
  used.textContent = percent + "% used";
  of.textContent = k(tokens) + " of " + k(contextWindow);
  bar.className = "context-bar" + (percent >= 85 ? " full" : percent >= 60 ? " warn" : "");
  bar.firstElementChild.style.width = percent + "%";
  note.textContent = percent >= 85 ? "Nearly full: older turns get summarized. /new starts afresh."
    : "Older turns are summarized when it is full.";
  line.textContent = "Context " + percent + "%";
  document.getElementById("bar-context-pct").textContent = percent + "%";
  document.getElementById("bar-context-fill").style.width = percent + "%";
  top.dataset.level = percent >= 85 ? "full" : percent >= 60 ? "warn" : "ok";
}
async function refresh() {
  try {
    const s = await api("/api/status");
    const name = s.model_name || s.model;
    document.getElementById("model").textContent = name;
    document.getElementById("model-name").textContent = name;
    document.getElementById("model-dot").classList.add("on");
    const size = s.context_window || 0;  // (known now, or changed: the meter is drawn again)
    if (size !== contextWindow) { contextWindow = size; showContext(contextTokens); }
    const where = document.getElementById("where");
    if (!s.local) { where.hidden = false; where.textContent = s.device || "remote"; }
    const k = document.getElementById("kill");
    if (s.kill.engaged) {
      k.textContent = "Resume"; k.className = "ok"; k.dataset.on = "1"; k.disabled = !s.local;
      k.title = s.local ? "The kill switch is on: Starlight does nothing until you resume"
                        : "Release the kill switch on the PC";
    } else if (k.dataset.on !== "0") {
      k.replaceChildren(el("span", "wide", "Kill switch"), el("span", "narrow", "Kill"),
                        el("kbd", "key-wide", "Ctrl Alt J"));
      k.className = "kill"; k.dataset.on = "0"; k.disabled = false;
      k.title = "Kill switch: stop everything Starlight does on this PC, until you resume (also Ctrl+Alt+J)";
    }
    document.getElementById("practice").hidden = !s.practice;
    const paused = document.getElementById("paused");
    paused.hidden = !s.paused;
    if (s.paused) paused.title = `Game mode (${s.paused}): the model is off the graphics card. Press to bring it back now`;
    // the access module's status tool (modules/access/module.py), read by /api/status
    fill("servers", s.access.servers || []); fill("logins", s.access.login_sites || []); fill("secrets", s.access.secrets || []);
    document.getElementById("saves").textContent = s.saves_files || "";
    const waiting = document.getElementById("waiting");
    waiting.hidden = !s.waiting;
    waiting.querySelector(".wide").textContent = `${s.waiting} waiting for your OK`;
    waiting.querySelector(".narrow").textContent = `${s.waiting} ✋`;
    misses = 0; document.getElementById("offline").hidden = true;
    try { if (s.wake_url) localStorage.setItem("starlight-wake", s.wake_url); else localStorage.removeItem("starlight-wake"); } catch (e) { /* */ }
  } catch (e) {  // the server is starting - or, twice in a row, not there at all
    if (++misses >= 2) showOffline();
  }
}
let misses = 0;
function showOffline() {
  document.getElementById("offline").hidden = false;
  let wake = null;
  try { wake = localStorage.getItem("starlight-wake"); } catch (e) { /* */ }
  const button = document.getElementById("wake");
  button.hidden = !wake;
  button.onclick = async () => {
    // Something else on the network (that stays on) sends the Wake-on-LAN packet.
    try { await fetch(wake, { method: "POST", mode: "no-cors" }); toast("Asked the PC to wake up; give it a minute"); }
    catch (e) { toast("Could not reach " + wake); }
  };
}
function fill(id, items) {
  const box = document.getElementById(id); box.innerHTML = "";
  if (!items.length) { box.className = "dim small"; box.textContent = "none"; return; }
  box.className = ""; items.forEach(x => box.appendChild(el("div", "item", x)));
}

// Shared from the phone's share menu (/share), the "Talk" shortcut on the home screen, or
// "Ask Starlight" on the Notes page (?ask=: the request waits in the box of a new
// conversation; nothing is sent until you press Send). True for ?ask.
function takeShared() {
  const params = new URLSearchParams(location.search);
  if (params.get("ask")) {
    input.value = params.get("ask").slice(0, 4000);
    input.oninput();
    input.focus();
    toast("Check the request, then press Send");
    history.replaceState(null, "", "/");
    return true;
  }
  if (!params.has("share") && !params.has("files") && !params.has("talk")) return false;
  if (params.get("share")) { input.value = params.get("share") + "\n"; input.oninput(); }
  try { JSON.parse(params.get("files") || "[]").forEach(f => attached.push({ id: f.id, name: f.name })); } catch (e) { /* */ }
  showFiles();
  if (params.has("share") || params.has("files")) { input.focus(); toast("Shared: say what to do with it, then Send"); }
  if (params.has("talk")) { document.getElementById("talk").focus(); toast("Press the talk button, next to the microphone, to start"); }
  history.replaceState(null, "", "/");
  return false;
}

// From the quick switcher on another page (quick.js): /?c=<id> opens that conversation, and
// /?new=new or /?new=private starts one. Read once and taken out of the address; an id that
// does not exist (or anything else) gives the chat as usual.
function takeJump() {
  const params = new URLSearchParams(location.search);
  const id = params.get("c"), start = params.get("new");
  if (id === null && start === null) return {};
  history.replaceState(null, "", "/");
  return { id: id && id.length <= 80 ? id : null, start: start === "new" || start === "private" ? start : null };
}

(async () => {
  const fresh = takeShared();
  const jump = takeJump();
  await loadList();
  if (fresh || jump.start) {  // a question about a note, or a command: in a conversation of its own
    const body = jump.start === "private" ? { private: true } : undefined;
    try { conversation = (await api("/api/conversations", { method: "POST", body })).id; } catch (e) { /* the current one */ }
  } else if (jump.id) {
    try { await api("/api/conversations/" + encodeURIComponent(jump.id)); conversation = jump.id; } catch (e) { /* none: as usual */ }
  }
  if (conversation) await openConversation(conversation);
  cleanDrafts();
  if (jump.start) input.focus();
  connect(); refresh(); setInterval(refresh, 4000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { /* http on a phone: no offline page */ });
})();
