// Steam client globals the plugin reads, behind try/catch so a Steam update that renames one degrades a feature
// instead of breaking the panel. Field names verified against SimpleDeckyTDP / PowerControl (2026); see DESIGN.md.
declare const Router: any;
declare const appStore: any;
declare const SteamClient: any;

export type RunningApp = { appid: string; name: string; dir: string | null };

export const runningApp = (): RunningApp | null => {
  try {
    const a = Router?.MainRunningApp;
    if (!a) return null;
    let dir: string | null = null;
    try { dir = appStore?.GetAppOverviewByAppID?.(a.appid)?.m_strInstallDir ?? null; } catch { /* not available */ }
    return { appid: String(a.appid), name: a.display_name, dir };
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
