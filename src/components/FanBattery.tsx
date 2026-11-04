import { DropdownItem, PanelSectionRow, SliderField } from "@decky/ui";
import { FC, useEffect, useRef, useState } from "react";

import { getCharge, getFan, setChargeLimit, setFan, ChargeState, FanState } from "../api";
import { Advanced } from "./Advanced";
import { Anchor, explain, Group, markPick, Problem, Status, useExplanations } from "./Hint";
import { notify } from "./notify";

const FAN_OPTIONS = [
  { data: "auto", label: "Automatic (MSI)" },
  { data: "quiet", label: "Quiet" },
  { data: "cooling", label: "Cooling" },
  { data: "full", label: "Full speed" },
  { data: "custom", label: "Custom" },
];
const FAN_DOES: Record<string, string> = {
  auto: "MSI's own fan control, as the Claw ships.",
  quiet: "Off at idle, then a gentle ramp: quieter in light games.",
  cooling: "Spins from idle and ramps earlier: cooler, so the chip holds its clocks longer.",
  full: "Both fans at full speed.",
  custom: "Your own six points (Fan curve below).",
};
const CHARGE_OPTIONS = [
  { data: "off", label: "Off (charges to 100 %)" },
  { data: "80", label: "80 %" },
  { data: "90", label: "90 %" },
  { data: "custom", label: "Custom" },
];
// kept outside the component: Steam's dropdown can re-mount Quick Access (picks were lost, 2026-11-04)
const kept: { fan?: string; charge?: string; chargePct?: number } = {};
const snap = (d: number) => (d <= 0 ? 0 : d < 40 ? (d < 20 ? 0 : 40) : Math.min(100, Math.round(d)));

/** Power → Fan and Battery: ClawTweaks' fan presets for the Claw (MSI's EC through the kernel's msi-wmi-platform
 * driver) and the battery charge limit. Both stay as set across games and restarts; "Automatic" / "Off" undo them. */
export const FanBattery: FC<{ quick?: boolean }> = ({ quick }) => {
  useExplanations();
  const [fs, setFs] = useState<FanState | null>(null);
  const [cs, setCs] = useState<ChargeState | null>(null);
  const [fanPick, setFanPick] = useState<string | undefined>(kept.fan);
  const [chargePick, setChargePick] = useState<string | undefined>(kept.charge);
  const [pct, setPct] = useState<number>(kept.chargePct ?? 85);
  const [custom, setCustom] = useState<number[] | null>(null);
  const [fanErr, setFanErr] = useState<string | null>(null);
  const [chErr, setChErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const load = async () => { const [f, c] = await Promise.all([getFan(), getCharge()]); setFs(f); setCs(c); };
  useEffect(() => {
    load().catch(() => undefined);
    const t = setInterval(() => getFan().then(setFs).catch(() => undefined), 3000);   // live rpm
    return () => clearInterval(t);
  }, []);
  if (!fs?.supported && !cs?.supported) return null;

  const fanNow = fanPick ?? fs?.preset ?? "auto";
  const curve = custom ?? fs?.custom ?? [0, 40, 40, 45, 60, 75];
  const temps = fs?.fans?.[0]?.temps ?? [];
  const rpm = (fs?.fans ?? []).map((f) => f.rpm ?? "?").join(" / ");
  const pickFan = async (p: string, c?: number[]) => {
    kept.fan = p; setFanPick(p);
    const r = await setFan(p, p === "custom" ? (c ?? curve) : null);
    setFanErr(r.ok ? null : r.error);
    notify(r.summary);
    load();
  };
  const slide = (i: number, v: number) => {
    const next = curve.slice(); next[i] = snap(v); setCustom(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => pickFan("custom", next), 500);     // write once the slider stops
  };

  const setting = cs?.setting ?? null;
  const chargeNow = chargePick ?? (setting == null ? "off" : setting === 80 ? "80" : setting === 90 ? "90" : "custom");
  const writeCharge = async (v: number | null) => {
    const r = await setChargeLimit(v);
    setChErr(r.ok ? null : r.error);
    notify(r.summary);
    load();
  };
  const pickCharge = (k: string) => {
    kept.charge = k; setChargePick(k);
    if (k === "custom") { writeCharge(setting ?? pct); return; }
    writeCharge(k === "off" ? null : Number(k));
  };

  return (
    <>
      {fs?.supported && (
        <PanelSectionRow>
          <Group title="Fan" />
          <Anchor id="fan"><DropdownItem label={quick ? undefined : "Fan"} layout="below" childrenContainerWidth="max" rgOptions={FAN_OPTIONS}
            selectedOption={fanNow} onChange={(o) => { markPick("fan"); pickFan(String(o.data)); }}
            description={explain(FAN_DOES[fanNow] ?? "", quick ? undefined : "the same presets as ClawTweaks on Windows; Steam has no fan control for the Claw.",
              fanNow === "full" ? "loud" : fanNow === "quiet" ? "warmer: the chip may lower its clocks sooner in heavy games" : undefined)} /></Anchor>
          <Status ok={fanErr ? false : null} label={`Fans now: ${rpm} rpm`} detail={fs.fans?.[0]?.enable === 1 ? "Following ClawDeck's fan table" : "MSI automatic"} />
          <Problem error={fanErr} what="Fan" />
        </PanelSectionRow>
      )}
      {fs?.supported && fanNow === "custom" && (
        <Advanced id="fan" label="Fan curve" description={explain("Six points, both fans. The temperatures are MSI's own and fixed; set how fast the fans spin at each. 0 = off; between 1 and 40 the fan spins at its minimum anyway, so the slider jumps to 40.")}>
          {curve.map((d, i) => (
            <PanelSectionRow key={i}>
              <SliderField label={`${temps[i] ?? "?"} °C`} value={d} min={0} max={100} step={1} showValue
                onChange={(v) => slide(i, v)} />
            </PanelSectionRow>
          ))}
        </Advanced>
      )}
      {cs?.supported && (
        <PanelSectionRow>
          <Group title="Battery" />
          <Anchor id="charge"><DropdownItem label={quick ? undefined : "Charge limit"} layout="below" childrenContainerWidth="max" rgOptions={CHARGE_OPTIONS}
            selectedOption={chargeNow} onChange={(o) => { markPick("charge"); pickCharge(String(o.data)); }}
            description={explain("Stops charging at this level.", quick ? undefined : "a Claw that lives on the charger keeps its battery healthier for longer at 80 %.",
              "less play time when you unplug it.")} /></Anchor>
          {chargeNow === "custom" && (
            <SliderField label="Stop charging at (%)" value={setting ?? pct} min={50} max={100} step={5} showValue
              onChange={(v) => { kept.chargePct = v; setPct(v); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => writeCharge(v), 500); }} />
          )}
          <Status ok={chErr ? false : null} label={`Battery stops at: ${!cs.limit ? "100 % (no limit)" : `${cs.limit} %`}`} detail="Read back from the battery" />
          <Problem error={chErr} what="Charge limit" />
        </PanelSectionRow>
      )}
    </>
  );
};
