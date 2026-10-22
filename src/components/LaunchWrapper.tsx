import { ButtonItem, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getLaunch } from "../api";
import { getLaunchOptions, hasWrapper, setLaunchOptions, withWrapper, withoutWrapper } from "../steam";
import { explain, Note, Status } from "./Hint";

/**
 * Autotune, XeSS XMX and XeLL only reach a game through ClawDeck's launch wrapper. This
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
        <ButtonItem layout="below" description={explain("Needed once per game: Autotune, XeSS XMX on/off and XeLL reach the game through it. Your own launch options are kept; works from the next launch.")} onClick={() => {
          const next = on ? withoutWrapper(opts!) : withWrapper(opts!, wrapper);
          const ok = setLaunchOptions(appid, next);
          toaster.toast({ title: "ClawDeck", body: ok ? (on ? `Removed from ${name}` : `Added to ${name}: restart the game to use it`) : "Steam refused; add it in the game's Properties → Launch options" });
          load();
        }}>{on ? "Remove from launch options" : "Add to launch options"}</ButtonItem>
      )}
      {!readable && <Note label="Add by hand: Properties → Launch options"><span style={{ wordBreak: "break-all" }}>{wrapper} %command%</span></Note>}
    </PanelSectionRow>
  );
};
