// Drawing messages: a message, Markdown, tools, changes, stats, waiting and thoughts.
// Part of the chat page (chat.html), which loads its parts in this order: chat-core.js,
// chat-messages.js, chat-list.js, chat-input.js, chat-page.js. They are plain scripts:
// what one declares at the top, the others use.
// Needs the state of chat-core.js.

// Your messages show as you wrote them; answers are drawn from their Markdown (plain: a
// notice that is not the model's).
function addMsg(who, text, files, plain) {
  const stick = atBottom();
  log.querySelector(".empty")?.remove();  // (the start of a new conversation)
  const m = el("div", "msg " + (who === "you" ? "you" : "bot"));
  m.appendChild(el("div", "who", who === "you" ? "you" : "starlight"));
  const body = el("div", "body", who === "you" || plain ? text || "" : undefined);
  if (who !== "you" && !plain) setAnswer(body, text || "");
  m.appendChild(body);
  if (files && files.length) m.appendChild(el("div", "att", "📎 " + files.join(", ")));
  const tools = el("div", "tools");
  if (who === "you") {
    const edit = el("button", "icon edit", "Edit");
    edit.title = "Change this question and ask it again (its answer is replaced)";
    edit.onclick = () => startEdit(m);
    tools.append(edit);
  } else {
    const copy = el("button", "icon", "Copy");
    copy.onclick = () => navigator.clipboard.writeText(body.raw ?? body.textContent).then(() => toast("Copied"));
    const retry = el("button", "icon regen", "Regenerate");
    retry.title = "Ask the last message again and replace this answer";
    retry.onclick = () => regenerate(m);
    const fork = el("button", "icon branch", "Branch");
    fork.title = "A new conversation up to here, to go another way (this one stays)";
    fork.onclick = () => branchFrom(m);
    tools.append(copy, retry, fork);
  }
  m.appendChild(tools);
  log.appendChild(m);
  markLatest();
  if (stick) scrollToEnd();
  return body;
}
function markLatest() {
  for (const kind of [".msg.bot", ".msg.you"]) {
    const all = log.querySelectorAll(kind);
    all.forEach((a, i) => a.classList.toggle("latest", i === all.length - 1));
  }
}

// -- the tools an answer used ------------------------------------------------------------------
const TOOL_MARK = { running: "⋯", done: "✓", failed: "✗", refused: "⊘", practice: "◌", stopped: "■" };
const TOOL_SAYS = { running: "running …", done: "done", failed: "failed", refused: "not run",
                    practice: "practice mode: described, not run", stopped: "stopped" };
function toolStep(body, tool, state, ms, error) {
  let row = body.toolRow;
  if (!row) {
    row = body.toolRow = el("div", "toolrow");
    body.parentElement.insertBefore(row, body);
  }
  let chip = state === "running" ? null
    : [...row.children].reverse().find(c => c.dataset.tool === tool && c.dataset.state === "running");
  if (!chip) { chip = el("span", "toolstep"); chip.dataset.tool = tool; row.appendChild(chip); }
  chip.dataset.state = state;
  const time = ms ? " " + (ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(1) + " s") : "";
  chip.textContent = `${TOOL_MARK[state] || "•"} ${tool}${time}`;
  chip.title = error || TOOL_SAYS[state] || "";
}
// A note or file the assistant changed: a chip under the answer. Undo sends only the id the
// server gave the change (never a path), and the server keeps the current file as a version.
function changeChip(body, m) {
  let row = body.changeRow;
  if (!row) { row = body.changeRow = el("div", "changerow"); body.after(row); }
  const chip = el("span", "change");
  chip.title = m.path;
  const label = el("span", "", m.new ? "New:" : "Changed:");
  chip.append(label, el("b", "", m.title));
  if (m.undoable) {
    const undo = el("button", "", "Undo"); undo.type = "button";
    undo.onclick = async () => {
      undo.disabled = true;
      try {
        await api("/api/changes/" + encodeURIComponent(m.id) + "/undo", { method: "POST" });
        label.textContent = "Undone:"; undo.remove(); chip.classList.add("undone");
      } catch (e) { toast(e.message); undo.disabled = false; }
    };
    chip.appendChild(undo);
  }
  row.appendChild(chip);
}
function toolsDone(body) {  // the answer ended: a tool still "running" was stopped
  if (body) body.parentElement.classList.remove("streaming");
  if (body && body.toolRow) body.toolRow.querySelectorAll('[data-state="running"]').forEach(c => {
    c.dataset.state = "stopped"; c.textContent = `${TOOL_MARK.stopped} ${c.dataset.tool}`; c.title = TOOL_SAYS.stopped;
  });
}

// An answer's text (body.raw), drawn at once; while it streams in, at most once a frame.
function setAnswer(body, text) {
  body.raw = text;
  body.classList.add("md");
  renderMarkdown(body, text);
}
function addToAnswer(body, text) {
  body.raw = (body.raw || "") + text;
  body.classList.add("md");
  body.parentElement.classList.add("streaming");  // (until toolsDone: the answer ended)
  if (body.drawing) return;
  body.drawing = true;
  requestAnimationFrame(() => {
    body.drawing = false;
    const stick = atBottom();
    renderMarkdown(body, body.raw);
    if (stick) scrollToEnd();
  });
}
function addStats(body, stats) {
  if (!stats || !stats.seconds) return;
  const parts = [];
  if (stats.tokens_per_second) parts.push(stats.tokens_per_second + " tok/s");
  parts.push(stats.seconds + " s");
  body.parentElement.appendChild(el("div", "att", parts.join(" · ")));
}
function addSys(text) { const stick = atBottom(); log.appendChild(el("div", "msg sys", text)); if (stick) scrollToEnd(); }

// Until the first word arrives the answer shows that Starlight is at work, and after a few
// seconds for how long (a model that thinks first can take a minute).
function waitFor(body) {
  const started = Date.now();
  body.classList.add("waiting");
  body.dataset.wait = "…";
  const timer = setInterval(() => {
    if (!body.isConnected || !body.classList.contains("waiting")) { clearInterval(timer); return; }
    const seconds = Math.round((Date.now() - started) / 1000);
    if (seconds >= 3) body.dataset.wait = `Working on it … ${seconds} s`;
  }, 1000);
}
function doneWaiting(body) { if (body) { body.classList.remove("waiting"); thoughtsDone(body); } }

// The model's thinking while it comes, folded away above the answer: open it to read
// along. Only as text, never HTML; it is not kept with the conversation.
function addThought(body, text) {
  let box = body.thoughts;
  if (!box) {
    box = body.thoughts = el("details", "thoughts");
    box.appendChild(el("summary", "", "Thinking …"));
    box.text = box.appendChild(el("div", "thought-text"));
    box.started = Date.now();
    body.parentElement.insertBefore(box, body);
  }
  const stick = atBottom();
  box.text.textContent += text;
  if (box.open) box.text.scrollTop = box.text.scrollHeight;
  if (stick) scrollToEnd();
}
function thoughtsDone(body) {
  const box = body.thoughts;
  if (!box || box.done) return;
  box.done = true;
  const seconds = Math.max(1, Math.round((Date.now() - box.started) / 1000));
  box.firstChild.textContent = `Thought for ${seconds} s`;
}
