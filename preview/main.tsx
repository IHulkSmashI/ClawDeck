// Browser preview of ClawDeck's Quick Access panel: the real tab components from src/, with @decky/ui, @decky/api
// and Steam's globals replaced by stand-ins (preview/mocks) and an example backend (preview/backend.ts).
import { useEffect, useState } from "react";
import { ModalHost, setNavigator } from "@decky/ui";
import { onToast } from "@decky/api";

import { scenario } from "./backend";
import { FullPage, QuickPanel } from "../src/pages";

let launchOptions = "";   // the example game starts without ClawDeck in its launch options
(window as any).SteamClient = {
  Apps: {
    RegisterForAppDetails: (_id: number, cb: (d: any) => void) => { setTimeout(() => cb({ strLaunchOptions: launchOptions })); return { unregister() {} }; },
    SetAppLaunchOptions: (_id: number, opts: string) => { launchOptions = opts; },
  },
};

const PAGES = ["power", "game", "fixes", "setup"];

/** Quick Access panel on the left, the full-screen page (Steam's sidebar layout) on the right. */
const Panels = () => {
  const [rev, setRev] = useState(0);
  const [page, setPage] = useState(() => {
    const h = location.hash.replace("#", "");
    return PAGES.includes(h) ? h : "power";
  });
  useEffect(() => { scenario.listeners.push(() => setRev((r) => r + 1)); }, []);
  useEffect(() => { setNavigator((path) => { const p = path.split("/").pop() || "power"; setPage(p); location.hash = p; }); }, []);
  return (
    <div className="screens">
      <div className="device qam-device" aria-label="Quick Access panel">
        <div className="qam">
          <div className="qam-head"><span className="qam-title">ClawDeck</span><span className="qam-clock">18:20 · 64%</span></div>
          <div className="qam-tabpanel"><QuickPanel key={`q-${rev}`} /></div>
        </div>
      </div>
      <div className="device full-device" aria-label="Full-screen settings page">
        <div className="full"><FullPage key={`f-${rev}-${page}`} page={`/clawdeck/${page}`} /></div>
      </div>
    </div>
  );
};

const Toasts = () => {
  const [items, setItems] = useState<{ id: number; title: string; body: string }[]>([]);
  useEffect(() => onToast((t) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-2), { id, ...t }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4200);
  }), []);
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => <div key={t.id} className="toast"><b>{t.title}</b><span>{t.body}</span></div>)}
    </div>
  );
};

const Switch = ({ id, label, checked, onChange }: { id: string; label: string; checked: boolean; onChange: (v: boolean) => void }) => (
  <label className="scn-switch" htmlFor={id}>
    <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    <span>{label}</span>
  </label>
);

const Scenario = () => {
  const [, force] = useState(0);
  const set = (k: "game" | "plugged" | "controller" | "which", v: any) => { scenario.set(k, v); force((n) => n + 1); };
  return (
    <fieldset className="scenario">
      <legend>Preview only: pretend that</legend>
      <Switch id="scn-game" label="A game is running" checked={scenario.game} onChange={(v) => set("game", v)} />
      <div className="scn-choice" role="radiogroup" aria-label="Example game">
        <span>The game</span>
        {([["dd2", "Dragon's Dogma 2"], ["hk", "Hollow Knight"]] as const).map(([k, label]) => (
          <label key={k} htmlFor={`scn-${k}`}>
            <input id={`scn-${k}`} type="radio" name="scn-which" checked={scenario.which === k} onChange={() => set("which", k)} />
            {label}
          </label>
        ))}
      </div>
      <Switch id="scn-plug" label="Plugged in, dock with a 4K TV" checked={scenario.plugged} onChange={(v) => set("plugged", v)} />
      <div className="scn-choice" role="radiogroup" aria-label="Controller service">
        <span>Your Claw runs (the real plugin detects this itself)</span>
        {(["inputplumber", "hhd"] as const).map((c) => (
          <label key={c} htmlFor={`scn-${c}`}>
            <input id={`scn-${c}`} type="radio" name="scn-controller" checked={scenario.controller === c} onChange={() => set("controller", c)} />
            {c === "inputplumber" ? "InputPlumber (current Bazzite)" : "Handheld Daemon"}
          </label>
        ))}
        <p className="scn-why">The program behind the MSI buttons, gyro and lights. New Bazzite uses InputPlumber, older
          installs Handheld Daemon. There is no setting for this in ClawDeck: it uses whichever your Claw has.</p>
      </div>
    </fieldset>
  );
};

const App = () => (
  <main className="page">
    <header className="intro">
      <p className="eyebrow">Decky plugin · MSI Claw 8 EX · Bazzite</p>
      <h1>ClawDeck</h1>
      <p className="lede">This is the plugin's Quick Access panel running in your browser. Every control works, but it talks to
        example data instead of a real Claw, so nothing here is a measurement.</p>
      <Scenario />
      <p className="note">Same code as the plugin's <code>src/tabs</code>, with Steam's parts swapped for stand-ins. Build it
        yourself with <code>pnpm preview</code>.</p>
    </header>
    <div className="stage">
      <Panels />
      <ModalHost />
      <Toasts />
    </div>
  </main>
);

const ReactDOM = (window as any).ReactDOM;
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
