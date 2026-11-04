import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { FC, useEffect, useState } from "react";

import { gamesList, GameEntry } from "../api";
import { Note, Status } from "../components/Hint";
import { runningApp } from "../steam";
import { PerGameTab } from "./PerGame";

/** Every installed game, so one can be set up before it is ever started (what OptiClick's game list does on
 * Windows): pick one, and the Per-game panel below shows its recommended setup with one tap to apply or undo. */
export const GamesTab: FC = () => {
  const [games, setGames] = useState<GameEntry[] | null>(null);
  const [pick, setPick] = useState<GameEntry | null>(null);
  useEffect(() => { gamesList().then(setGames).catch(() => setGames([])); }, []);
  const running = runningApp();

  if (pick) {
    return (
      <>
        <PanelSection>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => { setPick(null); gamesList().then(setGames).catch(() => undefined); }}>← All games</ButtonItem>
          </PanelSectionRow>
        </PanelSection>
        <PerGameTab pick={{ appid: pick.appid, name: pick.name }} />
      </>
    );
  }
  return (
    <PanelSection title="Games">
      <PanelSectionRow>
        <Note>Pick a game to see what suits it on the Claw and set it up in one tap, before you start it. A game that is
          running is set up from Quick Access too; changes reach it at the next launch.</Note>
      </PanelSectionRow>
      {games === null && <PanelSectionRow><Status ok={null} label="Reading your Steam library…" /></PanelSectionRow>}
      {games?.length === 0 && <PanelSectionRow><Status ok={null} label="No installed Steam games found" /></PanelSectionRow>}
      {games?.map((g) => (
        <PanelSectionRow key={g.appid}>
          <ButtonItem layout="below" onClick={() => setPick(g)}
            description={g.setup === "optiscaler" ? "ClawDeck's OptiScaler is set up" : g.setup === "launch" ? "ClawDeck settings saved" : undefined}>
            {g.name}{running?.appid === g.appid ? " (running)" : ""}
          </ButtonItem>
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
};
