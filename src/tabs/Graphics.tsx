import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import {
  getLaunch, injectorDownload, injectorInstall, injectorState, listProton, setLaunch, xessInstall, xessProbe,
  xessState, xessSwitchDriver, Launch, ProtonTool, XessState,
} from "../api";
import { Advanced } from "../components/Advanced";
import { Hint, Status } from "../components/Hint";
import { availableCompatTools, runningApp, specifyCompatTool } from "../steam";
import { confirmUntested } from "../components/confirm";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });
const LSFG_DEFAULT = { multiplier: 2, flow_scale: 0.8, performance_mode: true };

export const GraphicsTab: FC = () => {
  const app = runningApp();
  return (
    <>
      <XessSection />
      <InjectorSection app={app} />
      <PerGameSection app={app} />
    </>
  );
};

const XessSection: FC = () => {
  const [x, setX] = useState<XessState | null>(null);
  const [busy, setBusy] = useState(false);
  const load = async () => setX(await xessState());
  useEffect(() => { load(); }, []);
  const install = async (allow = false) => {
    setBusy(true);
    try {
      const r = await xessInstall(allow);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("xess-xmx-linux", () => install(true)); return; }
      toast(r.ok ? (r.would ?? r.next ?? "Installed") : `Install failed: ${r.error ?? r.log}`);
    } finally { setBusy(false); load(); }
  };
  return (
    <PanelSection title="XeSS on XMX">
      <PanelSectionRow>
        <Status ok={x ? x.installed && x.status.state === "ok" : null} label={x?.installed ? `xess-xmx-linux ${x.version ?? ""}` : "XeSS XMX driver not installed"}
          detail={x?.installed ? (x.status.mode ?? x.status.line ?? "no session check yet: log out and in") : undefined} />
        <Hint does="Runs Intel XeSS upscaling and frame generation on the Claw's XMX cores in Proton games (xess-xmx-linux)." why="native XeSS games get Windows-level upscaling and up to 4x frame generation." cost="first launch of a game compiles its kernels (a minute or two, once)." />
      </PanelSectionRow>
      {x?.official_proton.length ? (
        <PanelSectionRow><div style={{ fontSize: 11, opacity: 0.8 }}>Intel's official XeSS path found in: {x.official_proton.join(", ")}. Prefer it for games on that Proton.</div></PanelSectionRow>
      ) : null}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => install(false)}>{x?.installed ? "Update driver" : "Install driver"}</ButtonItem>
        <Hint does="Downloads the release from IHulkSmashI/xess-xmx-linux and runs its installer as you." why="one tap instead of a terminal." cost="about 230 MB download; log out of Game Mode and back in after." />
      </PanelSectionRow>
      {x?.installed && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={async () => {
            setBusy(true);
            try { const r = await xessProbe(); toast(r.ok ? "Patched driver loads and finds the GPU" : `Probe failed: ${(r.log ?? r.error ?? "").split("\n").pop()}`); }
            finally { setBusy(false); load(); }
          }}>Probe driver</ButtonItem>
          <Hint does="Runs the package's own driver check right now." why="confirm XMX mode after a system update." cost="none." />
        </PanelSectionRow>
      )}
      {x?.installed && x.drivers.length > 1 && (
        <Advanced id="xess">
          <PanelSectionRow>
            <DropdownItem label="Default Mesa driver" rgOptions={x.drivers.map((d) => ({ data: d, label: `Mesa ${d}` }))}
              selectedOption={x.default_driver} onChange={async (o) => {
                const r = await xessSwitchDriver(o.data);
                toast(r.ok ? `Mesa ${o.data} is the default for the next game` : `Not switched: ${(r.log ?? r.error ?? "").split("\n").pop()}`);
                load();
              }} />
            <Hint does="Which patched Mesa games use unless their launch settings pick one." why="26.3 (Jay compiler) is faster in most games; 26.1 if a game misbehaves." cost="none; next game start, no re-login." />
          </PanelSectionRow>
        </Advanced>
      )}
    </PanelSection>
  );
};

const InjectorSection: FC<{ app: ReturnType<typeof runningApp> }> = ({ app }) => {
  const [mode, setMode] = useState("none");
  const [xessFg, setXessFg] = useState(true);
  const [lsfg, setLsfg] = useState<NonNullable<Launch["lsfg"]>>(LSFG_DEFAULT);
  const [state, setState] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    if (!app) return;
    const s = await injectorState(app.appid); setState(s); setMode(s.mode);
    const l = await getLaunch(app.appid);
    if (l.launch.inject) setMode(l.launch.inject);
    if (l.launch.lsfg) setLsfg(l.launch.lsfg);
  };
  useEffect(() => { load(); }, []);

  const download = async (kind: string, allow = false) => {
    setBusy(true);
    try {
      const r = await injectorDownload(kind, allow);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested(kind === "xess" ? "Intel's XeSS DLLs" : "OptiScaler", () => download(kind, true)); return; }
      toast(r.ok ? `${kind} ${r.tag} downloaded` : `Download failed: ${r.error}`);
    } finally { setBusy(false); load(); }
  };
  const needsAssets = mode === "optiscaler" && state && !state.assets?.optiscaler;
  const lsfgMissing = mode === "lsfg" && state && !state.lsfg?.ready;

  return (
    <PanelSection title={app ? `XMX injector: ${app.name}` : "XMX injector"}>
      <PanelSectionRow>
        <DropdownItem label="Mode for this game" disabled={!state?.dir} rgOptions={[
          { data: "none", label: "Off (game as shipped)" },
          { data: "optiscaler", label: "OptiScaler → XeSS on XMX" },
          { data: "lsfg", label: "lsfg-vk frame generation (no upscaler needed)" },
        ]} selectedOption={mode} onChange={(o) => setMode(o.data)} />
        <Hint does="OptiScaler makes DLSS/FSR games feed the Claw's XMX cores and can swap their frame generation for XeSS FG; lsfg-vk adds frame generation to anything." why="higher fps in games without native XeSS." cost="a little input delay; anti-cheat games refuse injected DLLs." />
      </PanelSectionRow>
      {mode === "optiscaler" && (
        <PanelSectionRow>
          <ToggleField label="Swap frame generation for XeSS FG" checked={xessFg} onChange={setXessFg} />
          <Hint does="Replaces the game's DLSS-FG / FSR-FG with XeSS frame generation." why="runs on XMX; up to 4x with the XeSS XMX driver." cost="off if the game's own FG looks better to you." />
        </PanelSectionRow>
      )}
      {mode === "lsfg" && (
        <>
          <PanelSectionRow>
            <SliderField label="Frame multiplier" value={lsfg.multiplier} min={2} max={4} step={1} showValue onChange={(v) => setLsfg({ ...lsfg, multiplier: v })} />
            <Hint does="Generated frames per real frame (2x, 3x, 4x)." why="2x from a steady 40–60 fps feels smoothest." cost="higher = more artifacts and input delay." />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="Flow scale" value={lsfg.flow_scale} min={0.25} max={1} step={0.05} showValue onChange={(v) => setLsfg({ ...lsfg, flow_scale: v })} />
            <ToggleField label="Performance mode" checked={lsfg.performance_mode} onChange={(v) => setLsfg({ ...lsfg, performance_mode: v })} />
            <Hint does="Lower flow scale and performance mode make frame generation cheaper." why="leaves more of the power budget for the game on battery." cost="slightly softer generated frames." />
          </PanelSectionRow>
          {lsfgMissing && <PanelSectionRow><div style={{ fontSize: 11, color: "#f0a000" }}>{!state.lsfg.layer ? "lsfg-vk is not installed (lsfg-vk.dev)." : "lsfg-vk needs Lossless Scaling from Steam (its DLL)."}</div></PanelSectionRow>}
        </>
      )}
      {needsAssets && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => download("optiscaler")}>Download OptiScaler</ButtonItem>
          <ButtonItem layout="below" disabled={busy || state?.assets?.xess} onClick={() => download("xess")}>{state?.assets?.xess ? "XeSS DLLs ready" : "Download Intel XeSS DLLs"}</ButtonItem>
          <Hint does="Fetches the releases from their own GitHub pages." why="ClawDeck never bundles upstream DLLs." cost="a few hundred MB once." />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!state?.dir || busy || needsAssets} onClick={async () => {
          setBusy(true);
          try {
            const r = await injectorInstall(app!.appid, mode, xessFg, mode === "lsfg" ? lsfg : null);
            toast(r.error ?? (r.dry_run ? `Dry run: ${r.would}` : mode === "none" ? "Injector removed, originals restored" : `${mode} set up; restart the game`));
          } finally { setBusy(false); load(); }
        }}>{mode === "none" ? "Remove injector" : "Apply to this game"}</ButtonItem>
        <Hint does={app ? (state?.native_xess ? "This game ships XeSS already: no injector needed, the XMX driver handles it." : (state?.dir ? `Writes into ${state.dir}; originals are backed up.` : "This game's folder was not found in Steam's library (non-Steam games are not supported yet).")) : "Start a game to configure it (the game folder is read from Steam)."}
          why="needs the ClawDeck launch wrapper in the game's launch options (see below)." />
      </PanelSectionRow>
    </PanelSection>
  );
};

const PerGameSection: FC<{ app: ReturnType<typeof runningApp> }> = ({ app }) => {
  const [launch, setL] = useState<Launch>({});
  const [wrapper, setWrapper] = useState("");
  const [tools, setTools] = useState<ProtonTool[]>([]);
  const load = async () => {
    if (!app) return;
    const l = await getLaunch(app.appid); setL(l.launch); setWrapper(l.wrapper);
    const steamTools = await availableCompatTools(app.appid);
    setTools(steamTools?.length ? steamTools.map((t) => ({ ...t, kind: "steam" })) : await listProton());
  };
  useEffect(() => { load(); }, []);
  const save = async (v: Partial<Launch>) => { if (!app) return; const r = await setLaunch(app.appid, v); setL(r.launch); };
  if (!app) {
    return <PanelSection title="This game"><PanelSectionRow><Hint does="Start a game to pick its Proton, HDR and driver." /></PanelSectionRow></PanelSection>;
  }
  return (
    <PanelSection title={`This game: ${app.name}`}>
      <PanelSectionRow>
        <DropdownItem label="Proton version" disabled={!tools.length}
          rgOptions={[{ data: "", label: "Steam default" }, ...tools.map((t) => ({ data: t.name, label: t.display }))]}
          selectedOption={launch.proton ?? ""} onChange={async (o) => {
            const ok = specifyCompatTool(app.appid, o.data);
            await save({ proton: o.data || null });
            toast(ok ? `${o.data || "Steam default"} for ${app.name}; takes effect next launch` : "Steam refused the change; set it in the game's Properties → Compatibility");
          }} />
        <Hint does="Which Proton runs this game (GE-Proton from ProtonUp-Qt or Valve's)." why="GE-Proton often fixes video cutscenes and new games sooner." cost="a different Proton can need a new shader cache." />
      </PanelSectionRow>
      <Advanced id="graphics">
        <PanelSectionRow>
          <ToggleField label="HDR output (Proton games)" checked={!!launch.hdr} onChange={(v) => save({ hdr: v })} />
          <Hint does="Lets DXVK games output HDR (DXVK_HDR=1)." why="docked on an HDR TV with HDR on in the Display tab." cost="washed-out colours if the screen is not in HDR." />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem label="XeSS driver for this game" rgOptions={[
            { data: "", label: "Package default" }, { data: "26.3", label: "Mesa 26.3 (faster)" }, { data: "26.1", label: "Mesa 26.1 (most tested)" },
          ]} selectedOption={launch.mesa ?? ""} onChange={(o) => save({ mesa: o.data })} />
          <Hint does="Picks xess-xmx-linux's patched Mesa for this game only." why="26.1 if a game misbehaves on 26.3." cost="none." />
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: 11, opacity: 0.75, wordBreak: "break-all" }}>Launch options (once per game): <b>{wrapper}</b></div>
          <Hint does="The wrapper applies everything above and gives autotune its frame times." why="ClawDeck never edits Steam's launch options for you." />
        </PanelSectionRow>
      </Advanced>
    </PanelSection>
  );
};
