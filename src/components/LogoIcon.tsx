import { FC } from "react";

/** ClawDeck's mark for Decky's plugin list and the Quick Access tab: the logo's screen with its three claw marks,
 * filling the whole 1em box and drawn in the text colour (currentColor) like the other plugins' icons, so it follows
 * Steam's focus colour. The full logo (handheld on a blue disc, assets/logo.png) read too small at this size. */
export const LogoIcon: FC = () => (
  <svg viewBox="0 0 100 100" width="1em" height="1em" role="img" aria-label="ClawDeck" style={{ display: "block" }}>
    <rect x="6" y="14" width="88" height="72" rx="16" fill="none" stroke="currentColor" strokeWidth="9" />
    <g fill="currentColor">
      <rect x="27" y="27" width="10" height="46" rx="5" transform="rotate(20 32 50)" />
      <rect x="45" y="27" width="10" height="46" rx="5" transform="rotate(20 50 50)" />
      <rect x="63" y="27" width="10" height="46" rx="5" transform="rotate(20 68 50)" />
    </g>
  </svg>
);
