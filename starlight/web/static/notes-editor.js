// The Notes page's editor (docs/plan-notes-app.md, block B6): CodeMirror 6 from the shipped
// bundle (/vendor/editor.js), set up so writing a note needs no Markdown knowledge:
// headings show big, bold shows bold, links are coloured, the marks (#, **, -) are dimmed,
// [[links]] and #tags stand out, a toolbar and a "/" menu insert what you need, and
// Ctrl+Enter ticks a task. Loaded only when you edit (notes.html imports it then).

import * as cm from "/vendor/editor.js";

const look = cm.EditorView.theme({
  "&": { color: "var(--text)", backgroundColor: "transparent", fontSize: "16.5px" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "inherit", lineHeight: "1.7", overflow: "visible" },
  ".cm-content": { padding: "6px 0 120px", caretColor: "var(--text)", minHeight: "50vh" },
  ".cm-line": { padding: "0" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text)", borderLeftWidth: "2px" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "color-mix(in srgb, var(--accent) 28%, transparent)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--text) 4%, transparent)" },
  ".cm-placeholder": { color: "var(--dim)" },
  ".cm-wikilink": { color: "var(--accent)" },
  ".cm-tag": { color: "var(--accent)", backgroundColor: "color-mix(in srgb, var(--accent) 13%, transparent)",
               borderRadius: "999px", padding: "0 4px" },
  ".cm-tooltip": { backgroundColor: "var(--panel)", border: "1px solid var(--border)", borderRadius: "8px",
                   boxShadow: "var(--shadow)", color: "var(--text)" },
  ".cm-tooltip.cm-tooltip-autocomplete > ul": { fontFamily: "inherit", maxHeight: "18em" },
  ".cm-tooltip-autocomplete > ul > li": { padding: "4px 10px !important", lineHeight: "1.5" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "color-mix(in srgb, var(--accent) 22%, transparent) !important", color: "var(--text) !important" },
  ".cm-completionDetail": { color: "var(--dim)", fontStyle: "normal", marginLeft: "10px" },
  ".cm-completionIcon": { display: "none" },
  ".cm-panels": { backgroundColor: "var(--panel)", color: "var(--text)", borderColor: "var(--border)" },
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--warn) 35%, transparent)" },
});

// how the Markdown looks while you write it
const styled = cm.HighlightStyle.define([
  { tag: cm.tags.heading1, fontSize: "1.55em", fontWeight: "700" },
  { tag: cm.tags.heading2, fontSize: "1.3em", fontWeight: "700" },
  { tag: cm.tags.heading3, fontSize: "1.12em", fontWeight: "700" },
  { tag: [cm.tags.heading4, cm.tags.heading5, cm.tags.heading6], fontWeight: "700" },
  { tag: cm.tags.strong, fontWeight: "700" },
  { tag: cm.tags.emphasis, fontStyle: "italic" },
  { tag: cm.tags.strikethrough, textDecoration: "line-through" },
  { tag: cm.tags.link, color: "var(--accent)" },
  { tag: cm.tags.url, color: "var(--accent)", textDecoration: "underline" },
  { tag: [cm.tags.processingInstruction, cm.tags.meta, cm.tags.contentSeparator], color: "var(--dim)" },
  { tag: cm.tags.monospace, fontFamily: "ui-monospace, Consolas, 'Cascadia Mono', monospace", fontSize: ".9em" },
  { tag: cm.tags.quote, color: "var(--dim)" },
  { tag: cm.tags.atom, color: "var(--ok)" },
  { tag: cm.tags.comment, color: "var(--hl-c)", fontStyle: "italic" },
]);

// The properties block at the top (--- key: value ---) is not text: without this, Markdown
// would read "key: value" above "---" as a heading. Shown dimmed, as a whole. (It reads the
// document through the parser's input to be sure the block is closed.)
const PROPERTIES_CLOSED = /^---[ \t]*\r?\n(?:[\s\S]*?\r?\n)?(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/;
const properties = {
  defineNodes: [{ name: "Properties", block: true, style: cm.tags.meta }],
  parseBlock: [{
    name: "Properties",
    before: "HorizontalRule",
    parse(cx, line) {
      if (cx.lineStart !== 0 || !/^---[ \t]*$/.test(line.text)) return false;
      const input = cx.input;
      const head = input && typeof input.read === "function" ? input.read(0, Math.min(input.length, 65536)) : "";
      if (!PROPERTIES_CLOSED.test(head)) return false;
      const start = cx.lineStart;
      while (cx.nextLine()) {
        if (/^(---|\.\.\.)[ \t]*$/.test(line.text)) {
          const end = cx.lineStart + line.text.length;
          cx.nextLine();
          cx.addElement(cx.elt("Properties", start, end));
          return true;
        }
      }
      cx.addElement(cx.elt("Properties", start, cx.prevLineEnd()));
      return true;
    },
  }],
};

// [[links]] and #tags stand out (as on the page)
function marks(pattern, cls) {
  const decorator = new cm.MatchDecorator({ regexp: pattern, decoration: cm.Decoration.mark({ class: cls }) });
  return cm.ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = decorator.createDeco(view); }
    update(update) { this.decorations = decorator.updateDeco(update, this.decorations); }
  }, { decorations: (plugin) => plugin.decorations });
}

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// -- changing the text: what the toolbar, the "/" menu and the keys do --------------------

function wrap(view, mark) {  // **bold**, *italic*: around the selection, or a place to type
  view.dispatch(view.state.changeByRange((range) => {
    const text = view.state.sliceDoc(range.from, range.to);
    if (text.startsWith(mark) && text.endsWith(mark) && text.length >= 2 * mark.length) {
      const inner = text.slice(mark.length, -mark.length);
      return { changes: { from: range.from, to: range.to, insert: inner },
               range: cm.EditorSelection.range(range.from, range.from + inner.length) };
    }
    return { changes: [{ from: range.from, insert: mark }, { from: range.to, insert: mark }],
             range: cm.EditorSelection.range(range.from + mark.length, range.to + mark.length) };
  }));
  view.focus();
  return true;
}

function lines(view) {  // the lines the selection touches
  const seen = new Set(), found = [];
  for (const range of view.state.selection.ranges) {
    for (let at = range.from; at <= range.to;) {
      const line = view.state.doc.lineAt(at);
      if (!seen.has(line.number)) { seen.add(line.number); found.push(line); }
      at = line.to + 1;
    }
  }
  return found;
}

const LIST = /^(\s*)([-*+]|\d+[.)]) (\[.\] )?/;
function prefix(view, kind) {  // "list" or "task" on each selected line (again: off)
  const all = lines(view);
  const isTask = (text) => /^\s*[-*+] \[.\] /.test(text);
  const isList = (text) => LIST.test(text) && !isTask(text);
  const on = all.every((line) => (kind === "task" ? isTask(line.text) : isList(line.text)));
  view.dispatch({ changes: all.map((line) => {
    const m = line.text.match(LIST);
    const indent = m ? m[1] : (line.text.match(/^\s*/) || [""])[0];
    const old = m ? m[0].length : indent.length;
    const insert = on ? indent : indent + (kind === "task" ? "- [ ] " : "- ");
    return { from: line.from, to: line.from + old, insert };
  }) });
  view.focus();
  return true;
}

function heading(view) {  // none -> # -> ## -> ### -> none
  view.dispatch({ changes: lines(view).map((line) => {
    const m = line.text.match(/^(#{1,6}) /);
    const level = m ? m[1].length : 0;
    const next = level >= 3 ? "" : "#".repeat(level + 1) + " ";
    return { from: line.from, to: line.from + (m ? m[0].length : 0), insert: next };
  }) });
  view.focus();
  return true;
}

function toggleTask(view) {  // Ctrl+Enter: [ ] <-> [x] on the task lines
  const changes = [];
  for (const line of lines(view)) {
    const m = line.text.match(/^(\s*[-*+] \[)(.)(\])/);
    if (m) changes.push({ from: line.from + m[1].length, to: line.from + m[1].length + 1, insert: m[2] === " " ? "x" : " " });
  }
  if (!changes.length) return false;
  view.dispatch({ changes });
  return true;
}

function insert(view, text, cursor = text.length) {  // at the cursor, replacing the selection
  const range = view.state.selection.main;
  view.dispatch({ changes: { from: range.from, to: range.to, insert: text },
                  selection: { anchor: range.from + cursor } });
  view.focus();
  return true;
}

function link(view) {  // [[the selected words]], or [[ ]] to type a note's name into
  const range = view.state.selection.main;
  const text = view.state.sliceDoc(range.from, range.to);
  return insert(view, "[[" + text + "]]", text ? text.length + 4 : 2);
}

// -- the "/" menu: typed at the start of a line or after a space ----------------------------

function slashMenu(options) {
  const say = options.say || ((text) => text);
  const items = [
    ["heading", "Heading", "# ", 2], ["subheading", "Subheading", "## ", 3],
    ["task", "Task", "- [ ] ", 6], ["list", "Bullet list", "- ", 2],
    ["table", "Table", "| Column | Column |\n| --- | --- |\n|  |  |\n", 2],
    ["link", "Link to a note", "[[]]", 2], ["quote", "Callout", "> [!tip] \n> ", 9],
    ["date", "Today's date", null, 0], ["picture", "Picture", null, 0],
    ["template", "Template", null, 0],
  ];
  return (context) => {
    const before = context.matchBefore(/(?:^|\s)\/[\p{L}']*$/u);
    if (!before) return null;
    const slash = before.text.startsWith("/") ? before.from : before.from + 1;
    return {
      from: slash + 1,  // what is typed after the "/" picks the item (in the page's language)
      filter: true,
      options: items.map(([word, label, text, cursor], number) => ({
        label: say(label),
        detail: "/" + word,
        boost: items.length - number,  // (in this order, not A to Z)
        apply: (view, completion, after, to) => {
          const from = after - 1;  // (the "/" goes too)
          if (word === "date") text = today();
          if (word === "picture") {
            view.dispatch({ changes: { from, to, insert: "" } });
            if (options.pickPicture) options.pickPicture();
            return;
          }
          if (word === "template") {
            view.dispatch({ changes: { from, to, insert: "" } });
            if (options.notice) options.notice(say("Templates come in a later update."));
            return;
          }
          view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + (cursor || text.length) } });
        },
      })),
    };
  };
}

// [[ suggests the notes (their names and other names) to link to.
function linkMenu(options) {
  return (context) => {
    const before = context.matchBefore(/\[\[[^\][|#\n]*$/);
    if (!before || !options.noteNames) return null;
    return {
      from: before.from + 2,
      filter: true,
      options: options.noteNames().map(({ name, detail }) => ({
        label: name,
        detail,
        apply: (view, completion, from, to) => {
          const closed = view.state.sliceDoc(to, to + 2) === "]]";
          view.dispatch({ changes: { from, to, insert: name + (closed ? "" : "]]") },
                          selection: { anchor: from + name.length + 2 } });
        },
      })),
    };
  };
}

// #ta... suggests the tags already in use; at the start of a line in the properties block,
// the property names already in use.
function tagMenu(options) {
  return (context) => {
    const before = context.matchBefore(/(?:^|[\s(])#[\p{L}\p{N}_\-/]+$/u);
    if (!before || !options.tagNames) return null;
    const hash = before.text.indexOf("#");
    return {
      from: before.from + hash + 1,
      filter: true,
      options: options.tagNames().map(({ name, count }) => ({ label: name, detail: String(count) })),
    };
  };
}

function propertyMenu(options) {
  return (context) => {
    if (!options.propertyNames) return null;
    const head = context.state.sliceDoc(0, Math.min(context.state.doc.length, 65536));
    const block = head.match(PROPERTIES_CLOSED);
    const line = context.state.doc.lineAt(context.pos);
    if (!block || line.number === 1 || line.to >= block[0].trimEnd().length - 3) return null;
    const before = context.matchBefore(/^[\p{L}\p{N}_\-]*$/u);
    if (!before || (!context.explicit && before.from === before.to)) return null;
    return {
      from: before.from,
      filter: true,
      options: options.propertyNames().map(({ name, count }) => ({
        label: name, detail: String(count), apply: name + ": ",
      })),
    };
  };
}

// The editor in ``parent``. options: text, onChange(text), pictures(files, at) (paste and
// drop), pickPicture(), notice(text), say(text) (the page's translation), placeholder,
// noteNames() ([{name, detail}] for the [[ suggestions), tagNames() and propertyNames()
// ([{name, count}] for # and for property names).
export function createEditor(parent, options) {
  const view = new cm.EditorView({
    parent,
    state: cm.EditorState.create({
      doc: options.text || "",
      extensions: [
        cm.history(),
        cm.drawSelection(),
        cm.dropCursor(),
        cm.highlightActiveLine(),
        cm.EditorView.lineWrapping,
        cm.markdown({ base: cm.markdownLanguage, extensions: [properties] }),
        cm.syntaxHighlighting(styled),
        marks(/!?\[\[[^\[\]\n]+\]\]/g, "cm-wikilink"),
        marks(/(?<![\p{L}\p{N}_#&/\\])#[\p{L}\p{N}_\-/]*[\p{L}_][\p{L}\p{N}_\-/]*/gu, "cm-tag"),
        cm.autocompletion({
          override: [slashMenu(options), linkMenu(options), tagMenu(options), propertyMenu(options)],
          icons: false,
        }),
        cm.highlightSelectionMatches(),
        cm.placeholder(options.placeholder || ""),
        cm.keymap.of([
          { key: "Mod-b", run: (v) => wrap(v, "**") },
          { key: "Mod-i", run: (v) => wrap(v, "*") },
          { key: "Mod-Enter", run: toggleTask },
          ...cm.completionKeymap,
          ...cm.markdownKeymap,
          ...cm.searchKeymap,
          ...cm.historyKeymap,
          ...cm.defaultKeymap,
          cm.indentWithTab,
        ]),
        look,
        cm.EditorView.updateListener.of((update) => {
          if (update.docChanged && options.onChange) options.onChange(update.state.doc.toString());
        }),
        cm.EditorView.domEventHandlers({
          paste(event, v) {
            const files = [...(event.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
            if (!files.length || !options.pictures) return false;
            event.preventDefault();
            options.pictures(files, v.state.selection.main.head);
            return true;
          },
          drop(event, v) {
            const files = [...(event.dataTransfer?.files || [])].filter((f) => f.type.startsWith("image/"));
            if (!files.length || !options.pictures) return false;
            event.preventDefault();
            const at = v.posAtCoords({ x: event.clientX, y: event.clientY }) ?? v.state.selection.main.head;
            options.pictures(files, at);
            return true;
          },
        }),
      ],
    }),
  });
  return {
    view,
    text: () => view.state.doc.toString(),
    setText(text) {  // (another version: undo can still go back to what was there)
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
    },
    insertAt(at, text) {
      view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length } });
      view.focus();
    },
    focus: () => view.focus(),
    run(name) {
      const commands = {
        bold: () => wrap(view, "**"), italic: () => wrap(view, "*"), heading: () => heading(view),
        list: () => prefix(view, "list"), task: () => prefix(view, "task"), link: () => link(view),
        toggle: () => toggleTask(view), undo: () => cm.undo(view), redo: () => cm.redo(view),
      };
      return commands[name] ? commands[name]() : false;
    },
    destroy: () => view.destroy(),
  };
}
