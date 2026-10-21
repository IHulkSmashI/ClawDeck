import { callable } from "@decky/api";

export type Health = { id: string; title: string; ok: boolean; detail: string; fix: string | null };
export type PowerStatus = {
  gpu: { available: boolean; min_mhz: number | null; max_mhz: number | null; cur_mhz: number | null; hw_min_mhz: number | null; hw_max_mhz: number | null; efficient_mhz: number | null };
  cpu: { available: boolean; max_perf_pct: number | null; min_perf_pct: number | null; no_turbo: number | null };
  rapl: { available: boolean; pl1_w: number | null; pl2_w: number | null };
  tdp: { mode: string; custom_tdp: number; boost: boolean; ready: boolean } | null;
  hhd: boolean; msi_firmware: boolean;
  limits: { min: number; battery_max: number; plugged_max: number; sppt_max: number };
};
export type BatteryStatus = {
  present: boolean; status?: string; capacity_pct?: number; power_w?: number | null; energy_wh?: number | null;
  energy_full_wh?: number | null; health_pct?: number | null; plugged?: boolean | null;
  charge_limit_supported?: boolean; charge_limit?: number | null;
};
export type Hardware = { product: string; known: boolean; kernel: string; cpu_model: string; gpu_driver: string | null; model: { name: string; soc: string; gpu: string; xmx: boolean } };
export type GameProfile = { base?: string; tdp?: number; cpu_max_pct?: number; gpu_min_mhz?: number; gpu_max_mhz?: number; target_fps?: number; autotune?: boolean; learned?: any };
export type AutotuneStatus = { running: boolean; enabled: boolean; appid?: string; target_fps?: number; learned?: any; paused?: boolean; last?: any };

export const getHardware = callable<[], Hardware>("get_hardware");
export const getHealth = callable<[], Health[]>("get_health");
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

export const injectorState = callable<[gameDir: string], any>("injector_state");
export const injectorInstall = callable<[gameDir: string, mode: string, xessFg: boolean], any>("injector_install");
