import { staticClasses, Tabs } from "@decky/ui";
import { definePlugin, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { GiClawHammer } from "react-icons/gi";

import { gameStarted, gameStopped } from "./api";
import { SetupTab } from "./tabs/Setup";
import { PowerTab } from "./tabs/Power";
import { GraphicsTab } from "./tabs/Graphics";
import { BatteryTab } from "./tabs/Battery";
import { DisplayTab } from "./tabs/Display";
import { ControllerTab } from "./tabs/Controller";

declare const SteamClient: any;

function Content() {
  const [tab, setTab] = useState<string>(() => {
    try { return localStorage.getItem("clawdeck.tab") || "setup"; } catch { return "setup"; }
  });
  useEffect(() => { try { localStorage.setItem("clawdeck.tab", tab); } catch { /* ignore */ } }, [tab]);
  return (
    <div style={{ height: "95%", width: "calc(100vw - 50px)", maxWidth: "100%", marginTop: -12, position: "absolute" }}>
      <Tabs
        activeTab={tab}
        onShowTab={(id: string) => setTab(id)}
        autoFocusContents={true}
        tabs={[
          { id: "setup", title: "Setup", content: <SetupTab /> },
          { id: "power", title: "Power", content: <PowerTab /> },
          { id: "graphics", title: "Graphics", content: <GraphicsTab /> },
          { id: "display", title: "Display", content: <DisplayTab /> },
          { id: "controller", title: "Controller", content: <ControllerTab /> },
          { id: "battery", title: "Battery", content: <BatteryTab /> },
        ]}
      />
    </div>
  );
}

export default definePlugin(() => {
  // Game lifecycle: apply the per-game profile at start, revert and save what autotune learned at stop.
  let unregister: (() => void) | null = null;
  try {
    const hook = SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.((e: { unAppID: number; bRunning: boolean }) => {
      const appid = String(e.unAppID);
      if (e.bRunning) {
        gameStarted(appid).then((r: any) => {
          if (r?.failed?.length) toaster.toast({ title: "ClawDeck", body: `Profile applied, but ${r.failed.join(", ")} failed` });
        }).catch(() => { /* backend logs it */ });
      } else {
        gameStopped(appid).then((r: any) => {
          if (r?.learned?.learned) toaster.toast({ title: "ClawDeck autotune", body: `Saved: ${r.learned.learned.tdp} W, ${r.learned.learned.bound}-bound` });
        }).catch(() => { /* backend logs it */ });
      }
    });
    unregister = hook?.unregister ? () => hook.unregister() : null;
  } catch (err) {
    console.warn("ClawDeck: app lifetime hook unavailable", err);
  }

  return {
    name: "ClawDeck",
    titleView: <div className={staticClasses.Title}>ClawDeck</div>,
    content: <Content />,
    icon: <GiClawHammer />,
    onDismount() {
      if (unregister) unregister();
    },
  };
});
