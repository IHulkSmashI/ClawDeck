import { PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { gameStatus, setLaunch, GameStatus } from "../api";
import { LaunchWrapper } from "../components/LaunchWrapper";
import { explain, Note, Problem, Status } from "../components/Hint";
import { runningApp } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/**
 * The running game's XeSS: on the XMX cores or not, and XeLL in driver mode or not (the fork shim's logs say what
 * it did in the last launch). OptiScaler and Lossless Scaling per game are Bazzite's own Decky plugins (Decky
 * Framegen, decky-lsfg-vk), so they are not here.
 */
export const PerGameTab: FC<{ quick?: boolean }> = ({ quick }) => {
  const app = runningApp();
  const [gs, setGs] = useState<GameStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => { if (app) setGs(await gameStatus(app.appid)); };
  useEffect(() => { load(); }, [app?.appid]);

  if (!app) {
    return (
      <PanelSection title="Per-game">
        <PanelSectionRow><Note>Start a game: this panel shows whether its XeSS runs on the XMX cores and what XeLL does, and lets you change both.</Note></PanelSectionRow>
      </PanelSection>
    );
  }
  const launch = gs?.launch ?? {};
  const xmxOn = launch.xmx !== false;
  const xessOk = !!gs?.xess.installed && gs.xess.status.state === "ok";
  const xell = gs?.xell;
  const save = async (v: Partial<typeof launch>) => {
    const r = await setLaunch(app.appid, v);
    setErr(r.ok ? null : (r.error ?? "not saved"));
    if (!r.ok) toast(`Not saved: ${r.error}`);
    load();
  };

  return (
    <PanelSection title={`Per-game: ${app.name}`}>
      <PanelSectionRow>
        <ToggleField label="XeSS on XMX" checked={xmxOn} disabled={!gs?.xess.installed}
          description={explain(gs?.xess.installed ? `The patched driver runs this game's XeSS on the XMX cores (driver ${xessOk ? "loads: " + (gs.xess.status.mode ?? "") : "not loaded: " + (gs?.xess.status.line ?? "no check yet")}). Off = stock driver for this game only.`
            : "Install the XeSS XMX driver in Setup first.", undefined, quick ? undefined : "from the next launch; the first one compiles kernels for a minute or two, once.")}
          onChange={(v) => save({ xmx: v })} />
        <Status ok={gs ? (xmxOn && xessOk ? true : xmxOn && gs.xess.installed ? false : null) : null}
          label={!gs ? "XeSS: …" : !gs.xess.installed ? "XeSS: driver not installed (Setup)" : xmxOn ? (xessOk ? `XeSS on XMX: ${gs.xess.status.mode ?? "patched driver"}` : "XeSS: patched driver not loading, DP4a fallback") : "XeSS: stock driver for this game (DP4a)"} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="XeLL driver mode" checked={!!launch.xell} disabled={!gs?.xess.installed}
          description={explain(gs?.xess.xell_capable ? "Intel's latency reduction gets real GPU timings from the shim instead of pacing blind (the fork's XeLL driver mode)."
            : "Needs the fork build of the XeSS driver (Setup → Install XeSS XMX, once the fork publishes a release); with upstream's build XeLL stays in cross-vendor mode.",
            undefined, quick ? undefined : "logs what XeLL did into the game's prefix; from the next launch.")}
          onChange={(v) => save({ xell: v })} />
        <Status ok={xell?.mode === "driver" ? true : xell?.mode === "none" && launch.xell ? false : null}
          label={xell ? `XeLL: ${xell.mode === "driver" ? "driver mode" : xell.mode === "cross-vendor" ? "cross-vendor mode" : xell.mode === "off" ? "off" : "not seen"}` : "XeLL: …"}
          detail={xell?.detail} />
        <Problem error={err} />
      </PanelSectionRow>
      {!quick && (
        <PanelSectionRow>
          <Note label="DLSS or FSR game? Use Bazzite's own plugins">Decky Framegen (OptiScaler) turns a game's DLSS/FSR into XeSS, which then runs on the XMX cores with the driver above; decky-lsfg-vk is Lossless Scaling for games with no upscaler. Both are in the Bazzite Portal.</Note>
        </PanelSectionRow>
      )}
      {!quick && <LaunchWrapper appid={app.appid} name={app.name} />}
    </PanelSection>
  );
};
