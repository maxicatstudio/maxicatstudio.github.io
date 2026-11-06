<#
  YT Nền — cài máy chủ âm thanh trên Windows Server 2019 / 2022 / Windows 10+.

  Chạy PowerShell bằng quyền Administrator, trong thư mục chứa file này:
      Set-ExecutionPolicy -Scope Process Bypass -Force
      .\install.ps1 -Domain tenban.duckdns.org

  -Domain   Tên miền trỏ về IP của VPS (vd. DuckDNS miễn phí). Có tên miền thì script cài
            thêm Caddy để có HTTPS tự động (Let's Encrypt) — BẮT BUỘC để trang web dùng được.
            Bỏ trống nếu bạn tự lo HTTPS (Cloudflare Tunnel, IIS…).
  -Port     Cổng nội bộ của máy chủ Python (mặc định 8080).

  Chạy lại script bất cứ lúc nào để cập nhật / sửa cài đặt — không mất tài khoản, playlist (thư mục data\).
#>
param(
    [string]$Domain = "",
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'                 # tải nhanh hơn nhiều trên PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Dir = $PSScriptRoot
$Bin = Join-Path $Dir 'bin'
$Logs = Join-Path $Dir 'logs'
New-Item -ItemType Directory -Force -Path $Bin, $Logs | Out-Null

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Hãy mở PowerShell bằng "Run as Administrator" rồi chạy lại.' }

# ------------------------------------------------------------------ 1. Python
Step 'Python 3.12'
$py = 'C:\Program Files\Python312\python.exe'
if (-not (Test-Path $py)) {
    $inst = Join-Path $env:TEMP 'python-3.12.10-amd64.exe'
    Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $inst
    Start-Process $inst -Wait -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_test=0'
    Remove-Item $inst -Force
}
& $py --version

# ------------------------------------------------------------------ 2. Thư viện
Step 'Môi trường ảo + aiohttp + yt-dlp'
$venvPy = Join-Path $Dir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $Dir '.venv') }
& $venvPy -m pip install --disable-pip-version-check -q -U pip
& $venvPy -m pip install --disable-pip-version-check -q -U -r (Join-Path $Dir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install thất bại' }

# ------------------------------------------------------------------ 3. Deno (yt-dlp cần để giải mã YouTube)
Step 'Deno (JavaScript runtime cho yt-dlp)'
$zip = Join-Path $env:TEMP 'deno.zip'
Invoke-WebRequest -UseBasicParsing 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' -OutFile $zip
Expand-Archive $zip -DestinationPath $Bin -Force
Remove-Item $zip -Force
& (Join-Path $Bin 'deno.exe') --version | Select-Object -First 1

Step 'ffmpeg (ghép hình + tiếng cho video 720p/1080p)'
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

# ------------------------------------------------------------------ 4. Cấu hình
Step 'Cấu hình (config.json)'
Push-Location $Dir
& $venvPy -c "import server" | Out-Null       # tạo config.json nếu chưa có
Pop-Location
$cfgPath = Join-Path $Dir 'config.json'
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
$cfg.host = '127.0.0.1'
$cfg.port = $Port
$cfg | ConvertTo-Json | Set-Content $cfgPath -Encoding UTF8

# ------------------------------------------------------------------ 4b. Tài khoản admin
if (-not (Test-Path (Join-Path $Dir 'data\users.json'))) {
    Step 'Tạo tài khoản admin (dùng để đăng nhập web và cấp tài khoản cho người khác)'
    do { $admin = (Read-Host 'Tên đăng nhập admin (a-z, 0-9, . _ -)').Trim().ToLower() } until ($admin -match '^[a-z0-9_.-]{3,32}$')
    Push-Location $Dir
    & $venvPy server.py adduser $admin --admin
    Pop-Location
    if ($LASTEXITCODE -ne 0) { throw 'Tạo tài khoản admin thất bại' }
} else {
    Write-Host 'Đã có tài khoản (data\users.json) — giữ nguyên.'
}

# ------------------------------------------------------------------ 5. Chạy nền khi khởi động
function Register-Bg($name, $exe, $arguments) {
    $act = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $Dir
    $trg = New-ScheduledTaskTrigger -AtStartup
    $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName $name
}

Step 'Tạo tác vụ chạy nền: YTNen-Server'
Register-Bg 'YTNen-Server' $venvPy "`"$Dir\server.py`" --log `"$Logs\server.log`""

Step 'Tạo tác vụ cập nhật yt-dlp hằng ngày (4:00 sáng): YTNen-Update'
$uAct = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Dir\update.ps1`"" -WorkingDirectory $Dir
$uTrg = New-ScheduledTaskTrigger -Daily -At 4am
Register-ScheduledTask -TaskName 'YTNen-Update' -Action $uAct -Trigger $uTrg -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

# ------------------------------------------------------------------ 6. HTTPS bằng Caddy
if ($Domain) {
    Step "HTTPS cho $Domain (Caddy + Let's Encrypt)"
    $caddy = Join-Path $Bin 'caddy.exe'
    Invoke-WebRequest -UseBasicParsing 'https://caddyserver.com/api/download?os=windows&arch=amd64' -OutFile $caddy
    @"
{
    storage file_system {
        root "$($Dir -replace '\\','/')/caddy-data"
    }
}

$Domain {
    reverse_proxy 127.0.0.1:$Port {
        flush_interval -1
    }
}
"@ | Set-Content (Join-Path $Dir 'Caddyfile') -Encoding ASCII

    Step 'Mở tường lửa Windows cổng 80, 443'
    if (-not (Get-NetFirewallRule -DisplayName 'YTNen HTTP/HTTPS' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName 'YTNen HTTP/HTTPS' -Direction Inbound -Protocol TCP -LocalPort 80, 443 -Action Allow | Out-Null
    }
    Register-Bg 'YTNen-Caddy' $caddy "run --config `"$Dir\Caddyfile`" --adapter caddyfile"
}

# ------------------------------------------------------------------ 7. Kiểm tra
Step 'Kiểm tra máy chủ'
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    Start-Sleep -Seconds 2
    try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/"; $ok = $r.StatusCode -eq 200 } catch {}
}
if ($ok) { Write-Host 'Máy chủ chạy OK' -ForegroundColor Green }
else { Write-Host "Máy chủ chưa phản hồi — xem log: $Logs\server.log" -ForegroundColor Yellow }

Write-Host ''
if ($Domain) {
    Write-Host "Mở trên điện thoại / máy tính:  https://$Domain" -ForegroundColor Green
    Write-Host 'Đăng nhập bằng tài khoản admin, vào tab "Quản trị" để cấp tài khoản cho người khác.'
    Write-Host '(Lần đầu Caddy cần ~1 phút để xin chứng chỉ HTTPS. Nhớ mở cổng 80 và 443 ở tường lửa của nhà cung cấp VPS.)'
} else {
    Write-Host "Máy chủ đang chạy tại http://127.0.0.1:$Port (chỉ trong VPS)." -ForegroundColor Yellow
    Write-Host 'Bạn cần HTTPS — chạy lại với -Domain <tên miền>.'
}
Write-Host "Dữ liệu (tài khoản, playlist) nằm trong $Dir\data — nên sao lưu định kỳ."
