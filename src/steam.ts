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

/** Steam's own per-game compatibility tool setter (Steam stays the owner of config.vdf). */
export const specifyCompatTool = (appid: string, toolName: string): boolean => {
  try { SteamClient.Apps.SpecifyCompatTool(Number(appid), toolName); return true; } catch { return false; }
};

export const availableCompatTools = async (appid: string): Promise<{ name: string; display: string }[] | null> => {
  try {
    const list = await SteamClient.Apps.GetAvailableCompatTools(Number(appid));
    return (list ?? []).map((t: any) => ({ name: t.strToolName, display: t.strDisplayName }));
  } catch { return null; }
};
