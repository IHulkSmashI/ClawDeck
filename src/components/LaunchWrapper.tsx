import { ButtonItem, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getLaunch } from "../api";
import { getLaunchOptions, hasWrapper, setLaunchOptions, withWrapper, withoutWrapper } from "../steam";
import { Hint, Status } from "./Hint";

/**
 * Autotune, the benchmark, the injector and per-game HDR only reach a game through ClawDeck's launch wrapper. This
 * row shows whether the running game has it and adds or removes it in Steam's own launch options with one tap.
 */
export const LaunchWrapper: FC<{ appid: string; name: string }> = ({ appid, name }) => {
  const [opts, setOpts] = useState<string | null>(null);
  const [wrapper, setWrapper] = useState("");
  const load = async () => { setWrapper((await getLaunch(appid)).wrapper.replace(/ %command%$/, "")); setOpts(await getLaunchOptions(appid)); };
  useEffect(() => { load(); }, [appid]);
  const on = hasWrapper(opts);
  const readable = opts !== null;
  return (
    <PanelSectionRow>
      <Status ok={readable ? on : null} label={on ? "ClawDeck is in this game's launch options" : "ClawDeck is not in this game's launch options yet"} />
      {readable && wrapper && (
        <ButtonItem layout="below" onClick={() => {
          const next = on ? withoutWrapper(opts!) : withWrapper(opts!, wrapper);
          const ok = setLaunchOptions(appid, next);
          toaster.toast({ title: "ClawDeck", body: ok ? (on ? `Removed from ${name}` : `Added to ${name}: restart the game to use it`) : "Steam refused; add it in the game's Properties → Launch options" });
          load();
        }}>{on ? "Remove from launch options" : "Add to launch options"}</ButtonItem>
      )}
      <Hint does="Needed once per game for Autotune, Benchmark, OptiScaler and HDR. Your own launch options are kept; works from the next launch." />
      {!readable && <div style={{ fontSize: 11, opacity: 0.7, wordBreak: "break-all" }}>Add by hand: Properties → Launch options → <b>{wrapper} %command%</b></div>}
    </PanelSectionRow>
  );
};
