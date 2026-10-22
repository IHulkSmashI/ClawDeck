import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getFixes, getHealth, runFix, setWideVrr, Fixes, Health } from "../api";
import { confirmRestart } from "../components/confirm";
import { explain, Problem, Status } from "../components/Hint";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** What the hardware gets wrong out of the box: the VRR range (ClawLab's EDID fix), the Claw 8 EX face buttons, and
 *  the health check in one line. Install / remove buttons, not toggles: each one is a change to the system. */
export const FixesTab: FC<{ quick?: boolean }> = ({ quick }) => {
  const [f, setF] = useState<Fixes | null>(null);
  const [health, setHealth] = useState<Health[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = async (force = false) => { setF(await getFixes()); if (!quick) setHealth(await getHealth(force)); };
  useEffect(() => { load(); }, []);

  const vrr = f?.vrr;
  const want = vrr?.pending ? vrr.pending === "on" : !!vrr?.wide;
  const runVrr = async (on: boolean) => {
    setBusy(true);
    try {
      const r = await setWideVrr(on);
      toast(r.ok ? `${on ? "VRR fix installed: 30–120 Hz" : "VRR fix removed: factory range"} after the next restart` : `VRR fix: not changed (${r.error})`);
      setErr(r.ok ? null : (r.error ?? "unknown error"));
    } finally { setBusy(false); load(); }
  };
  const fix = async (id: string) => {
    setBusy(true);
    try {
      const r = await runFix(id, false);
      toast(r.ok ? (r.next ?? (r.removed ? "Face-button fix removed; InputPlumber restarted" : "Done")) : `Not done: ${r.error ?? r.log ?? "see the log"}`);
      setErr(r.ok ? null : (r.error ?? "unknown error"));
    } finally { setBusy(false); load(true); }
  };
  const b = f?.buttons;
  const problems = health.filter((h) => !h.ok && !h.optional);

  return (
    <PanelSection title="Fixes">
      <PanelSectionRow>
        <Status ok={vrr ? (vrr.wide ? true : vrr.supported ? false : null) : null}
          label={vrr?.range ? `VRR range: ${vrr.range[0]}–${vrr.range[1]} Hz` : "VRR range unknown"}
          detail={vrr?.pending && (vrr.pending === "on") !== vrr.wide ? "restart to apply the change" : vrr?.reason ?? (vrr?.panel_name ? `${vrr.panel_name} panel, ClawLab-validated` : undefined)} />
        {vrr && (vrr.supported || vrr.wide) && (
          <ButtonItem layout="below" disabled={busy}
            description={explain(want ? "Puts the factory 48–120 Hz description back." : "The screen does VRR from 30 Hz but says 48, so games at 30–48 fps judder. ClawLab's fix (the one Windows users install), for the exact panels ClawLab validated.", undefined, "a restart; the previous boot entry always starts without it.")}
            onClick={() => confirmRestart(want ? "Remove the VRR fix?" : "Install the VRR fix 30–120 Hz?",
              want ? "ClawDeck removes its corrected screen description. Takes a minute or two, then restart."
                : "ClawDeck gives the GPU driver a corrected description of this screen, so VRR works from 30 Hz instead of 48. Takes a minute or two, then restart.",
              want ? "Remove" : "Install", () => runVrr(!want))}>
            {want ? "Remove VRR fix" : "Install VRR fix 30–120 Hz"}
          </ButtonItem>
        )}
      </PanelSectionRow>
      {b?.applies && (
        <PanelSectionRow>
          <Status ok={b.ok} label={b.installed ? "Face buttons: fix installed" : b.upstream_fixed ? "Face buttons: InputPlumber maps them itself" : "Face buttons: dead in Game Mode"}
            detail={b.installed ? "left = Steam menu, right = Quick Access (InputPlumber override)" : b.upstream_fixed ? `map ${b.stock_map}; ClawDeck's override isn't needed` : "the EX sends Win+G and F15; InputPlumber's stock map waits for other keys"} />
          <ButtonItem layout="below" disabled={busy} onClick={() => fix(b.installed ? "undo_face_buttons" : "fix_face_buttons")}
            description={explain(b.installed ? "Deletes ClawDeck's InputPlumber override and restarts InputPlumber." : "Gives InputPlumber the right button map for the EX and restarts it.", undefined, "none; takes effect at once.")}>
            {b.installed ? "Remove face-button fix" : "Install face-button fix"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Status ok={f ? f.health.ok : null} label={f ? `Health: ${f.health.line}` : "Health: checking…"} detail={f ? `${f.health.total - f.health.problems} of ${f.health.total} checks pass` : undefined} />
        <Problem error={err} what="Fix" />
      </PanelSectionRow>
      {!quick && problems.map((h) => (
        <PanelSectionRow key={h.id}>
          <Status ok={false} label={h.title} detail={h.detail} />
          {h.fix && <ButtonItem layout="below" disabled={busy} onClick={() => fix(h.fix!)}>{h.fix === "install_xess" ? "Install the XeSS XMX driver (Setup)" : h.fix === "report_unknown_model" ? "Save a hardware survey for an issue" : "Fix"}</ButtonItem>}
        </PanelSectionRow>
      ))}
      {!quick && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => load(true)}>Re-check</ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
