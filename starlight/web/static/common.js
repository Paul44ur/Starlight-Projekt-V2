// Shared by the Starlight pages: theme switch, small helpers, JSON calls.
(function () {
  const saved = (() => { try { return localStorage.getItem("starlight-theme"); } catch (e) { return null; } })();
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
})();

const THEMES = { system: ["◐", "like your system"], light: ["☀", "light"], dark: ["☾", "dark"] };
// The theme button(s) show the theme in use; the tooltip says it in words.
function showTheme() {
  const now = document.documentElement.dataset.theme || "system";
  ["theme", "theme2"].forEach(id => {
    const b = document.getElementById(id);
    if (!b) return;
    b.textContent = THEMES[now][0] + (id === "theme2" ? " Theme" : "");
    b.title = "Theme: " + THEMES[now][1] + " (click to change)";
    b.setAttribute("aria-label", b.title);
  });
}
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

function toast(text) {
  const t = el("div", "toast", text);
  t.setAttribute("role", "status");  // read out by screen readers too
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

// The Notes page is on the PC only: a paired phone does not show the link to it.
if (document.querySelector('a[href="/notes"]')) {
  fetch("/api/status").then(r => r.json()).then(s => {
    if (s && s.local === false) document.body.classList.add("remote");
  }).catch(() => { /* the page says itself when Starlight cannot be reached */ });
}

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
