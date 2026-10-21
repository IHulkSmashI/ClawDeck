// Stand-in for @decky/api: callable() goes to the example backend instead of the plugin's Python process.
import { backend } from "../backend";

export const callable = <A extends any[], R>(name: string) => async (...args: A): Promise<R> => {
  const fn = (backend as any)[name];
  await new Promise((r) => setTimeout(r, 60));
  if (!fn) { console.warn("preview: no example for", name); return undefined as any; }
  return fn(...args);
};

type Toast = { title: string; body: string };
const listeners: ((t: Toast) => void)[] = [];
export const onToast = (fn: (t: Toast) => void) => { listeners.push(fn); };
export const toaster = { toast: (t: Toast) => listeners.forEach((l) => l(t)) };
export const definePlugin = (fn: any) => fn;
