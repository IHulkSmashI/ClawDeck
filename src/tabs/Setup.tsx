import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { collectLogs, framegenInstall, framegenState, getHardware, selfTest, xessInstall, xessState, FramegenState, Hardware, SelfTest, XessState } from "../api";
import { confirmUntested } from "../components/confirm";
import { explain, Note, Problem, Status } from "../components/Hint";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** The XeSS XMX driver and Decky Framegen installs, the self-test and the log export. */
export const SetupTab: FC<{ quick?: boolean }> = ({ quick }) => {
  const [hw, setHw] = useState<Hardware | null>(null);
  const [x, setX] = useState<XessState | null>(null);
  const [fg, setFg] = useState<FramegenState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<SelfTest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = async () => {
    const [h, xs, f] = await Promise.all([quick ? Promise.resolve(null) : getHardware(), xessState(), framegenState()]);
    setHw(h); setX(xs); setFg(f);
  };
  useEffect(() => { refresh(); }, []);

  const installXess = async (allow = false) => {
    setBusy("xess");
    try {
      const r = await xessInstall(allow);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("xess-xmx-linux", () => installXess(true)); return; }
      // nothing newer: "up to date" (the driver check still ran, so the line above is current), never a red row
      toast(r.up_to_date ? `XeSS XMX ${r.tag ?? ""}: up to date`
        : r.ok ? (`${r.repo ?? "xess-xmx-linux"} ${r.tag ?? ""} installed. ${r.note ?? r.next ?? ""}`) : `XeSS XMX: not installed (${r.error})`);
      setErr(r.ok ? null : (r.error ?? "unknown error"));
    } finally { setBusy(null); refresh(); }
  };
  const installFramegen = async (allow = false) => {
    setBusy("framegen");
    try {
      const r = await framegenInstall(allow);
      if (!r.ok && r.error?.includes("no tested version")) { confirmUntested("Decky Framegen", () => installFramegen(true)); return; }
      toast(r.already ? (r.summary ?? "Decky Framegen is installed") : r.ok ? `Decky Framegen ${r.tag ?? ""} installed. ${r.next ?? ""}` : `Decky Framegen: not installed (${r.error})`);
      setErr(r.ok ? null : (r.error ?? "unknown error"));
    } finally { setBusy(null); refresh(); }
  };
  const noXmx = hw?.known && !hw.model.xmx;
  const xessLine = !x ? "XeSS XMX driver: …" : !x.installed ? "XeSS XMX driver: not installed"
    : `XeSS XMX ${x.version ?? ""}: ${x.status.state === "ok" ? "driver loads" : x.status.state === "failed" ? "driver failed, DP4a fallback" : "no check yet (log out and in)"}`;
  const xessDetail = x?.installed ? `On for every game; Per-game sets frame generation, FSR games and low latency per game. ${x.status.mode ?? x.status.line ?? ""} · from ${x.source ? `${x.source.repo} ${x.source.tag}` : "unknown (installed by hand)"}${x.low_latency ? " · low latency with frame generation" : " · update for low latency and FSR on XeSS"}` : "Gives XeSS games the same upscaling and 2–4x frame generation as on Windows (up to 8x experimental), with low latency, on for every game once installed.";
  const fgLine = !fg ? "Decky Framegen: …" : !fg.installed ? "Decky Framegen (OptiScaler): not installed"
    : fg.fgmod_ready ? `Decky Framegen ${fg.version ?? ""}: OptiScaler ${fg.optiscaler ?? ""} ready` : `Decky Framegen ${fg.version ?? ""}: open it once and tap Setup OptiScaler Mod`;

  return (
    <PanelSection title="Setup">
      <PanelSectionRow>
        <Status ok={!x ? null : !x.installed || x.status.state === "failed" ? false : x.status.state === "ok" ? true : null} label={xessLine} detail={quick ? undefined : xessDetail} />
        {!noXmx && (
          <ButtonItem layout="below" disabled={!!busy} onClick={() => installXess(false)}
            description={explain("Downloads xess-xmx-linux's tested release and runs its installer.", undefined, "about 260 MB; log out and in once after. Each game's first launch compiles for a minute or two, once.")}>
            {busy === "xess" ? "Installing…" : x?.installed ? "Update XeSS XMX" : "Install XeSS XMX"}
          </ButtonItem>
        )}
        {noXmx && <Note>No XMX cores on the {hw?.model.name}: games use XeSS on the regular graphics cores.</Note>}
      </PanelSectionRow>
      <PanelSectionRow>
        <Status ok={!fg ? null : fg.fgmod_ready ? true : fg.installed ? false : null} label={fgLine}
          detail={quick ? undefined : "OptiScaler for DLSS and FSR games (Bazzite's Portal ships it). Per-game adds it to a game and sets it to XeSS on XMX."} />
        {fg && !fg.installed && (
          <ButtonItem layout="below" disabled={!!busy} onClick={() => installFramegen(false)}
            description={explain("Downloads Decky Framegen's release into Decky's plugins and restarts Decky. Then open Decky Framegen once and tap Setup OptiScaler Mod.", undefined, "Decky restarts (a few seconds); an installed copy is never replaced.")}>
            {busy === "framegen" ? "Installing…" : "Install Decky Framegen"}
          </ButtonItem>
        )}
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
