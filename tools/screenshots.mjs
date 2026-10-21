// Takes the README screenshots from the browser preview (example data), one per tab: docs/screenshots/<tab>.png.
//   pnpm preview && PW=<path to playwright> node tools/screenshots.mjs
import { createRequire } from "module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW || "playwright");
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = join(tmpdir(), "clawdeck-preview-page.html");
writeFileSync(page, '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + readFileSync(join(root, "preview/dist/ClawDeck-preview.html"), "utf8") + "</html>");
const out = join(root, "docs/screenshots");
mkdirSync(out, { recursive: true });
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 2 });
await p.goto("file://" + page, { waitUntil: "networkidle" });
await p.evaluate(() => document.fonts.ready);
const tabs = ["setup", "power", "graphics", "display", "controller", "battery"];
for (const [i, name] of tabs.entries()) {
  await p.click(`.qam-tab >> nth=${i}`);
  await p.waitForTimeout(700);
  await p.locator(".qam").screenshot({ path: join(out, `${name}.png`) });
  console.log(`docs/screenshots/${name}.png`);
}
await b.close();
