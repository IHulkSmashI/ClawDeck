import { DropdownItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getBattery, setChargeLimit, BatteryStatus } from "../api";
import { Hint } from "../components/Hint";

const CHOICES = [50, 60, 70, 80, 90, 100];

export const BatteryTab: FC = () => {
  const [b, setB] = useState<BatteryStatus | null>(null);
  const [limit, setLimit] = useState(100);
  const refresh = async () => { const s = await getBattery(); setB(s); if (s.charge_limit) setLimit(s.charge_limit); };
  useEffect(() => { refresh(); }, []);
  return (
    <>
      <PanelSection title="Battery">
        <PanelSectionRow>
          <div style={{ fontSize: 12 }}>
            {b?.present ? (
              <>
                <b>{b.capacity_pct}%</b> · {b.status} {b.power_w != null && <>· {b.power_w.toFixed(1)} W</>}
                <div style={{ opacity: 0.7 }}>
                  {b.energy_wh != null && b.energy_full_wh != null && <>{b.energy_wh.toFixed(1)} / {b.energy_full_wh.toFixed(1)} Wh · </>}
                  health {b.health_pct ?? "?"}% · {b.plugged ? "plugged in" : "on battery"}
                </div>
              </>
            ) : "no battery found"}
          </div>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Charge limit">
        <PanelSectionRow>
          <DropdownItem label="Stop charging at" disabled={!b?.charge_limit_supported}
            rgOptions={CHOICES.map((c) => ({ data: c, label: c === 100 ? "100% (no limit)" : `${c}%` }))}
            selectedOption={limit}
            onChange={async (o) => {
              setLimit(o.data);
              const ok = await setChargeLimit(o.data);
              toaster.toast({ title: "ClawDeck", body: ok ? `Charge limit ${o.data}%` : "Charge limit not supported on this kernel" });
              refresh();
            }} />
          <Hint does="Stops charging at the chosen percent; re-applied at every boot." why="80% roughly doubles battery lifespan; 50–60% for a unit that lives on the dock." cost="less runtime per charge." />
        </PanelSectionRow>
        {b && !b.charge_limit_supported && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.7 }}>This kernel does not expose charge_control_end_threshold for the Claw yet. Handheld Daemon's own limit (70–95%) appears here once it does.</div>
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
};
