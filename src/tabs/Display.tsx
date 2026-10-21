import { ButtonItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getDisplay, getWideVrr, resetDisplay, saveDisplay, setWideVrr, DisplaySettings, DisplayState, WideVrr } from "../api";
import { Advanced } from "../components/Advanced";
import { confirmRestart } from "../components/confirm";
import { explain, Note, Problem, Status } from "../components/Hint";
import { StreamingSection } from "./Streaming";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** ClawLab's 30-120 Hz VRR fix for the Claw's own screen (py_modules/clawdeck/edid.py). */
const WideVrrSection: FC = () => {
  const [w, setW] = useState<WideVrr | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => setW(await getWideVrr());
  useEffect(() => { load(); }, []);
  if (!w || (!w.supported && !w.wide)) {
    return w?.panel ? (
      <PanelSection title="VRR range">
        <PanelSectionRow>
          <Status ok={null} label={w.range ? `${w.range[0]}–${w.range[1]} Hz` : "VRR range unknown"} detail={w.reason ?? undefined} />
        </PanelSectionRow>
      </PanelSection>
    ) : null;
  }
  const want = w.pending ? w.pending === "on" : w.wide;
  const run = async (on: boolean) => {
    setBusy(true);
    try {
      const r = await setWideVrr(on);
      toast(r.ok ? `${on ? "30–120 Hz" : "Factory range"} after the next restart` : `VRR fix: not changed (${r.error})`);
      setErr(r.ok ? null : (r.error ?? "unknown error"));
    } finally { setBusy(false); load(); }
  };
  return (
    <PanelSection title="VRR range">
      <PanelSectionRow>
        <Status ok={w.wide} label={w.range ? `Screen VRR: ${w.range[0]}–${w.range[1]} Hz` : "Screen VRR"}
          detail={w.pending && (w.pending === "on") !== w.wide ? "restart to apply the change" : `${w.panel_name ?? w.panel} panel`} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="VRR fix 30–120 Hz" checked={want} disabled={busy}
          description={explain("VRR follows the frame rate down to 30 fps instead of 48, so games running 30–48 fps stop juddering.", undefined, "a restart. Turn it off to undo.")}
          onChange={(v) => confirmRestart(v ? "Widen the VRR range?" : "Back to the factory range?",
            v ? "ClawDeck gives the GPU driver a corrected description of this screen (ClawLab's fix, only for the exact panel they validated), so VRR works from 30 Hz instead of 48. Takes a minute or two, then restart. To undo, turn this off and restart, or pick the previous entry in the boot menu."
              : "ClawDeck removes its corrected screen description. Takes a minute or two, then restart.",
            v ? "Widen" : "Undo", () => run(v))} />
        <Problem error={err} what="VRR fix" />
      </PanelSectionRow>
    </PanelSection>
  );
};

export const DisplayTab: FC = () => {
  const [d, setD] = useState<DisplayState | null>(null);
  const [s, setS] = useState<DisplaySettings | null>(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => { const r = await getDisplay(); setD(r); setS(r.settings); setDirty(false); };
  useEffect(() => { load(); }, []);
  const upd = (v: Partial<DisplaySettings>) => { setS({ ...(s as DisplaySettings), ...v }); setDirty(true); };
  const range = d?.vrr_range ?? [d?.panel_hz[0] ?? 60, d?.panel_hz[d.panel_hz.length - 1] ?? 120];
  const ext = d?.connectors.find((c) => !c.internal && c.status === "connected");
  const panel = d?.connectors.find((c) => c.internal);

  return (
    <>
      <PanelSection title="Screen">
        <PanelSectionRow>
          <Status ok={panel ? panel.status === "connected" : null} label={`Internal panel${panel ? ` (${panel.name})` : ""}`} detail={panel?.modes[0]} />
          <Status ok={ext ? true : null} label={ext ? `Dock / TV on ${ext.name}` : "No external display"} detail={ext?.modes.slice(0, 3).join(", ")} />
        </PanelSectionRow>
        {s && (
          <>
            <PanelSectionRow>
              <ToggleField label="Variable refresh rate (VRR)" checked={s.vrr} onChange={(v) => upd({ vrr: v })}
                description={explain("The screen refreshes when a frame is ready: smooth without tearing or lag.")} />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField label="Lowest refresh (Hz)" value={s.vrr_min} min={range[0]} max={range[1]} step={1} showValue onChange={(v) => upd({ vrr_min: Math.min(v, s.vrr_max) })}
                description={explain("The range Steam's own refresh slider offers (Quick Access → Performance).")} />
              <SliderField label="Highest refresh (Hz)" value={s.vrr_max} min={range[0]} max={range[1]} step={1} showValue onChange={(v) => upd({ vrr_max: Math.max(v, s.vrr_min) })} />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField label="HDR on the dock" checked={s.hdr} onChange={(v) => upd({ hdr: v })}
                description={explain("For an HDR TV or monitor. Normal content can look dim on some TVs: raise the brightness under Advanced.")} />
            </PanelSectionRow>
            <Advanced id="display">
              <PanelSectionRow>
                <SliderField label="Normal content brightness in HDR (nits)" value={s.hdr_nits} min={100} max={1000} step={50} showValue disabled={!s.hdr} onChange={(v) => upd({ hdr_nits: v })}
                  description={explain("Raise it if menus look grey on your TV; too high washes out highlights.")} />
              </PanelSectionRow>
              <PanelSectionRow>
                <Note label="Saved to">{d?.conf}{d?.other_overrides.length ? `; also set in: ${d.other_overrides.join(", ")}` : ""}</Note>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={!d?.saved} onClick={async () => { const r = await resetDisplay(); toast(r.ok ? "Back to the image's defaults after Game Mode restarts" : `Display settings: not reset (${r.error})`); setErr(r.ok ? null : (r.error ?? "unknown error")); load(); }}>Reset display settings</ButtonItem>
              </PanelSectionRow>
            </Advanced>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={!dirty} description={explain("Nothing changes until you save. Applies after Game Mode restarts.")} onClick={async () => {
                const r = await saveDisplay(s);
                toast(r.ok ? (r.dry_run ? "Dry run: nothing written (see log)" : "Saved. Restart Game Mode to apply: Steam → Power → Restart Steam") : `Display settings: not saved (${r.error})`);
                setErr(r.ok ? null : (r.error ?? "unknown error"));
                load();
              }}>Save</ButtonItem>
              <Problem error={err} what="Display settings" />
            </PanelSectionRow>
          </>
        )}
      </PanelSection>
      <WideVrrSection />
      <StreamingSection />
    </>
  );
};
