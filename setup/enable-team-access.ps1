#Requires -RunAsAdministrator
<#
    enable-team-access.ps1  -  one-shot deploy for the NLC Dashboard
    ------------------------------------------------------------------
    Fixes the TWO things that block full team use, in one elevated run:

      1. FIREWALL   - allow inbound TCP 5000 from the internal network only
                      (Domain + Private profiles; NOT Public).
      2. ALWAYS-ON  - register a Scheduled Task that runs the dashboard
                      "whether the user is logged on or not", at startup,
                      with highest privileges  =>  behaves like an internal
                      website that is always up, even after a reboot with
                      nobody signed in.

    It also removes two landmines that a naive setup hits (see comments below):
      - runs the service AS serviceaccount, so it opens the REAL database
        (the DB + WinPython live in that profile), and
      - copies the app to a LOCAL folder first, so the boot service does not
        depend on the S: drive (\\fileserver\Clients) being mapped/reachable.

    RUN THIS ON THE HOST (THE-HOST) FROM AN ELEVATED PowerShell:
        powershell -ExecutionPolicy Bypass -File .\enable-team-access.ps1

    Re-running it is safe (idempotent) and is also how you DEPLOY code updates:
    it re-copies src\ from the share and restarts the service.
#>

$ErrorActionPreference = 'Stop'

# ---- Settings (change here if anything moves) ----------------------------
$TaskName    = 'KarbonDashboard'
$Port        = 5000
$ServiceUser = 'YOURDOMAIN\serviceaccount'   # MUST own the DB + WinPython (see landmine #1)

# Source of the code (UNC, not S:  -- an elevated session may not have S: mapped).
$SourceRoot  = '\\fileserver\Clients\KARBON\Update Project - Sarah''s Excels\2025 Tax Season\Summer Interns\AI Dashboard'
# Local deploy target on the host (removes the network dependency -- landmine #2).
$DeployRoot  = 'C:\KarbonDashboard'
# Absolute path to the WinPython interpreter in serviceaccount's profile.
$Python      = 'C:\Users\serviceaccount\AppData\Local\WP\WPy64-313130\python\python.exe'

$LauncherCmd = Join-Path $DeployRoot 'run-service.cmd'
$LogDir      = Join-Path $DeployRoot 'logs'
$ruleName    = 'Karbon Dashboard (LAN 5000)'

function Step($n, $m) { Write-Host "`n=== [$n] $m ===" -ForegroundColor Cyan }

# ---- Pre-flight ----------------------------------------------------------
Step 0 'Pre-flight checks'
if (-not (Test-Path $Python))      { throw "WinPython not found at $Python. Is serviceaccount's profile present on this host?" }
if (-not (Test-Path "$SourceRoot\src\webapp.py")) { throw "Cannot read source at $SourceRoot (need access to the \\fileserver\Clients share)." }
Write-Host "  Python : $Python"
Write-Host "  Source : $SourceRoot"
Write-Host "  Deploy : $DeployRoot"

# ---- 1. Copy the app to a LOCAL folder -----------------------------------
# Landmine #2: the boot service must not depend on the S: mapping / file server.
Step 1 'Copying app to a local folder on the host'
New-Item -ItemType Directory -Path $DeployRoot -Force | Out-Null
New-Item -ItemType Directory -Path $LogDir     -Force | Out-Null
# /MIR mirrors src\ (so re-running deploys the latest code and prunes removed files).
robocopy "$SourceRoot\src" "$DeployRoot\src" /MIR /XD __pycache__ .pytest_cache /NFL /NDL /NJH /NJS /R:2 /W:2 | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
Copy-Item "$SourceRoot\requirements.txt" $DeployRoot -Force -ErrorAction SilentlyContinue
Write-Host "  Copied src\ -> $DeployRoot\src  (robocopy code $LASTEXITCODE = success)"

# A tiny launcher so the service logs to a file (pythonw would swallow output).
# python.exe (not pythonw) + redirect => we get a real log for troubleshooting.
$launcher = @"
@echo off
if not exist "$LogDir" mkdir "$LogDir"
"$Python" "$DeployRoot\src\webapp.py" >> "$LogDir\dashboard.log" 2>&1
"@
Set-Content -Path $LauncherCmd -Value $launcher -Encoding ascii
Write-Host "  Wrote launcher: $LauncherCmd"

# ---- 2. Firewall: inbound TCP 5000, internal network only ----------------
Step 2 "Opening the firewall for inbound TCP $Port (internal only)"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -Profile Domain,Private -Enabled True | Out-Null
Write-Host "  Rule '$ruleName' created for Domain+Private (Public deliberately excluded)."

# ---- 3. Always-on Scheduled Task -----------------------------------------
# Landmine #1: run AS serviceaccount so %LOCALAPPDATA% resolves to that profile and the
# app opens the REAL database (1096 items) rather than a fresh empty one.
Step 3 "Registering the always-on service (Scheduled Task '$TaskName')"
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue;
                     Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }

$action  = New-ScheduledTaskAction -Execute $LauncherCmd -WorkingDirectory "$DeployRoot\src"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)   # zero = run forever, never auto-killed

Write-Host "  This task runs as $ServiceUser 'whether logged on or not', so it needs that account's password."
$sec  = Read-Host "  Enter the Windows password for $ServiceUser" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
try {
    # -User + -Password => LogonType Password (runs with nobody logged in).
    # -RunLevel Highest => "Run with highest privileges".
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -User $ServiceUser -Password $plain -RunLevel Highest `
        -Description 'NLC Dashboard - always-on internal web app (see setup\enable-team-access.ps1)' -Force | Out-Null
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $plain = $null
}
Write-Host "  Task '$TaskName' registered: At startup, highest privileges, runs whether logged on or not."

Start-ScheduledTask -TaskName $TaskName
Write-Host "  Started the task now (no reboot needed)."

# ---- 4. Verify it is actually serving ------------------------------------
Step 4 'Verifying the dashboard is up'
$up = $false
foreach ($i in 1..20) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/login" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
$listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
       Select-Object -First 1).IPAddress

if ($up -and $listen) {
    Write-Host "`nSUCCESS - the dashboard is live." -ForegroundColor Green
    Write-Host "  Listening on: $($listen.LocalAddress):$Port"
    Write-Host "  Staff can reach it at:  http://$($env:COMPUTERNAME):$Port   (or http://$ip`:$Port )"
    Write-Host "  Logs: $LogDir\dashboard.log"
} else {
    Write-Warning "The service was registered but is not serving yet. Check $LogDir\dashboard.log"
    Write-Host "  (A wrong password is the most common cause -- re-run and re-enter it.)"
}

Write-Host "`nOptional next steps for IT (not required to work):" -ForegroundColor DarkGray
Write-Host "  - DNS 'nlcdashboard' -> this host, + reverse proxy on :80 to drop the ':$Port'." -ForegroundColor DarkGray
Write-Host "  - HTTPS via that proxy (needed for 'install as an app'/PWA on other machines)." -ForegroundColor DarkGray
Write-Host "  - To update the app later: re-run this script (it re-copies src\ and restarts)." -ForegroundColor DarkGray
