// node test for src/vrr.ts (run: pnpm test:ts). Byte layouts from SteamDatabase/Protobufs webui/common.proto.
import { strict as assert } from "node:assert";
import { decode, encodeVrrUpdate, readState, startVrrScope } from "../../src/vrr";

const hex = (b: Uint8Array) => Array.from(b).map((x) => x.toString(16).padStart(2, "0")).join(" ");
// gameid 2638890 (varint aa 88 a1 01), settings_delta { per_app { is_vrr_enabled: true } }, skip_storage_update: true
assert.equal(hex(encodeVrrUpdate(2638890n, true)), "08 aa 88 a1 01 1a 05 12 03 a0 01 01 20 01");
assert.equal(hex(encodeVrrUpdate(0n, false)), "08 00 1a 05 12 03 a0 01 00 20 01");
const f = decode(encodeVrrUpdate(12345678901234n, true));
assert.equal(f.get(1)?.[0], 12345678901234n);                       // 64-bit game ids survive

const perApp = (body: number[]) => [0x12, body.length + 2, 0x12, body.length, ...body];   // settings { per_app { ... } }
const id = [0xaa, 0x88, 0xa1, 0x01];
// in a game, VRR on
assert.deepEqual(readState(new Uint8Array([...perApp([0x10, 40, 0xa0, 0x01, 0x01]), 0x18, ...id, 0x20, ...id])),
  { game: 2638890n, profile: 2638890n, vrr: true });
// in the menus, VRR off: Steam leaves a false bool out, which must read as off (or the scope would rewrite forever)
assert.deepEqual(readState(new Uint8Array(perApp([0x10, 40]))), { game: 0n, profile: 0n, vrr: false });
// an update without settings: unknown, no write
assert.deepEqual(readState(new Uint8Array([0x18, ...id])), { game: 2638890n, profile: 0n, vrr: null });

// ---- the scope against a fake Steam -----------------------------------------------------------------------------
const state = (game: number[], vrr: boolean) => new Uint8Array([...perApp(vrr ? [0x10, 40, 0xa0, 0x01, 0x01] : [0x10, 40]),
  ...(game.length ? [0x18, ...game, 0x20, ...game] : [])]);
const sent: string[] = [];
let cb: (b: Uint8Array) => void = () => {};
(globalThis as any).SteamClient = { System: { Perf: {
  UpdateSettings: (b: string) => { sent.push(hex(Uint8Array.from(atob(b), (c) => c.charCodeAt(0)))); },
  RegisterForStateChanges: (f: any) => { cb = f; return { unregister: () => { cb = () => {}; } }; },
} } };
const OFF0 = "08 00 1a 05 12 03 a0 01 00 20 01", ON0 = "08 00 1a 05 12 03 a0 01 01 20 01";
let aside = false, running: boolean | null = false;
const sc = startVrrScope(() => {}, () => aside, () => running)!;
cb(state([], true));                              // menus with VRR on: off, once
assert.deepEqual(sent, [OFF0]);
cb(state(id, true));                              // Steam's state names a game, but none runs: still the menus
assert.equal(sent.length, 1);                     // (profile 2638890 has VRR on: switched off once the second ends)
await new Promise((r) => setTimeout(r, 1100));
assert.equal(sent[1], "08 aa 88 a1 01 1a 05 12 03 a0 01 00 20 01");
running = true;                                   // a game on the default profile: the user's VRR back once
cb(state([], false));
await new Promise((r) => setTimeout(r, 1100));
assert.equal(sent[2], ON0);
const n = sent.length;
cb(state([], false));                             // the user turns VRR off in the game: left alone
cb(state([], true));
assert.equal(sent.length, n);
running = false; aside = true; sc.poke();         // a monitor: what is still kept goes back (the game profile)
assert.ok(sent.slice(n).includes("08 aa 88 a1 01 1a 05 12 03 a0 01 01 20 01"));
const m = sent.length;
cb(state([], true));                              // standing aside: no writes
assert.equal(sent.length, m);
sc.stop();
assert.equal(sent.length, m);
console.log("vrr.ts: ok");
