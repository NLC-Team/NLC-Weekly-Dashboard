<#
    verify-team-access.ps1  -  read-only health check (no admin needed)
    -------------------------------------------------------------------
    Run this any time to confirm the dashboard is set up for team use.
    It changes nothing; it just reports what is / isn't in place.

        powershell -ExecutionPolicy Bypass -File .\verify-team-access.ps1
#>

$TaskName = 'KarbonDashboard'
$Port     = 5000
$ruleName = 'Karbon Dashboard (LAN 5000)'
$ok = @(); $bad = @()

Write-Host "NLC Dashboard - team-access health check`n" -ForegroundColor Cyan

# 1. Firewall rule
try {
    $rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction Stop
    $pf = (Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule).LocalPort
    if ($rule.Enabled -eq 'True') { $ok += "Firewall rule present & enabled (port $pf, profiles: $($rule.Profile))" }
    else { $bad += "Firewall rule exists but is DISABLED." }
} catch { $bad += "Firewall rule '$ruleName' NOT found (port $Port is not open)." }

# 2. Scheduled task
try {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $princ = "$($t.Principal.UserId), logon=$($t.Principal.LogonType), level=$($t.Principal.RunLevel)"
    if ($t.State -eq 'Running') { $ok += "Task '$TaskName' is RUNNING ($princ)" }
    else { $bad += "Task '$TaskName' exists but state is '$($t.State)' (last result: $($info.LastTaskResult))" }
    Write-Host "  Task runs as : $princ" -ForegroundColor DarkGray
    Write-Host "  Last run     : $($info.LastRunTime)  result=$($info.LastTaskResult)" -ForegroundColor DarkGray
} catch { $bad += "Scheduled task '$TaskName' NOT found (nothing keeps the app running)." }

# 3. Listening socket
$listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listen) {
    $addr = ($listen | Select-Object -First 1).LocalAddress
    if ($addr -eq '0.0.0.0') { $ok += "Listening on 0.0.0.0:$Port (reachable from the LAN)" }
    else { $bad += "Listening on $addr`:$Port only - NOT reachable from other machines (expected 0.0.0.0)." }
} else { $bad += "Nothing is listening on port $Port." }

# 4. HTTP response (loopback). Probes "/" — the dashboard has no login page, and
#    every page is reachable without a session, so the overview is the health check.
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 4
    if ($r.StatusCode -eq 200) { $ok += "HTTP 200 from the dashboard (app is serving)" }
    else { $bad += "The dashboard returned HTTP $($r.StatusCode)." }
} catch { $bad += "No HTTP response on http://127.0.0.1:$Port ." }

# ---- Report --------------------------------------------------------------
Write-Host ""
foreach ($m in $ok)  { Write-Host "  [OK ] $m"  -ForegroundColor Green }
foreach ($m in $bad) { Write-Host "  [!! ] $m"  -ForegroundColor Red }

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
       Select-Object -First 1).IPAddress
Write-Host "`nStaff URL:  http://$($env:COMPUTERNAME):$Port   (or http://$ip`:$Port )" -ForegroundColor Cyan
if ($bad.Count -eq 0) { Write-Host "All checks passed - the dashboard is ready for the whole team.`n" -ForegroundColor Green }
else { Write-Host "$($bad.Count) problem(s) above - run setup\enable-team-access.ps1 (as admin) to fix.`n" -ForegroundColor Yellow }
