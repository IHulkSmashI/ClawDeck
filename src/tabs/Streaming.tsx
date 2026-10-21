import { ButtonItem, PanelSection, PanelSectionRow, TextField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getStreaming, moonlightInstall, rollback, setSetting, setStreamHost, StreamingState } from "../api";
import { Advanced } from "../components/Advanced";
import { Hint, Status } from "../components/Hint";
import { addShortcut, shortcutExists } from "../steam";

const toast = (body: string) => toaster.toast({ title: "ClawDeck", body });

/** Moonlight with VRR (Nonary's fork, by the author of Vibepollo): stream games from your PC to the Claw. */
export const StreamingSection: FC = () => {
  const [s, setS] = useState<StreamingState | null>(null);
  const [host, setHost] = useState("");
  const [app, setApp] = useState("Desktop");
  const [busy, setBusy] = useState(false);
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
      if (!r.ok) { toast(`Not installed: ${r.error}`); return; }
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
        <Hint does="Plays games running on your PC on the Claw, over Wi-Fi or a cable. Use Vibepollo (or Apollo / Sunshine) on the PC." why="this Moonlight build shows each frame as soon as it arrives, so VRR stays smooth." cost="needs a good 5 GHz or 6 GHz Wi-Fi; on Wi-Fi pick AV1 in Moonlight's settings." />
        <ButtonItem layout="below" disabled={busy} onClick={install}>{busy ? "Working…" : !s?.installed ? "Install Moonlight VRR" : s.update ? "Update Moonlight VRR" : inSteam ? "Check for a newer build" : "Add Moonlight to Steam"}</ButtonItem>
        {s?.installed && inSteam && <Hint does="Moonlight VRR is in your Steam library: start it from there and pair with your PC once (it shows a PIN to type into Vibepollo's web page)." />}
        {s?.flathub_moonlight && <Hint does="The regular Moonlight from Flathub is installed too. Both can stay; use Moonlight VRR for smoother streams." />}
      </PanelSectionRow>
      {s?.installed && (
        <PanelSectionRow>
          <TextField label="Your PC (name or IP address)" value={host} onChange={(e: any) => setHost(e.target.value)} />
          <TextField label="App to open" value={app} onChange={(e: any) => setApp(e.target.value)} />
          <ButtonItem layout="below" disabled={!host.trim() || busy} onClick={async () => {
            const r = await setStreamHost(host, app);
            if (!r.ok) { toast(r.error ?? "Not saved"); return; }
            if (!hostInSteam) {
              const id = await addShortcut(`Stream: ${app || "Desktop"} on ${host}`, r.launcher!, r.launcher!.replace(/\/[^/]+$/, ""), r.launch_options!);
              if (id) { await setSetting("moonlight_host_shortcut", id); toast("Added to Steam: start it to go straight into the stream"); }
              else toast("Steam refused the shortcut; start Moonlight VRR and pick your PC there");
            } else toast("Saved");
            load();
          }}>{hostInSteam ? "Save" : "Add a one-tap stream to Steam"}</ButtonItem>
          <Hint does="A Steam shortcut that opens the stream straight away, without going through Moonlight's menus." why="pair once in Moonlight first." cost="to leave a stream: hold View + Menu + LB + RB." />
        </PanelSectionRow>
      )}
      {s?.previous && (
        <Advanced id="streaming" label="Problems after an update?">
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => { const r = await rollback("moonlight"); toast(r.ok ? `Back to Moonlight VRR ${r.version}` : `Not possible: ${r.error}`); load(); }}>Go back to the previous Moonlight build</ButtonItem>
            <Hint does="Puts the build you had before back; automatic updates skip the one you left until a newer build comes out." />
          </PanelSectionRow>
        </Advanced>
      )}
    </PanelSection>
  );
};
