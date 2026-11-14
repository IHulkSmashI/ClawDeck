import { DropdownItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { FC, useEffect, useMemo, useState } from "react";

import { gamesList, GameEntry } from "../api";
import { GameName } from "../components/GameIcon";
import { Anchor, markPick, Note } from "../components/Hint";
import { runningApp } from "../steam";
import { PerGameTab } from "./PerGame";

// the game picked last, outside the component: closing Steam's dropdown re-mounts Quick Access, and the new panel took
// the running ES-DE over the pick. It holds until a game starts or stops (index.tsx), so it doesn't read as the next
// game's settings
let lastPick: string | null = null;
export const forgetGamePick = () => { lastPick = null; };

/** One game at a time, picked from a list: the running game first, then every installed game. Nothing is picked
 * while no game that can be set up runs, until the user picks one.
 * Under it the game's recommended setup, upscaler and frame generation (PerGameTab). Same in Quick Access and the
 * full view; the full view adds the explanations and the less used switches. */
export const GameSection: FC<{ quick?: boolean }> = ({ quick }) => {
  const running = runningApp();
  const [games, setGames] = useState<GameEntry[] | null>(null);
  const [pick, setPick] = useState<string | null>(lastPick);
  useEffect(() => { gamesList().then(setGames).catch(() => setGames([])); }, []);

  const options = useMemo(() => {
    // only games ClawDeck can set up: DLSS, FSR or XeSS inside, or in the list
    return (games ?? []).filter((g) => g.supported !== false)
      .map((g) => ({ data: g.appid, label: <GameName appid={g.appid} name={g.appid === running?.appid ? `${g.name} (running)` : g.name} tags={g.tags} iconUrl={g.icon_url} /> }));
  }, [games, running?.appid]);
  // the user's pick, else the running game when it can be set up, else none: a game nobody picked and that isn't
  // running (the first in the list) read as the running game's settings, in an emulator from ES-DE too
  const cur = options.find((o) => o.data === pick)?.data ?? options.find((o) => o.data === running?.appid)?.data ?? null;
  const picked = options.find((o) => o.data === cur);
  const hidden = (games ?? []).filter((g) => g.supported === false);
  const runningUnsupported = !!running && hidden.some((g) => g.appid === running.appid);
  const name = (games ?? []).find((g) => g.appid === cur)?.name ?? "";

  return (
    <>
      <PanelSection title={quick ? undefined : "Game"}>
        <PanelSectionRow>
          <Anchor id="game"><DropdownItem onMenuOpened={() => markPick("game")} layout="below" label={quick ? undefined : "Game"} menuLabel="Pick a game" strDefaultLabel={games === null ? "Reading your games…" : "Pick a game"}
            disabled={!options.length} rgOptions={options} selectedOption={cur}
            onChange={(o) => { markPick("game"); lastPick = String(o.data); setPick(lastPick); }} /></Anchor>
        </PanelSectionRow>
        {runningUnsupported && (
          <PanelSectionRow><Note>{running!.name} has no DLSS, FSR or XeSS for OptiScaler to use, so there's nothing to set up for it (Power above still applies).</Note></PanelSectionRow>
        )}
        {!quick && hidden.length > 0 && (
          <PanelSectionRow><Note>{hidden.length} installed game{hidden.length === 1 ? "" : "s"} without DLSS, FSR or XeSS not listed: {hidden.map((g) => g.name).join(", ")}.</Note></PanelSectionRow>
        )}
        {!cur && games !== null && (
          <PanelSectionRow><Note>{options.length ? "Pick a game to set up XeSS, FSR 4 and frame generation on the XMX cores." : "No installed Steam games found."}</Note></PanelSectionRow>
        )}
      </PanelSection>
      {cur && name && picked && <PerGameTab key={cur} quick={quick} embedded pick={{ appid: cur, name }} />}
    </>
  );
};
