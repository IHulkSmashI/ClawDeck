import { ConfirmModal, showModal } from "@decky/ui";

/** Ask before running a fix that downloads something nobody has tested yet (versions.json not pinned). */
export const confirmUntested = (what: string, run: () => void) =>
  showModal(<ConfirmModal strTitle="Not tested on a Claw yet" strDescription={`No version of ${what} has been verified on a Claw 8 EX yet, so ClawDeck would install the latest release. It can be undone, but it may not work. Continue?`}
    strOKButtonText="Install latest" onOK={run} />);
