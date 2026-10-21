import { FC } from "react";

/**
 * The point-form explanation under every toggle and slider (project UI rule):
 * what it does / why you'd turn it on / what it costs. Two or three short bullets, no paragraphs.
 */
export const Hint: FC<{ does: string; why?: string; cost?: string }> = ({ does, why, cost }) => (
  <ul style={{ margin: "2px 0 6px 0", paddingLeft: 16, fontSize: 11, opacity: 0.75, lineHeight: 1.35 }}>
    <li>{does}</li>
    {why && <li>On: {why}</li>}
    {cost && <li>Cost: {cost}</li>}
  </ul>
);

export const Status: FC<{ ok: boolean | null; label: string; detail?: string }> = ({ ok, label, detail }) => (
  <div style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 12, padding: "2px 0" }}>
    <span style={{ color: ok === null ? "#999" : ok ? "#4caf50" : "#f44336", minWidth: 14 }}>{ok === null ? "·" : ok ? "✓" : "✗"}</span>
    <span>{label}</span>
    {detail && <span style={{ opacity: 0.6, fontSize: 11 }}>{detail}</span>}
  </div>
);
