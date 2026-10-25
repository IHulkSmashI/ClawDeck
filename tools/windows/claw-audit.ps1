<#
  MSI Claw on Windows: gaming check-up. READ-ONLY: it changes nothing.
  Collects what matters for gaming, battery and frame pacing, flags what looks off, and saves a report to
  Desktop\claw-audit.txt (paste or attach it for review).

  Run from an Administrator PowerShell (some checks need it):
      powershell -ExecutionPolicy Bypass -File .\claw-audit.ps1
#>
$ErrorActionPreference = 'SilentlyContinue'
$out = New-Object System.Collections.Generic.List[string]
$flags = New-Object System.Collections.Generic.List[string]
function Say($s) { Write-Host $s; $out.Add($s) }
function Head($s) { Say ""; Say "==== $s" }
function Flag($s) { $flags.Add($s) }
function Reg($path, $name) { (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
          [Security.Principal.WindowsBuiltInRole]::Administrator)

Head "System"
$cs = Get-CimInstance Win32_ComputerSystem; $os = Get-CimInstance Win32_OperatingSystem
$bios = Get-CimInstance Win32_BIOS; $cpu = Get-CimInstance Win32_Processor
Say ("{0} {1} | BIOS {2} ({3:yyyy-MM-dd})" -f $cs.Manufacturer, $cs.Model, $bios.SMBIOSBIOSVersion, $bios.ReleaseDate)
Say ("{0} | build {1}.{2} | admin: {3}" -f $os.Caption, $os.BuildNumber, (Reg 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' UBR), $admin)
Say ("CPU {0} | RAM {1:N1} GB | uptime {2:N1} h" -f $cpu.Name.Trim(), ($cs.TotalPhysicalMemory/1GB), ((Get-Date) - $os.LastBootUpTime).TotalHours)

Head "Graphics driver and display"
foreach ($g in Get-CimInstance Win32_VideoController) {
    Say ("{0} | driver {1} ({2:yyyy-MM-dd}) | {3}x{4} @ {5} Hz" -f $g.Name, $g.DriverVersion, $g.DriverDate,
         $g.CurrentHorizontalResolution, $g.CurrentVerticalResolution, $g.CurrentRefreshRate)
    if ($g.DriverDate -and ((Get-Date) - $g.DriverDate).TotalDays -gt 60) { Flag "Graphics driver is over 2 months old: check Intel's latest Arc driver." }
}
$hags = Reg 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' HwSchMode
Say ("Hardware-accelerated GPU scheduling: {0}" -f $(switch ($hags) { 2 {'on'} 1 {'off'} default {'default'} }))
if ($hags -ne 2) { Flag "Hardware-accelerated GPU scheduling is off: XeSS frame generation wants it on." }
$dx = Reg 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences' DirectXUserGlobalSettings
Say ("DirectX global settings (windowed optimizations / auto HDR / VRR): {0}" -f $(if ($dx) { $dx } else { 'defaults' }))
if ($dx -and $dx -match 'SwapEffectUpgradeEnable=0') { Flag "Optimizations for windowed games is off: turn it on (lower latency in borderless)." }
if ($dx -and $dx -match 'VRROptimizeEnable=0') { Flag "Variable refresh rate optimization is off." }
$mpo = Reg 'HKLM:\SOFTWARE\Microsoft\Windows\Dwm' OverlayTestMode
if ($mpo -eq 5) { Say "Multiplane overlay: DISABLED by registry"; Flag "Multiplane overlay is disabled (OverlayTestMode=5): usually costs latency in borderless games; remove unless it fixes a flicker." }

Head "Power"
Say ("Active plan: " + ((powercfg /getactivescheme) -join ' '))
$ovl = Reg 'HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes' ActiveOverlayAcPowerScheme
Say ("Power mode overlay (AC): {0}" -f $(switch ($ovl) { 'ded574b5-45a0-4f42-8737-46345c09c238' {'Best performance'} '961cc777-2547-4f9d-8174-7d86181b8a7a' {'Best power efficiency'} default { if ($ovl) { $ovl } else { 'Balanced' } } }))
$bat = Get-CimInstance Win32_Battery
if ($bat) {
    Say ("Battery: {0}% | status {1}" -f $bat.EstimatedChargeRemaining, $bat.BatteryStatus)
    $rep = Join-Path $env:TEMP 'claw-battery.xml'; powercfg /batteryreport /xml /output $rep | Out-Null
    if (Test-Path $rep) {
        [xml]$x = Get-Content $rep; $b = $x.BatteryReport.Batteries.Battery
        if ($b.DesignCapacity -gt 0) {
            $h = [math]::Round(100.0 * $b.FullChargeCapacity / $b.DesignCapacity)
            Say ("Battery health: {0}% of design ({1} / {2} mWh), {3} cycles" -f $h, $b.FullChargeCapacity, $b.DesignCapacity, $b.CycleCount)
            if ($h -lt 85) { Flag "Battery holds $h% of its design capacity." }
        }
    }
}
$sleepstudy = powercfg /a 2>$null
Say ("Sleep states: " + (($sleepstudy | Select-String 'Standby|Hibernate' | Select-Object -First 3) -join ' | '))

Head "Game Mode, Game Bar, recording"
$gm = Reg 'HKCU:\Software\Microsoft\GameBar' AutoGameModeEnabled
Say ("Game Mode: {0}" -f $(if ($gm -eq 0) { 'off' } else { 'on' }))
if ($gm -eq 0) { Flag "Game Mode is off: on keeps Windows Update and background work out of games." }
$dvr = Reg 'HKCU:\System\GameConfigStore' GameDVR_Enabled; $cap = Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR' AppCaptureEnabled
Say ("Background recording (Game DVR): {0} | app capture: {1}" -f $(if ($dvr -eq 0) {'off'} else {'on'}), $(if ($cap -eq 0) {'off'} else {'on'}))
if ($dvr -ne 0 -or $cap -ne 0) { Flag "Xbox Game Bar capture/background recording is on: costs GPU time and battery. Turn off in Settings > Gaming > Captures." }

Head "Security features that cost frames (report only; changing them is your call)"
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard
if ($dg) {
    $vbs = @('off', 'enabled, not running', 'running')[[int]$dg.VirtualizationBasedSecurityStatus]
    $hvci = $dg.SecurityServicesRunning -contains 2
    Say ("Virtualization-based security: {0} | Memory integrity (HVCI): {1}" -f $vbs, $(if ($hvci) {'running'} else {'off'}))
    if ($hvci) { Flag "Memory integrity (HVCI) is running: typically 5-10% in CPU-heavy games. A security trade-off; only you decide." }
}
$sac = Reg 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' VerifiedAndReputablePolicyState
Say ("Smart App Control: {0}" -f $(switch ($sac) { 0 {'off'} 1 {'on'} 2 {'evaluation'} default {'unknown'} }))

Head "Startup apps"
$run = @()
foreach ($k in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run',
               'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run') {
    $p = Get-ItemProperty $k; if ($p) { $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $run += "$($_.Name)  <-  $k" } }
}
Get-CimInstance Win32_StartupCommand | Where-Object { $_.Location -like '*Startup*' } | ForEach-Object { $run += "$($_.Name)  <-  Startup folder" }
$run | ForEach-Object { Say "  $_" }
Say ("{0} startup entries" -f $run.Count)
if ($run.Count -gt 12) { Flag "$($run.Count) startup entries: worth trimming." }
Say "Scheduled tasks at logon (third-party):"
Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and $_.TaskPath -notlike '\Microsoft\*' -and ($_.Triggers | Where-Object { $_.CimClass.CimClassName -match 'Logon|Boot' }) } |
    ForEach-Object { Say "  $($_.TaskPath)$($_.TaskName)" }

Head "Running processes that matter for games"
$watch = 'RTSS|MSIAfterburner|Overwolf|Discord|Steam|EpicGamesLauncher|EADesktop|upc|Battle.net|GalaxyClient|MSI.Center|MSI_Central|ClawTweaks|OneDrive|Teams|Wallpaper|iCUE|Armoury|HandheldCompanion|PhawxON|NVIDIA|RadeonSoftware|IntelGraphicsSoftware|IGCC|XboxGameBar|GameBar|obs64|Rewind|Copilot|widgets|SearchHost|PhoneExperienceHost'
Get-Process | Where-Object { $_.ProcessName -match $watch } | Sort-Object ProcessName -Unique |
    ForEach-Object { Say ("  {0,-32} {1,7:N0} MB" -f $_.ProcessName, ($_.WorkingSet64 / 1MB)) }
$overlays = Get-Process | Where-Object { $_.ProcessName -match 'RTSS|Overwolf|Discord|obs64|MSIAfterburner' }
if (($overlays | Select-Object -Unique ProcessName).Count -gt 1) { Flag "More than one overlay/hooking app runs (e.g. RTSS + Discord + Overwolf): each hook can cause stutter with frame generation; keep one." }
Say "Top memory users:"
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 | ForEach-Object { Say ("  {0,-32} {1,7:N0} MB" -f $_.ProcessName, ($_.WorkingSet64 / 1MB)) }

Head "Services worth knowing about"
foreach ($s in 'SysMain', 'WSearch', 'DiagTrack', 'XblGameSave', 'MSI_Center_Service', 'MSI_Central_Service', 'IntelGraphicsSoftwareService', 'Intel(R) TPM Provisioning Service') {
    $x = Get-Service -Name $s; if ($x) { Say ("  {0,-34} {1,-8} {2}" -f $x.Name, $x.Status, $x.StartType) }
}

Head "Storage"
Get-PhysicalDisk | ForEach-Object { Say ("  {0} | {1} | {2:N0} GB | health {3}" -f $_.FriendlyName, $_.MediaType, ($_.Size/1GB), $_.HealthStatus) }
Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
    $free = if ($_.Size) { 100.0 * $_.SizeRemaining / $_.Size } else { 100 }
    Say ("  {0}: {1:N0} GB free of {2:N0} GB ({3:N0}%)" -f $_.DriveLetter, ($_.SizeRemaining/1GB), ($_.Size/1GB), $free)
    if ($_.DriveLetter -eq 'C' -and $free -lt 15) { Flag "C: has under 15% free: shader caches and updates need room." }
}
Say ("TRIM: " + ((fsutil behavior query DisableDeleteNotify | Select-Object -First 1) -join ''))
$pf = Get-CimInstance Win32_PageFileUsage; if ($pf) { Say ("Page file: {0} ({1} MB allocated)" -f $pf.Name, $pf.AllocatedBaseSize) } else { Flag "No page file: some games crash or stutter without one." }

Head "Windows Update"
$wu = New-Object -ComObject Microsoft.Update.Session
if ($wu) {
    $pending = $wu.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0").Updates
    Say ("Pending updates: {0}" -f $pending.Count); foreach ($u in $pending) { Say "  $($u.Title)" }
    if ($pending.Count) { Flag "$($pending.Count) Windows/driver update(s) waiting." }
}
if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { Flag "A restart is pending from Windows Update." }

Head "Gaming tools found"
$apps = Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' |
        Where-Object { $_.DisplayName -match 'Intel.*Graphics|Arc|MSI Center|ClawTweaks|PawnIO|RivaTuner|Afterburner|OptiClick|Handheld Companion|Steam|Xbox|DirectX|Visual C\+\+ 20|\.NET.*Runtime' } |
        Sort-Object DisplayName -Unique
$apps | ForEach-Object { Say ("  {0}  {1}" -f $_.DisplayName, $_.DisplayVersion) }
if (-not ($apps | Where-Object { $_.DisplayName -match 'Visual C\+\+ 2015-20\d\d.*x64' })) { Flag "No x64 Visual C++ 2015-2022 runtime found: many games and OptiScaler need it." }

Head "OptiScaler installs (game folders of Steam libraries)"
$steam = Reg 'HKCU:\Software\Valve\Steam' SteamPath
if ($steam) {
    $libs = @($steam)
    $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
    if (Test-Path $vdf) { $libs += (Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' }) }
    foreach ($l in ($libs | Sort-Object -Unique)) {
        Get-ChildItem (Join-Path $l 'steamapps\common') -Recurse -Depth 4 -Filter 'OptiScaler.ini' |
            ForEach-Object { Say ("  {0}  ({1:yyyy-MM-dd})" -f $_.DirectoryName, $_.LastWriteTime) }
    }
}

Say ""; Say "==== Things to look at"
if ($flags.Count) { $flags | ForEach-Object { Say "  - $_" } } else { Say "  nothing obvious" }
$path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'claw-audit.txt'
$out | Set-Content -Encoding UTF8 $path
Write-Host "`nSaved to $path"
