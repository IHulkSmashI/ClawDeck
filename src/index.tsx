import { staticClasses } from "@decky/ui";
import { definePlugin, routerHook, toaster } from "@decky/api";
import { GiClawHammer } from "react-icons/gi";

import { gameStarted, gameStopped, getChanges, powerSourceChanged, resumed } from "./api";
import { FullPage, QuickPanel, ROUTE } from "./pages";

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

  // Resume from sleep: firmware may reset limits; the backend re-applies the charge limit and the running game.
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
    toaster.toast({ title: "ClawDeck: system updated", body: c.failing?.length ? `${what}. Needs a look: ${c.failing.map((f) => f.title).join(", ")} (All settings → Setup)` : `${what}. All checks still pass.` });
  }).catch(() => { /* backend logs it */ });

  return {
    name: "ClawDeck",
    titleView: <div className={staticClasses.Title}>ClawDeck</div>,
    content: <QuickPanel />,
    icon: <GiClawHammer />,
    onDismount() {
      unregisters.forEach((u) => { try { u(); } catch { /* already gone */ } });
      routerHook.removeRoute(ROUTE);
    },
  };
});
