import { PanelSection, PanelSectionRow } from "@decky/ui";
import { FC } from "react";
import { Hint } from "../components/Hint";

export const DisplayTab: FC = () => (
  <PanelSection title="Display and dock">
    <PanelSectionRow>
      <div style={{ fontSize: 12 }}>Coming in the next milestone, after the dock session is verified on the Claw 8 EX:</div>
      <Hint does="VRR range on the internal 120 Hz panel; 4K120 HDR gamescope flags for the dock; per-game refresh rate." why="one place for the settings that today live in launch options." cost="none." />
    </PanelSectionRow>
  </PanelSection>
);
