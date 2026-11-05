// Example backend for the browser preview: what main.py would answer on a Claw 8 EX running current Bazzite.
// Every number here is an example, not a measurement. The scenario switches (game running, charger, controller
// service) are set from the preview page.

export const GAMES = {
  dd2: { appid: 2054970, name: "Dragon's Dogma 2" },
  hk: { appid: 367520, name: "Hollow Knight" },
};

export const scenario = {
  game: true,
  which: "dd2" as keyof typeof GAMES,
  plugged: false,
  controller: "inputplumber" as "inputplumber" | "hhd",
  listeners: [] as (() => void)[],
  set(k: "game" | "plugged" | "controller" | "which", v: any) { (this as any)[k] = v; this.listeners.forEach((l) => l()); },
};

const hhd = () => scenario.controller === "hhd";
const state = {
  gpuSaving: false,
  wideVrr: false,
  buttonsFixed: true,
  autotune: true,
  profile: "balanced",
  tdp: 17,
  cpu: 85,
  boost: true,
  gpuMin: 300,
  gpuMax: 2300,
  epp: "balance_power",
  floor: null as null | number,
  customTdp: null as null | number,
  keep: 5,
  launch: { xmx: true, opti: "fsr4_xmx", fg: 2, opti_fg: "dlssg", opti_overlay: 2 } as any,
  framegen: true,
  xessInstalled: !(typeof location !== "undefined" && location.hash.includes("firstrun")),
  setupDone: !(typeof location !== "undefined" && location.hash.includes("firstrun")),   // #firstrun: a fresh install
  fan: "auto",
  charge: null as null | number,
};

const ok = (extra: object = {}) => ({ ok: true, ...extra });
const PROFILE_TABLE: Record<string, [number, number]> = { saver: [10, 60], battery: [12, 70], balanced: [17, 85], performance: [25, 100], plugged: [30, 100], max: [35, 100] };
const done = (what: string, wanted: any, read: any, unit = "") => {
  const f = (v: any) => (typeof v === "boolean" ? (v ? "on" : "off") : `${v}${unit ? " " + unit : ""}`);
  return { ok: true, what, wanted, read, unit, summary: `${what} set to ${f(wanted)} → hardware reads ${f(read)}`, error: null };
};
// what autotune uses under the limit in this example: 2 W less, never below its floor
const atW = () => Math.max(state.floor ?? 8, state.tdp - 2);
const thermal = () => ({ sensor: "x86_pkg_temp", temp_c: scenario.game ? 78 : 52, tripped: false, stepped_w: null, line: null, stepped_s: null });
const limit = (name: string, w: number) => {
  state.tdp = w;
  const running = scenario.game && state.autotune;
  const used = running ? atW() : w;
  const read = { tdp_w: used, pl2_w: used + 7, cpu_max_pct: state.cpu, epp: state.epp, backend: hhd() ? "hhd" : "rapl" };
  return { ok: true, profile: name, applied: { tdp: w }, failed: [], read, limit_w: w, autotune_w: running ? used : null,
           summary: `${name} ${w} W → ${hhd() ? "Handheld Daemon" : "RAPL"} reads ${used} W${hhd() ? "" : ` / ${used + 7} W`}${running && used !== w ? ` (Autotune uses ${used} W of it)` : ""}`, error: null };
};

const xessState = () => (state.xessInstalled
  ? { installed: true, package_dir: "/home/bazzite/.local/share/clawdeck/xess-xmx-linux-20261104", version: "20261104",
      source: { repo: "HHHHanasak1/xess-xmx-linux", tag: "20261104" }, drivers: ["26.1.2", "26.3.0-rc3"], default_driver: "26.3.0-rc3",
      status: { state: "ok", line: "ok 2026-11-04 18:02:11 /x Mesa 26.3.0-rc3", mode: "XMX (patched driver)" }, low_latency: true, official_proton: [],
      scope: { session_wide: false, per_game: true, auto: true, protons: ["/x/Proton 11.0"], scoped: ["/x/Proton 11.0"], unscoped: [] } }
  : { installed: false, package_dir: null, version: null, source: null, drivers: [], default_driver: null, status: { state: "unknown", line: null }, low_latency: false, official_proton: [] });

const framegenState = () => (state.framegen
  ? { installed: true, dir: "/home/bazzite/homebrew/plugins/Decky-Framegen", version: "0.17", fgmod_ready: true, fgmod: "/home/bazzite/fgmod/fgmod", optiscaler: "0.9.4-final.20260718" }
  : { installed: false, dir: null, version: null, fgmod_ready: false, fgmod: null, optiscaler: null });

// what the launch wrapper would export for the example game (mirrors launchenv.compose)
const envOf = (l: any) => {
  const e: Record<string, string> = {};
  if (l.xmx === false) e.VK_DRIVER_FILES = "/usr/share/vulkan/icd.d/intel_icd.x86_64.json";
  if (l.fg) e.XESS_FG = String(l.fg);
  if (l.fsr === "xess") e.FSR = "xess";
  if (l.fsr === "fg") e.FSR_FG = "xess";
  if (l.fsr === "inject") { e.FSR = "xess"; e.FSR_FG = "inject"; }
  if (l.xess_latest) e.XESS = "latest";
  if (l.low_latency === false) e.IGDEXT_XELL_CAP = "off";
  if (l.opti === "xess") e.Dx12Upscaler = "xess";
  if (l.opti === "fsr4") { e.Dx12Upscaler = "fsr31"; e.FSR_Fsr4ForceEnableInt8 = "true"; }
  if (l.opti_xefg) { e.FrameGen_Enabled = "true"; e.FrameGen_FGOutput = "xefg"; }
  return e;
};

const health = () => [
  { id: "model", title: "Claw model recognised", ok: true, detail: "MSI Claw 8 EX AI+ (MS-1T91), Panther Lake", fix: null },
  { id: "mesa", title: "Intel Vulkan driver", ok: true, detail: "Mesa 26.2.1, kernel 6.19.4, xe", fix: null },
  hhd()
    ? { id: "controller", title: "Controller service: Handheld Daemon", ok: true, detail: "version 4.2.1; MSI buttons, TDP, fans and RGB come from here", fix: null }
    : { id: "controller", title: "Controller service: InputPlumber (Bazzite default)", ok: true, detail: "MSI buttons and gyro come from InputPlumber", fix: null },
  { id: "face_buttons", title: "MSI face buttons reach Steam", ok: state.buttonsFixed, detail: state.buttonsFixed ? "ClawDeck's InputPlumber override is in place: left = Steam menu, right = Quick Access" : "both buttons are dropped in Game Mode", fix: state.buttonsFixed ? null : "fix_face_buttons" },
  { id: "tdp_owner", title: "Power limit control", ok: true, detail: "RAPL package limits (what the SoC enforces); PowerStation running", fix: null },
  { id: "gpu_clocks", title: "GPU clock control (autotune's GPU lever)", ok: true, detail: "floor and ceiling can be set", fix: null },
  { id: "cpu_pct", title: "CPU ceiling control (autotune's CPU lever)", ok: true, detail: "available", fix: null },
  state.xessInstalled
    ? { id: "xess", title: "XeSS XMX driver (xess-xmx-linux)", ok: true, detail: "20261018: ok 2026-10-21 Mesa 26.3.0-rc1", fix: null }
    : { id: "xess", title: "XeSS XMX driver (xess-xmx-linux)", ok: false, detail: "not installed", fix: "install_xess" },
  { id: "mangohud", title: "MangoHud (needed by autotune for frame times)", ok: true, detail: "installed", fix: null },
];
const healthSummary = () => {
  const h = health();
  const problems = h.filter((c) => !c.ok && !c.optional);
  return { ok: !problems.length, total: h.length, problems: problems.length, line: problems.length ? `${problems.length} to fix: ${problems.map((c) => c.title).join(" · ")}` : "All checks pass" };
};

export const backend = {
  // ---- setup / health ----
  get_hardware: () => ({
    product: "MS-1T91", known: true, kernel: "6.19.4-ba01.fc44.x86_64", cpu_model: "Intel Core Ultra X7 358H",
    gpu_driver: "xe", model: { name: "MSI Claw 8 EX AI+", soc: "Panther Lake", gpu: "Arc B390 (Xe3)", xmx: true },
    stack: { hhd: hhd(), inputplumber: !hhd(), powerstation: !hhd(), controller: scenario.controller, tdp_owner: hhd() ? "hhd" : "steam", conflict: false },
  }),
  get_health: () => health(),
  collect_logs: () => "/home/bazzite/Desktop/clawdeck-report-20261021-181502.json",
  run_fix: (fix: string, allow: boolean) => {
    if (fix === "install_xess" && !allow) return { ok: false, error: "xess-xmx-linux has no tested version pinned yet; allow 'latest (untested)' to continue" };
    if (fix === "install_xess") { state.xessInstalled = true; return ok({ next: "log out of Game Mode and back in (or reboot) so the session picks up the driver" }); }
    if (fix === "fix_face_buttons") { state.buttonsFixed = true; return ok({ next: "press the buttons in Game Mode: left = Steam menu, right = Quick Access" }); }
    if (fix === "overlay_power_on" || fix === "overlay_power_off") { (state as any).overlayPower = fix.endsWith("_on"); return ok(); }
    if (fix === "undo_face_buttons") { state.buttonsFixed = false; return ok({ removed: ["/etc/inputplumber/devices.d/50-msi_claw8_ex_ai_cg3em.yaml"] }); }
    return ok();
  },
  get_fixes: () => ({
    vrr: { panel: "CSW0801", panel_name: "PN8007QB1-2", range: state.wideVrr ? [30, 120] : [48, 120], wide: state.wideVrr, supported: true, reason: null, pending: null, rpm_ostree: true },
    buttons: { applies: true, ok: state.buttonsFixed, installed: state.buttonsFixed, stock_map: "claw2", upstream_fixed: false, legacy_hwdb: [] },
    health: healthSummary(),
    overlay_power: { available: true, on: !!(state as any).overlayPower, files: [], battery_w: -9.9 },
  }),
  get_setting: (k: string) => (k === "setup_done" ? state.setupDone : k === "autotune" ? state.autotune : k === "last_profile" ? state.profile : k === "autotune_plugged" ? !!(state as any).autotune_plugged : null),
  set_setting: (k: string, v: any) => {
    if (k === "autotune") state.autotune = v;
    if (k === "setup_done") state.setupDone = v;
    if (k === "autotune_plugged") (state as any).autotune_plugged = v;
    return true;
  },
  self_test: () => (scenario.game
    ? { ok: false, error: "close the game first: the self-test changes and reverts the power limits" }
    : { ok: true, passed: 12, failed: 0, summary: "12 passed, 0 failed", error: null,
        lines: ["PASS  TDP write (RAPL PL1)  asked 12 W, sysfs reads 12 W", "PASS  TDP effect (power under load)  measured 15.1 W, limit 18 W",
                "PASS  TDP revert  back to 25/37 W", "PASS  CPU ceiling write  asked 80 %, reads 80 %", "PASS  GPU ceiling write  asked 2100 MHz, reads 2100 MHz",
                "PASS  Face buttons mapped  InputPlumber override installed", "PASS  Nothing left changed  all values reverted"] }),

  // ---- power ----
  get_power: () => ({
    gpu: { available: true, min_mhz: state.gpuMin, max_mhz: state.gpuMax, cur_mhz: scenario.game ? 1950 : 450, hw_min_mhz: 100, hw_max_mhz: 2300, efficient_mhz: 1000, power_profile: state.gpuSaving ? "power_saving" : "base", power_profiles: ["base", "power_saving"] },
    cpu: { available: true, max_perf_pct: state.cpu, min_perf_pct: 10, no_turbo: state.boost ? 0 : 1, epp: state.epp, epp_available: true },
    rapl: { available: true, pl1_w: scenario.game && state.autotune ? atW() : state.tdp, pl2_w: (scenario.game && state.autotune ? atW() : state.tdp) + 7, max_w: 25 },
    tdp: hhd() ? { mode: "custom", custom_tdp: state.tdp, boost: true, ready: true } : null,
    hhd: hhd(), msi_firmware: true, tdp_backend: hhd() ? "hhd" : "rapl", tdp_w: scenario.game && state.autotune ? atW() : state.tdp,
    firmware_tdp: { pl1_w: 0, pl2_w: 0 },
    limits: { min: 8, battery_max: 35, plugged_max: 35, sppt_max: 45 }, range: { min: 8, max: 35 },
    custom_tdp: state.customTdp, autotune_floor: state.floor, autotune_keep: state.keep, thermal: thermal(),
  }),
  apply_profile: (name: string) => {
    const p = PROFILE_TABLE[name];
    state.profile = name; state.cpu = p[1]; state.gpuSaving = name === "saver" || name === "battery";
    return limit(name[0].toUpperCase() + name.slice(1), p[0]);
  },
  set_tdp: (w: number) => { state.profile = "custom"; state.customTdp = w; return { ...limit("Custom", w), profile: "custom" }; },
  power_slot: () => ({ appid: scenario.game ? "2054970" : null, plugged: false, base: null, tdp: null, running: !!scenario.game,
    pick: { profile: state.profile, tdp: state.profile === "custom" ? state.customTdp : PROFILE_TABLE[state.profile]?.[0] ?? null, from: "last" } }),
  set_cpu_turbo: (v: boolean) => { state.boost = v; return done("CPU boost", v, v); },
  set_cpu_max_pct: (v: number) => { state.cpu = v; return done("CPU ceiling", v, v, "%"); },
  set_gpu_window: (lo: number | null, hi: number | null) => {
    if (lo != null) state.gpuMin = Math.min(lo, state.gpuMax); if (hi != null) state.gpuMax = Math.max(hi, state.gpuMin);
    return lo != null ? done("GPU floor", lo, state.gpuMin, "MHz") : done("GPU ceiling", hi, state.gpuMax, "MHz");
  },
  set_epp: (v: string) => { state.epp = v; return done("CPU energy mode", v, v); },
  set_gpu_power_profile: (v: boolean) => { state.gpuSaving = v; const p = v ? "power_saving" : "base"; return done("GPU efficiency mode", p, p); },
  set_autotune_floor: (w: number | null) => { state.floor = w; return { ok: true, floor_w: w ?? 8, summary: `Autotune floor ${w ?? 8} W`, error: null }; },
  list_profiles: () => Object.fromEntries(Object.entries(PROFILE_TABLE).map(([k, v]) => [k, { tdp: v[0], cpu_max_pct: v[1] }])),
  set_autotune_keep: (v: number) => { state.keep = v; return { ok: true, keep_pct: v, summary: `Autotune keeps within ${v} % of the most the TDP gives`, error: null }; },
  get_autotune: () => (scenario.game && state.autotune
    ? { running: true, enabled: true, appid: "2054970", paused: false, phase: "hold", tdp: atW(), ceiling_tdp: state.tdp, floor_tdp: state.floor ?? 8,
        keep_pct: state.keep, learned: { tdp: atW(), cpu_max_pct: state.cpu, gpu_min_mhz: null, boost: true, bound: "cpu", max_fps: 31.0 },
        bound: "cpu", max_fps: 31.0, avg_fps: 30.2, low_fps: 28.4, pct_of_max: 97, fps_per_w: +(30.2 / (atW() + 0.4)).toFixed(2),
        source: "rendered", multiplier: 2, window_s: 10,
        last: { fps: 30.2, shown_fps: 60.4, pkg_w: atW() + 0.4 },
        last_action: "raised CPU ceiling to 100 %: 29 → 31 fps, kept", last_delta: "CPU ceiling 100 %", last_change_s: 12,
        thermal_cap: null, thermal: thermal() }
    : { running: false, enabled: state.autotune, thermal: thermal() }),
  // ---- fan and battery (fan.py) ----
  get_fan: () => ({ supported: true, preset: state.fan, custom: [0, 40, 49, 58, 67, 75],
    fans: [1, 2].map((fan) => ({ fan, rpm: state.fan === "quiet" ? 2630 : state.fan === "full" ? 5400 : 3428, enable: state.fan === "auto" ? 2 : 1,
      temps: [47, 50, 57, 64, 71, 78], duties: [153, 163, 173, 188, 204, 214] })) }),
  set_fan: (p: string) => { state.fan = p; return { ok: true, summary: `Fan: ${p}`, error: null }; },
  get_charge: () => ({ supported: true, limit: state.charge ?? 0, setting: state.charge }),
  set_charge_limit: (v: number | null) => { state.charge = v; return { ok: true, summary: v ? `Charge limit ${v} %` : "Charge limit off", error: null }; },
  ui_log: () => undefined,
  game_started: () => ({}), game_stopped: () => ({}), power_source_changed: () => ({}), resumed: () => ({}),

  // ---- per game ----
  get_launch: () => ({ launch: state.launch, env: envOf(state.launch), wrapper: "/home/bazzite/.local/share/clawdeck/clawdeck-launch.sh %command%" }),
  set_launch: (_a: string, v: any) => {
    for (const [k, val] of Object.entries(v)) { if (val === null) delete state.launch[k]; else state.launch[k] = val; }
    return { ok: true, launch: state.launch, env: envOf(state.launch), wrapper: "/home/bazzite/.local/share/clawdeck/clawdeck-launch.sh %command%" };
  },
  game_status: () => ({
    launch: state.launch, xess: xessState(), wrapper: "/home/bazzite/.local/share/clawdeck/clawdeck-launch.sh %command%",
    opti_owner: "clawdeck", opti_version: "0.9.4", xmx_proof: { state: "ok", at: 0, last_launch: 0, kernels: 14, failed: false }, files: { owner: "clawdeck", dll: "dxgi.dll", preset: "fsr4_xmx", folder: "/x", reframework: false, install_dir: "/x" }, fsr4_xmx: { dir: "/home/bazzite/fsr4-xmx", ready: true, missing: [] },
    xmx_scope: { session_wide: false, per_game: true, auto: true, protons: ["Proton 11.0"], scoped: ["Proton 11.0"], unscoped: [] },
    optiscaler: scenario.which === "dd2"
      ? { present: true, dir: "/home/bazzite/.local/share/Steam/steamapps/common/Dragons Dogma 2", version: "v0.9.4-final", xess_fg: true }
      : { present: false, dir: "/home/bazzite/.local/share/Steam/steamapps/common/Hollow Knight" },
    framegen: framegenState(),
    latency: state.launch.low_latency === false
      ? { mode: "off", detail: "Turned off for this game: frames may queue in front of the GPU (more input lag)" }
      : { mode: "pacing", detail: "Holding frames back so they don't queue: waited in 87 of the last 100 frames, 6.4 ms each" },
  }),

  // ---- setup: installs ----
  xess_state: () => xessState(),
  game_recommend: () => ({ frame_gen: "dlssg", fsr4: { dx12: true, files: true, missing: [], dir: "/home/bazzite/fsr4-xmx" }, route: "re_engine", known: true, name: "Onimusha: Way of the Sword", re_engine: true, optiscaler: true,
    ini: { Hotfix_RestoreComputeSignature: "true", Spoofing_Dxgi: "false" }, later: [], applied: false, ini_version: "0.9.4",
    reasons: ["Capcom RE Engine: OptiScaler (XeSS + XeSS frame generation) with REFramework, which keeps Capcom's anti-tamper from stopping the game. Pick DLSS in the game's menu.",
      "OptiScaler's own settings for this game: Hotfix_RestoreComputeSignature=true, Spoofing_Dxgi=false"], reframework_state: { present: false, ours: false } }),
  game_apply_recommended: () => ({ ok: true, next: "Pick DLSS in the game's menu.", env: { Dx12Upscaler: "xess" } }),
  game_undo_recommended: () => ({ ok: true }),
  game_set_opti: () => ({ ok: true }),
  game_choose: () => ({ ok: true, env: { CLAWDECK_XMX: "1" } }),
  games_list: () => [{ appid: "2358720", name: "Black Myth: Wukong", dir: "/x", setup: null, supported: true }, { appid: "3669870", name: "CONTROL Resonant", dir: "/x", setup: "launch", supported: true },
    { appid: "2638890", name: "Onimusha: Way of the Sword", dir: "/x", setup: "optiscaler", supported: true }],
  optiscaler_state: () => ({ version: "0.9.4", installed: true, games: 1, framegen: true, framegen_version: "v0.17" }),
  optiscaler_install: () => ({ ok: true, version: "0.9.4" }),
  xess_install: () => (state.xessInstalled
    ? ok({ up_to_date: true, repo: "HHHHanasak1/xess-xmx-linux", tag: "20261104", summary: "XeSS XMX 20261104 is up to date" })
    : (state.xessInstalled = true, ok({ repo: "HHHHanasak1/xess-xmx-linux", tag: "20261104", next: "log out of Game Mode and back in once" }))),
  framegen_state: () => framegenState(),

  // ---- fixes ----
  set_wide_vrr: (on: boolean) => { state.wideVrr = on; return { ok: true, restart: true }; },
  get_changes: () => ({ first_run: false, changed: [] }),
};
