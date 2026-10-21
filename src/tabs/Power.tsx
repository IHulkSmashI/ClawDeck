import { ButtonItem, PanelSection, PanelSectionRow, SliderField, ToggleField, DropdownItem } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import {
  applyProfile, getAutotune, getGameProfile, getPower, getSetting, resetGpuWindow, revertPower,
  setCpuMaxPct, setCpuTurbo, setGameProfile, setGpuWindow, setSetting, setTdp, AutotuneStatus, PowerStatus,
} from "../api";
import { Hint } from "../components/Hint";

declare const Router: any;

const runningAppId = (): string | null => {
  try { const id = Router?.MainRunningApp?.appid; return id ? String(id) : null; } catch { return null; }
};

export const PowerTab: FC = () => {
  const [st, setSt] = useState<PowerStatus | null>(null);
  const [at, setAt] = useState<AutotuneStatus | null>(null);
  const [autotune, setAutotune] = useState(false);
  const [targetFps, setTargetFps] = useState(60);
  const [tdp, setTdpV] = useState(20);
  const [cpuPct, setCpuPct] = useState(100);
  const [gpuMin, setGpuMin] = useState(0);
  const [gpuMax, setGpuMax] = useState(0);
  const appid = runningAppId();

  const refresh = async () => {
    const s = await getPower(); setSt(s);
    if (s.tdp?.custom_tdp) setTdpV(s.tdp.custom_tdp);
    if (s.cpu.max_perf_pct != null) setCpuPct(s.cpu.max_perf_pct);
    if (s.gpu.min_mhz != null) setGpuMin(s.gpu.min_mhz);
    if (s.gpu.max_mhz != null) setGpuMax(s.gpu.max_mhz);
    setAutotune(!!(await getSetting("autotune")));
    setAt(await getAutotune());
    if (appid) {
      const gp = await getGameProfile(appid);
      const p = gp.battery;
      if (p.target_fps) setTargetFps(p.target_fps);
    }
  };
  useEffect(() => { refresh(); }, []);

  const lim = st?.limits ?? { min: 8, battery_max: 35, plugged_max: 35, sppt_max: 45 };
  const gpuHwMin = st?.gpu.hw_min_mhz ?? 300, gpuHwMax = st?.gpu.hw_max_mhz ?? 2400;

  return (
    <>
      <PanelSection title="Autotune">
        <PanelSectionRow>
          <ToggleField label="Autotune" checked={autotune} onChange={async (v) => { setAutotune(v); await setSetting("autotune", v); }} />
          <Hint does="Learns the lowest power that holds your target fps, per game, and whether the game is CPU- or GPU-bound." why="longer battery, same smoothness; applied automatically next launch." cost="the first minutes in a new game may stutter while it learns." />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField label="Target fps (this game)" value={targetFps} min={30} max={120} step={5} showValue
            disabled={!appid}
            onChange={async (v) => { setTargetFps(v); if (appid) await setGameProfile(appid, false, { target_fps: v }); if (appid) await setGameProfile(appid, true, { target_fps: v }); }} />
          <Hint does={appid ? "Saved for the running game." : "Start a game to set its target."} />
        </PanelSectionRow>
        {at?.running && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.8 }}>
              Learning: {at.learned?.tdp} W · CPU {at.learned?.cpu_max_pct}% · GPU floor {at.learned?.gpu_min_mhz ?? "auto"} · {at.learned?.bound}-bound · confidence {at.learned?.confidence}/5
              {at.last?.fps != null && <> · {Math.round(at.last.fps)} fps</>}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Profile">
        <PanelSectionRow>
          <DropdownItem label="Apply profile" rgOptions={[
            { data: "battery", label: "Battery (best frames per watt)" },
            { data: "performance", label: "Performance (plugged in)" },
            { data: "dock", label: "Dock (full power, fans audible)" },
          ]} selectedOption={"battery"} onChange={async (o) => {
            const r = await applyProfile(o.data, appid);
            toaster.toast({ title: "ClawDeck", body: r.failed?.length ? `Applied with failures: ${r.failed.join(", ")}` : `${o.data} profile applied` });
            refresh();
          }} />
          <Hint does="Sets TDP, CPU ceiling and GPU clocks together." why="one tap when you dock or unplug." cost="none; reverts when the game closes unless saved." />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={async () => { await revertPower(); toaster.toast({ title: "ClawDeck", body: "Firmware defaults restored" }); refresh(); }}>Revert to defaults</ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Manual">
        <PanelSectionRow>
          <SliderField label="TDP (W)" value={tdp} min={lim.min} max={lim.sppt_max} step={1} showValue
            disabled={!st?.hhd && !st?.rapl.available}
            onChange={async (v) => { setTdpV(v); if (!(await setTdp(v))) toaster.toast({ title: "ClawDeck", body: "TDP not applied: enable TDP controls in Handheld Daemon once" }); }} />
          <Hint does="Package power limit, shared by CPU and GPU (through Handheld Daemon)." why="17–25 W on battery is the sweet spot; 35 W docked." cost="more watts = more heat and fan noise; above the battery limit only when plugged in." />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField label="CPU ceiling (%)" value={cpuPct} min={10} max={100} step={5} showValue disabled={!st?.cpu.available}
            onChange={async (v) => { setCpuPct(v); await setCpuMaxPct(v); }} />
          <Hint does="Caps CPU clocks so the GPU gets the power budget." why="GPU-bound games: same fps, less heat; try 70–80%." cost="CPU-heavy scenes (towns, crowds) get slower." />
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
              GPU now {st.gpu.cur_mhz ?? "?"} MHz (hw {st.gpu.hw_min_mhz}–{st.gpu.hw_max_mhz}) · HHD {st.hhd ? (st.tdp?.ready ? "ready" : "TDP controls not enabled") : "not running"} · firmware {st.msi_firmware ? "msi-wmi" : "RAPL"}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
};
