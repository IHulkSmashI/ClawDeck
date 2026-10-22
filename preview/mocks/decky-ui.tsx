// Stand-in for @decky/ui: the same component names and props the tabs use, drawn to look like Steam's Quick Access
// menu. Only what ClawDeck's tabs touch is implemented.
import { FC, ReactNode, useEffect, useState } from "react";
import { GAMES, scenario } from "../backend";

export const staticClasses = { Title: "qam-title" };

// description: the explanation line Steam draws under a control's label, inside the same focusable row
const Desc: FC<{ d?: ReactNode }> = ({ d }) => (d ? <div className="qam-desc">{d}</div> : null);

export const Field: FC<{ label?: ReactNode; description?: ReactNode; focusable?: boolean; bottomSeparator?: string; children?: ReactNode }> =
  ({ label, description, focusable, children }) => (
    <div className="qam-field qam-info" tabIndex={focusable ? 0 : undefined}>
      {label && <span className="qam-label">{label}</span>}
      <Desc d={description} />
      {children}
    </div>
  );

let navigate: ((path: string) => void) | null = null;
export const Navigation = {
  Navigate: (path: string) => navigate?.(path),
  CloseSideMenus: () => undefined,
};
export const setNavigator = (fn: (path: string) => void) => { navigate = fn; };

export const SidebarNavigation: FC<{ title?: ReactNode; showTitle?: boolean; page?: string;
  pages: { title: ReactNode; content: ReactNode; icon?: ReactNode; route?: string }[] }> = ({ title, pages, page }) => {
  const [cur, setCur] = useState(() => Math.max(0, pages.findIndex((p) => p.route === page)));
  useEffect(() => { const i = pages.findIndex((p) => p.route === page); if (i >= 0) setCur(i); }, [page]);
  return (
    <div className="sb">
      <nav className="sb-nav" aria-label="ClawDeck sections">
        {title && <div className="sb-title">{title}</div>}
        {pages.map((p, i) => (
          <button key={i} type="button" className="sb-item" aria-current={i === cur ? "page" : undefined} onClick={() => setCur(i)}>
            <span className="sb-icon" aria-hidden="true">{p.icon}</span>{p.title}
          </button>
        ))}
      </nav>
      <div className="sb-content">{pages[cur]?.content}</div>
    </div>
  );
};

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

export const ToggleField: FC<{ label: ReactNode; description?: ReactNode; checked: boolean; disabled?: boolean; onChange?: (v: boolean) => void }> =
  ({ label, description, checked, disabled, onChange }) => {
    const id = useId();
    return (
      <div className={`qam-field${disabled ? " is-disabled" : ""}`}>
        <label className="qam-toggle" htmlFor={id}>
          <span className="qam-label">{label}</span>
          <input id={id} type="checkbox" role="switch" checked={!!checked} disabled={disabled} onChange={(e) => onChange?.(e.target.checked)} />
          <span className="qam-switch" aria-hidden="true" />
        </label>
        <Desc d={description} />
      </div>
    );
  };

export const SliderField: FC<{ label: ReactNode; description?: ReactNode; value: number; min: number; max: number; step?: number; showValue?: boolean; disabled?: boolean; onChange?: (v: number) => void }> =
  ({ label, description, value, min, max, step = 1, showValue, disabled, onChange }) => {
    const id = useId();
    const [v, setV] = useState(value);
    useEffect(() => setV(value), [value]);
    return (
      <div className={`qam-field qam-slider${disabled ? " is-disabled" : ""}`}>
        <label className="qam-label" htmlFor={id}>{label}{showValue && <output>{Number.isInteger(step) ? v : v.toFixed(2)}</output>}</label>
        <input id={id} type="range" min={min} max={max} step={step} value={v} disabled={disabled}
          onChange={(e) => setV(Number(e.target.value))}
          onPointerUp={() => onChange?.(v)} onKeyUp={() => onChange?.(v)} />
        <Desc d={description} />
      </div>
    );
  };

export const DropdownItem: FC<{ label: ReactNode; description?: ReactNode; rgOptions: { data: any; label: ReactNode }[]; selectedOption: any; disabled?: boolean; onChange?: (o: { data: any }) => void }> =
  ({ label, description, rgOptions, selectedOption, disabled, onChange }) => {
    const id = useId();
    const idx = Math.max(0, rgOptions.findIndex((o) => o.data === selectedOption));
    return (
      <div className={`qam-field qam-dropdown${disabled ? " is-disabled" : ""}`}>
        <label className="qam-label" htmlFor={id}>{label}</label>
        <select id={id} value={idx} disabled={disabled} onChange={(e) => onChange?.(rgOptions[Number(e.target.value)])}>
          {rgOptions.map((o, i) => <option key={i} value={i}>{o.label as any}</option>)}
        </select>
        <Desc d={description} />
      </div>
    );
  };

export const ButtonItem: FC<{ layout?: string; description?: ReactNode; disabled?: boolean; onClick?: () => void; children?: ReactNode }> =
  ({ description, disabled, onClick, children }) => (
    <div className="qam-field">
      <button type="button" className="qam-button" disabled={disabled} onClick={onClick}>{children}</button>
      <Desc d={description} />
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
