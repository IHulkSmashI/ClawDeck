import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getHealth, injectorInstall, injectorState, Health } from "../api";
import { Hint, Status } from "../components/Hint";

declare const Router: any;
declare const appStore: any;

const runningApp = (): { appid: string; name: string; dir: string | null } | null => {
  try {
    const a = Router?.MainRunningApp;
    if (!a) return null;
    let dir: string | null = null;
    try { dir = appStore?.GetAppOverviewByAppID?.(a.appid)?.m_strInstallDir ?? null; } catch { /* not available */ }
    return { appid: String(a.appid), name: a.display_name, dir };
  } catch { return null; }
};

export const GraphicsTab: FC = () => {
  const [health, setHealth] = useState<Health[]>([]);
  const [mode, setMode] = useState("none");
  const [xessFg, setXessFg] = useState(true);
  const [state, setState] = useState<any>(null);
  const app = runningApp();

  useEffect(() => {
    getHealth().then((h) => setHealth(h.filter((x) => ["xess", "mesa", "protonup"].includes(x.id))));
    if (app?.dir) injectorState(app.dir).then((s) => { setState(s); setMode(s.mode); });
  }, []);

  return (
    <>
      <PanelSection title="XeSS on XMX">
        {health.map((h) => <PanelSectionRow key={h.id}><Status ok={h.ok} label={h.title} detail={h.detail} /></PanelSectionRow>)}
        <PanelSectionRow>
          <Hint does="The xess-xmx-linux driver runs Intel's XeSS kernels on the XMX cores and puts XeLL into driver mode." why="native XeSS games get Windows-level upscaling and frame generation." cost="first launch of a game compiles its kernels (about a minute)." />
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title={app ? `XMX injector: ${app.name}` : "XMX injector"}>
        <PanelSectionRow>
          <DropdownItem label="Mode for this game" disabled={!app?.dir} rgOptions={[
            { data: "none", label: "Off (game as shipped)" },
            { data: "optiscaler", label: "OptiScaler → XeSS on XMX" },
            { data: "lsfg", label: "lsfg-vk frame generation (no upscaler needed)" },
          ]} selectedOption={mode} onChange={(o) => setMode(o.data)} />
          <Hint does="OptiScaler makes DLSS/FSR games feed the Claw's XMX cores and can swap their frame generation for XeSS FG; lsfg-vk adds frame generation to anything." why="higher fps in games without native XeSS." cost="a little input delay; anti-cheat games refuse injected DLLs." />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField label="Swap frame generation for XeSS FG" checked={xessFg} disabled={mode !== "optiscaler"} onChange={setXessFg} />
          <Hint does="Replaces the game's DLSS-FG / FSR-FG with XeSS frame generation." why="runs on XMX; up to 4x in supported titles." cost="off if the game's own FG looks better to you." />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!app?.dir} onClick={async () => {
            const r = await injectorInstall(app!.dir!, mode, xessFg);
            toaster.toast({ title: "ClawDeck", body: r.error ?? (mode === "none" ? "Injector removed, originals restored" : `${mode} installed`) });
            setState(await injectorState(app!.dir!));
          }}>{mode === "none" ? "Remove injector" : "Apply to this game"}</ButtonItem>
          <Hint does={app ? (state?.native_xess ? "This game ships XeSS already: native mode needs no injector." : `Writes into ${state?.dir ?? "the game folder"}; originals are backed up.`) : "Start a game to configure it (the game folder is read from Steam)."} />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
