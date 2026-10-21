// Example backend for the browser preview: what main.py would answer on a Claw 8 EX running current Bazzite.
// Every number here is an example, not a measurement. The scenario switches (game running, charger, controller
// service) are set from the preview page.

export const scenario = {
  game: true,
  plugged: false,
  controller: "inputplumber" as "inputplumber" | "hhd",
  listeners: [] as (() => void)[],
  set(k: "game" | "plugged" | "controller", v: any) { (this as any)[k] = v; this.listeners.forEach((l) => l()); },
};

const hhd = () => scenario.controller === "hhd";
const state = {
  dry: false,
  autotune: true,
  tdp: 20,
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
  get_setting: (k: string) => (k === "autotune" ? state.autotune : k === "dry_run" ? state.dry : null),
  set_setting: (k: string, v: any) => { if (k === "autotune") state.autotune = v; if (k === "dry_run") state.dry = v; return true; },
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
    cpu: { available: true, max_perf_pct: state.cpu, min_perf_pct: 10, no_turbo: state.turbo ? 0 : 1 },
    rapl: { available: true, pl1_w: state.tdp, pl2_w: state.tdp + 6 },
    tdp: hhd() ? { mode: "custom", custom_tdp: state.tdp, boost: true, ready: true } : null,
    hhd: hhd(), msi_firmware: true, tdp_backend: hhd() ? "hhd" : "msi-firmware",
    firmware_tdp: { pl1_w: state.tdp, pl2_w: Math.min(45, Math.round(state.tdp * 45 / 35)) },
    limits: { min: 8, battery_max: 35, plugged_max: 35, sppt_max: 45 },
  }),
  set_tdp: (w: number) => { state.tdp = w; return true; },
  set_cpu_max_pct: (p: number) => { state.cpu = p; return true; },
  set_cpu_turbo: (v: boolean) => { state.turbo = v; return true; },
  set_gpu_window: (lo: number | null, hi: number | null) => { if (lo != null) state.gpuMin = lo; if (hi != null) state.gpuMax = hi; return true; },
  reset_gpu_window: () => { state.gpuMin = 300; state.gpuMax = 2500; return true; },
  apply_profile: (name: string) => {
    const p: any = { battery: [20, 80], performance: [30, 100], dock: [35, 100] }[name];
    state.tdp = p[0]; state.cpu = p[1];
    return { profile: name, applied: { tdp: p[0], cpu_max_pct: p[1] }, failed: [] };
  },
  revert_power: () => { state.tdp = 20; state.cpu = 100; state.gpuMin = 300; state.gpuMax = 2500; return 3; },
  list_profiles: () => ({}),
  save_game_power: (_a: string, v: any) => v,
  get_game_profile: () => ({ battery: { base: "battery", target_fps: state.targetFps }, plugged: { base: "performance", target_fps: state.targetFps }, launch: state.launch }),
  set_game_profile: (_a: string, _p: boolean, v: any) => { if (v.target_fps) state.targetFps = v.target_fps; return v; },
  reset_learned: () => true,
  get_autotune: () => (scenario.game && state.autotune
    ? { running: true, enabled: true, appid: "2054970", target_fps: state.targetFps, paused: false,
        learned: { tdp: 21, cpu_max_pct: 90, gpu_min_mhz: null, bound: "cpu", confidence: 2, samples: 412 },
        last: { fps: 61.4, pkg_w: 20.8 } }
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
  set_charge_limit: (p: number) => { state.chargeLimit = p; return true; },
  get_fans: () => ({ available: hhd(), mode: hhd() ? (state.fan === "auto" ? "disabled" : "manual") : null, preset: hhd() ? state.fan : null,
                     presets: ["auto", "quiet", "balanced", "cool"], fan_rpm: [3150], cpu_temp_c: 71, curve: null }),
  set_fan_preset: (n: string) => { state.fan = n; return ok(); },

  // ---- display ----
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
    if (!hhd()) return { available: false, settings: [] };
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
  injector_state: () => ({ dir: "/home/bazzite/.local/share/Steam/steamapps/common/Dragons Dogma 2", mode: state.launch.inject, optiscaler_present: state.launch.inject === "optiscaler",
                           native_xess: false, info: null, assets: { optiscaler: true, xess: true }, lsfg: { layer: "/usr/share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.json", lossless_dll: null, ready: false } }),
  injector_install: (_a: string, mode: string) => { state.launch.inject = mode; return { mode }; },
  injector_download: () => ok({ tag: "v0.9.4" }),
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
