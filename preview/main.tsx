// Browser preview of ClawDeck's Quick Access panel: the real tab components from src/, with @decky/ui, @decky/api
// and Steam's globals replaced by stand-ins (preview/mocks) and an example backend (preview/backend.ts).
import { useEffect, useState } from "react";
import { Tabs, ModalHost } from "@decky/ui";
import { onToast } from "@decky/api";

import { scenario } from "./backend";
import { SetupTab } from "../src/tabs/Setup";
import { PowerTab } from "../src/tabs/Power";
import { GraphicsTab } from "../src/tabs/Graphics";
import { DisplayTab } from "../src/tabs/Display";
import { ControllerTab } from "../src/tabs/Controller";
import { BatteryTab } from "../src/tabs/Battery";

(window as any).SteamClient = {
  Apps: {
    SpecifyCompatTool: () => undefined,
    GetAvailableCompatTools: async () => [
      { strToolName: "GE-Proton11-6", strDisplayName: "GE-Proton11-6" },
      { strToolName: "proton_experimental", strDisplayName: "Proton Experimental" },
      { strToolName: "proton_10", strDisplayName: "Proton 10.0-3" },
    ],
  },
};

const TABS = [
  { id: "setup", title: "Setup", C: SetupTab },
  { id: "power", title: "Power", C: PowerTab },
  { id: "graphics", title: "Graphics", C: GraphicsTab },
  { id: "display", title: "Display", C: DisplayTab },
  { id: "controller", title: "Controller", C: ControllerTab },
  { id: "battery", title: "Battery & Fans", C: BatteryTab },
];

const initialTab = () => {
  const h = location.hash.replace("#", "");
  if (TABS.some((t) => t.id === h)) return h;
  try { return localStorage.getItem("clawdeck.preview.tab") || "setup"; } catch { return "setup"; }
};

const Panel = () => {
  const [tab, setTab] = useState(initialTab);
  const [rev, setRev] = useState(0);
  useEffect(() => { scenario.listeners.push(() => setRev((r) => r + 1)); }, []);
  useEffect(() => { try { localStorage.setItem("clawdeck.preview.tab", tab); } catch { /* private window */ } }, [tab]);
  return (
    <Tabs activeTab={tab} onShowTab={setTab}
      tabs={TABS.map((t) => ({ id: t.id, title: t.title, content: <t.C key={`${t.id}-${rev}`} /> }))} />
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
  const set = (k: "game" | "plugged" | "controller", v: any) => { scenario.set(k, v); force((n) => n + 1); };
  return (
    <fieldset className="scenario">
      <legend>Try it as</legend>
      <Switch id="scn-game" label="A game is running (Dragon's Dogma 2)" checked={scenario.game} onChange={(v) => set("game", v)} />
      <Switch id="scn-plug" label="Plugged in, dock with a 4K TV" checked={scenario.plugged} onChange={(v) => set("plugged", v)} />
      <div className="scn-choice" role="radiogroup" aria-label="Controller service">
        <span>Controller service</span>
        {(["inputplumber", "hhd"] as const).map((c) => (
          <label key={c} htmlFor={`scn-${c}`}>
            <input id={`scn-${c}`} type="radio" name="scn-controller" checked={scenario.controller === c} onChange={() => set("controller", c)} />
            {c === "inputplumber" ? "InputPlumber (current Bazzite)" : "Handheld Daemon"}
          </label>
        ))}
        <p className="scn-why">The program that runs the MSI buttons, gyro and lights. New Bazzite uses InputPlumber; older
          installs use Handheld Daemon. Only one should run at a time, so ClawDeck works with whichever your Claw has.</p>
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
    <div className="device" aria-label="Quick Access panel">
      <div className="qam">
        <div className="qam-head"><span className="qam-title">ClawDeck</span><span className="qam-clock">18:20 · 64%</span></div>
        <Panel />
      </div>
      <ModalHost />
      <Toasts />
    </div>
  </main>
);

const ReactDOM = (window as any).ReactDOM;
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
