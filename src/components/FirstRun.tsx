import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { notify } from "./notify";
import { FC, useEffect, useState } from "react";

import { getFixes, getHardware, setSetting, setWideVrr, xessInstall, xessState, Fixes, Hardware, XessState } from "../api";
import { confirmUntested } from "./confirm";
import { Group, Note, Status } from "./Hint";

const toast = (body: string) => notify(body);

/** The first start: what has to be in place before games can be set up, one step at a time (the XeSS XMX driver,
 * then the 30–120 Hz VRR fix), then the everyday panel. OptiScaler needs no step: it downloads with the first game.
 * The face buttons and the log fix need none either: ClawDeck applies them by itself. */
export const FirstRun: FC<{ onDone: () => void }> = ({ onDone }) => {
  const [x, setX] = useState<XessState | null>(null);
  const [f, setF] = useState<Fixes | null>(null);
  const [hw, setHw] = useState<Hardware | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = async () => { const [a, b, h] = await Promise.all([xessState(), getFixes(), getHardware()]); setX(a); setF(b); setHw(h); };
  useEffect(() => { load(); }, []);
  // the device first (maintainer, 2026-11-06): a chip without XMX cores (Claw A1M, Claw A8) gets no driver step,
  // a screen ClawDeck has no VRR fix for gets no VRR step
  const noXmx = hw?.caps?.xmx === false;
  const driverOk = !!x?.installed || noXmx;
  const vrr = f?.vrr;
  const vrrDone = !!vrr && (vrr.wide || vrr.pending === "on" || !vrr.supported);
  const finish = async () => { await setSetting("setup_done", true); onDone(); };

  const installDriver = async (allow = false) => {
    setBusy("xess");
    toast("Installing the XeSS XMX driver (about 260 MB)…");
    try {
      const r = await xessInstall(allow);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("xess-xmx-linux", () => installDriver(true)); return; }
      toast(r.ok ? "XeSS XMX driver installed" : `Not installed: ${r.error}`);
    } finally { setBusy(null); load(); }
  };
  const installVrr = async () => {
    setBusy("vrr");
    try {
      const r = await setWideVrr(true);
      toast(r.ok ? (r.restart ? "VRR fix 30–120 Hz installed: it works from the next restart" : "VRR fix 30–120 Hz on") : `Not installed: ${r.error}`);
    } finally { setBusy(null); load(); }
  };

  return (
    <PanelSection title="Set up ClawDeck">
      <PanelSectionRow>
        <Note>A step or two, once. After that this panel shows Power and your games.</Note>
      </PanelSectionRow>
      {hw && (
        <PanelSectionRow>
          <Status ok={hw.known ? true : null} label={`This is a ${hw.model.name}${hw.caps?.beta ? " (beta)" : ""}`}
            detail={hw.known ? `${hw.model.soc} · ${hw.model.gpu}${noXmx ? " · no XMX cores: XeSS runs on the regular graphics cores" : ""}` : "Not in ClawDeck's list yet: only what this device supports is shown"} />
        </PanelSectionRow>
      )}
      {!noXmx && <PanelSectionRow>
        <Group title={vrr?.supported ? "Step 1 of 2" : "Step 1"} first />
        <Status ok={driverOk ? true : null} label={`XeSS XMX driver: ${driverOk ? "installed" : "not installed"}`}
          detail="Runs XeSS, FSR 4.1.2, frame generation and XeLL on the XMX cores, per game (never for Steam's own screen)." />
        {!driverOk && (
          <ButtonItem layout="below" disabled={!!busy || !x} onClick={() => installDriver(false)}>
            {busy === "xess" ? "Installing… (a minute or two)" : "Install the XeSS XMX driver"}
          </ButtonItem>
        )}
      </PanelSectionRow>}
      {vrr?.supported && (
        <PanelSectionRow>
          <Group title={noXmx ? "Step 1" : "Step 2 of 2"} />
          <Status ok={vrrDone ? true : null} label={`VRR 30–120 Hz: ${vrr.wide ? "installed" : vrr.pending === "on" ? "installed, restart to use it" : "not installed"}`}
            detail={`The screen can do VRR from 30 Hz but says 48, so games below 48 fps judder.${vrr.live_possible && !vrr.boot_backend ? "" : " Needs one restart."}`} />
          {!vrrDone && (
            <ButtonItem layout="below" disabled={!!busy} onClick={installVrr}>
              {busy === "vrr" ? "Installing…" : "Install the VRR fix"}
            </ButtonItem>
          )}
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!!busy} onClick={finish}>
          {driverOk && (vrrDone || !vrr?.supported) ? "Done: show the panel" : "Skip for now"}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};
