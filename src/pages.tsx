import { ButtonItem, Focusable, GamepadButton, GamepadEvent, Navigation, PanelSection, PanelSectionRow, SidebarNavigation, ToggleField } from "@decky/ui";
import { FC, ReactNode, useEffect, useState } from "react";

import { getSetting, setSetting, uiLog, xessState } from "./api";
import { FirstRun } from "./components/FirstRun";
import { LastMessage } from "./components/LastMessage";
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

type QuickTab = { id: string; title: string; content: ReactNode };
// the tab you were on, kept while the plugin is loaded: Steam's dropdowns re-mount Quick Access when their menu closes
// (2026-11-04), and coming back to ClawDeck lands where you left it
let lastTab = "power";

/** Four tabs always on screen at the top, switched with LB / RB (also the D-pad and A on the strip), one tab's
 * settings below. Replaces the four folding sections, which took a press to open and another to close (#171,
 * maintainer 2026-11-14: "tabs that are always on screen to pick between with rb and lb"). */
export const QuickTabs: FC<{ tabs: QuickTab[] }> = ({ tabs }) => {
  const [cur, setCur] = useState<string>(() => (tabs.some((t) => t.id === lastTab) ? lastTab : tabs[0].id));
  const active = tabs.find((t) => t.id === cur) ?? tabs[0];
  const pick = (id: string) => { lastTab = id; setCur(id); };
  const step = (d: number) => {
    const i = tabs.findIndex((t) => t.id === active.id);
    pick(tabs[(i + d + tabs.length) % tabs.length].id);
  };
  const onButtonDown = (e: GamepadEvent) => {
    if (e.detail.button === GamepadButton.BUMPER_LEFT) step(-1);
    else if (e.detail.button === GamepadButton.BUMPER_RIGHT) step(1);
  };
  return (
    <Focusable onButtonDown={onButtonDown}
      actionDescriptionMap={{ [GamepadButton.BUMPER_LEFT]: "Previous tab", [GamepadButton.BUMPER_RIGHT]: "Next tab" }}>
      <Focusable flow-children="horizontal" style={{ display: "flex", gap: 4, padding: "0 16px 8px",
        borderBottom: "1px solid rgba(255,255,255,0.12)", marginBottom: 4 }}>
        {tabs.map((t) => (
          <Focusable key={t.id} onActivate={() => pick(t.id)} onClick={() => pick(t.id)} noFocusRing={false}
            style={{ flex: 1, textAlign: "center", padding: "6px 2px", fontSize: 13, fontWeight: 700,
              letterSpacing: "0.04em", textTransform: "uppercase", borderRadius: 4,
              color: t.id === active.id ? "#1a9fff" : "#b8bcbf",
              borderBottom: t.id === active.id ? "2px solid #1a9fff" : "2px solid transparent" }}>
            {t.title}
          </Focusable>
        ))}
      </Focusable>
      <div key={active.id}>{active.content}</div>
    </Focusable>
  );
};

/**
 * The Quick Access panel holds the four panels (Power, Game, Fixes, Setup) in their short form, one tab each: short
 * labels, the read-back line after every change, nothing that needs a settings page. The full-screen page below has
 * the same four with the longer explanations and the health check's details.
 */
export const QuickPanel: FC = () => {
  const [help, setHelp] = useState(false);
  const [setupDone, setSetupDone] = useState<boolean | null>(null);
  useEffect(() => { getSetting("quick_help").then((v) => setHelp(!!v)); uiLog("ui: quick access panel mounted").catch(() => undefined); }, []);
  // the steps show on the first start and stay until Done or Skip; without the XMX driver they come back (nothing on
  // the XMX cores works without it). A driver left over from an earlier install no longer skips the VRR step.
  useEffect(() => { Promise.all([getSetting("setup_done"), xessState()]).then(([d, x]) => setSetupDone(!!d && !!x?.installed)).catch(() => setSetupDone(true)); }, []);
  const tabs: QuickTab[] = [
    { id: "power", title: "Power", content: <PowerTab quick /> },
    ...(setupDone ? [{ id: "game", title: "Game", content: <GameSection quick /> }] : []),
    { id: "fixes", title: "Fixes", content: <FixesTab quick /> },
    { id: "setup", title: "Setup", content: <><SetupTab quick />
      <PanelSection>
        <PanelSectionRow>
          <ToggleField label="Explain every control" checked={help} bottomSeparator="none"
            description={help ? "What each control does, why you'd use it and what it costs." : "Adds a short explanation under each control."}
            onChange={(v) => { setHelp(v); setSetting("quick_help", v); }} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => openPage("power")}>Full view: health check and rarely used settings</ButtonItem>
        </PanelSectionRow>
      </PanelSection></> },
  ];
  return (
  <HelpContext.Provider value={help}>
    <LastMessage />
    {setupDone === false && <FirstRun onDone={() => setSetupDone(true)} />}
    <QuickTabs tabs={tabs} />
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
