import { PanelSection, PanelSectionRow } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import { getHealth, Health } from "../api";
import { Hint, Status } from "../components/Hint";

export const ControllerTab: FC = () => {
  const [h, setH] = useState<Health | null>(null);
  useEffect(() => { getHealth().then((hs) => setH(hs.find((x) => x.id === "hhd") ?? null)); }, []);
  return (
    <PanelSection title="Controller (Handheld Daemon)">
      {h && <PanelSectionRow><Status ok={h.ok} label={h.title} detail={h.detail} /></PanelSectionRow>}
      <PanelSectionRow>
        <Hint does="The MSI buttons, gyro, RGB and back buttons are handled by Handheld Daemon; open its overlay with the right MSI button." why="if the buttons do nothing, update: Desktop Mode → terminal → rpm-ostree upgrade → reboot (the Claw 8 EX was added in 2026)." cost="none." />
      </PanelSectionRow>
    </PanelSection>
  );
};
