// elkjs (the ELK layout: about 1.5 MB, under the EPL-2.0 licence) is left out of Starlight's
// mermaid.js. The Notes page sets the dagre layout instead (Mermaid 11's default; Mermaid 12
// would use ELK), so only a diagram that asks for "layout: elk" itself gets this message.
export default class ELK {
  layout() {
    return Promise.reject(
      new Error("Starlight's Mermaid has no ELK layout: leave out \"layout: elk\""),
    );
  }
}
