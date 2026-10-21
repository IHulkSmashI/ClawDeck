// Renders assets/src/banner.html to assets/banner.png (1280x640) with Playwright's Chromium.
//   PW=<path to playwright> node tools/render-banner.mjs
import { createRequire } from "module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW || "playwright");
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 640 } });
await p.goto("file://" + join(root, "assets/src/banner.html"), { waitUntil: "networkidle" });
await p.evaluate(() => document.fonts.ready);
await p.screenshot({ path: join(root, "assets/banner.png") });
await b.close();
console.log("assets/banner.png");
