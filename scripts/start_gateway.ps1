param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

# Resolve the project root from this script so any working directory is safe.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment Python was not found: $Python. Follow README setup first."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stdout = Join-Path $LogDir "gateway.stdout.log"
$Stderr = Join-Path $LogDir "gateway.stderr.log"

# Start hidden in the background and return the PID for service supervision.
$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "gateway.main:app", "--host", $HostAddress, "--port", $Port) `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -PassThru

Write-Output "Automation gateway started. PID=$($Process.Id), URL=http://${HostAddress}:$Port"
