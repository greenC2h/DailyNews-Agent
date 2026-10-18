# DailyNews local launcher.
# Migrates the database, starts Web/Worker, waits for readiness, and opens the browser.

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$webProcess = $null
$workerProcess = $null

function Stop-DailyNewsProcesses {
    <# Stop the Web and Worker process tree started by this launcher. #>
    foreach ($process in @($webProcess, $workerProcess)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    # uv can leave a Python child behind when its wrapper is stopped; clean only this project.
    $projectPattern = [regex]::Escape($projectRoot)
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('python.exe', 'uvicorn.exe') -and $_.CommandLine -match $projectPattern
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

try {
    Write-Host 'Preparing DailyNews...' -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath '.env')) {
        throw 'Missing .env. Copy .env.example to .env and configure the database and model.'
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv was not found. Install uv and add it to PATH.'
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'npm was not found. Install Node.js and add npm to PATH.'
    }

    Write-Host 'Syncing Python dependencies...' -ForegroundColor DarkGray
    uv sync

    if (-not (Test-Path -LiteralPath 'frontend/node_modules')) {
        Write-Host 'Installing frontend dependencies...' -ForegroundColor DarkGray
        npm --prefix frontend ci
    }

    Write-Host 'Building frontend...' -ForegroundColor DarkGray
    npm --prefix frontend run build

    Write-Host 'Applying database migrations...' -ForegroundColor DarkGray
    uv run alembic upgrade head

    New-Item -ItemType Directory -Force -Path 'data/logs' | Out-Null
    $webLog = Join-Path $projectRoot 'data/logs/web.log'
    $webErrorLog = Join-Path $projectRoot 'data/logs/web-error.log'
    $workerLog = Join-Path $projectRoot 'data/logs/worker.log'
    $workerErrorLog = Join-Path $projectRoot 'data/logs/worker-error.log'

    Write-Host 'Starting Web service and Worker...' -ForegroundColor DarkGray
    $webProcess = Start-Process -FilePath 'uv' -ArgumentList 'run','uvicorn','main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectRoot -RedirectStandardOutput $webLog -RedirectStandardError $webErrorLog -PassThru -WindowStyle Hidden
    $workerProcess = Start-Process -FilePath 'uv' -ArgumentList 'run','python','-m','app.worker' -WorkingDirectory $projectRoot -RedirectStandardOutput $workerLog -RedirectStandardError $workerErrorLog -PassThru -WindowStyle Hidden

    $ready = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        Start-Sleep -Seconds 1
        if ($webProcess.HasExited) {
            throw "Web service failed to start. Check $webLog"
        }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 2
            if ($health.status -eq 'ok') {
                $ready = $true
                break
            }
        } catch {
            # The service is not listening yet; keep waiting.
        }
        Write-Progress -Activity 'Waiting for DailyNews' -Status "$attempt / 60 seconds" -PercentComplete ($attempt / 60 * 100)
    }
    Write-Progress -Activity 'Waiting for DailyNews' -Completed
    if (-not $ready) {
        throw "Service was not ready within 60 seconds. Check $webLog and $workerLog"
    }

    Start-Process 'http://127.0.0.1:8000/'
    Write-Host 'DailyNews is ready. Opening the browser.' -ForegroundColor Green
    Write-Host 'Closing this window stops the Web service and Worker started by this run.' -ForegroundColor Yellow
    Write-Host 'Logs: data/logs/web.log, web-error.log, worker.log, worker-error.log' -ForegroundColor DarkGray

    while (-not $webProcess.HasExited -and -not $workerProcess.HasExited) {
        Start-Sleep -Seconds 2
    }
    if ($webProcess.HasExited) {
        throw "Web service exited. Check $webLog"
    }
    throw "Worker exited. Check $workerLog"
} catch {
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
} finally {
    Stop-DailyNewsProcesses
}
