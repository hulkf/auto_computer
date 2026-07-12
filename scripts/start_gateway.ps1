param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$OpenConsole,
    [int]$StartupWaitSeconds = 15
)

# Resolve the project root from this script so any working directory is safe.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$ConsoleUrl = "http://${HostAddress}:$Port/console/"
$HealthUrl = "http://${HostAddress}:$Port/health"

function Test-GatewayHealth {
    try {
        $Response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        return $Response.StatusCode -ge 200 -and $Response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment Python was not found: $Python. Follow README setup first."
}

if (Test-GatewayHealth) {
    Write-Output "Automation gateway is already running. URL=$ConsoleUrl"
    if ($OpenConsole) {
        Start-Process $ConsoleUrl
    }
    exit 0
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stdout = Join-Path $LogDir "gateway.stdout.log"
$Stderr = Join-Path $LogDir "gateway.stderr.log"
$Runner = Join-Path $LogDir "run_gateway.cmd"
$RunnerContent = @"
@echo off
cd /d "$ProjectRoot"
"$Python" -m uvicorn gateway.main:app --host "$HostAddress" --port $Port > "$Stdout" 2> "$Stderr"
"@
[System.IO.File]::WriteAllText($Runner, $RunnerContent, [System.Text.Encoding]::ASCII)

# Start hidden in the background and return the PID for service supervision.
# The runner cmd owns stdout/stderr redirection; this avoids Start-Process'
# environment dictionary bug with -RedirectStandardOutput/-RedirectStandardError.
$Process = Start-Process `
    -FilePath $env:ComSpec `
    -ArgumentList @("/d", "/c", $Runner) `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -PassThru

Write-Output "Automation gateway started. PID=$($Process.Id), URL=$ConsoleUrl"

if ($OpenConsole) {
    $Deadline = (Get-Date).AddSeconds($StartupWaitSeconds)
    while ((Get-Date) -lt $Deadline) {
        if (Test-GatewayHealth) {
            Start-Process $ConsoleUrl
            exit 0
        }
        Start-Sleep -Milliseconds 500
    }

    Write-Warning "Gateway was started but did not become healthy within $StartupWaitSeconds seconds. Check $Stderr"
}
