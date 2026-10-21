// Renders assets/src/banner.html -> assets/banner.png (1280x640) and assets/src/logo.html -> assets/logo.png
// (600x600, transparent) with Playwright's Chromium.
//   PW=<path to playwright> node tools/render-banner.mjs
import { createRequire } from "module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW || "playwright");
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const b = await chromium.launch();
for (const [name, w, h, transparent] of [["banner", 1280, 640, false], ["logo", 600, 600, true], ["download", 520, 120, true]]) {
  const p = await b.newPage({ viewport: { width: w, height: h } });
  await p.goto("file://" + join(root, `assets/src/${name}.html`), { waitUntil: "networkidle" });
  await p.evaluate(() => document.fonts.ready);
  const file = name === "download" ? "download-button" : name;
  await p.screenshot({ path: join(root, `assets/${file}.png`), omitBackground: transparent });
  console.log(`assets/${file}.png`);
}
await b.close();
