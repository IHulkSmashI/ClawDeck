import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { gameStatus, injectorInstall, injectorPlan, lsfgInstall, setLaunch, GameStatus, InjectorPlan } from "../api";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Note, Problem, Status } from "../components/Hint";
import { installLossless, losslessOwnership, runningApp, showLosslessInStore, Ownership } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/**
 * The upscaling and frame-generation ladder, per game (docs/DESIGN.md): the game's own XeSS through the XMX driver
 * (+ XeLL) → OptiScaler turns DLSS/FSR into XeSS on XMX → lsfg-vk for games with no upscaler at all. The panel shows
 * which tier is active and lets the user step down one if it misbehaves.
 */
const TIER: Record<string, string> = {
  native: "Native XeSS + XeLL", optiscaler: "OptiScaler → XMX", lsfg: "Lossless Scaling (shader cores)", none: "Off",
};

export const PerGameTab: FC<{ quick?: boolean }> = ({ quick }) => {
  const app = runningApp();
  const [gs, setGs] = useState<GameStatus | null>(null);
  const [p, setP] = useState<InjectorPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [own, setOwn] = useState<Ownership>({ owned: null, installed: null });
  const load = async () => {
    if (!app) return;
    const [g, plan] = await Promise.all([gameStatus(app.appid), injectorPlan(app.appid, app.name)]);
    setGs(g); setP(plan); setOwn(losslessOwnership());
  };
  useEffect(() => { load(); }, [app?.appid]);

  if (!app) {
    return (
      <PanelSection title="Per-game">
        <PanelSectionRow><Note>Start a game: this panel shows what it uses (XeSS on XMX, XeLL, OptiScaler) and lets you change it.</Note></PanelSectionRow>
      </PanelSection>
    );
  }
  const launch = gs?.launch ?? {};
  const xmxOn = launch.xmx !== false;
  const inj = gs?.injector;
  const installed = inj?.mode ?? "none";
  const plan = p?.plan;
  const native = !!inj?.native_xess || plan?.mode === "native";
  // which tier runs now: the game's own XeSS when it has it and nothing is injected; else what is installed
  const active = installed !== "none" ? installed : native ? "native" : "none";
  const xessOk = !!gs?.xess.installed && gs.xess.status.state === "ok";
  const xell = gs?.xell;
  const foreign = !!inj?.foreign_optiscaler;

  const save = async (v: Partial<typeof launch>) => {
    const r = await setLaunch(app.appid, v);
    setErr(r.ok ? null : (r.error ?? "not saved"));
    if (!r.ok) toast(`Not saved: ${r.error}`);
    load();
  };
  const useTier = async (mode: string) => {
    setBusy(true);
    try {
      const r = await injectorInstall(app.appid, mode, mode === "optiscaler" ? { multiplier: plan?.options.multiplier } : null, null, app.name);
      setErr(r.error ?? null);
      toast(r.error ? `${TIER[mode] ?? mode}: failed (${r.error})` : r.dry_run ? `Dry run: ${r.would}`
        : mode === "none" ? "Removed; the game's own files are back. Restart it."
        : mode === "optiscaler" ? `OptiScaler ${r.version ?? ""} set up${r.reframework_version ? " with REFramework" : ""}. Restart the game.`
        : "Lossless Scaling frame generation on for this game. Restart it.");
    } finally { setBusy(false); load(); }
  };
  const hook = installed === "optiscaler"
    ? `Hook active: OptiScaler ${inj?.version ?? "?"}${inj?.info?.reframework_version ? " + REFramework" : ""}${inj?.info?.optipatcher_version ? " + OptiPatcher" : ""}`
    : installed === "lsfg" ? `Hook active: lsfg-vk${inj?.lsfg_version ? ` ${inj.lsfg_version}` : ""} ${launch.lsfg?.multiplier ?? 2}x`
    : foreign ? "OptiScaler is in this game, set up by another tool (ClawDeck leaves it alone)" : "No hook: the game runs as shipped";

  return (
    <PanelSection title={`Per-game: ${app.name}`}>
      <PanelSectionRow>
        <Status ok={active === "none" ? null : true} label={`Tier: ${TIER[active]}`}
          detail={p?.found === false ? "Game folder not found in Steam's library (non-Steam games aren't supported yet)"
            : plan ? `Best for this game: ${TIER[plan.mode]}${plan.reasons[0] ? ` · ${plan.reasons[0]}` : ""}` : "Checking the game…"} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="XeSS on XMX" checked={xmxOn} disabled={!gs?.xess.installed}
          description={explain(gs?.xess.installed ? `The patched driver runs this game's XeSS on the XMX cores (driver ${xessOk ? "loads: " + (gs.xess.status.mode ?? "") : "not loaded: " + (gs?.xess.status.line ?? "no check yet")}). Off = stock driver for this game only.`
            : "Install the XeSS XMX driver in Setup first.", undefined, quick ? undefined : "from the next launch; the first one compiles kernels for a minute or two, once.")}
          onChange={(v) => save({ xmx: v })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="XeLL driver mode" checked={!!launch.xell} disabled={!gs?.xess.installed}
          description={explain(gs?.xess.xell_capable ? "Intel's latency reduction gets real GPU timings from the shim instead of pacing blind (the fork's XeLL driver mode)."
            : "Needs the fork build of the XeSS driver (Setup → Install XeSS XMX, once the fork publishes a release); with upstream's build XeLL stays in cross-vendor mode.",
            undefined, quick ? undefined : "logs what XeLL did into the game's prefix; from the next launch.")}
          onChange={(v) => save({ xell: v })} />
        <Status ok={xell?.mode === "driver" ? true : xell?.mode === "none" && launch.xell ? false : null}
          label={xell ? `XeLL: ${xell.mode === "driver" ? "driver mode" : xell.mode === "cross-vendor" ? "cross-vendor mode" : xell.mode === "off" ? "off" : "not seen"}` : "XeLL: …"}
          detail={xell?.detail} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="OptiScaler injection" checked={installed === "optiscaler"} disabled={busy || foreign || !!plan?.blocked || (installed !== "optiscaler" && plan?.mode !== "optiscaler" && plan?.alternative !== "optiscaler")}
          description={explain(plan?.blocked ? "Anti-cheat game: changed files can get accounts banned, so nothing is injected."
            : plan?.mode === "optiscaler" || plan?.alternative === "optiscaler" ? `Swaps the game's DLSS/FSR for XeSS on the XMX cores and its frame generation for XeSS FG (${plan?.options.multiplier ?? 2}x).`
            : plan?.mode === "native" ? "This game ships XeSS: use it in the game's settings, nothing to inject." : "This game has no DLSS, FSR or XeSS for OptiScaler to work with.",
            undefined, quick ? undefined : "needs Borderless or Fullscreen Windowed; a little input delay.")}
          onChange={(v) => useTier(v ? "optiscaler" : "none")} />
        <Status ok={installed === "none" ? null : true} label={hook} />
        <Problem error={err} />
      </PanelSectionRow>
      {!quick && plan && !plan.blocked && !foreign && (
        <PanelSectionRow>
          <DropdownItem label="Use for this game" selectedOption={installed} disabled={busy} rgOptions={[
            ...(plan.mode === "optiscaler" || plan.alternative === "optiscaler" ? [{ data: "optiscaler", label: TIER.optiscaler }] : []),
            { data: "lsfg", label: TIER.lsfg }, { data: "none", label: plan.mode === "native" ? "Off (native XeSS)" : TIER.none },
          ]} onChange={(o) => { if (o.data !== installed) useTier(o.data); }}
            description={explain("Step down a tier if one misbehaves. Lossless Scaling is the last option: it works with any game, on the regular shader cores, and needs your own copy from Steam.")} />
          {own.owned === false && <ButtonItem layout="below" onClick={() => { if (!showLosslessInStore()) toast("Open the Steam store and search for Lossless Scaling"); }}>Get Lossless Scaling on Steam</ButtonItem>}
          {own.owned && own.installed === false && <ButtonItem layout="below" onClick={() => { if (!installLossless()) toast("Install Lossless Scaling from your Steam library"); else toast("Steam is installing Lossless Scaling"); }}>Install Lossless Scaling</ButtonItem>}
          {own.owned && own.installed && !inj?.lsfg?.layer && (
            <ButtonItem layout="below" disabled={busy} onClick={async () => { setBusy(true); try { const r = await lsfgInstall(); toast(r.ok ? `lsfg-vk ${r.version ?? ""} installed` : `lsfg-vk: not installed (${r.error})`); setErr(r.ok ? null : (r.error ?? "unknown error")); } finally { setBusy(false); load(); } }}>Install lsfg-vk (newest)</ButtonItem>
          )}
        </PanelSectionRow>
      )}
      {!quick && <LaunchWrapper appid={app.appid} name={app.name} />}
    </PanelSection>
  );
};
