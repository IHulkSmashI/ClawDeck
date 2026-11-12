import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { notify } from "../components/notify";
import { FC, useEffect, useState } from "react";

import { gameChoose, gameRecommend, gameStatus, setDebugLaunch, setLaunch, FsrRoute, GameStatus, Launch, Recommendation } from "../api";
import { confirmUntested } from "../components/confirm";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { Anchor, explain, Group, markPick, Note, Problem, Status, useExplanations } from "../components/Hint";
import { getLaunchOptions, hasFgmod, hasWrapper, runningApp, setLaunchOptions, withFgmod, withoutFgmod, withWrapper, withoutWrapper } from "../steam";

const toast = (body: string) => notify(body);

const FSR_OPTIONS: { data: FsrRoute; label: string }[] = [
  { data: "off", label: "Off (the game's own FSR)" },
  { data: "xess", label: "FSR on XeSS (upscaling + frame gen)" },
  { data: "fg", label: "FSR frame gen on XeSS only" },
  { data: "inject", label: "Add XeSS frame gen to FSR upscaling" },
];
const picks = new Map<string, { up?: string; fg?: number; ov?: number }>();   // unapplied picks per game, across re-mounts

const UPSCALERS = [
  { data: "xess", label: "XeSS 2" },
  { data: "fsr4_xmx", label: "FSR 4.1.2" },
  { data: "fsr4_xmx_lite", label: "FSR 4.1.2 Lite (faster)" },
];
const OVERLAYS = [
  { data: -1, label: "Off" },
  { data: 0, label: "FPS only" },
  { data: 1, label: "Simple" },
  { data: 2, label: "Detailed (upscaler, frame generation)" },
  { data: 3, label: "Detailed + graph" },
  { data: 4, label: "Full" },
  { data: 5, label: "Full + graph" },
];
const FRAMEGENS = [
  { data: 0, label: "Off" },
  { data: 2, label: "2x" },
  { data: 3, label: "3x" },
  { data: 4, label: "4x" },
];

/**
 * The running game's XeSS on the XMX cores (xess-xmx-linux's per-game launch options, one tap each) and ClawDeck's
 * own pinned OptiScaler with Intel presets (a game Decky Framegen patched is left alone, with a warning). Every choice reaches the game through ClawDeck's launch wrapper
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


  const ours = gs?.opti_owner === "clawdeck";
  // pick the upscaler and frame generation, then one Apply (or Update when a newer OptiScaler is
  // pinned): nothing is written while picking
  // not set up yet: the recommended upscaler, the newest that runs here (FSR 4.1.2 on XMX where it can)
  const nowUp = ours ? (launch.opti === "fsr4" ? "fsr4_xmx" : launch.opti ?? "xess") : (rec?.upscaler ?? "xess");
  const nowFg = launch.fg ?? 0;
  // kept outside the component: picking in Steam's dropdown can re-mount Quick Access, which lost the pick (the
  // choice jumped back to XeSS 2 on the Claw, 2026-11-04)
  const kept = picks.get(app.appid) ?? {};
  const [pickUp, setPickUpS] = useState<string | null>(kept.up ?? null);
  const [pickFg, setPickFgS] = useState<number | null>(kept.fg ?? null);
  const setPickUp = (v: string | null) => { picks.set(app.appid, { ...(picks.get(app.appid) ?? {}), up: v ?? undefined }); setPickUpS(v); };
  const setPickFg = (v: number | null) => { picks.set(app.appid, { ...(picks.get(app.appid) ?? {}), fg: v ?? undefined }); setPickFgS(v); };
  const [pickOv, setPickOvS] = useState<number | null>(kept.ov ?? null);
  const setPickOv = (v: number | null) => { picks.set(app.appid, { ...(picks.get(app.appid) ?? {}), ov: v ?? undefined }); setPickOvS(v); };
  const nowOv = launch.opti_overlay ?? -1;
  const [busy, setBusy] = useState(false);
  // and not while this game's last launch ran without the XMX cores: FSR 4.1.2 drew a black screen then (FH5 on a
  // Claw 8 AI+, r/MSIClaw 2026-11-07). One launch with XeSS 2 on XMX brings it back. The backend refuses it too.
  const xmxMissed = gs?.xmx_proof?.state === "stale" || gs?.xmx_proof?.state === "failed";
  // a game on FSR 4.1.2 when that happened: the pick moves to XeSS 2, so Apply switches it
  const wantUp = pickUp ?? (xmxMissed && nowUp.startsWith("fsr4") ? "xess" : nowUp), wantFg = pickFg ?? (ours ? nowFg : rec?.frame_gen && rec.frame_gen !== "off" ? 2 : nowFg);
  const wantOv = pickOv ?? nowOv;
  const changed = !ours || wantUp !== nowUp || wantFg !== nowFg || wantOv !== nowOv;
  // FSR 4.1.2 only where it can run: only what the game and the hardware support
  // never without XMX: nitec's FSR 4.1.2 drew a black screen on the stock driver (Onimusha, Claw 8 EX, 2026-11-04)
  // only an explicit "XMX off" for the game hides it: before OptiScaler is set up the per-game XMX driver reads as off
  // (nothing in the game uses XeSS yet), and picking an OptiScaler upscaler is what switches it on (launchenv: opti
  // implies CLAWDECK_XMX=1). CONTROL showed no FSR 4.1.2 for that reason, 2026-11-05.
  const fsr4Xmx = launch.xmx !== false && rec?.fsr4?.xmx !== false;
  const fsr4Ok = !!(rec?.fsr4?.dx12 && rec.fsr4.files && fsr4Xmx && !xmxMissed);
  const fsr4Why = !rec ? "" : !rec.fsr4 ? "FSR 4.1.2 isn't offered: ClawDeck couldn't check this game (see its log)." : !rec.fsr4.dx12 ? "FSR 4.1.2 isn't offered: this game isn't DirectX 12."
    : !fsr4Xmx ? "FSR 4.1.2 isn't offered: XMX is off for this game, and FSR 4.1.2 needs the XMX cores (a black screen without them)."
    : xmxMissed ? "FSR 4.1.2 isn't offered right now: this game's last launch ran without the XMX cores, and FSR 4.1.2 needs them (a black screen without them). Start it once with XeSS 2; when \"In this game now\" shows the XMX cores were used, FSR 4.1.2 is back."
    : !rec.fsr4.files ? `FSR 4.1.2's files aren't in place yet: reinstall ClawDeck, or put nitec's two files (or their zip) in Downloads.` : "";
  const upscalers = UPSCALERS.filter((u) => u.data === "xess" || (fsr4Ok && u.data.startsWith("fsr4")));
  const optiVer = gs?.opti_version ?? null;
  const outdated = !!(ours && optiVer && gs?.files?.version && gs.files.version !== optiVer);
  const apply = async () => {
    setBusy(true);
    try { await choose(wantUp, wantFg); setPickUp(null); setPickFg(null); setPickOv(null); } finally { setBusy(false); }
  };
  const choose = async (upscaler: string, fg: number, allow = false) => {
    const size = rec?.capcom ? "about 85 MB with REFramework" : "about 55 MB";
    if (upscaler !== "own" && !ours) notify(`${app.name}: setting up OptiScaler (the first time downloads it, ${size})…`, { title: `${app.name}: installing…`, body: `OptiScaler (first time: ${size} download)` });
    const r = await gameChoose(app.appid, upscaler, fg, allow, upscaler === "own" || wantOv < 0 ? null : wantOv);
    if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("REFramework", () => choose(upscaler, fg, true)); return; }
    setErr(r.ok ? null : (r.error ?? "not saved"));
    const needsWrapper = Object.keys(r.env ?? {}).length > 0;
    if (r.ok && needsWrapper && opts !== null && !hasWrapper(opts) && gs?.wrapper) {
      setLaunchOptions(app.appid, withWrapper(opts, gs.wrapper.replace(/ %command%$/, "")));
    }
    // OptiScaler taken out and nothing else of ClawDeck's left for this game: the launch options go back to what they
    // were (maintainer, 2026-11-05: "the run command remains"). With something left (its own XeSS on XMX, low latency
    // set by hand), the wrapper stays, and the toast says why.
    let kept = false;
    if (r.ok && upscaler === "own" && opts !== null && hasWrapper(opts)) {
      if (needsWrapper) kept = true; else setLaunchOptions(app.appid, withoutWrapper(opts));
    }
    const up = UPSCALERS.find((u) => u.data === upscaler)?.label ?? upscaler;
    const full = r.ok ? `${app.name}: ${up}${fg ? `, frame generation ${fg}x` : ""}. ${upscaler === "own" ? "" : "In the game's menu pick DLSS (or FSR). "}From the next launch.` : `Not saved: ${r.error}`;
    const refLeft = r.ok && upscaler === "own" && r.reframework_left
      ? " A REFramework that isn't ClawDeck's (dinput8.dll, another build) is still next to the game's exe: delete it there if you don't want it." : "";
    notify(full + (kept ? " ClawDeck stays in the launch options so this game's own XeSS runs on the XMX cores." : "") + refLeft,
      r.ok ? (upscaler === "own" ? { title: `${app.name}: OptiScaler removed`, body: kept ? "Launch options kept: its XeSS runs on XMX" : "Back to the game's own files and launch options" }
      : { title: `${app.name}: ${up}${fg ? ` + ${fg}x` : ""} ✓`, body: "Start the game, pick DLSS in its menu" })
      : { title: `${app.name}: not done`, body: "Reason at the top of ClawDeck" });
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

  // how to use it in the game; Capcom RE Engine games (REFramework) are set up so the game's DLSS and
  // DLSS Frame Generation become XeSS and XeSS frame generation, and REFramework's menu moves to Delete
  const fsr4Now = (launch.opti ?? "xess").startsWith("fsr4_xmx");
  const capcom = !!launch.reframework;
  const steps = (
    <ol style={{ margin: "0 0 4px 18px", padding: 0, fontSize: 12, lineHeight: "18px", color: "#b8bcbf" }}>
      <li>Restart the game if it was running when you changed something here.</li>
      {capcom ? <>
        <li>Graphics settings: turn on <b>DLSS</b> (upscaling, at the quality you like){launch.opti_fg !== "off" ? <> and <b>DLSS Frame Generation</b></> : null}. OptiScaler turns DLSS into {fsr4Now ? "FSR 4.1.2" : "XeSS"} on XMX{launch.opti_fg !== "off" ? <> and DLSS Frame Generation into <b>XeSS frame generation</b> (XeFG, {launch.fg ?? 2}x)</> : null}.</li>
        <li>Then <b>quit and start the game again</b> once: switching these while the game runs can crash it.</li>
        <li>Check it worked: press <b>Insert</b> for OptiScaler's menu; press it again to close. {fsr4Now
          ? <>FSR 4.1.2 shows as <b>FSR 3.X</b> under Upscalers with <b>FSR 4.1.2</b> picked under FFX Settings, FFX Upscaler, Input: DLSS.</>
          : <>XeSS shows under Upscalers, Input: DLSS.</>}{launch.opti_fg !== "off" ? <> Frame generation: FG Input <b>DLSSG</b>, FG Output <b>XeFG</b>.</> : null}</li>
        <li><b>Delete</b> opens REFramework's menu (moved off Insert, which OptiScaler uses).</li>
      </> : <>
        <li>Graphics settings: set the upscaler to <b>DLSS</b> (or FSR if the game has no DLSS), at the quality you like. OptiScaler swaps it for {fsr4Now ? "FSR 4.1.2 on XMX" : "XeSS on XMX"}.</li>
        <li>Pick it <b>once</b>: switching between DLSS and FSR while the game runs can crash it (CONTROL does). To change it, quit, change it here, start the game again.</li>
        {(launch.opti_fg === "dlssg" || launch.opti_fg === "fsrfg") && <li>Turn on the game's <b>{launch.opti_fg === "dlssg" ? "DLSS" : "FSR"} Frame Generation</b> (any multiplier: ClawDeck's {launch.fg ?? 2}x is what you get), then <b>quit and start the game again</b>: switching it on while the game runs can crash it.</li>}
        <li>Check it worked: press <b>Insert</b> for OptiScaler's menu; press it again to close. {fsr4Now
          ? <>FSR 4.1.2 shows as <b>FSR 3.X</b> under Upscalers with <b>FSR 4.1.2</b> picked under FFX Settings, FFX Upscaler (not 4.0.2) (OptiScaler files FSR 4 under FSR 3.X), Input: DLSS.</>
          : <>XeSS shows under Upscalers, Input: DLSS.</>}{launch.fg ? <> Frame generation: FG Output <b>XeFG</b>, Active ticked.</> : null}</li>
      </>}
      <li>No keyboard? Steam button → Controller settings → Edit layout → pick a back button (M1 or M2) → Keyboard → <b>Insert</b>{capcom ? <> (and another for <b>Delete</b>)</> : null}. Bind it in this game's layout only.</li>
    </ol>
  );

  return (
    <PanelSection title={embedded ? undefined : `Per-game: ${app.name}`}>
      {!fgmodHere && gs?.opti_owner !== "decky-framegen" && gs?.opti_owner !== "other" && (
        <PanelSectionRow>
          <Group title="Upscaling and frame generation (XMX cores)" first />
          <Anchor id="upscaler"><DropdownItem onMenuOpened={() => markPick("upscaler")} label="Upscaler" layout="below" childrenContainerWidth="max" rgOptions={upscalers}
            selectedOption={wantUp}
            onChange={(o) => { markPick("upscaler"); setPickUp(String(o.data)); }}
            description={fsr4Why ? <>{fsr4Why}{explain(" XeSS 2: Intel's own, the fastest here. FSR 4.1.2: AMD's FSR 4 as nitec built it for Intel's matrix cores, the best image; Lite is faster for a little less detail.")}</>
              : explain("XeSS 2: Intel's own, the fastest here. FSR 4.1.2: AMD's FSR 4 as nitec built it for Intel's matrix cores, the best image; Lite is faster for a little less detail.")} /></Anchor>
          <Anchor id="framegen"><DropdownItem onMenuOpened={() => markPick("framegen")} label="Frame generation" layout="below" childrenContainerWidth="max" rgOptions={FRAMEGENS} disabled={!installed}
            selectedOption={wantFg} onChange={(o) => { markPick("framegen"); setPickFg(Number(o.data)); }}
            description={explain("XeSS frame generation on the XMX cores, with XeLL low latency: 2x adds one frame per rendered one, 3x two, 4x three. It uses the game's own frame generation when the game has one.",
              quick ? undefined : "fill a 120 Hz screen from 30–40 rendered fps; keep the game's frame limit on.", quick ? undefined : "a little input lag, which low latency keeps down.")} /></Anchor>
          <Anchor id="overlay"><DropdownItem onMenuOpened={() => markPick("overlay")} label="OptiScaler overlay" layout="below" childrenContainerWidth="max" rgOptions={OVERLAYS}
            selectedOption={wantOv} onChange={(o) => { markPick("overlay"); setPickOv(Number(o.data)); }}
            description={explain("OptiScaler's own counter at the top right, from the game's start: rendered and shown fps, the upscaler in use and \"(XeFG)\" while frame generation runs, which Steam's overlay can't tell. Saved in the game's OptiScaler settings.",
              undefined, quick ? undefined : "Detailed and Full add a few lines of text over the game.")} /></Anchor>
          <div style={{ height: 8 }} />
          {rec?.ships && <Status ok={null} label={`In the game: ${[...rec.ships.upscaler, ...rec.ships.fg, ...rec.ships.latency].join(", ") || "no upscaler of its own"}`}
            detail={quick ? undefined : "What the game ships (upscaler, frame generation, low latency), checked in its folder before anything is set up."} />}
          {rec?.supported_list?.listed && rec.supported_list.intel_ok === false && <Status ok={false} label="Not supported on Intel Arc"
            detail="The public supported-games list marks this game as not working on Intel Arc, so ClawDeck doesn't recommend OptiScaler here." />}
          {!installed && <Status ok={false} label="Install the XeSS XMX driver first" detail="Setup → Install XeSS XMX (one time). Without it nothing runs on the XMX cores." />}
          {rec?.pd_upscaler && !ours && <Status ok={false} label="Not set up by ClawDeck yet: no DLSS in this game"
            detail={rec.reasons[0]} />}
          <ButtonItem layout="below" disabled={busy || !installed || (ours && !changed && !outdated) || (!!rec?.pd_upscaler && !ours)} onClick={() => apply()}
            description={!ours && rec ? explain(rec.reasons.join(" · ")) : undefined}>
            {busy ? "Working…" : !ours ? "Install OptiScaler in this game" : outdated && !changed ? `Update OptiScaler to ${optiVer}` : changed ? "Apply changes" : "Installed"}
          </ButtonItem>
          {ours && (
            <ButtonItem layout="below" disabled={busy} onClick={async () => { setBusy(true); try { await choose("own", 0); } finally { setBusy(false); } }}>
              Uninstall OptiScaler from this game
            </ButtonItem>
          )}
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Group title="In this game now" />
        <div style={{ fontSize: 12, lineHeight: "18px", color: "#b8bcbf", padding: "0 0 4px" }}>
          <div>{ours ? "✓" : "–"} OptiScaler: {ours ? `ClawDeck's, as ${gs?.files?.dll ?? "?"} (${UPSCALERS.find((u) => u.data === (gs?.files?.preset === "fsr4" ? "fsr4_xmx" : gs?.files?.preset))?.label ?? gs?.files?.preset ?? "XeSS 2 on XMX"})`
            : fgmodHere || gs?.opti_owner === "decky-framegen" ? "Decky Framegen's (ClawDeck leaves it alone)" : gs?.opti_owner === "other" ? "put there by another tool or by hand" : "not in the game"}</div>
          {gs?.files?.reframework && <div>✓ REFramework (dinput8.dll)</div>}
          <div>{wrapped ? "✓" : "–"} Launch options: {wrapped ? "ClawDeck is in them" : "ClawDeck not added yet (added when you pick below)"}</div>
          {!quick && opts !== null && <div style={{ wordBreak: "break-all", opacity: 0.8 }}>{opts || "(empty)"}</div>}
          <div>{xmxOn && xessOk ? "✓" : "–"} XMX driver: {xmxOn ? (xessOk ? "on for this game" : "on, but the driver check hasn't passed") : "off for this game"}</div>
          {gs?.xmx_proof && <div style={{ color: gs.xmx_proof.state === "ok" ? "#4caf50" : gs.xmx_proof.state === "never" ? undefined : "#f44336" }}>
            {({ ok: `✓ XMX cores used in the last launch (${gs.xmx_proof.kernels} XMX programs)`,
                stale: "✗ Last launch didn't use the XMX cores (stock driver): restart the game",
                never: "– XMX cores not used yet: start the game once",
                failed: "✗ An XMX program didn't compile: the game falls back to DP4a (see ClawDeck's log)" } as const)[gs.xmx_proof.state]}</div>}
          <div>{launch.fg ? `✓ Frame generation ${launch.fg}x (XeSS on XMX)` : "– Frame generation off"} · XeLL low latency always on</div>
        </div>
      </PanelSectionRow>
      {(fgmodHere || gs?.opti_owner === "decky-framegen" || gs?.opti_owner === "other") && (
        <PanelSectionRow>
          <Status ok={ours ? true : null}
            label={ours ? "OptiScaler: ClawDeck's, in this game" : fgmodHere || gs?.opti_owner === "decky-framegen"
              ? "OptiScaler: Decky Framegen's, in this game" : "OptiScaler from somewhere else is in this game"}
            detail={explain(ours ? `Upscaler and frame generation below; the game's own files are kept and come back with Undo.${xmxOn && xessOk ? " Its XeSS runs on XMX through the driver above." : ""}`
              : fgmodHere || gs?.opti_owner === "decky-framegen"
                ? "Decky Framegen manages OptiScaler here. ClawDeck's upscaler presets still reach it, but the recommended setup, REFramework and its own OptiScaler wait until Decky Framegen is removed from this game (below, then Decky Framegen → Unpatch)."
                : "OptiScaler files ClawDeck didn't put there (from a Windows install, or by hand). ClawDeck leaves them alone; remove OptiScaler.ini and its DLL to let ClawDeck set the game up.")} />
          {fgmodHere && opts !== null && (
            <ButtonItem layout="below" onClick={() => setFgmod(false)}>Stop using Decky Framegen here</ButtonItem>
          )}
        </PanelSectionRow>
      )}
      {(ours || fgmodHere) && (quick ? <Advanced id="ingame" label="How to use it in the game">{<PanelSectionRow>
          <Group title="In the game" />
          {steps}
        </PanelSectionRow>}</Advanced> : <PanelSectionRow>
          <Group title="In the game" />
          {steps}
        </PanelSectionRow>)}
      {gs?.drive?.slow && (
        <PanelSectionRow>
          <Status ok={false} label={`Installed on ${gs.drive.kind}${gs.drive.sd ? " (SD card)" : ""}: expect slower loads and pop-in`}
            detail={explain(`Linux reads ${gs.drive.kind} through a slower driver, so this game streams its textures and shaders slower than on Windows. For the best result, move it to a Linux-formatted drive: Steam → game → Properties → Installed Files → Move install folder. An SD card formatted by Steam in Game Mode works.`)} />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Group title="Driver and low latency" />
        <Status ok={gs ? (xmxOn && xessOk ? true : (xmxOn && installed) || (!xmxOn && !wrapped) ? false : null) : null}
          label={!gs ? "XeSS: …" : !installed ? "XeSS: driver not installed (Setup)"
            : xmxOn ? (xessOk ? `XeSS runs on XMX (${perGame ? (launch.xmx == null ? "automatic" : "switched on for this game") : "default"})`
              : gs.xess.status.state === "unknown" ? "XeSS on XMX from your next log-in (the session check hasn't run yet)" : "XeSS: patched driver not loading, DP4a fallback")
            : perGame ? (launch.xmx === false ? "XeSS: the system's driver for this game (switched off)" : "The system's driver: this game ships no XeSS or OptiScaler")
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

      {!quick && (
        <PanelSectionRow>
          <Group title="Something not working?" />
          <ButtonItem layout="below" disabled={!wrapped || !gs} onClick={async () => {
            const r = await setDebugLaunch(app.appid, !gs?.debug?.armed);
            if (!r.ok) setErr(r.error ?? "couldn't change the debug logs"); else await load();
          }}
            description={explain(!wrapped ? "Needs ClawDeck in this game's launch options first (pick a setup above)."
              : gs?.debug?.armed ? "On for the next launch: start the game, play a minute where the problem shows, quit, then Setup → Save a bug report."
              : gs?.debug?.captured_at ? `Logs from the debug launch of ${new Date(gs.debug.captured_at * 1000).toLocaleString()} go into Setup → Save a bug report.`
              : "Turns on Proton's log, the XeSS XMX driver's messages and OptiScaler's log for one launch of this game, so a bug report shows why the XMX cores were missed.",
              "without them a report can only say that XeSS didn't reach the XMX cores, not where it stopped.",
              "that one launch starts a little slower and writes a few log files; the next launch is normal again.")}>
            {gs?.debug?.armed ? "Cancel debug logs for the next launch" : "Debug logs for the next launch"}
          </ButtonItem>
        </PanelSectionRow>
      )}

      <PanelSectionRow><Problem error={err} /></PanelSectionRow>
      {!quick && <LaunchWrapper key={wrapped ? "on" : "off"} appid={app.appid} name={app.name} />}
    </PanelSection>
  );
};
