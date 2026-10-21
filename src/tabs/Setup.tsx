import { ButtonItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { collectLogs, getHardware, getHealth, getSetting, setDryRun, setSetting, Hardware, Health } from "../api";
import { Hint, Status } from "../components/Hint";

export const SetupTab: FC = () => {
  const [hw, setHw] = useState<Hardware | null>(null);
  const [health, setHealth] = useState<Health[]>([]);
  const [dry, setDry] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try {
      setHw(await getHardware());
      setHealth(await getHealth());
      setDry(!!(await getSetting("dry_run")));
    } finally { setBusy(false); }
  };
  useEffect(() => { refresh(); }, []);

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
        {health.map((h) => (
          <PanelSectionRow key={h.id}>
            <Status ok={h.ok} label={h.title} detail={h.detail} />
          </PanelSectionRow>
        ))}
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
      <PanelSection title="About">
        <PanelSectionRow>
          <div style={{ fontSize: 11, opacity: 0.75 }}>
            ClawDeck is free and open source (MIT) — github.com/IHulkSmashI/ClawDeck. Built on Handheld Daemon, PowerControl and xess-xmx-linux.
            If it saves you battery or time: <b>ko-fi.com/gamervault</b>. Never a paywall, never a nag.
          </div>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Safety">
        <PanelSectionRow>
          <ToggleField label="Dry run" checked={dry} onChange={async (v) => { setDry(v); await setDryRun(v); await setSetting("dry_run", v); }} />
          <Hint does="Logs every value ClawDeck would write instead of writing it." why="test a profile or autotune without touching the hardware." cost="nothing changes until you turn it off." />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
