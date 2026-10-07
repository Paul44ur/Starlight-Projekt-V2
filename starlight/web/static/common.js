// Shared by the Starlight pages: theme switch, small helpers, JSON calls.
(function () {
  const saved = (() => { try { return localStorage.getItem("starlight-theme"); } catch (e) { return null; } })();
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
})();

function showTheme() {
  const b = document.getElementById("theme");
  if (b) b.textContent = { system: "◐", light: "☀", dark: "☾" }[document.documentElement.dataset.theme || "system"];
}
document.addEventListener("DOMContentLoaded", showTheme);

function cycleTheme() {
  const order = ["system", "light", "dark"];
  const now = document.documentElement.dataset.theme || "system";
  const next = order[(order.indexOf(now) + 1) % order.length];
  if (next === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = next;
  try { localStorage.setItem("starlight-theme", next); } catch (e) { /* private window */ }
  const b = document.getElementById("theme");
  if (b) b.textContent = { system: "◐", light: "☀", dark: "☾" }[next];
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
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}
