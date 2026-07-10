param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

# 从脚本位置定位项目根目录，保证从任意 PowerShell 工作目录启动都一致。
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到虚拟环境 Python：$Python。请先按 README 完成安装。"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stdout = Join-Path $LogDir "gateway.stdout.log"
$Stderr = Join-Path $LogDir "gateway.stderr.log"

# 后台隐藏启动网关；PID 会返回给调用方，便于后续纳入 Windows 服务或守护程序。
$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "gateway.main:app", "--host", $HostAddress, "--port", $Port) `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -PassThru

Write-Output "Automation gateway started. PID=$($Process.Id), URL=http://${HostAddress}:$Port"

