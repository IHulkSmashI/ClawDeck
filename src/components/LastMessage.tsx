import { PanelSection, PanelSectionRow } from "@decky/ui";
import { FC, useEffect, useState } from "react";

import { Note } from "./Hint";
import { lastMessage, onMessage } from "./notify";

/** The whole text of the last toast (Steam's toast cuts it to one line), for two minutes, at the top of Quick Access. */
export const LastMessage: FC = () => {
  const [, tick] = useState(0);
  useEffect(() => onMessage(() => tick((n) => n + 1)), []);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 15000); return () => clearInterval(t); }, []);
  const m = lastMessage();
  if (!m || Date.now() - m.at > 120000) return null;
  return (
    <PanelSection>
      <PanelSectionRow><Note label="Last message">{m.text}</Note></PanelSectionRow>
    </PanelSection>
  );
};
