import { FC } from "react";

/** ClawDeck's logo (assets/logo.png, drawn from assets/src/logo.html) as an SVG for Decky's plugin list and the
 * Quick Access tab: the blue disc, the handheld, three claw marks on its screen. 1em square, so it sits where the
 * other plugins' icons do. */
export const LogoIcon: FC = () => (
  <svg viewBox="0 0 600 600" width="1em" height="1em" role="img" aria-label="ClawDeck" style={{ display: "block" }}>
    <defs>
      <linearGradient id="clawdeck-logo-disc" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="#7fd8ff" />
        <stop offset="0.48" stopColor="#3fa7ff" />
        <stop offset="1" stopColor="#23406f" />
      </linearGradient>
      <linearGradient id="clawdeck-logo-screen" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#1f2a38" />
        <stop offset="1" stopColor="#0e1218" />
      </linearGradient>
      <linearGradient id="clawdeck-logo-mark" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="#a6e6ff" />
        <stop offset="0.55" stopColor="#3fa7ff" />
        <stop offset="1" stopColor="#8a7bff" />
      </linearGradient>
      <clipPath id="clawdeck-logo-clip">
        <rect x="195" y="227" width="210" height="146" rx="14" />
      </clipPath>
    </defs>
    <circle cx="300" cy="300" r="280" fill="url(#clawdeck-logo-disc)" />
    {/* the handheld: 60 px corners on top, 95 px at the grips */}
    <path d="M150 195 H450 A60 60 0 0 1 510 255 V310 A95 95 0 0 1 415 405 H185 A95 95 0 0 1 90 310 V255 A60 60 0 0 1 150 195 Z" fill="#15191f" />
    <circle cx="147" cy="276" r="23" fill="#2a313c" />
    <circle cx="453" cy="330" r="23" fill="#2a313c" />
    <rect x="195" y="227" width="210" height="146" rx="14" fill="url(#clawdeck-logo-screen)" />
    <g clipPath="url(#clawdeck-logo-clip)">
      <rect x="247" y="241" width="24" height="124" rx="12" fill="url(#clawdeck-logo-mark)" transform="rotate(20 259 303)" />
      <rect x="291" y="247" width="24" height="124" rx="12" fill="url(#clawdeck-logo-mark)" transform="rotate(20 303 309)" />
      <rect x="335" y="241" width="24" height="124" rx="12" fill="url(#clawdeck-logo-mark)" transform="rotate(20 347 303)" />
    </g>
  </svg>
);
