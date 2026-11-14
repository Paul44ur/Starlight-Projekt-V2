// The message box: / commands, sending, keys, files, voice, conversation mode and photos.
// Part of the chat page (chat.html), which loads its parts in this order: chat-core.js,
// chat-messages.js, chat-list.js, chat-input.js, chat-page.js. They are plain scripts:
// what one declares at the top, the others use.
// Needs chat-core.js and chat-list.js (openMenu, popMenu).

// -- / commands: done here in the page, never sent to the model ---------------------------------
async function currentConversation() { return api("/api/conversations/" + conversation); }
const leavePractice = () => askYesNo("Leave practice mode?\n\nActions will run for real again.", { yes: "Leave" });
async function togglePractice() {
  const s = await api("/api/status");
  if (s.practice && !(await leavePractice())) return;
  try {
    await api("/api/practice", { method: "POST", body: { on: !s.practice } });
    toast(s.practice ? "Practice mode is off" : "Practice mode: actions are described, not done");
  } catch (e) { toast("Only at the PC: " + e.message); }
  refresh();
}
const COMMANDS = [
  ["/new", "plus", "New conversation", () => document.getElementById("new").click()],
  ["/private", "lock", "New private conversation", () => document.getElementById("newprivate").click()],
  ["/practice", "◌", "Practice mode on or off", togglePractice],
  ["/pin", "pin", "Pin or unpin this conversation", async () => {
    const c = await currentConversation(); changeConversation(conversation, { pinned: !c.pinned });
    toast(c.pinned ? "Unpinned" : "Pinned to the top");
  }],
  ["/archive", "archive", "Archive this conversation", async () => {
    await changeConversation(conversation, { archived: true }); toast("Archived (see Archived conversations in the list)");
  }],
  ["/export", "download", "Download it as Markdown", () => download(`/api/conversations/${conversation}/export`)],
  ["/pdf", "print", "Print or save it as PDF", () => window.open(`/api/conversations/${conversation}/export?format=html&show=true`, "_blank")],
  ["/activity", "activity", "What Starlight did", () => { location.href = "/activity"; }],
  ["/memory", "memory", "Memory and skills", () => { location.href = "/memory"; }],
  ["/settings", "settings", "Settings", () => { location.href = "/settings"; }],
  ["/stars", "✦", "A little starfall", () => starfall()],
  ["/help", "?", "These commands", () => {
    // (German keeps its capital letters: its nouns have them)
    const say = (text) => window.starlightTranslate ? window.starlightTranslate(text) : text, small = (text) => window.starlightLanguage === "de" ? text : text.toLowerCase();
    addSys(say("Commands:") + " " + COMMANDS.map(c => `${c[0]} (${small(say(c[2]))})`).join(", "));
  }],
];
// A typed command; false when the text is not one (it is then sent as a message).
function runCommand(text) {
  if (!/^\/[a-z]+$/.test(text)) return false;
  const found = COMMANDS.find(c => c[0] === text);
  if (found) found[3](); else toast(`No command ${text}: type / to see them`);
  return true;
}
let slashSel = 0, slashItems = [];
function slashMenu() {
  const text = input.value;
  if (!/^\/[a-z]*$/.test(text)) { if (openMenu && openMenu.slash) closeMenu(); return; }
  slashItems = COMMANDS.filter(c => c[0].startsWith(text));
  if (!slashItems.length) { closeMenu(); return; }
  slashSel = Math.max(0, Math.min(slashSel, slashItems.length - 1));
  const menu = popMenu(document.getElementById("form"),
    slashItems.map(([cmd, icon, label, action]) => [icon, label, () => { input.value = ""; input.oninput(); action(); }, cmd]),
    "above");
  menu.slash = true;
  menu.children[slashSel].classList.add("sel");
}

// Typing /stars: a little starfall, here in the page (nothing is sent to the model).
function starfall() {
  for (let i = 0; i < 48; i++) {
    const star = el("div", "star", "✦✧⋆★"[i % 4]);
    star.style.left = Math.random() * 100 + "vw";
    star.style.fontSize = 10 + Math.random() * 24 + "px";
    star.style.animationDuration = 2.2 + Math.random() * 2.4 + "s";
    star.style.animationDelay = Math.random() * 1.4 + "s";
    document.body.appendChild(star);
    setTimeout(() => star.remove(), 6500);
  }
}

document.getElementById("form").onsubmit = (e) => {
  e.preventDefault();
  if (busy()) { stopAnswer(); return; }
  const text = input.value.trim();
  if (!text) return;
  if (text.startsWith("/") && runCommand(text)) { input.value = ""; input.oninput(); return; }
  if (editing) { input.value = ""; input.style.height = INPUT_HEIGHT + "px"; saveDraft(); sendEdit(text); return; }
  const files = attached; attached = []; showFiles();
  document.getElementById("photo-actions").hidden = true;
  input.value = ""; input.style.height = INPUT_HEIGHT + "px"; saveDraft();  // (sent: no draft)
  ask(text, files);
};
input.onkeydown = (e) => {
  if (openMenu && openMenu.slash && slashItems.length) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      slashSel = (slashSel + (e.key === "ArrowDown" ? 1 : -1) + slashItems.length) % slashItems.length;
      slashMenu(); return;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      const pick = slashItems[slashSel];
      closeMenu(); input.value = ""; input.oninput(); pick[3](); return;
    }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    // Enter never stops an answer (you may be typing the next question): that is Esc or Stop.
    if (busy()) toast("Starlight is still answering: Esc or Stop ends it");
    else document.getElementById("form").requestSubmit();
  }
  else if (e.key === "ArrowUp" && !input.value) {
    // Up in an empty box brings back the last question, to change it or send it again.
    const asked = log.querySelectorAll(".msg.you .body");
    const last = lastQuestion || (asked.length ? asked[asked.length - 1].textContent : "");
    if (!last) return;
    e.preventDefault();
    input.value = last; input.oninput();
    input.setSelectionRange(last.length, last.length);
  }
};
function sizeInput() {
  input.style.height = INPUT_HEIGHT + "px";  // shrink first, then grow with the text
  input.style.height = Math.min(input.scrollHeight, INPUT_MAX_HEIGHT) + "px";
}
input.oninput = () => {
  sizeInput();
  if (!input.value.startsWith("/")) slashSel = 0;
  slashMenu();
  clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 300);
};

// -- drafts: what you typed and did not send stays with its conversation -----------------------
// In localStorage, one key each (nothing goes to the server). A private conversation's draft
// stays in this Map and never on disk. Attached files are not part of a draft.
const DRAFT = "starlight-draft:";
const privateDrafts = new Map();
let draftFor = null, draftPrivate = false, draftTimer = null;  // draftFor: the conversation the box belongs to
function saveDraft() {
  clearTimeout(draftTimer);
  if (!draftFor) return;
  const text = input.value;
  if (draftPrivate) { if (text) privateDrafts.set(draftFor, text); else privateDrafts.delete(draftFor); return; }
  try { if (text) localStorage.setItem(DRAFT + draftFor, text); else localStorage.removeItem(DRAFT + draftFor); } catch (e) { /* full or off: no drafts */ }
}
// Another conversation is opened: keep this box's text for the old one, show the new one's draft.
function switchDraft(id, isPrivate) {
  if (id === draftFor) { draftPrivate = isPrivate; return; }  // (the same one shown again: the box stays)
  if (editing) { cancelEdit(); input.value = ""; }  // (an edit belongs to the old conversation)
  saveDraft();
  const first = draftFor === null;
  draftFor = id; draftPrivate = isPrivate;
  if (first && input.value) { saveDraft(); return; }  // (text put there by a shared link: it stays)
  let text = "";
  if (isPrivate) text = privateDrafts.get(id) || "";
  else { try { text = localStorage.getItem(DRAFT + id) || ""; } catch (e) { /* */ } }
  input.value = text;
  sizeInput();  // (not oninput: a draft starting with / must not open the command menu)
  if (openMenu && openMenu.slash) closeMenu();
}
// A conversation is deleted: its draft goes too.
function dropDraft(id) {
  privateDrafts.delete(id);
  try { localStorage.removeItem(DRAFT + id); } catch (e) { /* */ }
  if (id === draftFor) { clearTimeout(draftTimer); draftFor = null; input.value = ""; sizeInput(); }
}
// Drafts of conversations that are gone: looked at once when the page opens, with the whole
// list (the open ones and the archived ones, neither filtered nor cut at 200); otherwise skipped.
async function cleanDrafts() {
  try {
    const [open, archived] = await Promise.all([api("/api/conversations"), api("/api/conversations?archived=true")]);
    if (open.conversations.length >= 200 || archived.conversations.length >= 200) return;
    const known = new Set([...open.conversations, ...archived.conversations].map(c => c.id));
    if (draftFor) known.add(draftFor);
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key && key.startsWith(DRAFT) && !known.has(key.slice(DRAFT.length))) localStorage.removeItem(key);
    }
  } catch (e) { /* */ }
}
window.addEventListener("pagehide", saveDraft);

// Esc stops the answer that is being written (as the Stop button does).
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (openMenu) { closeMenu(true); return; }
  if (document.body.classList.contains("drawer")) { setDrawer(false); document.getElementById("menu").focus(); return; }
  if ((current || answering) && !e.target.closest(".confirm")) {
    e.preventDefault(); stopAnswer(); toast("Stopping …");
  } else if (editing) { cancelEdit(); input.value = ""; input.oninput(); }
});
document.getElementById("practice").onclick = async () => {
  if (!(await leavePractice())) return;
  try { await api("/api/practice", { method: "POST", body: { on: false } }); toast("Practice mode is off"); }
  catch (e) { toast("Turn it off at the PC: " + e.message); }
  refresh();
};
document.getElementById("paused").onclick = async () => {
  try {
    const r = await api("/api/model/resume", { method: "POST" });
    toast(r.resumed ? "The model is loading again (about a minute)" : "It was not paused");
  } catch (e) { toast("Could not resume: " + e.message); }
  refresh();
};
document.getElementById("kill").onclick = async () => {
  const k = document.getElementById("kill");
  try { await api(k.dataset.on === "1" ? "/api/resume" : "/api/kill", { method: "POST" }); }
  catch (e) { toast(e.message); }
  refresh();
};

// -- files ---------------------------------------------------------------------------------
async function upload(file) {
  const response = await fetch("/api/upload", { method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { toast(data.detail || "Could not add " + file.name); return; }
  attached.push({ id: data.id, name: data.name });
  showFiles();
}
function showFiles() {
  const box = document.getElementById("files"); box.innerHTML = "";
  attached.forEach((f, i) => {
    const p = el("span", "pill", "📎 " + f.name + " ✕");
    p.title = "Remove"; p.onclick = () => { attached.splice(i, 1); showFiles(); };
    box.appendChild(p);
  });
}
const picker = document.getElementById("picker");
document.getElementById("attach").onclick = () => picker.click();
picker.onchange = () => { [...picker.files].forEach(upload); picker.value = ""; };
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { dragDepth++; document.body.classList.add("dragging"); } });
window.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) document.body.classList.remove("dragging"); });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; document.body.classList.remove("dragging"); [...(e.dataTransfer.files || [])].forEach(upload); });
input.addEventListener("paste", (e) => { [...(e.clipboardData.files || [])].forEach(upload); });

// -- voice -----------------------------------------------------------------------------------
let speakAnswers = false, recorder = null;
try { speakAnswers = localStorage.getItem("starlight-speak") === "1"; } catch (e) { /* */ }
function showSpeaker() {
  const b = document.getElementById("speaker");
  setIcon(b, speakAnswers ? "speaker" : "mute"); b.setAttribute("aria-pressed", String(speakAnswers));
}
document.getElementById("speaker").onclick = () => {
  speakAnswers = !speakAnswers; showSpeaker();
  try { localStorage.setItem("starlight-speak", speakAnswers ? "1" : "0"); } catch (e) { /* */ }
};
async function say(text) {
  try {
    const response = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!response.ok) { const d = await response.json().catch(() => ({})); toast(d.detail || "Could not speak"); return; }
    new Audio(URL.createObjectURL(await response.blob())).play();
  } catch (e) { toast("Could not speak"); }
}
// (startRecording and wav are in common.js: the Notes page records memos too)
document.getElementById("mic").onclick = async () => {
  const mic = document.getElementById("mic");
  if (!recorder) {
    if (!navigator.mediaDevices) { toast("The microphone needs https:// or this PC"); return; }
    try { recorder = await startRecording(); mic.classList.add("on"); }
    catch (e) { toast("No microphone access: " + e.message); }
    return;
  }
  mic.classList.remove("on");
  const audio = await recorder.stop(); recorder = null;
  input.placeholder = "Listening…";
  try {
    const response = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: audio });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { toast(data.detail || "Could not understand that"); return; }
    input.value = (input.value ? input.value + " " : "") + (data.text || "");
    input.focus(); input.oninput();
  } finally { input.placeholder = idleHint(); }
};
showSpeaker();

// -- conversation mode: talk, hear the answer, talk again; speaking interrupts the answer ---
// One microphone stream stays open while it is on. What it hears is cut into stretches of
// speech (louder than the room's own noise, then a short pause); each one is written down by
// the speech recognition on the PC and sent. While the answer is read out, speaking stops it.
const TALK = { frame: 4096, pause: 0.8, longest: 30, ratio: 3, bargeIn: 0.25 };
let talk = null;  // { stream, context, node, source, state, chunks, ... } while on

function rms(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / (samples.length || 1));
}
// The talking state machine, free of the browser so it can be tried out on its own:
// feed(level, seconds) for each piece of sound; it says "start", "end", "barge" or "".
function talkMachine(opts = TALK) {
  const m = { noise: 0.004, speaking: false, quiet: 0, voiced: 0, playing: false, loud: 0 };
  m.feed = (level, seconds) => {
    const loud = level > Math.max(m.noise * opts.ratio, 0.01);
    if (m.playing) {  // the answer is being read out: only a voice for a moment counts
      m.loud = loud ? m.loud + seconds : 0;
      if (m.loud >= opts.bargeIn) { m.loud = 0; m.playing = false; m.speaking = true; m.quiet = 0; m.voiced = seconds; return "barge"; }
      return "";
    }
    if (!m.speaking) {
      if (!loud) { m.noise = 0.95 * m.noise + 0.05 * level; return ""; }
      m.speaking = true; m.quiet = 0; m.voiced = seconds; return "start";
    }
    if (loud) { m.quiet = 0; m.voiced += seconds; } else m.quiet += seconds;
    if (m.quiet >= opts.pause || m.voiced + m.quiet >= opts.longest) {
      m.speaking = false; const enough = m.voiced >= 0.3; m.voiced = 0;
      return enough ? "end" : "";
    }
    return "";
  };
  return m;
}

let talkAudio = null;  // the answer being read out in conversation mode
async function startTalk() {
  if (!navigator.mediaDevices) { toast("The microphone needs https:// or this PC"); return; }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const node = context.createScriptProcessor(TALK.frame, 1, 1);
  talk = { stream, context, source, node, machine: talkMachine(), chunks: [], before: [], waiting: false };
  node.onaudioprocess = (e) => {
    const samples = new Float32Array(e.inputBuffer.getChannelData(0));
    const what = talk.waiting ? "" : talk.machine.feed(rms(samples), samples.length / context.sampleRate);
    if (what === "barge" && talkAudio) { talkAudio.pause(); talkAudio = null; talk.chunks = [...talk.before]; }
    if (what === "start") talk.chunks = [...talk.before];
    talk.before = [...talk.before.slice(-2), samples];  // a moment from before the voice began
    if (talk.machine.speaking || what === "end") talk.chunks.push(samples);
    if (what === "end") heardInTalk(wav(talk.chunks, context.sampleRate));
  };
  source.connect(node); node.connect(context.destination);
  document.getElementById("talk").classList.add("on");
  document.getElementById("talk").setAttribute("aria-pressed", "true");
  input.placeholder = "Conversation: just talk (press 🗣 to end)";
}
function stopTalk() {
  if (!talk) return;
  talk.node.disconnect(); talk.source.disconnect(); talk.stream.getTracks().forEach(t => t.stop());
  talk.context.close(); talk = null;
  if (talkAudio) { talkAudio.pause(); talkAudio = null; }
  document.getElementById("talk").classList.remove("on");
  document.getElementById("talk").setAttribute("aria-pressed", "false");
  input.placeholder = idleHint();
}
async function heardInTalk(audio) {
  if (!talk) return;
  talk.waiting = true;  // not listening while it writes down and answers
  try {
    const response = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: audio });
    const data = await response.json().catch(() => ({}));
    const text = (data.text || "").trim();
    if (!response.ok) { toast(data.detail || "Could not understand that"); talk.waiting = false; return; }
    if (!text || busy()) { talk.waiting = false; return; }
    const files = attached; attached = []; showFiles();
    ask(text, files);
  } catch (e) { toast("Could not understand that"); if (talk) talk.waiting = false; }
}
// The answer came: read it out, and listen again (for the next question or to interrupt).
async function talkAnswer(text) {
  if (!talk) return;
  talk.waiting = false;
  try {
    const response = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!response.ok || !talk) return;
    talkAudio = new Audio(URL.createObjectURL(await response.blob()));
    talk.machine.playing = true;
    talkAudio.onended = () => { if (talk) talk.machine.playing = false; talkAudio = null; };
    await talkAudio.play();
  } catch (e) { if (talk) talk.machine.playing = false; }
}
document.getElementById("talk").onclick = async () => {
  if (talk) { stopTalk(); return; }
  if (recorder) document.getElementById("mic").click();  // (one microphone at a time)
  try { await startTalk(); } catch (e) { toast("No microphone access: " + e.message); stopTalk(); }
};

// -- a photo from the phone's camera: read the text, translate it, or describe it --------
const PHOTO = [["Read the text", "Write out all the text in this picture."],
               ["Translate it", "Translate the text in this picture into my language."],
               ["Describe it", "Describe what is in this picture."]];
document.getElementById("camera").onclick = () => document.getElementById("shot").click();
document.getElementById("shot").onchange = async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  const before = attached.length;
  await upload(file);
  if (attached.length === before) return;
  const box = document.getElementById("photo-actions");
  box.replaceChildren(); box.hidden = false;
  PHOTO.forEach(([label, prompt]) => {
    const b = el("button", "pill", label); b.type = "button";
    b.onclick = () => { box.hidden = true; input.value = prompt; document.getElementById("form").requestSubmit(); };
    box.appendChild(b);
  });
};
