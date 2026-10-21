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

// the shape every write answers with (src/api.ts WriteResult): asked, read back, summary, error
const done = (what: string, wanted: any, read: any, unit = "") => {
  const f = (v: any) => (typeof v === "boolean" ? (v ? "on" : "off") : `${v}${unit ? " " + unit : ""}`);
  return { ok: true, what, wanted, read, unit, summary: `${what} set to ${f(wanted)} → hardware reads ${f(read)}`, error: null };
};
const hhd = () => scenario.controller === "hhd";
const state = {
  wideVrr: false,
  dry: false,
  autotune: true,
  tdp: 12,
  cpu: 80,
  gpuMin: 300,
  gpuMax: 2500,
  turbo: true,
  chargeLimit: 80,
  fan: "auto",
  display: { vrr: true, vrr_min: 40, vrr_max: 120, hdr: false, hdr_nits: 400 },
  displaySaved: false,
  targetFps: 60,
  launch: { inject: "none", lsfg: { multiplier: 2, flow_scale: 0.8, performance_mode: true }, hdr: false, mesa: "", proton: "GE-Proton11-6" } as any,
  xessInstalled: false,
  injected: {} as Record<string, string>,
  lsfgInstalled: false,
  autoUpdate: true,
  epp: "balance_power",
  boost: true,
  moonlight: null as null | string,
  streamHost: null as null | string,
  streamApp: "Desktop",
  shortcuts: { moonlight: null as null | number, host: null as null | number },
  benchRunning: false,
  benchStart: 0,
  results: [
    { appid: "2054970", name: "Dragon's Dogma 2", label: "battery", time: "2026-10-20 21:14", avg_fps: 41.8, low1_fps: 33.0, avg_w: 19.6, fps_per_w: 2.13, duration_s: 120, samples: 120 },
    { appid: "2054970", name: "Dragon's Dogma 2", label: "dock", time: "2026-10-20 21:40", avg_fps: 58.3, low1_fps: 46.5, avg_w: 34.1, fps_per_w: 1.71, duration_s: 120, samples: 120 },
  ],
  controller: {
    "controllers.claw.controller_mode.mode": "dualsense",
    "controllers.claw.controller_mode.dualsense.gyro": true,
    "controllers.claw.controller_mode.dualsense.gyro_scaling": 40,
    "controllers.claw.swap_guide": false,
    "controllers.claw.main_chords": "disabled",
    "rgb.handheld.mode.mode": "solid",
    "rgb.handheld.mode.solid.hue": 200,
  } as Record<string, any>,
};

const ok = (extra: object = {}) => ({ ok: true, ...extra });

export const backend = {
  // ---- setup ----
  get_hardware: () => ({
    product: "MS-1T91", known: true, kernel: "6.19.4-ba01.fc44.x86_64", cpu_model: "Intel Core Ultra X7 358H",
    gpu_driver: "xe", model: { name: "MSI Claw 8 EX AI+", soc: "Panther Lake", gpu: "Arc B390 (Xe3)", xmx: true },
    stack: { hhd: hhd(), inputplumber: !hhd(), powerstation: !hhd(), controller: scenario.controller, tdp_owner: hhd() ? "hhd" : "steam", conflict: false },
  }),
  get_health: () => [
    { id: "model", title: "Claw model recognised", ok: true, detail: "MSI Claw 8 EX AI+ (MS-1T91), Panther Lake", fix: null },
    { id: "mesa", title: "Intel Vulkan driver", ok: true, detail: "Mesa 26.2.1, kernel 6.19.4, xe", fix: null },
    hhd()
      ? { id: "controller", title: "Controller service: Handheld Daemon", ok: true, detail: "version 4.2.1; MSI buttons, TDP, fans and RGB come from here", fix: null }
      : { id: "controller", title: "Controller service: InputPlumber (Bazzite default)", ok: true, detail: "MSI buttons and gyro come from InputPlumber; Controller and Fans tabs stay read-only", fix: null },
    hhd()
      ? { id: "hhd_tdp", title: "TDP controls enabled in Handheld Daemon", ok: true, detail: "accepted", fix: null }
      : { id: "tdp_owner", title: "Power limit control", ok: true, detail: "MSI firmware limits (the same ones Steam's performance slider sets; the last change wins); PowerStation running", fix: null },
    { id: "msi_fw", title: "MSI firmware controls (msi-wmi-platform)", ok: true, detail: "TDP and fan curve attributes present", fix: null },
    { id: "gpu_clocks", title: "GPU clock control", ok: true, detail: "/sys/class/drm/card1/device/tile0/gt0/freq0/min_freq", fix: null },
    { id: "cpu_pct", title: "CPU ceiling control (intel_pstate)", ok: true, detail: "/sys/devices/system/cpu/intel_pstate/max_perf_pct", fix: null },
    { id: "charge", title: "Battery charge limit", ok: true, detail: "/sys/class/power_supply/BAT1/charge_control_end_threshold", fix: null },
    state.xessInstalled
      ? { id: "xess", title: "XeSS XMX driver (xess-xmx-linux)", ok: true, detail: "20261018: ok 2026-10-21 Mesa 26.3.0-rc1", fix: null }
      : { id: "xess", title: "XeSS XMX driver (xess-xmx-linux)", ok: false, detail: "not installed", fix: "install_xess" },
    { id: "lsfg", title: "lsfg-vk frame generation (optional)", ok: false, detail: "Lossless Scaling (Steam) not installed: lsfg-vk needs its DLL", fix: null, optional: true },
    { id: "mangohud", title: "MangoHud (needed by autotune for frame times)", ok: true, detail: "installed", fix: null },
    { id: "protonup", title: "ProtonUp-Qt / GE-Proton", ok: true, detail: "a GE-Proton is installed", fix: null },
    { id: "sleep_mode", title: "Sleep mode", ok: true, detail: "s2idle (options: s2idle)", fix: null },
    { id: "sleep_failures", title: "Suspend failures since boot", ok: true, detail: "6 ok, 0 failed", fix: null },
    { id: "s0ix", title: "Deep idle (S0ix) counter", ok: true, detail: "5214 s in S0ix since boot", fix: null },
  ],
  collect_logs: () => "/home/bazzite/Desktop/clawdeck-report-20261021-181502.json",
  run_fix: (fix: string, allow: boolean) => {
    if (fix === "install_xess" && !allow) return { ok: false, error: "xess-xmx-linux has no tested version pinned yet; allow 'latest (untested)' to continue" };
    if (fix === "install_xess") { state.xessInstalled = true; return ok({ next: "log out of Game Mode and back in (or reboot) so the session picks up the driver" }); }
    return ok();
  },
  get_setting: (k: string) => (k === "autotune" ? state.autotune : k === "dry_run" ? state.dry : k === "tdp_boost" ? state.boost : null),
  set_setting: (k: string, v: any) => {
    if (k === "autotune") state.autotune = v; if (k === "dry_run") state.dry = v; if (k === "auto_update") state.autoUpdate = v;
    if (k === "moonlight_shortcut") state.shortcuts.moonlight = v; if (k === "moonlight_host_shortcut") state.shortcuts.host = v;
    return true;
  },
  set_dry_run: (v: boolean) => { state.dry = v; return v; },
  get_versions: () => ({}),
  get_sleep: () => ({
    mem_sleep: "s2idle", mem_sleep_options: ["s2idle"], stats: { success: "6", fail: "0" }, s0ix_residency_us: 5214000000,
    s0ix_supported: true, kernel_params: [], wake_enabled: ["XHCI", "PWRB"],
    fixes: [
      { id: "instant_wake", symptom: "wakes up immediately after sleeping", fix: "disable USB controller wake for this boot: echo XHCI | sudo tee /proc/acpi/wakeup" },
      { id: "no_s0ix", symptom: "battery drains while asleep", fix: "update first (sudo rpm-ostree upgrade); then post tools/suspend-cycle.sh output" },
    ],
  }),

  // ---- power ----
  get_power: () => ({
    gpu: { available: true, min_mhz: state.gpuMin, max_mhz: state.gpuMax, cur_mhz: scenario.game ? 1950 : 450, hw_min_mhz: 300, hw_max_mhz: 2500, efficient_mhz: 1300 },
    cpu: { available: true, max_perf_pct: state.cpu, min_perf_pct: 10, no_turbo: state.turbo ? 0 : 1, epp: state.epp, epp_available: true },
    rapl: { available: true, pl1_w: state.tdp, pl2_w: state.tdp + 6 },
    tdp: hhd() ? { mode: "custom", custom_tdp: state.tdp, boost: true, ready: true } : null,
    hhd: hhd(), msi_firmware: true, tdp_backend: hhd() ? "hhd" : "rapl", tdp_w: state.tdp,
    firmware_tdp: { pl1_w: 0, pl2_w: 0 },
    limits: { min: 8, battery_max: 35, plugged_max: 35, sppt_max: 45 },
  }),
  set_tdp: (w: number) => { state.tdp = w; return done("power limit", w, w, "W"); },
  set_cpu_max_pct: (p: number) => { state.cpu = p; return done("CPU limit", p, p, "%"); },
  set_cpu_turbo: (v: boolean) => { state.turbo = v; return done("CPU turbo", v, v); },
  set_gpu_window: (lo: number | null, hi: number | null) => {
    if (lo != null) state.gpuMin = lo; if (hi != null) state.gpuMax = hi;
    return lo != null ? done("GPU minimum clock", lo, state.gpuMin, "MHz") : done("GPU maximum clock", hi, state.gpuMax, "MHz");
  },
  reset_gpu_window: () => { state.gpuMin = 300; state.gpuMax = 2500; return done("GPU clocks", "300–2500", "300–2500", "MHz"); },
  apply_profile: (name: string) => {
    const p: any = { battery: [12, 70], balanced: [17, 85], performance: [25, 100], dock: [35, 100] }[name];
    state.tdp = p[0]; state.cpu = p[1];
    return { ok: true, profile: name, applied: { tdp: p[0], cpu_max_pct: p[1] }, failed: [], read: { tdp_w: p[0], cpu_max_pct: p[1] },
             summary: `${name} set → hardware reads ${p[0]} W, CPU ${p[1]}%`, error: null };
  },
  revert_power: () => { state.tdp = 25; state.cpu = 100; state.gpuMin = 300; state.gpuMax = 2500;
    return { ok: true, reverted: 3, read: { tdp_w: 25, cpu_max_pct: 100 }, summary: "3 value(s) restored → hardware reads 25 W, CPU 100%", error: null }; },
  list_profiles: () => ({ battery: { tdp: 12 }, balanced: { tdp: 17 }, performance: { tdp: 25 }, dock: { tdp: 35 } }),
  save_game_power: (_a: string, v: any) => v,
  get_game_profile: () => ({ battery: { base: "battery", target_fps: state.targetFps }, plugged: { base: "performance", target_fps: state.targetFps }, launch: state.launch }),
  set_game_profile: (_a: string, _p: boolean, v: any) => { if (v.target_fps) state.targetFps = v.target_fps; return v; },
  reset_learned: () => true,
  get_autotune: () => (scenario.game && state.autotune
    ? { running: true, enabled: true, appid: "2054970", target_fps: state.targetFps, paused: false,
        learned: { tdp: 10, cpu_max_pct: 70, gpu_min_mhz: null, bound: "gpu", confidence: 2, samples: 412 },
        last: { fps: 60.2, pkg_w: 10.4 }, avg_fps: 60.1, low_fps: 57.0, window_s: 10 }
    : { running: false, enabled: state.autotune }),
  export_profiles: () => "{}",
  import_profiles: () => 1,
  export_profiles_file: () => "/home/bazzite/Desktop/clawdeck-profiles-2054970-20261021.json",
  list_profile_files: () => ["/home/bazzite/Downloads/clawdeck-profiles-wuthering-waves.json"],
  import_profiles_file: () => ({ ok: true, imported: 1 }),
  game_started: () => ({}), game_stopped: () => ({}), power_source_changed: () => ({}), resumed: () => ({}),
  bench_start: () => { state.benchRunning = true; state.benchStart = Date.now(); return ok(); },
  bench_status: () => ({ running: state.benchRunning }),
  bench_stop: () => {
    state.benchRunning = false;
    const r = { appid: "2054970", name: "Dragon's Dogma 2", label: "battery", time: "2026-10-21 18:20", avg_fps: 43.1, low1_fps: 34.2, avg_w: 19.9, fps_per_w: 2.17, duration_s: 120, samples: 120 };
    state.results.push(r);
    return r;
  },
  bench_results: () => state.results,
  bench_export: () => "/home/bazzite/Desktop/clawdeck-benchmarks-20261021-182004.csv",

  // ---- battery and fans ----
  get_battery: () => ({
    present: true, status: scenario.plugged ? "Charging" : "Discharging", capacity_pct: 64, power_w: scenario.plugged ? 41.0 : 22.4,
    energy_wh: 51.2, energy_full_wh: 80.0, health_pct: 98.6, plugged: scenario.plugged, charge_limit_supported: true, charge_limit: state.chargeLimit,
  }),
  set_charge_limit: (p: number) => { state.chargeLimit = p; return done("charge limit", p, p, "%"); },
  get_fans: () => ({ available: hhd(), mode: hhd() ? (state.fan === "auto" ? "disabled" : "manual") : null, preset: hhd() ? state.fan : null,
                     presets: ["auto", "quiet", "balanced", "cool"], fan_rpm: [3150], cpu_temp_c: 71, curve: null }),
  set_fan_preset: (n: string) => { state.fan = n; return ok(); },

  // ---- display ----
  get_wide_vrr: () => ({ panel: "CSW0801", panel_name: "PN8007QB1-2", range: state.wideVrr ? [30, 120] : [48, 120],
    wide: state.wideVrr, supported: true, reason: null, pending: null, rpm_ostree: true }),
  set_wide_vrr: (on: boolean) => { state.wideVrr = on; return { ok: true, restart: true }; },
  get_display: () => ({
    settings: state.display, saved: state.displaySaved, conf: "/home/bazzite/.config/environment.d/60-clawdeck-display.conf",
    connectors: [
      { name: "eDP-1", status: "connected", internal: true, modes: ["1920x1200"] },
      ...(scenario.plugged ? [{ name: "DP-1", status: "connected", internal: false, modes: ["3840x2160", "2560x1440", "1920x1080"] }] : []),
    ],
    panel_hz: [60, 120], vrr_range: [30, 120], other_overrides: [],
  }),
  save_display: (s: any) => { state.display = s; state.displaySaved = true; return ok(); },
  reset_display: () => { state.displaySaved = false; return ok(); },

  // ---- controller ----
  get_controller: () => {
    if (!hhd()) return { available: false, settings: [], inputplumber: [
      { path: "/org/shadowblip/InputPlumber/CompositeDevice0", name: "MSI Claw 8 EX AI+ CG3EM", profile: "OpenGamepadUI Default" }] };
    const s = state.controller;
    return { available: true, settings: [
      { key: "controllers.claw.controller_mode.mode", type: "mode", title: "Emulation mode", group: "mode", advanced: false, value: s["controllers.claw.controller_mode.mode"],
        options: [{ value: "dualsense", label: "DualSense" }, { value: "xbox", label: "Xbox" }, { value: "disabled", label: "Disabled" }] },
      { key: "controllers.claw.controller_mode.dualsense.gyro", type: "bool", title: "Gyro", group: "gyro", advanced: false, value: s["controllers.claw.controller_mode.dualsense.gyro"],
        mode_of: { key: "controllers.claw.controller_mode.mode", mode: "dualsense" } },
      { key: "controllers.claw.controller_mode.dualsense.gyro_scaling", type: "int", title: "Gyro scale", min: 1, max: 100, step: 1, group: "gyro", advanced: false,
        value: s["controllers.claw.controller_mode.dualsense.gyro_scaling"], mode_of: { key: "controllers.claw.controller_mode.mode", mode: "dualsense" } },
      { key: "controllers.claw.swap_guide", type: "bool", title: "Swap Guide and QAM buttons", group: "buttons", advanced: false, value: s["controllers.claw.swap_guide"],
        hint: "Swaps the two MSI buttons." },
      { key: "rgb.handheld.mode.mode", type: "mode", title: "RGB mode", group: "rgb", advanced: false, value: s["rgb.handheld.mode.mode"],
        options: [{ value: "disabled", label: "Off" }, { value: "solid", label: "Solid" }, { value: "pulse", label: "Pulse" }, { value: "rainbow", label: "Rainbow" }] },
      { key: "rgb.handheld.mode.solid.hue", type: "int", title: "Hue", min: 0, max: 359, step: 1, group: "rgb", advanced: false, value: s["rgb.handheld.mode.solid.hue"],
        mode_of: { key: "rgb.handheld.mode.mode", mode: "solid" } },
      { key: "controllers.claw.main_chords", type: "multiple", title: "Shortcut chords", group: "buttons", advanced: true, value: s["controllers.claw.main_chords"],
        options: [{ value: "disabled", label: "Off" }, { value: "qam", label: "QAM" }] },
    ] };
  },
  set_controller: (k: string, v: any) => { state.controller[k] = v; return ok({ value: v }); },

  // ---- graphics ----
  get_launch: () => ({ launch: state.launch, env: {}, wrapper: "/home/bazzite/.local/share/clawdeck/clawdeck-launch.sh %command%" }),
  set_launch: (_a: string, v: any) => { Object.assign(state.launch, v); return { launch: state.launch, env: {}, wrapper: "/home/bazzite/.local/share/clawdeck/clawdeck-launch.sh %command%" }; },
  injector_state: () => injectorState(),
  injector_plan: () => {
    const dd2 = scenario.which === "dd2";
    const st = injectorState();
    return dd2 ? {
      found: true, state: st, updates: updates(),
      found_in_game: ["Upscalers in this game: DLSS, FSR 3", "Capcom RE Engine game"],
      compat: { listed: true, optipatcher: false, needs_reframework: true, list_loaded: true },
      plan: { mode: "optiscaler", blocked: false, page: "https://github.com/optiscaler/OptiScaler/wiki/Dragons-Dogma-2",
        reasons: [`This game has DLSS and FSR 3. OptiScaler turns it into XeSS${state.xessInstalled ? " on the XMX cores" : ""}, and adds XeSS frame generation.`,
                  "OptiScaler's compatibility list: works (tested inputs: DLSS, FSR3)."],
        steps: ["Newest OptiScaler, installed next to the game's main program", "REFramework, which Capcom RE Engine games need for OptiScaler"],
        warnings: ["XeSS frame generation needs Borderless or Fullscreen Windowed mode in the game, and HDR10 (not scRGB) if you use HDR.",
                   "Note for this game: Check Wiki entry for more info"],
        options: { fg: true, fg_input: "upscaler", multiplier: state.xessInstalled ? 3 : 2, spoof: true, optipatcher: false, reframework: true } },
    } : {
      found: true, state: st, updates: updates(),
      found_in_game: ["Upscalers in this game: none found"],
      compat: { listed: false, optipatcher: false, needs_reframework: false, list_loaded: true },
      plan: { mode: "lsfg", blocked: false, steps: [], options: {},
        reasons: ["This game has no DLSS, FSR or XeSS for OptiScaler to work with.",
                  "Lossless Scaling frame generation (lsfg-vk) works with any game, as the last option. It runs on the GPU's regular shader cores, not the XMX cores."],
        warnings: ["Needs your own copy of Lossless Scaling from Steam."] },
    };
  },
  injector_install: (_a: string, mode: string) => {
    state.injected[scenario.which] = mode;
    if (mode === "lsfg") state.lsfgInstalled = true;
    return mode === "optiscaler" ? { mode, version: "v0.9.4", reframework_version: "nightly-01433" } : { mode };
  },
  lsfg_install: () => { state.lsfgInstalled = true; return ok({ version: "lsfg-vk-2.1.3" }); },
  get_updates: () => ({ ...updates(), auto: state.autoUpdate }),
  set_epp: (v: string) => { state.epp = v; return done("CPU energy mode", v, v); },
  set_tdp_boost: (v: boolean) => { state.boost = v; return true; },
  rollback: () => ok({ version: "v6.2.0-daily.190" }),
  get_changes: () => ({ first_run: false, changed: [] }),
  get_streaming: () => ({ installed: !!state.moonlight, version: state.moonlight, launcher: "/home/bazzite/.local/share/clawdeck/moonlight/moonlight-vrr",
    host: state.streamHost, app: state.streamApp, shortcut: state.shortcuts.moonlight, host_shortcut: state.shortcuts.host,
    flathub_moonlight: false, latest: "v6.2.0-daily.191", update: false, previous: state.moonlight ? "v6.2.0-daily.190" : null }),
  moonlight_install: () => { state.moonlight = "v6.2.0-daily.191"; return ok({ version: state.moonlight, launcher: "/home/bazzite/.local/share/clawdeck/moonlight/moonlight-vrr", updated: true }); },
  set_stream_host: (h: string, a: string) => { state.streamHost = h; state.streamApp = a || "Desktop"; return ok({ launch_options: `stream ${h} "${a || "Desktop"}"`, launcher: "/home/bazzite/.local/share/clawdeck/moonlight/moonlight-vrr" }); },
  update_now: () => ({ optiscaler: "v0.9.4" }),
  xess_state: () => (state.xessInstalled
    ? { installed: true, package_dir: "/home/bazzite/.local/share/clawdeck/xess-xmx-linux-20261018", version: "20261018", drivers: ["26.1.2", "26.3.0-rc1"],
        default_driver: "26.3.0-rc1", status: { state: "ok", line: "ok 2026-10-21 18:02:11 Mesa 26.3.0-rc1", mode: "XMX (patched driver)" }, official_proton: [] }
    : { installed: false, package_dir: null, version: null, drivers: [], default_driver: null, status: { state: "unknown", line: null }, official_proton: [] }),
  xess_install: (allow: boolean) => (allow ? (state.xessInstalled = true, ok({ next: "log out of Game Mode and back in" }))
    : { ok: false, error: "xess-xmx-linux has no tested version pinned yet; allow 'latest (untested)' to continue" }),
  xess_probe: () => ok(),
  xess_switch_driver: () => ok(),
  list_proton: () => [
    { name: "GE-Proton11-6", display: "GE-Proton11-6", kind: "ge" },
    { name: "proton_experimental", display: "Proton - Experimental", kind: "valve" },
    { name: "proton_10", display: "Proton 10.0", kind: "valve" },
  ],
};

function injectorState() {
  const mode = state.injected[scenario.which] ?? "none";
  return { dir: `/home/bazzite/.local/share/Steam/steamapps/common/${GAMES[scenario.which].name}`, mode,
           optiscaler_present: mode === "optiscaler", foreign_optiscaler: false, native_xess: false,
           version: mode === "optiscaler" ? "v0.9.4" : null,
           info: mode === "optiscaler" && scenario.which === "dd2" ? { reframework_version: "nightly-01433" } : null,
           assets: { optiscaler: true, optipatcher: false, reframework: true },
           lsfg: { layer: state.lsfgInstalled ? "/home/bazzite/.local/share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.json" : null,
                   lossless_dll: "/home/bazzite/.local/share/Steam/steamapps/common/Lossless Scaling/Lossless.dll", ready: state.lsfgInstalled },
           lsfg_version: state.lsfgInstalled ? "lsfg-vk-2.1.3" : null };
}

function updates() {
  const anyOpti = Object.values(state.injected).includes("optiscaler");
  return {
    optiscaler: { installed: anyOpti ? "v0.9.4" : null, latest: "v0.9.4", update: false },
    optipatcher: { installed: null, latest: "2026-10-19T08:12:00Z", update: false },
    reframework: { installed: anyOpti ? "nightly-01433" : null, latest: "nightly-01433", update: false },
    lsfg: { installed: state.lsfgInstalled ? "lsfg-vk-2.1.3" : null, latest: "lsfg-vk-2.1.3", update: false },
  };
}
