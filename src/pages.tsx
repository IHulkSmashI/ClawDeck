import { ButtonItem, Navigation, PanelSection, PanelSectionRow, SidebarNavigation, ToggleField } from "@decky/ui";
import { FC, useEffect, useState } from "react";

import { getSetting, setSetting, xessState } from "./api";
import { FirstRun } from "./components/FirstRun";
import { HelpContext } from "./components/Hint";
import { MdBolt, MdBuild, MdSportsEsports } from "react-icons/md";

import { FixesTab } from "./tabs/Fixes";
import { GameSection } from "./tabs/Game";
import { PowerTab } from "./tabs/Power";
import { SetupTab } from "./tabs/Setup";

export const ROUTE = "/clawdeck";
export const PAGES = ["power", "game", "fixes", "setup"] as const;

/** Opens the full-screen ClawDeck page (optionally on one section) and closes the Quick Access menu. */
export const openPage = (page = "power") => {
  Navigation.Navigate(`${ROUTE}/${page}`);
  Navigation.CloseSideMenus();
};

/**
 * The Quick Access panel holds the four panels (Power, Per-game, Fixes, Setup) in their short form: short labels,
 * the read-back line after every change, nothing that needs a settings page. The full-screen page below has the
 * same four with the longer explanations and the health check's details.
 */
export const QuickPanel: FC = () => {
  const [help, setHelp] = useState(false);
  const [setupDone, setSetupDone] = useState<boolean | null>(null);
  useEffect(() => { getSetting("quick_help").then((v) => setHelp(!!v)); }, []);
  useEffect(() => { Promise.all([getSetting("setup_done"), xessState()]).then(([d, x]) => setSetupDone(!!d || !!x?.installed)).catch(() => setSetupDone(true)); }, []);
  return (
  <HelpContext.Provider value={help}>
    <PanelSection>
      <PanelSectionRow>
        <ToggleField label="Explain" checked={help} bottomSeparator="none"
          description={help ? "What each control does, why you'd use it and what it costs." : "Show what each control does."}
          onChange={(v) => { setHelp(v); setSetting("quick_help", v); }} />
      </PanelSectionRow>
    </PanelSection>
    {setupDone === false && <FirstRun onDone={() => setSetupDone(true)} />}
    <PowerTab quick />
    {setupDone && <GameSection quick />}
    <FixesTab quick />
    <SetupTab quick />
    <PanelSection>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => openPage("power")}>Open the full view</ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  </HelpContext.Provider>
  );
};

/** The same four, one page each, navigated with the D-pad on the left like Steam's own settings. */
export const FullPage: FC<{ page?: string }> = ({ page }) => (
  <HelpContext.Provider value={true}>
  <SidebarNavigation title="ClawDeck" showTitle page={page} pages={[
    { title: "Power", icon: <MdBolt />, route: `${ROUTE}/power`, content: <PowerTab /> },
    { title: "Games", icon: <MdSportsEsports />, route: `${ROUTE}/game`, content: <GameSection /> },
    { title: "System", icon: <MdBuild />, route: `${ROUTE}/system`, content: <><FixesTab /><SetupTab /></> },
  ]} />
  </HelpContext.Provider>
);
