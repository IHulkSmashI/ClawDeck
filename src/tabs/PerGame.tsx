import { ButtonItem, DropdownItem, Field, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { gameApplyRecommended, gameRecommend, gameSetOpti, gameStatus, gameUndoRecommended, setLaunch, FsrRoute, GameStatus, Launch, OptiPreset, Recommendation } from "../api";
import { confirmUntested } from "../components/confirm";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Note, Problem, Status, useExplanations } from "../components/Hint";
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
const OPTI_FG_OPTIONS: { data: string; label: string }[] = [
  { data: "off", label: "Off" },
  { data: "dlssg", label: "From the game's DLSS frame generation" },
  { data: "fsrfg", label: "From the game's FSR frame generation" },
  { data: "upscaler", label: "Any game (OptiScaler's own, may glitch the HUD)" },
];
const OPTI_OPTIONS: { data: OptiPreset; label: string }[] = [
  { data: "off", label: "Decky Framegen's setting" },
  { data: "xess", label: "XeSS on XMX (recommended)" },
  { data: "fsr4_xmx", label: "FSR 4.1.1 on XMX (nitec's build)" },
  { data: "fsr4_xmx_lite", label: "FSR 4.1.1 on XMX, Lite (faster)" },
];

/**
 * The running game's XeSS on the XMX cores (xess-xmx-linux's per-game launch options, one tap each) and OptiScaler
 * through Bazzite's Decky Framegen with Intel presets. Every choice reaches the game through ClawDeck's launch wrapper
 * (an environment file per game), so the first change adds the wrapper to the game's launch options.
 */
export const PerGameTab: FC<{ quick?: boolean; pick?: { appid: string; name: string } | null; embedded?: boolean }> = ({ quick, pick, embedded }) => {
  useExplanations();
  const app = pick ?? runningApp();
  const [gs, setGs] = useState<GameStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [opts, setOpts] = useState<string | null>(null);    // the game's Steam launch options; null = unreadable
  const load = async () => { if (app) { setGs(await gameStatus(app.appid)); setOpts(await getLaunchOptions(app.appid)); } };
  useEffect(() => { load(); }, [app?.appid]);

  if (!app) {
    return (
      <PanelSection title="Per-game">
        <PanelSectionRow><Note>Start a game, or set one up before you play: open the full view (gear at the top right) → Games. This panel sets the game's XeSS on the XMX cores, OptiScaler (FSR 4, frame generation) and the recommended setup, and shows what each did.</Note></PanelSectionRow>
      </PanelSection>
    );
  }
  const launch: Launch = gs?.launch ?? {};
  const installed = !!gs?.xess.installed;
  const scope = gs?.xmx_scope;
  const perGame = !!scope && !scope.session_wide;            // the XMX driver loads per game (xmxscope.py), the default
  const xmxOn = perGame ? (launch.xmx ?? (!!scope?.auto || !!scope?.every_game)) : launch.xmx !== false;
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
  const [rec, setRec] = useState<Recommendation | null>(null);
  useEffect(() => { gameRecommend(app.appid).then(setRec).catch(() => setRec(null)); }, [app.appid, gs]);
  const applyRec = async (allow = false) => {
    if (!rec) return;
    if (rec.optiscaler && fgmodHere) { toast("This game uses Decky Framegen's OptiScaler: tap \"Stop using Decky Framegen here\" below first"); return; }
    toast(rec.optiscaler ? `${app.name}: setting up OptiScaler (the first time downloads it, about 55 MB)…` : `${app.name}: saving…`);
    const r = await gameApplyRecommended(app.appid, allow);
    if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("REFramework", () => applyRec(true)); return; }
    if (!r.ok) { setErr(r.error ?? "not applied"); toast(`Recommended setup: not applied (${r.error})`); return; }
    let next = opts ?? "";
    if (Object.keys(r.env ?? {}).length && !hasWrapper(next) && gs?.wrapper) next = withWrapper(next, gs.wrapper.replace(/ %command%$/, ""));
    const ok = next === (opts ?? "") || setLaunchOptions(app.appid, next);
    toast(ok ? `${app.name}: recommended setup saved. ${r.next ?? ""} Restart the game.` : "Saved, but Steam refused the launch options");
    setErr(null); load();
  };
  const undoRec = async () => {
    const r = await gameUndoRecommended(app.appid);
    if (opts !== null && fgmodHere) setLaunchOptions(app.appid, withoutFgmod(opts));
    toast(r.ok ? `${app.name}: back to how it was (ClawDeck's OptiScaler and REFramework taken out, the game's own files put back)` : `Not undone: ${r.error}`);
    load();
  };
  const ours = gs?.opti_owner === "clawdeck";
  const setOpti = async (v: Partial<Launch>) => {
    const r = await gameSetOpti(app.appid, v);
    setErr(r.ok ? null : (r.error ?? "not saved"));
    toast(r.ok ? `${app.name}: OptiScaler updated, from the next launch` : `Not saved: ${r.error}`);
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
    <PanelSection title={embedded ? undefined : `Per-game: ${app.name}`}>
      {rec && (
        <PanelSectionRow>
          <Status ok={rec.applied ? true : null}
            label={rec.applied ? "Recommended setup: in use" : rec.route === "own_xess" ? "Recommended: the game's own XeSS"
              : rec.route === "re_engine" ? "Recommended: OptiScaler + REFramework (Capcom)" : rec.route === "fsr_on_xess" ? "Recommended: FSR on XeSS (XMX)"
              : "Recommended: OptiScaler with XeSS on XMX"}
            detail={explain(rec.reasons.join(" · ") + (rec.known ? "" : " (not in the per-game list: ClawDeck's Intel default)"))} />
          {rec.route !== "own_xess" && (
            <ButtonItem layout="below" onClick={() => (rec.applied ? undoRec() : applyRec(false))}
              description={explain(rec.applied ? "Takes out what the recommended setup added (ClawDeck's OptiScaler, REFramework, the launch settings) and puts the game's own files back." : "One tap: the settings above, from OptiScaler's own per-game list and what was tested on the Claw.",
                undefined, quick ? undefined : rec.optiscaler ? "OptiScaler's files go next to the game's exe (any file of the game they replace is kept and comes back with Undo); the first time downloads OptiScaler (55 MB). From the next launch." : "takes effect from the next launch.")}>
              {rec.applied ? "Undo the recommended setup" : "Use the recommended setup"}
            </ButtonItem>
          )}
        </PanelSectionRow>
      )}
      {(ours || fgmodHere || gs?.opti_owner === "decky-framegen" || gs?.opti_owner === "other") && (
        <PanelSectionRow>
          <Status ok={ours ? true : null}
            label={ours ? "OptiScaler: ClawDeck's, in this game" : fgmodHere || gs?.opti_owner === "decky-framegen"
              ? "OptiScaler: Decky Framegen's, in this game" : "OptiScaler from somewhere else is in this game"}
            detail={explain(ours ? `Upscaler and frame generation below; the game's own files are kept and come back with Undo.${xmxOn && xessOk ? " Its XeSS runs on XMX through the driver above." : ""}`
              : fgmodHere || gs?.opti_owner === "decky-framegen"
                ? "Decky Framegen manages OptiScaler here. ClawDeck's upscaler presets still reach it, but the recommended setup, REFramework and its own OptiScaler wait until Decky Framegen is removed from this game (below, then Decky Framegen → Unpatch)."
                : "OptiScaler files ClawDeck didn't put there (OptiClick's from a Windows install, or by hand). ClawDeck leaves them alone; remove OptiScaler.ini and its DLL to let ClawDeck set the game up.")} />
          {fgmodHere && opts !== null && (
            <ButtonItem layout="below" onClick={() => setFgmod(false)}>Stop using Decky Framegen here</ButtonItem>
          )}
        </PanelSectionRow>
      )}
      {(ours || fgmodHere) && (
        <PanelSectionRow>
          <DropdownItem label="OptiScaler upscaler" layout="below" childrenContainerWidth="max" rgOptions={OPTI_OPTIONS.filter((o) => ours ? o.data !== "off" : true)} selectedOption={launch.opti ?? (ours ? "xess" : "off")}
            onChange={(o) => {
              if (String(o.data).startsWith("fsr4_xmx") && gs?.fsr4_xmx && !gs.fsr4_xmx.ready) {
                toast(`FSR 4 on XMX: put nitec's files in ${gs.fsr4_xmx.dir} first (missing: ${gs.fsr4_xmx.missing.join(", ")})`); return;
              }
              const v = { opti: o.data === "off" ? null : o.data } as any;
              ours ? setOpti(v) : save(v);
            }}
            description={explain("XeSS runs on the XMX cores and is the cheapest here (about 1.5 ms per frame on the B390). FSR 4.1.1 on XMX is nitec's build of AMD's FSR 4 for Intel's matrix cores: the best image of the FSR options; Lite trades a little image quality for speed. It needs nitec's files in ~/fsr4-xmx (from the OptiScaler Discord) and is experimental. Every choice runs on the XMX cores.",
              undefined, quick ? undefined : "FSR 4 on XMX: the files go into the game folder and come out again when you pick something else.")} />
          <DropdownItem label="Frame generation source (XeSS on XMX)" layout="below" childrenContainerWidth="max" rgOptions={OPTI_FG_OPTIONS}
            selectedOption={launch.opti_fg ?? (launch.opti_xefg ? "dlssg" : "off")}
            onChange={(o) => { const x = { opti_fg: o.data, opti_xefg: null } as any; ours ? setOpti(x) : save(x); }}
            description={explain("Where OptiScaler's generated frames come from; they always come out as XeSS frame generation on the XMX cores, with the multiplier above. Use the game's own frame generation when it has one (turn it on in the game's menu); \"Any game\" makes frames for games without it.",
              undefined, quick ? undefined : "frame generation adds a little input lag; low latency above keeps it down.")} />
        </PanelSectionRow>
      )}
      {(ours || fgmodHere) && (quick ? <Advanced id="ingame" label="How to use it in the game">{<PanelSectionRow>
          <Field label="In the game" bottomSeparator="none" />
          <ol style={{ margin: "0 0 4px 18px", padding: 0, fontSize: 12, lineHeight: "18px", color: "#b8bcbf" }}>
            <li>Restart the game if it was running when you changed something here.</li>
            <li>Graphics settings: set the upscaler to <b>DLSS</b> (or FSR if the game has no DLSS), at the quality you like. OptiScaler swaps it for {(launch.opti ?? "xess").startsWith("fsr4_xmx") ? "FSR 4.1.1 on XMX" : "XeSS on XMX"}.</li>
            {(launch.opti_fg === "dlssg" || launch.opti_fg === "fsrfg") && <li>Turn on the game's <b>{launch.opti_fg === "dlssg" ? "DLSS" : "FSR"} Frame Generation</b>.</li>}
            <li>Check it worked: press <b>Insert</b> for OptiScaler's menu (it shows the upscaler in use); press it again to close.</li>
            <li>No keyboard? Steam button → Controller settings → Edit layout → pick a back button (M1 or M2) → Keyboard → <b>Insert</b>. Bind it in this game's layout only.</li>
          </ol>
        </PanelSectionRow>}</Advanced> : <PanelSectionRow>
          <Field label="In the game" bottomSeparator="none" />
          <ol style={{ margin: "0 0 4px 18px", padding: 0, fontSize: 12, lineHeight: "18px", color: "#b8bcbf" }}>
            <li>Restart the game if it was running when you changed something here.</li>
            <li>Graphics settings: set the upscaler to <b>DLSS</b> (or FSR if the game has no DLSS), at the quality you like. OptiScaler swaps it for {(launch.opti ?? "xess").startsWith("fsr4_xmx") ? "FSR 4.1.1 on XMX" : "XeSS on XMX"}.</li>
            {(launch.opti_fg === "dlssg" || launch.opti_fg === "fsrfg") && <li>Turn on the game's <b>{launch.opti_fg === "dlssg" ? "DLSS" : "FSR"} Frame Generation</b>.</li>}
            <li>Check it worked: press <b>Insert</b> for OptiScaler's menu (it shows the upscaler in use); press it again to close.</li>
            <li>No keyboard? Steam button → Controller settings → Edit layout → pick a back button (M1 or M2) → Keyboard → <b>Insert</b>. Bind it in this game's layout only.</li>
          </ol>
        </PanelSectionRow>)}
      {gs?.drive?.slow && (
        <PanelSectionRow>
          <Status ok={false} label={`Installed on ${gs.drive.kind}${gs.drive.sd ? " (SD card)" : ""}: expect slower loads and pop-in`}
            detail={explain(`Linux reads ${gs.drive.kind} through a slower driver, so this game streams its textures and shaders slower than on Windows. For the best result, move it to a Linux-formatted drive: Steam → game → Properties → Installed Files → Move install folder. An SD card formatted by Steam in Game Mode works.`)} />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        {!quick && <ToggleField label="XeSS on XMX (switch off only to troubleshoot)" checked={xmxOn} disabled={!installed}
          description={explain(installed ? (perGame ? (scope?.every_game ? "Setup has the XMX driver on for every game: whatever XeSS, XeSS frame generation or XeLL this game has runs on the XMX cores. Switch it off here only if this game misbehaves or loses speed." : `Automatic: the XMX driver loads in games that ship XeSS or OptiScaler, or are set up for FSR on XeSS (${scope?.auto ? "this one is" : "this one isn't"}); Steam's own screen keeps Bazzite's driver. Setup can turn it on for every game.`)
              : "On for every game once the driver is installed: the game's XeSS (or OptiScaler's) runs on the XMX cores. Off = the stock driver for this game only, if it misbehaves.")
            : "Install the XeSS XMX driver in Setup first; from then it is on for every game.", undefined, quick ? undefined : "from the next launch; the first one compiles kernels for a minute or two, once.")}
          onChange={(v) => save({ xmx: perGame && v === (!!scope?.auto || !!scope?.every_game) ? null : v } as any)} />}
        <Status ok={gs ? (xmxOn && xessOk ? true : (xmxOn && installed) || (!xmxOn && !wrapped) ? false : null) : null}
          label={!gs ? "XeSS: …" : !installed ? "XeSS: driver not installed (Setup)"
            : xmxOn ? (xessOk ? `XeSS runs on XMX (${perGame ? (launch.xmx == null ? "automatic" : "switched on for this game") : "default"})`
              : gs.xess.status.state === "unknown" ? "XeSS on XMX from your next log-in (the session check hasn't run yet)" : "XeSS: patched driver not loading, DP4a fallback")
            : perGame ? (launch.xmx === false ? "XeSS: Bazzite's driver for this game (switched off)" : "Bazzite's driver: this game ships no XeSS or OptiScaler")
              : wrapped ? "XeSS: stock driver for this game (DP4a)" : "XMX off is saved, but ClawDeck isn't in this game's launch options yet, so the game still uses XMX"}
          detail={xmxOn && gs?.xess.status.state === "failed" ? gs.xess.status.line ?? undefined : undefined} />
      </PanelSectionRow>

      <PanelSectionRow>
        <DropdownItem label="Frame generation: frames per rendered one" layout="below" childrenContainerWidth="max" disabled={!installed} rgOptions={FG_OPTIONS.filter((o) => newPkg || o.data <= 4)}
          selectedOption={launch.fg ?? 0} onChange={(o) => save({ fg: o.data || null } as any)}
          description={explain("How many frames XeSS frame generation shows per rendered one, whatever the game's menu offers (Cyberpunk 2077 offers only 2x). Frame generation itself stays on or off in the game.",
            quick ? undefined : "fill a 120 Hz screen from 30–40 rendered fps; keep the game's frame limit on.", quick ? undefined : "5x and up are beyond what Intel supports; 7x and 8x add noticeable lag.")} />
      </PanelSectionRow>

      <PanelSectionRow>
        {!newPkg && <Status ok={false} label="Low latency (XeLL) needs XeSS XMX 20261104 or newer" detail="Setup → Update XeSS XMX." />}
        {newPkg && lat && <Status ok={lat.mode === "pacing" || lat.mode === "idle" ? true : lat.mode === "off" ? false : null}
          label={`Low latency (XeLL, always on): ${({ pacing: "working", idle: "on, nothing to remove", "no-fg": "waiting for frame generation", off: "off", none: "not seen yet" } as const)[lat.mode]}`}
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

      <PanelSectionRow><Problem error={err} /></PanelSectionRow>
      {!quick && <LaunchWrapper key={wrapped ? "on" : "off"} appid={app.appid} name={app.name} />}
    </PanelSection>
  );
};
