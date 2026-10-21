import { ButtonItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { collectLogs, getHardware, getHealth, getSetting, getSleep, runFix, setDryRun, setSetting, Hardware, Health, SleepState } from "../api";
import { Advanced } from "../components/Advanced";
import { confirmUntested } from "../components/confirm";
import { Hint, Status } from "../components/Hint";

const FIX_LABEL: Record<string, string> = {
  enable_hhd: "Turn on Handheld Daemon",
  install_xess: "Install the XeSS XMX driver",
  report_unknown_model: "Save a hardware survey for an issue",
};

export const SetupTab: FC = () => {
  const [hw, setHw] = useState<Hardware | null>(null);
  const [health, setHealth] = useState<Health[]>([]);
  const [sleep, setSleep] = useState<SleepState | null>(null);
  const [dry, setDry] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try {
      setHw(await getHardware());
      setHealth(await getHealth());
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
          <div style={{ fontSize: 12 }}>
            <b>{hw?.model.name ?? "…"}</b> {hw && !hw.known && <span style={{ color: "#f44336" }}>(not in the table yet)</span>}
            <div style={{ opacity: 0.7 }}>{hw?.model.soc} · {hw?.model.gpu} · kernel {hw?.kernel}</div>
          </div>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Health check">
        <PanelSectionRow>
          <div style={{ fontSize: 13 }}>
            {health.length === 0 ? "Checking…" : problems.length === 0 ? <b>Everything ClawDeck needs is ready.</b>
              : <><b>{problems.length} to fix</b> · {passing.length} of {health.length} ready</>}
          </div>
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
          <ButtonItem layout="below" disabled={busy} onClick={refresh}>Re-check</ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={async () => {
            const p = await collectLogs();
            toaster.toast({ title: "ClawDeck", body: `Report saved: ${p}` });
          }}>Save a bug report to Desktop</ButtonItem>
          <Hint does="Writes one JSON file with hardware, paths and the checks above." why="attach it to a GitHub issue." cost="none; no personal data beyond your username in paths." />
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Safety">
        <PanelSectionRow>
          <ToggleField label="Dry run" checked={dry} onChange={async (v) => { setDry(v); await setDryRun(v); await setSetting("dry_run", v); }} />
          <Hint does="Logs every value ClawDeck would write instead of writing it." why="test a profile or autotune without touching the hardware." cost="nothing changes until you turn it off." />
        </PanelSectionRow>
        <Advanced id="setup" label="Show sleep details">
          {sleep && (
            <PanelSectionRow>
              <div style={{ fontSize: 11, opacity: 0.8 }}>
                <b>Sleep</b>: {sleep.mem_sleep ?? "?"} · {sleep.stats.success ?? 0} ok / {sleep.stats.fail ?? 0} failed since boot
                {sleep.s0ix_supported && <> · S0ix {Math.round((sleep.s0ix_residency_us ?? 0) / 1e6)} s</>}
                {sleep.wake_enabled.length > 0 && <div>Can wake the Claw: {sleep.wake_enabled.join(", ")}</div>}
                {sleep.kernel_params.length > 0 && <div>Sleep parameters: {sleep.kernel_params.join(" ")}</div>}
                {sleep.fixes.map((f) => <div key={f.id} style={{ marginTop: 4 }}>• {f.symptom}: {f.fix}</div>)}
              </div>
              <Hint does="What the kernel says about suspend: mode, failures, time in deep idle." why="if the Claw is warm after sleeping, S0ix is not being reached." cost="none; read-only. Full test: tools/suspend-cycle.sh." />
            </PanelSectionRow>
          )}
        </Advanced>
      </PanelSection>
      <PanelSection title="About">
        <PanelSectionRow>
          <div style={{ fontSize: 11, opacity: 0.75 }}>
            ClawDeck is free and open source (MIT) — github.com/IHulkSmashI/ClawDeck. Built on Handheld Daemon, PowerControl, xess-xmx-linux, OptiScaler and lsfg-vk.
            Not affiliated with MSI, Valve or Intel. If it saves you battery or time: <b>ko-fi.com/gamervault</b>. Never a paywall, never a nag.
          </div>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
