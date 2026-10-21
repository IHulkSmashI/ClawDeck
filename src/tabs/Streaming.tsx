import { ButtonItem, PanelSection, PanelSectionRow, TextField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getStreaming, moonlightInstall, rollback, setSetting, setStreamHost, StreamingState } from "../api";
import { Advanced } from "../components/Advanced";
import { explain, Note, Problem, Status } from "../components/Hint";
import { addShortcut, shortcutExists } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** Moonlight with VRR (Nonary's fork, by the author of Vibepollo): stream games from your PC to the Claw. */
export const StreamingSection: FC = () => {
  const [s, setS] = useState<StreamingState | null>(null);
  const [host, setHost] = useState("");
  const [app, setApp] = useState("Desktop");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => {
    const r = await getStreaming();
    setS(r);
    if (r.host) setHost(r.host);
    if (r.app) setApp(r.app);
  };
  useEffect(() => { load(); }, []);

  const inSteam = shortcutExists(s?.shortcut);
  const hostInSteam = shortcutExists(s?.host_shortcut);

  const install = async () => {
    setBusy(true);
    try {
      const r = await moonlightInstall();
      if (!r.ok) { toast(`Moonlight VRR: not installed (${r.error})`); setErr(r.error ?? "unknown error"); return; }
      setErr(null);
      if (r.dry_run) { toast(`Dry run: ${r.would}`); return; }
      toast(r.updated ? `Moonlight VRR ${r.version} installed` : "Moonlight VRR is up to date");
      const st = await getStreaming();
      if (!shortcutExists(st.shortcut)) {
        const id = await addShortcut("Moonlight VRR", st.launcher, st.launcher.replace(/\/[^/]+$/, ""), "");
        if (id) await setSetting("moonlight_shortcut", id);
      }
    } finally { setBusy(false); load(); }
  };

  return (
    <PanelSection title="Stream from your PC">
      <PanelSectionRow>
        <Status ok={s ? s.installed : null} label={s?.installed ? `Moonlight VRR ${s.version}` : "Moonlight VRR is not installed"}
          detail={s?.installed ? (s.update ? `${s.latest} is out` : "newest version") : undefined} />
        <ButtonItem layout="below" disabled={busy} onClick={install}
          description={explain("Play games running on your PC, over Wi-Fi or a cable. Use Vibepollo, Apollo or Sunshine on the PC; this Moonlight keeps VRR smooth.", undefined, "needs good 5 or 6 GHz Wi-Fi.")}>{busy ? "Working…" : !s?.installed ? "Install Moonlight VRR" : s.update ? "Update Moonlight VRR" : inSteam ? "Check for a newer build" : "Add Moonlight to Steam"}</ButtonItem>
        <Problem error={err} what="Streaming" />
        {s?.installed && inSteam && <Note>In your Steam library: start it from there and pair with your PC once (type the PIN it shows into Vibepollo's web page).</Note>}
        {s?.flathub_moonlight && <Note>The regular Moonlight is installed too. Both can stay; Moonlight VRR streams more smoothly.</Note>}
      </PanelSectionRow>
      {s?.installed && (
        <PanelSectionRow>
          <TextField label="Your PC (name or IP address)" value={host} onChange={(e: any) => setHost(e.target.value)} />
          <TextField label="App to open" value={app} onChange={(e: any) => setApp(e.target.value)} />
          <ButtonItem layout="below" disabled={!host.trim() || busy}
            description={explain("A Steam shortcut that goes straight into the stream. Pair once in Moonlight first. To leave a stream: hold View + Menu + LB + RB.")}
            onClick={async () => {
            const r = await setStreamHost(host, app);
            if (!r.ok) { toast(`Stream: not saved (${r.error ?? "unknown error"})`); setErr(r.error ?? "unknown error"); return; }
            setErr(null);
            if (!hostInSteam) {
              const id = await addShortcut(`Stream: ${app || "Desktop"} on ${host}`, r.launcher!, r.launcher!.replace(/\/[^/]+$/, ""), r.launch_options!);
              if (id) { await setSetting("moonlight_host_shortcut", id); toast("Added to Steam: start it to go straight into the stream"); }
              else toast("Steam refused the shortcut; start Moonlight VRR and pick your PC there");
            } else toast("Saved");
            load();
          }}>{hostInSteam ? "Save" : "Add a one-tap stream to Steam"}</ButtonItem>
        </PanelSectionRow>
      )}
      {s?.previous && (
        <Advanced id="streaming" label="Problems after an update?">
          <PanelSectionRow>
            <ButtonItem layout="below" description={explain("Puts the build you had before back. Updates skip the one you left until a newer one comes out.")}
              onClick={async () => { const r = await rollback("moonlight"); toast(r.ok ? `Back to Moonlight VRR ${r.version}` : `Not possible: ${r.error}`); load(); }}>Go back to the previous Moonlight build</ButtonItem>
          </PanelSectionRow>
        </Advanced>
      )}
    </PanelSection>
  );
};
