import { ButtonItem, PanelSectionRow } from "@decky/ui";
import { FC, useEffect, useState } from "react";

import { updateCheck, updatePrepare, UpdateState } from "../api";
import { explain, Problem, Status } from "./Hint";

/** Decky's own plugin installer (utilities/install_plugin, what "Install plugin from URL" uses): it asks the user,
 *  checks the sha256, replaces ClawDeck's folder and reloads it. Install type 2 is Decky's UPDATE. */
const deckyInstall = (url: string, version: string, sha256: string) =>
  (window as any).DeckyBackend.callable("utilities/install_plugin")(url, "ClawDeck", version, sha256, 2);

const label = (tag: string) => tag.replace(/^v/, "").replace(/-beta\.?(\d+)/, " beta $1");

/** Setup -> Updates: which ClawDeck is installed, the newest release, and one button to update through Decky. GitHub
 *  is asked when Setup opens and on Check, never in the background. */
export const Updates: FC<{ quick?: boolean }> = ({ quick }) => {
  const [u, setU] = useState<UpdateState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const check = async () => {
    setBusy(true);
    try { const r = await updateCheck(); setU(r); setErr(r.ok ? null : r.error ?? null); } finally { setBusy(false); }
  };
  useEffect(() => { check(); }, []);

  const mine = u?.installed.tag ? label(u.installed.tag) : u?.installed.commit ? `test build ${u.installed.commit}` : "0.2.0 (version unknown)";
  const latest = u?.latest;
  return (
    <PanelSectionRow>
      <Status ok={!u ? null : !u.ok ? false : u.update ? null : true}
        label={!u ? "Updates: checking…" : !u.ok ? `ClawDeck ${mine}: couldn't check for updates` : !latest ? `ClawDeck ${mine}` :
          u.update ? `ClawDeck ${label(latest.tag)} is out (you have ${mine})` : `ClawDeck ${mine}: up to date`}
        detail={quick ? undefined : latest ? `Newest release: ${latest.name}. ${latest.notes}` : undefined} />
      {u?.ok && latest && u.update && (
        <ButtonItem layout="below" disabled={busy} onClick={async () => {
          setBusy(true);
          // first: tell ClawDeck the uninstall Decky runs before installing is an update, so nothing is reverted
          try {
            const p = await updatePrepare();
            if (!p.ok) { setErr(`couldn't prepare the update: ${p.error}`); return; }
            await deckyInstall(latest.url, latest.tag, latest.sha256);
          } catch (e) { setErr(String(e)); } finally { setBusy(false); }
        }} description={quick ? undefined : explain("Decky installs it the same way as Install plugin from URL: it asks first, checks the download, then reloads ClawDeck. Your settings, the XMX driver and your game setups stay.")}>
          {`Update to ${label(latest.tag)}`}
        </ButtonItem>
      )}
      {(!u?.update || !u?.ok) && (
        <ButtonItem layout="below" disabled={busy} onClick={check}>{busy ? "Checking…" : "Check for updates"}</ButtonItem>
      )}
      <Problem error={err} what="Updates" />
    </PanelSectionRow>
  );
};
