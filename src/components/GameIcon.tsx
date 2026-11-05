import { FC } from "react";

/** The game's own small icon, as Steam's library shows it (appStore.GetIconURLForApp), or nothing when Steam doesn't
 * have one. Used next to game names in ClawDeck's lists (maintainer, 2026-11-05). */
export const gameIconUrl = (appid: string): string | null => {
  try {
    const store = (window as any).appStore;
    const ov = store?.GetAppOverviewByAppID?.(Number(appid));
    const url = ov ? store?.GetIconURLForApp?.(ov) : null;
    return typeof url === "string" && url ? url : null;
  } catch {
    return null;
  }
};

export const GameName: FC<{ appid: string; name: string; size?: number }> = ({ appid, name, size = 20 }) => {
  const url = gameIconUrl(appid);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      {url ? <img src={url} width={size} height={size} style={{ borderRadius: 4, flex: "none" }} alt="" />
        : <span style={{ width: size, height: size, flex: "none" }} />}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
    </span>
  );
};
