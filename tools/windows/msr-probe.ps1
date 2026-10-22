<#
  MSI Claw 8 EX on Windows: can the package power limit be lowered through MSR 0x610 (PawnIO, signed driver),
  underneath whatever ClawTweaks / MSI Center set? This decides how the Windows autotune writes the TDP.

  What it does (about 40 s, fans may spin up, nothing is left changed):
    1. reads the RAPL units (0x606), the limit (0x610) and the package energy counter (0x611)
    2. measures package power under an all-core load at the current limit
    3. writes PL1 12 W / PL2 15 W into 0x610 (all other bits kept), measures again
    4. writes the original 0x610 back, reads it back, measures a third time
  Prints a table and saves it to msr-probe.txt on your Desktop.

  Needs: PawnIO installed (ClawTweaks' "Install PawnIO" button, or https://pawnio.eu), and an
  Administrator PowerShell:   powershell -ExecutionPolicy Bypass -File .\msr-probe.ps1
#>
$ErrorActionPreference = 'Stop'
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { Write-Host $s; $out.Add($s) }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Run this from an Administrator PowerShell."; exit 1
}

# ---- PawnIOLib.dll --------------------------------------------------------------------------------------------
$lib = $null
foreach ($k in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO',
               'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO') {
    $loc = (Get-ItemProperty $k -ErrorAction SilentlyContinue).InstallLocation
    if ($loc -and (Test-Path (Join-Path $loc 'PawnIOLib.dll'))) { $lib = Join-Path $loc 'PawnIOLib.dll'; break }
}
if (-not $lib) {
    foreach ($p in "$env:ProgramFiles\PawnIO\PawnIOLib.dll", "${env:ProgramFiles(x86)}\PawnIO\PawnIOLib.dll") {
        if (Test-Path $p) { $lib = $p; break }
    }
}
if (-not $lib) { Write-Host "PawnIO is not installed (no PawnIOLib.dll). Install it from ClawTweaks or https://pawnio.eu, then run again."; exit 1 }

# ---- IntelMSR.bin (signed module from namazso/PawnIO.Modules 0.2.11) -----------------------------------------
$work = Join-Path $env:TEMP 'claw-msr-probe'
New-Item -ItemType Directory -Force $work | Out-Null
$bin = Get-ChildItem $work -Recurse -Filter IntelMSR.bin -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $bin) {
    $zip = Join-Path $work 'modules.zip'
    Invoke-WebRequest -UseBasicParsing 'https://github.com/namazso/PawnIO.Modules/releases/download/0.2.11/release_0_2_11.zip' -OutFile $zip
    Expand-Archive -Force $zip (Join-Path $work 'modules')
    $bin = Get-ChildItem $work -Recurse -Filter IntelMSR.bin | Select-Object -First 1
}
if (-not $bin) { Write-Host "IntelMSR.bin not found in the PawnIO.Modules release."; exit 1 }

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Pawn {
    [DllImport("kernel32", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr LoadLibrary(string p);
    [DllImport("kernel32", CharSet=CharSet.Ansi)] static extern IntPtr GetProcAddress(IntPtr h, string n);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int OpenFn(out IntPtr h);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int LoadFn(IntPtr h, byte[] blob, UIntPtr size);
    [UnmanagedFunctionPointer(CallingConvention.StdCall, CharSet=CharSet.Ansi)]
    delegate int ExecFn(IntPtr h, string name, ulong[] inp, UIntPtr inSize, ulong[] outp, UIntPtr outSize, out UIntPtr ret);
    static ExecFn exec; static IntPtr handle;
    public static void Init(string dll, byte[] module) {
        IntPtr m = LoadLibrary(dll);
        if (m == IntPtr.Zero) throw new Exception("cannot load " + dll);
        var open = (OpenFn)Marshal.GetDelegateForFunctionPointer(GetProcAddress(m, "pawnio_open"), typeof(OpenFn));
        var load = (LoadFn)Marshal.GetDelegateForFunctionPointer(GetProcAddress(m, "pawnio_load"), typeof(LoadFn));
        exec = (ExecFn)Marshal.GetDelegateForFunctionPointer(GetProcAddress(m, "pawnio_execute"), typeof(ExecFn));
        int hr = open(out handle); if (hr < 0) throw new Exception("pawnio_open 0x" + hr.ToString("x8"));
        hr = load(handle, module, (UIntPtr)module.Length); if (hr < 0) throw new Exception("pawnio_load 0x" + hr.ToString("x8"));
    }
    public static ulong Read(uint msr) {
        var o = new ulong[1]; UIntPtr r;
        int hr = exec(handle, "ioctl_read_msr", new ulong[] { msr }, (UIntPtr)1, o, (UIntPtr)1, out r);
        if (hr < 0) throw new Exception("read 0x" + msr.ToString("x") + ": 0x" + hr.ToString("x8"));
        return o[0];
    }
    public static void Write(uint msr, ulong v) {
        UIntPtr r;
        int hr = exec(handle, "ioctl_write_msr", new ulong[] { msr, v }, (UIntPtr)2, new ulong[0], UIntPtr.Zero, out r);
        if (hr < 0) throw new Exception("write 0x" + msr.ToString("x") + ": 0x" + hr.ToString("x8"));
    }
    // bit math here, not in PowerShell 5.1 (which turns UInt64 with bit 63 into Int64/double)
    public static double Pl1(ulong v, double pu) { return (v & 0x7FFF) * pu; }
    public static double Pl2(ulong v, double pu) { return ((v >> 32) & 0x7FFF) * pu; }
    public static bool Bit(ulong v, int b) { return ((v >> b) & 1) == 1; }
    public static ulong Encode(ulong orig, double w1, double w2, double pu) {
        ulong mask = 0x7FFFUL | (0x7FFFUL << 32);
        ulong v = orig & ~mask;
        v |= (ulong)Math.Round(w1 / pu) & 0x7FFF;
        v |= ((ulong)Math.Round(w2 / pu) & 0x7FFF) << 32;
        return v | (1UL << 15) | (1UL << 47);
    }
    public static ulong Energy() { return Read(0x611) & 0xFFFFFFFFUL; }
}
public static class Burn {
    static volatile bool run;
    public static void Start() {
        run = true;
        for (int i = 0; i < Environment.ProcessorCount; i++) {
            var t = new System.Threading.Thread(() => { long x = 0; while (run) { x++; } });
            t.IsBackground = true; t.Start();
        }
    }
    public static void Stop() { run = false; }
}
"@
[Pawn]::Init($lib, [IO.File]::ReadAllBytes($bin.FullName))

# ---- units and current limit ----------------------------------------------------------------------------------
$units = [Pawn]::Read(0x606)
$pu = 1.0 / [math]::Pow(2, [int]($units % 16))                    # watts per power unit (bits 3:0)
$eu = 1.0 / [math]::Pow(2, [int]([math]::Floor($units / 256) % 32)) # joules per energy unit (bits 12:8)
function Decode($v) {
    "PL1 {0:N1} W (on={1})  PL2 {2:N1} W (on={3})  locked={4}" -f [Pawn]::Pl1($v, $pu), [Pawn]::Bit($v, 15),
        [Pawn]::Pl2($v, $pu), [Pawn]::Bit($v, 47), [Pawn]::Bit($v, 63)
}
function Measure-Watts([int]$seconds = 6) {
    [Burn]::Start()
    try {
        Start-Sleep -Milliseconds 2500
        $a = [double][Pawn]::Energy(); $t = [Diagnostics.Stopwatch]::StartNew()
        Start-Sleep -Seconds $seconds
        $b = [double][Pawn]::Energy(); $s = $t.Elapsed.TotalSeconds
    } finally { [Burn]::Stop() }
    if ($b -lt $a) { $b += 4294967296.0 }                    # 32-bit counter wrapped
    [math]::Round(($b - $a) * $eu / $s, 1)
}

$orig = [Pawn]::Read(0x610)
Say ("Claw MSR probe  {0}  {1}" -f (Get-Date -Format s), (Get-CimInstance Win32_Processor).Name)
Say ("power unit {0} W, energy unit {1} uJ" -f $pu, [math]::Round($eu * 1e6, 2))
Say ("0x610 now:      " + (Decode $orig) + ("  raw 0x{0:x16}" -f $orig))
if ([Pawn]::Bit($orig, 63)) { Say "0x610 is locked by the firmware: MSR writes cannot change the limit."; $out | Set-Content "$env:USERPROFILE\Desktop\msr-probe.txt"; exit 0 }

Say ("load at current limit:      {0} W" -f (Measure-Watts))
try {
    [Pawn]::Write(0x610, [Pawn]::Encode($orig, 12, 15, $pu))
    Say ("0x610 after write: " + (Decode ([Pawn]::Read(0x610))))
    Say ("load at MSR 12/15 W:        {0} W   (works if this is about 15 W or less)" -f (Measure-Watts))
} finally {
    [Pawn]::Write(0x610, $orig)
}
$back = [Pawn]::Read(0x610)
Say ("0x610 restored: " + (Decode $back) + $(if ($back -eq $orig) { "  (identical)" } else { "  (DIFFERENT from the original)" }))
Say ("load after restore:         {0} W" -f (Measure-Watts))
Say ("ClawTweaks / MSI Center TDP setting at the time: write it here by hand -> ")
$out | Set-Content "$env:USERPROFILE\Desktop\msr-probe.txt"
Write-Host "`nSaved to Desktop\msr-probe.txt"
