import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import {
  getHardware, getLaunch, getUpdates, injectorInstall, injectorPlan, lsfgInstall, setSetting, updateNow, listProton, setLaunch, xessInstall, xessProbe,
  xessState, xessSwitchDriver, InjectorPlan, Launch, ProtonTool, Updates, XessState,
} from "../api";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Note, Problem, Status } from "../components/Hint";
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
  const [err, setErr] = useState<string | null>(null);
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
      toast(r.ok ? (r.would ?? r.next ?? "Installed") : `XeSS driver: not installed (${r.error ?? r.log})`);
      setErr(r.ok ? null : (r.error ?? (r.log ? r.log.trim().split("\n").slice(-3).join(" · ") : "see the log")));
    } finally { setBusy(false); load(); }
  };
  if (noXmx) {
    return (
      <PanelSection title="XeSS on XMX">
        <PanelSectionRow>
          <Status ok={null} label={`No XMX cores on the ${noXmx}`}
            detail="Games can still use XeSS, FSR or Lossless Scaling below; they run on the regular graphics cores." />
        </PanelSectionRow>
      </PanelSection>
    );
  }
  return (
    <PanelSection title="XeSS on XMX">
      <PanelSectionRow>
        <Status ok={x ? x.installed && x.status.state === "ok" : null} label={x?.installed ? `xess-xmx-linux ${x.version ?? ""}` : "XeSS XMX driver not installed"}
          detail={x?.installed ? (x.status.mode ?? x.status.line ?? "no check yet: restart the Claw once") : "Gives XeSS games the same upscaling and up to 4x frame generation as on Windows."} />
      </PanelSectionRow>
      {x?.official_proton.length ? (
        <PanelSectionRow><Note>Intel's official XeSS path found in: {x.official_proton.join(", ")}. Prefer it for games on that Proton.</Note></PanelSectionRow>
      ) : null}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => install(false)}
          description={explain("Downloads and installs xess-xmx-linux for you.", undefined, "about 230 MB; restart the Claw once after. Each game's first launch takes a minute or two longer, once.")}>{x?.installed ? "Update driver" : "Install driver"}</ButtonItem>
        <Problem error={err} what="XeSS driver" />
      </PanelSectionRow>
      {x?.installed && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} description={explain("Checks the driver now, for example after a system update.")} onClick={async () => {
            setBusy(true);
            try { const r = await xessProbe(); toast(r.ok ? "Patched driver loads and finds the GPU" : `Probe failed: ${(r.log ?? r.error ?? "").split("\n").pop()}`); }
            finally { setBusy(false); load(); }
          }}>Probe driver</ButtonItem>
        </PanelSectionRow>
      )}
      {x?.installed && x.drivers.length > 1 && (
        <Advanced id="xess">
          <PanelSectionRow>
            <DropdownItem label="Default Mesa driver" rgOptions={x.drivers.map((d) => ({ data: d, label: `Mesa ${d}` }))}
              selectedOption={x.default_driver} description={explain("26.3 is faster in most games; 26.1 if a game misbehaves. From the next game start.")} onChange={async (o) => {
                const r = await xessSwitchDriver(o.data);
                toast(r.ok ? `Mesa ${o.data} is the default for the next game` : `Not switched: ${(r.log ?? r.error ?? "").split("\n").pop()}`);
                load();
              }} />
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

const Bullets: FC<{ items: string[]; tone?: "warn"; label?: string }> = ({ items, tone, label }) => (
  <Note label={label}>
    <span style={{ color: tone ? "#f0b429" : undefined }}>{items.map((t, i) => <div key={i}>• {t}</div>)}</span>
  </Note>
);

const InjectorSection: FC<{ app: ReturnType<typeof runningApp> }> = ({ app }) => {
  const [p, setP] = useState<InjectorPlan | null>(null);
  const [mode, setMode] = useState<string>("none");
  const [mult, setMult] = useState(3);
  const [lsfg, setLsfg] = useState<NonNullable<Launch["lsfg"]>>(LSFG_DEFAULT);
  const [busy, setBusy] = useState(false);
  const [others, setOthers] = useState<string[]>([]);
  const [own, setOwn] = useState<Ownership>({ owned: null, installed: null });
  const [err, setErr] = useState<string | null>(null);
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

  if (!app) return <PanelSection title="Upscaling and frame generation"><PanelSectionRow><Note>Start a game: ClawDeck checks what it supports and recommends the best option.</Note></PanelSectionRow></PanelSection>;
  if (p && !p.found) return <PanelSection title="Upscaling and frame generation"><PanelSectionRow><Note>This game's folder wasn't found in Steam's library (non-Steam games aren't supported yet).</Note></PanelSectionRow></PanelSection>;

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
      setErr(r.error ?? null);
      toast(r.error ? `${m === "none" ? "Remove" : m === "optiscaler" ? "OptiScaler" : "Lossless Scaling"}: failed (${r.error})` : (r.dry_run ? `Dry run: ${r.would}` : m === "none" ? "Removed; the game's own files are back"
        : m === "optiscaler" ? `OptiScaler ${r.version ?? ""} set up${r.reframework_version ? " with REFramework" : ""}${r.optipatcher_version ? " and OptiPatcher" : ""}. Restart the game.`
        : "Lossless Scaling frame generation on for this game. Restart it."));
    } finally { setBusy(false); load(); }
  };

  return (
    <PanelSection title="Upscaling and frame generation">
      {others.length > 0 && <PanelSectionRow><Bullets tone="warn" items={[`${others.join(" and ")} is installed too. Use one tool per game: if a game is set up there, leave it Off here.`]} /></PanelSectionRow>}
      {!plan && <PanelSectionRow><Note>Checking {app.name}…</Note></PanelSectionRow>}
      {plan && (
        <>
          <PanelSectionRow>
            <Bullets label={`Best for ${app.name}: ${MODE_LABEL[plan.mode] ?? plan.mode}`} items={[...(p?.found_in_game ?? []), ...plan.reasons]} />
            {plan.steps.length > 0 && <Bullets items={plan.steps.map((x: string) => `Sets up: ${x}`)} />}
            {plan.warnings.length > 0 && <Bullets tone="warn" items={plan.warnings} />}
            {plan.page && <Note label="Community notes"><span style={{ wordBreak: "break-all" }}>{plan.page}</span></Note>}
          </PanelSectionRow>
          {foreign && <PanelSectionRow><Note>OptiScaler is already in this game, set up by another tool. Manage it there; ClawDeck won't overwrite it.</Note></PanelSectionRow>}
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
              ]} selectedOption={mult} onChange={(o) => setMult(o.data)}
                description={explain("Frames added per real frame, on the XMX cores. 3x from about 40 fps gives a smooth 120.", undefined, "more than 2x needs the XeSS driver above; a little input delay.")} />
            </PanelSectionRow>
          )}
          {mode === "lsfg" && (
            <>
              <PanelSectionRow>
                <Status ok={own.owned === null ? null : own.owned && own.installed ? true : false}
                  label={own.owned === false ? "You don't own Lossless Scaling yet" : own.installed === false ? "Lossless Scaling is in your library but not installed" : "Lossless Scaling is installed"} />
                {own.owned === false && <ButtonItem layout="below" onClick={() => { if (!showLosslessInStore()) toast("Open the Steam store and search for Lossless Scaling"); }}>Get Lossless Scaling on Steam</ButtonItem>}
                {own.owned && own.installed === false && <ButtonItem layout="below" onClick={() => { if (!installLossless()) toast("Install Lossless Scaling from your Steam library"); else toast("Steam is installing Lossless Scaling"); }}>Install Lossless Scaling</ButtonItem>}
                <Note>Lossless Scaling is a paid Steam app: lsfg-vk uses the frame generation from your own copy. It's the last option, for games with no DLSS, FSR or XeSS, and runs on the regular graphics cores, not the XMX cores.</Note>
              </PanelSectionRow>
              <PanelSectionRow>
                <Status ok={!!st?.lsfg?.layer} label={st?.lsfg?.layer ? `lsfg-vk installed${st?.lsfg_version ? ` (${st.lsfg_version})` : ""}` : "lsfg-vk is not installed yet"} />
                {!st?.lsfg?.layer && <ButtonItem layout="below" disabled={busy} onClick={async () => { setBusy(true); try { const r = await lsfgInstall(); toast(r.ok ? `lsfg-vk ${r.version ?? ""} installed` : `lsfg-vk: not installed (${r.error})`); setErr(r.ok ? null : (r.error ?? "unknown error")); } finally { setBusy(false); load(); } }}>Install lsfg-vk (newest)</ButtonItem>}
              </PanelSectionRow>
              <PanelSectionRow>
                <SliderField label="Frame multiplier" value={lsfg.multiplier} min={2} max={4} step={1} showValue onChange={(v) => setLsfg({ ...lsfg, multiplier: v })}
                  description={explain("2x from a steady 40–60 fps feels smoothest. Higher = more artifacts and input delay.")} />
              </PanelSectionRow>
              <Advanced id="lsfg" label="More frame generation settings">
                <PanelSectionRow>
                  <SliderField label="Flow scale" value={lsfg.flow_scale} min={0.25} max={1} step={0.05} showValue onChange={(v) => setLsfg({ ...lsfg, flow_scale: v })}
                    description={explain("Lower = cheaper frame generation, more power left for the game; slightly softer generated frames.")} />
                  <ToggleField label="Performance mode" checked={lsfg.performance_mode} onChange={(v) => setLsfg({ ...lsfg, performance_mode: v })}
                    description={explain("Cheaper frame generation on battery.")} />
                </PanelSectionRow>
              </Advanced>
            </>
          )}
          {(mode !== "none" || installed !== "none") && (
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy || (mode === "lsfg" && !(lsfgReady || (own.owned && own.installed)))} onClick={() => apply(mode)}
                description={explain(mode === "optiscaler" ? "Installs the newest OptiScaler, plus OptiPatcher and REFramework when this game needs them. From the next game start." : mode === "lsfg" ? "Turns lsfg-vk on for this game; nothing is copied into the game. From the next game start." : "Takes ClawDeck's files out of this game and puts its originals back.")}>
                {busy ? "Working…" : mode === "none" ? "Remove from this game" : installed === mode ? "Update settings for this game" : mode === "optiscaler" ? "Set up OptiScaler (newest)" : "Turn on Lossless Scaling for this game"}
              </ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow><Problem error={err} /></PanelSectionRow>
          {mode !== "none" && <LaunchWrapper appid={app.appid} name={app.name} />}
        </>
      )}
      {plan?.blocked && <PanelSectionRow><Note>Online games with anti-cheat can ban accounts for changed game files, so ClawDeck offers nothing here. Use the game's own upscaler settings.</Note></PanelSectionRow>}
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
        <Status ok={null} label={parts.length ? parts.join(" · ") : "Nothing downloaded yet"} detail={u.optiscaler?.latest && !u.optiscaler.installed ? `Newest OptiScaler: ${u.optiscaler.latest}` : undefined} />
        <ToggleField label="Keep tools up to date automatically" checked={u.auto !== false} onChange={async (v) => { await setSetting("auto_update", v); load(); }}
          description={explain("Checks once a day and updates your games right after you close them, never while one runs.")} />
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
    return <PanelSection title="This game"><PanelSectionRow><Note>Start a game to pick its Proton, HDR and driver.</Note></PanelSectionRow></PanelSection>;
  }
  return (
    <PanelSection title={`This game: ${app.name}`}>
      <PanelSectionRow>
        <DropdownItem label="Proton version" disabled={!tools.length}
          rgOptions={[{ data: "", label: "Steam default" }, ...tools.map((t) => ({ data: t.name, label: t.display }))]}
          selectedOption={launch.proton ?? ""} description={explain("GE-Proton often fixes cutscenes and new games sooner. A different Proton may rebuild its shader cache.")} onChange={async (o) => {
            const ok = specifyCompatTool(app.appid, o.data);
            await save({ proton: o.data || null });
            toast(ok ? `${o.data || "Steam default"} for ${app.name}; takes effect next launch` : "Steam refused the change; set it in the game's Properties → Compatibility");
          }} />
      </PanelSectionRow>
      <Advanced id="graphics">
        <PanelSectionRow>
          <ToggleField label="HDR output (Proton games)" checked={!!launch.hdr} onChange={(v) => save({ hdr: v })}
            description={explain("For an HDR TV with HDR on in Display. Colours look washed out if the screen isn't in HDR.")} />
        </PanelSectionRow>
        {xess?.installed && xess.drivers.length > 1 && (
          <PanelSectionRow>
            <DropdownItem label="XeSS driver for this game" rgOptions={[
              { data: "", label: "Same as the default" }, { data: "26.3", label: "Mesa 26.3 (faster)" }, { data: "26.1", label: "Mesa 26.1 (most tested)" },
            ]} selectedOption={launch.mesa ?? ""} onChange={(o) => save({ mesa: o.data })}
              description={explain("26.1 if this game misbehaves on 26.3.")} />
          </PanelSectionRow>
        )}
        {(launch.hdr || launch.mesa) && <LaunchWrapper appid={app.appid} name={app.name} />}
      </Advanced>
    </PanelSection>
  );
};
