import { ButtonItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { collectLogs, getHardware, optiscalerState, selfTest, setXmxEveryGame, xessInstall, xessState, Hardware, OptiState, SelfTest, XessState } from "../api";
import { confirmUntested } from "../components/confirm";
import { explain, Note, Problem, Status, useExplanations } from "../components/Hint";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** The XeSS XMX driver and Decky Framegen installs, the self-test and the log export. */
export const SetupTab: FC<{ quick?: boolean }> = ({ quick }) => {
  useExplanations();
  const [hw, setHw] = useState<Hardware | null>(null);
  const [x, setX] = useState<XessState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [op, setOp] = useState<OptiState | null>(null);
  const [test, setTest] = useState<SelfTest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = async () => {
    const [h, xs, o] = await Promise.all([quick ? Promise.resolve(null) : getHardware(), xessState(), optiscalerState()]);
    setHw(h); setX(xs); setOp(o);
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
  const noXmx = hw?.known && !hw.model.xmx;
  const xessLine = !x ? "XeSS XMX driver: …" : !x.installed ? "XeSS XMX driver: not installed"
    : x.update ? `XeSS XMX ${x.version ?? ""}: update ${x.pinned} available (tested)`
    : `XeSS XMX ${x.version ?? ""}: ${x.status.state === "ok" ? "driver loads" : x.status.state === "failed" ? "driver failed, DP4a fallback" : "no check yet (log out and in)"}`;
  const sc = x?.scope;
  const scopeText = !sc ? "" : sc.session_wide ? "Session-wide (older install): restart ClawDeck to make it per game. "
    : sc.per_game ? `Per game: games that ship XeSS or OptiScaler (${sc.scoped.length} Proton${sc.scoped.length === 1 ? "" : "s"} covered${sc.unscoped.length ? `; not yet: ${sc.unscoped.map((p: string) => p.split("/").pop()).join(", ")}` : ""}). `
    : "No Proton covered yet: start a game once, then re-open Setup. ";
  const xessDetail = x?.installed ? `${scopeText}Per-game sets frame generation, FSR games and low latency. ${x.status.mode ?? x.status.line ?? ""} · from ${x.source ? `${x.source.repo} ${x.source.tag}` : "unknown (installed by hand)"}${x.low_latency ? " · low latency with frame generation" : " · update for low latency and FSR on XeSS"}` : "Gives XeSS games the same upscaling and 2–4x frame generation as on Windows (up to 8x experimental), with low latency. It loads only in games that ship XeSS or OptiScaler, never for Steam's own screen.";

  return (
    <PanelSection title="Setup">
      <PanelSectionRow>
        <Status ok={!x ? null : !x.installed || x.status.state === "failed" ? false : x.status.state === "ok" ? true : null} label={xessLine} detail={quick ? undefined : xessDetail} />
        {!noXmx && (
          <ButtonItem layout="below" disabled={!!busy} onClick={() => installXess(false)}
            description={explain("Downloads xess-xmx-linux's tested release and runs its installer.", undefined, "about 260 MB; log out and in once after. Each game's first launch compiles for a minute or two, once.")}>
            {busy === "xess" ? "Installing…" : x?.update ? `Update XeSS XMX to ${x.pinned}` : x?.installed ? "Check for an update" : "Install XeSS XMX"}
          </ButtonItem>
        )}
        {noXmx && <Note>No XMX cores on the {hw?.model.name}: games use XeSS on the regular graphics cores.</Note>}
      </PanelSectionRow>
      {x?.installed && x.scope?.per_game && !noXmx && (
        <PanelSectionRow>
          <ToggleField label="XMX driver for every game" checked={!!x.scope.every_game}
            onChange={async (v) => { const r = await setXmxEveryGame(v); toast(r.ok ? (v ? "Every game gets the XMX driver from its next launch" : "Only games with XeSS, OptiScaler or FSR on XeSS get the XMX driver") : `Not changed: ${r.error}`); refresh(); }}
            description={explain("Every game starts on the XeSS XMX driver, so whatever XeSS, XeSS frame generation or XeLL a game has built in runs on the XMX cores, with or without OptiScaler. Steam's own screen keeps Bazzite's driver either way. Off: only games that ship XeSS or OptiScaler, or that you set up for FSR on XeSS.",
              undefined, "a game without any XeSS gains nothing from it and can lose a little speed (Onimusha did); switch that one off in Per-game. Each game's first launch compiles for a minute or two, once.")} />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Status ok={!op ? null : op.installed ? true : null}
          label={!op ? "OptiScaler: …" : op.installed ? `OptiScaler ${op.version}: ready${op.games ? `, in ${op.games} game${op.games === 1 ? "" : "s"}` : ""}` : `OptiScaler ${op.version}: downloads by itself when you pick an upscaler for a game`}
          detail={quick ? undefined : `ClawDeck's own OptiScaler and OptiPatcher (pinned, checked), put into a game by Per-game's recommended setup and taken out again by Undo.${op?.framegen ? ` Decky Framegen ${op.framegen_version ?? ""} is installed too: games it set up stay its own, ClawDeck shows a warning there and doesn't touch them.` : ""}`} />
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
