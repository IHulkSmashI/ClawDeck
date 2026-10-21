import { ButtonItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getDisplay, resetDisplay, saveDisplay, DisplaySettings, DisplayState } from "../api";
import { Advanced } from "../components/Advanced";
import { Hint, Status } from "../components/Hint";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

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
    </>
  );
};
