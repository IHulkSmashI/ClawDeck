import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import {
  getHardware, getLaunch, getUpdates, injectorInstall, injectorPlan, lsfgInstall, setSetting, updateNow, listProton, setLaunch, xessInstall, xessProbe,
  xessState, xessSwitchDriver, InjectorPlan, Launch, ProtonTool, Updates, XessState,
} from "../api";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { Hint, Status } from "../components/Hint";
import { availableCompatTools, installLossless, losslessOwnership, runningApp, showLosslessInStore, specifyCompatTool, Ownership } from "../steam";
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
  const [noXmx, setNoXmx] = useState<string | null>(null);   // model name when this Claw has no XMX cores
  const load = async () => setX(await xessState());
  useEffect(() => {
    load();
    getHardware().then((h) => setNoXmx(h.known && !h.model.xmx ? h.model.name : null));
  }, []);
  const install = async (allow = false) => {
    setBusy(true);
    try {
      const r = await xessInstall(allow);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("xess-xmx-linux", () => install(true)); return; }
      toast(r.ok ? (r.would ?? r.next ?? "Installed") : `Install failed: ${r.error ?? r.log}`);
    } finally { setBusy(false); load(); }
  };
  if (noXmx) {
    return (
      <PanelSection title="XeSS on XMX">
        <PanelSectionRow>
          <Status ok={null} label={`No XMX cores on the ${noXmx}`} />
          <Hint does="The XMX driver needs Intel Arc graphics with XMX cores (Claw 7 AI+, 8 AI+, 8 EX)." why="games can still use XeSS, FSR or Lossless Scaling below; they just run on the regular GPU cores." cost="none." />
        </PanelSectionRow>
      </PanelSection>
    );
  }
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
        <Hint does="Downloads xess-xmx-linux from its GitHub releases and runs its installer for you." why="one tap instead of a terminal." cost="about 230 MB download; restart the Claw once after." />
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

const MODE_LABEL: Record<string, string> = {
  native: "Native XeSS (nothing to install)",
  optiscaler: "OptiScaler: XeSS + XeSS frame generation",
  lsfg: "Lossless Scaling frame generation (lsfg-vk)",
  none: "Off (game as shipped)",
};

const Bullets: FC<{ items: string[]; tone?: "warn" }> = ({ items, tone }) => (
  <ul style={{ margin: "2px 0 6px 0", paddingLeft: 16, fontSize: 11, lineHeight: 1.4, opacity: tone ? 1 : 0.8, color: tone ? "#f0b429" : undefined }}>
    {items.map((t, i) => <li key={i}>{t}</li>)}
  </ul>
);

const InjectorSection: FC<{ app: ReturnType<typeof runningApp> }> = ({ app }) => {
  const [p, setP] = useState<InjectorPlan | null>(null);
  const [mode, setMode] = useState<string>("none");
  const [mult, setMult] = useState(3);
  const [lsfg, setLsfg] = useState<NonNullable<Launch["lsfg"]>>(LSFG_DEFAULT);
  const [busy, setBusy] = useState(false);
  const [others, setOthers] = useState<string[]>([]);
  const [own, setOwn] = useState<Ownership>({ owned: null, installed: null });
  const load = async () => {
    setOthers((await getHardware()).other_plugins?.injector ?? []);
    setOwn(losslessOwnership());
    if (!app) return;
    const r = await injectorPlan(app.appid, app.name);
    setP(r);
    if (r.plan) {
      const installed = r.state?.mode && r.state.mode !== "none" ? r.state.mode : null;
      setMode(installed ?? (r.plan.mode === "native" ? "none" : r.plan.mode));
      setMult(r.plan.options.multiplier ?? 2);
    }
    const l = await getLaunch(app.appid);
    if (l.launch.lsfg) setLsfg(l.launch.lsfg);
  };
  useEffect(() => { load(); }, []);

  if (!app) return <PanelSection title="Upscaling and frame generation"><PanelSectionRow><Hint does="Start a game: ClawDeck checks what it supports and recommends the best option." /></PanelSectionRow></PanelSection>;
  if (p && !p.found) return <PanelSection title="Upscaling and frame generation"><PanelSectionRow><Hint does="This game's folder was not found in Steam's library (non-Steam games are not supported yet)." /></PanelSectionRow></PanelSection>;

  const plan = p?.plan;
  const st = p?.state;
  const installed: string = st?.mode ?? "none";
  const foreign = !!st?.foreign_optiscaler;
  const lsfgReady = !!st?.lsfg?.ready;
  const upd = p?.updates;
  const stale = installed === "optiscaler" && upd?.optiscaler?.latest && st?.version !== upd.optiscaler.latest;

  const apply = async (m: string) => {
    setBusy(true);
    try {
      const r = await injectorInstall(app.appid, m, m === "optiscaler" ? { multiplier: mult } : null, m === "lsfg" ? lsfg : null, app.name);
      toast(r.error ?? (r.dry_run ? `Dry run: ${r.would}` : m === "none" ? "Removed; the game's own files are back"
        : m === "optiscaler" ? `OptiScaler ${r.version ?? ""} set up${r.reframework_version ? " with REFramework" : ""}${r.optipatcher_version ? " and OptiPatcher" : ""}. Restart the game.`
        : "Lossless Scaling frame generation on for this game. Restart it."));
    } finally { setBusy(false); load(); }
  };

  return (
    <PanelSection title="Upscaling and frame generation">
      {others.length > 0 && <PanelSectionRow><Bullets tone="warn" items={[`${others.join(" and ")} is installed too. Use one tool per game: if a game is set up there, leave it Off here.`]} /></PanelSectionRow>}
      {!plan && <PanelSectionRow><div style={{ fontSize: 12, opacity: 0.7 }}>Checking {app.name}…</div></PanelSectionRow>}
      {plan && (
        <>
          <PanelSectionRow>
            <div style={{ fontSize: 13 }}><b>Best for {app.name}:</b> {MODE_LABEL[plan.mode] ?? plan.mode}</div>
            <Bullets items={[...(p?.found_in_game ?? []), ...plan.reasons]} />
            {plan.steps.length > 0 && <Bullets items={plan.steps.map((x: string) => `Sets up: ${x}`)} />}
            {plan.warnings.length > 0 && <Bullets tone="warn" items={plan.warnings} />}
            {plan.page && <div style={{ fontSize: 10, opacity: 0.6, wordBreak: "break-all" }}>Community notes: {plan.page}</div>}
          </PanelSectionRow>
          {foreign && <PanelSectionRow><Hint does="OptiScaler is already in this game, set up by another tool." why="manage it where you set it up; ClawDeck won't overwrite it." /></PanelSectionRow>}
          {installed !== "none" && (
            <PanelSectionRow>
              <Status ok={!stale} label={`Installed: ${MODE_LABEL[installed] ?? installed}`}
                detail={installed === "optiscaler" ? `OptiScaler ${st?.version ?? "?"}${st?.info?.reframework_version ? " · REFramework" : ""}${st?.info?.optipatcher_version ? " · OptiPatcher" : ""}${stale ? ` · ${upd?.optiscaler?.latest} is out, updates after you close the game` : " · newest version"}` : undefined} />
            </PanelSectionRow>
          )}
        </>
      )}
      {plan && !plan.blocked && !foreign && (
        <>
          <PanelSectionRow>
            <DropdownItem label="Use for this game" rgOptions={[
              ...(plan.mode === "optiscaler" || plan.alternative === "optiscaler" ? [{ data: "optiscaler", label: MODE_LABEL.optiscaler + (plan.mode === "optiscaler" ? " (best)" : "") }] : []),
              { data: "lsfg", label: MODE_LABEL.lsfg + (plan.mode === "lsfg" ? " (best)" : " (last resort)") },
              { data: "none", label: plan.mode === "native" ? "Nothing: use XeSS in the game's settings (best)" : MODE_LABEL.none },
            ]} selectedOption={mode} onChange={(o) => setMode(o.data)} />
          </PanelSectionRow>
          {mode === "optiscaler" && plan.options.fg !== false && (
            <PanelSectionRow>
              <DropdownItem label="XeSS frame generation" rgOptions={[
                { data: 2, label: "2x" }, { data: 3, label: "3x (recommended on the Claw)" }, { data: 4, label: "4x" },
              ]} selectedOption={mult} onChange={(o) => setMult(o.data)} />
              <Hint does="How many frames XeSS adds per real frame. Runs on the XMX cores." why="3x from about 40 fps gives a smooth 120 on the Claw's screen." cost="more than 2x needs the XeSS driver (above); a little input delay." />
            </PanelSectionRow>
          )}
          {mode === "lsfg" && (
            <>
              <PanelSectionRow>
                <Status ok={own.owned === null ? null : own.owned && own.installed ? true : false}
                  label={own.owned === false ? "You don't own Lossless Scaling yet" : own.installed === false ? "Lossless Scaling is in your library but not installed" : "Lossless Scaling is installed"} />
                {own.owned === false && <ButtonItem layout="below" onClick={() => { if (!showLosslessInStore()) toast("Open the Steam store and search for Lossless Scaling"); }}>Get Lossless Scaling on Steam</ButtonItem>}
                {own.owned && own.installed === false && <ButtonItem layout="below" onClick={() => { if (!installLossless()) toast("Install Lossless Scaling from your Steam library"); else toast("Steam is installing Lossless Scaling"); }}>Install Lossless Scaling</ButtonItem>}
                <Hint does="lsfg-vk uses the frame generation from Lossless Scaling, a paid Steam app, so you need your own copy." why="it is the last option, for games with no DLSS, FSR or XeSS." cost="runs on the GPU's regular shader cores, not the XMX cores (only XeSS uses those); a little more input delay than XeSS frame generation." />
              </PanelSectionRow>
              <PanelSectionRow>
                <Status ok={!!st?.lsfg?.layer} label={st?.lsfg?.layer ? `lsfg-vk installed${st?.lsfg_version ? ` (${st.lsfg_version})` : ""}` : "lsfg-vk is not installed yet"} />
                {!st?.lsfg?.layer && <ButtonItem layout="below" disabled={busy} onClick={async () => { setBusy(true); try { const r = await lsfgInstall(); toast(r.ok ? `lsfg-vk ${r.version ?? ""} installed` : `Not installed: ${r.error}`); } finally { setBusy(false); load(); } }}>Install lsfg-vk (newest)</ButtonItem>}
              </PanelSectionRow>
              <PanelSectionRow>
                <SliderField label="Frame multiplier" value={lsfg.multiplier} min={2} max={4} step={1} showValue onChange={(v) => setLsfg({ ...lsfg, multiplier: v })} />
                <Hint does="Generated frames per real frame (2x, 3x, 4x)." why="2x from a steady 40–60 fps feels smoothest." cost="higher = more artifacts and input delay." />
              </PanelSectionRow>
              <Advanced id="lsfg" label="More frame generation settings">
                <PanelSectionRow>
                  <SliderField label="Flow scale" value={lsfg.flow_scale} min={0.25} max={1} step={0.05} showValue onChange={(v) => setLsfg({ ...lsfg, flow_scale: v })} />
                  <ToggleField label="Performance mode" checked={lsfg.performance_mode} onChange={(v) => setLsfg({ ...lsfg, performance_mode: v })} />
                  <Hint does="Lower flow scale and performance mode make frame generation cheaper." why="leaves more of the power budget for the game on battery." cost="slightly softer generated frames." />
                </PanelSectionRow>
              </Advanced>
            </>
          )}
          {(mode !== "none" || installed !== "none") && (
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy || (mode === "lsfg" && !(lsfgReady || (own.owned && own.installed)))} onClick={() => apply(mode)}>
                {busy ? "Working…" : mode === "none" ? "Remove from this game" : installed === mode ? "Update settings for this game" : mode === "optiscaler" ? "Set up OptiScaler (newest)" : "Turn on Lossless Scaling for this game"}
              </ButtonItem>
              <Hint does={mode === "optiscaler" ? "Downloads the newest OptiScaler and installs it exactly as its own installer does, plus OptiPatcher and REFramework when this game needs them." : mode === "lsfg" ? "Turns lsfg-vk on for this game; nothing is copied into the game folder." : "Takes ClawDeck's files out of this game and puts its originals back."} why="takes effect the next time the game starts." />
            </PanelSectionRow>
          )}
          {mode !== "none" && <LaunchWrapper appid={app.appid} name={app.name} />}
        </>
      )}
      {plan?.blocked && <PanelSectionRow><Hint does="Online games with anti-cheat can ban accounts for changed game files, so ClawDeck offers nothing here." why="use the game's own upscaler settings instead." /></PanelSectionRow>}
      <UpdatesRow />
    </PanelSection>
  );
};

const UpdatesRow: FC = () => {
  const [u, setU] = useState<Updates | null>(null);
  const [busy, setBusy] = useState(false);
  const load = async (force = false) => setU(await getUpdates(force));
  useEffect(() => { load(); }, []);
  if (!u) return null;
  const parts = [["OptiScaler", "optiscaler"], ["REFramework", "reframework"], ["lsfg-vk", "lsfg"]]
    .filter(([, k]) => u[k]?.installed).map(([n, k]) => `${n} ${u[k].installed}${u[k].update ? ` → ${u[k].latest}` : ""}`);
  const anyUpdate = ["optiscaler", "optipatcher", "reframework", "lsfg"].some((k) => u[k]?.update && u[k]?.installed);
  return (
    <Advanced id="updates" label="Updates">
      <PanelSectionRow>
        <div style={{ fontSize: 11, opacity: 0.8 }}>{parts.length ? parts.join(" · ") : "Nothing downloaded yet"}{u.optiscaler?.latest && !u.optiscaler.installed ? ` · newest OptiScaler: ${u.optiscaler.latest}` : ""}</div>
        <ToggleField label="Keep tools up to date automatically" checked={u.auto !== false} onChange={async (v) => { await setSetting("auto_update", v); load(); }} />
        <Hint does="Checks once a day for new OptiScaler, OptiPatcher, REFramework and lsfg-vk releases and updates your games right after you close them." why="game fixes arrive with new versions." cost="a small download now and then; never while a game runs." />
        <ButtonItem layout="below" disabled={busy} onClick={async () => { setBusy(true); try { if (anyUpdate) { await updateNow(); toast("Updated; games pick it up the next time they close"); } else { await load(true); toast("Everything is up to date"); } } finally { setBusy(false); load(); } }}>{anyUpdate ? "Update now" : "Check for updates"}</ButtonItem>
      </PanelSectionRow>
    </Advanced>
  );
};

const PerGameSection: FC<{ app: ReturnType<typeof runningApp> }> = ({ app }) => {
  const [launch, setL] = useState<Launch>({});
  const [tools, setTools] = useState<ProtonTool[]>([]);
  const [xess, setXess] = useState<XessState | null>(null);
  const load = async () => {
    if (!app) return;
    const l = await getLaunch(app.appid); setL(l.launch);
    setXess(await xessState());
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
        {xess?.installed && xess.drivers.length > 1 && (
          <PanelSectionRow>
            <DropdownItem label="XeSS driver for this game" rgOptions={[
              { data: "", label: "Same as the default" }, { data: "26.3", label: "Mesa 26.3 (faster)" }, { data: "26.1", label: "Mesa 26.1 (most tested)" },
            ]} selectedOption={launch.mesa ?? ""} onChange={(o) => save({ mesa: o.data })} />
            <Hint does="Picks xess-xmx-linux's patched Mesa for this game only." why="26.1 if a game misbehaves on 26.3." cost="none." />
          </PanelSectionRow>
        )}
        {(launch.hdr || launch.mesa) && <LaunchWrapper appid={app.appid} name={app.name} />}
      </Advanced>
    </PanelSection>
  );
};
