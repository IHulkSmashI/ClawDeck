import { PanelSectionRow, ToggleField } from "@decky/ui";
import { FC, ReactNode, useState } from "react";

/** An expander for controls that stay off the main view (HARDWARE-FINDINGS §19). Remembered per id on this device. */
export const Advanced: FC<{ id: string; label?: string; description?: ReactNode; children: ReactNode }> = ({ id, label = "Advanced", description, children }) => {
  const key = `clawdeck.advanced.${id}`;
  const [on, setOn] = useState<boolean>(() => { try { return localStorage.getItem(key) === "1"; } catch { return false; } });
  return (
    <>
      <PanelSectionRow>
        <ToggleField label={label} checked={on} description={description}
          onChange={(v) => { setOn(v); try { localStorage.setItem(key, v ? "1" : "0"); } catch { /* private storage: not remembered */ } }} />
      </PanelSectionRow>
      {on && children}
    </>
  );
};
