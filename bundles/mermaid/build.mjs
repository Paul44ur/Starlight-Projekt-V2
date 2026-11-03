// Builds starlight/web/static/vendor/mermaid.js from entry.js, and next to it:
// mermaid.sha256 (a test checks the shipped file against it) and mermaid-LICENSES.txt (the
// licence of every package that ended up in the bundle). Run: npm ci, then node build.mjs.
// elkjs is replaced by elk-stub.js (see there).
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const vendor = join(here, "..", "..", "starlight", "web", "static", "vendor");
mkdirSync(vendor, { recursive: true });

const noElk = {
  name: "no-elk",
  setup(b) {
    b.onResolve({ filter: /^elkjs(\/|$)/ }, () => ({ path: join(here, "elk-stub.js") }));
  },
};

const result = await build({
  entryPoints: [join(here, "entry.js")],
  bundle: true,
  minify: true,
  format: "esm",
  target: "es2020",
  legalComments: "none", // the licences go to mermaid-LICENSES.txt instead
  charset: "utf8",
  metafile: true,
  write: false,
  logLevel: "warning",
  plugins: [noElk],
});
const code = result.outputFiles[0].contents;
writeFileSync(join(vendor, "mermaid.js"), code);
const digest = createHash("sha256").update(code).digest("hex");
writeFileSync(join(vendor, "mermaid.sha256"), `${digest}  mermaid.js\n`);

// Which packages are inside: every input file under node_modules/<package>/.
const packages = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  const match = input.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//);
  if (match) packages.add(match[1]);
}
const parts = [
  "Third-party code in mermaid.js (built by bundles/mermaid/build.mjs).\n" +
    "Each package's name, version and licence follow.\n",
];
for (const name of [...packages].sort()) {
  const folder = join(here, "node_modules", ...name.split("/"));
  const info = JSON.parse(readFileSync(join(folder, "package.json"), "utf8"));
  const file = ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENSE-MIT", "license", "license.md", "license.txt"]
    .map((f) => join(folder, f))
    .find((f) => existsSync(f));
  if (!file) throw new Error(`${name} has no licence file`);
  const text = readFileSync(file, "utf8").replace(/\r\n/g, "\n").trim();
  // (a package without a licence field: its licence text says which one it is)
  const kind = info.license || (/^(The )?MIT License/i.test(text) ? "MIT" : null);
  if (!kind) throw new Error(`${name}: which licence?`);
  parts.push(`${"=".repeat(78)}\n${name} ${info.version} (${kind})\n\n${text}\n`);
}
writeFileSync(join(vendor, "mermaid-LICENSES.txt"), parts.join("\n"));
console.log(`mermaid.js: ${code.length} bytes, sha256 ${digest}, ${packages.size} packages`);
