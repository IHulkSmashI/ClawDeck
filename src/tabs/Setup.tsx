import { ButtonItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { collectLogs, getHardware, getHealth, getSetting, getSleep, runFix, setDryRun, setSetting, Hardware, Health, SleepState } from "../api";
import { Advanced } from "../components/Advanced";
import { confirmUntested } from "../components/confirm";
import { explain, Note, Status } from "../components/Hint";

const FIX_LABEL: Record<string, string> = {
  enable_hhd: "Turn on Handheld Daemon",
  install_xess: "Install the XeSS XMX driver",
  report_unknown_model: "Save a hardware survey for an issue",
  fix_face_buttons: "Map the face buttons (InputPlumber override for the EX)",
};

export const SetupTab: FC = () => {
  const [hw, setHw] = useState<Hardware | null>(null);
  const [health, setHealth] = useState<Health[]>([]);
  const [sleep, setSleep] = useState<SleepState | null>(null);
  const [dry, setDry] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const refresh = async (force = false) => {
    setBusy(true);
    try {
      setHw(await getHardware());
      setHealth(await getHealth(force));
      setDry(!!(await getSetting("dry_run")));
      setSleep(await getSleep());
    } finally { setBusy(false); }
  };
  useEffect(() => { refresh(); }, []);
  const problems = health.filter((h) => !h.ok && !h.optional);
  const optional = health.filter((h) => !h.ok && h.optional);
  const passing = health.filter((h) => h.ok);

  const fix = async (id: string, allowUnpinned = false) => {
    setBusy(true);
    try {
      const r = await runFix(id, allowUnpinned);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("this component", () => fix(id, true)); return; }
      toaster.toast({ title: "ClawDeck", body: r.ok ? (r.would ?? r.next ?? r.path ?? "Done") : `Failed: ${r.error ?? r.log ?? "see log"}` });
    } finally { setBusy(false); refresh(); }
  };

  return (
    <>
      <PanelSection title="This device">
        <PanelSectionRow>
          <Status ok={hw ? hw.known : null} label={hw?.model.name ?? "…"}
            detail={hw ? `${hw.known ? "" : "Not in ClawDeck's list yet · "}${hw.model.soc} · ${hw.model.gpu} · kernel ${hw.kernel}` : undefined} />
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Health check">
        <PanelSectionRow>
          <Status ok={health.length === 0 ? null : problems.length === 0}
            label={health.length === 0 ? "Checking…" : problems.length === 0 ? "Everything ClawDeck needs is ready" : `${problems.length} to fix`}
            detail={health.length ? `${passing.length} of ${health.length} checks pass` : undefined} />
        </PanelSectionRow>
        {[...problems, ...optional, ...(showAll ? passing : [])].map((h) => (
          <PanelSectionRow key={h.id}>
            <Status ok={h.ok ? true : h.optional ? null : false} label={h.title} detail={h.detail} />
            {h.fix && !h.ok && (
              <ButtonItem layout="below" disabled={busy} onClick={() => fix(h.fix!)}>{FIX_LABEL[h.fix] ?? h.fix}</ButtonItem>
            )}
          </PanelSectionRow>
        ))}
        {passing.length > 0 && (
          <PanelSectionRow>
            <ToggleField label={`Show the ${passing.length} passing checks`} checked={showAll} onChange={setShowAll} />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => refresh(true)}>Re-check</ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" description={explain("Saves one file with your hardware and the checks above. Attach it to a GitHub issue.")} onClick={async () => {
            const p = await collectLogs();
            toaster.toast({ title: "ClawDeck", body: `Report saved: ${p}` });
          }}>Save a bug report to Desktop</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Safety">
        <PanelSectionRow>
          <ToggleField label="Dry run" checked={dry} onChange={async (v) => { setDry(v); await setDryRun(v); await setSetting("dry_run", v); }}
            description={explain("ClawDeck only logs what it would change, and changes nothing. For trying things safely.")} />
        </PanelSectionRow>
        <Advanced id="setup" label="Show sleep details">
          {sleep && (
            <PanelSectionRow>
              <Note label={`Sleep: ${sleep.mem_sleep ?? "?"} · ${sleep.stats.success ?? 0} ok / ${sleep.stats.fail ?? 0} failed since boot`}>
                {sleep.s0ix_supported && <>Deep idle (S0ix): {Math.round((sleep.s0ix_residency_us ?? 0) / 1e6)} s. </>}
                {sleep.wake_enabled.length > 0 && <>Can wake the Claw: {sleep.wake_enabled.join(", ")}. </>}
                {sleep.kernel_params.length > 0 && <>Sleep parameters: {sleep.kernel_params.join(" ")}. </>}
                {sleep.fixes.map((f) => <div key={f.id}>• {f.symptom}: {f.fix}</div>)}
                If the Claw is warm after sleeping, deep idle is not being reached.
              </Note>
            </PanelSectionRow>
          )}
        </Advanced>
      </PanelSection>
      <PanelSection title="About">
        <PanelSectionRow>
          <Note label="Free and open source (MIT)">
            github.com/IHulkSmashI/ClawDeck. Built on the work of many projects; the full list is in CREDITS.md. Not affiliated
            with MSI, Valve or Intel. If it saves you battery or time: ko-fi.com/gamervault. Never a paywall, never a nag.
          </Note>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
