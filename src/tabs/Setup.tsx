import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { collectLogs, getHardware, selfTest, xessInstall, xessState, Hardware, SelfTest, XessState } from "../api";
import { confirmUntested } from "../components/confirm";
import { explain, Note, Problem, Status } from "../components/Hint";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** The XeSS XMX driver install, the self-test and the log export. */
export const SetupTab: FC<{ quick?: boolean }> = ({ quick }) => {
  const [hw, setHw] = useState<Hardware | null>(null);
  const [x, setX] = useState<XessState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<SelfTest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = async () => {
    const [h, xs] = await Promise.all([quick ? Promise.resolve(null) : getHardware(), xessState()]);
    setHw(h); setX(xs);
  };
  useEffect(() => { refresh(); }, []);

  const installXess = async (allow = false) => {
    setBusy("xess");
    try {
      const r = await xessInstall(allow);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("xess-xmx-linux", () => installXess(true)); return; }
      toast(r.ok ? (`${r.repo ?? "xess-xmx-linux"} ${r.tag ?? ""} installed. ${r.note ?? r.next ?? ""}`) : `XeSS XMX: not installed (${r.error})`);
      setErr(r.ok ? null : (r.error ?? "unknown error"));
    } finally { setBusy(null); refresh(); }
  };
  const noXmx = hw?.known && !hw.model.xmx;
  const xessLine = !x ? "XeSS XMX driver: …" : !x.installed ? "XeSS XMX driver: not installed"
    : `XeSS XMX ${x.version ?? ""}: ${x.status.state === "ok" ? "driver loads" : x.status.state === "failed" ? "driver failed, DP4a fallback" : "no check yet (log out and in)"}`;
  const xessDetail = x?.installed ? `On for every game; Per-game turns it off for one. ${x.status.mode ?? x.status.line ?? ""} · from ${x.source ? `${x.source.repo} ${x.source.tag}` : "unknown (installed by hand)"} · ${x.xell_capable ? "XeLL driver mode available" : "no XeLL driver mode (upstream build)"}` : "Gives XeSS games the same upscaling and 2–4x frame generation as on Windows, on for every game once installed.";

  return (
    <PanelSection title="Setup">
      <PanelSectionRow>
        <Status ok={x ? x.installed && x.status.state === "ok" : null} label={xessLine} detail={quick ? undefined : xessDetail} />
        {!noXmx && (
          <ButtonItem layout="below" disabled={!!busy} onClick={() => installXess(false)}
            description={explain("Downloads the fork's release (XeLL driver mode) and runs its installer; while the fork has no release, upstream's.", undefined, "about 230 MB; log out and in once after. Each game's first launch compiles for a minute or two, once.")}>
            {busy === "xess" ? "Installing…" : x?.installed ? "Update XeSS XMX" : "Install XeSS XMX"}
          </ButtonItem>
        )}
        {noXmx && <Note>No XMX cores on the {hw?.model.name}: games use XeSS on the regular graphics cores.</Note>}
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!!busy} onClick={async () => {
          setBusy("test");
          try { const r = await selfTest(); setTest(r); toast(r.ok ? `Self-test: ${r.summary}` : `Self-test: ${r.error}`); } finally { setBusy(null); }
        }} description={explain("Writes each control, reads it back, measures it and reverts (tools/selftest.py).", undefined, "about 40 s with fans; no game may run.")}>
          {busy === "test" ? "Testing…" : "Run self-test"}
        </ButtonItem>
        {test && <Status ok={test.ok} label={`Self-test: ${test.summary ?? (test.ok ? "PASS" : "FAIL")}`} detail={quick ? test.error ?? undefined : (test.lines ?? []).join(" · ") || test.error || undefined} />}
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={async () => { const p = await collectLogs(); toast(`Report saved: ${p}`); }}
          description={quick ? undefined : explain("One file on the Desktop with your hardware, the checks and what ClawDeck changed. Attach it to a GitHub issue.")}>Export log to Desktop</ButtonItem>
        <Problem error={err} what="Setup" />
      </PanelSectionRow>
      {!quick && hw && (
        <PanelSectionRow>
          <Status ok={hw.known} label={hw.model.name} detail={`${hw.known ? "" : "Not in ClawDeck's list yet · "}${hw.model.soc} · ${hw.model.gpu} · kernel ${hw.kernel} · ${hw.stack.controller ?? "no controller service"}`} />
          <Note label="Free and open source (MIT)">github.com/IHulkSmashI/ClawDeck. Built on many projects (CREDITS.md). Not affiliated with MSI, Valve or Intel. ko-fi.com/gamervault if it helps; never a nag.</Note>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
