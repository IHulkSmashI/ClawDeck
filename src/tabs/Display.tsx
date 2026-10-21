import { ButtonItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getDisplay, getWideVrr, resetDisplay, saveDisplay, setWideVrr, DisplaySettings, DisplayState, WideVrr } from "../api";
import { Advanced } from "../components/Advanced";
import { confirmRestart } from "../components/confirm";
import { Hint, Status } from "../components/Hint";
import { StreamingSection } from "./Streaming";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** ClawLab's 30-120 Hz VRR fix for the Claw's own screen (py_modules/clawdeck/edid.py). */
const WideVrrSection: FC = () => {
  const [w, setW] = useState<WideVrr | null>(null);
  const [busy, setBusy] = useState(false);
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
      toast(r.ok ? `${on ? "30–120 Hz" : "Factory range"} after the next restart` : `Not changed: ${r.error}`);
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
          onChange={(v) => confirmRestart(v ? "Widen the VRR range?" : "Back to the factory range?",
            v ? "ClawDeck gives the GPU driver a corrected description of this screen (ClawLab's fix, only for the exact panel they validated), so VRR works from 30 Hz instead of 48. Takes a minute or two, then restart. To undo, turn this off and restart, or pick the previous entry in the boot menu."
              : "ClawDeck removes its corrected screen description. Takes a minute or two, then restart.",
            v ? "Widen" : "Undo", () => run(v))} />
        <Hint does="Lets VRR follow the frame rate down to 30 fps instead of 48 (the screen can do it; its factory description says 48)." why="games running 30–48 fps stop juddering, e.g. heavy games on battery." cost="a restart; on an untested panel the screen could flicker below 48 Hz (turn it off again)." />
      </PanelSectionRow>
    </PanelSection>
  );
};

export const DisplayTab: FC = () => {
  const [d, setD] = useState<DisplayState | null>(null);
  const [s, setS] = useState<DisplaySettings | null>(null);
  const [dirty, setDirty] = useState(false);
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
              <ToggleField label="Variable refresh rate (VRR)" checked={s.vrr} onChange={(v) => upd({ vrr: v })} />
              <Hint does="The screen refreshes when a frame is ready instead of on a fixed clock." why="smooth at 45–90 fps without tearing or vsync lag." cost="rare flicker in menus at very low fps." />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField label="Slowest refresh Steam offers (Hz)" value={s.vrr_min} min={range[0]} max={range[1]} step={1} showValue onChange={(v) => upd({ vrr_min: Math.min(v, s.vrr_max) })} />
              <SliderField label="Fastest refresh Steam offers (Hz)" value={s.vrr_max} min={range[0]} max={range[1]} step={1} showValue onChange={(v) => upd({ vrr_max: Math.max(v, s.vrr_min) })} />
              <Hint does="Sets the range of Steam's own refresh-rate slider (Quick Access → Performance); you still pick the rate there, per game." why="40 Hz for long battery, 120 Hz for fast games." cost="none by itself." />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField label="HDR in Game Mode" checked={s.hdr} onChange={(v) => upd({ hdr: v })} />
              <Hint does="Turns on gamescope's HDR output (for an HDR TV or monitor on the dock)." why="brighter highlights in HDR games; pair with 'HDR output' per game in Graphics." cost="SDR content can look dim on some TVs; tune brightness below." />
            </PanelSectionRow>
            <Advanced id="display">
              <PanelSectionRow>
                <SliderField label="SDR brightness in HDR (nits)" value={s.hdr_nits} min={100} max={1000} step={50} showValue disabled={!s.hdr} onChange={(v) => upd({ hdr_nits: v })} />
                <Hint does="How bright normal (SDR) content is while HDR is on." why="raise it if menus look grey on your TV." cost="too high clips highlights." />
              </PanelSectionRow>
              <PanelSectionRow>
                <div style={{ fontSize: 11, opacity: 0.7, wordBreak: "break-all" }}>Saved to {d?.conf}{d?.other_overrides.length ? `; also set in: ${d.other_overrides.join(", ")}` : ""}</div>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={!d?.saved} onClick={async () => { const r = await resetDisplay(); toast(r.ok ? "Back to the image's defaults after Game Mode restarts" : `Failed: ${r.error}`); load(); }}>Reset display settings</ButtonItem>
              </PanelSectionRow>
            </Advanced>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={!dirty} onClick={async () => {
                const r = await saveDisplay(s);
                toast(r.ok ? (r.dry_run ? "Dry run: nothing written (see log)" : "Saved. Restart Game Mode to apply: Steam → Power → Restart Steam") : `Failed: ${r.error}`);
                load();
              }}>Save</ButtonItem>
              <Hint does="Writes these settings for Game Mode; nothing changes until you save." why="docked 4K120 needs no flags: gamescope uses the TV's best mode." cost="takes effect after Game Mode restarts." />
            </PanelSectionRow>
          </>
        )}
      </PanelSection>
      <WideVrrSection />
      <StreamingSection />
    </>
  );
};
