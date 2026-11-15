// Takes the README's Quick Access screenshots from the browser preview (example data): docs/img/<name>.png.
//   pnpm preview && node tools/readme-shots.mjs
// Each shot picks one of Quick Access's tabs by clicking it, the way LB / RB switch them on the Claw.
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
// the whole panel in one picture (on the Claw it scrolls): the tab pane drops its fixed height too
const FULL = ".qam-tabpanel, .qam-device .qam-tabpanel { max-height: none !important; overflow: visible !important; }"
  + " .clawdeck-qam-tabs { height: auto !important; } .qam-tabs { position: static !important; overflow: visible !important; }";
const open = async (hash = "") => {
  await p.goto("file://" + page + hash, { waitUntil: "networkidle" });
  await p.evaluate(() => document.fonts.ready);
  // the whole panel in one picture: on the Claw it scrolls
  await p.addStyleTag({ content: FULL });
  await p.waitForTimeout(900);
};
// a tab (titles from src/pages.tsx)
const tab = async (title) => {
  await p.locator(".qam .qam-tab", { hasText: new RegExp(`^${title}$`) }).first().click();
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
  // the Game tab with a game picked from its list (the panel shows a game once it runs or you pick it)
  await tab("Game");
  await p.locator(".qam .qam-select").first().click();
  await p.waitForTimeout(500);
  await p.locator(".qam .qam-menu-item", { hasText: "CONTROL Resonant" }).first().click();
  await p.waitForTimeout(1200);
  await shot("game");
}
if (want("power")) {
  await tab("Power");
  await shot("power");
}
if (want("fixes")) {
  await tab("Fixes");
  await shot("fixes");
}
if (want("game-tags")) {
  // the game list: the Game tab's first dropdown, opened
  await tab("Game");
  await p.locator(".qam .qam-select").first().click();
  await p.waitForTimeout(700);
  // the open list hangs below the panel: the picture runs to the list's end
  const qam = await p.locator(".qam").boundingBox();
  const menu = await p.locator(".qam .qam-menu").first().boundingBox();
  await p.screenshot({ path: join(out, "game-tags.png"),
    clip: { x: qam.x, y: qam.y, width: qam.width, height: Math.max(qam.height, menu.y + menu.height + 16 - qam.y) } });
  console.log("docs/img/game-tags.png");
  await p.keyboard.press("Escape");
}
if (want("first-run")) {
  await open("#firstrun");          // the example backend as on a fresh install (preview/backend.ts)
  await p.reload({ waitUntil: "networkidle" });   // a hash-only change keeps the page: load it again
  await p.addStyleTag({ content: FULL });
  await p.waitForTimeout(900);
  // just the steps: the tabs under them are the other shots
  const box = await p.locator(".qam").boundingBox();
  const end = await p.locator(".qam .qam-tab").first().boundingBox();
  await p.screenshot({ path: join(out, "first-run.png"), clip: { x: box.x, y: box.y, width: box.width, height: end.y - box.y - 4 } });
  console.log("docs/img/first-run.png");
}
await b.close();
