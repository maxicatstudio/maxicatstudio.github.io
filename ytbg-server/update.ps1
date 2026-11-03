# Cập nhật yt-dlp (YouTube hay thay đổi, yt-dlp ra bản sửa thường xuyên) rồi khởi động lại máy chủ.
# install.ps1 đã đặt lịch chạy file này mỗi ngày lúc 4:00. Có thể chạy tay bất cứ lúc nào.
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Dir = $PSScriptRoot
$venvPy = Join-Path $Dir '.venv\Scripts\python.exe'
$log = Join-Path $Dir 'logs\update.log'
New-Item -ItemType Directory -Force -Path (Join-Path $Dir 'logs') | Out-Null

"$(Get-Date -Format s) cập nhật yt-dlp" | Add-Content $log
& $venvPy -m pip install --disable-pip-version-check -q -U "yt-dlp[default]" 2>&1 | Add-Content $log

Stop-ScheduledTask -TaskName 'YTNen-Server' -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName 'YTNen-Server'
"$(Get-Date -Format s) đã khởi động lại máy chủ" | Add-Content $log
