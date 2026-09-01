# Runs the read-path load test against STAGE in increasing stages.
# Default: 20 -> 50 -> 100 -> 200. Stops if a stage finishes with a
# failure ratio above 1%.
#
#   .\load_tests\run-staged.ps1
#   .\load_tests\run-staged.ps1 -Users 100,200
#
# Before running:
#   1. .\load_tests\throttling-stage.ps1 on
#   2. Set the LOAD_TEST_* environment variables (see load_tests/README.md).
# After running:
#   3. .\load_tests\throttling-stage.ps1 off

param(
    [int[]]$Users = @(20, 50, 100, 200)
)

$ErrorActionPreference = "Stop"

$LocustHost = "https://stage.benim.automatonsoft.de"
$MaxFailureRatio = 0.01

$AllStages = @{
    20  = @{ SpawnRate = 2;  Duration = "5m" }
    50  = @{ SpawnRate = 5;  Duration = "10m" }
    100 = @{ SpawnRate = 10; Duration = "10m" }
    200 = @{ SpawnRate = 10; Duration = "15m" }
}

$Stages = foreach ($count in $Users) {
    if (-not $AllStages.ContainsKey($count)) {
        throw "Unsupported user count $count. Use 20, 50, 100, or 200."
    }
    @{ Users = $count; SpawnRate = $AllStages[$count].SpawnRate; Duration = $AllStages[$count].Duration }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResultsDir = Join-Path $ScriptDir "results"
New-Item -ItemType Directory -Force $ResultsDir | Out-Null

$lastPassed = $null

foreach ($stage in $Stages) {
    $users = $stage.Users
    $prefix = Join-Path $ResultsDir "read-$users"

    Write-Host ""
    Write-Host ">>> Stage: $users users, spawn $($stage.SpawnRate)/s, duration $($stage.Duration)" -ForegroundColor Cyan

    locust -f (Join-Path $ScriptDir "locustfile.py") `
        --host $LocustHost --headless `
        -u $users -r $stage.SpawnRate -t $stage.Duration `
        --csv $prefix --only-summary

    $statsFile = "$prefix`_stats.csv"
    if (-not (Test-Path $statsFile)) {
        Write-Host ">>> No stats file produced ($statsFile) - aborting." -ForegroundColor Red
        break
    }

    $total = Import-Csv $statsFile | Where-Object { $_.Name -eq "Aggregated" }
    $requests = [int]$total."Request Count"
    $failures = [int]$total."Failure Count"
    $ratio = if ($requests -gt 0) { $failures / $requests } else { 1 }

    Write-Host (">>> Result: {0} requests, {1} failures ({2:P2}), p95 {3} ms" -f `
        $requests, $failures, $ratio, $total."95%")

    if ($ratio -gt $MaxFailureRatio) {
        Write-Host ">>> Failure ratio above 1% - stopping the staged run here." -ForegroundColor Red
        break
    }

    $lastPassed = $users

    if ($users -ne $Stages[-1].Users) {
        Write-Host ">>> Cooldown 60s before the next stage..."
        Start-Sleep -Seconds 60
    }
}

Write-Host ""
if ($lastPassed) {
    Write-Host ">>> Highest stage completed with <1% errors: $lastPassed users." -ForegroundColor Green
    Write-Host ">>> Check CPU/RAM on the server and p95 in results/*_stats.csv before quoting this number."
} else {
    Write-Host ">>> No stage completed cleanly - inspect results/ and server logs." -ForegroundColor Red
}
Write-Host ">>> Reminder: .\load_tests\throttling-stage.ps1 off"
