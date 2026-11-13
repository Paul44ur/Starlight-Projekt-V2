// The conversations: the list, a conversation's menu, the title in the bar, the pop-up
// menu, opening one, and the sidebar or drawer.
// Part of the chat page (chat.html), which loads its parts in this order: chat-core.js,
// chat-messages.js, chat-list.js, chat-input.js, chat-page.js. They are plain scripts:
// what one declares at the top, the others use.
// Needs chat-core.js and chat-messages.js.

// -- conversations ---------------------------------------------------------------------------
// A small heading in the list, an icon before its words (a folder's name is the user's: never translated).
function labelled(name, text, own) {
  const label = el("div", "folder");
  const words = el("span", "", text);
  if (own) words.translate = false;
  label.innerHTML = icon(name);
  label.append(words);
  return label;
}
async function loadList() {
  const search = document.getElementById("search").value;
  let data;
  try { data = await api("/api/conversations?search=" + encodeURIComponent(search) + (showArchived ? "&archived=true" : "")); } catch (e) { return; }
  if (!conversation) conversation = data.current;
  // The list is drawn anew: the keyboard's place in it (a row, or its ⋯) comes back after.
  const had = document.activeElement && document.activeElement.closest && document.activeElement.closest(".conv");
  const keep = had ? { id: had.dataset.id, more: document.activeElement !== had } : null;
  const list = document.getElementById("list"); list.innerHTML = "";
  if (showArchived) list.appendChild(labelled("archive", "Archived conversations"));
  // pinned ones stay on top; the rest are grouped by folder (no folder first)
  const pinned = data.conversations.filter(c => c.pinned);
  const rest = data.conversations.filter(c => !c.pinned)
    .sort((a, b) => (a.folder || "").localeCompare(b.folder || ""));
  let folder = null;
  if (pinned.length && !showArchived) list.appendChild(el("div", "folder section-label", "Pinned"));
  [...pinned, ...rest].forEach(c => {
    if (!c.pinned && (c.folder || "") !== folder) {
      folder = c.folder || "";
      if (folder) list.appendChild(labelled("folder", folder, true));
      else if (pinned.length && !showArchived) list.appendChild(el("div", "folder section-label", "Recent"));
    }
    const row = el("div", "conv" + (c.id === conversation ? " active" : ""));
    const t = el("span", "t", c.title); t.translate = false; t.title = c.title + " — " + c.updated.replace("T", " ").slice(0, 16);
    const acts = el("span", "acts");
    const more = el("button", "icon"); setIcon(more, "more"); more.title = "Rename, pin, folder, archive, download, delete"; more.setAttribute("aria-label", "More for this conversation");
    more.onclick = (e) => { e.stopPropagation(); conversationMenu(c, more); };
    acts.append(more); row.append(t, acts);
    if (c.private) row.insertAdjacentHTML("afterbegin", icon("lock"));  // (its own element, before the title)
    row.onclick = () => openConversation(c.id);
    row.tabIndex = 0;
    row.dataset.id = c.id;
    row.onkeydown = (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target === row) { e.preventDefault(); openConversation(c.id); }
    };
    list.appendChild(row);
  });
  const toggle = el("button", "icon", showArchived ? "Back to your conversations" : "Archived conversations");
  setIcon(toggle, showArchived ? "back" : "archive");
  toggle.id = "archived";
  toggle.onclick = () => { showArchived = !showArchived; loadList(); };
  list.appendChild(toggle);
  const again = keep && [...list.querySelectorAll(".conv")].find(r => r.dataset.id === keep.id);
  if (again) (keep.more ? again.querySelector(".acts button") : again).focus();
  // A search or the archive can leave the open conversation out of the list: its name stays.
  const open = data.conversations.find(c => c.id === conversation);
  showTitle(open || (titled && titled.id === conversation ? titled : null));
}
// The open conversation's name in the bar; a click opens its menu (as its ⋯ in the list).
let titled = null;
function showTitle(c) {
  const button = document.getElementById("title");
  titled = c || null;
  button.hidden = !c;
  document.querySelector(".bar-sep").hidden = !c;
  if (!c) return;
  const lock = document.getElementById("title-lock");
  lock.hidden = !c.private;
  lock.innerHTML = c.private ? icon("lock") : "";
  document.getElementById("title-text").textContent = c.title;
  button.onclick = (e) => { e.stopPropagation(); conversationMenu(c, button); };
}

// Conversations: pinned first, then by folder; the archive is a list of its own.
let showArchived = false;
function download(href) {
  const a = document.createElement("a");
  a.href = href; a.download = "";
  document.body.appendChild(a); a.click(); a.remove();
}
async function changeConversation(id, change) {
  try {
    const changed = await api("/api/conversations/" + id, { method: "PATCH", body: change });
    if (titled && titled.id === id) {  // (the bar's name, also when the list leaves it out)
      for (const key of ["title", "pinned", "archived", "folder"]) if (key in changed) titled[key] = changed[key];
    }
  } catch (e) { toast(e.message); }
  loadList();
}
function conversationMenu(c, anchor) {
  const base = "/api/conversations/" + c.id;
  popMenu(anchor, [
    ["edit", "Rename", async () => {
      const title = ((await askText("New name", c.title, { yes: "Rename" })) || "").trim();
      if (title && title !== c.title) changeConversation(c.id, { title });
    }],
    ["pin", c.pinned ? "Unpin" : "Pin to the top", () => changeConversation(c.id, { pinned: !c.pinned })],
    ["folder", c.folder ? `Folder: ${c.folder}` : "Put in a folder", async () => {
      const folder = await askText("Folder (empty: none)", c.folder || "", { yes: "Save" });
      if (folder !== null) changeConversation(c.id, { folder: folder.trim() });
    }],
    ["archive", c.archived ? "Take out of the archive" : "Archive", () => changeConversation(c.id, { archived: !c.archived })],
    ["forget", "Forget what it learned here", async () => {
      if (!(await askYesNo("Forget the memories Starlight learned in \"" + c.title + "\"?\n\nThe conversation stays.", { yes: "Forget", danger: true }))) return;
      try {
        const r = await api(base + "/forget-learned", { method: "POST" });
        const kept = r.kept_because_also_said_elsewhere.length;
        toast(`Forgot ${r.forgotten.length} ${r.forgotten.length === 1 ? "memory" : "memories"}` + (kept ? ` (kept ${kept} also said elsewhere)` : ""));
      } catch (e) { toast(e.message); }
    }],
    ["download", "Download as Markdown", () => download(base + "/export")],
    ["download", "Download as a web page", () => download(base + "/export?format=html")],
    ["print", "Print or save as PDF", () => window.open(base + "/export?format=html&show=true", "_blank")],
    ["trash", "Delete", async () => {
      if (!(await askYesNo("Delete \"" + c.title + "\"?", { yes: "Delete", danger: true }))) return;
      await api(base, { method: "DELETE" });
      if (c.id === conversation) { const d = await api("/api/conversations", { method: "POST" }); await openConversation(d.id); } else loadList();
    }],
  ]);
}

// A small menu at an element: items are [icon, label, action, hint?]. The icon is the name of
// one in ICONS (drawn); any other text there is shown as it is.
let openMenu = null;
// refocus (Esc): back to the button that opened it, first, while that button is still shown
function closeMenu(refocus) {
  if (!openMenu) return;
  const back = openMenu.returnTo;
  if (refocus && back && back.isConnected) back.focus();
  openMenu.remove(); openMenu = null;
  if (back) back.setAttribute("aria-expanded", "false");
}
function popMenu(anchor, items, place) {
  closeMenu();
  const menu = el("div", "popmenu");
  menu.setAttribute("role", "menu");
  items.forEach(([name, label, action, hint]) => {
    const b = el("button");
    b.setAttribute("role", "menuitem");
    const mark = el("span", "", ICONS[name] ? "" : name);
    if (ICONS[name]) mark.innerHTML = icon(name);
    b.append(mark, el("span", "", label));
    if (hint) b.appendChild(el("span", "hint", hint));
    b.onclick = (e) => { e.stopPropagation(); closeMenu(); action(); };
    menu.appendChild(b);
  });
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.left, innerWidth - menu.offsetWidth - 8));
  const below = r.bottom + 4, above = r.top - menu.offsetHeight - 4;
  menu.style.left = left + "px";
  menu.style.top = (place === "above" || below + menu.offsetHeight > innerHeight ? Math.max(8, above) : below) + "px";
  openMenu = menu;
  // Opened from a button (a conversation's ⋯): the focus moves into the menu, ↑ and ↓ go
  // through it, and Esc goes back to the button. (The / menu stays with the message box.)
  if (anchor.tagName === "BUTTON") {
    menu.returnTo = anchor;
    anchor.setAttribute("aria-haspopup", "menu");
    anchor.setAttribute("aria-expanded", "true");  // (and its ⋯ stays shown: CSS)
    menu.querySelector("button").focus();
    menu.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const all = [...menu.querySelectorAll("button")], at = all.indexOf(document.activeElement);
      all[(at + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length].focus();
    });
  }
  return menu;
}
document.addEventListener("click", (e) => { if (openMenu && !openMenu.contains(e.target)) closeMenu(); });
async function openConversation(id) {
  setDrawer(false);
  showContext(null);  // (another conversation: known again after its next answer)
  showAnswerStats(null);
  let data;
  try { data = await api("/api/conversations/" + id); } catch (e) { toast(e.message); return; }
  conversation = id; log.innerHTML = ""; current = null;
  showContext(data.context_tokens || null);  // (the number kept with it, if it has one)
  if (data.title) { const { messages, ...about } = data; showTitle(about); }  // (also outside a search)
  document.getElementById("privacy").hidden = !data.private;
  data.messages.forEach(m => {
    const body = addMsg(m.role === "user" ? "you" : "bot", m.content);
    if (m.role !== "user") body.parentElement.dataset.kept = "1";
    (m.tools || []).forEach(t => toolStep(body, t.tool, t.ok ? "done" : "failed"));
  });
  if (!data.messages.length) showEmpty(data.private);
  loadList(); scrollToEnd();
}
document.getElementById("new").onclick = async () => { const d = await api("/api/conversations", { method: "POST" }); await openConversation(d.id); input.focus(); };
document.getElementById("newprivate").onclick = async () => { const d = await api("/api/conversations", { method: "POST", body: { private: true } }); await openConversation(d.id); input.focus(); };
document.getElementById("quick-open").onclick = () => { setDrawer(false); if (window.openQuick) window.openQuick(); };
document.getElementById("search").oninput = () => loadList();
const phone = matchMedia("(max-width: 760px)");  // (the drawer; wider: the sidebar)
function setDrawer(open) {
  document.body.classList.toggle("drawer", open);
  if (phone.matches) document.getElementById("menu").setAttribute("aria-expanded", open ? "true" : "false");
  if (open) document.getElementById("convs").focus();
}
// On a wide screen the same button hides the sidebar or shows it again (remembered here).
function showSidebar(on) {
  document.body.classList.toggle("no-sidebar", !on);
  if (!phone.matches) document.getElementById("menu").setAttribute("aria-expanded", on ? "true" : "false");
  try { localStorage.setItem("starlight-sidebar", on ? "on" : "off"); } catch (e) { /* */ }
}
document.getElementById("menu").onclick = () => phone.matches
  ? setDrawer(!document.body.classList.contains("drawer"))
  : showSidebar(document.body.classList.contains("no-sidebar"));
try { if (localStorage.getItem("starlight-sidebar") === "off") document.body.classList.add("no-sidebar"); } catch (e) { /* */ }
if (!phone.matches) document.getElementById("menu").setAttribute("aria-expanded", String(!document.body.classList.contains("no-sidebar")));
log.addEventListener("click", () => setDrawer(false));
