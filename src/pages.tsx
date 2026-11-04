import { ButtonItem, Navigation, PanelSection, PanelSectionRow, SidebarNavigation, ToggleField } from "@decky/ui";
import { FC, useEffect, useState } from "react";

import { getSetting, setSetting } from "./api";
import { HelpContext } from "./components/Hint";
import { MdBolt, MdBuild, MdSettings, MdSportsEsports, MdTune, MdViewList } from "react-icons/md";

import { FixesTab } from "./tabs/Fixes";
import { GamesTab } from "./tabs/Games";
import { PerGameTab } from "./tabs/PerGame";
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
  useEffect(() => { getSetting("quick_help").then((v) => setHelp(!!v)); }, []);
  return (
  <HelpContext.Provider value={help}>
    <PanelSection>
      <PanelSectionRow>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, lineHeight: "16px", color: "#b8bcbf", padding: "2px 0 6px" }}>
          <span style={{ flex: "none", display: "inline-flex", alignItems: "center", justifyContent: "center", width: 26, height: 20,
            borderRadius: 3, background: "#3d4450", color: "#fff", fontSize: 14 }}><MdSettings /></span>
          <span>Everyday controls are here. The <b>gear at the top right</b> opens the full view: every setting explained, the
            recommended setup for any game, the health check and Setup.</span>
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Explain" checked={help} bottomSeparator="none"
          description={help ? "What each control does, why you'd use it and what it costs." : "Show what each control does."}
          onChange={(v) => { setHelp(v); setSetting("quick_help", v); }} />
      </PanelSectionRow>
    </PanelSection>
    <PowerTab quick />
    <PerGameTab quick />
    <FixesTab quick />
    <SetupTab quick />
    <PanelSection>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => openPage("games")}>Set up a game (all installed games)</ButtonItem>
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
    { title: "Per-game", icon: <MdSportsEsports />, route: `${ROUTE}/game`, content: <PerGameTab /> },
    { title: "Games", icon: <MdViewList />, route: `${ROUTE}/games`, content: <GamesTab /> },
    { title: "Fixes", icon: <MdBuild />, route: `${ROUTE}/fixes`, content: <FixesTab /> },
    { title: "Setup", icon: <MdTune />, route: `${ROUTE}/setup`, content: <SetupTab /> },
  ]} />
  </HelpContext.Provider>
);
