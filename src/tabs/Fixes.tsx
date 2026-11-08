import { ButtonItem, ToggleField, PanelSection, PanelSectionRow } from "@decky/ui";
import { notify } from "../components/notify";
import { FC, useEffect, useState } from "react";

import { getFixes, getHealth, runFix, setWideVrr, Fixes, Health } from "../api";
import { confirmRestart } from "../components/confirm";
import { Brightness } from "../components/Brightness";
import { explain, Group, Problem, Status, useExplanations } from "../components/Hint";

const toast = (body: string) => notify(body);

/** What the hardware gets wrong out of the box: the VRR range, the Claw 8 EX face buttons, and
 *  the health check in one line. Install / remove buttons, not toggles: each one is a change to the system. */
export const FixesTab: FC<{ quick?: boolean }> = ({ quick }) => {
  useExplanations();
  const [f, setF] = useState<Fixes | null>(null);
  const [health, setHealth] = useState<Health[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = async (force = false) => { setF(await getFixes()); if (!quick) setHealth(await getHealth(force)); };
  useEffect(() => { load(); }, []);

  const vrr = f?.vrr;
  const want = vrr?.pending ? vrr.pending === "on" : !!vrr?.wide;
  const live = vrr?.method === "live" || (!vrr?.method && !vrr?.boot_backend && !!vrr?.live_possible);
  const runVrr = async (on: boolean) => {
    setBusy(true);
    try {
      const r = await setWideVrr(on);
      toast(r.ok ? `${on ? "VRR fix on: 30–120 Hz" : "VRR fix off: factory range"}${r.restart ? " after the next restart" : ""}` : `VRR fix: not changed (${r.error})`);
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
    <PanelSection title={quick ? undefined : "Fixes"}>
      <PanelSectionRow>
        <Group title="Screen (VRR)" first />
        <Status ok={vrr ? (vrr.wide ? true : vrr.supported ? false : null) : null}
          label={vrr?.range ? `VRR range: ${vrr.range[0]}–${vrr.range[1]} Hz` : "VRR range unknown"}
          detail={vrr?.not_taken ? vrr.reason ?? undefined : vrr?.pending && (vrr.pending === "on") !== vrr.wide ? "restart to apply the change" : vrr?.reason ?? (vrr?.panel_name ? `${vrr.panel_name} panel, validated` : undefined)} />
        {vrr && (vrr.supported || vrr.wide) && (
          <ButtonItem layout="below" disabled={busy}
            description={explain(want ? "Puts the factory 48–120 Hz description back." : "The screen does VRR from 30 Hz but says 48, so games at 30–48 fps judder. Only for screens whose factory description is known.", undefined,
              live ? "turn it off; a restart also drops it until ClawDeck starts again. Nothing on disk changes." : vrr?.boot_backend === "steamos-grub" ? "a restart; if the screen misbehaves, pick the other SteamOS version in the boot menu (after an update it may have the fix too) and Remove it here." : "a restart; the previous boot entry always starts without it.")}
            onClick={() => confirmRestart(want ? "Remove the VRR fix?" : "Install the VRR fix 30–120 Hz?",
              want ? (vrr.method === "boot" ? "ClawDeck removes its corrected screen description. Takes a minute or two, then restart." : "The factory range comes back at once; the screen may blink once.")
                : live ? "ClawDeck gives the GPU driver a corrected description of this screen, so VRR works from 30 Hz instead of 48. It applies at once (the screen may blink once) and again each time ClawDeck starts; nothing on disk changes."
                : "ClawDeck gives the GPU driver a corrected description of this screen, so VRR works from 30 Hz instead of 48. Takes a minute or two, then restart.",
              want ? "Remove" : "Install", () => runVrr(!want))}>
            {want ? "Remove VRR fix" : "Install VRR fix 30–120 Hz"}
          </ButtonItem>
        )}
      </PanelSectionRow>
      <Brightness />
      {b?.applies && (
        <PanelSectionRow>
          <Group title="Face buttons" />
          <Status ok={b.ok} label={b.installed ? "Face buttons: fix installed" : b.upstream_fixed ? "Face buttons: InputPlumber maps them itself" : "Face buttons: dead in Game Mode"}
            detail={b.installed ? "left = Steam menu, right = Quick Access (InputPlumber override)" : b.upstream_fixed ? `map ${b.stock_map}; ClawDeck's override isn't needed` : "the EX sends Win+G and F15; InputPlumber's stock map waits for other keys"} />
          <ButtonItem layout="below" disabled={busy} onClick={() => fix(b.installed ? "undo_face_buttons" : "fix_face_buttons")}
            description={explain(b.installed ? "Deletes ClawDeck's InputPlumber override and restarts InputPlumber." : "Gives InputPlumber the right button map for the EX and restarts it. ClawDeck installs this by itself on a Claw 8 EX; once you remove it, it stays removed.", undefined, "none; takes effect at once.")}>
            {b.installed ? "Remove face-button fix" : "Install face-button fix"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {f?.gyro?.applies && (
        <PanelSectionRow>
          <Group title="Gyro (beta)" />
          <Status ok={f.gyro.state === "on" ? true : f.gyro.state === "off" ? null : false} label={f.gyro.line ?? "Gyro"}
            detail={f.gyro.installed ? `firmware ${f.gyro.firmware_ok ? "MSI's" : "missing"} · sensor ${f.gyro.gyro_iio ? "found" : "not yet"} · InputPlumber ${f.gyro.inputplumber_imu ? "reads it" : "doesn't read it"}` : undefined} />
          {(f.gyro.installed || f.gyro.supported) && (
            <ButtonItem layout="below" disabled={busy}
              description={explain(f.gyro.installed ? "Takes out the sensor-hub firmware, the gyro program, its two services and the gyro line in InputPlumber's config. Restart to finish."
                : "The Claw 8 EX's gyro sits behind Intel's sensor hub, which needs MSI's firmware. ClawDeck downloads MSI's sensor-hub kit, checks the exact firmware file, installs it with a small program that lets Linux read the gyro, and tells InputPlumber to pass it to Steam as a Steam Deck gyro. Then restart once and recalibrate gyro in Steam's controller settings. Method found by Kisenir on r/MSIClaw.",
                undefined, f.gyro.installed ? "none; the gyro is gone after the restart." : "a cold boot (shut down, then power on); Remove puts everything back. A firmware file other than MSI's tested one is never installed.")}
              onClick={() => confirmRestart(f.gyro!.installed ? "Remove the gyro?" : "Install the gyro (beta)?",
                f.gyro!.installed ? "ClawDeck removes its gyro files and the kernel setting. Restart afterwards." : "ClawDeck downloads MSI's sensor-hub firmware (about 1 minute) and installs it with the gyro program. Then shut the Claw down fully and turn it on again: a cold boot, not a restart, so the sensor hub starts clean.",
                f.gyro!.installed ? "Remove" : "Install", () => fix(f.gyro!.installed ? "gyro_off" : "gyro_on"))}>
              {f.gyro.installed ? "Remove gyro" : "Install gyro (beta)"}
            </ButtonItem>
          )}
          {f.gyro.installed && (
            <ToggleField label="Accelerometer too" checked={!!f.gyro.accel} disabled={busy}
              onChange={(v) => fix(v ? "gyro_accel_on" : "gyro_accel_off")}
              description={explain("Also passes the accelerometer to Steam. Leave it off for gyro aiming: Steam mixes the two and aim gets worse. Turn it on for games that use tilt.", undefined, "none; it switches at once (InputPlumber restarts).")} />
          )}
        </PanelSectionRow>
      )}
      {f?.overlay_power?.available && (
        <PanelSectionRow>
          <Group title="Performance overlay" />
          <Status ok={f.overlay_power.on ? true : null}
            label={f.overlay_power.on ? "Watts in the overlay: on (shows after a restart)" : "Watts in the overlay: off (shows 0.0 W)"}
            detail={f.overlay_power.battery_w != null ? `Battery now: ${f.overlay_power.battery_w} W (ClawDeck corrects the EX's 100x battery reading)` : undefined} />
          <ButtonItem layout="below" disabled={busy} onClick={() => fix(f.overlay_power.on ? "overlay_power_off" : "overlay_power_on")}
            description={explain(f.overlay_power.on ? "Makes the chip's power counter root-only again, as Linux ships it."
              : "Lets the performance overlay read the chip's power counter, so it shows real watts (CPU and graphics together). Linux makes it root-only by default; ClawDeck opens it at every boot while this is on. The overlay checks once per Game Mode session, so the watts appear after the next restart.",
              undefined, f.overlay_power.on ? undefined : "any program on the Claw can then read the power use, which in theory can leak a little about what other programs compute. Harmless for a gaming handheld; off by default.")}>
            {f.overlay_power.on ? "Hide watts again" : "Show watts in the overlay"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {f?.quiet_log?.applies && (
        <PanelSectionRow>
          <Group title="System log" />
          <Status ok={f.quiet_log.installed}
            label={f.quiet_log.installed ? "System log: split-lock flood silenced" : "System log: flooded by split-lock lines"}
            detail={f.quiet_log.per_minute != null ? `${f.quiet_log.per_minute} split-lock lines in the last minute` : undefined} />
          <ButtonItem layout="below" disabled={busy} onClick={() => fix(f.quiet_log!.installed ? "undo_quiet_split_lock_log" : "quiet_split_lock_log")}
            description={explain(f.quiet_log.installed ? "Lets steamos-manager log split-lock hits again (removes ClawDeck's one-line setting)."
              : "steamos-manager writes every split-lock hit from Steam into the system log, thousands a minute, for a crash reporter this system doesn't have. It costs CPU and battery, and the log then erases earlier boots within minutes, so a crash or a failed wake leaves no trace. This silences only that part. ClawDeck does this by itself on Bazzite; once you undo it, it stays undone.",
              undefined, "steamos-manager restarts once (a second; Steam reconnects by itself).")}>
            {f.quiet_log.installed ? "Log split-lock hits again" : "Silence the split-lock flood"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Group title="Health check" />
        <Status ok={f ? f.health.ok : null} label={f ? `Health: ${f.health.line}` : "Health: checking…"} detail={f ? `${f.health.total - f.health.problems} of ${f.health.total} checks pass` : undefined} />
        <Problem error={err} what="Fix" />
      </PanelSectionRow>
      {!quick && problems.map((h) => (
        <PanelSectionRow key={h.id}>
          <Status ok={false} label={h.title} detail={h.detail} />
          {h.fix && <ButtonItem layout="below" disabled={busy} onClick={() => fix(h.fix!)}>{h.fix === "install_xess" ? "Install the XeSS XMX driver (Setup)" : h.fix === "quiet_split_lock_log" ? "Silence the split-lock flood" : h.fix === "report_unknown_model" ? "Save a hardware survey for an issue" : "Fix"}</ButtonItem>}
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
