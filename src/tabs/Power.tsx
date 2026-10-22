import { DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useRef, useState } from "react";

import {
  applyProfile, getAutotune, getPower, getSetting, listProfiles, setAutotuneFloor, setAutotuneKeep, setCpuMaxPct, setCpuTurbo,
  setEpp, setGpuWindow, setSetting, setTdp, AutotuneStatus, LimitResult, PowerStatus, WriteResult,
} from "../api";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Problem, Status } from "../components/Hint";
import { runningApp } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

// short label in the control (nothing truncated at 1280x800); what it is for, and what it costs, under it (§17)
const PROFILES: { id: string; short: string; does: string; cost: string }[] = [
  { id: "saver", short: "Battery saver", does: "2D, indie and old games; the longest play time", cost: "newer 3D games drop frames" },
  { id: "battery", short: "Battery", does: "light games and long trips", cost: "heavier games need Balanced" },
  { id: "balanced", short: "Balanced", does: "newer, heavier games away from a charger", cost: "shorter play time than Battery" },
  { id: "performance", short: "Performance", does: "handheld on the charger", cost: "drains the battery fast unplugged" },
  { id: "plugged", short: "Plugged", does: "heavy games on the charger", cost: "warm; fans audible" },
  { id: "max", short: "Max", does: "the firmware's ceiling, docked", cost: "hot and loud; few extra frames per watt" },
];
const EPP: { data: string; label: string }[] = [
  { data: "power", label: "Most battery" }, { data: "balance_power", label: "Balanced, battery first" },
  { data: "balance_performance", label: "Balanced, speed first" }, { data: "performance", label: "Fastest" },
];

const backendName = (b: string | null | undefined) => (b === "rapl" ? "RAPL" : b === "hhd" ? "Handheld Daemon" : b === "msi-firmware" ? "MSI firmware" : "hardware");
// one number in the main view: the sustained TDP the hardware reports back. The short boost limit (PL2) is shown
// only under Advanced, so the line never reads like two different TDPs (maintainer, 2026-10-22).
const reads = (st: PowerStatus | null) => `TDP ${st?.tdp_w ?? "?"} W (read back from ${backendName(st?.tdp_backend)})`;
const ago = (s: number | null | undefined) => (s == null ? "" : s < 90 ? `${s} s ago` : `${Math.round(s / 60)} min ago`);

/** Writes that follow a slider or a quick flick through a list: wait for the last value, write once, and let only the
 *  newest answer through (an older one that arrives late is dropped), so the toast never shows a stale value. */
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
  const [st, setSt] = useState<PowerStatus | null>(null);
  const [at, setAt] = useState<AutotuneStatus | null>(null);
  const [autotune, setAutotune] = useState(false);
  const [profile, setProfile] = useState("battery");
  const [tdp, setTdpV] = useState(12);
  const [table, setTable] = useState<Record<string, { tdp?: number }>>({});
  const [line, setLine] = useState<string | null>(null);      // after the last change: "RAPL reads 17 W / 24 W"
  const [err, setErr] = useState<string | null>(null);        // the last write that didn't take, in red
  const [advErr, setAdvErr] = useState<string | null>(null);
  const latest = useLatest();
  const app = runningApp();
  const appid = app?.appid ?? null;

  const refresh = async () => {
    const [s, auto, last, tbl, a] = await Promise.all([
      getPower(), getSetting("autotune"), getSetting("last_profile"), listProfiles(), getAutotune(),
    ]);
    const name = last === "dock" ? "max" : (last || "battery");
    setSt(s); setAutotune(!!auto); setProfile(name); setTable(tbl); setAt(a);
    setTdpV(name === "custom" ? (s.custom_tdp ?? s.tdp_w ?? 12) : (tbl[name]?.tdp ?? s.tdp_w ?? 12));
  };
  useEffect(() => { refresh(); }, []);
  // while the panel is open and Autotune runs (or the temperature guard is acting), its line follows along (one cheap
  // call every 3 s, only while visible)
  const hot = !!(at?.thermal?.tripped || at?.thermal?.stepped_w);
  useEffect(() => {
    if (!at?.running && !hot) return;
    const t = setInterval(() => { getAutotune().then(setAt).catch(() => undefined); }, 3000);
    return () => clearInterval(t);
  }, [at?.running, hot]);

  const lo = st?.range?.min ?? st?.limits.min ?? 8, hi = st?.range?.max ?? st?.limits.plugged_max ?? 35;
  const wattsOf = (id: string) => (table[id]?.tdp != null ? Math.max(lo, Math.min(table[id].tdp!, hi)) : undefined);
  const picked = PROFILES.find((p) => p.id === profile);
  const custom = profile === "custom";
  const onLimit = (r: LimitResult) => {
    toast(r.summary); setErr(r.ok ? null : r.error);
    setLine(`TDP ${r.read?.tdp_w ?? "?"} W (read back from ${backendName(r.read?.backend)})`);
    refresh();
  };
  const pick = (id: string) => {
    setProfile(id);
    if (wattsOf(id)) setTdpV(wattsOf(id)!);          // the slider follows the profile at once
    latest("limit", () => applyProfile(id, appid), onLimit);
  };
  const slide = (w: number) => {
    setTdpV(w); setProfile("custom");               // moving the slider = the Custom profile
    latest("limit", () => setTdp(w), onLimit);
  };
  const onLever = (r: WriteResult) => { toast(r.summary); setAdvErr(r.ok ? null : r.error); refresh(); };

  const running = !!at?.running && !at.paused;
  const thermalLine = at?.thermal?.line ?? null;     // "thermal: stepped to 15 W" once the temperature guard acted
  const phase = { measure: "measuring the most", balance: "balancing CPU/GPU", lower: "lowering", hold: "holding" }[at?.phase ?? "measure"];
  // "25 W max → using 17 W · 58 fps (97 % of 60) · 3.4 fps/W": rendered frames; with frame generation, what is shown too
  const fpsText = at?.avg_fps != null
    ? `${Math.round(at.avg_fps)} fps${at.max_fps ? ` (${at.pct_of_max} % of ${Math.round(at.max_fps)})` : ""}` +
      (at.source === "rendered" && at.multiplier && at.multiplier >= 1.5 ? ` rendered, ${at.multiplier}x shown` : "") : "measuring…";
  const atLine = !autotune ? "Autotune off"
    : !at?.running ? (appid ? "Autotune on: waiting for frame times (is ClawDeck in the launch options?)" : "Autotune on: starts with the next game")
    : at.paused ? "Autotune paused: you changed a power setting by hand. Pick a profile or move the slider to hand back control"
    : `Autotune: ${at.ceiling_tdp} W max → using ${at.tdp} W · ${fpsText}` + (at.fps_per_w ? ` · ${at.fps_per_w} fps/W` : "");
  const bound = at?.bound === "gpu" ? "GPU-bound" : at?.bound === "cpu" ? "CPU-bound" : "Balanced";
  const atDetail = running
    ? `${bound} · ${phase}${at?.last_action ? ` · ${at.last_action}${at.last_change_s != null ? ` (${ago(at.last_change_s)})` : ""}` : ""}` : undefined;
  const rapl25 = st?.rapl.max_w && st.rapl.max_w < hi ? ` The chip reports ${st.rapl.max_w} W as its sustained maximum; above that it may not follow.` : "";

  return (
    <PanelSection title="Power">
      <PanelSectionRow>
        {/* no side label: in the Quick Access column a label takes half the width and cuts the option text off */}
        <DropdownItem menuLabel="Power profile" selectedOption={profile} onChange={(o) => pick(o.data)}
          rgOptions={[...PROFILES.map((p) => ({ data: p.id, label: wattsOf(p.id) ? `${p.short} · ${wattsOf(p.id)} W` : p.short })),
            ...(custom ? [{ data: "custom", label: `Custom · ${tdp} W` }] : [])]}
          description={explain(custom ? "Your own limit, from the slider below." : `${picked?.short}: ${picked?.does}.`,
            quick ? undefined : "the most power the game may use; Autotune saves below it.", quick || custom ? undefined : picked?.cost)} />
        <SliderField label="TDP (W)" value={tdp} min={lo} max={hi} step={1} showValue disabled={!st?.tdp_backend} onChange={slide}
          description={explain(`The power CPU and graphics share, ${lo}–${hi} W. Moving it makes a Custom profile; Autotune never goes above it.`,
            quick ? undefined : "Steam's TDP slider does nothing on the Claw 8 EX (PowerStation reports no TDP range for this chip), so ClawDeck sets the chip's own limit." + rapl25,
            quick ? undefined : "each watt more buys fps with play time and heat.")} />
        <Status ok={err ? false : null} label={(line ?? reads(st)) + (thermalLine ? ` · ${thermalLine}` : "")}
          detail={thermalLine ? `Package ${at?.thermal?.temp_c ?? "?"} °C. thermald is paused while ClawDeck holds the TDP, so ClawDeck steps it down 2 W at a time above 95 °C until under 90 °C. Picking a profile or moving the slider sets it again.`
            : app ? app.name : "No game running: the profile applies when one starts"} />
        <Problem error={err} what="Power limit" />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Autotune" checked={autotune} onChange={async (v) => { setAutotune(v); await setSetting("autotune", v); refresh(); }}
          description={explain(`Finds the most frames the TDP above gives, shares that power between CPU and graphics where the game needs it, then lowers the TDP 1 W at a time while the game stays within ${st?.autotune_keep ?? 5} % of them. Counts rendered frames, not the ones frame generation adds.`,
            quick ? undefined : "nearly the same frames for less power: longer play time. With a frame limit (Steam's), it holds the limit with the least power.", quick ? undefined : "it spends a few seconds at the full TDP whenever it measures again (new area, every few minutes).")} />
        <Status ok={!autotune ? false : running ? true : null} label={atLine} detail={atDetail} />
      </PanelSectionRow>
      {app && autotune && !quick && <LaunchWrapper appid={app.appid} name={app.name} />}
      {st && (
        <Advanced id="power" label="Advanced"
          description={quick ? undefined : explain("CPU boost, CPU ceiling, GPU clocks, CPU energy mode and Autotune's floor. Steam has none of these for this chip.", undefined, "a change here pauses Autotune for this game.")}>
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
          <PanelSectionRow>
            <DropdownItem label="CPU energy mode" disabled={!st.cpu.epp_available} rgOptions={EPP} selectedOption={st.cpu.epp ?? "balance_performance"}
              onChange={(o) => setEpp(o.data).then(onLever)}
              description={explain(`How eagerly the CPU clocks up, like Windows' power mode. Reads ${EPP.find((e) => e.data === st.cpu.epp)?.label ?? st.cpu.epp ?? "?"}.`,
                quick ? undefined : "profiles set it for you.", "Fastest uses more battery at idle.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="Autotune: keep within (%)" value={st.autotune_keep ?? 5} min={2} max={15} step={1} showValue
              onChange={(v) => latest("keep", () => setAutotuneKeep(v), (r) => { toast(r.summary); refresh(); })}
              description={explain("How many of the most frames the TDP gives Autotune may trade for power (5 % of 60 fps = 3 fps).",
                quick ? undefined : "smaller keeps more frames; larger saves more battery.", undefined)} />
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
