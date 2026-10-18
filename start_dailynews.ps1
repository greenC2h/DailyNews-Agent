# DailyNews 本地一键启动脚本。
# 负责迁移数据库、启动 Web/Worker、等待服务就绪并打开浏览器。

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$webProcess = $null
$workerProcess = $null

function Stop-DailyNewsProcesses {
    <# 停止本次脚本启动的 Web 和 Worker 子进程，避免关闭窗口后遗留后台服务。 #>
    foreach ($process in @($webProcess, $workerProcess)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    Write-Host 'DailyNews 正在准备启动...' -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath '.env')) {
        throw '未找到 .env。请先复制 .env.example 为 .env 并填写数据库和模型配置。'
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw '未找到 uv。请先安装 uv 并确保它已加入 PATH。'
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw '未找到 npm。请先安装 Node.js 并确保 npm 已加入 PATH。'
    }

    Write-Host '正在同步 Python 依赖...' -ForegroundColor DarkGray
    uv sync

    if (-not (Test-Path -LiteralPath 'frontend/node_modules')) {
        Write-Host '正在安装前端依赖...' -ForegroundColor DarkGray
        npm --prefix frontend ci
    }

    Write-Host '正在构建前端...' -ForegroundColor DarkGray
    npm --prefix frontend run build

    Write-Host '正在执行数据库迁移...' -ForegroundColor DarkGray
    uv run alembic upgrade head

    New-Item -ItemType Directory -Force -Path 'data/logs' | Out-Null
    $webLog = Join-Path $projectRoot 'data/logs/web.log'
    $webErrorLog = Join-Path $projectRoot 'data/logs/web-error.log'
    $workerLog = Join-Path $projectRoot 'data/logs/worker.log'
    $workerErrorLog = Join-Path $projectRoot 'data/logs/worker-error.log'

    Write-Host '正在启动 Web 服务和 Worker...' -ForegroundColor DarkGray
    $webProcess = Start-Process -FilePath 'uv' -ArgumentList 'run','uvicorn','main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectRoot -RedirectStandardOutput $webLog -RedirectStandardError $webErrorLog -PassThru -WindowStyle Hidden
    $workerProcess = Start-Process -FilePath 'uv' -ArgumentList 'run','python','-m','app.worker' -WorkingDirectory $projectRoot -RedirectStandardOutput $workerLog -RedirectStandardError $workerErrorLog -PassThru -WindowStyle Hidden

    $ready = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        Start-Sleep -Seconds 1
        if ($webProcess.HasExited) {
            throw "Web 服务启动失败，请查看 $webLog"
        }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 2
            if ($health.status -eq 'ok') {
                $ready = $true
                break
            }
        } catch {
            # 服务尚未监听，继续等待。
        }
        Write-Progress -Activity '等待 DailyNews 服务就绪' -Status "$attempt / 60 秒" -PercentComplete ($attempt / 60 * 100)
    }
    Write-Progress -Activity '等待 DailyNews 服务就绪' -Completed
    if (-not $ready) {
        throw "服务在 60 秒内未就绪，请查看 $webLog 和 $workerLog"
    }

    Start-Process 'http://127.0.0.1:8000/'
    Write-Host 'DailyNews 已启动，浏览器正在打开。' -ForegroundColor Green
    Write-Host '关闭此窗口将停止本次启动的 Web 服务和 Worker。' -ForegroundColor Yellow
    Write-Host '日志：data/logs/web.log、data/logs/web-error.log、data/logs/worker.log、data/logs/worker-error.log' -ForegroundColor DarkGray

    while (-not $webProcess.HasExited -and -not $workerProcess.HasExited) {
        Start-Sleep -Seconds 2
    }
    if ($webProcess.HasExited) {
        throw "Web 服务已退出，请查看 $webLog"
    }
    throw "Worker 已退出，请查看 $workerLog"
} catch {
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    Read-Host '按 Enter 退出'
    exit 1
} finally {
    Stop-DailyNewsProcesses
}
