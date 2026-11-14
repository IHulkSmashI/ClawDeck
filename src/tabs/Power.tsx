import { DialogButton, DropdownItem, Field, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { notify } from "../components/notify";
import { FC, useEffect, useMemo, useRef, useState } from "react";

import {
  applyProfile, gameStarted, uiLog, powerSlot, resetPowerSlot, PowerSlot, getAutotune, getPower, getSetting, listProfiles, setPl2, setAutotuneFloor, setAutotuneKeep, setAutotuneTarget, setCpuMaxPct, setCpuTurbo,
  setEpp, setGpuPowerProfile, setGpuWindow, setSetting, setTdp, AutotuneStatus, LimitResult, PowerStatus, WriteResult,
} from "../api";
import { Advanced } from "../components/Advanced";
import { FanBattery } from "../components/FanBattery";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Group, Note, Problem, Status, useExplanations, Anchor, markPick } from "../components/Hint";
import { runningApp } from "../steam";

const toast = (body: string) => notify(body);

// short label in the control (nothing truncated at 1280x800); what it is for, and what it costs, under it (§17)
const PROFILES: { id: string; short: string; does: string; cost: string }[] = [
  { id: "saver", short: "Battery saver", does: "2D, indie and old games; the longest play time", cost: "newer 3D games drop frames" },
  { id: "battery", short: "Battery", does: "light games and long trips", cost: "heavier games need Balanced" },
  { id: "balanced", short: "Balanced", does: "newer, heavier games away from a charger", cost: "shorter play time than Battery" },
  { id: "performance", short: "Performance", does: "handheld on the charger", cost: "drains the battery fast unplugged" },
  { id: "plugged", short: "Plugged", does: "heavy games on the charger", cost: "warm; fans audible" },
  { id: "max", short: "Max", does: "the firmware's ceiling, docked", cost: "hot and loud; few extra frames per watt" },
];
const TARGETS = [{ data: 0, label: "None: nearly the most frames" },
  ...[30, 40, 45, 50, 60, 72, 90, 120].map((f) => ({ data: f, label: `${f} fps` }))];
const EPP: { data: string; label: string }[] = [
  { data: "power", label: "Most battery" }, { data: "balance_power", label: "Balanced, battery first" },
  { data: "balance_performance", label: "Balanced, speed first" }, { data: "performance", label: "Fastest" },
];

const backendName = (b: string | null | undefined) => (b === "rapl" ? "RAPL" : b === "hhd" ? "Handheld Daemon" : b === "msi-firmware" ? "MSI firmware" : "hardware");
// one number in the main view: the sustained TDP the hardware reports back. The short boost limit (PL2) is shown
// only under Advanced, so the line never reads like two different TDPs (maintainer, 2026-10-22).
const reads = (st: PowerStatus | null) => `Chip now: ${st?.tdp_w ?? "?"} W (read back from ${backendName(st?.tdp_backend)})`;
const ago = (s: number | null | undefined) => (s == null ? "" : s < 90 ? `${s} s ago` : `${Math.round(s / 60)} min ago`);

/** Writes that follow a slider or a quick flick through a list: wait for the last value, write once, and let only the
 *  newest answer through (an older one that arrives late is dropped), so the toast never shows a stale value. */
// the last pick, outside the component: choosing in Steam's dropdown can re-mount Quick Access, and the new panel's
// first read-back (before the write landed) showed the previous profile (Claw, 2026-11-04)
let lastPick: { profile: string; tdp: number; at: number } | null = null;

const useLatest = () => {
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const seq = useRef<Record<string, number>>({});
  return <T,>(key: string, write: () => Promise<T>, done: (r: T) => void, ms = 300) => {
    clearTimeout(timers.current[key]);
    const mine = (seq.current[key] = (seq.current[key] ?? 0) + 1);
    timers.current[key] = setTimeout(async () => { const r = await write(); if (mine === seq.current[key]) done(r); }, ms);
  };
};

/** The Power panel: profile, TDP slider, Autotune and the read-back line; everything else under Advanced (§19). */
export const PowerTab: FC<{ quick?: boolean }> = ({ quick }) => {
  useExplanations();
  const [st, setSt] = useState<PowerStatus | null>(null);
  const [at, setAt] = useState<AutotuneStatus | null>(null);
  const [autotune, setAutotune] = useState(false);
  const [atPlugged, setAtPlugged] = useState(false);
  const recent = lastPick && Date.now() - lastPick.at < 4000 ? lastPick : null;
  const [profile, setProfile] = useState(recent?.profile ?? "battery");
  const [tdp, setTdpV] = useState(recent?.tdp ?? 12);
  const [table, setTable] = useState<Record<string, { tdp?: number }>>({});
  const [err, setErr] = useState<string | null>(null);        // the last write that didn't take, in red
  const [advErr, setAdvErr] = useState<string | null>(null);
  const latest = useLatest();
  const app = runningApp();
  const appid = app?.appid ?? null;
  const [slot, setSlot] = useState<PowerSlot | null>(null);

  // Only the newest read-back may change what the profile list and the slider show, and none while a pick is still
  // on its way to the hardware: an older answer arriving late put the previous profile or watts back on screen
  // (2026-11-04: "one of them misses sometimes").
  const readSeq = useRef(0);
  const pending = useRef(0);
  const refresh = async () => {
    const mine = ++readSeq.current;
    const [s, auto, last, tbl, a, atp, sl] = await Promise.all([
      getPower(), getSetting("autotune"), getSetting("last_profile"), listProfiles(), getAutotune(), getSetting("autotune_plugged"),
      powerSlot(appid).catch(() => null),
    ]);
    if (mine !== readSeq.current) return;
    setSlot(sl);
    setSt(s); setAutotune(auto !== false); setTable(tbl); setAt(a); setAtPlugged(atp !== false);
    if (pending.current > 0) return;
    // the pick for this power source, as the backend applies it: in a game that game's own pick, else the last pick on
    // battery / plugged in (not the last pick anywhere: on battery that showed the plugged-in profile), else the default.
    // Its watts are your limit; what Autotune uses under it is the live line below, never the slider (2026-11-04).
    const name = sl?.pick?.profile ?? (last === "dock" ? "max" : (last || "battery"));
    const l = s?.range?.min ?? s?.limits.min ?? 8, h = s?.range?.max ?? s?.limits.plugged_max ?? 35;
    const w = Math.max(l, Math.min(sl?.pick?.tdp ?? (name === "custom" ? s.custom_tdp : tbl[name]?.tdp) ?? s.tdp_w ?? 12, h));
    if (lastPick && Date.now() - lastPick.at < 4000 && (lastPick.profile !== name || lastPick.tdp !== w)) {
      setProfile(lastPick.profile); setTdpV(lastPick.tdp); return;     // the write is still on its way
    }
    setProfile(name);
    setTdpV(w);
  };
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const atRef = useRef<AutotuneStatus | null>(null);
  atRef.current = at;
  useEffect(() => { refresh(); }, []);
  // a game runs but ClawDeck wasn't told (Decky restarted during the game, an update): tell it now, once, so the
  // game's power pick and Autotune start without quitting the game
  const resynced = useRef<string | null>(null);
  useEffect(() => {
    if (!appid || !at || at.game === appid || resynced.current === appid) return;
    resynced.current = appid;
    uiLog(`power: ${appid} runs but the backend has ${at.game ?? "no game"}: reporting it`);
    gameStarted(appid).then(() => refreshRef.current()).catch(() => undefined);
  }, [appid, at?.game]);
  // while the panel is open in a game (Autotune on), or the temperature guard is acting, the live line follows along:
  // one cheap call every 3 s, only while visible. When Autotune starts, stops, gets a new limit or the power source
  // changes, everything is read again, so nothing stale stays on screen
  const hot = !!(at?.thermal?.tripped || at?.thermal?.stepped_w);
  const live = !!(at?.running || hot || (appid && autotune));
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => {
      getAutotune().then((a) => {
        const p = atRef.current;
        setAt(a);
        if (!p || p.running !== a.running || p.ceiling_tdp !== a.ceiling_tdp || p.plugged !== a.plugged || p.appid !== a.appid) refreshRef.current();
      }).catch(() => undefined);
    }, 3000);
    return () => clearInterval(t);
  }, [live]);

  const lo = st?.range?.min ?? st?.limits.min ?? 8, hi = st?.range?.max ?? st?.limits.plugged_max ?? 35;
  // Custom is always in the list (maintainer, 2026-11-05: it only appeared after moving the slider): picking it brings
  // back the last Custom watts saved (backend apply_profile("custom")), and the PL2 control with it
  const customW = profile === "custom" ? tdp : Math.max(lo, Math.min(st?.custom_tdp ?? tdp, hi));
  const wattsOf = (id: string) => (id === "custom" ? customW : table[id]?.tdp != null ? Math.max(lo, Math.min(table[id].tdp!, hi)) : undefined);
  const picked = PROFILES.find((p) => p.id === profile);
  // one options array per change of the table/Custom watts: Steam's dropdown keeps the option it matched on the first
  // render, so a fresh array every poll (and labels that change after the first load) left it showing the old pick
  const profileOptions = useMemo(() => [
    ...PROFILES.map((p) => ({ data: p.id, label: wattsOf(p.id) ? `${p.short} · ${wattsOf(p.id)} W` : p.short })),
    { data: "custom", label: `Custom · ${customW} W` }],
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [table, lo, hi, customW]);   // tdp only matters for Custom's label:
  // rebuilding the options on every pick (the slider follows each profile) made a quick second pick show the first
  const custom = profile === "custom";
  const onLimit = (r: LimitResult) => {
    toast(r.summary); setErr(r.ok ? null : r.error);
    refresh();
  };
  const writeSeq = useRef(0);
  const write = (run: () => Promise<LimitResult>, ms = 300) => {
    const mine = ++writeSeq.current;
    pending.current = 1;                             // a debounced pick replaces the one before it: one pending at most
    latest("limit", async () => { try { return await run(); } finally { if (mine === writeSeq.current) pending.current = 0; } }, onLimit, ms);
  };
  const pick = (id: string) => {
    setProfile(id);
    const w = wattsOf(id) ?? tdp;
    setTdpV(w);                                      // the slider follows the profile at once
    lastPick = { profile: id, tdp: w, at: Date.now() };
    write(() => applyProfile(id, appid), 0);         // a pick is written at once; only the slider waits for the last value
  };
  const slide = (w: number) => {
    setTdpV(w); setProfile("custom");               // moving the slider = the Custom profile
    lastPick = { profile: "custom", tdp: w, at: Date.now() };
    write(() => setTdp(w));
  };
  const onLever = (r: WriteResult) => { toast(r.summary); setAdvErr(r.ok ? null : r.error); refresh(); };

  const running = !!at?.running && !at.paused;
  const thermalLine = at?.thermal?.line ?? null;     // "thermal: stepped to 15 W" once the temperature guard acted
  const phase = { measure: "measuring the most", balance: "balancing CPU/GPU", lower: "lowering", hold: "holding",
    target: at?.target ? `holding ${at.target} fps` : "target" }[at?.phase ?? "measure"];
  // "25 W max → using 17 W · 58 fps (97 % of 60) · 3.4 fps/W": rendered frames; with frame generation, what is shown too
  const fpsText = at?.avg_fps != null
    ? `${Math.round(at.avg_fps)} fps` + (at.phase === "target" && at.target ? ` (target ${at.target}${at.unreachable ? ", out of reach" : ""})`
        : at.max_fps ? ` (${at.pct_of_max} % of ${Math.round(at.max_fps)})` : "") +
      ((at.source === "rendered" || at.source === "frame-gen pick") && at.multiplier && at.multiplier >= 1.5 ? ` rendered, ${at.multiplier}x shown` : "") : "measuring…";
  const atLine = !autotune ? "Autotune off"
    : !at?.running ? (!appid ? "Autotune on: starts with the next game"
      : at?.why && at.why !== "not started" ? `Autotune: ${at.why}` : "Autotune on: starting…")
    : at.no_frames ? "Autotune can't read this game's frame rate (it has no XeSS or XeLL), so it stays out: your limit is on the chip"
    : at.paused ? "Autotune paused: you changed a power setting by hand. Pick a profile or move the slider to hand back control"
    : `Autotune on · ${fpsText}` + (at.fps_per_w ? ` · ${at.fps_per_w} fps/W` : "");
  // the live line under the slider: your limit (the slider) and what Autotune runs the chip at under it now
  const limitW = running ? (at?.ceiling_tdp ?? tdp) : null;
  const savesW = running && at?.tdp != null && limitW != null && limitW > at.tdp ? limitW - at.tdp : 0;
  const usingLine = running && at?.tdp != null
    ? (limitW != null && at.tdp > limitW ? `Autotune: using ${at.tdp} W, up from your ${limitW} W for the ${at.target} fps target`
      : `Autotune: using ${at.tdp} W of your ${limitW} W` + (savesW ? ` · saves ${savesW} W` : "")) +
      ` · ${at.avg_fps != null ? `${Math.round(at.avg_fps)} fps` : "measuring…"}` : null;
  const bound = at?.bound === "gpu" ? "GPU-bound" : at?.bound === "cpu" ? "CPU-bound" : "Balanced";
  const atDetail = running
    ? `${bound} · ${phase}${at?.last_action ? ` · ${at.last_action}${at.last_change_s != null ? ` (${ago(at.last_change_s)})` : ""}` : ""}` : undefined;
  const rapl25 = st?.rapl.max_w && st.rapl.max_w < hi ? ` The chip reports ${st.rapl.max_w} W as its sustained maximum; above that it may not follow.` : "";

  return (
    <PanelSection title={quick ? undefined : "Power"}>
      <PanelSectionRow>
        {/* layout below + full width: inline, the description takes half the Quick Access column and cuts the option
            text off ("Max · 3..."). Rendered only once the saved profile is loaded (key), so it never mounts on the
            placeholder and keeps showing that (maintainer report 2026-10-22: "has to be selected twice"). */}
        <Anchor id="power"><DropdownItem onMenuOpened={() => markPick("power")} key={st ? "loaded" : "loading"} menuLabel="Power profile" layout="below" childrenContainerWidth="max"
          selectedOption={profile} onChange={(o) => { markPick("power"); pick(o.data); }} rgOptions={profileOptions}
          description={explain(custom ? "Your own limit, from the slider below." : `${picked?.short}: ${picked?.does}.`,
            quick ? undefined : "the most power the game may use; Autotune saves below it.", quick || custom ? undefined : picked?.cost)} /></Anchor>
        <Anchor id="tdp"><SliderField label="TDP (W)" value={tdp} min={lo} max={hi} step={1} showValue disabled={!st?.tdp_backend} onChange={(w) => { markPick("tdp"); slide(w); }}
          description={explain(`The power CPU and graphics share, ${lo}–${hi} W. Moving it makes a Custom profile; Autotune never goes above it.`,
            quick ? undefined : "Steam's TDP slider does nothing on the Claw 8 EX (PowerStation reports no TDP range for this chip), so ClawDeck sets the chip's own limit." + rapl25,
            quick ? undefined : "each watt more buys fps with play time and heat.")} /></Anchor>
        {custom && <ToggleField label="Set PL2 yourself" checked={!!st?.pl2_manual}
          description={explain(st?.pl2_manual ? "PL2, the short-burst limit, is the slider below instead of your TDP + 2 W." : `PL2, the short-burst limit, follows your TDP: ${tdp + 2} W (TDP + 2 W, as Handheld Daemon and PowerControl do).`,
            "a higher PL2 gives the CPU and graphics short bursts above the TDP: snappier loading, more heat, and frame times can get less even.")}
          onChange={async (v) => { const r = await setPl2(v, v ? (st?.pl2_set_w ?? tdp + 2) : null); toast(r.summary); refresh(); }} />}
        {custom && st?.pl2_manual && <SliderField label="PL2 (W)" value={Math.max(tdp, st?.pl2_set_w ?? tdp + 2)} min={tdp} max={st?.pl2_max ?? 45} step={1} showValue
          onChange={(w) => latest("pl2", () => setPl2(true, w), (r) => { toast(r.summary); refresh(); })}
          description={explain(`From your TDP (${tdp} W) to ${st?.pl2_max ?? 45} W, saved for ${slot?.plugged ? "plugged in" : "battery"}.`)} />}
        <Status ok={err ? false : usingLine ? true : null} label={(usingLine ?? reads(st)) + (thermalLine ? ` · ${thermalLine}` : "")}
          detail={thermalLine ? `Package ${at?.thermal?.temp_c ?? "?"} °C. thermald is paused while ClawDeck holds the TDP, so ClawDeck steps it down 2 W at a time above 95 °C until under 90 °C, then back up 2 W every 30 s once under 80 °C, to your limit.`
            : usingLine ? `The slider is your limit; Autotune runs the chip under it${atDetail ? ` (${atDetail})` : ""}. A new pick is set at once and measured again.`
            : app ? app.name : slot?.pick?.from === "default" ? "No game running: the chip keeps its own limits until you pick one; games start at this profile"
            : "No game running: this limit is on the chip now, and comes back when a game closes"} />
        <Problem error={err} what="Power limit" />
        {app && slot && (
          <Field focusable bottomSeparator="none"
            label={<span style={{ fontSize: 12, color: "#b8bcbf" }}>{slot.base
              ? `Saved for ${app.name} · ${slot.plugged ? "plugged in" : "on battery"}: ${PROFILES.find((p) => p.id === slot.base)?.short ?? "Custom"}${slot.base === "custom" && slot.tdp ? ` ${slot.tdp} W` : ""}`
              : `${app.name} · ${slot.plugged ? "plugged in" : "on battery"}: your last pick (pick one to save it for this game)`}</span>}
            description={explain("A pick while a game runs is saved for that game, on battery and plugged in separately, and comes back at its next start. Unplugging switches to its other one.")}>
            {slot.base && <DialogButton style={{ minWidth: 0, width: "auto", padding: "0 10px", height: 28 }}
              onClick={async () => { await resetPowerSlot(app.appid); toast(`${app.name}: back to your last pick`); setSlot(await powerSlot(app.appid)); refresh(); }}>Use default</DialogButton>}
          </Field>
        )}
      </PanelSectionRow>
      <PanelSectionRow>
        <Group title="Autotune" />
        <ToggleField label="Autotune (beta, every game)" checked={autotune} onChange={async (v) => { setAutotune(v); await setSetting("autotune", v); refresh(); }}
          description={explain(`Finds the most frames the TDP above gives, shares that power between CPU and graphics where the game needs it, then lowers the TDP (3 W steps on a plateau, then 1 W) while the game gives up at most ${Math.max(2, Math.round(((st?.autotune_keep ?? 5) / 100) * 60))} fps of 60 (${st?.autotune_keep ?? 5} %, or 2 fps, whichever is more). On battery and on the charger too: less heat and fan. Counts rendered frames, not the ones frame generation adds.`,
            quick ? undefined : "nearly the same frames for less power: longer play time. With a frame limit (Steam's), it holds the limit with the least power.", quick ? undefined : "it spends a few seconds at the full TDP whenever it measures again (new area, every few minutes).")} />
        {autotune && (
          <DropdownItem label="Autotune target" layout="below" childrenContainerWidth="max" rgOptions={TARGETS}
            selectedOption={st?.autotune_target ?? 0}
            onChange={async (o) => { const r = await setAutotuneTarget(o.data); toast(r.summary); refresh(); }}
            description={explain("A minimum frame rate to keep with the fewest watts (every second at it or over): starts at the TDP above, raises it (up to the most this Claw allows) while the game is under the target, lowers it only while the game stays above the target with room to spare, and the TDP it settles on becomes this game's TDP for its next start. Out of reach, it stays at the most this Claw allows. Counts rendered frames (with frame generation you see 2–4x as many). Set Steam's frame limit to the same number so the game doesn't render frames you don't need.",
              quick ? undefined : "No target: nearly the most frames the TDP above gives, for less power.", undefined)} />
        )}
        <Status ok={!autotune ? false : running ? true : null} label={atLine} detail={atDetail} />
        {autotune && !!at?.game_fg && (
          <Note label={`Frame generation is on (${at.game_fg}x)`}>
            {`The screen's refresh caps the frames the game renders at refresh ÷ ${at.game_fg} (40 fps at 3x on 120 Hz), and frame generation costs some frames too. So Autotune finds the least power that holds that cap, not the most this game can render. For a full measurement: set Frame generation to Off in Upscaling and frame generation, play until Autotune says "holding", then turn it back on.`}
          </Note>
        )}
      </PanelSectionRow>
      {app && autotune && !quick && <LaunchWrapper appid={app.appid} name={app.name} />}
      <FanBattery quick={quick} />
      {st && (
        <Advanced id="power" label="Advanced"
          description={quick ? undefined : explain("CPU boost, CPU ceiling, GPU clocks, GPU efficiency mode, CPU energy mode and Autotune's floor. Steam has none of these for this chip.", undefined, "a change here pauses Autotune for this game.")}>
          {st.tdp_backend === "rapl" && st.rapl.pl2_w ? (
            <PanelSectionRow>
              <Status ok={null} label={`Boost limit ${st.rapl.pl2_w} W`} detail="Short bursts (seconds) may draw this much; set from the TDP, read back from RAPL." />
            </PanelSectionRow>
          ) : null}
          <PanelSectionRow>
            <ToggleField label="CPU boost" checked={st.cpu.no_turbo === 0} disabled={st.cpu.no_turbo == null}
              onChange={(v) => setCpuTurbo(v).then(onLever)}
              description={explain(`Short bursts above base clock. Reads ${st.cpu.no_turbo == null ? "?" : st.cpu.no_turbo === 0 ? "on" : "off"}.`,
                quick ? undefined : "off is cooler and quieter on battery.", "off costs fps in CPU-heavy scenes.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="CPU ceiling (%)" value={st.cpu.max_perf_pct ?? 100} min={10} max={100} step={5} showValue disabled={!st.cpu.available}
              onChange={(v) => latest("cpu", () => setCpuMaxPct(v), onLever)}
              description={explain(`Caps the CPU's clock so graphics get more of the shared power. Reads ${st.cpu.max_perf_pct ?? "?"} %.`,
                quick ? undefined : "graphics-heavy games; try 70–80 %.", "CPU-heavy scenes get slower.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU floor (MHz)" value={st.gpu.min_mhz ?? st.gpu.hw_min_mhz ?? 0} min={st.gpu.hw_min_mhz ?? 100} max={st.gpu.hw_max_mhz ?? 2300} step={50} showValue disabled={!st.gpu.available}
              onChange={(v) => latest("gpumin", () => setGpuWindow(v, null), onLever)}
              description={explain(`The GPU never clocks below this while a game runs. Reads ${st.gpu.min_mhz ?? "?"} MHz (now ${st.gpu.cur_mhz ?? "?"}).`,
                quick ? undefined : "stops stutter from clock dips.", "the GPU's share of power stays up even when it isn't needed.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU ceiling (MHz)" value={st.gpu.max_mhz ?? st.gpu.hw_max_mhz ?? 0} min={st.gpu.hw_min_mhz ?? 100} max={st.gpu.hw_max_mhz ?? 2300} step={50} showValue disabled={!st.gpu.available}
              onChange={(v) => latest("gpumax", () => setGpuWindow(null, v), onLever)}
              description={explain(`The GPU never clocks above this. Reads ${st.gpu.max_mhz ?? "?"} MHz of ${st.gpu.hw_max_mhz ?? "?"}.`,
                quick ? undefined : "hands power back to the CPU in CPU-heavy games.", "lower peak fps.")} />
          </PanelSectionRow>
          {st.gpu.power_profiles?.includes("power_saving") ? (
            <PanelSectionRow>
              <ToggleField label="GPU efficiency mode" checked={st.gpu.power_profile === "power_saving"}
                onChange={(v) => setGpuPowerProfile(v).then(onLever)}
                description={explain(`The GPU prefers its most efficient clock and ramps up more slowly. Reads ${st.gpu.power_profile ?? "?"}.`,
                  quick ? undefined : "a few % more play time in light games; Battery saver and Battery turn it on.",
                  "slower to clock up, can add a stutter at scene changes.")} />
            </PanelSectionRow>
          ) : null}
          <PanelSectionRow>
            <DropdownItem label="CPU energy mode" layout="below" childrenContainerWidth="max" disabled={!st.cpu.epp_available} rgOptions={EPP} selectedOption={st.cpu.epp ?? "balance_performance"}
              onChange={(o) => setEpp(o.data).then(onLever)}
              description={explain(`How eagerly the CPU clocks up, like Windows' power mode. Reads ${EPP.find((e) => e.data === st.cpu.epp)?.label ?? st.cpu.epp ?? "?"}.`,
                quick ? undefined : "profiles set it for you.", "Fastest uses more battery at idle.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="Autotune: give up at most (% of the frames, 2 fps minimum)" value={st.autotune_keep ?? 5} min={2} max={15} step={1} showValue
              onChange={(v) => latest("keep", () => setAutotuneKeep(v), (r) => { toast(r.summary); refresh(); })}
              description={explain("How many of the most frames the TDP gives Autotune may trade for power: 5 % of 60 fps = 3 fps; at 30 fps it's 2 fps. 1–2 fps for 5–10 W is usually worth it.",
                quick ? undefined : "smaller keeps more frames; larger saves more battery.", undefined)} />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="Autotune when plugged in" checked={atPlugged}
              onChange={async (v) => { setAtPlugged(v); await setSetting("autotune_plugged", v); toast(v ? "Autotune also runs on the charger, from the next game" : "Autotune runs on battery only, from the next game"); }}
              description={explain("On by default: on the charger it still saves heat and fan noise for nearly the same frames.",
                quick ? undefined : "a quieter, cooler Claw when docked.", "fewer frames than the plugged-in profile could give.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="Autotune floor (W)" value={st.autotune_floor ?? lo} min={lo} max={hi} step={1} showValue
              onChange={(v) => latest("floor", () => setAutotuneFloor(v === lo ? null : v), (r) => { toast(r.summary); refresh(); })}
              description={explain(`The least power Autotune may use (${lo} W, the chip's minimum, unless you raise it).`,
                quick ? undefined : "a game that stutters on the way down.", undefined)} />
            <Problem error={advErr} what="Advanced" />
          </PanelSectionRow>
        </Advanced>
      )}
    </PanelSection>
  );
};
