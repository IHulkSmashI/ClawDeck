<#
  MSI Claw 8 EX on Windows: can this chip be undervolted? READ-ONLY: it changes nothing.

  Asks the processor, through PawnIO's signed IntelMSR module (the driver ClawTweaks installs):
    1. MSR 0x194 (FLEX_RATIO) bit 20: is the overclocking interface locked by the firmware?
    2. The overclocking mailbox (MSR 0x150), "read capabilities" and "read voltage offset" for each
       voltage plane (CPU cores, GPU, cache, system agent). These are the same read commands ThrottleStop
       sends; a read command does not change any voltage.
  Prints the answer and saves it to Desktop\uv-probe.txt.

  Run from an Administrator PowerShell:   powershell -ExecutionPolicy Bypass -File .\uv-probe.ps1
#>
$ErrorActionPreference = 'Stop'
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { Write-Host $s; $out.Add($s) }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Host "Run this from an Administrator PowerShell."; exit 1 }

$lib = $null
foreach ($k in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO',
               'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO') {
    $loc = (Get-ItemProperty $k -ErrorAction SilentlyContinue).InstallLocation
    if ($loc -and (Test-Path (Join-Path $loc 'PawnIOLib.dll'))) { $lib = Join-Path $loc 'PawnIOLib.dll'; break }
}
if (-not $lib) { foreach ($p in "$env:ProgramFiles\PawnIO\PawnIOLib.dll", "${env:ProgramFiles(x86)}\PawnIO\PawnIOLib.dll") { if (Test-Path $p) { $lib = $p; break } } }
if (-not $lib) { Write-Host "PawnIO is not installed. Install it from ClawTweaks or https://pawnio.eu, then run again."; exit 1 }

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
    public static int TryWrite(uint msr, ulong v) {
        UIntPtr r;
        return exec(handle, "ioctl_write_msr", new ulong[] { msr, v }, (UIntPtr)2, new ulong[0], UIntPtr.Zero, out r);
    }
    // OC mailbox (MSR 0x150): bit 63 = run/busy, bits 39:32 = command, bits 42:40 = plane.
    // Answer: bits 39:32 = error code (0 = ok), bits 31:0 = data.
    public static ulong Mailbox(ulong cmd, ulong plane, out int writeHr) {
        ulong req = (1UL << 63) | (cmd << 32) | (plane << 40);
        writeHr = TryWrite(0x150, req);
        if (writeHr < 0) return 0;
        ulong v = 0;
        for (int i = 0; i < 50; i++) { v = Read(0x150); if ((v >> 63) == 0) break; System.Threading.Thread.Sleep(1); }
        return v;
    }
    public static int ErrorOf(ulong v) { return (int)((v >> 32) & 0xFF); }
    public static uint DataOf(ulong v) { return (uint)(v & 0xFFFFFFFFUL); }
    // voltage offset: bits 31:21 of the data, 11-bit signed, units of 1/1024 V
    public static double OffsetMv(uint data) { int raw = (int)((data >> 21) & 0x7FF); if ((raw & 0x400) != 0) raw -= 0x800; return raw * 1000.0 / 1024.0; }
}
"@
[Pawn]::Init($lib, [IO.File]::ReadAllBytes($bin.FullName))

Say ("Claw undervolt probe  {0}  {1}" -f (Get-Date -Format s), (Get-CimInstance Win32_Processor).Name)
$bios = Get-CimInstance Win32_BIOS
Say ("BIOS {0}" -f $bios.SMBIOSBIOSVersion)

$flex = [Pawn]::Read(0x194)
$ocLock = (($flex -shr 20) -band 1) -eq 1
Say ("MSR 0x194 FLEX_RATIO = 0x{0:x16}  overclocking lock bit (20): {1}" -f $flex, $(if ($ocLock) { 'SET (locked by firmware)' } else { 'clear' }))

$planes = @{ 0 = 'CPU cores'; 1 = 'GPU'; 2 = 'Cache (ring)'; 3 = 'System agent' }
$anyOffset = $false; $mailboxBlocked = $false
foreach ($p in 0..3) {
    $hr = 0
    $cap = [Pawn]::Mailbox(0x01, [uint64]$p, [ref]$hr)
    if ($hr -lt 0) { Say ("  {0,-14} mailbox write refused by the driver (0x{1:x8})" -f $planes[$p], $hr); $mailboxBlocked = $true; continue }
    $e = [Pawn]::ErrorOf($cap); $d = [Pawn]::DataOf($cap)
    if ($e -ne 0) { Say ("  {0,-14} capabilities: error {1} (not answered)" -f $planes[$p], $e); continue }
    $ratioOc = (($d -shr 8) -band 1) -eq 1; $vOverride = (($d -shr 9) -band 1) -eq 1; $vOffset = (($d -shr 10) -band 1) -eq 1
    $vo = [Pawn]::Mailbox(0x10, [uint64]$p, [ref]$hr)
    $off = if ([Pawn]::ErrorOf($vo) -eq 0) { "{0:N1} mV" -f [Pawn]::OffsetMv([Pawn]::DataOf($vo)) } else { "error $([Pawn]::ErrorOf($vo))" }
    Say ("  {0,-14} ratio OC: {1,-5} voltage override: {2,-5} voltage offset: {3,-5} current offset: {4}" -f $planes[$p], $ratioOc, $vOverride, $vOffset, $off)
    if ($vOffset) { $anyOffset = $true }
}

Say ""
if ($mailboxBlocked) { Say "Verdict: PawnIO's MSR module refused the mailbox register, so this can't be answered with it. Send this file back." }
elseif ($ocLock -and -not $anyOffset) { Say "Verdict: LOCKED. The firmware has locked overclocking and no plane accepts a voltage offset: undervolting is not possible on this BIOS." }
elseif ($anyOffset -and -not $ocLock) { Say "Verdict: UNLOCKED on the planes marked 'voltage offset: True'. An undervolt tool is possible, carefully, with auto-revert." }
else { Say "Verdict: mixed answer (lock bit and capabilities disagree). Send this file back for a closer look." }

$path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'uv-probe.txt'
$out | Set-Content -Encoding UTF8 $path
Write-Host "`nSaved to $path"
