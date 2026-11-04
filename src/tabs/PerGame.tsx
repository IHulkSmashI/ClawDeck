import { ButtonItem, DropdownItem, Field, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { gameApplyRecommended, gameChoose, gameRecommend, gameStatus, gameUndoRecommended, setLaunch, FsrRoute, GameStatus, Launch, Recommendation } from "../api";
import { confirmUntested } from "../components/confirm";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Note, Problem, Status, useExplanations } from "../components/Hint";
import { getLaunchOptions, hasFgmod, hasWrapper, runningApp, setLaunchOptions, withFgmod, withoutFgmod, withWrapper } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

const FSR_OPTIONS: { data: FsrRoute; label: string }[] = [
  { data: "off", label: "Off (the game's own FSR)" },
  { data: "xess", label: "FSR on XeSS (upscaling + frame gen)" },
  { data: "fg", label: "FSR frame gen on XeSS only" },
  { data: "inject", label: "Add XeSS frame gen to FSR upscaling" },
];
const UPSCALERS = [
  { data: "own", label: "The game's own (no OptiScaler)" },
  { data: "xess", label: "XeSS 2 on XMX" },
  { data: "fsr4_xmx", label: "FSR 4.1.1 on XMX" },
  { data: "fsr4_xmx_lite", label: "FSR 4.1.1 Lite on XMX (faster)" },
];
const FRAMEGENS = [
  { data: 0, label: "Off" },
  { data: 2, label: "2x: XeSS frame generation on XMX" },
  { data: 3, label: "3x: XeSS frame generation on XMX" },
  { data: 4, label: "4x: XeSS frame generation on XMX" },
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
  const choose = async (upscaler: string, fg: number, allow = false) => {
    if (upscaler !== "own" && !ours) toast(`${app.name}: setting up OptiScaler (the first time downloads it, about 55 MB)…`);
    const r = await gameChoose(app.appid, upscaler, fg, allow);
    if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("REFramework", () => choose(upscaler, fg, true)); return; }
    setErr(r.ok ? null : (r.error ?? "not saved"));
    if (r.ok && Object.keys(r.env ?? {}).length && opts !== null && !hasWrapper(opts) && gs?.wrapper) {
      setLaunchOptions(app.appid, withWrapper(opts, gs.wrapper.replace(/ %command%$/, "")));
    }
    const up = UPSCALERS.find((u) => u.data === upscaler)?.label ?? upscaler;
    toast(r.ok ? `${app.name}: ${up}${fg ? `, frame generation ${fg}x` : ""}. ${upscaler === "own" ? "" : "In the game's menu pick DLSS (or FSR). "}From the next launch.` : `Not saved: ${r.error}`);
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
      <PanelSectionRow>
        <Field label="In this game now" bottomSeparator="none" />
        <div style={{ fontSize: 12, lineHeight: "18px", color: "#b8bcbf", padding: "0 0 4px" }}>
          <div>{ours ? "✓" : "–"} OptiScaler: {ours ? `ClawDeck's, as ${gs?.files?.dll ?? "?"} (${UPSCALERS.find((u) => u.data === (gs?.files?.preset === "fsr4" ? "fsr4_xmx" : gs?.files?.preset))?.label ?? gs?.files?.preset ?? "XeSS 2 on XMX"})`
            : fgmodHere || gs?.opti_owner === "decky-framegen" ? "Decky Framegen's (ClawDeck leaves it alone)" : gs?.opti_owner === "other" ? "put there by another tool or by hand" : "not in the game"}</div>
          {gs?.files?.reframework && <div>✓ REFramework (dinput8.dll)</div>}
          <div>{wrapped ? "✓" : "–"} Launch options: {wrapped ? "ClawDeck is in them" : "ClawDeck not added yet (added when you pick below)"}</div>
          {!quick && opts !== null && <div style={{ wordBreak: "break-all", opacity: 0.8 }}>{opts || "(empty)"}</div>}
          <div>{xmxOn && xessOk ? "✓" : "–"} XMX driver: {xmxOn ? (xessOk ? "on for this game" : "on, but the driver check hasn't passed") : "off for this game"}</div>
          <div>{launch.fg ? `✓ Frame generation ${launch.fg}x (XeSS on XMX)` : "– Frame generation off"} · XeLL low latency always on</div>
        </div>
      </PanelSectionRow>
      {!fgmodHere && gs?.opti_owner !== "decky-framegen" && gs?.opti_owner !== "other" && (
        <PanelSectionRow>
          <DropdownItem label="Upscaler" layout="below" childrenContainerWidth="max" rgOptions={UPSCALERS}
            selectedOption={ours ? (launch.opti === "fsr4" ? "fsr4_xmx" : launch.opti ?? "xess") : "own"}
            onChange={(o) => choose(String(o.data), launch.fg ?? 0)}
            description={explain("What the game's upscaling runs as. XeSS 2 and FSR 4.1.1 run on the XMX cores through OptiScaler, which takes the game's DLSS or FSR (pick that in the game's menu). FSR 4.1.1 is nitec's build of AMD's FSR 4 for Intel's matrix cores: the best image; Lite is faster for a little less detail; it needs nitec's files in ~/fsr4-xmx. \"The game's own\" leaves the game alone (a game's own XeSS still runs on XMX).",
              undefined, quick ? undefined : "OptiScaler's files go next to the game's exe, the game's own files are kept and come back when you pick \"The game's own\". The first time downloads OptiScaler (55 MB).")} />
          <DropdownItem label="Frame generation" layout="below" childrenContainerWidth="max" rgOptions={FRAMEGENS} disabled={!installed}
            selectedOption={launch.fg ?? 0}
            onChange={(o) => choose(ours ? (launch.opti === "fsr4" ? "fsr4_xmx" : launch.opti ?? "xess") : "own", Number(o.data))}
            description={explain("XeSS frame generation on the XMX cores: 2x shows one generated frame for every rendered one, 3x two, 4x three, with XeLL low latency always on. With OptiScaler it takes the game's own frame generation when the game has one (turn it on in the game's menu), otherwise OptiScaler makes the frames itself.",
              quick ? undefined : "fill a 120 Hz screen from 30–40 rendered fps; keep the game's frame limit on.", quick ? undefined : "a little input lag, which low latency keeps down.")} />
        </PanelSectionRow>
      )}
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
      {(fgmodHere || gs?.opti_owner === "decky-framegen" || gs?.opti_owner === "other") && (
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
          <DropdownItem label="FSR games without OptiScaler (xess-xmx-linux's FSR route)" layout="below" childrenContainerWidth="max" disabled={!installed || !newPkg} rgOptions={FSR_OPTIONS} selectedOption={launch.fsr ?? "off"}
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
