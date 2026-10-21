import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import {
  applyProfile, benchExport, benchResults, benchStart, benchStatus, benchStop, exportProfilesFile, getAutotune,
  getGameProfile, getHardware, getPower, getSetting, importProfilesFile, listProfileFiles, resetGpuWindow, resetLearned, revertPower,
  saveGamePower, setCpuMaxPct, setCpuTurbo, setEpp, setGameProfile, setTdpBoost, setGpuWindow, setSetting, setTdp, AutotuneStatus,
  BenchResult, PowerStatus,
} from "../api";
import { Advanced } from "../components/Advanced";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { Hint } from "../components/Hint";
import { runningApp } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

export const PowerTab: FC = () => {
  const [st, setSt] = useState<PowerStatus | null>(null);
  const [at, setAt] = useState<AutotuneStatus | null>(null);
  const [autotune, setAutotune] = useState(false);
  const [targetFps, setTargetFps] = useState(60);
  const [tdp, setTdpV] = useState(20);
  const [cpuPct, setCpuPct] = useState(100);
  const [gpuMin, setGpuMin] = useState(0);
  const [gpuMax, setGpuMax] = useState(0);
  const [profile, setProfile] = useState("battery");
  const [otherPower, setOtherPower] = useState<string[]>([]);
  const [boost, setBoost] = useState(true);
  const app = runningApp();
  const appid = app?.appid ?? null;

  const refresh = async () => {
    const s = await getPower(); setSt(s);
    if (s.tdp?.custom_tdp) setTdpV(s.tdp.custom_tdp);
    if (s.cpu.max_perf_pct != null) setCpuPct(s.cpu.max_perf_pct);
    if (s.gpu.min_mhz != null) setGpuMin(s.gpu.min_mhz);
    if (s.gpu.max_mhz != null) setGpuMax(s.gpu.max_mhz);
    setAutotune(!!(await getSetting("autotune")));
    setProfile((await getSetting("last_profile")) || "battery");
    setBoost((await getSetting("tdp_boost")) !== false);
    setOtherPower((await getHardware()).other_plugins?.power ?? []);
    setAt(await getAutotune());
    if (appid) {
      const gp = await getGameProfile(appid);
      if (gp.battery.target_fps) setTargetFps(gp.battery.target_fps);
    }
  };
  useEffect(() => { refresh(); }, []);

  const lim = st?.limits ?? { min: 8, battery_max: 35, plugged_max: 35, sppt_max: 45 };
  const gpuHwMin = st?.gpu.hw_min_mhz ?? 300, gpuHwMax = st?.gpu.hw_max_mhz ?? 2400;

  const steamSlider = st?.tdp_backend === "msi-firmware";
  const watts = st?.tdp_backend === "hhd" ? st?.tdp?.custom_tdp : st?.firmware_tdp?.pl1_w;
  const now = [
    `${profile[0].toUpperCase()}${profile.slice(1)} profile`,
    watts != null ? `${watts} W` : null,
    at?.running ? (at.paused ? "autotune paused" : "autotune learning") : autotune ? "autotune on" : null,
  ].filter(Boolean).join(" · ");

  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <div style={{ fontSize: 13 }}><b>Now:</b> {now}{app && <span style={{ opacity: 0.7 }}> · {app.name}</span>}</div>
        </PanelSectionRow>
        {otherPower.length > 0 && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, color: "#f0b429" }}>{otherPower.join(", ")} is also installed and sets power too. Two power plugins undo each other: keep one.</div>
          </PanelSectionRow>
        )}
        {steamSlider && (
          <PanelSectionRow>
            <Hint does="Leave Steam's own TDP limit (Quick Access → Performance) off while ClawDeck manages power." why="on Claws where Steam's slider works, both set the same limit; on the Claw 8 EX it may do nothing at all, and ClawDeck sets the limit directly." cost="none; turn it back on if you uninstall ClawDeck." />
          </PanelSectionRow>
        )}
      </PanelSection>
      <PanelSection title="Autotune">
        <PanelSectionRow>
          <ToggleField label="Autotune" checked={autotune} onChange={async (v) => { setAutotune(v); await setSetting("autotune", v); }} />
          <Hint does="Learns the lowest power that holds your target fps, per game, and whether the game is CPU- or GPU-bound." why="longer battery, same smoothness; applied automatically next launch." cost="the first minutes in a new game may stutter while it learns." />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField label="Target fps (this game)" value={targetFps} min={30} max={120} step={5} showValue disabled={!appid}
            onChange={async (v) => { setTargetFps(v); if (appid) { await setGameProfile(appid, false, { target_fps: v }); await setGameProfile(appid, true, { target_fps: v }); } }} />
          <Hint does={appid ? `Saved for ${app?.name}; starts learning at its next launch.` : "Start a game to set its target."} why="use the same number as Steam's frame limit (Performance menu), or none." cost="a target above Steam's frame limit can never be reached, so autotune would only add power." />
        </PanelSectionRow>
        {app && autotune && <LaunchWrapper appid={app.appid} name={app.name} />}
        {at?.running && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.8 }}>
              {at.paused ? "Paused (you changed power by hand)" : "Learning"}: {at.learned?.tdp} W · CPU {at.learned?.cpu_max_pct}% · GPU floor {at.learned?.gpu_min_mhz ?? "auto"} · {at.learned?.bound}-bound · confidence {at.learned?.confidence}/5
              {at.last?.fps != null && <> · {Math.round(at.last.fps)} fps</>}
              {at.last?.pkg_w != null && <> · {at.last.pkg_w.toFixed(1)} W</>}
            </div>
          </PanelSectionRow>
        )}
        {appid && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => { await resetLearned(appid); toast("Learned values cleared for this game"); }}>Reset what it learned (this game)</ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Profile">
        <PanelSectionRow>
          <DropdownItem label="Apply profile" rgOptions={[
            { data: "battery", label: "Battery (longest play time)" },
            { data: "balanced", label: "Balanced (heavier games, on battery)" },
            { data: "performance", label: "Performance (plugged in)" },
            { data: "dock", label: "Dock (full power, fans audible)" },
          ]} selectedOption={profile} onChange={async (o) => {
            setProfile(o.data);
            const r = await applyProfile(o.data, appid);
            toast(r.failed?.length ? `Applied with failures: ${r.failed.join(", ")}` : `${o.data} profile applied`);
            refresh();
          }} />
          <Hint does="Sets power limit, CPU limit and CPU energy mode together. Battery 12 W, Balanced 17 W, Performance 25 W, Dock 35 W (Claw 8 EX)." why="Battery for light games and long trips, Balanced for heavier games unplugged, Performance on the charger, Dock on a TV." cost="more watts = more heat, fan noise and less play time; reverts when the game closes unless saved." />
        </PanelSectionRow>
        {appid && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => {
              await saveGamePower(appid, { base: profile, tdp, cpu_max_pct: cpuPct });
              toast(`Saved for ${app?.name} (for the current power source)`);
            }}>Save as this game's profile</ButtonItem>
            <Hint does="Remembers the profile and the TDP/CPU values for this game, separately for battery and plugged in." why="it is applied every launch and follows the charger automatically." />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={async () => { await revertPower(); toast("Firmware defaults restored"); refresh(); }}>Revert to defaults</ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Manual">
        <PanelSectionRow>
          <SliderField label="TDP (W)" value={tdp} min={lim.min} max={lim.plugged_max} step={1} showValue
            disabled={!st?.tdp_backend}
            onChange={async (v) => { setTdpV(v); if (!(await setTdp(v))) toast(st?.hhd ? "TDP not applied: enable TDP controls in Handheld Daemon once" : "TDP not applied: see Setup → Power limit control"); }} />
          <Hint does={st?.hhd ? "Package power limit, shared by CPU and GPU (through Handheld Daemon)." : "Package power limit, shared by CPU and GPU (MSI firmware limits)."} why="lower lasts longer on battery; higher is for the dock. Autotune can pick it for you." cost={st?.hhd ? "more watts = more heat and fan noise; pauses autotune for this session." : "more heat and fan noise. Leave Steam's own TDP slider off so the two don't fight."} />
        </PanelSectionRow>
        <Advanced id="power">
          <PanelSectionRow>
            <SliderField label="CPU ceiling (%)" value={cpuPct} min={10} max={100} step={5} showValue disabled={!st?.cpu.available}
              onChange={async (v) => { setCpuPct(v); await setCpuMaxPct(v); }} />
            <Hint does="Caps CPU clocks so the GPU gets the power budget." why="GPU-bound games: same fps, less heat; try 70–80%." cost="CPU-heavy scenes (towns, crowds) get slower." />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem label="CPU energy mode" disabled={!st?.cpu.epp_available} rgOptions={[
              { data: "power", label: "Most battery" }, { data: "balance_power", label: "Balanced, battery first" },
              { data: "balance_performance", label: "Balanced, speed first" }, { data: "performance", label: "Fastest" },
            ]} selectedOption={st?.cpu.epp ?? "balance_performance"} onChange={async (o) => { await setEpp(o.data); refresh(); }} />
            <Hint does="How quickly the CPU speeds up when a game asks for more (like Windows' power mode)." why="profiles set it for you: battery first on Battery, fastest on Dock." cost="faster modes use more power for the same fps." />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="TDP boost" checked={boost} onChange={async (v) => { setBoost(v); await setTdpBoost(v); if (st?.tdp_backend) await setTdp(tdp); }} />
            <Hint does="Lets the Claw go above the TDP for a few seconds when a game needs a burst." why="snappier loading and menus." cost="off keeps power exactly at the TDP, which is easier on battery and heat." />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="CPU turbo" checked={st?.cpu.no_turbo === 0} disabled={st?.cpu.no_turbo == null}
              onChange={async (v) => { await setCpuTurbo(v); refresh(); }} />
            <Hint does="Allows boost clocks above base." why="off = quieter and cooler on battery." cost="off costs fps in CPU-bound games." />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU floor (MHz)" value={gpuMin} min={gpuHwMin} max={gpuHwMax} step={50} showValue disabled={!st?.gpu.available}
              onChange={async (v) => { setGpuMin(v); await setGpuWindow(v, null); }} />
            <Hint does="Stops the GPU clocking down between frames." why="fixes stutter from clock dips." cost="battery: the GPU never idles while a game runs." />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU ceiling (MHz)" value={gpuMax} min={gpuHwMin} max={gpuHwMax} step={50} showValue disabled={!st?.gpu.available}
              onChange={async (v) => { setGpuMax(v); await setGpuWindow(null, v); }} />
            <Hint does="Caps GPU clocks." why="CPU-bound games: hands power back to the CPU." cost="lower peak fps." />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => { await resetGpuWindow(); refresh(); }}>Reset GPU clocks</ButtonItem>
          </PanelSectionRow>
          {st && (
            <PanelSectionRow>
              <div style={{ fontSize: 11, opacity: 0.7 }}>
                GPU now {st.gpu.cur_mhz ?? "?"} MHz (hw {st.gpu.hw_min_mhz}–{st.gpu.hw_max_mhz}) · TDP via {st.tdp_backend === "hhd" ? `Handheld Daemon (${st.tdp?.ready ? st.tdp?.mode : "TDP controls not enabled"})` : st.tdp_backend === "msi-firmware" ? `MSI firmware (now ${st.firmware_tdp?.pl1_w ?? "?"} W / ${st.firmware_tdp?.pl2_w ?? "?"} W boost)` : st.tdp_backend ?? "nothing"}
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
