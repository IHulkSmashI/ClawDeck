import { FC } from "react";

/** ClawDeck's mark: the logo's screen with its three claw marks, filling the whole box. In colour (the logo's blue to
 * violet) in Decky's plugin list and at the top of ClawDeck's panel (maintainer, 2026-11-05: "some color"). */
let n = 0;
export const LogoIcon: FC<{ size?: string | number }> = ({ size = "1em" }) => {
  const id = `cdlogo${++n}`;
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label="ClawDeck" style={{ display: "block" }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3fa7ff" />
          <stop offset="1" stopColor="#a855f7" />
        </linearGradient>
      </defs>
      <rect x="6" y="14" width="88" height="72" rx="16" fill="none" stroke={`url(#${id})`} strokeWidth="9" />
      <g fill={`url(#${id})`}>
        <rect x="27" y="27" width="10" height="46" rx="5" transform="rotate(20 32 50)" />
        <rect x="45" y="27" width="10" height="46" rx="5" transform="rotate(20 50 50)" />
        <rect x="63" y="27" width="10" height="46" rx="5" transform="rotate(20 68 50)" />
      </g>
    </svg>
  );
};
