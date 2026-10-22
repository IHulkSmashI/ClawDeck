import { DialogButton, Focusable, staticClasses } from "@decky/ui";
import { definePlugin, routerHook, toaster } from "@decky/api";
import { MdSettings } from "react-icons/md";

import { gameStarted, gameStopped, getChanges, powerSourceChanged, resumed } from "./api";
import { LogoIcon } from "./components/LogoIcon";
import { FullPage, openPage, QuickPanel, ROUTE } from "./pages";

declare const SteamClient: any;

export default definePlugin(() => {
  routerHook.addRoute(ROUTE, FullPage, { exact: false });
  // Game lifecycle: apply the per-game profile at start, revert and save what autotune learned at stop.
  const unregisters: (() => void)[] = [];
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
    if (hook?.unregister) unregisters.push(() => hook.unregister());
  } catch (err) {
    console.warn("ClawDeck: app lifetime hook unavailable", err);
  }

  // Charger plugged / unplugged (eACState 2 = AC, 1 = battery; same API as PowerControl and SimpleDeckyTDP): the
  // running game's profile follows the power source. Steam fires this often, so only real changes reach the backend.
  try {
    let ac: number | undefined;
    const h = SteamClient?.System?.RegisterForBatteryStateChanges?.((e: { eACState: number }) => {
      if (e.eACState === ac) return;
      const first = ac === undefined;
      ac = e.eACState;
      if (!first) powerSourceChanged(ac === 2).catch(() => { /* backend logs it */ });
    });
    if (h?.unregister) unregisters.push(() => h.unregister());
  } catch (err) {
    console.warn("ClawDeck: battery state hook unavailable", err);
  }

  // Resume from sleep: firmware may reset limits; the backend re-applies the running game.
  try {
    const h = SteamClient?.System?.RegisterForOnResumeFromSuspend?.(() => { resumed().catch(() => { /* logged */ }); });
    if (h?.unregister) unregisters.push(() => h.unregister());
  } catch (err) {
    console.warn("ClawDeck: resume hook unavailable", err);
  }

  // After a system update: say what changed, and whether everything still works (health checks run on the backend).
  getChanges().then((c) => {
    if (!c?.changed?.length) return;
    const what = c.changed.map((x) => `${x.what} ${x.to ?? "removed"}`).join(", ");
    toaster.toast({ title: "ClawDeck: system updated", body: c.failing?.length ? `${what}. Needs a look: ${c.failing.map((f) => f.title).join(", ")} (Fixes)` : `${what}. All checks still pass.` });
  }).catch(() => { /* backend logs it */ });

  return {
    name: "ClawDeck",
    // the same title bar Decky plugins use, with a settings button that opens the full page (as "More details" does)
    titleView: (
      <Focusable style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
        <div className={staticClasses.Title}>ClawDeck</div>
        <DialogButton aria-label="All ClawDeck settings" onClick={() => openPage("power")}
          style={{ width: "auto", minWidth: 0, height: 28, padding: "0 10px", display: "flex", alignItems: "center" }}>
          <MdSettings />
        </DialogButton>
      </Focusable>
    ),
    content: <QuickPanel />,
    icon: <LogoIcon />,
    onDismount() {
      unregisters.forEach((u) => { try { u(); } catch { /* already gone */ } });
      routerHook.removeRoute(ROUTE);
    },
  };
});
