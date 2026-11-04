// node test for src/vrr.ts (run: pnpm test:ts). Byte layouts from SteamDatabase/Protobufs webui/common.proto.
import { strict as assert } from "node:assert";
import { decode, encodeVrrUpdate, readState } from "../../src/vrr";

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
console.log("vrr.ts: ok");
