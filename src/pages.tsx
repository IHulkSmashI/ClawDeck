import { ButtonItem, Navigation, PanelSection, PanelSectionRow, SidebarNavigation } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import { MdBatteryChargingFull, MdBolt, MdDisplaySettings, MdGamepad, MdMonitorHeart, MdTune } from "react-icons/md";

import { getHealth, Health } from "./api";
import { Status } from "./components/Hint";
import { SetupTab } from "./tabs/Setup";
import { PowerTab } from "./tabs/Power";
import { GraphicsTab } from "./tabs/Graphics";
import { BatteryTab } from "./tabs/Battery";
import { DisplayTab } from "./tabs/Display";
import { ControllerTab } from "./tabs/Controller";

export const ROUTE = "/clawdeck";

/** Opens the full-screen ClawDeck page (optionally on one section) and closes the Quick Access menu. */
export const openPage = (page = "power") => {
  Navigation.Navigate(`${ROUTE}/${page}`);
  Navigation.CloseSideMenus();
};

/**
 * The Quick Access panel is narrow (about 300 px), so it holds only what people change while playing: the power
 * profile and Autotune, plus a health line. Everything else lives on the full-screen page below, where a sidebar
 * replaces the tab strip that didn't fit.
 */
export const QuickPanel: FC = () => {
  const [health, setHealth] = useState<Health[] | null>(null);
  useEffect(() => { getHealth().then(setHealth).catch(() => setHealth([])); }, []);
  const problems = (health ?? []).filter((h) => !h.ok && !h.optional);
  return (
    <>
      <PowerTab quick />
      <PanelSection title="ClawDeck">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => openPage("power")}>All settings</ButtonItem>
        </PanelSectionRow>
        {health && problems.length > 0 && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => openPage("setup")}
              description={problems.map((p) => p.title).join(" · ")}>{`${problems.length} to fix: open Setup`}</ButtonItem>
          </PanelSectionRow>
        )}
        {health && problems.length === 0 && (
          <PanelSectionRow>
            <Status ok={true} label="Everything is ready" />
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
};

/** One page per area, navigated with the D-pad on the left like Steam's own settings. */
export const FullPage: FC<{ page?: string }> = ({ page }) => (
  <SidebarNavigation title="ClawDeck" showTitle page={page} pages={[
    { title: "Power", icon: <MdBolt />, route: `${ROUTE}/power`, content: <PowerTab /> },
    { title: "Graphics", icon: <MdTune />, route: `${ROUTE}/graphics`, content: <GraphicsTab /> },
    { title: "Display & streaming", icon: <MdDisplaySettings />, route: `${ROUTE}/display`, content: <DisplayTab /> },
    { title: "Controller", icon: <MdGamepad />, route: `${ROUTE}/controller`, content: <ControllerTab /> },
    { title: "Battery & fans", icon: <MdBatteryChargingFull />, route: `${ROUTE}/battery`, content: <BatteryTab /> },
    { title: "Setup", icon: <MdMonitorHeart />, route: `${ROUTE}/setup`, content: <SetupTab /> },
  ]} />
);
