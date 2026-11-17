import { ButtonItem, Navigation, PanelSection, PanelSectionRow, SidebarNavigation, Tabs, ToggleField, findClassModule } from "@decky/ui";
import { FC, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

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

// Steam's tabbed-page CSS module, the one Tabs renders with (class names hashed per build, so looked up at run time)
const tabClasses = findClassModule((m: any) => m.TabContentsScroll && m.TabRowTabs) as Record<string, string> | undefined;
const ROOT_CLASS = "clawdeck-qam-tabs";
// Tabs is laid out for a full page; in the narrow Quick Access panel its side padding and spacers clip the tabs.
// Four equal tabs, little padding, content under the 46 px header. Steam fades the row's first and last 24 px (a
// mask for scrolling tabs) and sets text-align: start: in a row this tight that cut into Power and Setup and pushed
// every title to the left of its pill, so the mask is off and the titles are centred; without the fade the end pills
// met the L1 / R1 glyphs, so the row keeps a small gap beside them (Claw 8 EX, 2026-11-17).
// Empty if Steam renames the module: the panel then keeps Steam's own (wider) spacing instead of breaking.
const TABS_CSS = ((c) => {
  if (!c) return "";
  const s = `.${ROOT_CLASS}`;
  return `
    ${s} .${c.TabHeaderRowWrapper} { padding: 4px 4px 0 !important; }
    ${s} .${c.TabRowSpacer} { display: none !important; }
    ${s} .${c.TabRow}, ${s} .${c.TabRowTabs}, ${s} .${c.TabsRowScroll} { width: 100%; }
    ${s} .${c.FixCenterAlignScroll} { display: flex; gap: 4px; width: 100%; padding: 0 !important; }
    ${s} .${c.TabRow} { gap: 6px; }
    ${s} .${c.TabsRowScroll} { mask-image: none !important; -webkit-mask-image: none !important; }
    ${s} .${c.Tab} { flex: 1; min-width: 0; padding: 10px 4px !important; justify-content: center; text-align: center; font-size: 0.85em;
      white-space: nowrap; overflow: hidden; }
    ${s} .${c.TabTitle} { flex: 1; min-width: 0; overflow: hidden; text-align: center; white-space: nowrap; text-overflow: ellipsis; }
    ${s} .${c.TabContentsScroll} { padding: 54px 0 40px !important; }
  `;
})(tabClasses);

/** Four tabs always on screen at the top, switched with LB / RB, one tab's settings below; replaces the four folding
 * sections (#171, maintainer 2026-11-14: "tabs that are always on screen to pick between with rb and lb").
 * Steam's own Tabs (the Library's), so the bumpers, focus and look are Steam's. Done as unifideck does it in its
 * Quick Access panel (GPL-3.0, verified there on a Deck): Tabs fills an absolutely positioned pane that needs a
 * definite height, and Decky's per-plugin wrapper sizes to its content, so the height comes from Decky's Quick
 * Access content slot, measured at run time. */
export const QuickTabs: FC<{ tabs: QuickTab[] }> = ({ tabs }) => {
  // the remembered tab is kept even while it isn't in the list yet (Game waits for the setup check after a re-mount):
  // Power shows meanwhile, and the tab comes back as soon as it is listed, instead of the panel settling on Power
  const [cur, setCur] = useState<string>(lastTab);
  const active = tabs.some((t) => t.id === cur) ? cur : tabs[0].id;
  const rootRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number>();
  useLayoutEffect(() => {
    const root = rootRef.current;
    const slot = root?.closest<HTMLElement>('[id^="quickaccess_content_"]');
    if (!root || !slot) return;
    const measure = () => setHeight(Math.max(0, slot.clientHeight - (root.getBoundingClientRect().top - slot.getBoundingClientRect().top + slot.scrollTop)));
    measure();
    const o = new ResizeObserver(measure);
    o.observe(slot);
    return () => o.disconnect();
  }, []);
  return (
    <div ref={rootRef} className={ROOT_CLASS} style={{ position: "relative", height: height ?? 600 }}>
      {TABS_CSS && <style>{TABS_CSS}</style>}
      <Tabs activeTab={active} onShowTab={(id: string) => { lastTab = id; setCur(id); }}
        tabs={tabs.map((t) => ({ id: t.id, title: t.title, content: t.content }))} />
    </div>
  );
};

// the last setup check, kept while the plugin is loaded: a Quick Access re-mount (a dropdown's menu closing) lists the
// Game tab from the first render instead of after the check, so a pick in the Game tab stays in the Game tab
let lastSetupDone: boolean | null = null;

/**
 * The Quick Access panel holds the four panels (Power, Game, Fixes, Setup) in their short form, one tab each: short
 * labels, the read-back line after every change, nothing that needs a settings page. The full-screen page below has
 * the same four with the longer explanations and the health check's details.
 */
export const QuickPanel: FC = () => {
  const [help, setHelp] = useState(false);
  const [setupDone, setSetupDoneState] = useState<boolean | null>(lastSetupDone);
  const setSetupDone = (v: boolean) => { lastSetupDone = v; setSetupDoneState(v); };
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
