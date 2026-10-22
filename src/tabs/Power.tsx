import { DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useRef, useState } from "react";

import { applyProfile, getAutotune, getGameProfile, getPower, getSetting, listProfiles, setSetting, setTargetFps, AutotuneStatus, PowerStatus } from "../api";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Note, Problem, Status } from "../components/Hint";
import { runningApp } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

// short label in the control (nothing truncated at 1280x800), full name and use in the description under it
const PROFILES: { id: string; short: string; name: string; use: string }[] = [
  { id: "battery", short: "Battery", name: "Battery (longest play time)", use: "indie, older and lighter games, long trips" },
  { id: "balanced", short: "Balanced", name: "Balanced (heavier games on battery)", use: "newer games away from a charger; more fps, shorter play" },
  { id: "performance", short: "Performance", name: "Performance (plugged in, handheld)", use: "on the charger, playing handheld" },
  { id: "dock", short: "Dock", name: "Dock (on a TV, fans audible)", use: "on a dock with a TV; the firmware's ceiling" },
];

const backendName = (b: string | null | undefined) => (b === "rapl" ? "RAPL" : b === "hhd" ? "Handheld Daemon" : b === "msi-firmware" ? "MSI firmware" : "hardware");

/** The Power panel: one profile dropdown and the Autotune switch, with the read-back line after every change. */
export const PowerTab: FC<{ quick?: boolean }> = ({ quick }) => {
  const [st, setSt] = useState<PowerStatus | null>(null);
  const [at, setAt] = useState<AutotuneStatus | null>(null);
  const [autotune, setAutotune] = useState(false);
  const [targetFps, setTarget] = useState(60);
  const [profile, setProfile] = useState("battery");
  const [table, setTable] = useState<Record<string, { tdp?: number }>>({});
  const [reads, setReads] = useState<string | null>(null);    // the line after the last change: "RAPL reads 17 W / 24 W"
  const [err, setErr] = useState<string | null>(null);        // the last write that didn't take, in red
  const seq = useRef(0);                                      // only the newest profile change may toast or write state
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const app = runningApp();
  const appid = app?.appid ?? null;

  const refresh = async () => {
    const [s, auto, last, tbl, a, gp] = await Promise.all([
      getPower(), getSetting("autotune"), getSetting("last_profile"), listProfiles(), getAutotune(),
      appid ? getGameProfile(appid) : Promise.resolve(null),
    ]);
    setSt(s); setAutotune(!!auto); setProfile(last || "battery"); setTable(tbl); setAt(a);
    if (gp?.battery.target_fps) setTarget(gp.battery.target_fps);
  };
  useEffect(() => { refresh(); }, []);
  // while the panel is open and Autotune runs, its line follows along (one cheap call every 3 s, only while visible)
  useEffect(() => {
    if (!at?.running) return;
    const t = setInterval(() => { getAutotune().then(setAt).catch(() => undefined); }, 3000);
    return () => clearInterval(t);
  }, [at?.running]);

  const lim = st?.limits ?? { min: 8, battery_max: 35, plugged_max: 35, sppt_max: 45 };
  const wattsOf = (id: string) => Math.min(table[id]?.tdp ?? 0, lim.plugged_max) || undefined;
  const picked = PROFILES.find((p) => p.id === profile) ?? PROFILES[0];
  const limitW = at?.ceiling_tdp ?? wattsOf(picked.id) ?? st?.tdp_w ?? undefined;

  // Picked quickly through the list: wait 300 ms for the last choice, write once, and show the toast only after the
  // write completed and the value was read back; an older answer that arrives late is ignored.
  const pick = (id: string) => {
    setProfile(id);
    if (timer.current) clearTimeout(timer.current);
    const mine = ++seq.current;
    timer.current = setTimeout(async () => {
      const r = await applyProfile(id, appid);
      if (mine !== seq.current) return;
      toast(r.summary); setErr(r.ok ? null : r.error);
      setReads(`${backendName(r.read?.backend)} reads ${r.read?.tdp_w ?? "?"} W${r.read?.pl2_w ? ` / ${r.read.pl2_w} W` : ""}`);
      await refresh();
    }, 300);
  };

  const tuning = at?.running && !at.paused;
  const atLine = !autotune ? "Autotune off"
    : !at?.running ? (appid ? "Autotune on: waiting for frame times (is ClawDeck in the launch options?)" : "Autotune on: starts with the next game")
    : at.paused ? "Autotune paused: you changed power by hand"
    : at.avg_fps != null ? `Holding ${at.target_fps} fps: ${Math.round(at.avg_fps)} fps at ${at.learned?.tdp} W (limit ${at.ceiling_tdp} W)`
    : `Measuring for ${at.target_fps} fps at ${at.learned?.tdp} W`;

  return (
    <PanelSection title="Power">
      <PanelSectionRow>
        <DropdownItem label="Profile" rgOptions={PROFILES.map((p) => ({ data: p.id, label: wattsOf(p.id) ? `${p.short} ${wattsOf(p.id)} W` : p.short }))}
          selectedOption={profile} onChange={(o) => pick(o.data)}
          description={explain(`${picked.name}: ${picked.use}. The most power the game may use; Autotune saves below it.`)} />
        <Status ok={err ? false : null} label={reads ?? `${backendName(st?.tdp_backend)} reads ${st?.tdp_w ?? "?"} W${st?.tdp_backend === "rapl" && st.rapl.pl2_w ? ` / ${st.rapl.pl2_w} W` : ""}`}
          detail={app ? app.name : "No game running: the profile applies when one starts"} />
        <Problem error={err} what="Profile" />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Autotune" checked={autotune} onChange={async (v) => { setAutotune(v); await setSetting("autotune", v); refresh(); }}
          description={explain(quick ? `Holds the target fps with the least power, up to ${limitW ?? "the profile's"} W.`
            : `Holds the target fps with the least power, never more than the profile (${limitW ?? "?"} W), and moves power between CPU and graphics to where the game needs it. Can't hold it? Pick a higher profile.`)} />
        <Status ok={!autotune ? false : tuning ? true : null} label={atLine}
          detail={autotune && at?.running && !at.paused ? (at.last_action ? `Last change: ${at.last_action}` : "No change yet") : undefined} />
      </PanelSectionRow>
      {autotune && appid && (
        <PanelSectionRow>
          <SliderField label="Target fps (this game)" value={targetFps} min={30} max={120} step={5} showValue
            description={explain("Use the same number as Steam's frame limit.")}
            onChange={async (v) => { setTarget(v); await setTargetFps(appid, v); }} />
        </PanelSectionRow>
      )}
      {autotune && !appid && <PanelSectionRow><Note>Start a game to set its target fps.</Note></PanelSectionRow>}
      {app && autotune && !quick && <LaunchWrapper appid={app.appid} name={app.name} />}
    </PanelSection>
  );
};
