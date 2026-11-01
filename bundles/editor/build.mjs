// Builds starlight/web/static/vendor/editor.js from entry.js, and next to it:
// editor.sha256 (a test checks the shipped file against it) and LICENSES.txt (the licence
// of every package that ended up in the bundle). Run: npm ci, then node build.mjs.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const vendor = join(here, "..", "..", "starlight", "web", "static", "vendor");
mkdirSync(vendor, { recursive: true });

const result = await build({
  entryPoints: [join(here, "entry.js")],
  bundle: true,
  minify: true,
  format: "esm",
  target: "es2020",
  legalComments: "none", // the licences go to LICENSES.txt instead
  charset: "utf8",
  metafile: true,
  write: false,
  logLevel: "warning",
});
const code = result.outputFiles[0].contents;
writeFileSync(join(vendor, "editor.js"), code);
const digest = createHash("sha256").update(code).digest("hex");
writeFileSync(join(vendor, "editor.sha256"), `${digest}  editor.js\n`);

// Which packages are inside: every input file under node_modules/<package>/.
const packages = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  const match = input.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//);
  if (match) packages.add(match[1]);
}
const parts = [
  "Third-party code in editor.js (built by bundles/editor/build.mjs).\n" +
    "Each package's name, version and licence follow.\n",
];
for (const name of [...packages].sort()) {
  const folder = join(here, "node_modules", ...name.split("/"));
  const info = JSON.parse(readFileSync(join(folder, "package.json"), "utf8"));
  const file = ["LICENSE", "LICENSE.md", "LICENSE.txt", "license"]
    .map((f) => join(folder, f))
    .find((f) => existsSync(f));
  if (!file) throw new Error(`${name} has no licence file`);
  const text = readFileSync(file, "utf8").replace(/\r\n/g, "\n").trim();
  parts.push(`${"=".repeat(78)}\n${name} ${info.version} (${info.license})\n\n${text}\n`);
}
writeFileSync(join(vendor, "LICENSES.txt"), parts.join("\n"));
console.log(`editor.js: ${code.length} bytes, sha256 ${digest}, ${packages.size} packages`);
