import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import {
  applyProfile, benchExport, benchResults, benchStart, benchStatus, benchStop, exportProfilesFile, getAutotune,
  getGameProfile, getHardware, getPower, getSetting, importProfilesFile, listProfileFiles, listProfiles, resetGpuWindow, resetLearned, revertPower,
  saveGamePower, setCpuMaxPct, setCpuTurbo, setEpp, setGameProfile, setTdpBoost, setGpuWindow, setSetting, setTdp, AutotuneStatus,
  BenchResult, PowerStatus,
} from "../api";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { Hint } from "../components/Hint";
import { runningApp } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

// names and what each is for; the watts come from the backend's table for this Claw (hw.PROFILES)
const PROFILES: { id: string; name: string; watts: number; use: string }[] = [
  { id: "battery", name: "Battery", watts: 12, use: "light games, longest play time" },
  { id: "balanced", name: "Balanced", watts: 17, use: "heavier games on battery" },
  { id: "performance", name: "Performance", watts: 25, use: "plugged in" },
  { id: "dock", name: "Dock", watts: 35, use: "on a TV, fans audible" },
];

export const PowerTab: FC = () => {
  const [st, setSt] = useState<PowerStatus | null>(null);
  const [at, setAt] = useState<AutotuneStatus | null>(null);
  const [autotune, setAutotune] = useState(false);
  const [targetFps, setTargetFps] = useState(60);
  const [tdp, setTdpV] = useState(17);
  const [cpuPct, setCpuPct] = useState(100);
  const [gpuMin, setGpuMin] = useState(0);
  const [gpuMax, setGpuMax] = useState(0);
  const [profile, setProfile] = useState("battery");
  const [otherPower, setOtherPower] = useState<string[]>([]);
  const [boost, setBoost] = useState(true);
  const [table, setTable] = useState<Record<string, { tdp?: number }>>({});
  const app = runningApp();
  const appid = app?.appid ?? null;

  const refresh = async () => {
    const s = await getPower(); setSt(s);
    const w = s.tdp_backend === "hhd" ? s.tdp?.custom_tdp : s.firmware_tdp?.pl1_w;
    if (w) setTdpV(w);
    if (s.cpu.max_perf_pct != null) setCpuPct(s.cpu.max_perf_pct);
    if (s.gpu.min_mhz != null) setGpuMin(s.gpu.min_mhz);
    if (s.gpu.max_mhz != null) setGpuMax(s.gpu.max_mhz);
    setAutotune(!!(await getSetting("autotune")));
    setProfile((await getSetting("last_profile")) || "battery");
    setBoost((await getSetting("tdp_boost")) !== false);
    setOtherPower((await getHardware()).other_plugins?.power ?? []);
    setTable(await listProfiles());
    setAt(await getAutotune());
    if (appid) {
      const gp = await getGameProfile(appid);
      if (gp.battery.target_fps) setTargetFps(gp.battery.target_fps);
    }
  };
  useEffect(() => { refresh(); }, []);

  const lim = st?.limits ?? { min: 8, battery_max: 35, plugged_max: 35, sppt_max: 45 };
  const wattsOf = (p: { id: string; watts: number }) => Math.min(table[p.id]?.tdp ?? p.watts, lim.plugged_max);
  const gpuHwMin = st?.gpu.hw_min_mhz ?? 300, gpuHwMax = st?.gpu.hw_max_mhz ?? 2400;
  const steamSlider = st?.tdp_backend === "msi-firmware";
  const picked = PROFILES.find((p) => p.id === profile) ?? PROFILES[0];
  const custom = profile === "custom";
  const limitW = custom ? tdp : wattsOf(picked);
  const tuning = at?.running && !at.paused;
  const now = tuning && at?.learned?.tdp != null
    ? `Autotune is using ${at.learned.tdp} W of your ${limitW} W limit`
    : `${custom ? "Custom" : picked.name} · ${limitW} W`;

  const pick = async (id: string) => {
    setProfile(id);
    const r = await applyProfile(id, appid);
    const p = PROFILES.find((x) => x.id === id);
    toast(r.failed?.length ? `Applied with failures: ${r.failed.join(", ")}` : `${p?.name} · ${r.applied?.tdp ?? p?.watts} W`);
    refresh();
  };

  return (
    <>
      <PanelSection title="Power">
        <PanelSectionRow>
          <div style={{ fontSize: 13 }}><b>Now:</b> {now}{app && <span style={{ opacity: 0.7 }}> · {app.name}</span>}</div>
        </PanelSectionRow>
        {otherPower.length > 0 && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, color: "#f0b429" }}>{otherPower.join(", ")} is also installed and sets power too. Two power plugins undo each other: keep one.</div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DropdownItem label="Profile" rgOptions={[
            ...PROFILES.map((p) => ({ data: p.id, label: `${p.name} · ${wattsOf(p)} W · ${p.use}` })),
            ...(custom ? [{ data: "custom", label: `Custom · ${tdp} W` }] : []),
          ]} selectedOption={profile} onChange={(o) => { if (o.data !== "custom") pick(o.data); }} />
          <Hint does="How much power the Claw may use. More watts = more fps, fewer watts = longer play time." />
        </PanelSectionRow>
        {appid && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => {
              await saveGamePower(appid, { base: custom ? "battery" : profile, tdp: limitW, cpu_max_pct: cpuPct });
              toast(`${app?.name}: ${custom ? "Custom" : picked.name} (${limitW} W) every launch, for the current power source`);
            }}>Use this for {app?.name}</ButtonItem>
            <Hint does="Every launch of this game uses it (battery and charger separately). Otherwise it resets when the game closes." />
          </PanelSectionRow>
        )}
        {steamSlider && (
          <PanelSectionRow>
            <Hint does="Leave Steam's own TDP slider (Quick Access → Performance) off: ClawDeck sets the power." />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Autotune">
        <PanelSectionRow>
          <ToggleField label="Save power automatically" checked={autotune} onChange={async (v) => { setAutotune(v); await setSetting("autotune", v); refresh(); }} />
          <Hint does={`Holds your target fps with the least power, never more than your profile (${limitW} W), and splits that power between CPU and graphics where the game needs it. It judges the average over 10 seconds, waits after every change, and keeps rechecking. Can't hold it? Pick a higher profile.`} />
        </PanelSectionRow>
        {autotune && (
          <PanelSectionRow>
            <SliderField label="Target fps (this game)" value={targetFps} min={30} max={120} step={5} showValue disabled={!appid}
              onChange={async (v) => { setTargetFps(v); if (appid) { await setGameProfile(appid, false, { target_fps: v }); await setGameProfile(appid, true, { target_fps: v }); } }} />
            <Hint does={appid ? "Use the same number as Steam's frame limit." : "Start a game to set its target."} />
          </PanelSectionRow>
        )}
        {app && autotune && <LaunchWrapper appid={app.appid} name={app.name} />}
        {at?.running && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.8 }}>
              {at.paused ? "Paused because you changed CPU or GPU settings by hand. Pick a profile to hand control back" : `${at.learned?.tdp} W of ${limitW} W`}
              {at.avg_fps != null ? <> · {Math.round(at.avg_fps)} fps average, {Math.round(at.low_fps ?? at.avg_fps)} at worst (last {at.window_s} s)</> : <> · measuring…</>}
              {at.learned?.bound && at.learned.bound !== "balanced" && <> · {at.learned.bound === "gpu" ? "graphics-heavy: more power to the GPU" : "CPU-heavy: more power to the CPU"}</>}
              {at.learned?.cpu_max_pct != null && at.learned.cpu_max_pct < 100 && <> · CPU capped at {at.learned.cpu_max_pct}%</>}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection>
        <Advanced id="power">
          <PanelSectionRow>
            <SliderField label="Custom power limit (W)" value={tdp} min={lim.min} max={lim.plugged_max} step={1} showValue
              disabled={!st?.tdp_backend}
              onChange={async (v) => {
                setTdpV(v); setProfile("custom");
                if (!(await setTdp(v))) toast(st?.hhd ? "Not applied: enable TDP controls in Handheld Daemon once" : "Not applied: see Setup → Power limit control");
              }} />
            <Hint does="Your own power limit instead of a profile's (shared by CPU and GPU)." why="fine-tuning one game; Autotune then stays under this limit." cost="more watts = more heat, fan noise and less play time." />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="CPU limit (%)" value={cpuPct} min={10} max={100} step={5} showValue disabled={!st?.cpu.available}
              onChange={async (v) => { setCpuPct(v); await setCpuMaxPct(v); }} />
            <Hint does="Caps CPU clocks so the graphics get more of the power budget." why="graphics-heavy games: same fps, less heat; try 70–80%." cost="CPU-heavy scenes (towns, crowds) get slower." />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem label="CPU energy mode" disabled={!st?.cpu.epp_available} rgOptions={[
              { data: "power", label: "Most battery" }, { data: "balance_power", label: "Balanced, battery first" },
              { data: "balance_performance", label: "Balanced, speed first" }, { data: "performance", label: "Fastest" },
            ]} selectedOption={st?.cpu.epp ?? "balance_performance"} onChange={async (o) => { await setEpp(o.data); refresh(); }} />
            <Hint does="How quickly the CPU speeds up when a game asks for more (like Windows' power mode)." why="profiles set it for you: battery first on Battery and Balanced, fastest on Dock." cost="faster modes use more power for the same fps." />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="Power boost" checked={boost} onChange={async (v) => { setBoost(v); await setTdpBoost(v); if (st?.tdp_backend) await setTdp(limitW); }} />
            <Hint does="Lets the Claw go above the limit for a few seconds when a game needs a burst." why="snappier loading and menus." cost="off keeps power exactly at the limit, which is easier on battery and heat." />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="CPU turbo" checked={st?.cpu.no_turbo === 0} disabled={st?.cpu.no_turbo == null}
              onChange={async (v) => { await setCpuTurbo(v); refresh(); }} />
            <Hint does="Allows boost clocks above base." why="off = quieter and cooler on battery." cost="off costs fps in CPU-heavy games." />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU floor (MHz)" value={gpuMin} min={gpuHwMin} max={gpuHwMax} step={50} showValue disabled={!st?.gpu.available}
              onChange={async (v) => { setGpuMin(v); await setGpuWindow(v, null); }} />
            <Hint does="Stops the GPU clocking down between frames." why="fixes stutter from clock dips." cost="battery: the GPU never idles while a game runs." />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU ceiling (MHz)" value={gpuMax} min={gpuHwMin} max={gpuHwMax} step={50} showValue disabled={!st?.gpu.available}
              onChange={async (v) => { setGpuMax(v); await setGpuWindow(null, v); }} />
            <Hint does="Caps GPU clocks." why="CPU-heavy games: hands power back to the CPU." cost="lower peak fps." />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => { await resetGpuWindow(); refresh(); }}>Reset GPU clocks</ButtonItem>
          </PanelSectionRow>
          {appid && (
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={async () => { await resetLearned(appid); toast("Autotune starts over for this game"); }}>Forget what Autotune learned (this game)</ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => { await revertPower(); toast("Claw defaults restored"); refresh(); }}>Back to the Claw's defaults</ButtonItem>
          </PanelSectionRow>
          {st && (
            <PanelSectionRow>
              <div style={{ fontSize: 11, opacity: 0.7 }}>
                GPU now {st.gpu.cur_mhz ?? "?"} MHz (hw {st.gpu.hw_min_mhz}–{st.gpu.hw_max_mhz}) · power via {st.tdp_backend === "hhd" ? `Handheld Daemon (${st.tdp?.ready ? st.tdp?.mode : "TDP controls not enabled"})` : st.tdp_backend === "msi-firmware" ? `MSI firmware (now ${st.firmware_tdp?.pl1_w ?? "?"} W / ${st.firmware_tdp?.pl2_w ?? "?"} W boost)` : st.tdp_backend ?? "nothing"}
              </div>
            </PanelSectionRow>
          )}
          <BenchmarkSection appid={appid} name={app?.name ?? ""} profile={profile} />
          <ShareSection appid={appid} />
        </Advanced>
      </PanelSection>
    </>
  );
};

const BenchmarkSection: FC<{ appid: string | null; name: string; profile: string }> = ({ appid, name, profile }) => {
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<BenchResult[]>([]);
  const [duration, setDuration] = useState(120);
  const load = async () => { setRunning((await benchStatus()).running); setResults((await benchResults()).slice(-5).reverse()); };
  useEffect(() => { load(); }, []);
  return (
    <>
      <PanelSectionRow>
        <SliderField label="Benchmark length (s)" value={duration} min={30} max={600} step={30} showValue onChange={setDuration} />
        <ButtonItem layout="below" disabled={!appid && !running} onClick={async () => {
          if (running) { const r = await benchStop(); toast(r?.avg_fps ? `${r.avg_fps} fps avg, ${r.low1_fps} 1% low, ${r.avg_w ?? "?"} W` : "Stopped (no frame data: is the launch wrapper set?)"); }
          else { const r = await benchStart(appid!, name, profile, duration); if (!r.ok) toast(r.error ?? "could not start"); else toast(`Recording ${duration} s: keep playing the same scene`); }
          load();
        }}>{running ? "Stop benchmark" : "Start benchmark (this game)"}</ButtonItem>
        <Hint does="Records fps, 1% low and package watts at 1 s for a fixed time with the current settings." why="numbers for battery vs dock, or Linux vs Windows posts." cost="needs the launch wrapper (MangoHud log); runs only while recording." />
      </PanelSectionRow>
      {results.map((r, i) => (
        <PanelSectionRow key={i}>
          <div style={{ fontSize: 11, opacity: 0.8 }}>{r.time} · {r.name} · {r.label}: {r.avg_fps} fps ({r.low1_fps} low) · {r.avg_w ?? "?"} W · {r.fps_per_w ?? "?"} fps/W</div>
        </PanelSectionRow>
      ))}
      {results.length > 0 && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={async () => toast(`Saved: ${await benchExport()}`)}>Export benchmarks (CSV to Desktop)</ButtonItem>
        </PanelSectionRow>
      )}
    </>
  );
};

const ShareSection: FC<{ appid: string | null }> = ({ appid }) => {
  const [files, setFiles] = useState<string[]>([]);
  const [pick, setPick] = useState<string | null>(null);
  useEffect(() => { listProfileFiles().then((f) => { setFiles(f); setPick(f[0] ?? null); }); }, []);
  return (
    <>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={async () => toast(`Saved: ${await exportProfilesFile(appid)}`)}>
          {appid ? "Export this game's profile" : "Export all game profiles"}</ButtonItem>
        <Hint does="Writes the profile (power, autotune results, injector choice) as JSON to the Desktop." why="share it on GitHub or Reddit so others skip the learning." />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem label="Import profile file" disabled={!files.length}
          rgOptions={files.map((f) => ({ data: f, label: f.split("/").pop() ?? f }))} selectedOption={pick}
          onChange={(o) => setPick(o.data)} />
        <ButtonItem layout="below" disabled={!pick} onClick={async () => {
          const r = await importProfilesFile(pick!, false);
          toast(r.ok ? `Imported ${r.imported} game(s); games you already have were kept` : `Import failed: ${r.error}`);
        }}>Import</ButtonItem>
        <Hint does="Reads a clawdeck-*.json from Desktop or Downloads." why="use someone's tested settings." cost="never overwrites a game you already have a profile for." />
      </PanelSectionRow>
    </>
  );
};
