// Copies the heading font into starlight/web/static/vendor, and next to it:
// fonts.sha256 (a test checks the shipped files against it) and space-grotesk-LICENSE.txt.
// Run: npm ci, then node build.mjs. Only the latin and latin-ext files are used.
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const vendor = join(here, "..", "..", "starlight", "web", "static", "vendor");
const pkg = join(here, "node_modules", "@fontsource-variable", "space-grotesk");
mkdirSync(vendor, { recursive: true });

const files = {
  "space-grotesk-latin.woff2": "files/space-grotesk-latin-wght-normal.woff2",
  "space-grotesk-latin-ext.woff2": "files/space-grotesk-latin-ext-wght-normal.woff2",
};
const lines = [];
for (const [name, source] of Object.entries(files)) {
  copyFileSync(join(pkg, source), join(vendor, name));
  const digest = createHash("sha256").update(readFileSync(join(vendor, name))).digest("hex");
  lines.push(`${digest}  ${name}`);
}
writeFileSync(join(vendor, "fonts.sha256"), lines.join("\n") + "\n");

const info = JSON.parse(readFileSync(join(pkg, "package.json"), "utf8"));
const licence = readFileSync(join(pkg, "LICENSE"), "utf8").replace(/\r\n/g, "\n").trim();
writeFileSync(
  join(vendor, "space-grotesk-LICENSE.txt"),
  "Space Grotesk, the heading font (built by bundles/fonts/build.mjs).\n" +
    `Package: @fontsource-variable/space-grotesk ${info.version} (${info.license})\n\n` +
    `${"=".repeat(78)}\n${licence}\n`,
);
console.log(`fonts: ${lines.length} files, @fontsource-variable/space-grotesk ${info.version}`);
