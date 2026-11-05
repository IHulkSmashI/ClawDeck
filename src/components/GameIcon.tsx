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

// FSR red (AMD), XeSS blue (Intel), frame generation purple
const TAG = { fsr: "#e5484d", xess: "#1a9fff", fg: "#a855f7" };

/** A small coloured tag after a game's name: what ClawDeck put into it ("FSR 4.1.2", "XeSS 2", "3x FG"). */
export const Tag: FC<{ text: string; kind: keyof typeof TAG }> = ({ text, kind }) => (
  <span style={{ flex: "none", fontSize: 10, fontWeight: 700, lineHeight: "14px", padding: "1px 6px", borderRadius: 7,
    color: "#fff", background: TAG[kind] ?? TAG.fg, whiteSpace: "nowrap" }}>{text}</span>
);

export const GameName: FC<{ appid: string; name: string; size?: number; tags?: { text: string; kind: keyof typeof TAG }[] }> = ({ appid, name, size = 20, tags }) => {
  const url = gameIconUrl(appid);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      {url ? <img src={url} width={size} height={size} style={{ borderRadius: 4, flex: "none" }} alt="" />
        : <span style={{ width: size, height: size, flex: "none" }} />}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
      {tags?.map((t) => <Tag key={t.text} text={t.text} kind={t.kind} />)}
    </span>
  );
};
