// Shared by the Starlight pages: theme switch, small helpers, JSON calls.
(function () {
  const saved = (() => { try { return localStorage.getItem("starlight-theme"); } catch (e) { return null; } })();
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
})();

const THEMES = { system: ["system", "like your system"], light: ["sun", "light"], dark: ["moon", "dark"] };
// The theme button(s) show the theme in use (an icon); the tooltip says it in words.
function showTheme() {
  const now = document.documentElement.dataset.theme || "system";
  ["theme", "theme2"].forEach(id => {
    const b = document.getElementById(id);
    if (!b) return;
    setIcon(b, THEMES[now][0]);
    b.title = "Theme: " + THEMES[now][1] + " (click to change)";
    b.setAttribute("aria-label", b.title);
  });
  paintBar();
}
// The browser's bar (on a phone, or an installed app's title bar) takes the colour of the
// page's bar, light or dark. A page that keeps its own colour marks its meta data-fixed.
// (The pages' heads hold a light and a dark one, right from the first paint.)
function paintBar() {
  const metas = [...document.querySelectorAll('meta[name="theme-color"]')];
  if (metas.some(m => m.hasAttribute("data-fixed"))) return;
  const panel = getComputedStyle(document.documentElement).getPropertyValue("--panel").trim();
  if (panel) metas.forEach(m => { m.content = panel; });
}
try { matchMedia("(prefers-color-scheme: light)").addEventListener("change", paintBar); } catch (e) { /* an old browser */ }
document.addEventListener("DOMContentLoaded", showTheme);

function cycleTheme() {
  const order = ["system", "light", "dark"];
  const now = document.documentElement.dataset.theme || "system";
  const next = order[(order.indexOf(now) + 1) % order.length];
  if (next === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = next;
  try { localStorage.setItem("starlight-theme", next); } catch (e) { /* private window */ }
  showTheme();
}

// One small set of line icons (drawn for Starlight, 24 x 24, in the text colour), for the
// buttons that carry no words: they look the same on every system, unlike emoji.
const ICONS = {
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
  system: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
  speaker: '<path d="M4 9.5h3l5-4v13l-5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  mute: '<path d="M4 9.5h3l5-4v13l-5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
  clip: '<path d="M20 11.5l-7.8 7.8a4.6 4.6 0 0 1-6.5-6.5l8-8a3.1 3.1 0 0 1 4.4 4.4l-8 8a1.5 1.5 0 0 1-2.2-2.2l7.4-7.4"/>',
  camera: '<path d="M4 8h3l1.8-2.5h6.4L17 8h3a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor"/>',
  send: '<path d="M4.5 12h14M13 6l6 6-6 6"/>',
  down: '<path d="M12 5v13M6 12l6 6 6-6"/>',
  more: '<circle cx="5.5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="18.5" cy="12" r="1.4" fill="currentColor"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5l5 5"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.6v.1"/>',
  history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3.5 4v4.5H8M12 7.5V12l3 2"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 19.5h14"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5.5M14 11v5.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  today: '<rect x="4" y="5.5" width="16" height="15" rx="2"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/><rect x="8" y="13" width="3" height="3" fill="currentColor" stroke="none"/>',
  inbox: '<path d="M4 13.5l2.5-8h11l2.5 8v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M4 13.5h4.5l1 2h5l1-2H20"/>',
  tasks: '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  graph: '<circle cx="6" cy="7" r="2.2"/><circle cx="18" cy="6" r="2.2"/><circle cx="12" cy="17.5" r="2.2"/><path d="M8 8l3 7.5M16.3 7.5l-3.3 8M8.2 6.8l7.6-.6"/>',
  cards: '<rect x="3.5" y="8" width="12.5" height="12.5" rx="2"/><path d="M8 4.5h10.5a2 2 0 0 1 2 2V17"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
  talk: '<path d="M4 6.5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-4 3.5v-3.5H6a2 2 0 0 1-2-2z"/><path d="M9 9.5v2M12 8v5M15 9.5v2"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4v4.5H15"/>',
  picture: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9.5" r="1.7"/><path d="M3.5 17l5-5 4 4 2.5-2.5 5.5 5"/>',
  notes: '<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10.5a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z"/><path d="M14 3.5V8h4M9 12.5h6M9 16h6"/>',
  memory: '<path d="M9 18h6M10 21h4M12 3.5a6 6 0 0 0-3.6 10.8c.6.5 1.1 1.2 1.1 2V17h5v-.7c0-.8.5-1.5 1.1-2A6 6 0 0 0 12 3.5z"/>',
  activity: '<path d="M3.5 12h4l2.5-6.5 4 13 2.5-6.5h4"/>',
  automations: '<path d="M13 3.5 5.5 13.5H12l-1 7 7.5-10H12z"/>',
  settings: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  chevron: '<path d="M7 10l5 5 5-5"/>',
  boards: '<rect x="3.5" y="4.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="4.5" width="7" height="4.5" rx="1.5"/><rect x="3.5" y="14.5" width="7" height="5" rx="1.5"/><rect x="13.5" y="12" width="7" height="7.5" rx="1.5"/>',
};
function icon(name, alone) {
  return '<svg class="ico' + (alone ? " alone" : "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + (ICONS[name] || "") + "</svg>";
}
// Puts the icon at the start of a node, before any words it keeps (e.g. "Theme").
function setIcon(node, name) {
  node.dataset.icon = name;
  const old = node.querySelector(":scope > svg.ico");
  if (old) old.remove();
  node.insertAdjacentHTML("afterbegin", icon(name, !node.textContent.trim()));
}
// The pages mark icon buttons with data-icon="…"; this draws them.
function drawIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach(n => { if (!n.querySelector(":scope > svg.ico")) setIcon(n, n.dataset.icon); });
}
drawIcons();  // (once: every page loads this script after its markup)

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
    init.headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, init);
  let data = {};
  try { data = await response.json(); } catch (e) { /* empty */ }
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

// One message at a time (a new one replaces the last, instead of covering it), and a longer
// one stays longer, so it can be read.
let toastShown = null;
function toast(text) {
  if (toastShown) { clearTimeout(toastShown.timer); toastShown.remove(); }
  const t = toastShown = el("div", "toast", text);
  t.setAttribute("role", "status");  // read out by screen readers too
  document.body.appendChild(t);
  const ms = Math.min(8000, 2600 + Math.max(0, String(text).length - 40) * 45);
  t.timer = setTimeout(() => {
    t.classList.add("leaving");
    setTimeout(() => { t.remove(); if (toastShown === t) toastShown = null; }, 200);
  }, ms);
}

// A list that never arrived (no access, an error): its placeholder bars go after a while.
setTimeout(() => document.querySelectorAll(".skeleton").forEach(s => s.remove()), 10000);

// The keyboard shortcuts of this page (window.SHORTCUTS: pairs of keys, as "Shift+Enter",
// and what they do), in a small window that Esc closes. Ctrl+/ opens it, also while typing in
// a box (where ? is just a question mark); ? alone opens it when no box has the focus. (Not in
// a note's editor: Ctrl+/ is its own key there.)
function showShortcuts() {
  if (!window.SHORTCUTS) return;
  let box = document.getElementById("shortcuts");
  if (!box) {
    box = el("dialog");
    box.id = "shortcuts";
    box.setAttribute("aria-labelledby", "shortcuts-title");
    const title = el("h2", "", "Keyboard shortcuts");
    title.id = "shortcuts-title";
    const list = el("dl");
    window.SHORTCUTS.forEach(([keys, what]) => {
      const dt = el("dt");
      keys.split("+").forEach((key, i) => {
        if (i) dt.appendChild(el("span", "plus", "+"));
        dt.appendChild(el("kbd", "", key));
      });
      list.append(dt, el("dd", "", what));
    });
    const close = el("button", "", "Close");
    close.type = "button";
    close.onclick = () => box.close();
    box.append(title, list, close);
    document.body.appendChild(box);
  }
  if (!box.open) box.showModal();
}
document.addEventListener("keydown", (e) => {
  const at = e.target.closest ? e.target : document.body;
  if (at.closest(".cm-editor")) return;
  const typing = at.closest("input, textarea, select, [contenteditable]");
  const ctrlSlash = e.key === "/" && (e.ctrlKey || e.metaKey) && !e.altKey;
  const question = e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !typing;
  if (!ctrlSlash && !question) return;
  e.preventDefault();
  showShortcuts();
});

// The Notes page is on the PC only: a paired phone does not show the link to it.
if (document.querySelector('a[href="/notes"]')) {
  fetch("/api/status").then(r => r.json()).then(s => {
    if (s && s.local === false) document.body.classList.add("remote");
  }).catch(() => { /* the page says itself when Starlight cannot be reached */ });
}

// -- Starlight's own window (`starlight app`) ----------------------------------------------------
// There, window.pywebview.api.open_window opens a page of this Starlight in a new Starlight
// window. A page that would open in a new tab (print, export, a link to a Starlight page) goes
// there; any other address opens in the browser, as the window does by itself.
function inAppWindow(address) {
  const api = window.pywebview && window.pywebview.api;
  if (!api || typeof api.open_window !== "function") return null;
  try { return new URL(String(address), location.href).origin === location.origin ? api : null; }
  catch (e) { return null; }
}
const browserOpen = window.open.bind(window);
window.open = (address, ...rest) => {
  const api = inAppWindow(address);
  if (!api) return browserOpen(address, ...rest);
  api.open_window(new URL(String(address), location.href).href);
  return null;
};
document.addEventListener("click", (e) => {
  const link = e.target instanceof Element ? e.target.closest("a[target=_blank]") : null;
  if (!link || e.defaultPrevented || link.hasAttribute("download")) return;
  const api = inAppWindow(link.href);
  if (!api) return;
  e.preventDefault();
  api.open_window(link.href);
});

// -- recording: the chat's microphone and the Notes page's voice memos ------------------------
// Record 16 kHz mono 16-bit WAV in the browser, which the speech server reads directly.
async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const node = context.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(node); node.connect(context.destination);
  return { stop: async () => {
    node.disconnect(); source.disconnect(); stream.getTracks().forEach(t => t.stop());
    const rate = context.sampleRate; await context.close();
    return wav(chunks, rate);
  } };
}
// A WAV file: a 44-byte RIFF header, then 16 kHz mono 16-bit samples (what whisper reads).
const WAV_RATE = 16000, WAV_BYTES = 2, WAV_HEADER = 44;
function wav(chunks, rate) {
  const all = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0; chunks.forEach(c => { all.set(c, at); at += c.length; });
  const ratio = rate / WAV_RATE, length = Math.floor(all.length / ratio);  // resampled
  const size = length * WAV_BYTES;
  const buffer = new ArrayBuffer(WAV_HEADER + size), view = new DataView(buffer);
  const writeAscii = (o, s) => [...s].forEach((ch, i) => view.setUint8(o + i, ch.charCodeAt(0)));
  writeAscii(0, "RIFF"); view.setUint32(4, WAV_HEADER - 8 + size, true); writeAscii(8, "WAVEfmt ");
  view.setUint32(16, 16, true);  // the fmt chunk's length
  view.setUint16(20, 1, true);  // PCM
  view.setUint16(22, 1, true);  // mono
  view.setUint32(24, WAV_RATE, true);  // samples per second
  view.setUint32(28, WAV_RATE * WAV_BYTES, true);  // bytes per second
  view.setUint16(32, WAV_BYTES, true);  // bytes per sample (all channels)
  view.setUint16(34, WAV_BYTES * 8, true);  // bits per sample
  writeAscii(36, "data"); view.setUint32(40, size, true);
  for (let i = 0; i < length; i++) {
    const v = Math.max(-1, Math.min(1, all[Math.floor(i * ratio)]));
    view.setInt16(WAV_HEADER + i * WAV_BYTES, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}
