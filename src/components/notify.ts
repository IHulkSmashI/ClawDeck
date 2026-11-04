import { toaster } from "@decky/api";

import { uiLog } from "../api";

/**
 * Steam's toast shows one short line and cuts the rest with "…" (maintainer, 2026-11-04: "I have no idea what it's
 * doing"). So a message goes out as a short title (what happened) and a short body (what to do next); the whole
 * message stays at the top of Quick Access (LastMessage) and in ClawDeck's log.
 */
const TITLE = 34, BODY = 48;
let last: { text: string; at: number } | null = null;
const subs = new Set<() => void>();

const cut = (s: string, n: number) => (s.length <= n ? s : s.slice(0, s.lastIndexOf(" ", n - 1) > n / 2 ? s.lastIndexOf(" ", n - 1) : n - 1) + "…");

/** Splits "Game: what happened. What next." into a title and a body that each fit on one toast line. */
export const split = (msg: string): { title: string; body: string } => {
  const m = msg.trim();
  const colon = m.indexOf(": ");
  if (colon > 0 && colon <= TITLE) return { title: m.slice(0, colon), body: cut(m.slice(colon + 2), BODY) };
  const dot = m.search(/[.;] /);
  if (dot > 0 && dot <= TITLE) return { title: m.slice(0, dot), body: cut(m.slice(dot + 2), BODY) };
  return m.length <= BODY ? { title: "ClawDeck", body: m } : { title: cut(m, TITLE), body: cut(m.slice(cut(m, TITLE).length - 1).trim(), BODY) };
};

/** A toast that fits, with the full text kept for the panel. `title`/`body` override the automatic split. */
export const notify = (msg: string, short?: { title?: string; body?: string }) => {
  const s = split(msg);
  toaster.toast({ title: short?.title ?? s.title, body: short?.body ?? s.body });
  last = { text: msg, at: Date.now() };
  subs.forEach((f) => f());
  uiLog(`toast: ${msg}`).catch(() => undefined);
};

export const lastMessage = () => last;
export const onMessage = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
