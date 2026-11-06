import { ButtonItem, PanelSectionRow, SliderField } from "@decky/ui";
import { FC, useEffect, useRef, useState } from "react";

import { getBrightness, setBrightness, BrightnessState } from "../api";
import { explain, Group, Problem, Status, useExplanations } from "./Hint";
import { notify } from "./notify";

/** Screen brightness on an OLED where Steam's slider does nothing (ONEXPLAYER 3): Game Mode drives the panel in HDR,
 *  where it ignores the backlight, so ClawDeck sets gamescope's brightness for normal (SDR) content instead. Shows
 *  nothing on devices where Steam's own slider works. */
export const Brightness: FC = () => {
  useExplanations();
  const [b, setB] = useState<BrightnessState | null>(null);
  const [v, setV] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const load = () => getBrightness().then(setB).catch(() => undefined);
  useEffect(() => { load(); }, []);
  if (!b || (!b.supported && !b.error)) return null;

  const lo = b.min ?? 20, hi = b.max ?? 600;
  const now = v ?? b.nits ?? b.saved ?? 200;
  const write = (n: number | null) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {             // once the slider stops
      const r = await setBrightness(n);
      setErr(r.ok ? null : r.error);
      notify(r.summary);
      load();
    }, 400);
  };
  return (
    <PanelSectionRow>
      <Group title="Screen brightness" />
      {!b.supported ? (
        <Status ok={null} label="Brightness: open Game Mode first" detail={b.error} />
      ) : (
        <>
          <SliderField label="Brightness" value={now} min={lo} max={hi} step={10} showValue
            description={explain("Steam's brightness slider does nothing on this screen: Game Mode runs it in HDR, where the panel ignores its backlight. This sets how bright normal games and Steam look instead.",
              undefined, "back to Game Mode's own level when Game Mode restarts or ClawDeck is removed.")}
            onChange={(n: number) => { setV(n); write(n); }} />
          <ButtonItem layout="below" onClick={() => { setV(null); write(null); }}>Back to Game Mode's default</ButtonItem>
        </>
      )}
      <Problem error={err} what="Brightness" />
    </PanelSectionRow>
  );
};
