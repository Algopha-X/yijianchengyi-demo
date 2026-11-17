[CmdletBinding()]
param(
    [ValidateRange(1, 65535)] [int] $Port = 8000,
    [ValidateSet('chromium', 'chrome', 'msedge')] [string] $BrowserChannel = 'chromium',
    [switch] $SkipInstall
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$previousDirectory = Get-Location
$environmentNames = @('LLM_MODE', 'IMAGE_MODE', 'VERIFICATION_MODE', 'APP_ENV', 'APP_HOST', 'APP_PORT', 'APP_ALLOWED_ORIGINS', 'DATA_DIR', 'DATABASE_PATH', 'YCY_BROWSER_CHANNEL', 'PYTHONUTF8')
$previousEnvironment = @{}
foreach ($environmentName in $environmentNames) {
    $previousEnvironment[$environmentName] = [Environment]::GetEnvironmentVariable($environmentName, 'Process')
}

function Invoke-CheckedCommand {
    param([string] $Executable, [string[]] $CommandArguments)
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败，退出码 $LASTEXITCODE。已停止后续步骤。"
    }
}

try {
    $uvExecutable = (Get-Command uv -ErrorAction Stop).Source
    $npmExecutable = (Get-Command npm.cmd -ErrorAction Stop).Source
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
    }
    catch {
        throw "本机端口 $Port 已占用或无法监听。请关闭占用程序或用 -Port 指定其他端口。"
    }
    finally {
        $listener.Stop()
    }

    $env:LLM_MODE = 'mock'
    $env:IMAGE_MODE = 'mock'
    $env:VERIFICATION_MODE = 'development'
    $env:PYTHONUTF8 = '1'
    $env:APP_ENV = 'development'
    $env:APP_HOST = '127.0.0.1'
    $env:APP_PORT = [string] $Port
    $env:APP_ALLOWED_ORIGINS = "http://127.0.0.1:$Port"
    $env:DATA_DIR = Join-Path $projectDirectory 'data'
    $env:DATABASE_PATH = Join-Path $projectDirectory 'data/app.db'
    $env:YCY_BROWSER_CHANNEL = if ($BrowserChannel -eq 'chromium') { $null } else { $BrowserChannel }

    if (-not $SkipInstall) {
        Write-Host '正在按锁文件安装后端依赖与 Chromium，首次运行需要联网。'
        Set-Location (Join-Path $projectDirectory 'backend')
        Invoke-CheckedCommand $uvExecutable @('sync', '--frozen')
        if ($BrowserChannel -eq 'chromium') {
            Invoke-CheckedCommand $uvExecutable @('run', '--frozen', 'playwright', 'install', 'chromium')
        }
        else {
            Write-Host "本次显式使用已安装的浏览器通道 $BrowserChannel；不会自动回退到其他浏览器。"
        }
        Write-Host '正在按锁文件安装前端依赖。'
        Set-Location (Join-Path $projectDirectory 'frontend')
        Invoke-CheckedCommand $npmExecutable @('ci')
    }
    Set-Location (Join-Path $projectDirectory 'frontend')
    Write-Host '正在构建前端。'
    Invoke-CheckedCommand $npmExecutable @('run', 'build')
    Set-Location (Join-Path $projectDirectory 'backend')
    Write-Host '正在检查模板缩略图，仅为缺失项生成真实缓存。'
    Invoke-CheckedCommand $uvExecutable @('run', '--frozen', 'python', '-m', 'app.rendering', '--missing', '--directory', (Join-Path $env:DATA_DIR 'template-previews'))
    Write-Host "本地 Mock 演示即将启动：http://127.0.0.1:$Port"
    Write-Host '无需 .env 或 API Key。当前终端用于运行服务；按 Ctrl+C 停止，已有数据会保留。'
    Invoke-CheckedCommand $uvExecutable @('run', '--frozen', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string] $Port)
}
finally {
    Set-Location $previousDirectory
    foreach ($environmentName in $environmentNames) {
        [Environment]::SetEnvironmentVariable($environmentName, $previousEnvironment[$environmentName], 'Process')
    }
}
