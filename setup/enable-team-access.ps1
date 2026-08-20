#Requires -RunAsAdministrator
<#
    enable-team-access.ps1  -  one-shot deploy of the NLC Dashboard on a host
    -------------------------------------------------------------------------
    Fixes the TWO things that block full team use, in one elevated run:

      1. FIREWALL   - allow inbound TCP 5000 from the internal network only
                      (Domain + Private profiles; NOT Public).
      2. ALWAYS-ON  - register a Scheduled Task that runs the dashboard
                      "whether the user is logged on or not", at startup,
                      with highest privileges  =>  behaves like an internal
                      website that is always up, even after a reboot with
                      nobody signed in.

    It also removes two landmines that a naive setup hits (see comments below):
      - runs the service AS the account that owns the database, so it opens the
        REAL data rather than a fresh empty one, and
      - copies the app to a LOCAL folder first, so the boot service does not
        depend on a mapped drive / file server being reachable.

    !! SECURITY !!  The dashboard has NO sign-in: anything that can reach port
    5000 gets the whole dashboard, including client data. The firewall rule
    below is therefore the only access control there is. Keep the port on the
    internal network and never forward it from the internet.

    Run FROM AN ELEVATED PowerShell ON THE HOST that should serve the dashboard:

        powershell -ExecutionPolicy Bypass -File .\enable-team-access.ps1 `
            -ServiceUser 'YOURDOMAIN\theaccount' `
            -SourceRoot  '\\yourfileserver\share\path\to\AI Dashboard'

    Re-running it is safe (idempotent) and is also how you DEPLOY code updates:
    it re-copies src\ from the source and restarts the service.
#>
param(
    # Domain account the service runs as. It MUST be the profile that owns the
    # dashboard database and the WinPython install (landmine #1) — the app reads
    # %LOCALAPPDATA% of whoever it runs as to find its database.
    [Parameter(Mandatory = $true)]
    [string] $ServiceUser,

    # Where this repo lives. Use a UNC path, not a mapped drive letter: an
    # elevated session often does not have the same drive mappings.
    [Parameter(Mandatory = $true)]
    [string] $SourceRoot,

    # Local deploy target on the host (removes the network dependency, landmine #2).
    [string] $DeployRoot = 'C:\KarbonDashboard',

    # WinPython interpreter inside the SERVICE account's profile. Defaults to the
    # standard location for that account; pass it explicitly if yours differs.
    [string] $Python,

    [int] $Port = 5000
)

$ErrorActionPreference = 'Stop'

$TaskName    = 'KarbonDashboard'
$ruleName    = "Karbon Dashboard (LAN $Port)"
$LauncherCmd = Join-Path $DeployRoot 'run-service.cmd'
$LogDir      = Join-Path $DeployRoot 'logs'

# Derive the interpreter path from the service account's profile unless told otherwise.
if (-not $Python) {
    $shortUser = ($ServiceUser -split '\\')[-1]
    $Python = "C:\Users\$shortUser\AppData\Local\WP\WPy64-313130\python\python.exe"
}

function Step($n, $m) { Write-Host "`n=== [$n] $m ===" -ForegroundColor Cyan }

# ---- Pre-flight ----------------------------------------------------------
Step 0 'Pre-flight checks'
if (-not (Test-Path $Python)) {
    throw "WinPython not found at $Python. Is $ServiceUser's profile present on this host? Pass -Python to override."
}
if (-not (Test-Path "$SourceRoot\src\webapp.py")) {
    throw "Cannot read source at $SourceRoot (check the path and that this elevated session can reach the share)."
}
Write-Host "  Python : $Python"
Write-Host "  Source : $SourceRoot"
Write-Host "  Deploy : $DeployRoot"
Write-Host "  Runs as: $ServiceUser"

# ---- 1. Copy the app to a LOCAL folder -----------------------------------
# Landmine #2: the boot service must not depend on a drive mapping / file server.
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
Write-Host "  Remember: there is no sign-in, so this rule is the access control." -ForegroundColor Yellow

# ---- 3. Always-on Scheduled Task -----------------------------------------
# Landmine #1: run as the DB-owning account so %LOCALAPPDATA% resolves to that
# profile and the app opens the REAL database rather than a fresh empty one.
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
        # "/" is the health check: there is no login page, and every page is
        # reachable without a session.
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3
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
Write-Host "  - An internal DNS name -> this host, + reverse proxy on :80 to drop the ':$Port'." -ForegroundColor DarkGray
Write-Host "  - HTTPS via that proxy (needed for 'install as an app'/PWA on other machines)." -ForegroundColor DarkGray
Write-Host "  - To update the app later: re-run this script (it re-copies src\ and restarts)." -ForegroundColor DarkGray
