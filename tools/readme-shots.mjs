// Takes the README's Quick Access screenshots from the browser preview (example data): docs/img/<name>.png.
//   pnpm preview && node tools/readme-shots.mjs
// Each shot opens one folded section of Quick Access by clicking its heading, the way you would on the Claw.
import { createRequire } from "module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW || "playwright");
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = join(tmpdir(), "clawdeck-readme-page.html");
writeFileSync(page, '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + readFileSync(join(root, "preview/dist/ClawDeck-preview.html"), "utf8") + "</html>");
const out = join(root, "docs/img");
mkdirSync(out, { recursive: true });
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 3000 }, deviceScaleFactor: 2 });
if (process.env.DEBUG) p.on("console", (m) => console.log("console:", m.text()));
const open = async (hash = "") => {
  await p.goto("file://" + page + hash, { waitUntil: "networkidle" });
  await p.evaluate(() => document.fonts.ready);
  // the whole panel in one picture: on the Claw it scrolls
  await p.addStyleTag({ content: ".qam-tabpanel, .qam-device .qam-tabpanel { max-height: none !important; overflow: visible !important; }" });
  await p.waitForTimeout(900);
};
// a fold heading: click to open or close it (titles from src/pages.tsx)
const fold = async (title) => {
  await p.locator(".qam span", { hasText: new RegExp(`^[▸▾]${title}$`) }).first().click();
  await p.waitForTimeout(700);
};
const shot = async (name) => {
  await p.locator(".qam").screenshot({ path: join(out, `${name}.png`) });
  console.log(`docs/img/${name}.png`);
};
const only = process.argv.slice(2);
const want = (n) => !only.length || only.includes(n);

await open();
if (process.env.DEBUG) console.log(await p.locator(".qam").innerText());
if (want("game")) {
  // the example scenario has a game running: Upscaling and frame generation is the open section
  await shot("game");
}
if (want("power")) {
  await fold("Upscaling and frame generation");
  await fold("Power");
  await shot("power");
  await fold("Power");
}
if (want("fixes")) {
  await fold("Fixes");
  await shot("fixes");
}
if (want("first-run")) {
  await open("#firstrun");          // the example backend as on a fresh install (preview/backend.ts)
  await p.reload({ waitUntil: "networkidle" });   // a hash-only change keeps the page: load it again
  await p.addStyleTag({ content: ".qam-tabpanel, .qam-device .qam-tabpanel { max-height: none !important; overflow: visible !important; }" });
  await p.waitForTimeout(900);
  await shot("first-run");
}
await b.close();
