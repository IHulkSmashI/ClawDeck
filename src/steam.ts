// Steam client calls the plugin makes, behind try/catch so a Steam update that renames one degrades a feature
// instead of breaking the panel. Names checked against @decky/ui's Steam typings (Router = CSteamUIStore,
// SteamClient.Apps.SpecifyCompatTool / GetAvailableCompatTools) and the PowerControl / SimpleDeckyTDP sources.
// The running game's install folder is not exposed to the frontend; the backend reads Steam's appmanifest.
import { Router } from "@decky/ui";

declare const SteamClient: any;

export type RunningApp = { appid: string; name: string };

export const runningApp = (): RunningApp | null => {
  try {
    const a = Router?.MainRunningApp;
    if (!a) return null;
    return { appid: String(a.appid), name: a.display_name };
  } catch { return null; }
};

// ---- launch options (Steam's own setting; ClawDeck changes it only when the user taps the button) ---------------
const WRAPPER_NAME = "clawdeck-launch.sh";

/** The game's launch options as Steam stores them. RegisterForAppDetails answers once right away with the details. */
export const getLaunchOptions = (appid: string): Promise<string | null> => new Promise((resolve) => {
  let done = false;
  try {
    const reg = SteamClient.Apps.RegisterForAppDetails(Number(appid), (d: any) => {
      if (done) return;
      done = true;
      try { reg?.unregister?.(); } catch { /* ignore */ }
      resolve(typeof d?.strLaunchOptions === "string" ? d.strLaunchOptions : null);
    });
    setTimeout(() => { if (!done) { done = true; try { reg?.unregister?.(); } catch { /* ignore */ } resolve(null); } }, 1500);
  } catch { resolve(null); }
});

export const hasWrapper = (opts: string | null) => !!opts && opts.includes(WRAPPER_NAME);

/** Put the wrapper in front of %command%, keeping whatever the user already has (env vars before, args after). */
export const withWrapper = (opts: string, wrapper: string): string => {
  const o = opts.trim();
  if (hasWrapper(o)) return o;
  if (o.includes("%command%")) return o.replace("%command%", `${wrapper} %command%`);
  return o ? `${wrapper} %command% ${o}` : `${wrapper} %command%`;
};

export const withoutWrapper = (opts: string): string => {
  const o = opts.replace(/\S*clawdeck-launch\.sh\s+/g, "").trim();
  return o === "%command%" ? "" : o;
};

export const setLaunchOptions = (appid: string, opts: string): boolean => {
  try { SteamClient.Apps.SetAppLaunchOptions(Number(appid), opts); return true; } catch { return false; }
};
