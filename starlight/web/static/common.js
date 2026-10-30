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
