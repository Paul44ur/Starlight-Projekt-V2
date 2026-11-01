// What the Notes page uses from CodeMirror 6, bundled into one file:
// starlight/web/static/vendor/editor.js. Rebuild with build.mjs (see README.md).
export * from "@codemirror/state";
export * from "@codemirror/view";
export * from "@codemirror/commands";
export * from "@codemirror/language";
export * from "@codemirror/autocomplete";
export * from "@codemirror/search";
export {
  markdown,
  markdownLanguage,
  markdownKeymap,
  insertNewlineContinueMarkup,
  deleteMarkupBackward,
} from "@codemirror/lang-markdown";
export { tags, Tag, styleTags } from "@lezer/highlight";
export { NodeProp } from "@lezer/common";
export { GFM, Table, TaskList, Strikethrough } from "@lezer/markdown";
