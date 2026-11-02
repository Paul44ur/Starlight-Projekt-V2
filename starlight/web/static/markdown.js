// Markdown in Starlight's answers (headings, lists, tables, code, bold, links), drawn as page
// elements. The model's text only ever becomes textContent, never HTML: an answer cannot run
// a script or load anything, and a link opens only if it is http(s) or mailto. A
// starlight://note/... link (block G6) becomes a link to that note on the Notes page.
//
// render.js fills in mdHooks for notes: links between notes ([[Note]]), pictures, tags,
// callouts and task boxes. Without it those stay plain text.
const mdHooks = { wikilink: null, text: null, quote: null, item: null };

const MD_FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)[^`]*$/;
const MD_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const MD_RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const MD_QUOTE = /^\s{0,3}>\s?(.*)$/;
const MD_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const MD_TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
// `code`, **bold**, __bold__, ~~struck~~, *italic*, _italic_, [text](link), a bare link,
// and maths: $$x$$, \(x\), $x$ (no space just inside the dollars, and no digit after the
// closing one: "$5 and $10" stays money)
const MD_INLINE = new RegExp([
  "(`+)([^`][\\s\\S]*?)\\1(?!`)",
  "\\*\\*(?=\\S)([\\s\\S]*?\\S)\\*\\*",
  "(?<![\\w])__(?=\\S)([\\s\\S]*?\\S)__(?![\\w])",
  "~~(?=\\S)([\\s\\S]*?\\S)~~",
  "\\*(?=[^\\s*])([\\s\\S]*?[^\\s*])\\*",
  "(?<![\\w])_(?=[^\\s_])([\\s\\S]*?[^\\s_])_(?![\\w])",
  "\\[([^\\]\\n]+)\\]\\(\\s*<?([^\\s()<>]+(?:\\([^\\s()]*\\))?)>?\\s*\\)",
  "((?:https?|starlight):\\/\\/[^\\s<>]*[^\\s<>()\\[\\].,;:!?'\"*_~])",
  "\\$\\$([^$\\n]+?)\\$\\$",
  "\\\\\\(([^\\n]+?)\\\\\\)",
  "(?<![\\w$\\\\])\\$(?=[^\\s$])([^$\\n]*?[^\\s$\\\\])\\$(?![\\d$])",
  "(!?)\\[\\[([^\\[\\]\\n]+?)\\]\\]",  // [[Note]], ![[photo.png]] (render.js draws them)
].join("|"), "g");
// $$ … $$ or \[ … \] on lines of their own (or on one line)
const MD_MATH_BLOCK = /^\s{0,3}(\$\$|\\\[)\s*(.*?)\s*(\$\$|\\\])?\s*$/;

function mdSafeHref(href) {
  try {
    const url = new URL(href, location.href);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
  } catch (e) { return null; }
}

// starlight://note/Notes/Plan.md (or /Plan, #Heading), starlight://notes, starlight://chat:
// the page of this web app it means, or null (the same rules as the launcher's link_target).
function mdStarlightHref(href) {
  const m = /^starlight:\/\/(note|notes|chat)(?:\/([^#]*))?(?:#(.*))?$/i.exec(href);
  if (!m) return null;
  let name, heading;
  try {
    name = decodeURIComponent(m[2] || "").replace(/^\/+|\/+$/g, "").trim();
    heading = decodeURIComponent(m[3] || "").trim();
  } catch (e) { return null; }
  const kind = m[1].toLowerCase();
  if (kind !== "note") return name ? null : (kind === "chat" ? "/" : "/notes");
  if (!name) return "/notes";
  if (name.length > 500 || heading.length > 200 || /[\x00-\x1f\\<>|"?*]/.test(name + heading)
      || /^[A-Za-z]:/.test(name) || name.split("/").some((p) => !p || p === "." || p === "..")) return null;
  const query = new URLSearchParams(/\.md$/i.test(name) ? { path: name } : { open: name });
  if (heading) query.set("heading", heading);
  return "/notes?" + query.toString();
}

function mdLink(text, href) {
  const local = /^starlight:/i.test(href) ? mdStarlightHref(href) : null;
  if (local) {  // (in this tab: the Notes page opens it in place)
    const a = el("a", "note-link", text);
    a.href = local;
    return a;
  }
  const safe = mdSafeHref(href);
  if (!safe) return document.createTextNode(text);
  const a = el("a", "", text);
  a.href = safe; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}

// Inline text into a parent element; single line breaks stay line breaks.
function mdInline(parent, text) {
  let last = 0;
  const add = (plain) => {
    plain.split("\n").forEach((line, i) => {
      if (i) parent.appendChild(document.createElement("br"));
      if (line && mdHooks.text) mdHooks.text(parent, line);  // (tags)
      else if (line) parent.appendChild(document.createTextNode(line));
    });
  };
  for (const m of text.matchAll(MD_INLINE)) {
    add(text.slice(last, m.index));
    last = m.index + m[0].length;
    if (m[2] !== undefined) parent.appendChild(el("code", "", m[2].replace(/^ (.*) $/, "$1")));
    else if (m[3] !== undefined || m[4] !== undefined) mdInline(parent.appendChild(el("strong")), m[3] ?? m[4]);
    else if (m[5] !== undefined) mdInline(parent.appendChild(el("del")), m[5]);
    else if (m[6] !== undefined || m[7] !== undefined) mdInline(parent.appendChild(el("em")), m[6] ?? m[7]);
    else if (m[8] !== undefined) parent.appendChild(mdLink(m[8], m[9]));
    else if (m[10] !== undefined) parent.appendChild(mdLink(m[10], m[10]));
    else if (m[15] !== undefined) {
      if (mdHooks.wikilink) parent.appendChild(mdHooks.wikilink(m[15], m[14] === "!"));
      else add(m[0]);
    }
    else parent.appendChild(el("span", "math", mathText(m[11] ?? m[12] ?? m[13])));
  }
  add(text.slice(last));
}

function mdCells(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, "|"));
}

function mdCodeBlock(code, lang) {
  const box = el("div", "md-code");
  const bar = el("div", "md-code-bar", lang || "code");
  const copy = el("button", "icon", "Copy");
  copy.onclick = (event) => {
    event.stopPropagation();
    navigator.clipboard.writeText(code).then(() => toast("Copied"));
  };
  bar.appendChild(copy);
  const pre = el("pre");
  pre.appendChild(highlight(code, lang));
  box.append(bar, pre);
  return box;
}

// -- code colours: keywords, strings, numbers, comments, names called, variables -----------------
const HL_WORDS = {
  py: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case self",
  js: "async await break case catch class const continue default delete do else export extends false finally for function from if import in instanceof let new null of return static super switch this throw true try typeof undefined var void while yield",
  sh: "if then else elif fi for while do done case esac in function return local export exit set unset source",
  ps: "if else elseif foreach for while do switch function param return try catch finally throw begin process end in",
  sql: "select from where and or not insert into values update set delete create table drop alter join left right inner outer on group by order having limit as distinct null is like in between union all case when then else end primary key",
  c: "break case char const continue default do double else enum extern float for goto if int long return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected new delete this true false null nullptr namespace using template bool string var func fn let mut impl pub use mod match trait package import go defer chan interface type",
  json: "true false null",
  yaml: "true false null yes no",
};
const HL_LANG = {
  python: "py", py: "py", javascript: "js", js: "js", ts: "js", typescript: "js", jsx: "js", tsx: "js",
  json: "json", bash: "sh", sh: "sh", shell: "sh", zsh: "sh", console: "sh", powershell: "ps", ps1: "ps",
  pwsh: "ps", sql: "sql", c: "c", cpp: "c", "c++": "c", cs: "c", csharp: "c", java: "c", go: "c",
  rust: "c", rs: "c", kotlin: "c", swift: "c", php: "c", yaml: "yaml", yml: "yaml", toml: "yaml", ini: "yaml",
};
const HL_COMMENT = {
  py: /#[^\n]*/, sh: /#[^\n]*/, yaml: /#[^\n]*/, ps: /<#[\s\S]*?#>|#[^\n]*/, sql: /--[^\n]*/,
  json: /(?!)/, js: /\/\/[^\n]*|\/\*[\s\S]*?\*\//, c: /\/\/[^\n]*|\/\*[\s\S]*?\*\//,
};
const HL_STRING = /"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/;
const HL_TRIPLE = /"""[\s\S]*?"""|'''[\s\S]*?'''/;
const HL_TEMPLATE = /`(?:\\[\s\S]|[^`\\])*`/;
const HL_NUMBER = /\b(?:0x[\da-fA-F]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b/;
const HL_RULES = {};
function hlRule(family) {
  if (HL_RULES[family]) return HL_RULES[family];
  const strings = [family === "py" && HL_TRIPLE.source, family === "js" && HL_TEMPLATE.source, HL_STRING.source]
    .filter(Boolean).join("|");
  const vars = family === "sh" || family === "ps" ? /\$\{?[\w:]+\}?/.source : "(?!)";
  const parts = [HL_COMMENT[family].source, strings, vars, HL_NUMBER.source, /[A-Za-z_][\w-]*/.source];
  HL_RULES[family] = {
    re: new RegExp(parts.map(p => `(${p})`).join("|"), "g"),
    words: new Set(HL_WORDS[family].split(" ")),
    caseless: family === "sql" || family === "ps",
  };
  return HL_RULES[family];
}
// The code as a <code> element with coloured spans: plain for a language it does not know,
// and for very long code (which is drawn again and again while it streams in).
function highlight(code, lang) {
  const out = el("code");
  const family = HL_LANG[String(lang || "").toLowerCase()];
  if (!family || code.length > 20000) { out.textContent = code; return out; }
  const rule = hlRule(family);
  let last = 0;
  for (const m of code.matchAll(rule.re)) {
    let cls = m[1] ? "tok-c" : m[2] ? "tok-s" : m[3] ? "tok-v" : m[4] ? "tok-n" : "";
    if (m[5]) {
      const word = rule.caseless ? m[5].toLowerCase() : m[5];
      if (rule.words.has(word)) cls = "tok-k";
      else if (code[m.index + m[5].length] === "(") cls = "tok-f";
      else continue;  // an ordinary name: stays in the plain text
    }
    if (m.index > last) out.appendChild(document.createTextNode(code.slice(last, m.index)));
    out.appendChild(el("span", cls, m[0]));
    last = m.index + m[0].length;
  }
  if (last < code.length) out.appendChild(document.createTextNode(code.slice(last)));
  return out;
}

// -- maths: LaTeX shown as text with Unicode (x², α ≤ β, √(a+b), a/b) ----------------------------
const MATH_SYMBOLS = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ", eta: "η",
  theta: "θ", iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ",
  sigma: "σ", tau: "τ", phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω", Gamma: "Γ",
  Delta: "Δ", Theta: "Θ", Lambda: "Λ", Pi: "Π", Sigma: "Σ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
  times: "×", cdot: "·", div: "÷", pm: "±", mp: "∓", leq: "≤", le: "≤", geq: "≥", ge: "≥",
  neq: "≠", ne: "≠", approx: "≈", equiv: "≡", sim: "∼", propto: "∝", infty: "∞", sum: "∑",
  prod: "∏", int: "∫", oint: "∮", partial: "∂", nabla: "∇", to: "→", rightarrow: "→",
  leftarrow: "←", Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔", iff: "⇔", implies: "⇒",
  mapsto: "↦", in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", supset: "⊃", cup: "∪", cap: "∩",
  forall: "∀", exists: "∃", emptyset: "∅", neg: "¬", land: "∧", lor: "∨", circ: "∘", degree: "°",
  ldots: "…", cdots: "⋯", dots: "…", angle: "∠", perp: "⊥", parallel: "∥", star: "⋆", sqrt: "√",
  lfloor: "⌊", rfloor: "⌋", lceil: "⌈", rceil: "⌉", langle: "⟨", rangle: "⟩", hbar: "ħ", ell: "ℓ",
  quad: "  ", qquad: "    ",
};
const MATH_SETS = { R: "ℝ", N: "ℕ", Z: "ℤ", Q: "ℚ", C: "ℂ" };
const SUP = Object.fromEntries([..."0123456789+-=()ni"].map((c, i) => [c, [..."⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"][i]]));
const SUB = Object.fromEntries([..."0123456789+-=()aeoxijnmk"].map((c, i) => [c, [..."₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓᵢⱼₙₘₖ"][i]]));
// x^{12} -> x¹²; what has no small form keeps a mark: x^{a+b} -> x^(a+b)
function mathScript(text, table, mark) {
  const plain = text.replace(/\s+/g, "");
  if (plain && [...plain].every(c => table[c])) return [...plain].map(c => table[c]).join("");
  return mark + ([...plain].length > 1 ? "(" + text + ")" : text);
}
function mathText(tex) {
  let t = String(tex).trim();
  const group = (inner) => (/^[\w.]+$/.test(inner) ? inner : "(" + inner + ")");
  for (let i = 0; i < 20; i++) {  // the innermost braces first: \frac{\sqrt{2}}{2}
    const before = t;
    t = t.replace(/\\(?:text|mathrm|mathbf|mathit|operatorname|textbf)\{([^{}]*)\}/g, "$1")
      .replace(/\\mathbb\{([A-Z])\}/g, (all, c) => MATH_SETS[c] || c)
      .replace(/\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}/g, (all, a, b) => group(a) + "/" + group(b))
      .replace(/\\sqrt\{([^{}]*)\}/g, (all, a) => "√" + group(a))
      .replace(/\^\{([^{}]*)\}/g, (all, a) => mathScript(a, SUP, "^"))
      .replace(/_\{([^{}]*)\}/g, (all, a) => mathScript(a, SUB, "_"));
    if (t === before) break;
  }
  return t.replace(/\\left|\\right|\\big|\\Big/g, "")
    .replace(/\\([A-Za-z]+)/g, (all, name) => MATH_SYMBOLS[name] ?? name)
    .replace(/\^([\w+-])/g, (all, c) => mathScript(c, SUP, "^"))
    .replace(/_(\w)/g, (all, c) => mathScript(c, SUB, "_"))
    .replace(/\\[,;:! ]/g, " ").replace(/\\\\/g, "\n").replace(/[{}]/g, "");
}

// The blocks of a text into a parent element.
function mdBlocks(parent, lines) {
  let i = 0;
  const blank = (line) => !line.trim();
  const starts = (line) => MD_FENCE.test(line) || MD_HEADING.test(line) || MD_RULE.test(line)
    || MD_QUOTE.test(line) || MD_ITEM.test(line) || MD_MATH_BLOCK.test(line);
  while (i < lines.length) {
    const line = lines[i];
    if (blank(line)) { i++; continue; }
    let m;
    if ((m = line.match(MD_FENCE))) {
      const close = new RegExp(`^\\s{0,3}\\${m[1][0]}{${m[1].length},}\\s*$`), code = [];
      i++;
      while (i < lines.length && !close.test(lines[i])) { code.push(lines[i]); i++; }
      i++;  // the closing fence (still missing while the answer streams in)
      const special = mdHooks.code && mdHooks.code(m[2], code.join("\n"));  // (a query table)
      parent.appendChild(special || mdCodeBlock(code.join("\n"), m[2]));
    } else if ((m = line.match(MD_MATH_BLOCK))) {
      const tex = [m[2]];
      i++;
      if (!m[3]) {  // over several lines, up to a closing $$ or \]
        while (i < lines.length) {
          const end = lines[i].match(/^(.*?)\s*(\$\$|\\\])\s*$/);
          if (end) { tex.push(end[1]); break; }
          tex.push(lines[i]); i++;
        }
        i++;  // the closing line (still missing while the answer streams in)
      }
      parent.appendChild(el("div", "math math-block", mathText(tex.join(" "))));
    } else if ((m = line.match(MD_HEADING))) {
      mdInline(parent.appendChild(el("h" + Math.min(m[1].length + 2, 6), "md-h")), m[2]);
      i++;
    } else if (MD_RULE.test(line)) {
      parent.appendChild(el("hr")); i++;
    } else if (MD_QUOTE.test(line)) {
      const quoted = [];
      while (i < lines.length && (m = lines[i].match(MD_QUOTE))) { quoted.push(m[1]); i++; }
      if (!(mdHooks.quote && mdHooks.quote(parent, quoted)))  // (a callout: > [!tip])
        mdBlocks(parent.appendChild(el("blockquote")), quoted);
    } else if ((m = line.match(MD_ITEM))) {
      const indent = m[1].length, ordered = /\d/.test(m[2]);
      const list = el(ordered ? "ol" : "ul");
      if (ordered && parseInt(m[2], 10) !== 1) list.start = parseInt(m[2], 10);
      while (i < lines.length) {
        if (blank(lines[i])) {  // a blank line between items: the list goes on
          let j = i;
          while (j < lines.length && blank(lines[j])) j++;
          const next = j < lines.length && lines[j].match(MD_ITEM);
          if (next && next[1].length === indent && /\d/.test(next[2]) === ordered) { i = j; continue; }
          break;
        }
        const item = lines[i].match(MD_ITEM);
        if (!item || item[1].length !== indent || /\d/.test(item[2]) !== ordered) break;
        const content = [item[3]];
        i++;
        // its own further lines: indented more than the marker, or a lazy continuation
        while (i < lines.length) {
          const next = lines[i];
          const sub = next.match(MD_ITEM);
          if (sub && sub[1].length <= indent) break;
          if (blank(next)) {
            const after = lines[i + 1];
            if (after === undefined || after.match(/^\s*/)[0].length <= indent) break;
          } else if (!sub && next.match(/^\s*/)[0].length <= indent && starts(next)) break;
          content.push(next.slice(Math.min(next.match(/^\s*/)[0].length, indent + 2)));
          i++;
        }
        const li = list.appendChild(el("li"));
        if (mdHooks.item) content[0] = mdHooks.item(li, content[0]);  // (a task box: [ ] milk)
        if (content.length === 1) mdInline(li, content[0]);
        else mdBlocks(li, content);
      }
      parent.appendChild(list);
    } else if (line.includes("|") && i + 1 < lines.length && MD_TABLE_RULE.test(lines[i + 1])) {
      const table = el("table", "md-table");
      // ":--" left, "--:" right, ":-:" centred
      const align = mdCells(lines[i + 1]).map(rule =>
        rule.endsWith(":") ? (rule.startsWith(":") ? "center" : "right") : "");
      const fill = (cellEl, text, n) => {
        if (align[n]) cellEl.style.textAlign = align[n];
        mdInline(cellEl, text);
      };
      const head = table.createTHead().insertRow();
      mdCells(line).forEach((cell, n) => fill(head.appendChild(el("th")), cell, n));
      i += 2;
      const rows = table.createTBody();
      while (i < lines.length && lines[i].includes("|") && !blank(lines[i])) {
        const row = rows.insertRow();
        mdCells(lines[i]).forEach((cell, n) => fill(row.insertCell(), cell, n));
        i++;
      }
      const scroller = el("div", "md-table-wrap");
      scroller.appendChild(table);
      parent.appendChild(scroller);
    } else {
      const text = [line];
      i++;
      while (i < lines.length && !blank(lines[i]) && !starts(lines[i])
             && !(lines[i].includes("|") && MD_TABLE_RULE.test(lines[i + 1] || ""))) {
        text.push(lines[i]); i++;
      }
      mdInline(parent.appendChild(el("p")), text.join("\n"));
    }
  }
}

// Draw a Markdown text into an element, replacing what it showed.
function renderMarkdown(target, text) {
  target.replaceChildren();
  if (text) mdBlocks(target, text.replace(/\r\n?/g, "\n").split("\n"));
}

// What to read aloud: the words without the Markdown around them, and no code.
function markdownToSpeech(text) {
  return text
    .replace(/^\s{0,3}(`{3,}|~{3,})[\s\S]*?(^\s{0,3}\1\s*$|(?![\s\S]))/gm, " (code) ")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*([-*+]|\d{1,9}[.)])\s+/gm, "")
    .replace(/^\s{0,3}>\s?/gm, "")
    .replace(/^\s*\[[ xX/-]\]\s+/gm, "")
    .replace(/\[!\w+\][+-]?\s*/g, "")
    .replace(/!\[\[[^\]\n]*\]\]/g, "")
    .replace(/\[\[(?:[^\]|\n]*\|)?([^\]\n]+)\]\]/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(?:https?|starlight):\/\/[^\s<>]*[^\s<>().,;:!?'"]/g, "a link")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(?<![\w*])\*(?=\S)([^*\n]+?)\*/g, "$1")
    .replace(/^\s*\|?\s*:?-{2,}[-|: ]*$/gm, "")
    .replace(/^\s*\|\s*|\s*\|\s*$/gm, "")
    .replace(/\s*\|\s*/g, ", ");
}
