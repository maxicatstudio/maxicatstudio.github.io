<#
  Cập nhật YT Nền rồi khởi động lại máy chủ.

    .\update.ps1                 chỉ cập nhật yt-dlp (install.ps1 đặt lịch chạy mỗi ngày lúc 4:00)
    .\update.ps1 -Code           tải mã nguồn mới nhất từ GitHub (nhánh main) + cập nhật thư viện
    .\update.ps1 -Code -Branch <tên-nhánh>

  Không bao giờ ghi đè: config.json, cookies.txt, data\ (tài khoản, playlist), bin\, .venv\, logs\.
#>
param(
    [switch]$Code,
    [string]$Branch = 'main'
)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Dir = $PSScriptRoot
$venvPy = Join-Path $Dir '.venv\Scripts\python.exe'
$log = Join-Path $Dir 'logs\update.log'
New-Item -ItemType Directory -Force -Path (Join-Path $Dir 'logs') | Out-Null
function Log($m) { $line = "$(Get-Date -Format s) $m"; Write-Host $line; Add-Content $log $line }

if ($Code) {
    Log "tải mã nguồn nhánh $Branch"
    $zip = Join-Path $env:TEMP 'ytbg-src.zip'
    $tmp = Join-Path $env:TEMP 'ytbg-src'
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Invoke-WebRequest -UseBasicParsing "https://github.com/maxicatstudio/maxicatstudio.github.io/archive/refs/heads/$Branch.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName 'ytbg-server' }
    if (-not (Test-Path (Join-Path $src 'server.py'))) { Log 'Không tìm thấy ytbg-server trong bản tải về'; exit 1 }
    foreach ($f in 'server.py', 'requirements.txt', 'README.md', 'install.ps1', 'update.ps1') {
        Copy-Item (Join-Path $src $f) (Join-Path $Dir $f) -Force
    }
    Remove-Item (Join-Path $Dir 'web') -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $src 'web') (Join-Path $Dir 'web') -Recurse -Force
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
    & $venvPy -m pip install --disable-pip-version-check -q -U -r (Join-Path $Dir 'requirements.txt') 2>&1 | Add-Content $log
    $Bin = Join-Path $Dir 'bin'
    New-Item -ItemType Directory -Force -Path $Bin | Out-Null
    # ------------------------------------------------------------------ ffmpeg (ghép video 480p–1080p)
    $ff = Join-Path $Bin 'ffmpeg.exe'
    if (-not (Test-Path $ff)) {
        Write-Host 'Tải ffmpeg (~100 MB)…'
        $fzip = Join-Path $env:TEMP 'ffmpeg.zip'
        $ftmp = Join-Path $env:TEMP 'ffmpeg-x'
        Remove-Item $ftmp -Recurse -Force -ErrorAction SilentlyContinue
        Invoke-WebRequest -UseBasicParsing 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $fzip
        Expand-Archive $fzip -DestinationPath $ftmp -Force
        $exe = Get-ChildItem $ftmp -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
        if ($exe) { Copy-Item $exe.FullName $ff -Force } else { Write-Host 'Không tìm thấy ffmpeg.exe trong gói tải về' -ForegroundColor Yellow }
        Remove-Item $fzip, $ftmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $ff) { & $ff -hide_banner -version | Select-Object -First 1 }
    Log 'đã cập nhật mã nguồn'
} else {
    Log 'cập nhật yt-dlp'
    & $venvPy -m pip install --disable-pip-version-check -q -U "yt-dlp[default]" 2>&1 | Add-Content $log
}

Stop-ScheduledTask -TaskName 'YTNen-Server' -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName 'YTNen-Server'
Log 'đã khởi động lại máy chủ'
