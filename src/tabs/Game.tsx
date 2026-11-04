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
  useEffect(() => { if (games && !sel) { const first = games.find((g) => g.supported !== false); if (first && !running) setSel(first.appid); } }, [games]);

  const options = useMemo(() => {
    // only games ClawDeck can set up, as OptiClick lists only supported games: DLSS, FSR or XeSS inside, or in the list
    return (games ?? []).filter((g) => g.supported !== false)
      .map((g) => ({ data: g.appid, label: g.appid === running?.appid ? `${g.name} (running)` : g.name }));
  }, [games, running?.appid]);
  const picked = options.find((o) => o.data === sel);
  const hidden = (games ?? []).filter((g) => g.supported === false);
  const runningUnsupported = !!running && hidden.some((g) => g.appid === running.appid);
  const name = (games ?? []).find((g) => g.appid === sel)?.name ?? (running?.appid === sel ? running?.name : null) ?? "";

  return (
    <>
      <PanelSection title="Game">
        <PanelSectionRow>
          <DropdownItem layout="below" label={quick ? undefined : "Game"} menuLabel="Pick a game" strDefaultLabel={games === null ? "Reading your games…" : "Pick a game"}
            disabled={!options.length} rgOptions={options} selectedOption={picked ? sel : null}
            onChange={(o) => { lastPick = String(o.data); setSel(String(o.data)); }} />
        </PanelSectionRow>
        {runningUnsupported && (
          <PanelSectionRow><Note>{running!.name} has no DLSS, FSR or XeSS for OptiScaler to use, so there's nothing to set up for it (Power above still applies).</Note></PanelSectionRow>
        )}
        {!quick && hidden.length > 0 && (
          <PanelSectionRow><Note>{hidden.length} installed game{hidden.length === 1 ? "" : "s"} without DLSS, FSR or XeSS not listed: {hidden.map((g) => g.name).join(", ")}.</Note></PanelSectionRow>
        )}
        {!sel && games !== null && (
          <PanelSectionRow><Note>{options.length ? "Pick a game to set up XeSS, FSR 4 and frame generation on the XMX cores." : "No installed Steam games found."}</Note></PanelSectionRow>
        )}
      </PanelSection>
      {sel && name && picked && <PerGameTab key={sel} quick={quick} embedded pick={{ appid: sel, name }} />}
    </>
  );
};
