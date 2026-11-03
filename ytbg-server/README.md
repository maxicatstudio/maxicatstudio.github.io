# YT Nền — máy chủ âm thanh tự host (Windows VPS)

Máy chủ nhỏ viết bằng Python + [yt-dlp](https://github.com/yt-dlp/yt-dlp). Nó làm thay việc của các máy chủ
Piped/Invidious công cộng, nhưng là **của riêng bạn**, nên ổn định hơn:

- tìm kiếm YouTube;
- lấy luồng **chỉ âm thanh** của video và chuyển tiếp về điện thoại (hỗ trợ tua).

Trang YT Nền (`/ytbg/`) dùng được ngay: dán địa chỉ máy chủ vào ô **Máy chủ riêng**.

> Link âm thanh YouTube cấp bị khoá theo IP của VPS, nên âm thanh phải đi qua VPS.
> Mỗi người nghe tốn khoảng 50–130 MB/giờ băng thông VPS.

---

## Cần chuẩn bị

1. **VPS Windows Server 2019** (hoặc 2022, Windows 10/11), có IP công cộng.
2. **Một tên miền trỏ về IP của VPS.** Cách miễn phí là dùng **DuckDNS**:
   - vào <https://www.duckdns.org>, đăng nhập bằng Google/GitHub;
   - tạo tên, ví dụ `tenban` → bạn có `tenban.duckdns.org`;
   - ô **current ip**: nhập IP công cộng của VPS, rồi bấm **update ip**.
3. **Mở cổng 80 và 443** trong trang quản lý của nhà cung cấp VPS (firewall / security group).
   Tường lửa trong Windows thì script tự mở.

Vì sao cần tên miền: trang web chạy trên `https://`, nên trình duyệt **chặn** mọi kết nối `http://`.
Script cài **Caddy**, tự xin chứng chỉ HTTPS miễn phí (Let's Encrypt) cho tên miền của bạn.

## Cài đặt

1. Trên VPS, tải mã nguồn của nhánh này:
   <https://github.com/maxicatstudio/maxicatstudio.github.io/archive/refs/heads/ytbg-self-host.zip>
   Giải nén, rồi chép thư mục `ytbg-server` ra chỗ cố định, ví dụ `C:\ytbg-server`.
2. Mở **PowerShell bằng "Run as Administrator"**, rồi chạy:

   ```powershell
   cd C:\ytbg-server
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\install.ps1 -Domain tenban.duckdns.org
   ```

   Script sẽ:
   - cài Python 3.12, aiohttp, yt-dlp và Deno (yt-dlp cần Deno để đọc YouTube);
   - tạo `config.json` kèm **khoá bí mật** ngẫu nhiên;
   - cài Caddy (HTTPS) và mở tường lửa cổng 80/443;
   - tạo tác vụ trong **Task Scheduler** để tự chạy khi VPS khởi động, và cập nhật yt-dlp mỗi ngày lúc 4:00.
3. Cuối cùng script in ra một địa chỉ dạng:

   ```
   https://tenban.duckdns.org/AbCdEf123456xyz
   ```

4. Trên điện thoại, mở trang YT Nền → **Cài đặt tìm kiếm** → dán địa chỉ đó vào ô **Máy chủ riêng**,
   rồi bấm **Kiểm tra máy chủ**. Nếu thấy `✓ Hoạt động` là xong.
   Dòng trạng thái khi phát sẽ ghi `… qua tenban.duckdns.org`.

> Đừng chia sẻ địa chỉ có khoá. Ai có nó đều dùng được băng thông VPS của bạn.

## Quản lý

| Việc | Cách làm |
|---|---|
| Xem log | `C:\ytbg-server\logs\server.log` (và `update.log`) |
| Khởi động lại | `Stop-ScheduledTask YTNen-Server; Start-ScheduledTask YTNen-Server` |
| Cập nhật yt-dlp ngay | `.\update.ps1` |
| Cập nhật mã nguồn | chép đè file mới (giữ nguyên `config.json`), rồi chạy lại `.\install.ps1 -Domain …` |
| Đổi khoá bí mật | xoá dòng `"key"` trong `config.json` → khởi động lại → xem khoá mới trong `config.json` |
| Kiểm tra từ trình duyệt | mở `https://tenban.duckdns.org/<khoá>/`, phải thấy `{"ok": true, ...}` |
| Gỡ cài đặt | `Unregister-ScheduledTask YTNen-Server,YTNen-Caddy,YTNen-Update -Confirm:$false`, rồi xoá thư mục |

## Xử lý sự cố

**"Sign in to confirm you're not a bot" trong log.** YouTube hay chặn IP của trung tâm dữ liệu (VPS).
Cách khắc phục là dùng cookie của một tài khoản YouTube (nên dùng tài khoản phụ):

1. Trên máy tính, cài tiện ích trình duyệt **"Get cookies.txt LOCALLY"**.
2. Đăng nhập youtube.com, rồi xuất cookie ra file.
3. Đặt tên file là `cookies.txt` và chép vào `C:\ytbg-server\`.
4. Khởi động lại máy chủ.

**Không xin được HTTPS** (log Caddy báo lỗi, trang web báo "không kết nối được"):
- Kiểm tra tên miền đã trỏ đúng IP chưa: chạy `nslookup tenban.duckdns.org`.
- Kiểm tra cổng 80 và 443 đã mở ở **nhà cung cấp VPS** chưa.
- Không có phần mềm nào khác (IIS…) đang chiếm cổng 80/443.
- Xem log Caddy: chạy tay `C:\ytbg-server\bin\caddy.exe run --config C:\ytbg-server\Caddyfile` để thấy lỗi trực tiếp.

**Bài không phát được, log ghi `yt-dlp lỗi`.** Chạy `.\update.ps1`. YouTube thay đổi thường xuyên,
và bản yt-dlp mới thường đã sửa.

**Chưa có tên miền, muốn thử nhanh:** dùng Cloudflare Quick Tunnel. Địa chỉ đổi mỗi lần chạy, nên chỉ hợp để thử.

```powershell
Invoke-WebRequest -UseBasicParsing https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile C:\ytbg-server\bin\cloudflared.exe
C:\ytbg-server\bin\cloudflared.exe tunnel --url http://127.0.0.1:8080
```

Nó in ra một địa chỉ `https://xxxx.trycloudflare.com`. Thêm `/<khoá>` vào sau địa chỉ đó rồi dán vào app.

## API (tương thích Piped)

| Đường dẫn | Mô tả |
|---|---|
| `GET /<khoá>/` | trạng thái |
| `GET /<khoá>/streams/<videoId>` | `{title, uploader, duration, audioStreams:[{url, mimeType, codec, bitrate}]}` |
| `GET /<khoá>/search?q=…` | `{items:[{url:"/watch?v=…", title, uploaderName, duration}], nextpage}` |
| `GET /<khoá>/nextpage/search?q=…&nextpage=2` | trang tiếp theo |
| `GET /<khoá>/audio/<videoId>/<formatId>` | luồng âm thanh, hỗ trợ `Range` |

---

Chỉ nên dùng cho cá nhân. Việc tải hoặc chuyển tiếp nội dung YouTube ngoài trình phát chính thức
trái với điều khoản sử dụng của YouTube.
