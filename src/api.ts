import { callable } from "@decky/api";

// One typed function per backend call (main.py). The wiring test (tests/test_modules.py) checks every name exists.

export type Health = { id: string; title: string; ok: boolean; detail: string; fix: string | null; optional?: boolean };
export type PowerStatus = {
  gpu: { available: boolean; min_mhz: number | null; max_mhz: number | null; cur_mhz: number | null; hw_min_mhz: number | null; hw_max_mhz: number | null; efficient_mhz: number | null; power_profile?: string | null; power_profiles?: string[] };
  cpu: { available: boolean; max_perf_pct: number | null; min_perf_pct: number | null; no_turbo: number | null; epp?: string | null; epp_available?: boolean };
  rapl: { available: boolean; pl1_w: number | null; pl2_w: number | null; max_w?: number | null };
  tdp: { mode: string; custom_tdp: number; boost: boolean; ready: boolean } | null;
  hhd: boolean; msi_firmware: boolean; tdp_backend: string | null; firmware_tdp: { pl1_w: number | null; pl2_w: number | null } | null;
  tdp_w?: number | null;
  limits: { min: number; battery_max: number; plugged_max: number; sppt_max: number };
  range?: { min: number; max: number };
  custom_tdp?: number | null; autotune_floor?: number | null; autotune_keep?: number; thermal?: Thermal;
};
/** The temperature guard while thermald is paused (§21): "thermal: stepped to N W" once it has acted. */
export type Thermal = { sensor: string | null; temp_c: number | null; tripped: boolean; stepped_w: number | null; line: string | null; stepped_s: number | null };
export type Stack = { hhd: boolean; inputplumber: boolean; powerstation: boolean; controller: string | null; tdp_owner: string | null; conflict: boolean };
export type Hardware = { product: string; known: boolean; kernel: string; cpu_model: string; gpu_driver: string | null; model: { name: string; soc: string; gpu: string; xmx: boolean }; stack: Stack; other_plugins?: { injector: string[]; power: string[] } };
export type AutotuneStatus = { running: boolean; enabled: boolean; appid?: string; learned?: any; paused?: boolean; last?: any;
  phase?: "measure" | "balance" | "lower" | "hold"; tdp?: number; ceiling_tdp?: number; floor_tdp?: number; keep_pct?: number;
  max_fps?: number | null; avg_fps?: number | null; low_fps?: number | null; pct_of_max?: number | null; fps_per_w?: number | null;
  source?: "rendered" | "presented"; multiplier?: number | null; window_s?: number;
  last_action?: string | null; last_delta?: string | null; last_change_s?: number | null; bound?: "gpu" | "cpu" | "balanced";
  thermal_cap?: number | null; thermal?: Thermal; learning?: boolean; pl1_window_s?: number | null };

/** What every write answers: what was asked, what the hardware reads back, and why it failed (shown in red). */
export type WriteResult = { ok: boolean; what?: string; wanted?: any; read?: any; unit?: string; summary: string; error: string | null };

// ---- setup / health ----------------------------------------------------------------------------------------------
export const getHardware = callable<[], Hardware>("get_hardware");
export const getHealth = callable<[force?: boolean], Health[]>("get_health");
export const collectLogs = callable<[], string>("collect_logs");
export const getSetting = callable<[key: string], any>("get_setting");
export const setSetting = callable<[key: string, value: any], boolean>("set_setting");
export type Fix = { ok: boolean; error?: string; log?: string; path?: string; next?: string; note?: string | null; removed?: string[] };
export const runFix = callable<[fix: string, allowUnpinned: boolean], Fix>("run_fix");
export type SelfTest = { ok: boolean; passed?: number; failed?: number; lines?: string[]; summary?: string; error: string | null };
export const selfTest = callable<[], SelfTest>("self_test");

// ---- power ---------------------------------------------------------------------------------------------------------
export const getPower = callable<[], PowerStatus>("get_power");
export type ProfileRead = { tdp_w: number | null; pl2_w: number | null; cpu_max_pct: number | null; epp: string | null; backend: string | null };
export type LimitResult = WriteResult & { profile: string; applied: any; failed: string[]; read: ProfileRead; limit_w?: number | null; autotune_w?: number | null };
export const applyProfile = callable<[name: string, appid: string | null], LimitResult>("apply_profile");
export const setTdp = callable<[watts: number], LimitResult>("set_tdp");
// advanced levers (§19): each answers with what the hardware reads back
export const setCpuTurbo = callable<[on: boolean], WriteResult>("set_cpu_turbo");
export const setCpuMaxPct = callable<[pct: number], WriteResult>("set_cpu_max_pct");
export const setGpuWindow = callable<[min: number | null, max: number | null], WriteResult>("set_gpu_window");
export const setEpp = callable<[value: string], WriteResult>("set_epp");
export const setGpuPowerProfile = callable<[saving: boolean], WriteResult>("set_gpu_power_profile");
export const setAutotuneFloor = callable<[watts: number | null], WriteResult & { floor_w: number }>("set_autotune_floor");
export const setAutotuneKeep = callable<[pct: number], WriteResult & { keep_pct: number }>("set_autotune_keep");
export const listProfiles = callable<[], Record<string, { tdp?: number; cpu_max_pct?: number; note?: string }>>("list_profiles");
export const gameStarted = callable<[appid: string], any>("game_started");
export const gameStopped = callable<[appid: string], any>("game_stopped");
export const powerSourceChanged = callable<[plugged: boolean], any>("power_source_changed");
export const resumed = callable<[], any>("resumed");
export const getAutotune = callable<[], AutotuneStatus>("get_autotune");

// ---- per game ------------------------------------------------------------------------------------------------------
export type FsrRoute = "off" | "xess" | "fg" | "inject";
export type OptiPreset = "off" | "xess" | "fsr4" | "fsr4_xmx" | "fsr4_xmx_lite";
export type Launch = { opti_overlay?: number | null; opti_fg?: string | null; opti_ini?: Record<string, string> | null; reframework?: boolean | null; xmx?: boolean | null; fg?: number; fsr?: FsrRoute; xess_latest?: boolean; low_latency?: boolean; opti?: OptiPreset; opti_xefg?: boolean };
export const getLaunch = callable<[appid: string], { launch: Launch; env: Record<string, string>; wrapper: string; error?: string | null }>("get_launch");
export const setLaunch = callable<[appid: string, values: Partial<Launch>], { ok: boolean; error?: string; launch: Launch; env: Record<string, string>; wrapper: string }>("set_launch_any");
export type XessState = { installed: boolean; package_dir: string | null; version: string | null; source: { repo: string; tag: string } | null; drivers: string[];
  default_driver: string | null; status: { state: string; line: string | null; mode?: string }; low_latency: boolean; official_proton: string[];
  scope?: XmxScope | null; pinned?: string | null; update?: boolean };
export type LatencyState = { mode: "pacing" | "idle" | "no-fg" | "off" | "none"; detail: string };
export type FramegenState = { installed: boolean; dir: string | null; version: string | null; fgmod_ready: boolean; fgmod: string | null; optiscaler: string | null };
export type OptiScalerIn = { present: boolean; dir: string | null; version?: string | null; xess_fg?: boolean };
export type XmxScope = { session_wide: boolean; per_game: boolean; every_game?: boolean; auto: boolean; protons: string[]; scoped: string[]; unscoped: string[] };
export type GameStatus = { launch: Launch; xess: XessState; latency: LatencyState; optiscaler: OptiScalerIn; framegen: FramegenState; wrapper: string; xmx_scope?: XmxScope;
  drive?: { device: string; mount: string; fstype: string; slow: boolean; kind: string; sd: boolean } | null;
  fsr4_xmx?: { dir: string; ready: boolean; missing: string[] }; opti_owner?: "clawdeck" | "decky-framegen" | "other" | null;
  opti_version?: string | null;
  files?: { owner: string | null; version?: string | null; dll?: string | null; preset?: string | null; folder: string | null; reframework: boolean; install_dir: string | null } };
export const gameStatus = callable<[appid: string], GameStatus>("game_status");
export type GameEntry = { appid: string; name: string; dir: string; setup: "optiscaler" | "launch" | null; supported?: boolean; via?: string[] };
export const gamesList = callable<[], GameEntry[]>("games_list");
export type Recommendation = { route: "own_xess" | "re_engine" | "fsr_on_xess" | "optiscaler"; known: boolean; name: string | null;
  re_engine: boolean; optiscaler: boolean; ini: Record<string, string>; later: string[]; reasons: string[]; applied: boolean;
  ini_version: string | null; opti_owner?: string | null; frame_gen?: string | null;
  fsr4?: { dx12: boolean; files: boolean; missing: string[]; dir: string }; reframework_state: { present: boolean; ours: boolean; tag?: string | null } | null };
export const gameRecommend = callable<[appid: string], Recommendation>("game_recommend");
export const gameApplyRecommended = callable<[appid: string, allowUnpinned: boolean], { ok: boolean; error?: string; next?: string; env?: Record<string, string> }>("game_apply_recommended");
export const gameChoose = callable<[appid: string, upscaler: string, fg: number, allowUnpinned: boolean, overlay: number | null], { ok: boolean; error?: string; next?: string; env?: Record<string, string> }>("game_choose");
export const gameSetOpti = callable<[appid: string, values: Partial<Launch>], { ok: boolean; error?: string }>("game_set_opti");
export const gameUndoRecommended = callable<[appid: string], { ok: boolean; error?: string }>("game_undo_recommended");

// ---- setup: installs ---------------------------------------------------------------------------------------------
export const xessState = callable<[], XessState>("xess_state");
export type DriverCheck = { ok: boolean; ran: boolean; error?: string; status: { state: string; line: string | null; mode?: string }; rc?: number; log?: string };
export const xessInstall = callable<[allowUnpinned: boolean], Fix & { tag?: string; repo?: string; up_to_date?: boolean; summary?: string; check?: DriverCheck }>("xess_install");
export type OptiState = { version: string; installed: boolean; games: number; framegen: boolean; framegen_version: string | null };
export const setXmxEveryGame = callable<[on: boolean], { ok: boolean; error?: string; every_game: boolean }>("set_xmx_every_game");
export const optiscalerState = callable<[], OptiState>("optiscaler_state");
export const optiscalerInstall = callable<[], { ok: boolean; error?: string; already?: boolean; version?: string }>("optiscaler_install");
export const framegenState = callable<[], FramegenState>("framegen_state");
export const framegenInstall = callable<[allowUnpinned: boolean], Fix & { already?: boolean; summary?: string; restarting?: boolean; tag?: string }>("framegen_install");

// ---- fixes -----------------------------------------------------------------------------------------------------------
export type WideVrr = { panel: string | null; panel_name: string | null; range: number[] | null; wide: boolean; supported: boolean;
  reason: string | null; pending: "on" | "off" | null; rpm_ostree: boolean };
export type ButtonsState = { applies: boolean; ok: boolean; installed: boolean; stock_map: string | null; upstream_fixed?: boolean; legacy_hwdb: string[] };
export type OverlayPower = { available: boolean; on: boolean; files: { path: string; mode: string; readable: boolean }[]; battery_w: number | null };
export type QuietLog = { applies: boolean; installed: boolean; per_minute: number | null };
export type Fixes = { vrr: WideVrr; buttons: ButtonsState; health: { ok: boolean; total: number; problems: number; line: string }; overlay_power: OverlayPower; quiet_log?: QuietLog };
export const getFixes = callable<[], Fixes>("get_fixes");
export const uiLog = callable<[msg: string], void>("ui_log");
export const displayExternal = callable<[], { connected: boolean; connectors: string[] }>("display_external");
export const setWideVrr = callable<[on: boolean], { ok: boolean; error?: string; restart?: boolean }>("set_wide_vrr");
export type Changes = { first_run: boolean; changed: { what: string; key: string; from: string | null; to: string | null }[]; failing?: { title: string; detail: string }[] };
export const getChanges = callable<[], Changes>("get_changes");

// fan presets and the battery charge limit (fan.py)
export type FanState = { supported: boolean; preset?: string; custom?: number[];
  fans?: { fan: number; rpm: number | null; enable: number | null; temps: (number | null)[]; duties: (number | null)[] }[] };
export type ChargeState = { supported: boolean; limit?: number | null; setting?: number | null };
export const getFan = callable<[], FanState>("get_fan");
export const setFan = callable<[preset: string, custom?: number[] | null], WriteResult>("set_fan");
export const getCharge = callable<[], ChargeState>("get_charge");
export const setChargeLimit = callable<[limit: number | null], WriteResult>("set_charge_limit");
