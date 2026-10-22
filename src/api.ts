import { callable } from "@decky/api";

// One typed function per backend call (main.py). The wiring test (tests/test_modules.py) checks every name exists.

export type Health = { id: string; title: string; ok: boolean; detail: string; fix: string | null; optional?: boolean };
export type PowerStatus = {
  gpu: { available: boolean; min_mhz: number | null; max_mhz: number | null; cur_mhz: number | null; hw_min_mhz: number | null; hw_max_mhz: number | null; efficient_mhz: number | null };
  cpu: { available: boolean; max_perf_pct: number | null; min_perf_pct: number | null; no_turbo: number | null; epp?: string | null; epp_available?: boolean };
  rapl: { available: boolean; pl1_w: number | null; pl2_w: number | null };
  tdp: { mode: string; custom_tdp: number; boost: boolean; ready: boolean } | null;
  hhd: boolean; msi_firmware: boolean; tdp_backend: string | null; firmware_tdp: { pl1_w: number | null; pl2_w: number | null } | null;
  tdp_w?: number | null;
  limits: { min: number; battery_max: number; plugged_max: number; sppt_max: number };
};
export type Stack = { hhd: boolean; inputplumber: boolean; powerstation: boolean; controller: string | null; tdp_owner: string | null; conflict: boolean };
export type Hardware = { product: string; known: boolean; kernel: string; cpu_model: string; gpu_driver: string | null; model: { name: string; soc: string; gpu: string; xmx: boolean }; stack: Stack; other_plugins?: { injector: string[]; power: string[] } };
export type GameProfile = { base?: string; tdp?: number; cpu_max_pct?: number; gpu_min_mhz?: number; gpu_max_mhz?: number; target_fps?: number; autotune?: boolean; learned?: any };
export type AutotuneStatus = { running: boolean; enabled: boolean; appid?: string; target_fps?: number; learned?: any; paused?: boolean; last?: any;
  avg_fps?: number | null; low_fps?: number | null; window_s?: number; last_action?: string | null; ceiling_tdp?: number };

/** What every write answers: what was asked, what the hardware reads back, and why it failed (shown in red). */
export type WriteResult = { ok: boolean; what?: string; wanted?: any; read?: any; unit?: string; summary: string; error: string | null };

// ---- setup / health ----------------------------------------------------------------------------------------------
export const getHardware = callable<[], Hardware>("get_hardware");
export const getHealth = callable<[force?: boolean], Health[]>("get_health");
export const collectLogs = callable<[], string>("collect_logs");
export const getSetting = callable<[key: string], any>("get_setting");
export const setSetting = callable<[key: string, value: any], boolean>("set_setting");
export const setDryRun = callable<[on: boolean], boolean>("set_dry_run");
export type Fix = { ok: boolean; error?: string; log?: string; path?: string; next?: string; dry_run?: boolean; would?: string; note?: string | null; removed?: string[] };
export const runFix = callable<[fix: string, allowUnpinned: boolean], Fix>("run_fix");
export type SelfTest = { ok: boolean; passed?: number; failed?: number; lines?: string[]; summary?: string; error: string | null };
export const selfTest = callable<[], SelfTest>("self_test");

// ---- power ---------------------------------------------------------------------------------------------------------
export const getPower = callable<[], PowerStatus>("get_power");
export type ProfileRead = { tdp_w: number | null; pl2_w: number | null; cpu_max_pct: number | null; epp: string | null; backend: string | null };
export const applyProfile = callable<[name: string, appid: string | null], WriteResult & { profile: string; applied: any; failed: string[]; read: ProfileRead }>("apply_profile");
export const listProfiles = callable<[], Record<string, { tdp?: number; cpu_max_pct?: number; note?: string }>>("list_profiles");
export const getGameProfile = callable<[appid: string], { battery: GameProfile; plugged: GameProfile; launch: Launch }>("get_game_profile");
export const setTargetFps = callable<[appid: string, fps: number], { ok: boolean; target_fps: number }>("set_target_fps");
export const gameStarted = callable<[appid: string], any>("game_started");
export const gameStopped = callable<[appid: string], any>("game_stopped");
export const powerSourceChanged = callable<[plugged: boolean], any>("power_source_changed");
export const resumed = callable<[], any>("resumed");
export const getAutotune = callable<[], AutotuneStatus>("get_autotune");

// ---- per game ------------------------------------------------------------------------------------------------------
export type Launch = { mesa?: string; xmx?: boolean; xell?: boolean };
export const getLaunch = callable<[appid: string], { launch: Launch; env: Record<string, string>; wrapper: string; error?: string | null }>("get_launch");
export const setLaunch = callable<[appid: string, values: Partial<Launch>], { ok: boolean; error?: string; launch: Launch; env: Record<string, string>; wrapper: string }>("set_launch");
export type XessState = { installed: boolean; package_dir: string | null; version: string | null; source: { repo: string; tag: string } | null; drivers: string[];
  default_driver: string | null; status: { state: string; line: string | null; mode?: string }; xell_capable: boolean; official_proton: string[] };
export type XellState = { mode: "driver" | "cross-vendor" | "none" | "off"; shim: boolean; detail: string };
export type GameStatus = { launch: Launch; xess: XessState; xell: XellState; wrapper: string };
export const gameStatus = callable<[appid: string], GameStatus>("game_status");

// ---- setup: installs ---------------------------------------------------------------------------------------------
export const xessState = callable<[], XessState>("xess_state");
export const xessInstall = callable<[allowUnpinned: boolean], Fix & { tag?: string; repo?: string }>("xess_install");
export const xessProbe = callable<[], Fix & { state?: XessState }>("xess_probe");

// ---- fixes -----------------------------------------------------------------------------------------------------------
export type WideVrr = { panel: string | null; panel_name: string | null; range: number[] | null; wide: boolean; supported: boolean;
  reason: string | null; pending: "on" | "off" | null; rpm_ostree: boolean };
export type ButtonsState = { applies: boolean; ok: boolean; installed: boolean; stock_map: string | null; upstream_fixed?: boolean; legacy_hwdb: string[] };
export type Fixes = { vrr: WideVrr; buttons: ButtonsState; health: { ok: boolean; total: number; problems: number; line: string } };
export const getFixes = callable<[], Fixes>("get_fixes");
export const setWideVrr = callable<[on: boolean], { ok: boolean; error?: string; restart?: boolean; dry_run?: boolean; would?: string }>("set_wide_vrr");
export type Changes = { first_run: boolean; changed: { what: string; key: string; from: string | null; to: string | null }[]; failing?: { title: string; detail: string }[] };
export const getChanges = callable<[], Changes>("get_changes");
