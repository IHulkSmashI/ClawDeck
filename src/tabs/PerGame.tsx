import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { gameStatus, setLaunch, FsrRoute, GameStatus, Launch, OptiPreset } from "../api";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Note, Problem, Status } from "../components/Hint";
import { getLaunchOptions, hasFgmod, hasWrapper, runningApp, setLaunchOptions, withFgmod, withoutFgmod, withWrapper } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

const FG_OPTIONS = [
  { data: 0, label: "Game's choice" },
  { data: 2, label: "2x" }, { data: 3, label: "3x" }, { data: 4, label: "4x" },
  { data: 5, label: "5x (experimental)" }, { data: 6, label: "6x (experimental)" },
  { data: 7, label: "7x (experimental, more lag)" }, { data: 8, label: "8x (experimental, more lag)" },
];
const FSR_OPTIONS: { data: FsrRoute; label: string }[] = [
  { data: "off", label: "Off (the game's own FSR)" },
  { data: "xess", label: "FSR on XeSS (upscaling + frame gen)" },
  { data: "fg", label: "FSR frame gen on XeSS only" },
  { data: "inject", label: "Add XeSS frame gen to FSR upscaling" },
];
const OPTI_OPTIONS: { data: OptiPreset; label: string }[] = [
  { data: "off", label: "Decky Framegen's setting" },
  { data: "xess", label: "XeSS on XMX (recommended)" },
  { data: "fsr4", label: "FSR 4 INT8 (experimental on Intel)" },
];

/**
 * The running game's XeSS on the XMX cores (xess-xmx-linux's per-game launch options, one tap each) and OptiScaler
 * through Bazzite's Decky Framegen with Intel presets. Every choice reaches the game through ClawDeck's launch wrapper
 * (an environment file per game), so the first change adds the wrapper to the game's launch options.
 */
export const PerGameTab: FC<{ quick?: boolean }> = ({ quick }) => {
  const app = runningApp();
  const [gs, setGs] = useState<GameStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [opts, setOpts] = useState<string | null>(null);    // the game's Steam launch options; null = unreadable
  const load = async () => { if (app) { setGs(await gameStatus(app.appid)); setOpts(await getLaunchOptions(app.appid)); } };
  useEffect(() => { load(); }, [app?.appid]);

  if (!app) {
    return (
      <PanelSection title="Per-game">
        <PanelSectionRow><Note>Start a game: this panel sets its XeSS on the XMX cores (frame generation, FSR games on XeSS, low latency) and OptiScaler, and shows what each did.</Note></PanelSectionRow>
      </PanelSection>
    );
  }
  const launch: Launch = gs?.launch ?? {};
  const installed = !!gs?.xess.installed;
  const scope = gs?.xmx_scope;
  const perGame = !!scope && !scope.session_wide;            // the XMX driver loads per game (xmxscope.py), the default
  const xmxOn = perGame ? (launch.xmx ?? !!scope?.auto) : launch.xmx !== false;
  const xessOk = installed && gs!.xess.status.state === "ok";
  const newPkg = !!gs?.xess.low_latency;                     // 20261104+: low latency, FSR on XeSS, XESS_FG to 8x
  const wrapped = hasWrapper(opts);
  const fg = gs?.framegen;
  const fgmodHere = hasFgmod(opts);
  const lat = gs?.latency;

  const save = async (v: Partial<Launch>) => {
    const r = await setLaunch(app.appid, v);
    setErr(r.ok ? null : (r.error ?? "not saved"));
    if (!r.ok) { toast(`Not saved: ${r.error}`); load(); return; }
    const needs = Object.keys(r.env ?? {}).length > 0;      // anything to export: the wrapper must run in front of the game
    if (needs && !wrapped && opts !== null && gs?.wrapper) {
      const added = setLaunchOptions(app.appid, withWrapper(opts, gs.wrapper.replace(/ %command%$/, "")));
      toast(added ? `Added ClawDeck to ${app.name}'s launch options so this reaches the game. Restart it.` : `Saved, but Steam refused the launch options: add ClawDeck by hand (below)`);
    } else {
      toast(`Saved for ${app.name}: from the next launch`);
    }
    load();
  };
  const setFgmod = (on: boolean) => {
    if (opts === null || !fg?.fgmod) return;
    let next = on ? withFgmod(opts, fg.fgmod) : withoutFgmod(opts);
    if (on && !hasWrapper(next) && gs?.wrapper) next = withWrapper(next, gs.wrapper.replace(/ %command%$/, ""));
    const ok = setLaunchOptions(app.appid, next);
    toast(ok ? (on ? `OptiScaler added to ${app.name}: restart it, then pick DLSS or FSR in its menu` : `OptiScaler removed from ${app.name}'s launch options`) : "Steam refused the launch options");
    load();
  };

  return (
    <PanelSection title={`Per-game: ${app.name}`}>
      <PanelSectionRow>
        <ToggleField label="XeSS on XMX" checked={xmxOn} disabled={!installed}
          description={explain(installed ? (perGame ? `Automatic: the XMX driver loads only in games that ship XeSS or OptiScaler (${scope?.auto ? "this one does" : "this one doesn't"}); Steam's own screen and other games keep Bazzite's driver. Switch it to force it on or off for this game.`
              : "On for every game once the driver is installed: the game's XeSS (or OptiScaler's) runs on the XMX cores. Off = the stock driver for this game only, if it misbehaves.")
            : "Install the XeSS XMX driver in Setup first; from then it is on for every game.", undefined, quick ? undefined : "from the next launch; the first one compiles kernels for a minute or two, once.")}
          onChange={(v) => save({ xmx: perGame && v === !!scope?.auto ? null : v } as any)} />
        <Status ok={gs ? (xmxOn && xessOk ? true : (xmxOn && installed) || (!xmxOn && !wrapped) ? false : null) : null}
          label={!gs ? "XeSS: …" : !installed ? "XeSS: driver not installed (Setup)"
            : xmxOn ? (xessOk ? `XeSS on XMX (${perGame ? (launch.xmx == null ? "automatic" : "on for this game") : "default"}): ${gs.xess.status.mode ?? "patched driver"}`
              : gs.xess.status.state === "unknown" ? "XeSS on XMX from your next log-in (the session check hasn't run yet)" : "XeSS: patched driver not loading, DP4a fallback")
            : perGame ? (launch.xmx === false ? "XeSS: Bazzite's driver for this game (switched off)" : "Bazzite's driver: this game ships no XeSS or OptiScaler")
              : wrapped ? "XeSS: stock driver for this game (DP4a)" : "XMX off is saved, but ClawDeck isn't in this game's launch options yet, so the game still uses XMX"}
          detail={xmxOn && gs?.xess.status.state === "failed" ? gs.xess.status.line ?? undefined : undefined} />
      </PanelSectionRow>

      <PanelSectionRow>
        <DropdownItem label="Frame generation" layout="below" childrenContainerWidth="max" disabled={!installed} rgOptions={FG_OPTIONS.filter((o) => newPkg || o.data <= 4)}
          selectedOption={launch.fg ?? 0} onChange={(o) => save({ fg: o.data || null } as any)}
          description={explain("How many frames XeSS frame generation shows per rendered one, whatever the game's menu offers (Cyberpunk 2077 offers only 2x). Frame generation itself stays on or off in the game.",
            quick ? undefined : "fill a 120 Hz screen from 30–40 rendered fps; keep the game's frame limit on.", quick ? undefined : "5x and up are beyond what Intel supports; 7x and 8x add noticeable lag.")} />
      </PanelSectionRow>

      <PanelSectionRow>
        <ToggleField label="Low latency" checked={launch.low_latency !== false} disabled={!installed || !newPkg}
          description={explain(newPkg ? "With XeSS frame generation on, starts each frame so it reaches the graphics chip just as it gets free, instead of queueing. Roughly halves the input lag in a GPU-limited game at the same frame rate (xess-xmx-linux measured 69 → 35 ms). On by default."
            : "Needs XeSS XMX 20261104 or newer: Setup → Update XeSS XMX.", undefined, quick ? undefined : "turn it off only if this game loses frame rate with it on.")}
          onChange={(v) => save({ low_latency: v ? null : false } as any)} />
        {newPkg && lat && <Status ok={lat.mode === "pacing" || lat.mode === "idle" ? true : lat.mode === "off" ? false : null}
          label={`Low latency: ${({ pacing: "working", idle: "on, nothing to remove", "no-fg": "waiting for frame generation", off: "off", none: "not seen yet" } as const)[lat.mode]}`}
          detail={lat.detail} />}
      </PanelSectionRow>

      {!quick && (
        <PanelSectionRow>
          <ToggleField label="Latest XeSS libraries" checked={!!launch.xess_latest} disabled={!installed}
            description={explain("Runs the game with Intel's current XeSS (SDK 3.0.2) instead of the older copy it ships; its files are swapped while it runs and put back when it exits.",
              "a game with XeSS older than 2.0.2.68 shows XeSS as DP4a or hides XeSS frame generation (Cyberpunk 2077).")}
            onChange={(v) => save({ xess_latest: v || null } as any)} />
          <DropdownItem label="FSR games" layout="below" childrenContainerWidth="max" disabled={!installed || !newPkg} rgOptions={FSR_OPTIONS} selectedOption={launch.fsr ?? "off"}
            onChange={(o) => save({ fsr: o.data === "off" ? null : o.data } as any)}
            description={explain(newPkg ? "For a game with FSR 3.1+ and no XeSS: its FSR runs on XeSS on the XMX cores, with multi-frame generation. Pick FSR in the game's menu as usual."
              : "Needs XeSS XMX 20261104 or newer: Setup → Update XeSS XMX.",
              "a game that has XeSS should use its own XeSS: this route can flicker more.", "FSR 2 or FSR built into the game: nothing changes.")} />
        </PanelSectionRow>
      )}

      <PanelSectionRow>
        <Status ok={fg?.fgmod_ready ? (fgmodHere ? true : null) : fg?.installed ? false : null}
          label={!fg ? "OptiScaler: …" : !fg.installed ? "OptiScaler: Decky Framegen not installed (Setup)"
            : !fg.fgmod_ready ? "OptiScaler: open Decky Framegen once and tap Setup OptiScaler Mod"
              : fgmodHere ? `OptiScaler ${fg.optiscaler ?? ""} in this game's launch options` : `OptiScaler ${fg.optiscaler ?? ""} ready, not used in this game`}
          detail={gs?.optiscaler.present ? `Installed in the game folder${gs.optiscaler.version ? ` (${gs.optiscaler.version})` : ""}${xmxOn && xessOk ? ": its XeSS runs on XMX through the driver above" : ""}.` : undefined} />
        {fg?.fgmod_ready && opts !== null && (
          <ButtonItem layout="below" onClick={() => setFgmod(!fgmodHere)}
            description={explain("For DLSS or FSR games without XeSS: Decky Framegen's OptiScaler takes over the upscaler. Pick DLSS (or FSR) in the game's menu; ClawDeck's preset below decides what OptiScaler uses.",
              undefined, quick ? undefined : "the game's folder gets OptiScaler's files at launch (Decky Framegen restores them when removed).")}>
            {fgmodHere ? "Stop using OptiScaler in this game" : "Use OptiScaler in this game"}
          </ButtonItem>
        )}
      </PanelSectionRow>
      {fgmodHere && (
        <PanelSectionRow>
          <DropdownItem label="OptiScaler upscaler" layout="below" childrenContainerWidth="max" rgOptions={OPTI_OPTIONS} selectedOption={launch.opti ?? "off"}
            onChange={(o) => save({ opti: o.data === "off" ? null : o.data } as any)}
            description={explain("XeSS runs on the XMX cores and is the cheapest here (about 1.5 ms per frame on the B390). FSR 4 INT8 looks good but costs more on Intel and is experimental: xess-xmx-linux stopped developing FSR 4 on XMX (no real gain, problems with multi-frame generation).")} />
          <ToggleField label="OptiScaler frame generation: XeSS" checked={!!launch.opti_xefg}
            description={explain("OptiScaler's frame generation output becomes XeSS frame generation, which runs on XMX and takes the multiplier above.",
              undefined, quick ? undefined : "if no frames are generated, open OptiScaler's menu in the game (Insert) and check the frame generation input.")}
            onChange={(v) => save({ opti_xefg: v || null } as any)} />
        </PanelSectionRow>
      )}
      <PanelSectionRow><Problem error={err} /></PanelSectionRow>
      {!quick && <LaunchWrapper key={wrapped ? "on" : "off"} appid={app.appid} name={app.name} />}
    </PanelSection>
  );
};
