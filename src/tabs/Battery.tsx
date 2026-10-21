import { DropdownItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getBattery, getFans, setChargeLimit, setFanPreset, BatteryStatus, FanStatus } from "../api";
import { explain, Note, Status } from "../components/Hint";

const CHOICES = [50, 60, 70, 80, 90, 100];
const FAN_LABELS: Record<string, string> = {
  auto: "Auto (firmware curve)", quiet: "Quiet", balanced: "Balanced (Handheld Daemon default)", cool: "Cool", custom: "Custom (set in Handheld Daemon)",
};

export const BatteryTab: FC = () => {
  const [b, setB] = useState<BatteryStatus | null>(null);
  const [f, setF] = useState<FanStatus | null>(null);
  const [limit, setLimit] = useState(100);
  const refresh = async () => {
    const s = await getBattery(); setB(s); if (s.charge_limit) setLimit(s.charge_limit);
    setF(await getFans());
  };
  useEffect(() => { refresh(); }, []);
  return (
    <>
      <PanelSection title="Battery">
        <PanelSectionRow>
          {b?.present ? (
            <Status ok={null} label={`${b.capacity_pct}% · ${b.status}${b.power_w != null ? ` · ${b.power_w.toFixed(1)} W` : ""}`}
              detail={`${b.energy_wh != null && b.energy_full_wh != null ? `${b.energy_wh.toFixed(1)} / ${b.energy_full_wh.toFixed(1)} Wh · ` : ""}health ${b.health_pct ?? "?"}% · ${b.plugged ? "plugged in" : "on battery"}`} />
          ) : <Status ok={null} label={b ? "No battery found" : "…"} />}
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Charge limit">
        <PanelSectionRow>
          <DropdownItem label="Stop charging at" disabled={!b?.charge_limit_supported}
            rgOptions={CHOICES.map((c) => ({ data: c, label: c === 100 ? "100% (no limit)" : `${c}%` }))}
            selectedOption={limit}
            description={explain("Batteries wear slower when they don't sit at 100%. 80% for every day, 50–60% if your Claw lives on the dock.")}
            onChange={async (o) => {
              setLimit(o.data);
              const ok = await setChargeLimit(o.data);
              toaster.toast({ title: "ClawDeck", body: ok ? `Charge limit ${o.data}%` : "Charge limit not supported on this kernel" });
              refresh();
            }} />
        </PanelSectionRow>
        {b && !b.charge_limit_supported && (
          <PanelSectionRow>
            <Note>This system doesn't offer a charge limit for the Claw yet.</Note>
          </PanelSectionRow>
        )}
      </PanelSection>
      <PanelSection title="Fans">
        <PanelSectionRow>
          <DropdownItem label="Fan curve" disabled={!f?.available}
            rgOptions={(f?.presets ?? []).concat(f?.preset === "custom" ? ["custom"] : []).map((p) => ({ data: p, label: FAN_LABELS[p] ?? p }))}
            selectedOption={f?.preset ?? "auto"}
            description={explain(f?.available ? "Quiet for battery play, Cool when docked. Quiet runs warmer; Cool is louder." : "The Claw's own fan curve runs. Fan presets need Handheld Daemon for now.")}
            onChange={async (o) => {
              if (o.data === "custom") return;
              const r = await setFanPreset(o.data);
              toaster.toast({ title: "ClawDeck", body: r.ok ? `Fan curve: ${FAN_LABELS[o.data]}` : `Not applied: ${r.error ?? "Handheld Daemon refused"}` });
              refresh();
            }} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Status ok={null} label={f?.fan_rpm.length ? `Fan ${f.fan_rpm.join(" / ")} rpm` : "Fan speed not reported"}
            detail={f?.cpu_temp_c != null ? `CPU ${Math.round(f.cpu_temp_c)} °C` : undefined} />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
