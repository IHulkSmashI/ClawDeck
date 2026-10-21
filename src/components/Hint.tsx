import { FC } from "react";

/**
 * The point-form explanation under every toggle and slider (project UI rule):
 * what it does / why you'd turn it on / what it costs. Two or three short bullets, no paragraphs.
 */
export const Hint: FC<{ does: string; why?: string; cost?: string }> = ({ does, why, cost }) => (
  <ul style={{ margin: "2px 0 6px 0", paddingLeft: 16, fontSize: 11, opacity: 0.75, lineHeight: 1.35 }}>
    <li>{does}</li>
    {why && <li>Why: {why}</li>}
    {cost && <li>Cost: {cost}</li>}
  </ul>
);

// Detail goes on its own line: the Quick Access menu is about 300 px wide, too narrow for label and detail side by side.
export const Status: FC<{ ok: boolean | null; label: string; detail?: string }> = ({ ok, label, detail }) => (
  <div style={{ display: "grid", gridTemplateColumns: "14px 1fr", columnGap: 8, fontSize: 12, padding: "2px 0" }}>
    <span style={{ color: ok === null ? "#999" : ok ? "#4caf50" : "#f44336" }}>{ok === null ? "·" : ok ? "✓" : "✗"}</span>
    <span>{label}</span>
    {detail && <span style={{ gridColumn: 2, opacity: 0.6, fontSize: 11, overflowWrap: "anywhere" }}>{detail}</span>}
  </div>
);
