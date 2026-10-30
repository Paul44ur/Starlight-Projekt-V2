// Markdown in Starlight's answers (headings, lists, tables, code, bold, links), drawn as page
// elements. The model's text only ever becomes textContent, never HTML: an answer cannot run
// a script or load anything, and a link opens only if it is http(s) or mailto.

const MD_FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)[^`]*$/;
const MD_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const MD_RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const MD_QUOTE = /^\s{0,3}>\s?(.*)$/;
const MD_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const MD_TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
// `code`, **bold**, __bold__, ~~struck~~, *italic*, _italic_, [text](link), a bare link
const MD_INLINE = new RegExp([
  "(`+)([^`][\\s\\S]*?)\\1(?!`)",
  "\\*\\*(?=\\S)([\\s\\S]*?\\S)\\*\\*",
  "(?<![\\w])__(?=\\S)([\\s\\S]*?\\S)__(?![\\w])",
  "~~(?=\\S)([\\s\\S]*?\\S)~~",
  "\\*(?=[^\\s*])([\\s\\S]*?[^\\s*])\\*",
  "(?<![\\w])_(?=[^\\s_])([\\s\\S]*?[^\\s_])_(?![\\w])",
  "\\[([^\\]\\n]+)\\]\\(\\s*<?([^\\s()<>]+(?:\\([^\\s()]*\\))?)>?\\s*\\)",
  "(https?:\\/\\/[^\\s<>]*[^\\s<>()\\[\\].,;:!?'\"*_~])",
].join("|"), "g");

function mdSafeHref(href) {
  try {
    const url = new URL(href, location.href);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
  } catch (e) { return null; }
}

function mdLink(text, href) {
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
      if (line) parent.appendChild(document.createTextNode(line));
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
  pre.appendChild(el("code", "", code));
  box.append(bar, pre);
  return box;
}

// The blocks of a text into a parent element.
function mdBlocks(parent, lines) {
  let i = 0;
  const blank = (line) => !line.trim();
  const starts = (line) => MD_FENCE.test(line) || MD_HEADING.test(line) || MD_RULE.test(line)
    || MD_QUOTE.test(line) || MD_ITEM.test(line);
  while (i < lines.length) {
    const line = lines[i];
    if (blank(line)) { i++; continue; }
    let m;
    if ((m = line.match(MD_FENCE))) {
      const close = new RegExp(`^\\s{0,3}\\${m[1][0]}{${m[1].length},}\\s*$`), code = [];
      i++;
      while (i < lines.length && !close.test(lines[i])) { code.push(lines[i]); i++; }
      i++;  // the closing fence (still missing while the answer streams in)
      parent.appendChild(mdCodeBlock(code.join("\n"), m[2]));
    } else if ((m = line.match(MD_HEADING))) {
      mdInline(parent.appendChild(el("h" + Math.min(m[1].length + 2, 6), "md-h")), m[2]);
      i++;
    } else if (MD_RULE.test(line)) {
      parent.appendChild(el("hr")); i++;
    } else if (MD_QUOTE.test(line)) {
      const quoted = [];
      while (i < lines.length && (m = lines[i].match(MD_QUOTE))) { quoted.push(m[1]); i++; }
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
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/[^\s<>]*[^\s<>().,;:!?'"]/g, "a link")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(?<![\w*])\*(?=\S)([^*\n]+?)\*/g, "$1")
    .replace(/^\s*\|?\s*:?-{2,}[-|: ]*$/gm, "")
    .replace(/^\s*\|\s*|\s*\|\s*$/gm, "")
    .replace(/\s*\|\s*/g, ", ");
}
