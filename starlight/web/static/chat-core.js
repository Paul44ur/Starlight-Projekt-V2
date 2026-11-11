// The page's state, scrolling, the socket, handle() and the message flow.
// Part of the chat page (chat.html), which loads its parts in this order: chat-core.js,
// chat-messages.js, chat-list.js, chat-input.js, chat-page.js. They are plain scripts:
// what one declares at the top, the others use.
// Needs common.js, markdown.js and render.js; the functions of the later files are only
// called once a message arrives or a key is pressed, when every file has loaded.

const log = document.getElementById("log");
const input = document.getElementById("input");
// The full hint is too long for a phone: the placeholder would be cut off.
const HINT = "Ask Starlight…  (Enter to send, Shift+Enter for a new line)";
const narrow = matchMedia("(max-width: 760px)");
const idleHint = () => (narrow.matches ? "Ask Starlight…" : HINT);
input.placeholder = idleHint();
narrow.addEventListener("change", () => { if (!recorder) input.placeholder = idleHint(); });
const connPill = document.getElementById("conn");
// current: the .body element of the answer being written into (null between answers);
// answering: the id of the conversation that answer belongs to (null when none).
let ws, current = null, conversation = null, lastQuestion = null, attached = [];
let answering = null, regenPending = false, resync = false;  // see handle() and connect()
const INPUT_HEIGHT = 44, INPUT_MAX_HEIGHT = 180;  // px: #input's min- and max-height above

// Pinned: you are at the newest message and want to stay there. Only scrolling up yourself
// unpins (scrolling back down pins again): when the conversation's area gets shorter (the
// message box grows as you type, or the window is resized), the scroll position stays and the
// newest lines would slide out of view, so a pinned page follows them (ResizeObserver below).
let pinned = true, lastTop = 0;
function nearBottom() { return log.scrollHeight - log.scrollTop - log.clientHeight < 80; }
function atBottom() { return pinned || nearBottom(); }
function scrollToEnd() { log.scrollTop = log.scrollHeight; }
// Scrolled up to read: a button back down, with a dot when something new arrived below.
const toLatest = document.getElementById("tolatest");
function syncLatest() {
  toLatest.hidden = atBottom();
  if (toLatest.hidden) toLatest.classList.remove("new");
}
log.addEventListener("scroll", () => {
  if (log.scrollTop < lastTop - 1) pinned = nearBottom();  // up: reading, unless still close
  else if (nearBottom()) pinned = true;  // back down at the newest message
  lastTop = log.scrollTop;
  syncLatest();
}, { passive: true });
new ResizeObserver(() => { if (pinned) scrollToEnd(); syncLatest(); }).observe(log);
new MutationObserver(() => { if (!atBottom()) toLatest.classList.add("new"); syncLatest(); })
  .observe(log, { childList: true, subtree: true, characterData: true });
toLatest.onclick = () => log.scrollTo({ top: log.scrollHeight,
  behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
// (the button floats just above the message box, however tall that has grown)
const footer = document.querySelector("footer");
new ResizeObserver(() => {
  document.getElementById("main").style.setProperty("--footer-h", footer.offsetHeight + "px");
  document.documentElement.style.setProperty("--toast-bottom", footer.offsetHeight + 12 + "px");  // (above it)
}).observe(footer);
// The keys of this page, for the ? window (common.js).
window.SHORTCUTS = [
  ["Enter", "Send"], ["Shift+Enter", "A new line"], ["Esc", "Stop the answer, or close a menu"],
  ["↑", "In the empty box: your last question again"], ["/", "Commands"],
  ["Ctrl+Alt+J", "Kill switch: stop everything Starlight does (anywhere on the PC)"],
  ["Ctrl+/", "These shortcuts (? when not typing)"],
];

function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => {
    connPill.textContent = "connected"; connPill.className = "pill on";
    if (resync && conversation) openConversation(conversation);  // an answer that was replaced is shown again
    resync = false;
  };
  ws.onclose = () => {
    connPill.textContent = "offline"; connPill.className = "pill off";
    // The server drops the answer in progress and the questions it was waiting on with the
    // socket: forget them here too, or Regenerate would wait for an answer that never ends.
    if (current || answering) addSys("Connection lost: the answer was interrupted.");
    if (regenPending) resync = true;
    doneWaiting(current);
    current = null; answering = null; regenPending = false; asking = false; syncSend();
    log.querySelectorAll(".confirm").forEach(card => card.remove());
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (["start", "busy", "error", "stopped", "answer"].includes(m.type)) asking = false;
    handle(m);
    syncSend();
  };
}

// The conversation an answer is coming for: if you open another one meanwhile, its words
// are not shown there (open the first one again to see the answer).
// The answer being written is for another conversation than the one open (by `answering`,
// set at "start": token, stopped and error messages carry no conversation of their own).
function answerIsElsewhere() { return answering && answering !== conversation; }
function handle(m) {
  if (m.type === "start") {
    if (!conversation) conversation = m.conversation;
    answering = m.conversation || conversation;
    current = addMsg("bot", "");
    waitFor(current);
    if (regenPending) current.parentElement.dataset.regen = "1";
  }
  else if (m.type === "tool") {
    if (answerIsElsewhere()) return;
    if (!current) { current = addMsg("bot", ""); waitFor(current); }
    toolStep(current, m.tool, m.state, m.ms, m.error);
  }
  else if (m.type === "changed") {
    if (answerIsElsewhere()) return;
    if (!current) { current = addMsg("bot", ""); waitFor(current); }
    changeChip(current, m);
  }
  else if (m.type === "thought") {
    if (answerIsElsewhere()) return;
    if (!current) { current = addMsg("bot", ""); waitFor(current); }
    addThought(current, m.text);
  }
  else if (m.type === "token") {
    if (answerIsElsewhere()) return;
    if (!current) current = addMsg("bot", ""); doneWaiting(current);
    addToAnswer(current, m.text);
  }
  else if (m.type === "answer" && answerIsElsewhere()) { answering = null; regenPending = false; current = null; loadList(); }
  else if (m.type === "answer") {
    // The final answer replaces what streamed in: it is the cleaned one (a reply cut
    // where the model started repeating itself may have streamed a little too far).
    // It and its stats can be longer than what streamed (or nothing streamed): stay at the
    // newest message if you were there, as for every other message.
    const stick = atBottom();
    if (m.text || !current) setAnswer(current || (current = addMsg("bot", "")), m.text);
    doneWaiting(current); toolsDone(current);
    addStats(current, m.stats);
    if (m.stats && m.stats.context_tokens) showContext(m.stats.context_tokens);
    if (stick) scrollToEnd();
    if (!m.stopped && !m.failed) current.parentElement.dataset.kept = "1";
    else if (editPending) { toast("Not changed: the earlier question and answer stay"); setTimeout(() => openConversation(conversation), 1500); }
    else if (regenPending) current.parentElement.dataset.regen = "1";  // the old answer stays
    editPending = false;
    if (m.stopped) addSys("Kill switch engaged. Press Resume on the PC to continue.");
    else if (talk) talkAnswer(markdownToSpeech(m.text));
    else if (speakAnswers) say(markdownToSpeech(m.text));
    current = null; answering = null; regenPending = false; loadList();
    signalReady("Your answer is ready");
  }
  else if ((m.type === "stopped" || m.type === "error") && regenPending && !current) {
    // Asking again did not even start: the earlier answer stays, so show it again.
    regenPending = false; answering = null;
    toast(m.type === "stopped" ? "Stopped; the earlier answer stays" : m.text);
    if (conversation) openConversation(conversation);
  }
  else if (m.type === "stopped") {
    doneWaiting(current); toolsDone(current);
    if (editPending) { editPending = false; setTimeout(() => openConversation(conversation), 300); }
    if (!answerIsElsewhere()) addSys("[stopped]");
    current = null; answering = null; regenPending = false;
  }
  else if (m.type === "error") {
    if (talk) talk.waiting = false;
    doneWaiting(current); toolsDone(current);
    if (editPending) { editPending = false; setTimeout(() => openConversation(conversation), 1500); }
    if (!answerIsElsewhere()) {
      const b = addMsg("bot", "⚠ " + m.text, null, true); b.style.color = "var(--bad)";
      if (regenPending) b.parentElement.dataset.regen = "1";
    }
    current = null; answering = null; regenPending = false;
  }
  else if (m.type === "notify") { addSys("ℹ " + m.text); }
  else if (m.type === "busy") {
    addSys("Starlight is still working on the last message.");
    if (regenPending) { regenPending = false; if (conversation) openConversation(conversation); }  // show the old answer again
  }
  else if (m.type === "confirm") { showConfirm(m); signalReady("Starlight is waiting for your OK"); }
  else if (m.type === "prompt") { showPrompt(m); signalReady("Starlight is waiting for your OK"); }
}

function showConfirm(m) {
  const stick = atBottom();
  const card = el("div", "confirm");
  card.appendChild(el("div", "q", m.question));
  const row = el("div", "row");
  const yes = el("button", "ok", "Approve");
  const no = el("button", "", "Deny");
  yes.onclick = () => { ws.send(JSON.stringify({ type: "reply", id: m.id, value: "yes" })); card.remove(); };
  no.onclick = () => { ws.send(JSON.stringify({ type: "reply", id: m.id, value: "no" })); card.remove(); };
  row.append(yes, no); card.appendChild(row);
  log.appendChild(card); if (stick) scrollToEnd();
}
function showPrompt(m) {
  const stick = atBottom();
  const card = el("div", "confirm");
  card.appendChild(el("div", "q", m.question));
  const box = document.createElement("input");
  box.type = m.secret ? "password" : "text"; box.style.cssText = "width:100%;margin-bottom:8px";
  const send = el("button", "ok", "Send");
  const go = () => { ws.send(JSON.stringify({ type: "reply", id: m.id, value: box.value })); card.remove(); };
  send.onclick = go; box.onkeydown = (e) => { if (e.key === "Enter") go(); };
  card.append(box, send); log.appendChild(card); if (stick) scrollToEnd(); box.focus();
}

function ask(text, files) {
  if (!ws || ws.readyState !== WebSocket.OPEN) { toast("Not connected yet"); return; }
  lastQuestion = text;
  asking = true; syncSend();
  addMsg("you", text, files.map(f => f.name));
  ws.send(JSON.stringify({ type: "ask", text, conversation, attachments: files.map(f => f.id) }));
}

function stopAnswer() { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" })); }
// While an answer is being written, the Send button stops it.
let asking = false;  // a question was sent and its answer has not begun yet
function busy() { return Boolean(asking || current || answering); }
// Every message from the PC calls this: the button is only rebuilt when Send becomes Stop or
// back (a click on it while an answer streams must reach it, and the work stays small).
let sendShows = null;
function syncSend() {
  if (busy() === sendShows) return;
  sendShows = busy();
  const b = document.getElementById("send");
  b.replaceChildren(el("span", "label", busy() ? "Stop" : "Send"));
  setIcon(b, busy() ? "stop" : "send");  // (the icon shows on a narrow phone)
  b.className = busy() ? "stop" : "primary";
  b.title = busy() ? "Stop this answer (Esc)" : "Send (Enter)";
}

// -- edit your last question, branch from an answer --------------------------------------------
let editing = null;  // the question (.msg.you) being changed
function startEdit(msg) {
  if (busy()) { toast("Wait until this answer is finished"); return; }
  cancelEdit();
  editing = msg;
  msg.classList.add("editing");
  input.value = msg.querySelector(".body").textContent; input.oninput(); input.focus();
  input.placeholder = "Change your question … (Enter asks it again, Esc cancels)";
}
function cancelEdit() {
  if (editing) editing.classList.remove("editing");
  editing = null; input.placeholder = idleHint();
}
function sendEdit(text) {
  const question = editing; cancelEdit();
  const old = question.querySelector(".body").textContent;
  let answer = question.nextElementSibling;
  while (answer && !answer.classList.contains("bot")) answer = answer.nextElementSibling;
  // kept: the server saved this answer; regen: it holds the earlier one while a new one is
  // asked. Either way the exchange is on the server, so the edit is sent as a regenerate.
  const kept = answer && (answer.dataset.kept || answer.dataset.regen);
  while (question.nextElementSibling) question.nextElementSibling.remove();
  question.remove();
  lastQuestion = text; asking = true; syncSend();
  addMsg("you", text);
  if (kept) {  // the server replaces that exchange; if this fails, the old one stays
    regenPending = true; editPending = true;
    ws.send(JSON.stringify({ type: "edit", question: old, text, conversation }));
  } else ws.send(JSON.stringify({ type: "ask", text, conversation }));
}
let editPending = false;
// The exchanges the server keeps: each question whose answer was kept.
function keptTurns(upTo) {
  let turns = 0;
  for (const m of log.querySelectorAll(".msg.you, .msg.bot")) {
    if (m.classList.contains("bot") && m.dataset.kept) turns++;
    if (m === upTo) return turns;
  }
  return turns;
}
async function branchFrom(msg) {
  if (!msg.dataset.kept) { toast("Only a saved answer can be a branch point"); return; }
  try {
    const d = await api(`/api/conversations/${conversation}/branch`, { method: "POST", body: { turns: keptTurns(msg) } });
    await openConversation(d.id);
    toast("A new branch from there; the original stays as it was");
  } catch (e) { toast(e.message); }
}
// Only the latest answer can be regenerated: it replaces that answer. An answer the server
// kept (or the old one it keeps after a failed regenerate) is asked again with "regenerate";
// one that failed or was stopped was never kept, so its question is simply asked again.
function regenerate(msg) {
  const answers = log.querySelectorAll(".msg.bot");
  if (msg !== answers[answers.length - 1]) { toast("Only the latest answer can be regenerated"); return; }
  if (current || answering) { toast("Wait until this answer is finished"); return; }
  if (!ws || ws.readyState !== WebSocket.OPEN) { toast("Not connected"); return; }
  let q = msg.previousElementSibling;
  while (q && !q.classList.contains("you")) q = q.previousElementSibling;
  const question = q ? q.querySelector(".body").textContent : "";
  if (msg.dataset.kept || msg.dataset.regen) {
    msg.remove(); markLatest(); regenPending = true;
    // The question tells the server which exchange is meant: another window may have
    // asked something since this page last showed the conversation.
    ws.send(JSON.stringify({ type: "regenerate", conversation, question }));
    return;
  }
  const text = question || lastQuestion;
  if (!text) { toast("Nothing to ask again"); return; }
  msg.remove(); markLatest();
  asking = true; syncSend();
  ws.send(JSON.stringify({ type: "ask", text, conversation }));
}
