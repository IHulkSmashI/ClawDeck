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
import { explain, Note, Status } from "../components/Hint";
import { runningApp } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

// names and what each is for; the watts come from the backend's table for this Claw (hw.PROFILES)
const PROFILES: { id: string; name: string; watts: number; use: string }[] = [
  { id: "battery", name: "Battery", watts: 12, use: "light games, longest play time" },
  { id: "balanced", name: "Balanced", watts: 17, use: "heavier games on battery" },
  { id: "performance", name: "Performance", watts: 25, use: "plugged in" },
  { id: "dock", name: "Dock", watts: 35, use: "on a TV, fans audible" },
];

/** quick = the Quick Access panel: profile and Autotune only. The full page adds everything under Advanced. */
export const PowerTab: FC<{ quick?: boolean }> = ({ quick }) => {
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
          <Status ok={null} label={now} detail={app ? app.name : "No game running"} />
        </PanelSectionRow>
        {otherPower.length > 0 && (
          <PanelSectionRow>
            <Note><span style={{ color: "#f0b429" }}>{otherPower.join(", ")} also sets power. Two power plugins undo each other: keep one.</span></Note>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DropdownItem label="Profile" rgOptions={[
            ...PROFILES.map((p) => ({ data: p.id, label: `${p.name} · ${wattsOf(p)} W` })),
            ...(custom ? [{ data: "custom", label: `Custom · ${tdp} W` }] : []),
          ]} selectedOption={profile} onChange={(o) => { if (o.data !== "custom") pick(o.data); }}
            description={explain(custom ? "Your own power limit (Advanced)." : `${picked.name}: ${picked.use}. More watts = more fps, fewer = longer play time.`)} />
        </PanelSectionRow>
        {appid && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => {
              await saveGamePower(appid, { base: custom ? "battery" : profile, tdp: limitW, cpu_max_pct: cpuPct });
              toast(`${app?.name}: ${custom ? "Custom" : picked.name} (${limitW} W) every launch, for the current power source`);
            }} description={quick ? undefined : explain("Every launch of this game uses it (battery and charger separately). Otherwise it resets when the game closes.")}>Use this for {app?.name}</ButtonItem>
          </PanelSectionRow>
        )}
        {steamSlider && !quick && (
          <PanelSectionRow>
            <Note>Leave Steam's own TDP slider (Quick Access → Performance) off: ClawDeck sets the power.</Note>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Autotune">
        <PanelSectionRow>
          <ToggleField label="Save power automatically" checked={autotune} onChange={async (v) => { setAutotune(v); await setSetting("autotune", v); refresh(); }}
            description={explain(quick ? `Holds your target fps with the least power, up to ${limitW} W.` : `Holds your target fps with the least power, never more than your profile (${limitW} W), and gives CPU or graphics the power where the game needs it. Can't hold it? Pick a higher profile.`)} />
        </PanelSectionRow>
        {autotune && (
          <PanelSectionRow>
            <SliderField label="Target fps (this game)" value={targetFps} min={30} max={120} step={5} showValue disabled={!appid}
              description={explain(appid ? "Use the same number as Steam's frame limit." : "Start a game to set its target.")}
              onChange={async (v) => { setTargetFps(v); if (appid) { await setGameProfile(appid, false, { target_fps: v }); await setGameProfile(appid, true, { target_fps: v }); } }} />
          </PanelSectionRow>
        )}
        {app && autotune && !quick && <LaunchWrapper appid={app.appid} name={app.name} />}
        {at?.running && (
          <PanelSectionRow>
            <Status ok={at.paused ? false : null}
              label={at.paused ? "Autotune paused" : at.avg_fps != null ? `${Math.round(at.avg_fps)} fps average · ${at.learned?.tdp} W of ${limitW} W` : "Measuring…"}
              detail={quick && !at.paused ? undefined : at.paused ? "You changed CPU or GPU settings by hand. Pick a profile to hand control back."
                : [at.low_fps != null ? `${Math.round(at.low_fps)} fps at worst (last ${at.window_s} s)` : null,
                   at.learned?.bound === "gpu" ? "graphics-heavy: more power to the GPU" : at.learned?.bound === "cpu" ? "CPU-heavy: more power to the CPU" : null,
                   at.learned?.cpu_max_pct != null && at.learned.cpu_max_pct < 100 ? `CPU capped at ${at.learned.cpu_max_pct}%` : null].filter(Boolean).join(" · ")} />
          </PanelSectionRow>
        )}
      </PanelSection>

      {!quick && (
      <PanelSection title="Fine-tuning">
        <Advanced id="power">
          <PanelSectionRow>
            <SliderField label="Custom power limit (W)" value={tdp} min={lim.min} max={lim.plugged_max} step={1} showValue
              disabled={!st?.tdp_backend} description={explain("Your own limit instead of a profile's, shared by CPU and graphics. Autotune stays under it.")}
              onChange={async (v) => {
                setTdpV(v); setProfile("custom");
                if (!(await setTdp(v))) toast(st?.hhd ? "Not applied: enable TDP controls in Handheld Daemon once" : "Not applied: see Setup → Power limit control");
              }} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="CPU limit (%)" value={cpuPct} min={10} max={100} step={5} showValue disabled={!st?.cpu.available}
              description={explain("Gives graphics-heavy games more of the power budget; try 70–80%. CPU-heavy scenes get slower.")}
              onChange={async (v) => { setCpuPct(v); await setCpuMaxPct(v); }} />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem label="CPU energy mode" disabled={!st?.cpu.epp_available} rgOptions={[
              { data: "power", label: "Most battery" }, { data: "balance_power", label: "Balanced, battery first" },
              { data: "balance_performance", label: "Balanced, speed first" }, { data: "performance", label: "Fastest" },
            ]} selectedOption={st?.cpu.epp ?? "balance_performance"} onChange={async (o) => { await setEpp(o.data); refresh(); }}
              description={explain("How fast the CPU speeds up, like Windows' power mode. Profiles set it for you.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="Power boost" checked={boost} onChange={async (v) => { setBoost(v); await setTdpBoost(v); if (st?.tdp_backend) await setTdp(limitW); }}
              description={explain("Short bursts above the limit for snappier loading. Off keeps power exactly at the limit.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="CPU turbo" checked={st?.cpu.no_turbo === 0} disabled={st?.cpu.no_turbo == null}
              onChange={async (v) => { await setCpuTurbo(v); refresh(); }} description={explain("Off is quieter and cooler, and costs fps in CPU-heavy games.")} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU minimum clock (MHz)" value={gpuMin} min={gpuHwMin} max={gpuHwMax} step={50} showValue disabled={!st?.gpu.available}
              description={explain("Stops stutter from clock dips; the GPU never idles while a game runs.")}
              onChange={async (v) => { setGpuMin(v); await setGpuWindow(v, null); }} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="GPU maximum clock (MHz)" value={gpuMax} min={gpuHwMin} max={gpuHwMax} step={50} showValue disabled={!st?.gpu.available}
              description={explain("Hands power back to the CPU in CPU-heavy games; lowers peak fps.")}
              onChange={async (v) => { setGpuMax(v); await setGpuWindow(null, v); }} />
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
              <Status ok={null} label={`GPU now ${st.gpu.cur_mhz ?? "?"} MHz`}
                detail={`Range ${st.gpu.hw_min_mhz}–${st.gpu.hw_max_mhz} MHz · power via ${st.tdp_backend === "hhd" ? `Handheld Daemon (${st.tdp?.ready ? st.tdp?.mode : "TDP controls not enabled"})` : st.tdp_backend === "msi-firmware" ? `MSI firmware (now ${st.firmware_tdp?.pl1_w ?? "?"} W, ${st.firmware_tdp?.pl2_w ?? "?"} W boost)` : st.tdp_backend ?? "nothing"}`} />
            </PanelSectionRow>
          )}
          <BenchmarkSection appid={appid} name={app?.name ?? ""} profile={profile} />
          <ShareSection appid={appid} />
        </Advanced>
      </PanelSection>
      )}
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
        <ButtonItem layout="below" disabled={!appid && !running}
          description={explain("Records fps, 1% lows and watts with the current settings, to compare profiles with real numbers. Needs the game in launch options.")}
          onClick={async () => {
          if (running) { const r = await benchStop(); toast(r?.avg_fps ? `${r.avg_fps} fps avg, ${r.low1_fps} 1% low, ${r.avg_w ?? "?"} W` : "Stopped (no frame data: is the launch wrapper set?)"); }
          else { const r = await benchStart(appid!, name, profile, duration); if (!r.ok) toast(r.error ?? "could not start"); else toast(`Recording ${duration} s: keep playing the same scene`); }
          load();
        }}>{running ? "Stop benchmark" : "Start benchmark (this game)"}</ButtonItem>
      </PanelSectionRow>
      {results.map((r, i) => (
        <PanelSectionRow key={i}>
          <Status ok={null} label={`${r.name} · ${r.label}: ${r.avg_fps} fps (${r.low1_fps} low)`} detail={`${r.time} · ${r.avg_w ?? "?"} W · ${r.fps_per_w ?? "?"} fps per watt`} />
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
        <ButtonItem layout="below" onClick={async () => toast(`Saved: ${await exportProfilesFile(appid)}`)}
          description={explain("Saves the settings as a file on the Desktop, to share so others skip the learning.")}>
          {appid ? "Export this game's profile" : "Export all game profiles"}</ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem label="Import profile file" disabled={!files.length}
          rgOptions={files.map((f) => ({ data: f, label: f.split("/").pop() ?? f }))} selectedOption={pick}
          onChange={(o) => setPick(o.data)} description={explain("A clawdeck-*.json from Desktop or Downloads. Never overwrites a game you already set up.")} />
        <ButtonItem layout="below" disabled={!pick} onClick={async () => {
          const r = await importProfilesFile(pick!, false);
          toast(r.ok ? `Imported ${r.imported} game(s); games you already have were kept` : `Import failed: ${r.error}`);
        }}>Import</ButtonItem>
      </PanelSectionRow>
    </>
  );
};
