// Builds preview/dist/ClawDeck-preview.html: one self-contained page (React from cdnjs, everything else inline).
//   pnpm preview
import { build } from "esbuild";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const alias = {
  "@decky/ui": join(here, "mocks/decky-ui.tsx"),
  "@decky/api": join(here, "mocks/decky-api.ts"),
  "react/jsx-runtime": join(here, "mocks/jsx-runtime.ts"),
  react: join(here, "mocks/react-shim.ts"),
};

const out = await build({
  entryPoints: [join(here, "main.tsx")],
  bundle: true, write: false, minify: true, format: "iife", target: "es2020",
  jsx: "automatic", alias, logLevel: "warning",
});
const js = out.outputFiles[0].text.replace(/<\/script/gi, "<\\/script");
const css = readFileSync(join(here, "preview.css"), "utf8");

const html = `<title>ClawDeck Preview</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@600;700&family=Mulish:wght@400;600;700&family=JetBrains+Mono:wght@400&display=swap">
<style>${css}</style>
<div id="root"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js"></script>
<script>${js}</script>
`;
mkdirSync(join(here, "dist"), { recursive: true });
writeFileSync(join(here, "dist", "ClawDeck-preview.html"), html);
console.log(`preview/dist/ClawDeck-preview.html (${(html.length / 1024).toFixed(0)} KB)`);
