/**
 * VRR only while a game shows frames: the screen runs 30–120 Hz VRR in games (with the VRR fix) and a fixed 120 Hz
 * in Steam's menus, as Windows does. With VRR on in the menus the screen follows the menu's frame rate (30 fps over
 * a game's shader compile = 30 Hz), which made Steam's menus choppy and smeared on the Claw (2026-11-04).
 *
 * Steam keeps VRR in its own performance settings (CMsgSystemPerfSettingsPerApp.is_vrr_enabled = 20) and takes
 * changes through SteamClient.System.Perf.UpdateSettings(base64 CMsgSystemPerfUpdateSettings). Field numbers from
 * SteamDatabase/Protobufs (webui/common.proto). ClawDeck writes with skip_storage_update, so the saved profiles never
 * change: turning this off (Fixes) leaves Steam's own VRR switch in charge again. With an external display connected
 * (dock, USB-C or HDMI monitor) it stands aside entirely: that display's VRR stays whatever the user sets in Steam.
 */
declare const SteamClient: any;

// ---- the few protobuf bits these two messages need ------------------------------------------------------------------
const varint = (n: bigint): number[] => {
  const out: number[] = [];
  do { let b = Number(n & 0x7fn); n >>= 7n; if (n) b |= 0x80; out.push(b); } while (n);
  return out;
};
const key = (field: number, wire: number) => varint(BigInt((field << 3) | wire));
const bool = (field: number, v: boolean) => [...key(field, 0), v ? 1 : 0];
const msg = (field: number, body: number[]) => [...key(field, 2), ...varint(BigInt(body.length)), ...body];

/** CMsgSystemPerfUpdateSettings { gameid, settings_delta { per_app { is_vrr_enabled } }, skip_storage_update } */
export const encodeVrrUpdate = (gameid: bigint, on: boolean): Uint8Array => new Uint8Array([
  ...key(1, 0), ...varint(gameid),
  ...msg(3, msg(2, bool(20, on))),
  ...bool(4, true),
]);

type Fields = Map<number, (bigint | Uint8Array)[]>;
const readVarint = (b: Uint8Array, i: number): [bigint, number] => {
  let r = 0n, s = 0n;
  for (;;) { const x = b[i++]; r |= BigInt(x & 0x7f) << s; s += 7n; if (!(x & 0x80) || i >= b.length) return [r, i]; }
};
export const decode = (b: Uint8Array): Fields => {
  const f: Fields = new Map();
  let i = 0;
  while (i < b.length) {
    const [k, j] = readVarint(b, i); i = j;
    const field = Number(k >> 3n), wire = Number(k & 7n);
    let v: bigint | Uint8Array;
    if (wire === 0) { const [x, n] = readVarint(b, i); v = x; i = n; }
    else if (wire === 2) { const [len, n] = readVarint(b, i); v = b.subarray(n, n + Number(len)); i = n + Number(len); }
    else if (wire === 5) { v = b.subarray(i, i + 4); i += 4; }
    else if (wire === 1) { v = b.subarray(i, i + 8); i += 8; }
    else break;                                   // groups: not in these messages
    f.set(field, [...(f.get(field) ?? []), v]);
  }
  return f;
};
const first = (f: Fields, n: number) => f.get(n)?.[0];

/** What ClawDeck needs from CMsgSystemPerfState: the running game, the profile in use, and VRR in it. */
export const readState = (b: Uint8Array): { game: bigint; profile: bigint; vrr: boolean | null } => {
  const f = decode(b);
  const game = (first(f, 3) as bigint | undefined) ?? 0n;
  const profile = (first(f, 4) as bigint | undefined) ?? 0n;
  const settings = first(f, 2);
  let vrr: boolean | null = null;
  if (settings instanceof Uint8Array) {
    const perApp = first(decode(settings), 2);
    if (perApp instanceof Uint8Array) {
      const v = first(decode(perApp), 20);
      vrr = typeof v === "bigint" ? v !== 0n : false;   // a false bool isn't sent: absent = off
    }
  }
  return { game, profile, vrr };
};

const toBuf = (x: any): Uint8Array | null =>
  x instanceof Uint8Array ? x : x instanceof ArrayBuffer ? new Uint8Array(x)
    : typeof x === "string" ? Uint8Array.from(atob(x), (c) => c.charCodeAt(0)) : null;
const b64 = (b: Uint8Array) => btoa(String.fromCharCode(...b));

export type VrrScope = {
  stop: () => void;
  poke: () => void;
  status: () => { active: boolean; game: string; vrr: boolean | null; writes: number; error: string | null };
};

/**
 * Keep VRR on while a game runs and off otherwise. Reacts to Steam's own state changes, so a profile Steam applies
 * at game start is corrected too; writes only when VRR differs from what it should be, at most once a second (a
 * change inside that second is retried when it ends). The value Steam had for each profile before ClawDeck's first
 * write is kept and put back when this stops or stands aside (an external display), so the user's setting returns.
 * If Steam doesn't take a write (three in a row for the same state), ClawDeck stops writing and says so.
 */
export const startVrrScope = (log: (m: string) => void = () => {}, standAside: () => boolean = () => false): VrrScope | null => {
  const perf = SteamClient?.System?.Perf;
  if (!perf?.UpdateSettings || !perf?.RegisterForStateChanges) { log("Steam has no Perf.UpdateSettings here"); return null; }
  let last = 0, writes = 0, error: string | null = null, stopped = false, aside = false, refused = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let seen: { game: bigint; profile: bigint; vrr: boolean | null } = { game: 0n, profile: 0n, vrr: null };
  const orig = new Map<string, boolean>();       // profile -> VRR before ClawDeck's first write
  let lastWrite: { key: string; want: boolean } | null = null;

  const write = (profile: bigint, on: boolean) => {
    perf.UpdateSettings(b64(encodeVrrUpdate(profile, on)));
    writes++;
  };
  const restore = (why: string) => {
    for (const [p, v] of orig) {
      try { write(BigInt(p), v); } catch (e) { error = `could not put VRR back: ${e}`; }
    }
    if (orig.size) log(`VRR: your setting back (${why})`);
    orig.clear();
  };
  const check = () => {
    if (stopped) return;
    if (standAside()) { if (!aside) { aside = true; restore("external display"); } return; }
    aside = false;
    const want = seen.game !== 0n;              // a game is running: VRR on; Steam's menus: off
    if (seen.vrr === null || seen.vrr === want) { refused = 0; return; }   // null: no settings in this update
    const wait = 1000 - (Date.now() - last);
    if (wait > 0) {                             // inside the 1 s window: try again when it ends
      if (!timer) timer = setTimeout(() => { timer = null; check(); }, wait);
      return;
    }
    const key = `${seen.game}/${seen.profile}`;
    refused = lastWrite && lastWrite.key === key && lastWrite.want === want ? refused + 1 : 0;
    if (refused >= 3) { error = "Steam doesn't take the VRR change; Steam's own switch is in charge"; return; }
    last = Date.now();
    const pk = String(seen.profile);
    if (!orig.has(pk)) orig.set(pk, seen.vrr);
    try {
      write(seen.profile, want);
      lastWrite = { key, want };
      error = null;
      log(`VRR ${want ? "on (game " + seen.game + ")" : "off (Steam's menus)"}`);
    } catch (e) { error = `Steam refused the VRR change: ${e}`; }
  };
  const reg = perf.RegisterForStateChanges((raw: any) => {
    const b = toBuf(raw);
    if (!b) return;
    try { seen = readState(b); } catch (e) { error = `could not read Steam's state: ${e}`; return; }
    check();
  });
  return {
    stop: () => {
      if (stopped) return;
      stopped = true;
      if (timer) clearTimeout(timer);
      try { reg?.unregister?.(); } catch { /* gone */ }
      if (!aside) restore("switched off");
    },
    poke: check,
    status: () => ({ active: !standAside(), game: String(seen.game), vrr: seen.vrr, writes, error }),
  };
};
