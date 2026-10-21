import { DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { FC, useEffect, useState } from "react";

import { getController, getHardware, setController, HhdSetting, InputPlumberDevice, Stack } from "../api";
import { Advanced } from "../components/Advanced";
import { explain, Note, Problem, Status } from "../components/Hint";

const GROUPS: { id: string; title: string; does: string; why: string; cost: string }[] = [
  { id: "mode", title: "Controller mode", does: "What games see: a DualSense, an Xbox pad, or the raw controller.", why: "DualSense gives gyro and the touchpad to Steam Input.", cost: "some games show the wrong button icons." },
  { id: "gyro", title: "Gyro", does: "Motion aiming through Steam Input.", why: "precise aiming in shooters.", cost: "a little battery." },
  { id: "buttons", title: "Buttons", does: "Back buttons, MSI button roles and shortcut chords.", why: "map the back buttons in Steam Input per game.", cost: "none." },
  { id: "rgb", title: "Stick lighting", does: "RGB rings around the sticks.", why: "match your setup, or turn them off.", cost: "off saves a little battery." },
  { id: "other", title: "Other", does: "Other Handheld Daemon controller options.", why: "", cost: "" },
];

const visible = (s: HhdSetting, all: HhdSetting[]): boolean => {
  if (!s.mode_of) return true;
  const parent = all.find((x) => x.key === s.mode_of!.key);
  return parent ? parent.value === s.mode_of.mode && visible(parent, all) : false;
};

const Control: FC<{ s: HhdSetting; onSet: (key: string, v: any) => void }> = ({ s, onSet }) => {
  const label = s.unit ? `${s.title} (${s.unit})` : s.title;
  const description = s.hint || undefined;
  let el = null;
  if (s.type === "bool") el = <ToggleField label={label} description={description} checked={!!s.value} onChange={(v) => onSet(s.key, v)} />;
  else if (s.options) el = <DropdownItem label={label} description={description} rgOptions={s.options.map((o) => ({ data: o.value, label: o.label }))} selectedOption={s.value} onChange={(o) => onSet(s.key, o.data)} />;
  else if (s.type === "int" || s.type === "float") el = <SliderField label={label} description={description} value={Number(s.value ?? s.min ?? 0)} min={s.min ?? 0} max={s.max ?? 100} step={s.step ?? 1} showValue onChange={(v) => onSet(s.key, v)} />;
  if (!el) return null;
  return <PanelSectionRow>{el}</PanelSectionRow>;
};

export const ControllerTab: FC = () => {
  const [st, setSt] = useState<{ available: boolean; settings: HhdSetting[]; inputplumber?: InputPlumberDevice[] } | null>(null);
  const [stack, setStack] = useState<Stack | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => { setSt(await getController()); setStack((await getHardware()).stack); };
  useEffect(() => { load(); }, []);
  const onSet = async (key: string, v: any) => {
    const r = await setController(key, v);
    toaster.toast({ title: "ClawDeck", body: r.ok ? `${key.split(".").pop()} set → Handheld Daemon reads ${JSON.stringify(r.value)}` : `${key.split(".").pop()}: not set (${r.error ?? "Handheld Daemon did not accept that"})` });
    setErr(r.ok ? null : (r.error ?? "Handheld Daemon did not accept that"));
    load();
  };
  if (st && !st.available) {
    return (
      <PanelSection title="Controller">
        <PanelSectionRow>
          {stack?.inputplumber ? (
            <Note label="InputPlumber runs the controller">Bazzite's controller service handles the MSI buttons, gyro and back buttons. Set them per game in Steam → Controller settings.</Note>
          ) : (
            <Note label="No controller service is running">The MSI buttons and gyro have no driver. Desktop Mode → Konsole → ujust update → restart, then Setup → Re-check.</Note>
          )}
        </PanelSectionRow>
        {(st.inputplumber ?? []).map((d) => (
          <PanelSectionRow key={d.path}>
            <Status ok={true} label={d.name ?? "Controller"} detail={d.profile ? `InputPlumber profile: ${d.profile}` : "InputPlumber"} />
          </PanelSectionRow>
        ))}
      </PanelSection>
    );
  }
  const all = st?.settings ?? [];
  const shown = all.filter((s) => visible(s, all));
  const basic = GROUPS.map((g) => {
    const items = shown.filter((s) => s.group === g.id && !s.advanced);
    if (!items.length) return null;
    return (
      <PanelSection key={g.id} title={g.title}>
        {g.why && <PanelSectionRow><Note>{explain(g.does, g.why, g.cost)}</Note></PanelSectionRow>}
        {items.map((s) => <Control key={s.key} s={s} onSet={onSet} />)}
      </PanelSection>
    );
  });
  const advanced = shown.filter((s) => s.advanced);
  return (
    <>
      {err && <PanelSection><PanelSectionRow><Problem error={err} /></PanelSectionRow></PanelSection>}
      {basic}
      {advanced.length > 0 && (
        <PanelSection title="More">
          <Advanced id="controller">{advanced.map((s) => <Control key={s.key} s={s} onSet={onSet} />)}</Advanced>
        </PanelSection>
      )}
      <PanelSection>
        <PanelSectionRow>
          <Note>Every option here comes from Handheld Daemon and is saved by it. Its own overlay has the full list.</Note>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
