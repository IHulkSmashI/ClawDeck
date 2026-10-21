// Stand-in for @decky/ui: the same component names and props the tabs use, drawn to look like Steam's Quick Access
// menu. Only what ClawDeck's tabs touch is implemented.
import { FC, ReactNode, useEffect, useState } from "react";
import { GAMES, scenario } from "../backend";

export const staticClasses = { Title: "qam-title" };

export const Router = {
  get MainRunningApp() {
    const g = GAMES[scenario.which];
    return scenario.game ? { appid: g.appid, display_name: g.name } : undefined;
  },
};

export const PanelSection: FC<{ title?: string; children?: ReactNode }> = ({ title, children }) => (
  <section className="qam-section">
    {title && <h3 className="qam-section-title">{title}</h3>}
    {children}
  </section>
);

export const PanelSectionRow: FC<{ children?: ReactNode }> = ({ children }) => <div className="qam-row">{children}</div>;

let uid = 0;
const useId = () => useState(() => `c${++uid}`)[0];

export const ToggleField: FC<{ label: ReactNode; checked: boolean; disabled?: boolean; onChange?: (v: boolean) => void }> =
  ({ label, checked, disabled, onChange }) => {
    const id = useId();
    return (
      <label className={`qam-field qam-toggle${disabled ? " is-disabled" : ""}`} htmlFor={id}>
        <span className="qam-label">{label}</span>
        <input id={id} type="checkbox" role="switch" checked={!!checked} disabled={disabled} onChange={(e) => onChange?.(e.target.checked)} />
        <span className="qam-switch" aria-hidden="true" />
      </label>
    );
  };

export const SliderField: FC<{ label: ReactNode; value: number; min: number; max: number; step?: number; showValue?: boolean; disabled?: boolean; onChange?: (v: number) => void }> =
  ({ label, value, min, max, step = 1, showValue, disabled, onChange }) => {
    const id = useId();
    const [v, setV] = useState(value);
    useEffect(() => setV(value), [value]);
    return (
      <div className={`qam-field qam-slider${disabled ? " is-disabled" : ""}`}>
        <label className="qam-label" htmlFor={id}>{label}{showValue && <output>{Number.isInteger(step) ? v : v.toFixed(2)}</output>}</label>
        <input id={id} type="range" min={min} max={max} step={step} value={v} disabled={disabled}
          onChange={(e) => setV(Number(e.target.value))}
          onPointerUp={() => onChange?.(v)} onKeyUp={() => onChange?.(v)} />
      </div>
    );
  };

export const DropdownItem: FC<{ label: ReactNode; rgOptions: { data: any; label: ReactNode }[]; selectedOption: any; disabled?: boolean; onChange?: (o: { data: any }) => void }> =
  ({ label, rgOptions, selectedOption, disabled, onChange }) => {
    const id = useId();
    const idx = Math.max(0, rgOptions.findIndex((o) => o.data === selectedOption));
    return (
      <div className={`qam-field qam-dropdown${disabled ? " is-disabled" : ""}`}>
        <label className="qam-label" htmlFor={id}>{label}</label>
        <select id={id} value={idx} disabled={disabled} onChange={(e) => onChange?.(rgOptions[Number(e.target.value)])}>
          {rgOptions.map((o, i) => <option key={i} value={i}>{o.label as any}</option>)}
        </select>
      </div>
    );
  };

export const TextField: FC<{ label?: ReactNode; value?: string; onChange?: (e: any) => void; disabled?: boolean }> =
  ({ label, value, onChange, disabled }) => {
    const id = useId();
    return (
      <div className={`qam-field${disabled ? " is-disabled" : ""}`}>
        {label && <label className="qam-label" htmlFor={id}>{label}</label>}
        <input id={id} className="qam-text" type="text" value={value ?? ""} disabled={disabled} onChange={onChange} />
      </div>
    );
  };

export const ButtonItem: FC<{ layout?: string; disabled?: boolean; onClick?: () => void; children?: ReactNode }> =
  ({ disabled, onClick, children }) => <button type="button" className="qam-button" disabled={disabled} onClick={onClick}>{children}</button>;

export const Tabs: FC<{ activeTab: string; onShowTab: (id: string) => void; tabs: { id: string; title: string; content: ReactNode }[]; autoFocusContents?: boolean }> =
  ({ activeTab, onShowTab, tabs }) => (
    <div className="qam-tabs">
      <div className="qam-tabstrip" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={t.id === activeTab} className="qam-tab" onClick={() => onShowTab(t.id)}>{t.title}</button>
        ))}
      </div>
      <div className="qam-tabpanel" role="tabpanel">{tabs.find((t) => t.id === activeTab)?.content}</div>
    </div>
  );

// ---- modals ----------------------------------------------------------------------------------------------------------
let setModal: ((n: ReactNode) => void) | null = null;
export const ModalHost: FC = () => {
  const [node, set] = useState<ReactNode>(null);
  setModal = set;
  return node ? <div className="qam-modal-backdrop">{node}</div> : null;
};
export const showModal = (n: ReactNode) => { setModal?.(n); return { Close: () => setModal?.(null) }; };

export const ConfirmModal: FC<{ strTitle: string; strDescription: string; strOKButtonText?: string; onOK?: () => void }> =
  ({ strTitle, strDescription, strOKButtonText = "OK", onOK }) => (
    <div className="qam-modal" role="dialog" aria-modal="true" aria-label={strTitle}>
      <h3>{strTitle}</h3>
      <p>{strDescription}</p>
      <div className="qam-modal-actions">
        <button type="button" className="qam-button" onClick={() => setModal?.(null)}>Cancel</button>
        <button type="button" className="qam-button is-primary" onClick={() => { setModal?.(null); onOK?.(); }}>{strOKButtonText}</button>
      </div>
    </div>
  );
