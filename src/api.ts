import { callable } from "@decky/api";

// One typed function per backend call (main.py). The wiring test (tests/test_modules.py) checks every name exists.

export type Health = { id: string; title: string; ok: boolean; detail: string; fix: string | null; optional?: boolean };
export type PowerStatus = {
  gpu: { available: boolean; min_mhz: number | null; max_mhz: number | null; cur_mhz: number | null; hw_min_mhz: number | null; hw_max_mhz: number | null; efficient_mhz: number | null };
  cpu: { available: boolean; max_perf_pct: number | null; min_perf_pct: number | null; no_turbo: number | null; epp?: string | null; epp_available?: boolean };
  rapl: { available: boolean; pl1_w: number | null; pl2_w: number | null; max_w?: number | null };
  tdp: { mode: string; custom_tdp: number; boost: boolean; ready: boolean } | null;
  hhd: boolean; msi_firmware: boolean; tdp_backend: string | null; firmware_tdp: { pl1_w: number | null; pl2_w: number | null } | null;
  tdp_w?: number | null;
  limits: { min: number; battery_max: number; plugged_max: number; sppt_max: number };
  range?: { min: number; max: number };
  custom_tdp?: number | null; autotune_floor?: number | null; autotune_keep?: number;
};
export type Stack = { hhd: boolean; inputplumber: boolean; powerstation: boolean; controller: string | null; tdp_owner: string | null; conflict: boolean };
export type Hardware = { product: string; known: boolean; kernel: string; cpu_model: string; gpu_driver: string | null; model: { name: string; soc: string; gpu: string; xmx: boolean }; stack: Stack; other_plugins?: { injector: string[]; power: string[] } };
export type AutotuneStatus = { running: boolean; enabled: boolean; appid?: string; learned?: any; paused?: boolean; last?: any;
  phase?: "measure" | "balance" | "lower" | "hold"; tdp?: number; ceiling_tdp?: number; floor_tdp?: number; keep_pct?: number;
  max_fps?: number | null; avg_fps?: number | null; low_fps?: number | null; pct_of_max?: number | null; fps_per_w?: number | null;
  source?: "rendered" | "presented"; multiplier?: number | null; window_s?: number;
  last_action?: string | null; last_delta?: string | null; last_change_s?: number | null; bound?: "gpu" | "cpu" | "balanced" };

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
export const setAutotuneFloor = callable<[watts: number | null], WriteResult & { floor_w: number }>("set_autotune_floor");
export const setAutotuneKeep = callable<[pct: number], WriteResult & { keep_pct: number }>("set_autotune_keep");
export const listProfiles = callable<[], Record<string, { tdp?: number; cpu_max_pct?: number; note?: string }>>("list_profiles");
export const gameStarted = callable<[appid: string], any>("game_started");
export const gameStopped = callable<[appid: string], any>("game_stopped");
export const powerSourceChanged = callable<[plugged: boolean], any>("power_source_changed");
export const resumed = callable<[], any>("resumed");
export const getAutotune = callable<[], AutotuneStatus>("get_autotune");

// ---- per game ------------------------------------------------------------------------------------------------------
export type Launch = { xmx?: boolean; xell?: boolean };
export const getLaunch = callable<[appid: string], { launch: Launch; env: Record<string, string>; wrapper: string; error?: string | null }>("get_launch");
export const setLaunch = callable<[appid: string, values: Partial<Launch>], { ok: boolean; error?: string; launch: Launch; env: Record<string, string>; wrapper: string }>("set_launch");
export type XessState = { installed: boolean; package_dir: string | null; version: string | null; source: { repo: string; tag: string } | null; drivers: string[];
  default_driver: string | null; status: { state: string; line: string | null; mode?: string }; xell_capable: boolean; official_proton: string[] };
export type XellState = { mode: "driver" | "cross-vendor" | "none" | "off"; shim: boolean; detail: string };
export type OptiScalerIn = { present: boolean; dir: string | null; version?: string | null; xess_fg?: boolean };
export type GameStatus = { launch: Launch; xess: XessState; xell: XellState; optiscaler: OptiScalerIn; wrapper: string };
export const gameStatus = callable<[appid: string], GameStatus>("game_status");

// ---- setup: installs ---------------------------------------------------------------------------------------------
export const xessState = callable<[], XessState>("xess_state");
export const xessInstall = callable<[allowUnpinned: boolean], Fix & { tag?: string; repo?: string }>("xess_install");

// ---- fixes -----------------------------------------------------------------------------------------------------------
export type WideVrr = { panel: string | null; panel_name: string | null; range: number[] | null; wide: boolean; supported: boolean;
  reason: string | null; pending: "on" | "off" | null; rpm_ostree: boolean };
export type ButtonsState = { applies: boolean; ok: boolean; installed: boolean; stock_map: string | null; upstream_fixed?: boolean; legacy_hwdb: string[] };
export type Fixes = { vrr: WideVrr; buttons: ButtonsState; health: { ok: boolean; total: number; problems: number; line: string } };
export const getFixes = callable<[], Fixes>("get_fixes");
export const setWideVrr = callable<[on: boolean], { ok: boolean; error?: string; restart?: boolean }>("set_wide_vrr");
export type Changes = { first_run: boolean; changed: { what: string; key: string; from: string | null; to: string | null }[]; failing?: { title: string; detail: string }[] };
export const getChanges = callable<[], Changes>("get_changes");
