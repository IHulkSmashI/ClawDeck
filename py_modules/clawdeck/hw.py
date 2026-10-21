"""Hardware detection: which Claw this is and which control paths the kernel exposes.

One table, one row per model. A new Claw is one new row. Everything else in ClawDeck asks this module for
capabilities instead of hard-coding paths, so Lunar Lake and Meteor Lake Claws (and, with a new backend row, AMD
handhelds) get the same features where the kernel offers them.

Sources: Handheld Daemon's device table (hhd/device/claw/const.py) for the DMI product names and button layout, its
MSI adjustor driver for the TDP firmware attributes and fan hwmon, PowerControl for the Intel xe clock paths.
"""
from __future__ import annotations

import glob
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field, asdict

from . import sysfs

# ---- the model table -------------------------------------------------------------------------------------------------
# DMI board name (/sys/class/dmi/id/board_name, the MSI board id) -> model facts that the kernel does not tell us.
# The product name is a marketing string on new Claws ("Claw 8 EX AI+ CG3EM Launch Pack" on the EX), so the board id
# is matched first; older firmware put the board id in product_name, which is still tried second.
MODELS: dict[str, dict] = {
    "MS-1T41": {"name": "MSI Claw A1M", "soc": "Meteor Lake", "gpu": "Arc (Xe-LPG)", "xmx": False,
                "tdp": {"min": 8, "battery_max": 35, "plugged_max": 43, "sppt_max": 45}, "panel_hz": [60, 120]},
    "MS-1T42": {"name": "MSI Claw 7 AI+", "soc": "Lunar Lake", "gpu": "Arc 140V (Xe2)", "xmx": True,
                "tdp": {"min": 8, "battery_max": 30, "plugged_max": 30, "sppt_max": 37}, "panel_hz": [60, 120]},
    "MS-1T52": {"name": "MSI Claw 8 AI+", "soc": "Lunar Lake", "gpu": "Arc 140V (Xe2)", "xmx": True,
                "tdp": {"min": 8, "battery_max": 30, "plugged_max": 30, "sppt_max": 37}, "panel_hz": [60, 120]},
    "MS-1T91": {"name": "MSI Claw 8 EX AI+", "soc": "Panther Lake", "gpu": "Arc B390 (Xe3)", "xmx": True,
                "tdp": {"min": 8, "battery_max": 35, "plugged_max": 35, "sppt_max": 45}, "panel_hz": [60, 120],
                "vrr": [30, 120]},
    "MS-1T8K": {"name": "MSI Claw A8", "soc": "AMD Ryzen Z2 Extreme", "gpu": "Radeon 890M", "xmx": False,
                "tdp": {"min": 7, "battery_max": 35, "plugged_max": 35, "sppt_max": 40}, "panel_hz": [60, 120]},
}

# recommended profiles per model, watts (TDP = PL1 sustained; the boost limit PL2 follows from it). Four steps, lowest
# to highest power: battery (most frames per watt), balanced (a middle step for heavier games on battery), performance
# (plugged in, handheld) and dock (the firmware ceiling). Starting points until Benchmark numbers from a Claw replace
# them.
PROFILES: dict[str, dict[str, dict]] = {
    "default": {
        "battery":     {"tdp": 20, "cpu_max_pct": 80,  "gpu_min_mhz": None, "epp": "balance_power", "note": "best frames per watt"},
        "balanced":    {"tdp": 25, "cpu_max_pct": 90,  "gpu_min_mhz": None, "epp": "balance_performance", "note": "more frames, still on battery"},
        "performance": {"tdp": 30, "cpu_max_pct": 100, "gpu_min_mhz": None, "epp": "balance_performance", "note": "plugged in, handheld"},
        "dock":        {"tdp": 35, "cpu_max_pct": 100, "gpu_min_mhz": None, "epp": "performance", "note": "docked, fans audible"},
    },
    "MS-1T91": {
        "battery":     {"tdp": 20, "cpu_max_pct": 80,  "gpu_min_mhz": None, "epp": "balance_power", "note": "best frames per watt"},
        "balanced":    {"tdp": 25, "cpu_max_pct": 90,  "gpu_min_mhz": None, "epp": "balance_performance", "note": "more frames, still on battery"},
        "performance": {"tdp": 30, "cpu_max_pct": 100, "gpu_min_mhz": None, "epp": "balance_performance", "note": "plugged in, handheld"},
        "dock":        {"tdp": 35, "cpu_max_pct": 100, "gpu_min_mhz": None, "epp": "performance", "note": "35 W sustained / 45 W boost on the dock"},
    },
}


@dataclass
class Paths:
    """Control paths discovered on this machine. None = not available on this kernel/model."""
    # Intel xe / i915 GPU clocks (PowerControl's discovery, extended with cur/rp0/rpe/rpn)
    gpu_card: str | None = None
    gpu_min_freq: str | None = None
    gpu_max_freq: str | None = None
    gpu_cur_freq: str | None = None
    gpu_rp0_freq: str | None = None      # hardware maximum
    gpu_rpe_freq: str | None = None      # efficient frequency
    gpu_rpn_freq: str | None = None      # hardware minimum
    gpu_busy: str | None = None          # not exposed by xe; PowerStation's GpuBusyPercent is read instead (shadowblip.py)
    # CPU
    cpu_max_perf_pct: str | None = None
    cpu_min_perf_pct: str | None = None
    cpu_no_turbo: str | None = None
    cpu_boost: str | None = None         # cpufreq boost (AMD / acpi-cpufreq)
    cpu_governor: list[str] = field(default_factory=list)
    cpu_epp: list[str] = field(default_factory=list)   # intel_pstate energy_performance_preference, one per CPU
    # package power
    rapl_package: str | None = None      # intel-rapl-mmio:0 or intel-rapl:0 directory
    rapl_pl1: str | None = None
    rapl_pl2: str | None = None
    rapl_energy: str | None = None       # energy_uj for power readings
    # MSI firmware (what Handheld Daemon drives)
    msi_pl1: str | None = None
    msi_pl2: str | None = None
    msi_pl3: str | None = None
    msi_fan_hwmon: str | None = None
    # battery
    battery: str | None = None
    charge_limit: str | None = None
    # daemon sockets
    hhd_api: str | None = None


@dataclass
class Hardware:
    product: str
    model: dict
    known: bool
    kernel: str
    cpu_model: str
    gpu_driver: str | None
    paths: Paths
    model_id: str = ""      # the MODELS key that matched (board id), "" when unknown

    @property
    def model_key(self) -> str:
        """Key for per-model tables (PROFILES): the matched board id, else the raw product name."""
        return self.model_id or self.product

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _first(globpat: str) -> str | None:
    hits = sorted(glob.glob(globpat))
    return hits[0] if hits else None


def discover_paths() -> Paths:
    p = Paths()
    # GPU: the first Intel card with xe-style clock files, else i915-style
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]")):
        vendor = sysfs.read(f"{card}/device/vendor")
        if vendor and vendor.lower() != "0x8086":
            continue
        # gt0 is the graphics GT; gt1 on Panther Lake is the media GT and is never touched
        xe = f"{card}/device/tile0/gt0/freq0" if os.path.isdir(f"{card}/device/tile0/gt0/freq0") else \
            _first(f"{card}/device/tile0/gt*/freq0")
        if xe:
            p.gpu_card = card
            for name in ("min_freq", "max_freq", "cur_freq", "rp0_freq", "rpe_freq", "rpn_freq"):
                fp = f"{xe}/{name}"
                if os.path.exists(fp):
                    setattr(p, f"gpu_{name}", fp)
            break
        if os.path.exists(f"{card}/gt_max_freq_mhz"):
            p.gpu_card = card
            p.gpu_min_freq = f"{card}/gt_min_freq_mhz"
            p.gpu_max_freq = f"{card}/gt_max_freq_mhz"
            p.gpu_cur_freq = f"{card}/gt_cur_freq_mhz" if os.path.exists(f"{card}/gt_cur_freq_mhz") else None
            p.gpu_rp0_freq = f"{card}/gt_RP0_freq_mhz" if os.path.exists(f"{card}/gt_RP0_freq_mhz") else None
            p.gpu_rpn_freq = f"{card}/gt_RPn_freq_mhz" if os.path.exists(f"{card}/gt_RPn_freq_mhz") else None
            break
    # CPU
    for name in ("max_perf_pct", "min_perf_pct", "no_turbo"):
        fp = f"/sys/devices/system/cpu/intel_pstate/{name}"
        if os.path.exists(fp):
            setattr(p, f"cpu_{name}", fp)
    if os.path.exists("/sys/devices/system/cpu/cpufreq/boost"):
        p.cpu_boost = "/sys/devices/system/cpu/cpufreq/boost"
    p.cpu_governor = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor"))
    p.cpu_epp = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/energy_performance_preference"))
    # RAPL package domain; the MMIO interface first, as PowerStation does (it is the one the SoC firmware honours on
    # recent Intel; the MSR one can be overridden by the platform)
    for d in sorted(glob.glob("/sys/class/powercap/intel-rapl-mmio:[0-9]")) + sorted(glob.glob("/sys/class/powercap/intel-rapl:[0-9]")):
        if (sysfs.read(f"{d}/name") or "").startswith("package"):
            p.rapl_package = d
            for c in sorted(glob.glob(f"{d}/constraint_[0-9]_name")):
                n = sysfs.read(c) or ""
                idx = c.rsplit("_", 2)[-2]
                limit = f"{d}/constraint_{idx}_power_limit_uw"
                if n == "long_term":
                    p.rapl_pl1 = limit
                elif n == "short_term":
                    p.rapl_pl2 = limit
            if os.path.exists(f"{d}/energy_uj"):
                p.rapl_energy = f"{d}/energy_uj"
            break
    # MSI firmware attributes (msi-wmi-platform), the paths Handheld Daemon writes
    base = "/sys/class/firmware-attributes/msi-wmi-platform/attributes"
    for attr, key in (("ppt_pl1_spl", "msi_pl1"), ("ppt_pl2_sppt", "msi_pl2"), ("ppt_pl3_fppt", "msi_pl3")):
        fp = f"{base}/{attr}/current_value"
        if os.path.exists(fp):
            setattr(p, key, fp)
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if sysfs.read(f"{hw}/name") == "msi_wmi_platform":
            p.msi_fan_hwmon = hw
            break
    # battery
    for bat in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        p.battery = bat
        if os.path.exists(f"{bat}/charge_control_end_threshold"):
            p.charge_limit = f"{bat}/charge_control_end_threshold"
        break
    if os.path.exists("/run/hhd/api"):
        p.hhd_api = "/run/hhd/api"
    return p


def match_model(board: str, product: str) -> tuple[str, dict | None]:
    """(MODELS key, row) for this machine's DMI names: board id first, then the product name."""
    for key in (board.strip(), product.strip()):
        if key in MODELS:
            return key, MODELS[key]
    return "", None


def detect() -> Hardware:
    product = (sysfs.read("/sys/class/dmi/id/product_name") or "").strip()
    board = (sysfs.read("/sys/class/dmi/id/board_name") or "").strip()
    model_id, model = match_model(board, product)
    known = model is not None
    if model is None:
        model = {"name": f"Unknown ({product or board or 'no DMI name'})", "soc": "?", "gpu": "?", "xmx": False,
                 "tdp": {"min": 5, "battery_max": 25, "plugged_max": 30, "sppt_max": 30}, "panel_hz": [60]}
    cpu_model = ""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    paths = discover_paths()
    gpu_driver = None
    if paths.gpu_card:
        drv = os.path.realpath(f"{paths.gpu_card}/device/driver")
        gpu_driver = os.path.basename(drv) if drv else None
    return Hardware(product=product, model=model, known=known, kernel=platform.release(), cpu_model=cpu_model,
                    gpu_driver=gpu_driver, paths=paths, model_id=model_id)


def profiles_for(product: str) -> dict[str, dict]:
    return PROFILES.get(product, PROFILES["default"])


ICD_DIRS = ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d")


def intel_vulkan_lib() -> str | None:
    """The Intel Vulkan driver the loader would use: VK_DRIVER_FILES, else the installed Intel ICD json's
    library_path, else the usual library locations."""
    jsons = [j for j in os.environ.get("VK_DRIVER_FILES", "").split(":") if j]
    for d in ICD_DIRS:
        jsons += sorted(glob.glob(f"{d}/intel_icd*.json"))
    for j in jsons:
        m = re.search(r'"library_path"\s*:\s*"([^"]+)"', sysfs.read(j) or "")
        if m and "hasvk" not in m.group(1):
            lib = m.group(1) if os.path.isabs(m.group(1)) else os.path.join(os.path.dirname(j), m.group(1))
            if os.path.exists(lib):
                return lib
            for d in ("/usr/lib64", "/usr/lib/x86_64-linux-gnu", "/usr/lib"):   # bare file name in the json
                if os.path.exists(os.path.join(d, os.path.basename(lib))):
                    return os.path.join(d, os.path.basename(lib))
    for lib in ("/usr/lib64/libvulkan_intel.so", "/usr/lib/x86_64-linux-gnu/libvulkan_intel.so", "/usr/lib/libvulkan_intel.so"):
        if os.path.exists(lib):
            return lib
    return None


MESA_RE = re.compile(rb"Mesa (\d+\.\d+\.\d+(?:-[0-9a-z.]+)?)")


def mesa_version() -> str | None:
    """Mesa version of the active Intel Vulkan driver: the "Mesa X.Y.Z" string in the library, else the rpm that
    owns it (Bazzite and Fedora). None when no Intel Vulkan driver is installed or neither source says."""
    lib = intel_vulkan_lib()
    if not lib:
        return None
    try:
        with open(lib, "rb") as f:
            m = MESA_RE.search(f.read())
        if m:
            return m.group(1).decode()
    except OSError:
        pass
    try:
        r = subprocess.run(["rpm", "-qf", "--qf", "%{VERSION}", lib], capture_output=True, text=True, timeout=5,
                           check=False)
        v = r.stdout.strip()
        if r.returncode == 0 and re.fullmatch(r"\d+\.\d+(\.\d+)?[0-9a-z.~-]*", v):
            return v
    except (OSError, subprocess.SubprocessError):
        pass
    return None
