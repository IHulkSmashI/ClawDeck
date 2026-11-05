import { ConfirmModal, showModal } from "@decky/ui";

/** Ask before a change that needs a restart and touches how the system boots. */
/** A confirm dialog that closes itself when OK is pressed, then runs the action. Left to Steam, the dialog stayed open
 * after "Remove" while the fix ran in the background (maintainer, 2026-11-05: "doesn't respond or take the pop up
 * window away"). */
const confirm = (title: string, text: string, ok: string, run: () => void) => {
  let m: { Close: () => void } | null = null;
  m = showModal(<ConfirmModal strTitle={title} strDescription={text} strOKButtonText={ok}
    onOK={() => { try { m?.Close(); } catch { /* already closed */ } run(); }} />);
  return m;
};

export const confirmRestart = (title: string, text: string, ok: string, run: () => void) => confirm(title, text, ok, run);

/** Ask before running a fix that downloads something nobody has tested yet (versions.json not pinned). */
export const confirmUntested = (what: string, run: () => void) =>
  confirm("Not tested on a Claw yet", `No version of ${what} has been verified on a Claw 8 EX yet, so ClawDeck would install the latest release. It can be undone, but it may not work. Continue?`,
    "Install latest", run);
