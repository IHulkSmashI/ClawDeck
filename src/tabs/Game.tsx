import { DropdownItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { FC, useEffect, useMemo, useState } from "react";

import { gamesList, GameEntry } from "../api";
import { Note } from "../components/Hint";
import { runningApp } from "../steam";
import { PerGameTab } from "./PerGame";

let lastPick: string | null = null;      // the game picked last, while the plugin stays loaded

/** One game at a time, picked from a list like Decky Framegen's: the running game first, then every installed game.
 * Under it the game's recommended setup, upscaler and frame generation (PerGameTab). Same in Quick Access and the
 * full view; the full view adds the explanations and the less used switches. */
export const GameSection: FC<{ quick?: boolean }> = ({ quick }) => {
  const running = runningApp();
  const [games, setGames] = useState<GameEntry[] | null>(null);
  const [sel, setSel] = useState<string | null>(running?.appid ?? lastPick);
  useEffect(() => { gamesList().then(setGames).catch(() => setGames([])); }, []);
  useEffect(() => { if (running?.appid && !lastPick) setSel(running.appid); }, [running?.appid]);

  const options = useMemo(() => {
    const list = (games ?? []).map((g) => ({ data: g.appid, label: g.appid === running?.appid ? `${g.name} (running)` : g.name }));
    if (running && !list.some((o) => o.data === running.appid)) list.unshift({ data: running.appid, label: `${running.name} (running)` });
    return list;
  }, [games, running?.appid]);
  const picked = options.find((o) => o.data === sel);
  const name = (games ?? []).find((g) => g.appid === sel)?.name ?? (running?.appid === sel ? running?.name : null) ?? "";

  return (
    <>
      <PanelSection title="Game">
        <PanelSectionRow>
          <DropdownItem layout="below" label={quick ? undefined : "Game"} menuLabel="Pick a game" strDefaultLabel={games === null ? "Reading your games…" : "Pick a game"}
            disabled={!options.length} rgOptions={options} selectedOption={picked ? sel : null}
            onChange={(o) => { lastPick = String(o.data); setSel(String(o.data)); }} />
        </PanelSectionRow>
        {!sel && games !== null && (
          <PanelSectionRow><Note>{options.length ? "Pick a game to set up XeSS, FSR 4 and frame generation on the XMX cores." : "No installed Steam games found."}</Note></PanelSectionRow>
        )}
      </PanelSection>
      {sel && name && <PerGameTab key={sel} quick={quick} embedded pick={{ appid: sel, name }} />}
    </>
  );
};
