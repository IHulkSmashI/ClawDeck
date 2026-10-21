import { PanelSectionRow, ToggleField } from "@decky/ui";
import { FC, ReactNode, useState } from "react";

/** One "Advanced" switch per tab (UI rule): the basics stay visible, everything else hides behind it. Remembered per tab. */
export const Advanced: FC<{ id: string; label?: string; children: ReactNode }> = ({ id, label = "Advanced", children }) => {
  const key = `clawdeck.advanced.${id}`;
  const [on, setOn] = useState<boolean>(() => { try { return localStorage.getItem(key) === "1"; } catch { return false; } });
  return (
    <>
      <PanelSectionRow>
        <ToggleField label={label} checked={on} onChange={(v) => { setOn(v); try { localStorage.setItem(key, v ? "1" : "0"); } catch { /* ignore */ } }} />
      </PanelSectionRow>
      {on && children}
    </>
  );
};
