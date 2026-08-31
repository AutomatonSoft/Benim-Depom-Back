# Toggles DRF throttling on the STAGE backend for load testing.
#
#   .\load_tests\throttling-stage.ps1 on      # disable rate limits on stage
#   .\load_tests\throttling-stage.ps1 off     # restore rate limits on stage
#   .\load_tests\throttling-stage.ps1 status  # show the current state
#
# The script only touches /home/alikhan/apps/benim-depom-stage and never
# the production directory. Turning the flag on/off recreates the "web"
# container (a few seconds of stage downtime).

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("on", "off", "status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

$SshTarget = "alikhan@31.70.112.98"
$StageDir = "/home/alikhan/apps/benim-depom-stage"
$Compose = "docker compose --project-name benim-depom-stage --env-file .deploy.env -f docker-compose.prod.yml"
$Flag = "LOAD_TEST_DISABLE_THROTTLING"

function Invoke-Stage([string]$RemoteCommand) {
    ssh $SshTarget "cd $StageDir && $RemoteCommand"
    if ($LASTEXITCODE -ne 0) {
        throw "Remote command failed: $RemoteCommand"
    }
}

function Show-Status {
    Write-Host "--- .env flag on stage:"
    Invoke-Stage "grep '^$Flag=' .env || echo '$Flag is not set (throttling ENABLED)'"
    Write-Host "--- flag inside the running web container:"
    Invoke-Stage "$Compose exec -T web sh -c 'printenv $Flag || echo not set (throttling ENABLED)'"
}

switch ($Action) {
    "on" {
        Write-Host ">>> Disabling throttling on STAGE..."
        Invoke-Stage "grep -q '^$Flag=1' .env || echo '$Flag=1' >> .env"
        Invoke-Stage "$Compose up -d --force-recreate web"
        Start-Sleep -Seconds 5
        Show-Status
        Write-Host ">>> Done. Do not forget to run '.\load_tests\throttling-stage.ps1 off' after the tests."
    }
    "off" {
        Write-Host ">>> Restoring throttling on STAGE..."
        Invoke-Stage "sed -i '/^$Flag=/d' .env"
        Invoke-Stage "$Compose up -d --force-recreate web"
        Start-Sleep -Seconds 5
        Show-Status
        Write-Host ">>> Done. Rate limits are active again."
    }
    "status" {
        Show-Status
    }
}
