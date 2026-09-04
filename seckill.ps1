#requires -Version 5.1
<#
.SYNOPSIS
    SecKill 快捷操作脚本

.EXAMPLE
    .\seckill.ps1 setup
    .\seckill.ps1 run
    .\seckill.ps1 cli -Url "https://item.jd.com/100014219124.html" -Time "2026-09-10 20:00:00"
    .\seckill.ps1 calibrate -Platform jd -Url "https://item.jd.com/xxxx.html"
    .\seckill.ps1 test
    .\seckill.ps1 release
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'run', 'cli', 'test', 'e2e', 'calibrate', 'release', 'clean', 'help')]
    [string]$Command = 'help',

    [string]$Url,
    [string]$Time,
    [int]$Qty = 0,
    [string]$Platform,
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'

function Ensure-Venv {
    if (-not (Test-Path $VenvPython)) {
        Write-Host "未找到虚拟环境，请先运行: .\seckill.ps1 setup" -ForegroundColor Yellow
        exit 1
    }
}

function Find-Python {
    if (Get-Command python -ErrorAction SilentlyContinue) { return 'python' }
    if (Get-Command py -ErrorAction SilentlyContinue)     { return 'py' }
    Write-Error "系统里找不到 Python，请先安装 Python 3.11+"
    exit 1
}

switch ($Command) {

    'setup' {
        if (-not (Test-Path $VenvPython)) {
            Write-Host "==> 创建虚拟环境 .venv" -ForegroundColor Cyan
            $py = Find-Python
            & $py -m venv .venv
            if (-not $?) { Write-Error "创建虚拟环境失败"; exit 1 }
        }
        Write-Host "==> 安装依赖" -ForegroundColor Cyan
        & $VenvPython -m pip install -r requirements.txt
        Write-Host "==> 下载 Chromium 内核（系统已装 Chrome 时也会下载，作为兜底）" -ForegroundColor Cyan
        & $VenvPython -m playwright install chromium
        Write-Host "环境就绪，直接 .\seckill.ps1 run 启动" -ForegroundColor Green
    }

    'run' {
        Ensure-Venv
        & $VenvPython main.py
    }

    'cli' {
        Ensure-Venv
        if (-not $Url) { Write-Error "用法: .\seckill.ps1 cli -Url <商品链接> [-Time '2026-09-10 20:00:00'] [-Qty 1] [-Headless]"; exit 1 }
        $cliArgs = @('main.py', '--cli', '--url', $Url)
        if ($Time)     { $cliArgs += @('--time', $Time) }
        if ($Qty -gt 0){ $cliArgs += @('--qty', $Qty) }
        if ($Headless) { $cliArgs += '--headless' }
        & $VenvPython @cliArgs
    }

    'test' {
        Ensure-Venv
        $env:PYTHONPATH = $Root
        & $VenvPython tests\test_core.py
    }

    'e2e' {
        Ensure-Venv
        $env:PYTHONPATH = $Root
        & $VenvPython tests\test_e2e.py
    }

    'calibrate' {
        Ensure-Venv
        if (-not $Platform) { Write-Error "用法: .\seckill.ps1 calibrate -Platform jd -Url <商品链接> [-Headless]"; exit 1 }
        $calArgs = @('tools\calibrate.py', '--platform', $Platform, '--candidates')
        if ($Url)     { $calArgs += @('--url', $Url) }
        if ($Headless) { $calArgs += '--headless' }
        & $VenvPython @calArgs
    }

    'release' {
        Ensure-Venv
        $pyinstallerMod = Join-Path $Root '.venv\Lib\site-packages\PyInstaller'
        if (-not (Test-Path $pyinstallerMod)) {
            Write-Host "==> 安装 PyInstaller（构建依赖，仅首次）" -ForegroundColor Cyan
            & $VenvPython -m pip install pyinstaller
        }
        Write-Host "==> 打包到 release\SecKill\" -ForegroundColor Cyan
        & $VenvPython -m PyInstaller --noconfirm --name SecKill --windowed --onedir `
            --add-data "config;config" --add-data "icons;icons" `
            --distpath release --workpath build main.py
        if ($LASTEXITCODE -eq 0) {
            Write-Host "打包完成: release\SecKill\SecKill.exe（整个目录拷走即可运行）" -ForegroundColor Green
        } else {
            Write-Error "打包失败，退出码 $LASTEXITCODE"
        }
    }

    'clean' {
        Write-Host "==> 清理缓存与构建产物" -ForegroundColor Cyan
        foreach ($dir in @('build', '__pycache__')) {
            if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
        }
        Get-ChildItem -Path . -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '\\.venv\\|\\release\\|\\.git\\' } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path 'SecKill.spec') { Remove-Item -Force 'SecKill.spec' }
        Write-Host "已清理（release\ 与 .venv\ 保留）" -ForegroundColor Green
    }

    'help' {
        Write-Host ""
        Write-Host "SecKill 快捷操作" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  .\seckill.ps1 setup                          创建 .venv 并安装全部依赖"
        Write-Host "  .\seckill.ps1 run                            启动图形界面"
        Write-Host "  .\seckill.ps1 cli -Url <链接>                无界面模式抢购"
        Write-Host "        [-Time '2026-09-10 20:00:00'] [-Qty 1] [-Headless]"
        Write-Host "  .\seckill.ps1 test                           核心逻辑自测（无需浏览器）"
        Write-Host "  .\seckill.ps1 e2e                            端到端测试（需要 Chrome）"
        Write-Host "  .\seckill.ps1 calibrate -Platform jd         校准平台选择器"
        Write-Host "        -Url <商品链接> [-Headless]"
        Write-Host "  .\seckill.ps1 release                        打包 exe 到 release\SecKill\"
        Write-Host "  .\seckill.ps1 clean                          清理缓存与构建产物"
        Write-Host ""
    }
}
