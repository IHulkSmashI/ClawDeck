import { callable } from "@decky/api";

export type Health = { id: string; title: string; ok: boolean; detail: string; fix: string | null; optional?: boolean };
export type PowerStatus = {
  gpu: { available: boolean; min_mhz: number | null; max_mhz: number | null; cur_mhz: number | null; hw_min_mhz: number | null; hw_max_mhz: number | null; efficient_mhz: number | null };
  cpu: { available: boolean; max_perf_pct: number | null; min_perf_pct: number | null; no_turbo: number | null; epp?: string | null; epp_available?: boolean };
  rapl: { available: boolean; pl1_w: number | null; pl2_w: number | null };
  tdp: { mode: string; custom_tdp: number; boost: boolean; ready: boolean } | null;
  hhd: boolean; msi_firmware: boolean; tdp_backend: string | null; firmware_tdp: { pl1_w: number | null; pl2_w: number | null } | null;
  limits: { min: number; battery_max: number; plugged_max: number; sppt_max: number };
};
export type BatteryStatus = {
  present: boolean; status?: string; capacity_pct?: number; power_w?: number | null; energy_wh?: number | null;
  energy_full_wh?: number | null; health_pct?: number | null; plugged?: boolean | null;
  charge_limit_supported?: boolean; charge_limit?: number | null;
};
export type Stack = { hhd: boolean; inputplumber: boolean; powerstation: boolean; controller: string | null; tdp_owner: string | null; conflict: boolean };
export type Hardware = { product: string; known: boolean; kernel: string; cpu_model: string; gpu_driver: string | null; model: { name: string; soc: string; gpu: string; xmx: boolean }; stack: Stack; other_plugins?: { injector: string[]; power: string[] } };
export type GameProfile = { base?: string; tdp?: number; cpu_max_pct?: number; gpu_min_mhz?: number; gpu_max_mhz?: number; target_fps?: number; autotune?: boolean; learned?: any };
export type AutotuneStatus = { running: boolean; enabled: boolean; appid?: string; target_fps?: number; learned?: any; paused?: boolean; last?: any;
  avg_fps?: number | null; low_fps?: number | null; window_s?: number };

export const getHardware = callable<[], Hardware>("get_hardware");
export const getHealth = callable<[force?: boolean], Health[]>("get_health");
export const collectLogs = callable<[], string>("collect_logs");
export const getSetting = callable<[key: string], any>("get_setting");
export const setSetting = callable<[key: string, value: any], boolean>("set_setting");
export const setDryRun = callable<[on: boolean], boolean>("set_dry_run");

export const getPower = callable<[], PowerStatus>("get_power");
export const setTdp = callable<[watts: number], boolean>("set_tdp");
export const setCpuMaxPct = callable<[pct: number], boolean>("set_cpu_max_pct");
export const setCpuTurbo = callable<[on: boolean], boolean>("set_cpu_turbo");
export const setGpuWindow = callable<[min: number | null, max: number | null], boolean>("set_gpu_window");
export const resetGpuWindow = callable<[], boolean>("reset_gpu_window");
export const applyProfile = callable<[name: string, appid: string | null], any>("apply_profile");
export const revertPower = callable<[], number>("revert_power");
export const listProfiles = callable<[], Record<string, any>>("list_profiles");

export const getGameProfile = callable<[appid: string], { battery: GameProfile; plugged: GameProfile }>("get_game_profile");
export const setGameProfile = callable<[appid: string, plugged: boolean, values: Partial<GameProfile>], GameProfile>("set_game_profile");
export const exportProfiles = callable<[appid: string | null], string>("export_profiles");
export const importProfiles = callable<[text: string, overwrite: boolean], number>("import_profiles");

export const gameStarted = callable<[appid: string], any>("game_started");
export const gameStopped = callable<[appid: string], any>("game_stopped");
export const getAutotune = callable<[], AutotuneStatus>("get_autotune");

export const getBattery = callable<[], BatteryStatus>("get_battery");
export const setChargeLimit = callable<[percent: number], boolean>("set_charge_limit");

export const injectorState = callable<[appid: string], any>("injector_state");

// ---- added modules ---------------------------------------------------------------------------------------------------
export type Fix = { ok: boolean; error?: string; log?: string; path?: string; next?: string; dry_run?: boolean; would?: string };
export const runFix = callable<[fix: string, allowUnpinned: boolean], Fix>("run_fix");
export const getVersions = callable<[], Record<string, { repo: string; tag: string; pinned: boolean; fork?: string }>>("get_versions");

export const saveGamePower = callable<[appid: string, values: Partial<GameProfile>], GameProfile>("save_game_power");
export const resetLearned = callable<[appid: string], boolean>("reset_learned");
export const exportProfilesFile = callable<[appid: string | null], string>("export_profiles_file");
export const listProfileFiles = callable<[], string[]>("list_profile_files");
export const importProfilesFile = callable<[path: string, overwrite: boolean], { ok: boolean; imported?: number; error?: string }>("import_profiles_file");
export const powerSourceChanged = callable<[plugged: boolean], any>("power_source_changed");
export const resumed = callable<[], any>("resumed");

export type FanStatus = { available: boolean; mode: string | null; preset: string | null; presets: string[]; fan_rpm: number[]; cpu_temp_c: number | null; curve: Record<string, number> | null };
export const getFans = callable<[], FanStatus>("get_fans");
export const setFanPreset = callable<[name: string], { ok: boolean; error?: string }>("set_fan_preset");

export type DisplaySettings = { vrr: boolean; vrr_min: number; vrr_max: number; hdr: boolean; hdr_nits: number };
export type Connector = { name: string; status: string | null; internal: boolean; modes: string[] };
export type DisplayState = { settings: DisplaySettings; saved: boolean; conf: string; connectors: Connector[]; panel_hz: number[]; vrr_range: number[] | null; other_overrides: string[] };
export const getDisplay = callable<[], DisplayState>("get_display");
export type WideVrr = { panel: string | null; panel_name: string | null; range: number[] | null; wide: boolean; supported: boolean;
  reason: string | null; pending: "on" | "off" | null; rpm_ostree: boolean };
export const getWideVrr = callable<[], WideVrr>("get_wide_vrr");
export const setWideVrr = callable<[on: boolean], { ok: boolean; error?: string; restart?: boolean }>("set_wide_vrr");
export const saveDisplay = callable<[settings: DisplaySettings], { ok: boolean; error?: string; dry_run?: boolean }>("save_display");
export const resetDisplay = callable<[], { ok: boolean; error?: string }>("reset_display");

export type SleepState = { mem_sleep: string | null; mem_sleep_options: string[]; stats: Record<string, string | null>; s0ix_residency_us: number | null; s0ix_supported: boolean; kernel_params: string[]; wake_enabled: string[]; fixes: { id: string; symptom: string; fix: string }[] };
export const getSleep = callable<[], SleepState>("get_sleep");

export type HhdSetting = { key: string; type: string; title: string; hint?: string | null; options?: { value: any; label: string }[]; min?: number; max?: number; step?: number; unit?: string; value: any; group: string; advanced: boolean; mode_of?: { key: string; mode: string } };
export type InputPlumberDevice = { path: string; name: string | null; profile: string | null };
export const getController = callable<[], { available: boolean; settings: HhdSetting[]; inputplumber?: InputPlumberDevice[] }>("get_controller");
export const setController = callable<[key: string, value: any], { ok: boolean; value?: any; error?: string }>("set_controller");

export type Launch = { inject?: string; lsfg?: { multiplier: number; flow_scale: number; performance_mode: boolean }; hdr?: boolean; mesa?: string; proton?: string | null };
export const getLaunch = callable<[appid: string], { launch: Launch; env: Record<string, string>; wrapper: string }>("get_launch");
export const setLaunch = callable<[appid: string, values: Partial<Launch>], { launch: Launch; env: Record<string, string>; wrapper: string }>("set_launch");
export type InjectorPlan = {
  found: boolean;
  plan?: { mode: "none" | "native" | "optiscaler" | "lsfg"; blocked: boolean; reasons: string[]; warnings: string[]; steps: string[];
    alternative?: string | null; page?: string | null;
    options: { fg?: boolean; fg_input?: string; multiplier?: number; spoof?: boolean; optipatcher?: boolean; reframework?: boolean } };
  found_in_game?: string[];
  compat?: { listed: boolean; optipatcher: boolean; needs_reframework: boolean; list_loaded: boolean };
  state?: any;
  updates?: Record<string, { installed: string | null; latest: string | null; update: boolean }>;
};
export type Updates = Record<string, { installed: string | null; latest: string | null; update: boolean }> & { auto?: boolean };
export const injectorPlan = callable<[appid: string, name: string], InjectorPlan>("injector_plan");
export const injectorInstall = callable<[appid: string, mode: string, options: { multiplier?: number; fg?: boolean } | null, lsfg: Launch["lsfg"] | null, name: string], any>("injector_install");
export const lsfgInstall = callable<[], { ok: boolean; version?: string; error?: string; dry_run?: boolean }>("lsfg_install");
export const getUpdates = callable<[force: boolean], Updates>("get_updates");
export const updateNow = callable<[], Record<string, string>>("update_now");

export type XessState = { installed: boolean; package_dir: string | null; version: string | null; drivers: string[]; default_driver: string | null; status: { state: string; line: string | null; mode?: string }; official_proton: string[] };
export const xessState = callable<[], XessState>("xess_state");
export const xessInstall = callable<[allowUnpinned: boolean], Fix>("xess_install");
export const xessProbe = callable<[], Fix & { state?: XessState }>("xess_probe");
export const xessSwitchDriver = callable<[version: string], Fix>("xess_switch_driver");

export type ProtonTool = { name: string; display: string; kind: string };
export const listProton = callable<[], ProtonTool[]>("list_proton");

export type BenchResult = { appid: string; name: string; label: string; time: string; avg_fps?: number; low1_fps?: number; avg_w?: number | null; fps_per_w?: number | null; duration_s: number; samples?: number };
export const benchStart = callable<[appid: string, name: string, label: string, durationS: number], { ok: boolean; error?: string }>("bench_start");
export const benchStatus = callable<[], { running: boolean; elapsed_s?: number; duration_s?: number; avg_fps?: number; avg_w?: number | null; label?: string }>("bench_status");
export const benchStop = callable<[], BenchResult | null>("bench_stop");
export const benchResults = callable<[], BenchResult[]>("bench_results");
export const benchExport = callable<[], string>("bench_export");

export const setEpp = callable<[value: string], boolean>("set_epp");
export const setTdpBoost = callable<[on: boolean], boolean>("set_tdp_boost");
export const rollback = callable<[comp: string], { ok: boolean; version?: string; error?: string }>("rollback");
export type StreamingState = { installed: boolean; version: string | null; launcher: string; host: string | null; app: string;
  shortcut: number | null; host_shortcut: number | null; flathub_moonlight: boolean; latest: string | null; update: boolean; previous: string | null };
export const getStreaming = callable<[], StreamingState>("get_streaming");
export const moonlightInstall = callable<[], { ok: boolean; version?: string; launcher?: string; error?: string; dry_run?: boolean; would?: string; updated?: boolean }>("moonlight_install");
export const setStreamHost = callable<[host: string, app: string], { ok: boolean; launch_options?: string; launcher?: string; error?: string }>("set_stream_host");
export type Changes = { first_run: boolean; changed: { what: string; key: string; from: string | null; to: string | null }[]; failing?: { title: string; detail: string }[] };
export const getChanges = callable<[], Changes>("get_changes");
