import { Field } from "@decky/ui";
import { FC, ReactNode } from "react";

/**
 * The explanation every control carries (project UI rule): what it does, why you'd use it, what it costs. It goes in
 * the control's own `description`, so it sits inside the focusable row: moving the D-pad onto a control scrolls its
 * explanation into view with it. Nothing on a page is plain text the controller can't reach.
 */
export const explain = (does: ReactNode, why?: ReactNode, cost?: ReactNode): ReactNode => (
  <span style={{ whiteSpace: "normal" }}>
    {does}
    {why && <><br />Why: {why}</>}
    {cost && <><br />Cost: {cost}</>}
  </span>
);

const MARK = { ok: "✓", bad: "✗", info: "" };
const COLOR = { ok: "#4caf50", bad: "#f44336", info: "#9aa4b1" };

/** A read-only status line the controller can land on: a ✓ / ✗ / · mark, a label and an optional detail line. */
export const Status: FC<{ ok: boolean | null; label: ReactNode; detail?: ReactNode }> = ({ ok, label, detail }) => {
  const k = ok === null ? "info" : ok ? "ok" : "bad";
  return (
    <Field focusable bottomSeparator="none" description={detail}
      label={<span>{MARK[k] && <span style={{ color: COLOR[k], marginRight: 8 }}>{MARK[k]}</span>}{label}</span>} />
  );
};

/** The last thing that didn't apply, in red under the control it belongs to, with the reason (HARDWARE-FINDINGS §14). */
export const Problem: FC<{ error: string | null | undefined; what?: string }> = ({ error, what }) =>
  error ? <Status ok={false} label={what ? `${what} didn't apply` : "Didn't apply"} detail={error} /> : null;

/** A paragraph of plain information (no control), focusable so the D-pad scrolls through it. */
export const Note: FC<{ children: ReactNode; label?: ReactNode }> = ({ children, label }) => (
  <Field focusable bottomSeparator="none" label={label} description={children} />
);
