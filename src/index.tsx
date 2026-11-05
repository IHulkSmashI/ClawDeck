import { Router, staticClasses } from "@decky/ui";
import { definePlugin, routerHook, toaster } from "@decky/api";

import { uiLog, displayExternal, gameStarted, gameStopped, getChanges, getFixes, getSetting, powerSourceChanged, resumed } from "./api";
import { startVrrScope, VrrScope } from "./vrr";
import { LogoIcon } from "./components/LogoIcon";
import { FullPage, QuickPanel, ROUTE } from "./pages";

declare const SteamClient: any;

export default definePlugin(() => {
  routerHook.addRoute(ROUTE, FullPage, { exact: false });
  // Game lifecycle: apply the per-game profile at start, revert and save what autotune learned at stop.
  const unregisters: (() => void)[] = [];
  const vrrScopeRef: { current: VrrScope | null } = { current: null };
  try {
    const hook = SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.((e: { unAppID: number; bRunning: boolean }) => {
      const appid = String(e.unAppID);
      setTimeout(() => vrrScopeRef.current?.poke(), 500);   // menus ↔ game: VRR follows without waiting for Steam
      if (e.bRunning) {
        gameStarted(appid).then((r: any) => {
          if (r?.failed?.length) toaster.toast({ title: "ClawDeck", body: `Profile applied, but ${r.failed.join(", ")} failed` });
        }).catch(() => { /* backend logs it */ });
      } else {
        gameStopped(appid).then((r: any) => {
          if (r?.learned?.summary) toaster.toast({ title: "ClawDeck autotune", body: r.learned.summary });
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

  // VRR only while a game runs (Fixes): with the 30–120 Hz fix the menus otherwise follow a game's low frame rate.
  // On by default once the VRR fix is installed; Fixes turns it off (Steam's own VRR switch is then in charge).
  let vrrScope: VrrScope | null = null;
  let disposed = false;
  let external = false;                         // a dock / monitor: stand aside (its VRR is the user's choice)
  const ext = () => displayExternal().then((r) => {
    const was = external; external = !!r?.connected;
    if (was !== external) vrrScope?.poke();     // plugged in: the user's VRR back at once; unplugged: ClawDeck's again
  }).catch(() => { /* keep the last answer */ });
  let extTimer: ReturnType<typeof setInterval> | null = null;
  let syncing: Promise<void> = Promise.resolve();
  const vrrScopeSync = () => (syncing = syncing.then(async () => {   // one at a time: never two scopes
    try {
      const [pref, f] = await Promise.all([getSetting("vrr_menus_fixed"), getFixes()]);
      const want = !disposed && (pref === null || pref === undefined ? !!f?.vrr?.wide : !!pref);
      if (want && !vrrScope) {
        await ext();
        vrrScope = startVrrScope((m) => { console.log("ClawDeck:", m); uiLog(`vrr: ${m}`).catch(() => undefined); },
          () => external, () => { try { return !!Router?.MainRunningApp; } catch { return null; } });
        vrrScopeRef.current = vrrScope;
        if (!extTimer) extTimer = setInterval(ext, 5000);   // polled only while the switch is on
      }
      if (!want && vrrScope) { vrrScope.stop(); vrrScope = null; vrrScopeRef.current = null; }
      if (!want && extTimer) { clearInterval(extTimer); extTimer = null; }
    } catch (err) { console.warn("ClawDeck: VRR scope", err); }
  }));
  (window as any).__clawdeckVrrScopeSync = vrrScopeSync;    // the Fixes switch calls it after a change
  (window as any).__clawdeckVrrScopeStatus = () => vrrScope?.status() ?? { active: false };
  vrrScopeSync();
  unregisters.push(() => { disposed = true; if (extTimer) clearInterval(extTimer); vrrScope?.stop(); vrrScope = null; });

  // After a system update: say what changed, and whether everything still works (health checks run on the backend).
  getChanges().then((c) => {
    if (!c?.changed?.length) return;
    const what = c.changed.map((x) => `${x.what} ${x.to ?? "removed"}`).join(", ");
    toaster.toast({ title: "ClawDeck: system updated", body: c.failing?.length ? `${what}. Needs a look: ${c.failing.map((f) => f.title).join(", ")} (Fixes)` : `${what}. All checks still pass.` });
  }).catch(() => { /* backend logs it */ });

  return {
    name: "ClawDeck",
    // the plain title bar: everything is in Quick Access's sections now; the full view is under Setup (maintainer,
    // 2026-11-04: "is the cog still needed?")
    titleView: <div className={staticClasses.Title}>ClawDeck</div>,
    content: <QuickPanel />,
    icon: <LogoIcon />,
    onDismount() {
      unregisters.forEach((u) => { try { u(); } catch { /* already gone */ } });
      routerHook.removeRoute(ROUTE);
    },
  };
});
