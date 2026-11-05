import { Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { createContext, FC, ReactNode, useContext, useEffect, useRef, useState } from "react";

/**
 * The explanation every control carries (project UI rule): what it does, why you'd use it, what it costs. It goes in
 * the control's own `description`, so it sits inside the focusable row: moving the D-pad onto a control scrolls its
 * explanation into view with it. Nothing on a page is plain text the controller can't reach.
 */
let shown = true;
/** Quick Access hides the explanations unless its "Explain" switch is on (like the plugins people know: controls and
 *  status first); the full-screen page always shows them. Each panel says which through HelpContext, and every
 *  component that calls explain() starts its render with useExplanations(), so a re-render of one panel never uses
 *  the other's choice. */
export const HelpContext = createContext(true);
export const useExplanations = () => { shown = useContext(HelpContext); return shown; };

export const explain = (does: ReactNode, why?: ReactNode, cost?: ReactNode): ReactNode => (!shown ? undefined :
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

/** A heading inside a panel section: Steam's blue, bold, small caps, with space above and a thin rule, so blocks of
 * settings read as separate groups instead of one long list (maintainer, 2026-11-04). */
export const Group: FC<{ title: ReactNode; first?: boolean }> = ({ title, first }) => (
  <div style={{ margin: first ? "2px 0 6px" : "14px 0 6px", paddingBottom: 4, borderBottom: "1px solid rgba(26,159,255,0.35)",
    color: "#1a9fff", fontWeight: 700, fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase" }}>{title}</div>
);

/** A section of Quick Access that folds away behind its heading, so the panel is a short list of headings and only the
 * part in use is open (maintainer, 2026-11-04: "it's just so long"). Open or closed is remembered on this device; the
 * state also lives outside the component, so a Quick Access re-mount keeps it. */
const folds = new Map<string, boolean>();
let foldsAt = 0;
const KEEP_MS = 10000;
export const Fold: FC<{ id: string; title: string; summary?: ReactNode; open?: boolean; children: ReactNode }> = ({ id, title, summary, open: dflt = false, children }) => {
  // every time Quick Access opens, all sections are closed (maintainer, 2026-11-05: "always open ClawDeck with all 4
  // menus closed"). Only a re-mount right after a change keeps them as they were: Steam's dropdown re-mounts Quick
  // Access when its menu closes, and the section in use must stay open then.
  const [open, setOpen] = useState<boolean>(() => (Date.now() - foldsAt < KEEP_MS && folds.has(id) ? folds.get(id)! : dflt));
  const toggle = () => { const v = !open; folds.set(id, v); foldsAt = Date.now(); setOpen(v); };
  useEffect(() => { if (open) foldsAt = Date.now(); });       // using an open section keeps it open across a re-mount
  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <Field focusable highlightOnFocus bottomSeparator="none" onActivate={toggle} onClick={toggle}
            label={<span style={{ color: "#1a9fff", fontWeight: 700, fontSize: 14, letterSpacing: "0.06em", textTransform: "uppercase" }}>
              <span style={{ display: "inline-block", width: 16 }}>{open ? "▾" : "▸"}</span>{title}</span>}
            description={!open && summary ? <span style={{ marginLeft: 16 }}>{summary}</span> : undefined} />
        </PanelSectionRow>
      </PanelSection>
      {open && children}
    </>
  );
};

/** Steam's dropdown can re-mount Quick Access when its menu closes; the D-pad then lands at the top, in another
 * section (maintainer, 2026-11-04: "picking FSR takes me to a different section"). A pick marks its control, and the
 * control, when it mounts again within a few seconds, takes the focus back and scrolls itself into view. */
let lastMark: { id: string; t: number } | null = null;
export const markPick = (id: string) => { lastMark = { id, t: Date.now() }; };
export const Anchor: FC<{ id: string; children: ReactNode }> = ({ id, children }) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!lastMark || lastMark.id !== id || Date.now() - lastMark.t > 4000) return;
    const t = setTimeout(() => {
      const el = ref.current;
      const target = el?.querySelector<HTMLElement>("button, [tabindex]");
      try { target?.focus(); el?.scrollIntoView({ block: "center" }); } catch { /* best effort */ }
    }, 60);
    return () => clearTimeout(t);
  }, []);
  return <div ref={ref}>{children}</div>;
};
