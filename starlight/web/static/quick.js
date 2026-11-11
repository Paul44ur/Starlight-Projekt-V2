// The quick switcher (Ctrl+K, Cmd+K on a Mac): one box to jump to a conversation, a note, a
// page or a command. Every page loads it after common.js (it uses el, api, icon, cycleTheme
// and showShortcuts from there). On the Notes page Ctrl+K stays that page's own note finder;
// Ctrl+Shift+K opens this one everywhere.
// Titles of conversations and notes are the user's words: they are set with textContent and
// marked translate="no", never put in as HTML.

// Every page lists its keys for the ? window; a page that has none gets this one (the chat
// and the Notes page set their own lists later, with the same entry).
window.SHORTCUTS = window.SHORTCUTS || [
  ["Ctrl+K", "Jump to a conversation, a note or a page"],
  ["Ctrl+/", "These shortcuts (? when not typing)"],
];

(function () {
  const MAX_ROWS = 6, WAIT = 150;
  const PAGES = [  // [address, icon, name, only at the PC]
    ["/", "talk", "Chat", false], ["/notes", "notes", "Notes", true], ["/memory", "memory", "Memory", false],
    ["/activity", "activity", "Activity", false], ["/automations", "automations", "Automations", false],
    ["/settings", "settings", "Settings", false],
  ];
  const COMMANDS = [  // [icon, name, what it does]
    ["plus", "New conversation", () => toChat("new", "new")],
    ["lock", "Private conversation", () => toChat("newprivate", "private")],
    ["sun", "Change the theme", () => cycleTheme()],
    ["info", "Keyboard shortcuts", () => showShortcuts()],
  ];
  let box = null, field = null, listing = null, local = null;
  let remote = { conversations: [], notes: [] };  // what the server answered, for the box's text
  let rows = [], marked = 0, moved = false, timer = 0;
  const latest = { conversations: 0, notes: 0 };  // the newest request of each group: older answers are dropped

  // The address of a page: kept in one place, so that a test (or a future app window) can
  // replace it.
  function go(address) { location.href = address; }
  // The chat page does it with its own buttons; any other page goes to the chat, which starts
  // it from the address (chat-page.js).
  function toChat(button, how) {
    const own = document.getElementById(button);
    if (own && typeof window.openConversation === "function") own.click();
    else go("/?new=" + how);
  }
  function openConversationFrom(id) {
    if (typeof window.openConversation === "function") window.openConversation(id);
    else go("/?" + new URLSearchParams({ c: id }));
  }
  const shown = (text) => (window.starlightTranslate ? window.starlightTranslate(text) : text);
  const fits = (query, text) => query.toLowerCase().split(/\s+/).filter(Boolean)
    .every(word => text.toLowerCase().includes(word));
  const pageIsNotes = () => !!document.getElementById("switcher");

  async function atThePC() {
    if (local === null) {
      try { local = (await api("/api/status")).local !== false; } catch (e) { local = false; }
    }
    return local;
  }

  function groups() {
    const query = field.value.trim();
    const out = [];
    const conversations = remote.conversations.slice(0, MAX_ROWS).map(c => ({
      icon: c.private ? "lock" : "talk", label: c.title || "", sub: c.folder || "", user: true,
      run: () => openConversationFrom(c.id),
    }));
    out.push(["Conversations", conversations]);
    if (local) {
      out.push(["Notes", remote.notes.slice(0, MAX_ROWS).map(n => {
        const path = String(n.path || ""), slash = path.lastIndexOf("/");
        return {
          icon: "notes", label: n.title || path.slice(slash + 1).replace(/\.md$/i, ""),
          sub: slash > 0 ? path.slice(0, slash) : "", user: true,
          run: () => go("/notes?" + new URLSearchParams({ path })),
        };
      })]);
    }
    out.push(["Pages", PAGES.filter(p => !p[3] || local)
      .filter(p => fits(query, shown(p[2]))).slice(0, MAX_ROWS)
      .map(([address, ico, name]) => ({ icon: ico, label: name, run: () => go(address) }))]);
    out.push(["Commands", COMMANDS.filter(c => fits(query, shown(c[1])))
      .slice(0, MAX_ROWS).map(([ico, name, run]) => ({ icon: ico, label: name, run }))]);
    return out;
  }

  function mark(i) {
    marked = i;
    rows.forEach((r, n) => r.node.setAttribute("aria-selected", String(n === i)));
    field.setAttribute("aria-activedescendant", rows[i] ? rows[i].node.id : "");
    if (rows[i]) rows[i].node.scrollIntoView({ block: "nearest" });
  }

  function draw() {
    listing.replaceChildren();
    rows = [];
    groups().forEach(([name, items]) => {
      if (!items.length) return;
      const group = el("div");
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", name);
      const head = el("div", "quick-head", name);
      head.setAttribute("aria-hidden", "true");
      group.appendChild(head);
      items.forEach(item => {
        const node = el("div", "quick-row");
        node.id = "quick-" + rows.length;
        node.setAttribute("role", "option");
        node.insertAdjacentHTML("afterbegin", icon(item.icon, true));
        const label = node.appendChild(el("span", "q-t", item.label));
        if (item.user) label.setAttribute("translate", "no");
        if (item.sub) {
          const sub = node.appendChild(el("span", "q-w", item.sub));
          sub.setAttribute("translate", "no");
        }
        const index = rows.length;
        // (no redraw on the mouse: a row rebuilt between press and release would lose the click)
        node.onmousemove = () => { if (marked !== index) { moved = true; mark(index); } };
        node.onclick = () => choose(index);
        rows.push({ node, run: item.run });
        group.appendChild(node);
      });
      listing.appendChild(group);
    });
    if (!rows.length) listing.appendChild(el("div", "quick-none", "Nothing found"));
    mark(moved && marked < rows.length ? marked : 0);
  }

  function choose(i) {
    const row = rows[i];
    if (!row) return;
    close();
    row.run();
  }
  function close() { if (box && box.open) box.close(); }

  // One group's request: an answer counts only if no newer one was asked for since, and a
  // failed one shows nothing (no message while typing).
  function load(group, url, pick) {
    const mine = ++latest[group];
    api(url).then(data => pick(data), () => []).then(items => {
      if (mine !== latest[group] || !box.open) return;
      remote[group] = items;
      draw();
    });
  }
  function ask() {
    const query = field.value.trim();
    if (query) {
      load("conversations", "/api/conversations?" + new URLSearchParams({ search: query }), d => d.conversations || []);
      if (local) load("notes", "/api/notes/search?" + new URLSearchParams({ q: query }), d => d.hits || []);
    } else {
      load("conversations", "/api/conversations", d => (d.conversations || []).slice()
        .sort((a, b) => String(b.updated || "").localeCompare(String(a.updated || ""))));
      latest.notes++;
      remote.notes = [];
    }
  }

  function build() {
    box = el("dialog");
    box.id = "quick";
    box.setAttribute("aria-label", "Quick switcher");
    const inner = el("div", "quick-box");
    field = el("input");
    field.type = "text"; field.autocomplete = "off"; field.spellcheck = false;
    field.setAttribute("role", "combobox");
    field.setAttribute("aria-expanded", "true");
    field.setAttribute("aria-controls", "quick-list");
    field.setAttribute("aria-autocomplete", "list");
    field.placeholder = "Search or jump to ...";
    listing = el("div", "quick-list");
    listing.id = "quick-list";
    listing.setAttribute("role", "listbox");
    listing.addEventListener("mousedown", (e) => e.preventDefault());  // (the focus stays in the box)
    inner.append(field, listing);
    box.appendChild(inner);
    field.addEventListener("input", () => {
      moved = false;
      remote = { conversations: [], notes: [] };  // (rows of an older text are never opened by Enter)
      latest.conversations++; latest.notes++;
      draw();
      clearTimeout(timer);
      timer = setTimeout(ask, WAIT);
    });
    // Keys pressed here are its own: the page's Esc (stop the answer) stays out of it.
    box.addEventListener("keydown", (e) => {
      if (!box.open) return;  // (a key right after closing: the focus has not left the box yet)
      e.stopPropagation();
      if (e.isComposing) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!rows.length) return;
        moved = true;
        mark((marked + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length);
      } else if (e.key === "Enter") { e.preventDefault(); choose(marked); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") e.preventDefault();
    });
    box.addEventListener("mousedown", (e) => { if (e.target === box) close(); });
    box.addEventListener("close", () => { clearTimeout(timer); latest.conversations++; latest.notes++; });
    document.body.appendChild(box);
  }

  async function open() {
    if (!box) build();
    if (box.open) return;
    field.value = "";
    remote = { conversations: [], notes: [] };
    moved = false;
    draw();
    box.showModal();
    field.focus();
    await atThePC();  // (usually known at once after the first time)
    if (!box.open) return;
    draw();
    ask();
  }

  document.addEventListener("keydown", (e) => {
    if (e.key.toLowerCase() !== "k" || !(e.ctrlKey || e.metaKey) || e.altKey) return;
    if (pageIsNotes() && !e.shiftKey) return;  // (the Notes page's own finder)
    if (document.querySelector("dialog.ask[open], dialog#shortcuts[open]")) return;
    if (e.target.closest && e.target.closest(".cm-editor") && e.shiftKey) return;  // (its own key: delete the line)
    e.preventDefault();
    if (pageIsNotes()) {
      e.stopImmediatePropagation();  // (the finder reacts to Ctrl+Shift+K too)
      document.getElementById("switcher").hidden = true;
    }
    open();
  });
})();
